"""End-to-end API tests against the offline provider."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from syllabus_studio.app import create_app
from syllabus_studio.config import Settings
from syllabus_studio.llm import BaseProvider
from syllabus_studio.llm.providers.echo_provider import EchoProvider


def test_health_reports_the_wiring(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["llm"]["author"]["provider"] == "echo"
    assert body["storage"]["backend"] == "sqlite"
    assert body["capabilities"]["authorCourses"] is True
    assert {lens["id"] for lens in body["lenses"]} >= {"eli5", "analogy", "picture", "rigor"}


def test_pages_and_static_are_served(client) -> None:
    for path in ("/studio", "/reader/", "/static/css/app.css", "/static/js/studio.js"):
        assert client.get(path).status_code == 200, path


def test_the_front_door_is_the_studio(client) -> None:
    # This server is the author's tool; readers get the published reader, not this.
    res = client.get("/", follow_redirects=False)
    assert res.is_redirect
    assert res.headers["location"] == "/studio"


def test_the_studio_opens_without_an_author_model(tmp_path: Path) -> None:
    with _app(tmp_path, author="none") as client:
        assert client.get("/api/v1/health").json()["capabilities"]["authorCourses"] is False
        assert client.get("/studio").status_code == 200


def test_the_frontend_is_revalidated_so_an_upgrade_never_runs_stale_js(client) -> None:
    # Asset URLs never change (no build step), so without this a browser keeps old
    # modules that misread a newer /health.
    pages = ("/studio", "/reader/", "/reader/catalog.json")
    assets = ("/static/js/studio.js", "/reader/static/js/reader.js", "/static/css/app.css")
    for path in pages + assets:
        assert client.get(path).headers.get("cache-control") == "no-cache", path


def test_sample_syllabus_is_available(client) -> None:
    text = client.get("/api/v1/sample-syllabus").text
    assert "LEARNING OUTCOMES" in text


def _build(client, syllabus: str) -> dict:
    res = client.post(
        "/api/v1/courses", json={"syllabus": syllabus, "name": "Applied ML", "depth": "standard"}
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_the_whole_journey(client, syllabus: str) -> None:
    course = _build(client, syllabus)
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]

    # it shows up in the list
    listed = client.get("/api/v1/courses").json()
    assert cid in [c["id"] for c in listed]

    # nothing written yet
    assert client.get(f"/api/v1/courses/{cid}/lessons/{lid}").status_code == 404

    # write it
    content = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate").json()
    assert content["bigIdea"] and content["sections"]
    assert client.get(f"/api/v1/courses/{cid}/lessons").json() == [lid]

    # a second generate returns the stored copy unless forced
    again = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate").json()
    assert again["generatedAt"] == content["generatedAt"]
    forced = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate?force=true").json()
    assert forced["generatedAt"] >= content["generatedAt"]

    # progress
    updated = client.put(
        f"/api/v1/courses/{cid}/lessons/{lid}/progress", json={"done": True, "score": 1, "total": 1}
    ).json()
    assert updated["progress"][lid]["done"] is True
    assert updated["progress"][lid]["built"] is True, "generating already marked it built"

    # export -> import
    exported = client.get(f"/api/v1/courses/{cid}/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    bundle = json.loads(exported.text)
    assert bundle["course"]["progress"] == {}

    imported = client.post("/api/v1/courses/import", json={"bundle": bundle})
    assert imported.status_code == 201
    copy = imported.json()
    assert copy["id"] != cid
    assert copy["progress"][lid]["built"] is True, "the written lesson came across"

    # delete
    assert client.delete(f"/api/v1/courses/{cid}").status_code == 204
    assert client.get(f"/api/v1/courses/{cid}").status_code == 404


def test_build_rejects_a_stub(client) -> None:
    res = client.post("/api/v1/courses", json={"syllabus": "hi", "depth": "standard"})
    assert res.status_code == 422
    assert res.json()["code"] == "too_short"


def test_tutor_needs_a_written_lesson(client, syllabus: str) -> None:
    course = _build(client, syllabus)
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]

    res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "eli5"})
    assert res.status_code == 404
    assert res.json()["code"] == "not_found"


def test_lens_streams_sse(client, syllabus: str) -> None:
    course = _build(client, syllabus)
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]
    client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate")

    res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "analogy"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event: delta" in res.text
    assert "event: done" in res.text


def test_unknown_lens_is_a_400(client, syllabus: str) -> None:
    course = _build(client, syllabus)
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]
    client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate")

    res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "nope"})
    assert res.status_code == 400
    assert res.json()["code"] == "unknown_lens"


def test_ask_streams_and_needs_a_user_turn(client, syllabus: str) -> None:
    course = _build(client, syllabus)
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]
    client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate")

    ok = client.post(
        f"/api/v1/courses/{cid}/lessons/{lid}/ask",
        json={"turns": [{"role": "user", "content": "why does this matter?"}]},
    )
    assert ok.status_code == 200 and "event: done" in ok.text

    bad = client.post(
        f"/api/v1/courses/{cid}/lessons/{lid}/ask",
        json={"turns": [{"role": "assistant", "content": "hi"}]},
    )
    assert bad.status_code == 400


def test_catalog_lists_and_installs(client) -> None:
    catalog = client.get("/api/v1/catalog").json()
    assert catalog["entries"], "the bundled catalog ships with one entry"
    entry = catalog["entries"][0]

    installed = client.post(f"/api/v1/catalog/{entry['id']}/install")
    assert installed.status_code == 201
    course = installed.json()
    assert course["origin"] == f"catalog:{entry['id']}"
    assert course["modules"]

    built = client.get(f"/api/v1/courses/{course['id']}/lessons").json()
    assert built, "the catalog bundle carried a written lesson"


def test_missing_course_is_a_404(client) -> None:
    assert client.get("/api/v1/courses/nope").status_code == 404


# --- read-time resolution and capabilities (spec 001, stage 4) --------------


class Untouchable(BaseProvider):
    """A reader that records any use at all, then fails loudly.

    Recording matters as much as raising: a raise inside an open SSE stream
    becomes an error frame, which an output check could miss.
    """

    name = "untouchable"

    def __init__(self) -> None:
        self.touched: list[str] = []

    def _hit(self, what: str) -> None:
        self.touched.append(what)
        raise AssertionError(f"the reader provider was used: {what}")

    async def complete(self, messages, *, tier="default", max_tokens=None):  # noqa: ANN001, ANN201
        self._hit("complete")

    def stream(self, messages, *, tier="default", max_tokens=None):  # noqa: ANN001, ANN201
        self._hit("stream")

    def describe(self) -> dict:
        self._hit("describe")
        return {}

    async def probe(self) -> dict:
        self._hit("probe")
        return {}


class CountingReader(EchoProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls = 0

    def stream(self, messages, *, tier="default", max_tokens=None):  # noqa: ANN001, ANN201
        self.calls += 1
        return super().stream(messages, tier=tier, max_tokens=max_tokens)


@contextmanager
def _app(tmp_path: Path, **roles: str) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        llm_provider="echo",
        author_provider=roles.get("author", ""),
        reader_provider=roles.get("reader", ""),
        db_path=tmp_path / "roles.db",
        seed_demo_course=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _written_with_lenses(client, syllabus: str, lenses: dict[str, str]) -> tuple[str, str]:
    """Author a course and one lesson on the author model, then store chosen lens text."""
    cid, lid = _written(client, syllabus)
    url = f"/api/v1/courses/{cid}/lessons/{lid}"
    content = client.get(url).json()
    content["lenses"] = lenses
    assert client.put(url, json=content).status_code == 200
    return cid, lid


def _written(client, syllabus: str) -> tuple[str, str]:
    course = _build(client, syllabus)
    cid, lid = course["id"], course["modules"][0]["lessons"][0]["id"]
    assert client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate").status_code == 201
    return cid, lid


def _sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((fields["event"], json.loads(fields["data"])))
    return frames


STORED = "**Stored at authoring time.** Nobody ran a model for this."


def test_a_precomputed_lens_never_reaches_the_reader_provider(
    tmp_path: Path, syllabus: str
) -> None:
    with _app(tmp_path, reader="none") as client:
        cid, lid = _written_with_lenses(client, syllabus, {"eli5": STORED})
        spy = Untouchable()
        client.app.state.providers["reader"] = spy

        res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "eli5"})

        assert spy.touched == [], "not even describe() may run on the precomputed path"
        assert res.status_code == 200
        assert _sse(res.text) == [("delta", {"text": STORED}), ("done", {"text": STORED})]


def test_a_precomputed_lens_wins_even_when_a_live_reader_exists(
    tmp_path: Path, syllabus: str
) -> None:
    with _app(tmp_path) as client:
        cid, lid = _written_with_lenses(client, syllabus, {"rigor": STORED})
        counting = CountingReader(client.app.state.settings)
        client.app.state.providers["reader"] = counting

        stored = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "rigor"})
        live = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "eli5"})

        assert _sse(stored.text)[-1] == ("done", {"text": STORED})
        assert "event: done" in live.text
        assert counting.calls == 1, "only the lens with nothing stored spent a call"


def test_without_a_reader_a_lens_with_nothing_stored_is_503(tmp_path: Path, syllabus: str) -> None:
    with _app(tmp_path, reader="none") as client:
        # per-lens, not all-or-nothing: eli5 is stored, analogy is not
        cid, lid = _written_with_lenses(client, syllabus, {"eli5": STORED})

        ok = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "eli5"})
        res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "analogy"})

    assert ok.status_code == 200
    assert res.status_code == 503
    assert res.json()["code"] == "not_configured"


def test_without_a_reader_an_unknown_lens_is_still_a_400(tmp_path: Path, syllabus: str) -> None:
    with _app(tmp_path, reader="none") as client:
        cid, lid = _written(client, syllabus)
        res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/lens", json={"lens": "nope"})
    assert res.status_code == 400, "a bad request is bad whether or not a model exists"


def test_ask_is_503_without_a_reader(tmp_path: Path, syllabus: str) -> None:
    with _app(tmp_path, reader="none") as client:
        cid, lid = _written(client, syllabus)
        url = f"/api/v1/courses/{cid}/lessons/{lid}/ask"
        res = client.post(url, json={"turns": [{"role": "user", "content": "why?"}]})
        bad = client.post(url, json={"turns": [{"role": "assistant", "content": "hi"}]})

    assert res.status_code == 503
    assert res.json()["code"] == "not_configured"
    assert bad.status_code == 400


def test_health_reports_capabilities_per_role(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        both = client.get("/api/v1/health").json()
    with _app(tmp_path, reader="none") as client:
        no_reader = client.get("/api/v1/health").json()
    with _app(tmp_path, author="none") as client:
        no_author = client.get("/api/v1/health").json()

    assert set(both["capabilities"]) == {"authorCourses", "liveTutor", "liveLenses"}
    assert both["capabilities"] == {"authorCourses": True, "liveTutor": True, "liveLenses": True}
    assert no_reader["capabilities"] == {
        "authorCourses": True,
        "liveTutor": False,
        "liveLenses": False,
    }
    assert no_author["capabilities"] == {
        "authorCourses": False,
        "liveTutor": True,
        "liveLenses": True,
    }
    for body in (both, no_reader, no_author):
        assert "canGenerate" not in body, "the alias went with its last frontend caller"


def test_health_describes_each_role(tmp_path: Path) -> None:
    with _app(tmp_path, reader="none") as client:
        llm = client.get("/api/v1/health").json()["llm"]

    assert llm["author"]["provider"] == "echo"
    assert llm["reader"] == {"provider": "none", "available": False}
    assert set(llm) == {"author", "reader"}

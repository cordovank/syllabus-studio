"""End-to-end API tests against the offline provider."""

from __future__ import annotations

import json


def test_health_reports_the_wiring(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["llm"]["provider"] == "echo"
    assert body["storage"]["backend"] == "sqlite"
    assert body["canGenerate"] is True
    assert {lens["id"] for lens in body["lenses"]} >= {"eli5", "analogy", "picture", "rigor"}


def test_index_and_static_are_served(client) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/main.js").status_code == 200


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

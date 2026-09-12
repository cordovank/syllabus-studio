# Syllabus Studio

Paste a syllabus. Get a course you can actually work through — modules, lessons
written on demand, checks that test judgement, flashcards, a tutor that
re-explains what didn't land, and progress that persists.

A FastAPI server is the whole backend: it owns the model call and the storage,
so the frontend never talks to a model or a database directly — only to the
API. Both of those responsibilities sit behind a swappable interface:

| Concern | Interface | Ships with |
|---|---|---|
| Talking to a model | `llm.BaseProvider` | `anthropic`, `ollama` (local **and** cloud), `echo` (offline) |
| Keeping courses | `storage.CourseStore` | `sqlite` (portable file), `remote` (another instance) |
| Sharing courses | `storage.CourseBundle` | one JSON file, plus a community catalog |

---

## Run it

```bash
cd path/to/syllabus-studio
bash scripts/dev-setup.sh
```

That creates `.venv`, installs the package, writes `.env`, the `Makefile`, and runs the tests. Then:

```bash
make dev            # http://127.0.0.1:8000
```

By hand, if you'd rather:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m syllabus_studio
```

Needs Python 3.11 or newer. 


It boots with **no API key**, on the `echo` provider: real navigation, real
storage, placeholder lesson text. That is deliberate — you can work on the UI,
the schema and the tests for free.

To write real lessons, pick a provider in `.env`:

```ini
# hosted
SS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# or entirely on your own machine
SS_LLM_PROVIDER=ollama
SS_OLLAMA_MODEL_DEFAULT=qwen2.5:14b   # models under ~14B tend to fail on the
                                      # structured JSON this app asks for
```

Restart, open any lesson, press **Rewrite this lesson**.

See [`docs/ollama.md`](docs/ollama.md) for running local models — including
which sizes are actually worth it for this workload, and how Ollama's cloud
models fit through the same path.

### In VS Code

`code .`, then pick `.venv/bin/python` as the interpreter (⇧⌘P → *Python:
Select Interpreter*). Run the server with `make dev` and the suite with
`make test` from the integrated terminal — no `.vscode/` config is generated
for you.

---

## How it fits together

```
web/                 static app: ES modules, no build step
  └── api.js         ← the only file that knows HTTP exists
        │
        ▼
api/routes/          FastAPI, /api/v1, Pydantic schemas as the contract
        │
        ▼
core/                models · prompts · builder · lessons · tutor
        │
   ┌────┴────┐
   ▼         ▼
llm/       storage/  two protocols, several implementations each
```

The frontend talks to the server **only** through `web/static/js/api.js`.
Replacing it with React, Svelte or HTMX means reusing that one file and
rewriting the render modules — nothing below `api/` needs to know.

### Where to change things

| You want to… | Edit |
|---|---|
| Improve lesson quality | `core/prompts.py` — every prompt is there, nothing else |
| Add a model provider | `llm/providers/` — subclass `BaseProvider`, `@register("name")` |
| Add a storage backend | `storage/` — satisfy the `CourseStore` protocol, wire it in `storage/__init__.py` |
| Change the reading experience | `web/static/js/lesson.js` |
| Change the look | `web/static/css/app.css` — all colour lives in the `:root` token blocks |
| Add an endpoint | `api/routes/`, then `api/routes/__init__.py` |

---

## The API

Interactive docs at `/docs` once it's running.

```
GET    /api/v1/health                                    what's wired up
GET    /api/v1/courses                                   list
POST   /api/v1/courses                                   build from a syllabus
GET    /api/v1/courses/{id}                              one course
DELETE /api/v1/courses/{id}
GET    /api/v1/courses/{id}/export                       portable bundle
POST   /api/v1/courses/import                            install a bundle

GET    /api/v1/courses/{id}/lessons                      which are written
GET    /api/v1/courses/{id}/lessons/{lid}
POST   /api/v1/courses/{id}/lessons/{lid}/generate?force= write it
PUT    /api/v1/courses/{id}/lessons/{lid}/progress

POST   /api/v1/courses/{id}/lessons/{lid}/lens           SSE — re-explain
POST   /api/v1/courses/{id}/lessons/{lid}/ask            SSE — tutor thread

GET    /api/v1/catalog                                   community courses
POST   /api/v1/catalog/{entry}/install
```

The two tutor endpoints stream server-sent events: `delta`, then `done`, or
`error` carrying whatever had already been written.

Note that `PUT /courses/{id}` and `PUT /courses/{id}/lessons/{lid}` exist so one
instance can act as the storage backend for another — that is the whole of the
`remote` backend.

---

## Storage, three ways

**Local and portable** (default). One SQLite file at `SS_DB_PATH`. No services.
Copy the `.db` to another machine and everything comes with it.

**Shared over HTTP.** Run one instance somewhere reachable, then on each client:

```ini
SS_STORAGE_BACKEND=remote
SS_REMOTE_URL=https://courses.example.com
```

**Bundles and the catalog.** Any course exports to a single `.course.json`
holding the outline and every written lesson — but never your progress. Commit
it, email it, or publish a catalog index and let anyone install from it. See
[`docs/catalog.md`](docs/catalog.md).

---

## CLI

```bash
syllabus-studio serve
syllabus-studio build ml-5300.txt --name "Applied ML" --depth deep
syllabus-studio list
syllabus-studio export applied-ml-62f7f9 -o applied-ml.course.json
syllabus-studio import applied-ml.course.json
```

---

## Tests

```bash
pytest -q
```

They run entirely on the `echo` provider — no key, no network, no cost. The
suite covers the storage round-trip, progress merging, bundle import/export,
id stability, prompt construction, and the full API journey including both SSE
endpoints.

---

## Things worth knowing

- **Ids are assigned by us, never by the model** (`core/ids.py`). Lessons are
  `m2l3`, so rewriting a lesson keeps its progress key.
- **Model output is escaped before rendering**, then a closed set of inline
  marks is applied (`web/static/js/markup.js`). Raw model text never reaches
  `innerHTML`.
- **`==highlight==`** in a lesson body is asked for in the prompt and rendered
  as a highlighter mark. If the model ignores it, nothing breaks.
- **Lessons are written once and stored.** Reopening one costs nothing;
  *Rewrite this lesson* is the only thing that spends tokens again.

## Licence

MIT.

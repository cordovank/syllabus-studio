# Syllabus Studio

Paste a syllabus. Get a course you can actually work through — modules, lessons
written on demand, checks that test judgement, flashcards, a tutor that
re-explains what didn't land, and progress that persists.

A FastAPI server is the whole backend: it owns the model calls and the storage,
so the frontend only ever talks to the API. Each concern sits behind an
interface you can swap:

| Concern | Interface | Ships with |
|---|---|---|
| Talking to a model | `llm.BaseProvider` | `anthropic`, `ollama` (local **and** cloud), `echo` (offline stub), `none` |
| Keeping courses | `storage.CourseStore` | `sqlite` (portable file), `remote` (another instance) |
| Sharing courses | `storage.CourseBundle` | one JSON file, plus a community catalog |

---

## Run it

Needs Python 3.11 or newer.

```bash
cd path/to/syllabus-studio
bash scripts/dev-setup.sh   # creates .venv, installs, writes .env and the Makefile, runs the tests
make dev                    # http://127.0.0.1:8000
```

Your `.env` starts as a copy of `.env.example`, which puts everything on the
offline `echo` provider. The app runs end to end with no key and no network,
and lessons contain placeholder text until you choose real models (below).

By hand, if you'd rather:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m syllabus_studio
```

In VS Code: `code .`, pick `.venv/bin/python` as the interpreter (⇧⌘P →
*Python: Select Interpreter*), then run `make dev` / `make test` in the
integrated terminal.

### Choose your models: author and reader

Model work is split into two roles, each with its own provider:

| Role | Does | Needs |
|---|---|---|
| **author** | builds outlines, writes lessons, precomputes lenses and FAQs | a strong model |
| **reader** | the ask box, and any lens a course didn't ship with | a small model is fine — or `none` |

`.env.example` sets all three to `echo`. Change the ones you need, for example:

```ini
# one hosted model for everything
SS_LLM_PROVIDER=anthropic
SS_AUTHOR_PROVIDER=           # empty = use SS_LLM_PROVIDER
SS_READER_PROVIDER=
ANTHROPIC_API_KEY=sk-ant-...

# or: write courses locally, read them with no model at all
SS_AUTHOR_PROVIDER=ollama
SS_READER_PROVIDER=none
SS_OLLAMA_MODEL_DEFAULT=qwen2.5:14b   # under ~14B tends to break the structured JSON
```

Keep all three variables in your `.env`. If a role variable is missing entirely
(not just empty), `config.py`'s built-in default applies instead of
`SS_LLM_PROVIDER`.

The UI only offers what the configured roles can do. With no author model,
the app can open and read courses but can't build them. With reader `none`,
the ask box is off, but lenses a course came with still work. The sidebar chip
shows `author … · reader … · storage`, and `GET /api/v1/health` gives each
role's status.

[`docs/ollama.md`](docs/ollama.md) covers which sizes are worth running and how
Ollama's cloud models use the same provider.

After changing `.env`, restart the server.

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
core/                models · prompts · builder · lessons · tutor · authoring
        │
   ┌────┴────┐
   ▼         ▼
llm/       storage/  two protocols, several implementations each
```

Swapping the frontend (React, Svelte, HTMX) means reusing `api.js` and
rewriting the render modules. Nothing below `api/` changes. The reasoning
behind the layers is in [`docs/architecture.md`](docs/architecture.md).

| You want to… | Edit |
|---|---|
| Improve lesson quality | `core/prompts.py` — every prompt is there, nothing else |
| Change what authoring precomputes | `core/authoring.py` |
| Add a model provider | `llm/providers/` — subclass `BaseProvider`, `@register("name")` |
| Add a storage backend | `storage/` — satisfy the `CourseStore` protocol, wire it in `storage/__init__.py` |
| Change the reading experience | `web/static/js/lesson.js` |
| Change the look | `web/static/css/app.css` — all colour lives in the `:root` token blocks |
| Add an endpoint | `api/routes/`, then `api/routes/__init__.py` |

---

## Publishing a course

A lens (a re-explanation of the lesson from another angle) depends only on the
lesson and which lens it is. A lesson's likely questions are predictable too.
So the author model can write both ahead of time. They are stored with the
lesson and travel in the bundle, and readers with a weak model, or none, still
get them.

```bash
syllabus-studio publish applied-ml-62f7f9 -o applied-ml.course.json [--reviewed-by NAME]
```

`publish` does four things in order:

1. Writes any lessons that don't exist yet.
2. Precomputes all four lenses and a FAQ for every lesson.
3. Stamps provenance: the author model, date, depth, what was enriched, and
   whether a person reviewed it.
4. Exports the bundle.

To precompute without exporting, run `syllabus-studio enrich <id>`.

**Cost.** Four short lens calls and one FAQ call per lesson. That's about 60
calls for a 12-lesson course, on top of the 13 that built it: minutes and cents
on a hosted model. Enrichment only fills in what's missing (unless you pass
`--force`), so re-running after a failure is cheap.

---

## The API

Interactive docs at `/docs` once it's running.

```
GET    /api/v1/health                                     status per role, and what the UI may offer
GET    /api/v1/courses                                    list
POST   /api/v1/courses                                    build from a syllabus            author
GET    /api/v1/courses/{id}                               one course
DELETE /api/v1/courses/{id}
GET    /api/v1/courses/{id}/export                        portable bundle
POST   /api/v1/courses/import                             install a bundle
POST   /api/v1/courses/{id}/enrich?lenses=&faq=&force=    SSE — enrich every written lesson author

GET    /api/v1/courses/{id}/lessons                       which are written
GET    /api/v1/courses/{id}/lessons/{lid}
POST   /api/v1/courses/{id}/lessons/{lid}/generate?force= write it                         author
POST   /api/v1/courses/{id}/lessons/{lid}/enrich?lenses=&faq=&force=  precompute one lesson author
PUT    /api/v1/courses/{id}/lessons/{lid}/progress

POST   /api/v1/courses/{id}/lessons/{lid}/lens            SSE — re-explain                 reader*
POST   /api/v1/courses/{id}/lessons/{lid}/ask             SSE — tutor thread               reader

GET    /api/v1/catalog                                    community courses
POST   /api/v1/catalog/{entry}/install
```

\* A lens that was precomputed is replayed from storage and never touches the
reader model.

Streams send `delta` frames, then `done`. If a stream fails partway, it sends
`error` with the text written so far. Course enrichment sends `progress` frames
(`{lessonId, stage, done, total}`) instead of `delta`. An endpoint whose role
has no model answers `503` before any stream opens.

`PUT /courses/{id}` and `PUT /courses/{id}/lessons/{lid}` let one instance act as
the storage backend for another. That is all the `remote` backend is.

---

## Storage, three ways

**Local and portable** (default). One SQLite file at `SS_DB_PATH`. No services.
Copy the `.db` to another machine and everything comes with it.

**Shared over HTTP.** Run one instance somewhere reachable, then on each client:

```ini
SS_STORAGE_BACKEND=remote
SS_REMOTE_URL=https://courses.example.com
```

**Bundles and the catalog.** Any course exports to a single `.course.json` with
the outline, every written lesson, its precomputed lenses and FAQ, and
provenance. Progress is never included. Commit it, email it, or publish a
catalog index and let anyone install from it. See
[`docs/catalog.md`](docs/catalog.md).

---

## CLI

```bash
syllabus-studio serve
syllabus-studio build ml-5300.txt --name "Applied ML" --depth deep
syllabus-studio list
syllabus-studio export applied-ml-62f7f9 -o applied-ml.course.json
syllabus-studio import applied-ml.course.json
syllabus-studio enrich applied-ml-62f7f9 [--lenses] [--faq] [--force]
syllabus-studio publish applied-ml-62f7f9 -o applied-ml.course.json [--reviewed-by NAME]
syllabus-studio site build [-o site/]
```

`build`, `enrich` and `publish` run on the author model.

`site build` writes the reader as static files — the reader pages, `catalog.json`
and one bundle per course — into `./site`. It needs no model and no server; open
it with `python -m http.server -d site`, or host the folder anywhere. The Studio's
**Preview as reader** serves the same reader at `/reader/`.

---

## Tests

```bash
make test
```

The tests run offline, with no key and no cost. They cover the storage
round-trip, progress merging, bundles and provenance, id stability, prompt
construction, author/reader roles, authoring passes, and the full API journey,
streaming endpoints included.

---

## Things worth knowing

- **Ids are assigned by us, never by the model** (`core/ids.py`). Lessons are
  `m2l3`, so rewriting a lesson keeps its progress key.
- **Nothing is generated twice unless you ask.** Lessons, lenses and FAQs are
  stored once they're written. *Rewrite this lesson* and `--force` are the only
  things that spend tokens on them again.
- **Model output is escaped before rendering**, then a closed set of inline
  marks is applied (`web/static/js/markup.js`). Raw model text never reaches
  `innerHTML`.
- **`==highlight==`** in a lesson body is requested in the prompt and rendered
  as a highlighter mark. If the model ignores it, nothing breaks.

## Licence

MIT.

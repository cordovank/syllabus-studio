# Architecture

How the system is shaped, and why. For the author's workflow see
[publishing.md](publishing.md). For the file formats see
[catalog.md](catalog.md).

## Two halves that never share a runtime

```
AUTHOR — this server, on your machine            READERS — a URL, nothing to install
┌──────────────────────────────┐                 ┌──────────────────────────────┐
│ Studio  (/studio)            │     publish     │ site/                        │
│ build → write → enrich →     │ ──────────────▶ │   index.html    the reader   │
│ preview → publish            │  writes files   │   catalog.json  every course │
│ FastAPI · models · SQLite    │                 │   courses/*.course.json      │
└──────────────────────────────┘                 └──────────────────────────────┘
```

Writing a course is expensive: it takes a strong model, minutes, and a little
money. Reading one should cost nothing. So the model work happens once, on the
author's side, and the results are saved as plain files. A reader opens a link.
There's no Python, no key and no server per reader.

The same **course bundle** (one JSON file per course) sits at the centre of both
halves. The author's server produces bundles; the reader reads them.

## Seams

```
Studio (studio.js)            Reader (reader.js)
   │                             │
   ▼                             ▼
api.js ── /api/v1           data.js ── catalog.json + bundles, over fetch
   │                                   progress in localStorage
   ▼
api/routes/    FastAPI, Pydantic schemas are the contract
   │
   ├──────────── publishing.py   runs model work, then file work
   ▼                   │
core/ ◀────────────────┤         models · prompts · builder · lessons · tutor · authoring
   │                   ▼
llm/            storage/         providers · course store · bundles · site
```

- **The Studio reaches the server only through `api.js`.**
- **The reader reaches data only through `data.js`.** It never calls `/api/v1`,
  which is why it runs on any static host. `data.js` takes `fetch` and storage
  as arguments, so its logic is tested under `node --test`.
- **`core/` never learns about a model vendor, a storage engine, or a file.**
  It talks to `BaseProvider` and `CourseStore` only. A different model is a new
  provider; a different store is one line in `storage/__init__.py`.

## Model roles: author and reader

Every model call belongs to a role, and each role has its own provider.

| Role | Calls | Used by |
|---|---|---|
| author | outline, lessons, lenses, FAQ | Studio, CLI, publish |
| reader | a lens that wasn't precomputed; the ask box | the API only |

The static reader has no model, so no UI uses the reader role today. It stays in
the API as the basis for an optional live tutor later.

`none` is a real provider that reports itself unavailable. So "no model" is
handled the same way as any other provider, with no `None` checks scattered
through the code. `/health` turns each role's availability into
`capabilities`, and the Studio disables what it can't do.

## Content is generated once, then stored

| Step | Model calls | When |
|---|---|---|
| Build the outline | 1 | when a course is created |
| Write a lesson | 1 per lesson | on demand in the Studio, or by publish |
| Enrich a lesson | 4 lenses + 1 FAQ | by publish, or `syllabus-studio enrich` |

Building only the outline keeps the first screen fast. Every later step skips
what already exists. The only thing that spends tokens twice is `force`, which
is *Rewrite* for a lesson and `--force` for enrichment.

Enrichment exists because a reader has no model. A lens is fully determined by
the lesson and which lens is asked for, and a lesson's likely questions are
predictable. So a strong model writes both ahead of time and they ship inside
the lesson. Lenses use the same prompt as the live path, so precomputed and live
lenses read the same.

Enrichment adds to a lesson and never rewrites it. Sections, quiz and practice
stay untouched, so progress stays valid. When one lens fails, the other three
are kept.

## Ids are ours, and positional

The model never assigns an id. `outline_to_course` stamps `m1`, `m1l1`, `m1l2`
by position.

- Progress is keyed on `(course id, lesson id)`. Rewriting a lesson, or
  republishing a course, keeps every reader's progress.
- The course id is `slugify(title)` plus three random bytes, since two courses
  can share a title. Publishing keeps it, and bundle files in the site are named
  by it.

## Publishing is three modules

| Module | Does | Knows about |
|---|---|---|
| `core/publishing.py` | write missing lessons, enrich all | models, not files |
| `storage/site.py` | stamp provenance, write the bundle, upsert `catalog.json` | files, not models |
| `publishing.py` | runs the two in order | both |

The split keeps `core/` free of files and `storage/` free of models. The CLI and
`POST /courses/{id}/publish` both call `publishing.publish_course` and nothing
else, so they can't drift apart.

Publishing writes in a safe order: reader files, then the bundle, then
`catalog.json` last. Each file is written to a temp file and renamed into place.
The catalog never names a bundle that isn't on disk, and a failed publish leaves
`catalog.json` unchanged. If a lesson can't be written, publishing stops before
it touches the site.

## Preview is the real reader

There is one reader implementation. The author's server serves it:

| URL | Reads |
|---|---|
| `/reader/` | the site as it is on disk (`SS_SITE_DIR`) |
| `/reader/preview/<id>/` | the same site, with course `<id>` added as publishing would add it now |

The reader fetches `catalog.json` from its own URL, so the URL alone decides
which catalog it sees. In preview, the course's catalog entry carries
`"preview": true`, and the reader shows a preview strip. A published site never
has that flag, so the preview mode can't stay switched on after publishing.

Preview is read-only and writes nothing to `site/`. Its progress lives in
`localStorage` on the author's machine.

## Untrusted content

A catalog can list bundles from anyone, and all of it is model output. Lesson
text is always **escaped first, then a closed set of inline marks is applied**
(`markup.js`). Raw text never reaches `innerHTML`. On a public site that rule is
a security boundary. `reader.html` adds a Content-Security-Policy that forbids
inline script and restricts `connect-src` to the site's own origin.

## Streaming

Anything that takes a while streams server-sent events: the tutor endpoints,
course enrichment, and publish. `api.js` reads them with a `ReadableStream`
rather than `EventSource`, because these are POSTs with a JSON body.

A stream can fail after the `200` has gone out, so errors travel in-band as an
`error` frame. Text streams include the text written so far (`partial`), and
the client keeps it. A role with no model is caught before the stream opens and
answers `503`.

## Deliberately missing

- **No auth.** The server is a single-user local tool. Readers never reach it.
- **No migrations.** Two tables, each row a whole JSON object; Pydantic defaults
  absorb additive changes. Bundles follow the same rule: new fields are added
  without bumping `formatVersion`, so older checkouts can still install new
  bundles.
- **No frontend build step.** Plain ES modules. Asset URLs never change, so
  pages and assets are served `Cache-Control: no-cache` to stop browsers running
  stale code.
- **No live tutor in the reader**, and no accounts or sync. Progress stays in
  the reader's own browser.

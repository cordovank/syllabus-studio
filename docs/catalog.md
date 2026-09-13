# Bundles and catalogs

Courses travel as two kinds of plain JSON file:

- A **bundle** is one course: the outline, every written lesson, and who made it.
- A **catalog** is a list of bundles: one entry per course, with a link to its
  bundle.

A published site is one catalog plus its bundles. The format is flat JSON on
purpose. It diffs well, it can live in git, and any static host can serve it.
There's no registry and no account: anyone can host a catalog.

## Bundle

```json
{
  "format": "syllabus-studio/course-bundle",
  "formatVersion": 1,
  "exportedAt": 1770000000000,
  "publishedAt": 1770000000000,
  "license": "",
  "course": { "id": "applied-ml-62f7f9", "title": "…", "modules": [ … ] },
  "lessons": {
    "m1l1": {
      "bigIdea": "…", "sections": [ … ], "quiz": [ … ],
      "lenses": { "<lens-id>": "…" },
      "faq": [ { "q": "…", "a": "…" } ]
    }
  },
  "provenance": {
    "authorProvider": "anthropic",
    "authorModel": "claude-sonnet-4-5",
    "depth": "standard",
    "generatedAt": 1770000000000,
    "enriched": ["lenses", "faq"],
    "humanReviewed": true,
    "reviewer": "N. Cordova",
    "note": ""
  }
}
```

| Field | Meaning |
|---|---|
| `lessons` | only lessons that have been written; a missing one reads as *not available yet* |
| `lenses`, `faq` | precomputed by enrichment; this is all the tutoring a reader gets |
| `provenance` | which model wrote the course, what was enriched, whether a person read it |
| `enriched` | only passes that are complete on **every** lesson; derived from the content, not from the flags that were passed |
| `publishedAt` | set when `publish` writes the bundle into a site; `0` for a plain export |

**Never included: progress.** Completion belongs to the reader, not the course.

**Provenance is stamped only by publishing.** Importing a bundle keeps its
provenance on the course, and exporting writes it back out unchanged, so a
re-shared course still says who wrote it.

**Compatibility.** New fields are additive and unknown keys are ignored, so
`formatVersion` stays at 1. An old bundle installs on a new checkout, and a new
bundle installs on an old checkout, which just ignores the extras.

## Catalog

This is `catalog.json` as publishing writes it into a site:

```json
{
  "name": "Courses",
  "entries": [
    {
      "id": "applied-ml-62f7f9",
      "title": "Applied Machine Learning",
      "description": "Framing, auditing, splitting, evaluation, production.",
      "url": "courses/applied-ml-62f7f9.course.json",
      "lessonCount": 12,
      "tags": ["ml"],
      "license": "CC BY 4.0",
      "authorModel": "claude-sonnet-4-5",
      "humanReviewed": true,
      "enriched": ["lenses", "faq"],
      "publishedAt": 1770000000000
    }
  ]
}
```

- **`url` is resolved relative to `catalog.json`**, not to the page. A catalog
  and its bundles can live anywhere, including a sub-path.
- **Provenance fields are copied into the entry** (`authorModel`,
  `humanReviewed`, `enriched`), so a reader can judge a course before
  downloading it. A missing `humanReviewed` means "the catalog didn't say", so
  the card shows neither *reviewed* nor *not reviewed*.
- **One entry per course id.** Republishing replaces the entry in place and
  keeps its position. A new course goes to the top.
- **Author-edited fields survive a republish:** `description`, `tags` and
  `license`. Edit them by hand in `catalog.json`. Everything else is regenerated
  from the course.

Publishing and unpublishing maintain the site's catalog. For those steps, see
[publishing.md](publishing.md).

## Bringing a course into the Studio

To remix someone else's course, install their bundle into your own store. The
installed copy gets a fresh course id, so it never collides with the original.

```bash
syllabus-studio import applied-ml.course.json
```

The API can also install straight from a catalog:

```
GET  /api/v1/catalog                  entries from SS_CATALOG_URL
POST /api/v1/catalog/{entry}/install
POST /api/v1/courses/import           a bundle in the request body
```

`SS_CATALOG_URL` can point at any hosted `catalog.json`. When it's unset, the
bundled example in `src/syllabus_studio/data/catalog.json` is used, so this
works offline. The Studio has no button for installing yet; use the CLI or the
API.

To share a single course without a site, export it and send the file:

```bash
syllabus-studio export <course-id> -o applied-ml.course.json
```

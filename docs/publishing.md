# Publishing courses

Getting from a syllabus to a course readers can open with a link.

> **Nothing here goes live on its own.** Publishing writes files into `./site` on
> your machine. They're public only once you upload that folder somewhere.

```
build ──▶ write ──▶ enrich ──▶ preview ──▶ publish ──▶ host site/
 Studio    Studio    publish     Studio      Studio      you
```

## The steps

| Step | In the Studio | CLI | Needs a model |
|---|---|---|---|
| 1. Build the outline | **New course** | `syllabus-studio build syllabus.txt` | author |
| 2. Write lessons | **Write** / **Rewrite** per lesson | done by `publish` | author |
| 3. Enrich (lenses + FAQ) | done by **Publish…** | `syllabus-studio enrich <id>` | author |
| 4. Preview | **Preview as reader** | — | no |
| 5. Publish into `site/` | **Publish…** | `syllabus-studio publish <id>` | author |
| 6. Host | — | upload `site/` | no |

Configure the author model in `.env` first (see the README). With no author
model, the Studio still opens and previews, but it can't build, write or
publish.

### Preview before you publish

**Preview as reader** opens `/reader/preview/<id>/` in a new tab. It's the real
reader, showing your site as it would look if you published this course now.
The provenance card is included, and nothing is written to disk.

Read the course there before you put your name on it.

### Publish

**Publish…** in the Studio (with an optional reviewer name), or:

```bash
syllabus-studio publish <course-id> [--reviewed-by NAME] [--force] [-o copy.course.json]
```

In order, it:

1. **Writes** every lesson that isn't written yet. If one fails, it stops here
   and nothing in `site/` changes. Lessons already written are kept, so running
   it again continues from where it stopped.
2. **Enriches** every lesson, filling in only missing lenses and FAQs.
   `--force` redoes them.
3. **Stamps provenance**: the author model, the date, what was enriched, and
   `humanReviewed` if you gave a reviewer name.
4. **Writes into the site**: the reader's files, `courses/<id>.course.json`,
   then the course's entry in `catalog.json`.
5. **Reports** what a reader would find missing: unwritten lessons, missing
   lenses or FAQ, and whether it's unreviewed. These are warnings only; the
   files are already written.

The Studio then shows *Published · date* for the course. Publishing again
updates the course in place, and readers keep their progress.

**Cost:** 1 call per unwritten lesson, plus 5 per lesson to enrich. That's
about 60 calls for a 12-lesson course. On a hosted model it takes minutes and
costs cents.

### Unpublish

**Unpublish** in the Studio, or `syllabus-studio unpublish <course-id>`. It
removes the catalog entry and the bundle file, and nothing else. The course
stays in your Studio. Readers stop seeing it once you upload the updated site.

## The site folder

```
site/                    SS_SITE_DIR, default ./site (gitignored)
  index.html             the reader
  static/…               its CSS and JS, only what the reader needs
  catalog.json           every published course
  courses/<id>.course.json
  .nojekyll              so GitHub Pages serves every file as is
```

- **Relative paths only.** It works at a domain root or under a sub-path such as
  `https://<user>.github.io/<repo>/`.
- **Files you add are kept.** Refreshing the site never deletes a file it didn't
  write, so a `CNAME` for a custom domain survives.
- `syllabus-studio site build` (or `make site`) refreshes the reader's files
  only. It never publishes a course.

Open it locally exactly as a reader would:

```bash
python -m http.server -d site     # http://localhost:8000/
```

`/reader/` in the Studio server shows the same thing.

## Hosting

`site/` is plain static files. Upload the folder's contents to any static host:
GitHub Pages, Netlify, Cloudflare Pages, S3, or your own web server. There's
nothing to configure beyond serving the files.

Keep in mind:

- **Uploading makes it public.** A GitHub Pages site is public even when its
  repository is private, and Pages on a private repository needs a paid plan.
- **Readers can see everything in it**: every bundle, its provenance, and the
  catalog.
- There's no deploy command yet, so uploading is up to you.

## What readers get

- Open a link. No install, no account, no model.
- A home page listing every course, with lesson count, the model that wrote
  it, license, tags, and a *reviewed* / *not reviewed* badge.
- Lessons, quizzes, flashcards, practice, the lenses and a FAQ, all read from
  the bundle.
- Progress saved in their own browser, keyed by course and lesson id, so it
  survives updates. It doesn't sync across devices.
- No ask box. The FAQ answers the questions a reader is most likely to have.

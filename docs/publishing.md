# Publishing courses

Getting from a syllabus to a course readers can open with a link.

> **Nothing here goes live on its own.** Publishing writes files into `./site` on
> your machine. They're public only once you run `syllabus-studio deploy`.

```
build ──▶ write ──▶ enrich ──▶ preview ──▶ publish ──▶ deploy site/
 Studio    Studio    publish     Studio      Studio      CLI
```

## The steps

| Step | In the Studio | CLI | Needs a model |
|---|---|---|---|
| 1. Build the outline | **New course** | `syllabus-studio build syllabus.txt` | author |
| 2. Write lessons | **Write** / **Rewrite** per lesson | done by `publish` | author |
| 3. Enrich (lenses + FAQ) | done by **Publish…** | `syllabus-studio enrich <id>` | author |
| 4. Preview | **Preview as reader** | — | no |
| 5. Publish into `site/` | **Publish…** | `syllabus-studio publish <id>` | author |
| 6. Deploy | shows *N changes not deployed* | `syllabus-studio deploy` | no |

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
stays in your Studio. Readers stop seeing it once you deploy.

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

## Deploying to GitHub Pages

The site lives on an orphan `gh-pages` branch of this repository, checked out as
a git worktree at `./site`. Orphan means it shares no history with the code:
course content never enters `main`, and `main`'s `.gitignore` already hides
`site/`.

### Once: `syllabus-studio site init`

```bash
syllabus-studio site init
```

It makes `./site` a worktree of `gh-pages`: the existing branch if there is one
locally or on `origin`, otherwise a new orphan branch (needs git 2.42 or newer).
Then it refreshes the reader's files. Running it again changes nothing.

It never pushes and never turns Pages on, because that makes the site public.
It prints the step for you to do:

> Settings → Pages → Deploy from a branch → `gh-pages` / `(root)`

If `./site` already exists as a plain folder, `site init` stops rather than
move it. The folder is generated, so delete it, run `site init`, and publish
your courses again.

### Every time: `syllabus-studio deploy`

```
$ syllabus-studio deploy
Site: ./site  →  origin/gh-pages
  updated  Applied Machine Learning   11 lessons · reviewed
  added    Intro to Statistics        8 lessons · NOT human-reviewed
  assets   2 file(s) changed
  warning: Intro to Statistics is not human-reviewed; readers will see that
Will go live at https://cordovank.github.io/syllabus-studio/
Deploy? [y/N]
```

It compares `catalog.json` with the last deployed commit to list what's added,
updated and removed, asks, then commits everything in `./site` and pushes
`gh-pages`. Pages usually updates within a minute.

| Flag | Does |
|---|---|
| `--dry-run` | shows the list and stops; nothing committed or pushed |
| `--yes` | skips the question |
| `-m MESSAGE` | your commit message instead of *"Publish: Applied ML (updated), …"* |

- **It never force-pushes.** If `gh-pages` changed elsewhere, the push is
  rejected and your commit stays in `./site`. Pull with
  `git -C site pull --rebase`, then run `deploy` again. If two people published
  at the same time, `catalog.json` can conflict; keep both entries.
- **Nothing to deploy** is reported as such, not as an error in the site.
- The Studio says *N changes not deployed* whenever `./site` has work this
  machine hasn't pushed. It shows the command; it doesn't run it.

### Moving the site to its own repository

Push `gh-pages` to the new repository, set `SS_SITE_REMOTE` (and
`SS_SITE_BRANCH` if it isn't `gh-pages`) in `.env`, and enable Pages there.
Paths are relative, so nothing needs rebuilding for the new URL.

### Other hosts

`./site` is plain static files, so Netlify, Cloudflare Pages, S3 or any web
server work too: upload the folder's contents. `deploy` is only for GitHub Pages.

Keep in mind, wherever it's hosted:

- **Deploying makes it public.** Anyone with the link can read the site.
- **Readers can see everything in it**: every bundle, its provenance, and the
  catalog.

## What readers get

- Open a link. No install, no account, no model.
- A home page listing every course, with lesson count, the model that wrote
  it, license, tags, and a *reviewed* / *not reviewed* badge.
- Lessons, quizzes, flashcards, practice, the lenses and a FAQ, all read from
  the bundle.
- Progress saved in their own browser, keyed by course and lesson id, so it
  survives updates. It doesn't sync across devices.
- No ask box. The FAQ answers the questions a reader is most likely to have.

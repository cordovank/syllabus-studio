/**
 * The published reader: boot, routing and the home page (spec 003).
 *
 * Runs from static files — catalog.json, course bundles and these modules — on any
 * host, including a sub-path. Every URL here is relative and every route is a
 * hash, so nothing depends on where the site is served from.
 *
 *   #/                             home: continue + every course
 *   #/course/<id>                  course overview
 *   #/course/<id>/lesson/<lid>     lesson
 */

import { browserStorage, createData } from "./data.js";
import { flashcardKey, flashcardsOpen, wireFlashcards } from "./flashcards.js";
import { openCourse, showLesson, showOverview } from "./lesson.js";
import { esc } from "./markup.js";
import { courseHref, renderRail } from "./rail.js";
import { $, S, toast } from "./state.js";

S.data = createData({
  catalogUrl: new URL("catalog.json", document.baseURI).href,
  storage: browserStorage(),
  onStorageError: () =>
    toast("This browser isn't saving progress, so it will be gone when you close the page.", "bad"),
});

/* ------------------------------------------------------------------- views */

function setView(view) {
  $("app").setAttribute("data-view", view);
}

function stateBox(title, body, action = "") {
  return `<div class="state"><h2>${esc(title)}</h2><p>${esc(body)}</p>${action}</div>`;
}

function loading(text) {
  $("stage").innerHTML = `<div class="state"><div class="spinner"></div><p>${esc(text)}</p></div>`;
}

/** Absent means the catalog didn't say — not the same as "not reviewed". */
function reviewTag(entry) {
  if (entry.humanReviewed === true) return "reviewed";
  if (entry.humanReviewed === false) return "not reviewed";
  return "";
}

function courseCard(entry, doneCount) {
  const started = doneCount > 0;
  const meta = [
    entry.lessonCount ? `${entry.lessonCount} lessons` : "",
    started && entry.lessonCount ? `${doneCount} done` : "",
    reviewTag(entry),
    entry.authorModel ? `written by ${entry.authorModel}` : "",
    entry.license || "",
    ...(entry.tags || []),
  ].filter(Boolean);

  return (
    `<div class="catalog-item"${entry.preview ? ' data-preview="true"' : ""}>` +
    '<div style="flex:1 1 auto;min-width:0">' +
    (entry.preview ? '<span class="chip preview-chip">Preview</span>' : "") +
    `<h3>${esc(entry.title)}</h3>` +
    (entry.description ? `<p>${esc(entry.description)}</p>` : "") +
    `<div class="catalog-meta">${meta.map((m) => `<span class="tag">${esc(m)}</span>`).join("")}</div>` +
    "</div>" +
    `<a class="btn btn-sm ${started ? "" : "btn-primary"}" href="${esc(courseHref(entry.id))}">` +
    `${started ? "Continue" : "Start"}</a></div>`
  );
}

function doneCount(courseId) {
  return Object.values(S.data.progress(courseId)).filter((p) => p && p.done).length;
}

async function renderHome(catalog) {
  setView("home");
  S.course = null;
  S.lessonId = null;
  document.title = catalog.name;

  if (!catalog.entries.length) {
    $("stage").innerHTML = stateBox("No courses yet", "Nothing has been published here so far.");
    return;
  }

  const byId = new Map(catalog.entries.map((e) => [e.id, e]));
  const recent = S.data.library().filter((id) => byId.has(id));

  let h =
    '<div class="lesson-head">' +
    `<h1 class="lesson-title">${esc(catalog.name)}</h1>` +
    `<p class="home-sub">${catalog.entries.length} ${catalog.entries.length === 1 ? "course" : "courses"}. ` +
    "Your progress stays in this browser.</p></div>";

  if (recent.length) {
    h +=
      '<div class="block"><div class="block-head"><h2>Continue</h2></div><div class="catalog-list">' +
      recent.map((id) => courseCard(byId.get(id), doneCount(id))).join("") +
      "</div></div>";
  }

  const rest = catalog.entries.filter((e) => !recent.includes(e.id));
  if (rest.length) {
    h +=
      `<div class="block"><div class="block-head"><h2>${recent.length ? "More courses" : "All courses"}</h2></div>` +
      '<div class="catalog-list">' +
      rest.map((e) => courseCard(e, doneCount(e.id))).join("") +
      "</div></div>";
  }

  $("stage").innerHTML = h;
  $("main").scrollTop = 0;
}

/* ----------------------------------------------------------------- preview */

/**
 * The author's server marks the course being previewed in its catalog. The strip
 * is driven by that data alone, so the published reader has no preview mode to
 * leave switched on: a built catalog never carries the flag.
 */
function renderPreviewStrip(catalog) {
  const entry = catalog.entries.find((e) => e.preview);
  const strip = $("previewStrip");
  strip.hidden = !entry;
  if (!entry) return;
  $("previewText").textContent =
    `“${entry.title}” as readers will see it if you publish now. Progress here is only for this preview.`;
  strip.dataset.courseId = entry.id;
}

function resetPreview() {
  const id = $("previewStrip").dataset.courseId;
  if (!id) return;
  S.data.resetProgress(id);
  if (S.course && S.course.id === id) S.course.progress = {};
  toast("Preview progress cleared");
  route();
}

/* ------------------------------------------------------- offline and updates */

// bundle URL -> the newer publishedAt the service worker found behind a cached copy.
// Kept rather than acted on at once: the message can land before the course opens.
const newerBundles = new Map();

async function renderUpdateStrip() {
  const strip = $("updateStrip");
  const open = S.course && S.bundle;
  if (!open) {
    strip.hidden = true;
    return;
  }
  let entry;
  try {
    entry = (await S.data.catalog()).entries.find((e) => e.id === S.course.id);
  } catch {
    return; // no catalog, no course on screen to be out of date
  }
  const newer = entry ? newerBundles.get(entry.bundleUrl) || 0 : 0;
  strip.hidden = !(newer > (S.bundle.publishedAt || 0));
}

/**
 * Installable and offline (spec 003 §3). Registration fails on the author's server,
 * which doesn't serve sw.js so previews are never cached; the reader works the same
 * without it.
 */
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.type !== "bundle-updated") return;
    newerBundles.set(e.data.url, e.data.publishedAt);
    renderUpdateStrip();
  });
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

/* ------------------------------------------------------------------ router */

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    return parts.map(decodeURIComponent);
  } catch {
    return [];
  }
}

let routeToken = 0;

async function route() {
  const token = ++routeToken;
  const [kind, courseId, sub, lessonId] = parseHash();

  let catalog;
  try {
    catalog = await S.data.catalog();
  } catch (e) {
    if (token !== routeToken) return;
    setView("home");
    $("stage").innerHTML = stateBox(
      "Couldn't load the courses",
      e.message || "Try again in a moment.",
      '<button class="btn btn-primary" id="retryBtn">Try again</button>',
    );
    $("retryBtn").addEventListener("click", route);
    return;
  }
  if (token !== routeToken) return;
  S.lenses = catalog.lenses;
  renderPreviewStrip(catalog);

  if (kind !== "course" || !courseId) {
    await renderHome(catalog);
    renderUpdateStrip();
    return;
  }

  if (!S.course || S.course.id !== courseId) {
    setView("course");
    loading("Opening the course…");
    let bundle;
    try {
      bundle = await S.data.course(courseId);
    } catch (e) {
      if (token !== routeToken) return;
      $("stage").innerHTML = stateBox(
        "Couldn't open this course",
        e.message || "Try again in a moment.",
        '<a class="btn btn-primary" href="#/">All courses</a>',
      );
      return;
    }
    if (token !== routeToken) return;
    openCourse(bundle);
  }

  setView("course");
  document.title = S.course.title;
  renderUpdateStrip();

  if (sub === "lesson" && lessonId) {
    // An old link to a lesson the course no longer has lands on the overview.
    if (!showLesson(lessonId)) location.replace(courseHref(courseId));
    return;
  }
  showOverview();
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  $("outline").addEventListener("click", (e) => {
    const mod = e.target.closest(".mod-btn");
    if (!mod) return;
    const id = mod.getAttribute("data-mod");
    S.openModule = S.openModule === id ? null : id;
    renderRail();
  });

  $("drawerBtn").addEventListener("click", function () {
    const app = $("app");
    const open = app.getAttribute("data-drawer") === "open";
    app.setAttribute("data-drawer", open ? "closed" : "open");
    this.setAttribute("aria-expanded", String(!open));
  });
  $("railBackdrop").addEventListener("click", () => {
    $("app").setAttribute("data-drawer", "closed");
    $("drawerBtn").setAttribute("aria-expanded", "false");
  });

  wireFlashcards();
  $("previewReset").addEventListener("click", resetPreview);
  $("updateReload").addEventListener("click", () => location.reload());

  document.addEventListener("keydown", (e) => {
    if (!flashcardsOpen()) return;
    if (e.key === "Escape") {
      $("fcSheet").hidden = true;
      return;
    }
    if (e.key === " ") e.preventDefault();
    flashcardKey(e.key);
  });

  window.addEventListener("hashchange", route);
}

wire();
registerServiceWorker();
route();

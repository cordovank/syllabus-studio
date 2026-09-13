/** Studio: the author's page and this server's front door.
 *
 * Build a course, write its lessons (one row each), preview it in the reader, and
 * publish it into the site (spec 002 §4, spec 003 §4). Publishing never makes
 * anything live — deploying the site is a separate, deliberate step.
 */

import { api, errCopy } from "./api.js";
import { buildCourse, closeNewSheet, openNewSheet, refreshCourses } from "./library.js";
import { esc } from "./markup.js";
import { $, S, allLessons, can, hueOf, toast } from "./state.js";

/** Lesson ids with a stored lesson, for the open course. */
let written = new Set();
/** lessonId -> { state: "writing" | "failed", message } — only rows mid-flight or failed. */
const rows = new Map();
/** What the site holds: { dir, exists, courses: [{ id, title, publishedAt, humanReviewed }] }. */
let site = { dir: "site", exists: false, courses: [] };
/** The course id a publish is running for, if any. One at a time. */
let publishing = null;

const shortDate = (ms) => new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/* ------------------------------------------------------------------ render */

function authorBanner() {
  if (can("authorCourses")) return "";
  const llm = (S.health && S.health.llm && S.health.llm.author) || {};
  const provider = llm.provider || "none";

  if (provider === "none") {
    return (
      '<div class="banner"><span>No authoring model is configured, so the Studio is <b>read-only</b>: ' +
      "you can see what's written and export it, but not write more. " +
      "Set <code>SS_AUTHOR_PROVIDER</code> in <code>.env</code> and restart.</span></div>"
    );
  }
  // A provider that probes itself says exactly what's wrong; the generic line is the fallback.
  const why = llm.detail ? esc(llm.detail) : "check the terminal running the server.";
  return `<div class="banner"><span>The authoring model (<b>${esc(provider)}</b>) isn't ready — ${why}</span></div>`;
}

function stateCell(lessonId) {
  const row = rows.get(lessonId);
  if (row && row.state === "writing") return '<span class="pipe-state" data-state="writing">writing…</span>';
  if (row && row.state === "failed") return '<span class="pipe-state" data-state="failed">failed</span>';
  return written.has(lessonId)
    ? '<span class="pipe-state" data-state="written">written</span>'
    : '<span class="pipe-state" data-state="new">not written</span>';
}

function actionCell(lessonId) {
  const busy = rows.get(lessonId)?.state === "writing";
  const off = !can("authorCourses") || busy;
  const title = can("authorCourses") ? "" : ' title="Needs an authoring model"';
  return written.has(lessonId)
    ? `<button class="btn btn-ghost btn-sm" data-rewrite="${esc(lessonId)}"${off ? " disabled" : ""}${title}>Rewrite</button>`
    : `<button class="btn btn-sm" data-write="${esc(lessonId)}"${off ? " disabled" : ""}${title}>Write</button>`;
}

function render() {
  const c = S.course;
  if (!c) return;

  const total = allLessons(c).length;
  let h = authorBanner();

  const pub = site.courses.find((x) => x.id === c.id);
  const busy = publishing === c.id;
  const status = busy
    ? '<span class="pub-status" data-state="busy">Publishing…</span>'
    : pub
      ? `<span class="pub-status" data-state="published">Published${pub.publishedAt ? ` · ${esc(shortDate(pub.publishedAt))}` : ""}` +
        `${pub.humanReviewed === false ? " · not reviewed" : ""}</span>`
      : '<span class="pub-status">Not published</span>';
  const noModel = can("authorCourses") ? "" : ' title="Publishing needs an authoring model"';

  h += deployNotice();
  h +=
    '<div class="studio-head"><div>' +
    `<h1>${esc(c.title)}</h1>` +
    `<p>${written.size} of ${total} lessons written · ${status}</p>` +
    "</div><div class=\"studio-actions\">" +
    `<a class="btn btn-sm" href="${esc(api.exportUrl(c.id))}">Export bundle</a>` +
    (pub && !busy ? '<button class="btn btn-ghost btn-sm" id="unpublishBtn">Unpublish</button>' : "") +
    `<button class="btn btn-primary btn-sm" id="publishBtn"${!can("authorCourses") || publishing ? " disabled" : ""}${noModel}>` +
    `${pub ? "Republish…" : "Publish…"}</button>` +
    "</div></div>";

  h += '<div class="pipeline">';
  (c.modules || []).forEach((m, mi) => {
    h +=
      `<div class="pipe-mod" style="--hue:${hueOf(mi)}">` +
      `<span class="eyebrow">Module ${mi + 1}</span><h2>${esc(m.title)}</h2></div>`;
    for (const l of m.lessons || []) {
      const row = rows.get(l.id);
      h +=
        '<div class="pipe-row">' +
        `<span class="pipe-id">${esc(l.id)}</span>` +
        `<span class="pipe-title" title="${esc(l.title)}">${esc(l.title)}</span>` +
        stateCell(l.id) +
        actionCell(l.id) +
        (row && row.state === "failed" ? `<div class="pipe-err">${esc(row.message)}</div>` : "") +
        "</div>";
    }
  });
  h += "</div>";

  $("stage").innerHTML = h;
}

/**
 * Site-wide, so it sits above the course. Shows the command rather than a button:
 * deploying makes things public, and that stays a deliberate step in a terminal.
 */
function deployNotice() {
  const d = site.deploy;
  if (!d) return "";
  if (!d.initialised) {
    if (!site.exists) return "";
    return (
      '<div class="banner deploy-note"><span><b>The site isn\'t set up to deploy yet.</b> ' +
      "Run <code>syllabus-studio site init</code> once — see <code>docs/publishing.md</code>.</span></div>"
    );
  }
  if (!d.pending && !d.ahead) return "";
  const what = d.pending
    ? `${d.pending} ${d.pending === 1 ? "change" : "changes"} in the site`
    : `${d.ahead} ${d.ahead === 1 ? "commit" : "commits"}`;
  return (
    `<div class="banner deploy-note"><span><b>${esc(what)} not deployed.</b> ` +
    "Readers see them once you run <code>syllabus-studio deploy</code>.</span></div>"
  );
}

function renderPicker() {
  $("coursePicker").innerHTML = S.courses
    .map(
      (c) =>
        `<option value="${esc(c.id)}"${S.course && S.course.id === c.id ? " selected" : ""}>` +
        `${esc(c.title)}${c.demo ? "  (sample)" : ""}</option>`,
    )
    .join("");
}

function renderEmpty() {
  $("stage").innerHTML =
    authorBanner() +
    '<div class="state"><h2>No courses yet</h2>' +
    "<p>Paste a syllabus to lay out a course, then write its lessons here.</p>" +
    `<button class="btn btn-primary" id="emptyNew"${can("authorCourses") ? "" : " disabled"}>Turn a syllabus into a course</button></div>`;
  $("emptyNew").addEventListener("click", openNewSheet);
}

/* ----------------------------------------------------------------- actions */

async function refreshSite() {
  try {
    site = await api.site();
  } catch {
    /* the table still works without it */
  }
}

async function openStudioCourse(course) {
  S.course = course;
  const id = encodeURIComponent(course.id);
  $("readerLink").href = `/reader/preview/${id}/#/course/${id}`;
  rows.clear();
  written = new Set();
  renderPicker();
  render();
  try {
    const [built] = await Promise.all([api.builtLessons(course.id), refreshSite()]);
    written = new Set(built);
  } catch (e) {
    toast(errCopy(e), "bad");
  }
  if (S.course === course) render();
}

/* ------------------------------------------------------------------ publish */

function openPublishSheet() {
  if (!S.course || publishing) return;
  $("pubTitle").textContent = `Publish “${S.course.title}”`;
  $("pubSiteDir").textContent = site.dir;
  $("pubForm").hidden = false;
  $("pubProgress").innerHTML = "";
  $("pubResult").innerHTML = "";
  $("pubGo").hidden = false;
  $("pubGo").disabled = false;
  $("pubGo").textContent = "Publish";
  $("pubCancel").textContent = "Cancel";
  $("pubSheet").hidden = false;
  $("pubReviewer").focus();
}

function closePublishSheet() {
  $("pubSheet").hidden = true; // a running publish carries on; the rows keep showing it
}

function progressLine(p) {
  if (p.phase === "write") {
    return `Writing lessons — ${Math.min(p.done + 1, p.total)} of ${p.total}`;
  }
  return `Precomputing lenses and a FAQ — lesson ${p.done} of ${p.total}`;
}

function reportMarkup(r) {
  const warn = [];
  if (r.unwritten.length) warn.push(`${r.unwritten.length} lesson(s) not written: ${r.unwritten.join(", ")}`);
  if (r.missingLenses.length) warn.push(`${r.missingLenses.length} lesson(s) missing lenses: ${r.missingLenses.join(", ")}`);
  if (r.missingFaq.length) warn.push(`${r.missingFaq.length} lesson(s) missing a FAQ: ${r.missingFaq.join(", ")}`);
  if (!r.humanReviewed) warn.push("Marked not reviewed. Readers will see that on the course card.");
  const id = encodeURIComponent(r.courseId);

  return (
    '<div class="pub-done">' +
    `<p><b>Published into <code>${esc(r.siteDir)}/${esc(r.bundle)}</code>.</b> ` +
    "Nothing is live yet: run <code>syllabus-studio deploy</code> to put it in front of readers.</p>" +
    (warn.length
      ? `<ul class="pub-warn">${warn.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
      : "<p>Every lesson is written, with all lenses and a FAQ.</p>") +
    `<p><a class="btn btn-sm" href="/reader/#/course/${id}" target="_blank" rel="noopener">See it in the site</a></p>` +
    "</div>"
  );
}

async function runPublish() {
  const course = S.course;
  if (!course || publishing) return;
  publishing = course.id;
  let current = null;

  $("pubForm").hidden = true;
  $("pubGo").disabled = true;
  $("pubCancel").textContent = "Close";
  $("pubResult").innerHTML = "";
  $("pubProgress").innerHTML = '<div class="pub-line"><div class="spinner"></div><span>Starting…</span></div>';
  render();

  const onEvent = (_name, p) => {
    const line = $("pubProgress").querySelector(".pub-line span");
    if (line) line.textContent = progressLine(p);
    if (p.phase !== "write" || S.course !== course) return;
    if (p.stage === "writing") {
      current = p.lessonId;
      rows.set(p.lessonId, { state: "writing" });
    } else if (p.stage === "written") {
      current = null;
      rows.delete(p.lessonId);
      written.add(p.lessonId);
    }
    render();
  };

  try {
    const report = await api.publish(course.id, { reviewedBy: $("pubReviewer").value.trim() }, { onEvent });
    $("pubProgress").innerHTML = "";
    $("pubResult").innerHTML = reportMarkup(report);
    $("pubGo").hidden = true;
    toast(`Published “${course.title}” into ${site.dir}`);
  } catch (e) {
    if (current && S.course === course) rows.set(current, { state: "failed", message: errCopy(e) || "Stopped." });
    $("pubProgress").innerHTML = "";
    $("pubResult").innerHTML =
      `<div class="err"><b>Nothing was published</b>${esc(e.message || errCopy(e))}</div>`;
    $("pubGo").disabled = false;
    $("pubGo").textContent = "Try again";
  } finally {
    publishing = null;
    await refreshSite();
    if (S.course === course) render();
  }
}

async function unpublishCourse() {
  const course = S.course;
  if (!course) return;
  if (!confirm(`Take “${course.title}” out of the site? Readers still see it until the site is deployed again.`)) {
    return;
  }
  try {
    await api.unpublish(course.id);
    toast(`Removed “${course.title}” from ${site.dir}`);
  } catch (e) {
    toast(errCopy(e), "bad");
  }
  await refreshSite();
  if (S.course === course) render();
}

async function writeLesson(lessonId, force) {
  const course = S.course;
  const lesson = allLessons(course).find((x) => x.l.id === lessonId);
  if (!lesson) return;
  if (force && !confirm(`Rewrite “${lesson.l.title}”? This replaces the stored lesson and spends tokens again.`)) {
    return;
  }

  rows.set(lessonId, { state: "writing" });
  render();
  try {
    await api.generateLesson(course.id, lessonId, force);
    toast(`Wrote “${lesson.l.title}”`);
    // Lesson ids repeat across courses (m1l1…), so a write that finishes after
    // the picker moved on must not touch the rows now on screen.
    if (S.course !== course) return;
    rows.delete(lessonId);
    written.add(lessonId);
  } catch (e) {
    if (S.course !== course) return;
    rows.set(lessonId, { state: "failed", message: errCopy(e) || "Stopped." });
  }
  render();
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  $("newCourseBtn").addEventListener("click", openNewSheet);
  $("newSheetClose").addEventListener("click", closeNewSheet);
  $("newSheetCancel").addEventListener("click", closeNewSheet);
  $("buildBtn").addEventListener("click", () => buildCourse(openStudioCourse));
  $("newSheet").addEventListener("click", (e) => {
    if (e.target === $("newSheet")) closeNewSheet();
  });

  $("loadSampleBtn").addEventListener("click", async () => {
    $("sylText").value = await api.sampleSyllabus();
    $("sylName").value = "";
    $("sylText").focus();
  });

  $("depthSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-depth]");
    if (!b) return;
    S.buildDepth = b.getAttribute("data-depth");
    $("depthSeg")
      .querySelectorAll("button")
      .forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  });

  $("coursePicker").addEventListener("change", async function () {
    if (S.course && S.course.id === this.value) return;
    try {
      await openStudioCourse(await api.getCourse(this.value));
    } catch (e) {
      toast(errCopy(e), "bad");
    }
  });

  $("pubClose").addEventListener("click", closePublishSheet);
  $("pubCancel").addEventListener("click", closePublishSheet);
  $("pubGo").addEventListener("click", runPublish);
  $("pubSheet").addEventListener("click", (e) => {
    if (e.target === $("pubSheet")) closePublishSheet();
  });
  $("pubReviewer").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runPublish();
  });

  $("stage").addEventListener("click", (e) => {
    if (e.target.closest("#publishBtn")) return openPublishSheet();
    if (e.target.closest("#unpublishBtn")) return unpublishCourse();
    const w = e.target.closest("[data-write]");
    if (w) return writeLesson(w.getAttribute("data-write"), false);
    const r = e.target.closest("[data-rewrite]");
    if (r) return writeLesson(r.getAttribute("data-rewrite"), true);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("newSheet").hidden) closeNewSheet();
    if (!$("pubSheet").hidden) closePublishSheet();
  });
}

async function boot() {
  wire();

  try {
    S.health = await api.health();
  } catch {
    $("stage").innerHTML =
      '<div class="state"><h2>Can\'t reach the server</h2>' +
      "<p>The page loaded but the API didn't answer. Check the terminal running <code>python -m syllabus_studio</code>.</p></div>";
    return;
  }

  $("newCourseBtn").disabled = !can("authorCourses");

  try {
    await refreshCourses();
  } catch (e) {
    toast(errCopy(e), "bad");
    return;
  }

  renderPicker();
  if (!S.courses.length) {
    renderEmpty();
    return;
  }
  await openStudioCourse(await api.getCourse(S.courses[0].id));
}

boot();

/** Studio: the author's page and this server's front door. Build courses and write their lessons.
 *
 * Phase 1 of spec 002 — one row per lesson with Write / Rewrite. Status columns
 * (lenses, FAQ), enrichment and publish land on these same rows later; the
 * reader at /reader is the preview.
 */

import { api, errCopy } from "./api.js";
import { buildCourse, closeNewSheet, openNewSheet, refreshCourses } from "./library.js";
import { esc } from "./markup.js";
import { $, S, allLessons, can, hueOf, toast } from "./state.js";

/** Lesson ids with a stored lesson, for the open course. */
let written = new Set();
/** lessonId -> { state: "writing" | "failed", message } — only rows mid-flight or failed. */
const rows = new Map();

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

  h +=
    '<div class="studio-head"><div>' +
    `<h1>${esc(c.title)}</h1>` +
    `<p>${written.size} of ${total} lessons written</p>` +
    "</div><div class=\"studio-actions\">" +
    `<a class="btn btn-sm" href="${esc(api.exportUrl(c.id))}">Export bundle</a>` +
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

async function openStudioCourse(course) {
  S.course = course;
  $("readerLink").href = `/reader/#/course/${encodeURIComponent(course.id)}`;
  rows.clear();
  written = new Set();
  renderPicker();
  render();
  try {
    written = new Set(await api.builtLessons(course.id));
  } catch (e) {
    toast(errCopy(e), "bad");
  }
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

  $("stage").addEventListener("click", (e) => {
    const w = e.target.closest("[data-write]");
    if (w) return writeLesson(w.getAttribute("data-write"), false);
    const r = e.target.closest("[data-rewrite]");
    if (r) return writeLesson(r.getAttribute("data-rewrite"), true);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("newSheet").hidden) closeNewSheet();
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

/** Creating, importing, exporting and installing courses. */

import { api, errCopy } from "./api.js";
import { esc } from "./markup.js";
import { openCourse } from "./lesson.js";
import { $, S, canGenerate, toast } from "./state.js";

export function openNewSheet() {
  $("newSheet").hidden = false;
  $("newSheetMsg").innerHTML = "";
  $("sylText").focus();
}

export function closeNewSheet() {
  $("newSheet").hidden = true;
}

export async function refreshCourses() {
  S.courses = await api.listCourses();
  return S.courses;
}

/* -------------------------------------------------------------- build new */

export async function buildCourse() {
  const syllabus = $("sylText").value.trim();
  const name = $("sylName").value.trim();
  const msg = $("newSheetMsg");

  if (syllabus.length < 60) {
    msg.innerHTML =
      '<div class="err"><b>Not enough to work with</b>Paste at least the course description and the topic list — a few lines won\'t produce a useful course.</div>';
    return;
  }
  if (!canGenerate()) {
    msg.innerHTML =
      '<div class="err"><b>No model configured</b>Building a course needs a provider. Set one in <code>.env</code> and restart the server.</div>';
    return;
  }

  const btn = $("buildBtn");
  btn.disabled = true;
  msg.innerHTML =
    '<div class="state" style="padding:26px"><div class="spinner"></div><p>Reading the syllabus and laying out modules and lessons — about 30 seconds.</p></div>';

  try {
    const course = await api.createCourse({ syllabus, name, depth: S.buildDepth });
    await refreshCourses();
    closeNewSheet();
    $("sylText").value = "";
    $("sylName").value = "";
    await openCourse(course);
    toast(`Built “${course.title}” — ${course.modules.length} modules`);
  } catch (e) {
    msg.innerHTML = `<div class="err"><b>Couldn't build the course</b>${esc(errCopy(e))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------ import/export */

export function exportCourse() {
  if (!S.course) return;
  // A plain navigation, so the browser handles the download and the filename.
  window.location.href = api.exportUrl(S.course.id);
}

export function pickImportFile() {
  $("importFile").click();
}

export async function importPickedFile(file) {
  if (!file) return;
  try {
    const bundle = JSON.parse(await file.text());
    const course = await api.importBundle(bundle);
    await refreshCourses();
    await openCourse(course);
    toast(`Installed “${course.title}”`);
  } catch (e) {
    toast(e instanceof SyntaxError ? "That file isn't a valid course bundle." : errCopy(e), "bad");
  } finally {
    $("importFile").value = "";
  }
}

/* ----------------------------------------------------------------- catalog */

export async function openCatalog() {
  const sheet = $("catalogSheet");
  const list = $("catalogList");
  sheet.hidden = false;
  list.innerHTML = '<div class="state" style="padding:30px"><div class="spinner"></div><p>Loading the catalog…</p></div>';

  try {
    const catalog = await api.catalog();
    if (!catalog.entries.length) {
      list.innerHTML =
        '<div class="state"><h2>Nothing published yet</h2><p>Point <code>SS_CATALOG_URL</code> at a JSON index of course bundles to list them here. Any static URL will do.</p></div>';
      return;
    }
    list.innerHTML = catalog.entries
      .map(
        (e) =>
          '<div class="catalog-item"><div style="flex:1 1 auto;min-width:0">' +
          `<h3>${esc(e.title)}</h3><p>${esc(e.description)}</p>` +
          '<div class="catalog-meta">' +
          (e.author ? `<span class="tag">${esc(e.author)}</span>` : "") +
          (e.license ? `<span class="tag">${esc(e.license)}</span>` : "") +
          (e.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("") +
          "</div></div>" +
          `<button class="btn btn-sm btn-primary" data-install="${esc(e.id)}">Install</button></div>`,
      )
      .join("");
  } catch (e) {
    list.innerHTML = `<div class="err"><b>Couldn't load the catalog</b>${esc(errCopy(e))}</div>`;
  }
}

export async function installEntry(entryId, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Installing…";
  }
  try {
    const course = await api.installEntry(entryId);
    await refreshCourses();
    $("catalogSheet").hidden = true;
    await openCourse(course);
    toast(`Installed “${course.title}”`);
  } catch (e) {
    toast(errCopy(e), "bad");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Install";
    }
  }
}

/* ------------------------------------------------------------------ delete */

export async function deleteCourse() {
  if (!S.course) return;
  const title = S.course.title;
  try {
    await api.deleteCourse(S.course.id);
    await refreshCourses();
    if (S.courses.length) {
      await openCourse(await api.getCourse(S.courses[0].id));
    } else {
      S.course = null;
      $("stage").innerHTML =
        '<div class="state"><h2>No courses yet</h2><p>Paste a syllabus to build one, or install a course from the catalog.</p></div>';
    }
    toast(`Removed “${title}”`);
  } catch (e) {
    toast(errCopy(e), "bad");
  }
}

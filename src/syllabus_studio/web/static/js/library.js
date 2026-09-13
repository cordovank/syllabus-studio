/** Studio: creating courses. The reader never builds, imports or removes anything. */

import { api, errCopy } from "./api.js";
import { esc } from "./markup.js";
import { $, S, can, toast } from "./state.js";

export function openNewSheet() {
  if (!can("authorCourses")) return;
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

/** `onBuilt(course)` decides what to show next; only the Studio builds courses. */
export async function buildCourse(onBuilt) {
  const syllabus = $("sylText").value.trim();
  const name = $("sylName").value.trim();
  const msg = $("newSheetMsg");

  if (syllabus.length < 60) {
    msg.innerHTML =
      '<div class="err"><b>Not enough to work with</b>Paste at least the course description and the topic list — a few lines won\'t produce a useful course.</div>';
    return;
  }
  if (!can("authorCourses")) {
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
    await onBuilt(course);
    toast(`Built “${course.title}” — ${course.modules.length} modules`);
  } catch (e) {
    msg.innerHTML = `<div class="err"><b>Couldn't build the course</b>${esc(errCopy(e))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

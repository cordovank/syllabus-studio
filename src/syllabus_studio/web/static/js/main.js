/** Reader: boot and wiring. Served at /reader as the author's preview; the Studio boots from studio.js. */

import { api, errCopy } from "./api.js";
import { flashcardKey, flashcardsOpen, wireFlashcards } from "./flashcards.js";
import {
  deleteCourse,
  exportCourse,
  importPickedFile,
  installEntry,
  openCatalog,
  pickImportFile,
  refreshCourses,
} from "./library.js";
import { goLesson, openCourse, renderOverview } from "./lesson.js";
import { renderCapChip, renderPicker, renderRail } from "./rail.js";
import { $, S, toast } from "./state.js";

function wireChrome() {
  $("coursePicker").addEventListener("change", async function () {
    if (S.course && S.course.id === this.value) return;
    try {
      await openCourse(await api.getCourse(this.value));
    } catch (e) {
      toast(errCopy(e), "bad");
    }
  });

  $("exportBtn").addEventListener("click", exportCourse);
  $("importBtn").addEventListener("click", pickImportFile);
  $("importFile").addEventListener("change", (e) => importPickedFile(e.target.files[0]));
  $("deleteBtn").addEventListener("click", deleteCourse);

  $("catalogBtn").addEventListener("click", openCatalog);
  $("catalogClose").addEventListener("click", () => {
    $("catalogSheet").hidden = true;
  });
  $("catalogSheet").addEventListener("click", (e) => {
    if (e.target === $("catalogSheet")) $("catalogSheet").hidden = true;
    const b = e.target.closest("[data-install]");
    if (b) installEntry(b.getAttribute("data-install"), b);
  });

  $("outline").addEventListener("click", (e) => {
    const mod = e.target.closest(".mod-btn");
    if (mod) {
      const id = mod.getAttribute("data-mod");
      S.openModule = S.openModule === id ? null : id;
      renderRail();
      return;
    }
    const lesson = e.target.closest(".lesson-btn");
    if (lesson) goLesson(lesson.getAttribute("data-lesson"));
  });

  $("stage").addEventListener("click", (e) => {
    const go = e.target.closest("[data-goto]");
    if (go) goLesson(go.getAttribute("data-goto"));
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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (flashcardsOpen()) {
        $("fcSheet").hidden = true;
        return;
      }
      if (!$("catalogSheet").hidden) {
        $("catalogSheet").hidden = true;
      }
      return;
    }
    if (!flashcardsOpen()) return;
    if (e.key === " ") e.preventDefault();
    flashcardKey(e.key);
  });
}

async function boot() {
  wireChrome();

  try {
    const [health, lenses] = await Promise.all([api.health(), api.lenses()]);
    S.health = health;
    S.lenses = lenses;
  } catch (e) {
    $("stage").innerHTML =
      '<div class="state"><h2>Can\'t reach the server</h2>' +
      "<p>The page loaded but the API didn't answer. Check the terminal running <code>python -m syllabus_studio</code>.</p></div>";
    renderCapChip();
    return;
  }
  renderCapChip();

  try {
    await refreshCourses();
  } catch (e) {
    toast(errCopy(e), "bad");
    return;
  }

  if (!S.courses.length) {
    renderPicker();
    $("stage").innerHTML =
      '<div class="state"><h2>No courses yet</h2>' +
      "<p>Build one in the Studio, or install a course from the catalog.</p>" +
      '<a class="btn btn-primary" href="/studio">Open the Studio</a></div>';
    return;
  }

  // The Studio links here as ?course=<id> to preview the course it has open.
  const wanted = new URLSearchParams(location.search).get("course");
  const pick = S.courses.find((c) => c.id === wanted) || S.courses[0];
  const course = await api.getCourse(pick.id);
  await openCourse(course);

  // Open the first lesson of a sample course so the page shows what it does.
  if (course.demo) {
    const first = course.modules[0] && course.modules[0].lessons[0];
    if (first && (course.progress[first.id] || {}).built) goLesson(first.id);
    else renderOverview();
  }
}

boot();

/** Boot and wiring. Everything else is a module. */

import { api, errCopy } from "./api.js";
import { flashcardKey, flashcardsOpen, wireFlashcards } from "./flashcards.js";
import {
  buildCourse,
  closeNewSheet,
  deleteCourse,
  exportCourse,
  importPickedFile,
  installEntry,
  openCatalog,
  openNewSheet,
  pickImportFile,
  refreshCourses,
} from "./library.js";
import { goLesson, openCourse, renderOverview } from "./lesson.js";
import { renderCapChip, renderPicker, renderRail } from "./rail.js";
import { $, S, can, toast } from "./state.js";

function wireChrome() {
  $("newCourseBtn").addEventListener("click", openNewSheet);
  $("newSheetClose").addEventListener("click", closeNewSheet);
  $("newSheetCancel").addEventListener("click", closeNewSheet);
  $("buildBtn").addEventListener("click", buildCourse);
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
        return;
      }
      if (!$("newSheet").hidden) closeNewSheet();
      return;
    }
    if (!flashcardsOpen()) return;
    if (e.key === " ") e.preventDefault();
    flashcardKey(e.key);
  });
}

/** "New course" and "Build the course" need the author role. */
function gateAuthoring() {
  const ok = can("authorCourses");
  for (const id of ["newCourseBtn", "buildBtn"]) {
    const b = $(id);
    b.disabled = !ok;
    b.title = ok ? "" : "Building a course needs an authoring model, and none is configured.";
  }
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
  gateAuthoring();

  try {
    await refreshCourses();
  } catch (e) {
    toast(errCopy(e), "bad");
    return;
  }

  if (!S.courses.length) {
    renderPicker();
    if (can("authorCourses")) {
      $("stage").innerHTML =
        '<div class="state"><h2>No courses yet</h2>' +
        "<p>Paste a syllabus to build one, or install a course from the catalog.</p>" +
        '<button class="btn btn-primary" id="emptyNew">Turn a syllabus into a course</button></div>';
      $("emptyNew").addEventListener("click", openNewSheet);
    } else {
      $("stage").innerHTML =
        '<div class="state"><h2>No courses yet</h2>' +
        "<p>Install one from the catalog. This install has no authoring model, so it reads courses rather than building them.</p>" +
        '<button class="btn btn-primary" id="emptyNew">Browse courses</button></div>';
      $("emptyNew").addEventListener("click", openCatalog);
    }
    return;
  }

  const course = await api.getCourse(S.courses[0].id);
  await openCourse(course);

  // Open the first lesson of a sample course so the page shows what it does.
  if (course.demo) {
    const first = course.modules[0] && course.modules[0].lessons[0];
    if (first && (course.progress[first.id] || {}).built) goLesson(first.id);
    else renderOverview();
  }
}

boot();

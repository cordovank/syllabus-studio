/** Shared client state and the small derivations every view needs. */

export const HUES = ["#FF5A36", "#0C8E8E", "#7A4BE0", "#C98A00", "#D6316B", "#2E7BE0", "#3F8F3A"];

export const S = {
  // shared
  course: null,
  // reader
  data: null,       // data.js instance: the reader's only way to catalog, bundles and progress
  bundle: null,     // the open course's bundle: { course, lessons, provenance }
  lessons: {},      // lessonId -> LessonContent, for the open course
  lessonId: null,
  openModule: null,
  lenses: [],       // [{id, label}] from catalog.json
  // studio
  health: null,
  courses: [],
  buildDepth: "standard",
};

export const $ = (id) => document.getElementById(id);

export const hueOf = (i) => HUES[i % HUES.length];

/**
 * What this install can do, from /health. Names: authorCourses, liveTutor, liveLenses.
 * Author and reader can be different models (or none), so ask per feature.
 */
export function can(name) {
  return !!(S.health && S.health.capabilities && S.health.capabilities[name]);
}

export function allLessons(course) {
  const out = [];
  (course?.modules || []).forEach((m, mi) =>
    (m.lessons || []).forEach((l) => out.push({ m, mi, l })),
  );
  return out;
}

export function findLesson(course, lessonId) {
  const modules = course?.modules || [];
  for (let mi = 0; mi < modules.length; mi++) {
    const lessons = modules[mi].lessons || [];
    for (let li = 0; li < lessons.length; li++) {
      if (lessons[li].id === lessonId) {
        return { module: modules[mi], mi, lesson: lessons[li], li };
      }
    }
  }
  return null;
}

/** "new" (not written) | "draft" (written) | "done" (completed). */
export function lessonState(course, lessonId) {
  const p = (course?.progress || {})[lessonId];
  if (p && p.done) return "done";
  if (S.lessons[lessonId] || (p && p.built)) return "draft";
  return "new";
}

export function courseStats(course) {
  const all = allLessons(course);
  const done = all.filter((x) => ((course.progress || {})[x.l.id] || {}).done).length;
  return { done, total: all.length, pct: all.length ? Math.round((done / all.length) * 100) : 0 };
}

let toastTimer = null;

export function toast(message, kind = "ok") {
  const el = $("toast");
  if (!el) return;
  el.textContent = message;
  el.setAttribute("data-kind", kind);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, kind === "bad" ? 6000 : 3200);
}

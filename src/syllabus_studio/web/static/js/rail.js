/** The left rail: progress ring, module accordion, lesson list. */

import { esc } from "./markup.js";
import { $, S, courseStats, hueOf, lessonState } from "./state.js";

function ringSvg(pct) {
  const r = 22;
  const c = 2 * Math.PI * r;
  const off = c * (1 - pct / 100);
  return (
    '<svg width="52" height="52" viewBox="0 0 52 52" aria-hidden="true">' +
    `<circle cx="26" cy="26" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="5"></circle>` +
    `<circle cx="26" cy="26" r="${r}" fill="none" stroke="var(--accent)" stroke-width="5" ` +
    `stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"></circle>` +
    `</svg><span class="ring-num">${pct}%</span>`
  );
}

const CHEVRON =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>';

const TICK =
  '<svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="4.5" ' +
  'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg>';

export function renderRail() {
  const c = S.course;
  if (!c) {
    $("outline").innerHTML = "";
    $("ring").innerHTML = ringSvg(0);
    return;
  }

  const st = courseStats(c);
  $("ring").innerHTML = ringSvg(st.pct);
  $("railTitle").textContent = c.title;
  $("railSub").textContent = `${st.done} of ${st.total} lessons complete`;

  $("outline").innerHTML = (c.modules || [])
    .map((m, mi) => {
      const hue = hueOf(mi);
      const open = S.openModule === m.id;
      const lessons = m.lessons || [];
      const mDone = lessons.filter((l) => lessonState(c, l.id) === "done").length;

      const items = lessons
        .map((l) => {
          const state = lessonState(c, l.id);
          return (
            `<li><a class="lesson-btn" href="${esc(lessonHref(c.id, l.id))}" ` +
            `aria-current="${S.lessonId === l.id}">` +
            `<span class="dot" data-state="${state}">${TICK}</span>` +
            `<span class="lesson-name">${esc(l.title)}</span>` +
            `<span class="lesson-min">${l.minutes || 12}m</span>` +
            `</a></li>`
          );
        })
        .join("");

      return (
        `<div class="mod" data-open="${open}" style="--hue:${hue}">` +
        `<button class="mod-btn" data-mod="${esc(m.id)}" aria-expanded="${open}">` +
        `<span class="mod-idx">${mi + 1}</span>` +
        '<span style="flex:1 1 auto;min-width:0">' +
        `<span class="mod-title">${esc(m.title)}</span>` +
        `<span class="mod-count" style="display:block">${mDone}/${lessons.length} done</span>` +
        "</span>" +
        `<span class="mod-chev">${CHEVRON}</span>` +
        `</button><ul class="lessons">${items}</ul></div>`
      );
    })
    .join("");
}

/* ------------------------------------------------------------------ routes */

// Hash routes work on every static host with no rewrite rules, and survive a
// sub-path such as GitHub Pages' /<repo>/.
export const courseHref = (courseId) => `#/course/${encodeURIComponent(courseId)}`;
export const lessonHref = (courseId, lessonId) =>
  `${courseHref(courseId)}/lesson/${encodeURIComponent(lessonId)}`;

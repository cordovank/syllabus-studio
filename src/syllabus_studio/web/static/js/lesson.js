/** Course overview, lesson reader, quiz, progress and lesson generation. */

import { api, errCopy } from "./api.js";
import { esc, inl, prose } from "./markup.js";
import { renderPicker, renderRail } from "./rail.js";
import { $, S, allLessons, canGenerate, courseStats, findLesson, hueOf, lessonState, toast } from "./state.js";
import { openFlashcards } from "./flashcards.js";
import { mountTutor } from "./tutor.js";

const TICK_SM =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--good)" ' +
  'stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg>';

/* ----------------------------------------------------------------- progress */

export async function patchProgress(lessonId, patch) {
  if (!S.course) return;
  const before = S.course.progress[lessonId] || {};
  S.course.progress[lessonId] = { ...before, ...patch };
  renderRail();
  try {
    S.course = await api.setProgress(S.course.id, lessonId, patch);
    renderRail();
  } catch (e) {
    S.course.progress[lessonId] = before;
    renderRail();
    toast(errCopy(e), "bad");
  }
}

/* ----------------------------------------------------------------- overview */

export function renderOverview() {
  const c = S.course;
  if (!c) {
    $("stage").innerHTML = "";
    return;
  }
  const st = courseStats(c);
  const mins = allLessons(c).reduce((a, x) => a + (x.l.minutes || 12), 0);

  let h = "";
  h += providerBanner();
  h +=
    '<div class="lesson-head"><div class="crumb">' +
    `<span class="chip chip-plain">${c.demo ? "Sample course" : "Your course"}</span>` +
    `<span class="chip chip-plain">${esc(c.level || "intermediate")}</span>` +
    `<span class="chip chip-plain">${st.total} lessons · ~${Math.round((mins / 60) * 10) / 10}h</span>` +
    "</div>" +
    `<h1 class="lesson-title">${esc(c.title)}</h1>` +
    (c.subtitle
      ? `<p style="font-family:var(--read);font-size:19px;line-height:1.45;color:var(--ink-2);max-width:58ch">${esc(c.subtitle)}</p>`
      : "") +
    "</div>";

  if (c.skills && c.skills.length) {
    h +=
      `<div class="bigidea" style="--hue:${hueOf(0)}"><span class="eyebrow">By the end you can</span>` +
      '<ul class="prose" style="margin:6px 0 0;padding-left:20px">' +
      c.skills.map((s) => `<li>${inl(s)}</li>`).join("") +
      "</ul></div>";
  }

  h += `<div class="block"><div class="block-head"><h2>The path through</h2><span class="eyebrow">${st.done} / ${st.total} done</span></div>`;
  (c.modules || []).forEach((m, mi) => {
    const hue = hueOf(mi);
    h +=
      `<div class="card" style="--hue:${hue};margin-bottom:12px;border-left:4px solid ${hue}">` +
      '<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:7px">' +
      `<span class="chip">Module ${mi + 1}</span>` +
      `<h3 style="font-size:17.5px;letter-spacing:-.018em">${esc(m.title)}</h3></div>` +
      (m.summary
        ? `<p style="font-family:var(--read);font-size:16px;line-height:1.5;color:var(--ink-2);margin-bottom:12px">${esc(m.summary)}</p>`
        : "") +
      '<div style="display:flex;flex-wrap:wrap;gap:7px">' +
      (m.lessons || [])
        .map((l) => {
          const done = lessonState(c, l.id) === "done";
          return (
            `<button class="btn btn-sm" data-goto="${esc(l.id)}"${done ? ' style="border-color:var(--good)"' : ""}>` +
            (done ? `${TICK_SM} ` : "") +
            `${esc(l.title)}</button>`
          );
        })
        .join("") +
      "</div></div>";
  });
  h += "</div>";

  $("stage").innerHTML = h;
}

function providerBanner() {
  if (canGenerate()) return "";

  const llm = (S.health && S.health.llm) || {};
  const provider = llm.provider || "none";

  if (provider === "echo") {
    return (
      '<div class="banner"><span>Running on the <b>offline provider</b> — lessons are placeholder text. ' +
      "Pick a provider in <code>.env</code> (<code>anthropic</code> or <code>ollama</code>), then restart.</span></div>"
    );
  }

  // A provider that probes itself tells us exactly what's wrong — say that
  // rather than a generic "not configured".
  if (llm.detail) {
    return `<div class="banner"><span><b>${esc(provider)}</b> isn't ready — ${esc(llm.detail)}</span></div>`;
  }

  return (
    '<div class="banner"><span>No model is configured, so lessons can\'t be written. ' +
    "Check <code>.env</code> and the terminal running the server.</span></div>"
  );
}

/* ------------------------------------------------------------------ lesson */

export function renderLesson() {
  const c = S.course;
  const ref = findLesson(c, S.lessonId);
  if (!c || !ref) {
    renderOverview();
    return;
  }

  const hue = hueOf(ref.mi);
  const content = S.lessons[S.lessonId];
  const prog = (c.progress || {})[S.lessonId] || {};

  const head =
    `<div class="lesson-head" style="--hue:${hue}"><div class="crumb">` +
    `<span class="chip">Module ${ref.mi + 1} · ${esc(ref.module.title)}</span>` +
    `<span class="chip chip-plain">Lesson ${ref.li + 1}</span>` +
    `<span class="chip chip-plain">${ref.lesson.minutes || 12} min</span>` +
    (prog.done
      ? '<span class="chip chip-plain" style="color:var(--good);border-color:var(--good)">Complete</span>'
      : "") +
    "</div>" +
    `<h1 class="lesson-title">${esc(ref.lesson.title)}</h1>` +
    (ref.lesson.hook
      ? `<p style="font-family:var(--read);font-size:18.5px;line-height:1.45;color:var(--ink-2);max-width:56ch">${esc(ref.lesson.hook)}</p>`
      : "") +
    "</div>";

  if (!content) {
    $("stage").innerHTML =
      head +
      '<div class="state">' +
      (S.generating
        ? '<div class="spinner"></div><h2>Writing this lesson…</h2><p>The explanation, key terms, a worked example and a set of checks. This takes 20–60 seconds.</p>'
        : '<h2>This lesson hasn\'t been written yet</h2>' +
          "<p>It will be written from the syllabus and the module it sits in — explanation, key terms, a worked example, questions to check yourself, and practice.</p>" +
          '<button class="btn btn-primary" id="genBtn">Write this lesson</button>') +
      "</div><div id=\"genErr\"></div>" +
      navRow();

    const g = $("genBtn");
    if (g) g.addEventListener("click", () => generateLesson(S.lessonId));
    return;
  }

  let h = head;

  if (content.bigIdea) {
    h += `<div class="bigidea" style="--hue:${hue}"><span class="eyebrow">The big idea</span><p>${inl(content.bigIdea)}</p></div>`;
  }

  h += `<div class="prose" style="--hue:${hue}">`;
  (content.sections || []).forEach((s) => {
    if (s.heading) h += `<h3>${esc(s.heading)}</h3>`;
    h += prose(s.body);
    if (s.note) h += `<p class="section-note"><b>Worth noting — </b>${inl(s.note)}</p>`;
  });
  h += "</div>";

  if (content.worked && (content.worked.steps || []).length) {
    h +=
      `<div class="block" style="--hue:${hue}"><div class="block-head">` +
      `<h2>${esc(content.worked.title || "Worked example")}</h2><span class="eyebrow">Follow along</span></div>` +
      `<ol class="steps">${content.worked.steps.map((s) => `<li>${inl(s)}</li>`).join("")}</ol></div>`;
  }

  if ((content.keyTerms || []).length) {
    h +=
      '<div class="block"><div class="block-head"><h2>Key terms</h2>' +
      '<button class="btn btn-sm" id="fcBtn">Study as flashcards</button></div>' +
      '<dl class="terms">' +
      content.keyTerms
        .map((t) => `<div class="term"><dt>${esc(t.term)}</dt><dd>${inl(t.definition)}</dd></div>`)
        .join("") +
      "</dl></div>";
  }

  if ((content.quiz || []).length) {
    h +=
      '<div class="block"><div class="block-head"><h2>Check yourself</h2>' +
      `<span class="eyebrow score-tag" id="scoreTag">${
        prog.score != null ? `${prog.score} / ${prog.total} correct` : `${content.quiz.length} questions`
      }</span></div><div class="quiz" id="quiz">` +
      content.quiz
        .map(
          (q, qi) =>
            `<div class="q" data-q="${qi}"><p class="q-stem">${inl(q.q)}</p><div class="opts">` +
            (q.options || [])
              .map(
                (o, oi) =>
                  `<button class="opt" data-q="${qi}" data-o="${oi}">` +
                  `<span class="opt-key">${"ABCD".charAt(oi)}</span><span>${inl(o)}</span></button>`,
              )
              .join("") +
            '</div><div class="why" hidden></div></div>',
        )
        .join("") +
      "</div></div>";
  }

  if ((content.practice || []).length) {
    h +=
      '<div class="block"><div class="block-head"><h2>Practice</h2><span class="eyebrow">Do it yourself</span></div>' +
      '<div class="practice">' +
      content.practice
        .map(
          (p) =>
            `<div class="task"><p>${inl(p.task)}</p>` +
            (p.hint ? `<details class="hint"><summary>Nudge</summary><p>${inl(p.hint)}</p></details>` : "") +
            "</div>",
        )
        .join("") +
      "</div></div>";
  }

  h += mountTutor.markup();

  h +=
    '<div class="block" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">' +
    `<button class="btn ${prog.done ? "" : "btn-primary"}" id="doneBtn">` +
    `${prog.done ? "Mark as not complete" : "Mark lesson complete"}</button>` +
    '<button class="btn btn-ghost btn-sm" id="regenBtn">Rewrite this lesson</button>' +
    "</div>" +
    navRow();

  $("stage").innerHTML = h;
  wireLesson(content, ref);
}

function navRow() {
  const flat = allLessons(S.course);
  const idx = flat.findIndex((x) => x.l.id === S.lessonId);
  const prev = idx > 0 ? flat[idx - 1] : null;
  const next = idx >= 0 && idx < flat.length - 1 ? flat[idx + 1] : null;
  return (
    '<div class="block" style="display:flex;gap:10px;justify-content:space-between;border-top:1px solid var(--line);padding-top:18px">' +
    (prev ? `<button class="btn btn-sm" data-goto="${esc(prev.l.id)}">← ${esc(prev.l.title)}</button>` : "<span></span>") +
    (next ? `<button class="btn btn-sm" data-goto="${esc(next.l.id)}">${esc(next.l.title)} →</button>` : "<span></span>") +
    "</div>"
  );
}

function wireLesson(content, ref) {
  const fc = $("fcBtn");
  if (fc) fc.addEventListener("click", () => openFlashcards(content, ref));

  const quiz = $("quiz");
  if (quiz) quiz.addEventListener("click", (ev) => onQuizClick(ev, content, quiz));

  const done = $("doneBtn");
  if (done)
    done.addEventListener("click", async () => {
      const cur = ((S.course.progress || {})[S.lessonId] || {}).done;
      await patchProgress(S.lessonId, { done: !cur, built: true });
      renderLesson();
    });

  const regen = $("regenBtn");
  if (regen)
    regen.addEventListener("click", () => {
      delete S.lessons[S.lessonId];
      generateLesson(S.lessonId, true);
    });

  mountTutor.wire(content, ref);
}

function onQuizClick(ev, content, quiz) {
  const btn = ev.target.closest(".opt");
  if (!btn || btn.disabled) return;

  const qi = +btn.getAttribute("data-q");
  const oi = +btn.getAttribute("data-o");
  const q = content.quiz[qi];
  const box = quiz.querySelector(`.q[data-q="${qi}"]`);

  box.querySelectorAll(".opt").forEach((opt, i) => {
    opt.disabled = true;
    if (i === q.answer) opt.setAttribute("data-verdict", "right");
    else if (i === oi) opt.setAttribute("data-verdict", "wrong");
  });

  const why = box.querySelector(".why");
  why.innerHTML = `<b>${oi === q.answer ? "Correct" : "Not quite"}</b>${inl(q.why || "")}`;
  why.hidden = false;
  box.setAttribute("data-answered", oi === q.answer ? "1" : "0");

  if (quiz.querySelectorAll(".q[data-answered]").length === content.quiz.length) {
    const right = quiz.querySelectorAll('.q[data-answered="1"]').length;
    $("scoreTag").textContent = `${right} / ${content.quiz.length} correct`;
    patchProgress(S.lessonId, { done: true, built: true, score: right, total: content.quiz.length });
    const db = $("doneBtn");
    if (db) {
      db.textContent = "Mark as not complete";
      db.classList.remove("btn-primary");
    }
  }
}

/* -------------------------------------------------------------- generation */

export async function generateLesson(lessonId, force = false) {
  if (S.generating || !S.course) return;
  S.generating = true;
  if (S.lessonId === lessonId) renderLesson();

  try {
    const content = await api.generateLesson(S.course.id, lessonId, force);
    S.lessons[lessonId] = content;
    S.course.progress[lessonId] = { ...(S.course.progress[lessonId] || {}), built: true };
  } catch (e) {
    S.generating = false;
    if (S.lessonId === lessonId) {
      renderLesson();
      const box = $("genErr");
      const msg = errCopy(e);
      if (box && msg) box.innerHTML = `<div class="err"><b>Couldn't write the lesson</b>${esc(msg)}</div>`;
    }
    return;
  }

  S.generating = false;
  renderRail();
  if (S.lessonId === lessonId) renderLesson();
}

/* ------------------------------------------------------------- navigation */

export async function goLesson(lessonId) {
  if (!findLesson(S.course, lessonId)) return;

  S.lessonId = lessonId;
  S.openModule = findLesson(S.course, lessonId).module.id;
  S.thread = [];
  renderRail();
  renderLesson();
  $("main").scrollTop = 0;
  $("app").setAttribute("data-drawer", "closed");
  $("drawerBtn").setAttribute("aria-expanded", "false");

  if (S.lessons[lessonId]) return;

  const prog = (S.course.progress || {})[lessonId];
  if (prog && prog.built) {
    try {
      const content = await api.getLesson(S.course.id, lessonId);
      S.lessons[lessonId] = content;
      if (S.lessonId === lessonId) renderLesson();
      return;
    } catch {
      /* stored copy is gone; fall through and write it again */
    }
  }
  if (canGenerate()) generateLesson(lessonId);
}

export async function openCourse(course, lessonId) {
  S.course = course;
  S.course.progress = S.course.progress || {};
  S.lessons = {};
  S.lessonId = null;
  S.openModule = null;
  S.thread = [];

  renderPicker();
  renderRail();
  renderOverview();

  try {
    const built = await api.builtLessons(course.id);
    await Promise.all(
      built.map(async (lid) => {
        try {
          S.lessons[lid] = await api.getLesson(course.id, lid);
        } catch {
          /* ignore one missing lesson */
        }
      }),
    );
    renderRail();
    if (S.lessonId) renderLesson();
  } catch {
    /* the outline is enough to show the course */
  }

  if (lessonId) goLesson(lessonId);
}

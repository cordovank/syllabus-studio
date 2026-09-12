/**
 * Help beyond the lesson text: re-explanation lenses, the question thread, and
 * the FAQ written at authoring time.
 *
 * Each piece is gated on its own. A lens works if the course shipped it or a
 * reader model can write it; the ask box needs a reader model; the FAQ needs
 * nothing. A reader with no model at all still gets every precomputed lens.
 */

import { errCopy, streamSSE } from "./api.js";
import { esc, inl, prose } from "./markup.js";
import { $, S, can } from "./state.js";

let lensCtl = null;
let askCtl = null;

const NO_LENS = "Not included with this course, and there's no model here to write it.";

function hasStoredLens(content, id) {
  const text = content && content.lenses && content.lenses[id];
  return !!(text && text.trim());
}

/** Per lens, not all-or-nothing: a course written before a lens existed keeps the rest. */
function lensMarkup(content) {
  const live = can("liveLenses");
  const lenses = S.lenses || [];
  if (!lenses.some((l) => live || hasStoredLens(content, l.id))) return "";

  const buttons = lenses
    .map((l) => {
      const ok = live || hasStoredLens(content, l.id);
      return (
        `<button class="lens" data-lens="${esc(l.id)}" aria-pressed="false"` +
        (ok ? "" : ` disabled title="${esc(NO_LENS)}"`) +
        `>${esc(l.label)}</button>`
      );
    })
    .join("");

  return (
    '<div class="block"><div class="block-head"><h2>Not landing? Try another angle</h2></div>' +
    `<div class="lens-row">${buttons}<button class="lens" id="lensStop" hidden>Stop</button></div>` +
    '<div class="stream" id="lensOut"></div><div id="lensErr"></div></div>'
  );
}

/** Questions and answers are model text: escaped by inl/prose, never raw. */
function faqItems(faq) {
  return faq
    .map(
      (item) =>
        `<details class="hint faq-item"><summary>${inl(item.q)}</summary>` +
        `<div class="faq-a">${prose(item.a)}</div></details>`,
    )
    .join("");
}

function askMarkup(content) {
  const faq = (content && content.faq) || [];

  if (!can("liveTutor")) {
    // No chat box without a model. Matching free text against a fixed list and
    // calling it an answer would be worse than an honest list a strong model wrote.
    if (!faq.length) return "";
    return (
      '<div class="block"><div class="block-head"><h2>Common questions about this lesson</h2></div>' +
      `<div class="faq">${faqItems(faq)}</div></div>`
    );
  }

  return (
    '<div class="block"><div class="block-head"><h2>Ask about this lesson</h2></div>' +
    '<div class="thread" id="thread"></div>' +
    '<form class="ask-form" id="askForm">' +
    '<textarea id="askBox" rows="1" placeholder="What still doesn\'t make sense?" aria-label="Your question about this lesson"></textarea>' +
    '<button class="btn btn-primary" type="submit" id="askBtn">Ask</button>' +
    '</form><div id="askErr"></div>' +
    (faq.length
      ? `<details class="hint faq-more"><summary>Already answered (${faq.length})</summary>` +
        `<div class="faq">${faqItems(faq)}</div></details>`
      : "") +
    "</div>"
  );
}

function markup(content) {
  return lensMarkup(content) + askMarkup(content);
}

function wire(content, ref) {
  const row = document.querySelector(".lens-row");
  if (row) {
    row.addEventListener("click", (ev) => {
      const b = ev.target.closest(".lens");
      if (!b || b.disabled) return;
      if (b.id === "lensStop") {
        if (lensCtl) lensCtl.abort();
        return;
      }
      runLens(b.getAttribute("data-lens"));
    });
  }

  const form = $("askForm");
  if (form) {
    renderThread();
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      runAsk();
    });
    $("askBox").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        runAsk();
      }
    });
  }
}

/* ------------------------------------------------------------------ lenses */

async function runLens(lensId) {
  if (lensCtl) lensCtl.abort();
  lensCtl = new AbortController();

  document
    .querySelectorAll(".lens[data-lens]")
    .forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-lens") === lensId)));

  $("lensStop").hidden = false;
  $("lensErr").innerHTML = "";
  const out = $("lensOut");
  out.textContent = "Thinking…";

  try {
    const text = await streamSSE(
      `/courses/${encodeURIComponent(S.course.id)}/lessons/${encodeURIComponent(S.lessonId)}/lens`,
      { lens: lensId },
      { signal: lensCtl.signal, onDelta: (whole) => { out.textContent = whole; } },
    );
    out.innerHTML = `<div class="prose" style="font-size:16.5px">${prose(text)}</div>`;
  } catch (e) {
    if (e.code === "cancelled") {
      if (!e.partial) out.textContent = "";
      return;
    }
    out.textContent = e.partial || "";
    const msg = errCopy(e);
    if (msg) $("lensErr").innerHTML = `<div class="err"><b>That didn't come through</b>${esc(msg)}</div>`;
  } finally {
    const stop = $("lensStop");
    if (stop) stop.hidden = true;
  }
}

/* --------------------------------------------------------------- ask thread */

function renderThread() {
  const t = $("thread");
  if (!t) return;
  t.innerHTML = S.thread
    .map((m) =>
      m.role === "user"
        ? `<div class="msg msg-you"><span class="eyebrow">You</span>${esc(m.content)}</div>`
        : `<div class="msg msg-claude"><span class="eyebrow">Tutor</span>${m.html}</div>`,
    )
    .join("");
}

async function runAsk() {
  const box = $("askBox");
  const question = box.value.trim();
  if (!question) return;

  if (askCtl) askCtl.abort();
  askCtl = new AbortController();

  box.value = "";
  $("askErr").innerHTML = "";
  $("askBtn").disabled = true;

  // The page owns the thread; the server keeps nothing between calls.
  const turns = S.thread.map((m) => ({ role: m.role, content: m.content }));
  turns.push({ role: "user", content: question });

  S.thread.push({ role: "user", content: question, html: esc(question) });
  const pending = { role: "assistant", content: "", html: "Thinking…" };
  S.thread.push(pending);
  renderThread();

  try {
    const text = await streamSSE(
      `/courses/${encodeURIComponent(S.course.id)}/lessons/${encodeURIComponent(S.lessonId)}/ask`,
      { turns },
      {
        signal: askCtl.signal,
        onDelta: (whole) => {
          pending.content = whole;
          pending.html = esc(whole);
          renderThread();
        },
      },
    );
    pending.content = text;
    pending.html = prose(text);
    renderThread();
  } catch (e) {
    S.thread.pop();
    if (e.code !== "cancelled") {
      S.thread.pop();
      const msg = errCopy(e);
      if (msg) $("askErr").innerHTML = `<div class="err"><b>No answer came back</b>${esc(msg)}</div>`;
    }
    renderThread();
  } finally {
    const btn = $("askBtn");
    if (btn) btn.disabled = false;
  }
}

export const mountTutor = { markup, wire };

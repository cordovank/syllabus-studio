/** The two streaming features: re-explanation lenses, and the question thread. */

import { errCopy, streamSSE } from "./api.js";
import { esc, prose } from "./markup.js";
import { $, S, canGenerate } from "./state.js";

let lensCtl = null;
let askCtl = null;

function markup() {
  if (!canGenerate()) return "";

  const lenses = (S.lenses || [])
    .map((l) => `<button class="lens" data-lens="${esc(l.id)}" aria-pressed="false">${esc(l.label)}</button>`)
    .join("");

  return (
    '<div class="block"><div class="block-head"><h2>Not landing? Try another angle</h2></div>' +
    `<div class="lens-row">${lenses}<button class="lens" id="lensStop" hidden>Stop</button></div>` +
    '<div class="stream" id="lensOut"></div><div id="lensErr"></div></div>' +
    '<div class="block"><div class="block-head"><h2>Ask about this lesson</h2></div>' +
    '<div class="thread" id="thread"></div>' +
    '<form class="ask-form" id="askForm">' +
    '<textarea id="askBox" rows="1" placeholder="What still doesn\'t make sense?" aria-label="Your question about this lesson"></textarea>' +
    '<button class="btn btn-primary" type="submit" id="askBtn">Ask</button>' +
    '</form><div id="askErr"></div></div>'
  );
}

function wire(content, ref) {
  const row = document.querySelector(".lens-row");
  if (row) {
    row.addEventListener("click", (ev) => {
      const b = ev.target.closest(".lens");
      if (!b) return;
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

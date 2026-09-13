/**
 * Help beyond the lesson text: re-explanation lenses and the FAQ, both written at
 * authoring time and shipped in the bundle.
 *
 * There is no model behind a static site, so there is no ask box and no live
 * lens. A lens the course didn't ship is simply not offered — a disabled button
 * for something the reader can never have is noise. The FAQ is the answer to
 * "what if I'm still stuck": a strong model wrote it with the whole lesson in view.
 */

import { esc, inl, prose } from "./markup.js";
import { $, S } from "./state.js";

function storedLenses(content) {
  const have = (content && content.lenses) || {};
  return (S.lenses || []).filter((l) => have[l.id] && have[l.id].trim());
}

function lensMarkup(content) {
  const lenses = storedLenses(content);
  if (!lenses.length) return "";

  const buttons = lenses
    .map((l) => `<button class="lens" data-lens="${esc(l.id)}" aria-pressed="false">${esc(l.label)}</button>`)
    .join("");

  return (
    '<div class="block"><div class="block-head"><h2>Not landing? Try another angle</h2></div>' +
    `<div class="lens-row">${buttons}</div>` +
    '<div class="stream" id="lensOut" aria-live="polite"></div></div>'
  );
}

/** Questions and answers are model text: escaped by inl/prose, never raw. */
function faqMarkup(content) {
  const faq = (content && content.faq) || [];
  if (!faq.length) return "";
  return (
    '<div class="block"><div class="block-head"><h2>Common questions about this lesson</h2></div>' +
    '<div class="faq">' +
    faq
      .map(
        (item) =>
          `<details class="hint faq-item"><summary>${inl(item.q)}</summary>` +
          `<div class="faq-a">${prose(item.a)}</div></details>`,
      )
      .join("") +
    "</div></div>"
  );
}

function markup(content) {
  return lensMarkup(content) + faqMarkup(content);
}

function wire(content) {
  const row = document.querySelector(".lens-row");
  if (!row) return;
  row.addEventListener("click", (ev) => {
    const b = ev.target.closest(".lens[data-lens]");
    if (b) showLens(content, b);
  });
}

function showLens(content, button) {
  const lensId = button.getAttribute("data-lens");
  const text = content.lenses && content.lenses[lensId];
  if (!text) return;
  // Pressing the open lens again closes it.
  const closing = button.getAttribute("aria-pressed") === "true";

  document
    .querySelectorAll(".lens[data-lens]")
    .forEach((b) => b.setAttribute("aria-pressed", String(!closing && b === button)));

  $("lensOut").innerHTML = closing ? "" : `<div class="prose" style="font-size:16.5px">${prose(text)}</div>`;
}

export const mountTutor = { markup, wire };

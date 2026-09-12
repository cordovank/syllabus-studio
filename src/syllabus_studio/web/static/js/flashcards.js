/** Flashcards built from the key terms of every written lesson in a module. */

import { $, S } from "./state.js";

const FC = { cards: [], i: 0, back: false };

export function openFlashcards(content, ref) {
  const cards = [];
  (ref.module.lessons || []).forEach((l) => {
    const data = S.lessons[l.id];
    if (data && data.keyTerms) cards.push(...data.keyTerms);
  });
  if (!cards.length) cards.push(...(content.keyTerms || []));
  if (!cards.length) return;

  FC.cards = cards;
  FC.i = 0;
  FC.back = false;
  $("fcSub").textContent = `From module ${ref.mi + 1} — ${ref.module.title}. Tap the card to flip.`;
  $("fcSheet").hidden = false;
  draw();
  $("fcCard").focus();
}

function draw() {
  const c = FC.cards[FC.i];
  if (!c) return;
  $("fcSide").textContent = FC.back ? "Definition" : "Term";
  const t = $("fcText");
  t.className = FC.back ? "fc-back" : "fc-face";
  t.textContent = FC.back ? c.definition : c.term;
  $("fcCount").textContent = `${FC.i + 1} / ${FC.cards.length}`;
}

export function wireFlashcards() {
  $("fcClose").addEventListener("click", () => { $("fcSheet").hidden = true; });
  $("fcSheet").addEventListener("click", (e) => {
    if (e.target === $("fcSheet")) $("fcSheet").hidden = true;
  });
  $("fcCard").addEventListener("click", () => { FC.back = !FC.back; draw(); });
  $("fcNext").addEventListener("click", () => { FC.i = (FC.i + 1) % FC.cards.length; FC.back = false; draw(); });
  $("fcPrev").addEventListener("click", () => {
    FC.i = (FC.i - 1 + FC.cards.length) % FC.cards.length;
    FC.back = false;
    draw();
  });
}

export function flashcardsOpen() {
  return !$("fcSheet").hidden;
}

export function flashcardKey(key) {
  if (key === "ArrowRight") $("fcNext").click();
  else if (key === "ArrowLeft") $("fcPrev").click();
  else if (key === " ") $("fcCard").click();
}

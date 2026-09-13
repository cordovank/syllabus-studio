/**
 * A deliberately tiny renderer for model-written text.
 *
 * Model output is escaped FIRST, then a closed set of inline marks is applied.
 * Nothing here ever puts raw model text into innerHTML.
 */

export function esc(s) {
  return String(s == null ? "" : s).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
}

/** Inline marks: `code`, **bold**, ==highlight==, *emphasis*. */
export function inl(s) {
  return esc(s)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/==([^=\n]+)==/g, "<mark>$1</mark>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");
}

/** Block level: paragraphs, - bullets, 1. numbers, ```fences```. */
export function prose(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  let list = null;
  let para = [];
  let fence = null;

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inl(para.join(" "))}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(`<${list.tag}>${list.items.map((i) => `<li>${inl(i)}</li>`).join("")}</${list.tag}>`);
      list = null;
    }
  };
  const codeBlock = (body) =>
    '<pre style="overflow-x:auto;background:var(--surface-2);border:1px solid var(--line);' +
    'border-radius:10px;padding:13px;font-family:var(--mono);font-size:12.5px;line-height:1.5;' +
    `margin:0 0 16px"><code>${esc(body)}</code></pre>`;

  for (const raw of lines) {
    const line = raw.trim();

    if (line.slice(0, 3) === "```") {
      if (fence === null) {
        flushPara();
        flushList();
        fence = [];
      } else {
        out.push(codeBlock(fence.join("\n")));
        fence = null;
      }
      continue;
    }
    if (fence !== null) {
      fence.push(raw);
      continue;
    }
    if (!line) {
      flushPara();
      flushList();
      continue;
    }

    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const number = line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || number) {
      flushPara();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, items: [] };
      }
      list.items.push(bullet ? bullet[1] : number[1]);
      continue;
    }

    flushList();
    para.push(line);
  }

  if (fence !== null) out.push(codeBlock(fence.join("\n")));
  flushPara();
  flushList();
  return out.join("");
}

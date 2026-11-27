// Inline markdown in the composer, drawn in a mirror behind the field: the
// parser that renders sent messages (static/chat/markdown.js) decides what is
// code, bold or a link, so the composer never disagrees with the result. The
// mirror holds exactly the field's characters; markers stay, faded. Styles
// must not change glyph widths, or the caret would drift from the text.
import { parseInline } from '../markdown.js';

const esc = (value) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// HTML for `text` and whether any of it is styled. `issues` ({start, end})
// are spelling marks, drawn inside whatever style they fall in.
export function marksHtml(text, issues = []) {
  const plain = (from, to) => {
    let out = "";
    let pos = from;
    for (const issue of issues) {
      if (issue.end <= pos || issue.start >= to) continue;
      const a = Math.max(issue.start, pos);
      const b = Math.min(issue.end, to);
      out += esc(text.slice(pos, a)) + `<span class="chat-misspell" data-i="${issue.start}:${issue.end}">${esc(text.slice(a, b))}</span>`;
      pos = b;
    }
    return out + esc(text.slice(pos, to));
  };
  const mark = (from, to) => (to > from ? `<span class="cm-mark">${esc(text.slice(from, to))}</span>` : "");
  let styled = false;
  const nodes = (list, from, to) => {
    let out = "";
    let pos = from;
    for (const node of list) {
      if (node.s > pos) out += plain(pos, node.s);
      out += draw(node);
      pos = Math.max(pos, node.e);
    }
    return out + (to > pos ? plain(pos, to) : "");
  };
  const wrap = (node, cls, inner) => {
    styled = true;
    return mark(node.s, node.cs) + `<span class="${cls}">${inner}</span>` + mark(node.ce, node.e);
  };
  const draw = (node) => {
    switch (node.t) {
      case "code": return wrap(node, "cm-code", plain(node.cs, node.ce));
      case "em": case "strong": case "del": return wrap(node, "cm-" + node.t, nodes(node.c, node.cs, node.ce));
      case "link": return wrap(node, "cm-link", nodes(node.c, node.cs, node.ce));
      case "image": return wrap(node, "cm-link", plain(node.cs, node.ce));
      case "autolink": return wrap(node, "cm-link", plain(node.cs, node.ce));
      default: return plain(node.s, node.e);
    }
  };
  // `parts` is `html` cut after every newline that no markup or mark spans,
  // so a mirror can redraw only the lines that changed.
  const parts = [];
  let part = "";
  const cut = (from, to) => {
    let pos = from;
    for (let nl = text.indexOf("\n", from); nl >= 0 && nl < to; nl = text.indexOf("\n", nl + 1)) {
      if (issues.some((issue) => issue.start <= nl && issue.end > nl + 1)) continue;
      parts.push(part + plain(pos, nl + 1));
      part = "";
      pos = nl + 1;
    }
    part += plain(pos, to);
  };
  const top = (from, to) => {
    let pos = from;
    for (const node of parseInline(text.slice(from, to), false, from)) {
      if (node.s > pos) cut(pos, node.s);
      if (node.t === "text" || node.t === "br") cut(node.s, node.e);
      else part += draw(node);
      pos = Math.max(pos, node.e);
    }
    if (to > pos) cut(pos, to);
  };
  // Inline markup never crosses a blank line, as in the sent message.
  let pos = 0;
  for (const gap of text.matchAll(/\n[ \t]*\n/g)) {
    top(pos, gap.index);
    cut(gap.index, gap.index + gap[0].length);
    pos = gap.index + gap[0].length;
  }
  top(pos, text.length);
  parts.push(part);
  return { html: parts.join(""), styled, parts };
}

// Italic may slant only where it keeps every glyph's width (monospace, or a
// face without its own italic); otherwise it is tinted. Measured per font.
const italicCache = new Map();
// A width measured before the web fonts arrived belongs to the fallback.
if (typeof document !== "undefined") document.fonts?.ready.then(() => italicCache.clear());
export function italicKeepsWidth(el) {
  const style = window.getComputedStyle(el);
  const font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  if (!italicCache.has(font)) {
    const ctx = document.createElement("canvas").getContext("2d");
    const sample = "The quick brown fox jumps over 1,234 lazy dogs.";
    ctx.font = font;
    const upright = ctx.measureText(sample).width;
    ctx.font = "italic " + font;
    italicCache.set(font, Math.abs(ctx.measureText(sample).width - upright) < 0.5);
  }
  return italicCache.get(font);
}

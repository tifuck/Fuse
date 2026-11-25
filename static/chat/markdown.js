// Markdown parsing for chat: text in, tokens out. No DOM and no markup; each
// theme draws the tokens (static/themes/<t>/markdown-syntax.js).
//
// A CommonMark / GitHub subset tuned for model output. Blocks: fenced code
// (backticks or tildes), ATX and setext headings, rules, quotes (with GitHub
// alerts), lists (nested, task items), tables and paragraphs. Inline: escapes,
// code spans, emphasis, strikethrough, links, images, autolinks, line breaks.
// Every newline inside a paragraph is a line break, as chat expects. Raw HTML
// stays text, and indented code blocks are not a thing: indentation only
// nests lists.

const ALERTS = ["note", "tip", "important", "warning", "caution"];

const expandTabs = (line) => line.replace(/^[ \t]+/, (lead) => lead.replace(/\t/g, "    "));
const indentOf = (line) => line.length - line.trimStart().length;
const isBlank = (line) => !line.trim();
const dedent = (line, n) => line.slice(Math.min(n, indentOf(line)));

function fenceOpen(line) {
  const m = line.match(/^(\s*)(`{3,}|~{3,})(.*)$/);
  if (!m || (m[2][0] === "`" && m[3].includes("`"))) return null;
  return { indent: m[1].length, ch: m[2][0], n: m[2].length, info: m[3].trim() };
}

function isFenceClose(line, fence) {
  const m = line.match(/^\s*(`{3,}|~{3,})\s*$/);
  return Boolean(m && m[1][0] === fence.ch && m[1].length >= fence.n);
}

function findClose(lines, from, fence) {
  for (let j = from; j < lines.length; j += 1) if (isFenceClose(lines[j], fence)) return j;
  return -1;
}

function headingOf(line) {
  const m = line.match(/^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/);
  if (!m) return null;
  const text = (m[2] || "").replace(/(?:^|[ \t]+)#+$/, "").trim();
  return { level: m[1].length, text };
}

const isRule = (line) => /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/.test(line);
const isQuote = (line) => /^ {0,3}>/.test(line);

function listMarker(line) {
  const m = line.match(/^(\s*)([-*+]|\d{1,9}[.)])(?:([ \t]+)(.*)|[ \t]*)$/);
  if (!m) return null;
  const ordered = /\d/.test(m[2]);
  const gap = m[3] ? Math.min(m[3].length, 4) : 1;
  return {
    indent: m[1].length,
    ordered,
    bullet: ordered ? m[2].slice(-1) : m[2],
    number: ordered ? parseInt(m[2], 10) : 0,
    content: m[1].length + m[2].length + gap,
    text: m[3] && m[3].length > 4 ? " ".repeat(m[3].length - 1) + m[4] : (m[4] || ""),
  };
}

const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;

// Cells of a table row: pipes split, except escaped ones and those inside code.
export function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cell = "";
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i];
    if (ch === "\\" && s[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (ch === "`") {
      const run = s.slice(i).match(/^`+/)[0];
      const close = s.indexOf(run, i + run.length);
      if (close < 0) {
        cell += run;
        i += run.length - 1;
      } else {
        // GitHub unescapes \| inside a cell's code too.
        cell += s.slice(i, close + run.length).replace(/\\\|/g, "|");
        i = close + run.length - 1;
      }
    } else if (ch === "|") {
      cells.push(cell.trim());
      cell = "";
    } else cell += ch;
  }
  cells.push(cell.trim());
  return cells;
}

function tableAt(lines, i) {
  const head = lines[i];
  const sep = lines[i + 1];
  if (sep === undefined || !head.includes("|") || !TABLE_SEP.test(sep)) return false;
  return splitRow(sep).length === splitRow(head).length;
}

// Does this line open a block that ends a paragraph?
function interrupts(lines, i) {
  const line = lines[i];
  if (headingOf(line) || isRule(line) || isQuote(line) || tableAt(lines, i)) return true;
  if (fenceStart(lines, i)) return true;
  const marker = listMarker(line);
  return Boolean(marker && marker.text.trim() && (!marker.ordered || marker.number === 1));
}

// The fence opened on line i, or null. A bare opener or one naming a
// language always opens (a streamed block has no close yet). Longer info
// ("``` like this" in prose) needs a close below and must read like
// attributes, so a sentence that starts with backticks stays a sentence.
function fenceStart(lines, i) {
  const fence = fenceOpen(lines[i]);
  if (!fence) return null;
  const words = fence.info.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return words[0] && !/^[\w+#.-]+$/.test(words[0]) ? null : fence;
  if (!/^[\w+#.-]+$/.test(words[0]) || findClose(lines, i + 1, fence) < 0) return null;
  return words.length <= 3 || /[={]/.test(fence.info) ? fence : null;
}

function readFence(lines, i, fence) {
  const close = findClose(lines, i + 1, fence);
  const end = close < 0 ? lines.length : close;
  const body = lines.slice(i + 1, end).map((line) => dedent(line, fence.indent));
  const lang = fence.info.split(/\s+/)[0] || "";
  return { token: { type: "code", lang, info: fence.info, text: body.join("\n"), closed: close >= 0 }, next: end + (close < 0 ? 0 : 1) };
}

function readList(lines, i) {
  const first = listMarker(lines[i]);
  const reach = (marker) => Math.min(marker.content, marker.indent + 2);
  const sibling = (marker) => marker && marker.ordered === first.ordered && marker.bullet === first.bullet && marker.indent < reach(first);
  const items = [];
  let loose = false;
  while (i < lines.length) {
    const marker = listMarker(lines[i]);
    if (!sibling(marker)) break;
    const keep = reach(marker);
    const body = [marker.text];
    let lazy = Boolean(marker.text.trim());
    i += 1;
    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) {
        let j = i;
        while (j < lines.length && isBlank(lines[j])) j += 1;
        if (j < lines.length && indentOf(lines[j]) >= keep) {
          body.push(...lines.slice(i, j).map(() => ""));
          i = j;
          lazy = false;
          continue;
        }
        if (j < lines.length && sibling(listMarker(lines[j]))) loose = true;
        break;
      }
      if (indentOf(line) >= keep) {
        body.push(dedent(line, marker.content));
        lazy = !fenceOpen(line);
        i += 1;
        continue;
      }
      if (lazy && !interrupts(lines, i) && !listMarker(line)) {
        body.push(line.trim());
        i += 1;
        continue;
      }
      break;
    }
    const task = body[0].match(/^\[([ xX])\][ \t]+/);
    if (task) body[0] = body[0].slice(task[0].length);
    items.push({ task: task ? task[1] !== " " : null, children: parseLines(body) });
    if (i < lines.length && isBlank(lines[i])) {
      let j = i;
      while (j < lines.length && isBlank(lines[j])) j += 1;
      if (j < lines.length && sibling(listMarker(lines[j]))) {
        loose = true;
        i = j;
      } else break;
    }
  }
  return { token: { type: "list", ordered: first.ordered, start: first.number, loose, items }, next: i };
}

function readTable(lines, i) {
  const align = splitRow(lines[i + 1]).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    return left && right ? "center" : right ? "right" : left ? "left" : "";
  });
  const head = splitRow(lines[i]);
  const rows = [];
  let j = i + 2;
  while (j < lines.length && !isBlank(lines[j]) && lines[j].includes("|") && !fenceOpen(lines[j]) && !isQuote(lines[j]) && !headingOf(lines[j])) {
    rows.push(splitRow(lines[j]));
    j += 1;
  }
  return { token: { type: "table", align, head, rows }, next: j };
}

function readQuote(lines, i) {
  const inner = [];
  while (i < lines.length && isQuote(lines[i])) {
    inner.push(lines[i].replace(/^ {0,3}> ?/, ""));
    i += 1;
  }
  let alert = "";
  const m = (inner[0] || "").match(/^\s*\[!(\w+)\]\s*(.*)$/);
  if (m && ALERTS.includes(m[1].toLowerCase())) {
    alert = m[1].toLowerCase();
    inner[0] = m[2];
  }
  return { token: { type: "quote", alert, children: parseLines(inner) }, next: i };
}

// One block from line i: { token, next } (token null for a skipped line).
function readBlock(lines, i) {
  const line = lines[i];
  if (isBlank(line)) return { token: null, next: i + 1 };
  const fence = fenceStart(lines, i);
  if (fence) {
    // A fence "named" like a heading wraps markdown, not code.
    if (fence.info.startsWith("#")) {
      const close = findClose(lines, i + 1, fence);
      const end = close < 0 ? lines.length : close;
      return { tokens: parseLines([fence.info, ...lines.slice(i + 1, end)]), next: end + (close < 0 ? 0 : 1) };
    }
    return readFence(lines, i, fence);
  }
  const heading = headingOf(line);
  if (heading) return { token: { type: "heading", ...heading }, next: i + 1 };
  if (isRule(line)) return { token: { type: "hr" }, next: i + 1 };
  if (isQuote(line)) return readQuote(lines, i);
  if (listMarker(line)) return readList(lines, i);
  if (tableAt(lines, i)) return readTable(lines, i);
  return readParagraph(lines, i);
}

function readParagraph(lines, i) {
  const text = [];
  let j = i;
  while (j < lines.length && !isBlank(lines[j])) {
    if (text.length) {
      const setext = lines[j].match(/^ {0,3}(=+|-+)[ \t]*$/);
      if (setext) return { token: { type: "heading", level: setext[1][0] === "=" ? 1 : 2, text: text.map((line) => line.trim()).join("\n") }, next: j + 1 };
      if (interrupts(lines, j)) break;
    }
    // A model's "Run this:```bash" with the code on the very next lines.
    const slip = lines[j].match(/^(.*\S)[ \t]*(`{3,}|~{3,})([\w+#.-]*)[ \t]*$/);
    if (slip && !slip[1].includes(slip[2]) && j + 1 < lines.length && !isBlank(lines[j + 1])) {
      const fence = { indent: 0, ch: slip[2][0], n: slip[2].length, info: slip[3] };
      if (findClose(lines, j + 1, fence) >= 0) {
        text.push(slip[1]);
        const code = readFence(lines, j, fence);
        return { tokens: [{ type: "paragraph", text: text.join("\n") }, code.token], next: code.next };
      }
    }
    text.push(lines[j]);
    j += 1;
  }
  return { token: { type: "paragraph", text: text.map((line) => line.trimStart()).join("\n") }, next: j };
}

function parseLines(lines, offsets) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const { token, tokens, next } = readBlock(lines, i);
    const add = tokens || (token ? [token] : []);
    if (offsets) add.forEach((t) => { t.offset = offsets[i]; });
    out.push(...add);
    i = Math.max(next, i + 1);
  }
  return out;
}

// Block tokens for a message; top-level tokens carry `offset`, where the
// line they begin on starts in `src` (line endings as given).
export function parseBlocks(src) {
  const text = String(src || "");
  const lines = text.replace(/\r\n?/g, "\n").split("\n").map(expandTabs);
  const offsets = [0];
  for (const m of text.matchAll(/\r\n?|\n/g)) offsets.push(m.index + m[0].length);
  return parseLines(lines, offsets);
}

// A fence that only counts once its close arrives: a title after the language
// or an opener at the end of a line of prose (fenceStart, readParagraph). It
// turns paragraphs already read into code, so nothing before it is final.
const LATE_FENCE = /[^\s`~][ \t]*(?:`{3,}|~{3,})|(?:`{3,}|~{3,})[ \t]*[^\s`~]\S*[ \t]+\S/;

// Where the last top-level block starts. Every block before it is final, so
// a stream can freeze what it drew for them. 0 while there is one block.
// `from`: a boundary an earlier call gave for the start of this text. Blocks
// parse the same from there, so a long message is not read again from its
// first line on every streamed frame.
export function stableBoundary(src, from = 0) {
  const text = String(src || "");
  if (from && LATE_FENCE.test(text)) from = 0;
  const tokens = parseBlocks(from ? text.slice(from) : text);
  return tokens.length > 1 ? from + tokens[tokens.length - 1].offset : from;
}

// ---- Inline ----

const PUNCT = /[\p{P}\p{S}]/u;
const SPACE = /\s/;
const ESCAPABLE = /[!-/:-@[-`{-~]/;

function flanking(src, at, len) {
  const before = at > 0 ? src[at - 1] : " ";
  const after = at + len < src.length ? src[at + len] : " ";
  const left = !SPACE.test(after) && (!PUNCT.test(after) || SPACE.test(before) || PUNCT.test(before));
  const right = !SPACE.test(before) && (!PUNCT.test(before) || SPACE.test(after) || PUNCT.test(after));
  return { left, right, before, after };
}

// The run of backticks at `at` closes where the next run of exactly its
// length starts (-1: never). `misses` remembers, per length, the offset from
// which no closing run exists, so unmatched backticks stay linear.
function codeClose(src, at, run, misses) {
  const from = at + run.length;
  if (from >= (misses.get(run.length) ?? Infinity)) return -1;
  let close = src.indexOf(run, from);
  while (close >= 0 && (src[close + run.length] === "`" || src[close - 1] === "`")) {
    close = src.indexOf(run, close + src.slice(close).match(/^`+/)[0].length);
  }
  if (close < 0) misses.set(run.length, Math.min(from, misses.get(run.length) ?? Infinity));
  return close;
}

// Each "[" that has a matching "]" (escapes and code spans skipped), found
// in one pass.
function bracketPairs(src, misses) {
  const pairs = new Map();
  const open = [];
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === "\\") i += 1;
    else if (ch === "`") {
      const run = src.slice(i).match(/^`+/)[0];
      const close = codeClose(src, i, run, misses);
      i = (close < 0 ? i : close) + run.length - 1;
    } else if (ch === "[") open.push(i);
    else if (ch === "]" && open.length) pairs.set(open.pop(), i);
  }
  return pairs;
}

// [label](dest "title") from `at` (the "["): { label, href, end } or null.
function linkAt(src, at, pairs) {
  const i = pairs.get(at);
  if (i === undefined || src[i + 1] !== "(") return null;
  const label = src.slice(at + 1, i);
  let j = i + 2;
  while (src[j] === " ") j += 1;
  let href = "";
  if (src[j] === "<") {
    const close = src.indexOf(">", j);
    if (close < 0 || src.slice(j, close).includes("\n")) return null;
    href = src.slice(j + 1, close);
    j = close + 1;
  } else {
    let parens = 0;
    const from = j;
    for (; j < src.length; j += 1) {
      const ch = src[j];
      if (ch === "\\" && j + 1 < src.length) { j += 1; continue; }
      if (/\s/.test(ch)) break;
      if (ch === "(") parens += 1;
      else if (ch === ")") {
        if (!parens) break;
        parens -= 1;
      }
    }
    href = src.slice(from, j).replace(/\\([!-/:-@[-`{-~])/g, "$1");
  }
  while (src[j] === " ") j += 1;
  const title = src.slice(j).match(/^("[^"\n]*"|'[^'\n]*'|\([^)\n]*\))\s*/);
  if (title) j += title[0].length;
  if (src[j] !== ")") return null;
  return { label, href, close: i, end: j + 1 };
}

// A bare URL from `at`: { href, end } or null. Trailing punctuation and an
// unbalanced closing parenthesis stay outside the link.
function bareUrlAt(src, at) {
  const before = at > 0 ? src[at - 1] : " ";
  if (!/[\s(*_~"'[]/.test(before)) return null;
  const m = src.slice(at).match(/^(?:https?:\/\/|mailto:)[^\s<>`]+/i);
  if (!m) return null;
  let href = m[0];
  for (;;) {
    if (/[.,;:!?'"*_~]$/.test(href)) href = href.slice(0, -1);
    else if (href.endsWith(")") && (href.match(/\(/g) || []).length < (href.match(/\)/g) || []).length) href = href.slice(0, -1);
    else break;
  }
  return /^(?:https?:\/\/|mailto:)./i.test(href) ? { href, end: at + href.length } : null;
}

// Whether a link's text names another site than its href opens: the text
// reads as a URL or bare host (`https://github.com/x`, `github.com`) whose
// host differs. Hosts compare without "www.".
export function linkMisleads(label, href) {
  const m = String(label || "").trim().match(/^(?:https?:\/\/)?((?:[a-z0-9-]+\.)+[a-z]{2,})\.?(?::\d+)?(?:[/?#]\S*)?$/i);
  if (!m) return false;
  let host;
  try { host = new URL(href).hostname; } catch (_) { return false; }
  const bare = (h) => h.toLowerCase().replace(/^www\./, "");
  return bare(m[1]) !== bare(host);
}

function pushText(items, text, s, e) {
  const last = items[items.length - 1];
  if (last && last.t === "text") {
    last.v += text;
    last.e = e;
  } else items.push({ t: "text", v: text, s, e });
}

// Inline nodes for a span of text: { t: "text" | "code" | "br" | "em" |
// "strong" | "del" | "link" | "image" | "autolink", ... }. Every node holds
// its source range [s, e); code, emphasis and links also the range of what
// sits between their markers [cs, ce). `base` offsets them (link labels).
export function parseInline(text, inLink = false, base = 0) {
  const src = String(text || "");
  const items = [];
  const misses = new Map();
  let pairs = null;
  const linkFrom = (at) => linkAt(src, at, pairs || (pairs = bracketPairs(src, misses)));
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "\\" && i + 1 < src.length && ESCAPABLE.test(src[i + 1])) {
      pushText(items, src[i + 1], base + i, base + i + 2);
      i += 2;
      continue;
    }
    if (ch === "\\" && src[i + 1] === "\n") {
      items.push({ t: "br", s: base + i, e: base + i + 2 });
      i += 2;
      continue;
    }
    if (ch === "\n") {
      const last = items[items.length - 1];
      if (last && last.t === "text") last.v = last.v.replace(/ +$/, "");
      const from = i;
      i += 1;
      while (src[i] === " ") i += 1;
      items.push({ t: "br", s: base + from, e: base + i });
      continue;
    }
    if (ch === "`") {
      const run = src.slice(i).match(/^`+/)[0];
      const close = codeClose(src, i, run, misses);
      if (close < 0) {
        pushText(items, run, base + i, base + i + run.length);
        i += run.length;
        continue;
      }
      let code = src.slice(i + run.length, close).replace(/\n/g, " ");
      if (/^ .*[^ ].* $/s.test(code) || /^ [^ ] $/.test(code)) code = code.slice(1, -1);
      items.push({ t: "code", v: code, s: base + i, e: base + close + run.length, cs: base + i + run.length, ce: base + close });
      i = close + run.length;
      continue;
    }
    if (ch === "!" && src[i + 1] === "[") {
      const link = linkFrom(i + 1);
      if (link) {
        items.push({ t: "image", href: link.href, alt: link.label, s: base + i, e: base + link.end, cs: base + i + 2, ce: base + link.close });
        i = link.end;
        continue;
      }
    }
    if (ch === "[" && !inLink) {
      const link = linkFrom(i);
      if (link) {
        items.push({ t: "link", href: link.href, c: parseInline(link.label, true, base + i + 1), s: base + i, e: base + link.end, cs: base + i + 1, ce: base + link.close });
        i = link.end;
        continue;
      }
    }
    if (ch === "<") {
      const m = src.slice(i).match(/^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i);
      if (m && !inLink) {
        items.push({ t: "autolink", href: m[1], label: m[1], s: base + i, e: base + i + m[0].length, cs: base + i + 1, ce: base + i + m[0].length - 1 });
        i += m[0].length;
        continue;
      }
    }
    if ((ch === "h" || ch === "H" || ch === "m" || ch === "M") && !inLink) {
      const url = bareUrlAt(src, i);
      if (url) {
        items.push({ t: "autolink", href: url.href, label: url.href, s: base + i, e: base + url.end, cs: base + i, ce: base + url.end });
        i = url.end;
        continue;
      }
    }
    if (ch === "*" || ch === "_" || ch === "~") {
      const run = src.slice(i).match(ch === "*" ? /^\*+/ : ch === "_" ? /^_+/ : /^~+/)[0];
      if (ch === "~" && run.length !== 2) {
        pushText(items, run, base + i, base + i + run.length);
        i += run.length;
        continue;
      }
      const f = flanking(src, i, run.length);
      let open = f.left;
      let close = f.right;
      if (ch === "_") {
        open = f.left && (!f.right || PUNCT.test(f.before));
        close = f.right && (!f.left || PUNCT.test(f.after));
      }
      items.push({ t: "delim", ch, n: run.length, size: run.length, open, close, at: base + i });
      i += run.length;
      continue;
    }
    let j = i + 1;
    while (j < src.length && !/[\\\n`![<*_~hHmM]/.test(src[j])) j += 1;
    pushText(items, src.slice(i, j), base + i, base + j);
    i = j;
  }
  return finish(emphasis(items));
}

// CommonMark's delimiter matching: each closer takes the nearest opener of
// its kind; *, ** and *** (and _) nest, ~~ strikes.
function emphasis(items) {
  if (items.filter((item) => item.t === "delim").length > 500) return items;
  for (let c = 0; c < items.length; c += 1) {
    const closer = items[c];
    if (closer.t !== "delim" || !closer.close || !closer.n) continue;
    for (let o = c - 1; o >= 0; o -= 1) {
      const opener = items[o];
      if (opener.t !== "delim" || opener.ch !== closer.ch || !opener.open || !opener.n) continue;
      if (closer.ch !== "~" && (opener.close || closer.open) && (opener.size + closer.size) % 3 === 0 &&
          !(opener.size % 3 === 0 && closer.size % 3 === 0)) continue;
      const use = closer.ch === "~" ? 2 : opener.n >= 2 && closer.n >= 2 ? 2 : 1;
      const inner = finish(items.splice(o + 1, c - o - 1));
      // The opener gives up its last characters, the closer its first.
      const s = opener.at + opener.n - use;
      items.splice(o + 1, 0, { t: closer.ch === "~" ? "del" : use === 2 ? "strong" : "em", c: inner, s, e: closer.at + use, cs: s + use, ce: closer.at });
      opener.n -= use;
      closer.n -= use;
      closer.at += use;
      c = o + 1;
      break;
    }
  }
  return items;
}

function finish(items) {
  const out = [];
  for (const item of items) {
    if (item.t === "delim") {
      if (item.n) pushText(out, item.ch.repeat(item.n), item.at, item.at + item.n);
    } else if (item.t === "text") pushText(out, item.v, item.s, item.e);
    else out.push(item);
  }
  return out;
}

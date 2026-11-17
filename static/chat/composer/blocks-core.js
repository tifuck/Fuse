// Composer code blocks, no DOM: a message is prose and fenced code, always
// text first and last (text, code, text, code, text). Each part keeps where
// its content sits in the message, so a caret maps between the two.

const OPEN = /^ {0,3}(`{3,})([^`\n]*)$/;

export function splitBlocks(value) {
  const src = String(value || "");
  const lines = src.split("\n");
  const starts = [];
  let pos = 0;
  for (const line of lines) {
    starts.push(pos);
    pos += line.length + 1;
  }
  const lineEnd = (k) => starts[k] + lines[k].length;
  // Lines a..b-1 as prose; an empty run sits at `at`.
  const prose = (a, b, at) => (a < b
    ? { kind: "text", text: lines.slice(a, b).join("\n"), start: starts[a], end: lineEnd(b - 1) }
    : { kind: "text", text: "", start: at, end: at });
  const parts = [];
  let from = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const open = lines[i].match(OPEN);
    if (!open) continue;
    const close = new RegExp("^ {0,3}`{" + open[1].length + ",}[ \\t]*$");
    let j = i + 1;
    while (j < lines.length && !close.test(lines[j])) j += 1;
    parts.push(prose(from, i, starts[i]));
    const body = j > i + 1;
    const start = body ? starts[i + 1] : (j < lines.length ? starts[j] : lineEnd(i));
    parts.push({
      kind: "code",
      lang: open[2].trim(),
      text: body ? lines.slice(i + 1, j).join("\n") : "",
      start,
      end: body ? lineEnd(j - 1) : start,
    });
    from = j + 1;
    i = j;
  }
  parts.push(prose(from, lines.length, src.length));
  return parts;
}

// The message for parts ({kind, text, lang}); empty prose adds no line.
// With `at` ({index, offset} in a part) also returns that caret's offset.
export function joinBlocks(parts, at) {
  let out = "";
  let caret = 0;
  parts.forEach((part, index) => {
    let head = "";
    let tail = "";
    if (part.kind === "code") {
      head = (out ? "\n" : "") + "```" + (part.lang || "") + "\n";
      tail = "\n```";
    } else if (part.text && index) {
      head = "\n";
    }
    if (at && at.index === index) caret = out.length + head.length + Math.min(at.offset, part.text.length);
    out += head + part.text + tail;
  });
  return at ? { value: out, caret } : out;
}

// Message offset -> {index, offset} in a part. Offsets on a fence line snap
// to the code beside it.
export function locate(parts, offset) {
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index];
    if (offset <= part.end) return { index, offset: Math.max(0, Math.min(offset - part.start, part.text.length)) };
  }
  const index = parts.length - 1;
  return { index, offset: parts[index].text.length };
}

export function offsetOf(parts, index, offset) {
  const part = parts[index];
  return part.start + Math.max(0, Math.min(offset, part.text.length));
}

// A line typed as just ``` at the caret: the fence to close. Returns the
// text to insert after the caret and where the caret goes, or null.
export function fenceTrigger(value, caret) {
  const lineStart = value.lastIndexOf("\n", caret - 1) + 1;
  const next = value.indexOf("\n", caret);
  if (caret !== (next < 0 ? value.length : next)) return null;
  if (!/^ {0,3}```$/.test(value.slice(lineStart, caret))) return null;
  return { insert: "\n\n```", caret: caret + 1 };
}

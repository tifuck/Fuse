// Prompt library text, no DOM: a prompt is its body plus the ground rules of
// the chosen mode plus any modifiers. Fields in a body are {{Label}} (a blank
// the user must fill, left in the text and selected) or {{Label|default}}
// (the default text, kept as a tab stop).

const FIELD = /\{\{([^{}|]+)(?:\|([^{}]*))?\}\}/g;
const BLANK = /\{\{[^{}|]+\}\}/;

export function composePrompt(catalog, prompt, mode, mods = []) {
  const parts = [prompt.body, catalog.footers[mode].text];
  for (const m of catalog.modifiers) {
    if (mods.includes(m.id) && m.modes.includes(mode)) parts.push(m.text);
  }
  return parts.join("\n\n");
}

// -> { text, fields: [{ start, end, label, required }] }
export function fillFields(raw) {
  let text = "";
  let last = 0;
  const fields = [];
  for (const match of raw.matchAll(FIELD)) {
    text += raw.slice(last, match.index);
    const value = match[2] == null ? match[0] : match[2];
    fields.push({ start: text.length, end: text.length + value.length, label: match[1].trim(), required: match[2] == null });
    text += value;
    last = match.index + match[0].length;
  }
  return { text: text + raw.slice(last), fields };
}

export function listFields(body) {
  return [...body.matchAll(FIELD)].map((m) => ({ label: m[1].trim(), value: m[2] ?? null }));
}

// Moves the tab stops through one edit (before -> after). A stop the edit
// falls inside grows with it (the user is filling it); one the edit cuts
// across is dropped.
export function shiftFields(fields, before, after) {
  let p = 0;
  while (p < before.length && p < after.length && before[p] === after[p]) p++;
  let s = 0;
  while (s < before.length - p && s < after.length - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  const oldEnd = before.length - s;
  const delta = after.length - before.length;
  const out = [];
  for (const f of fields) {
    if (p >= f.start && oldEnd <= f.end) out.push({ ...f, end: f.end + delta, required: false });
    else if (f.end <= p) out.push(f);
    else if (f.start >= oldEnd) out.push({ ...f, start: f.start + delta, end: f.end + delta });
  }
  return out;
}

export function hasBlank(text) {
  return BLANK.test(text);
}

export function firstBlank(text) {
  const m = text.match(BLANK);
  return m ? m[0].slice(2, -2) : "";
}

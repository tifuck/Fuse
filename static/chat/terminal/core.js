// Terminal logic with no DOM: input framing, flow-control acks, reconnects,
// the phone key bar's keys and sticky modifiers, and the palette. The wire
// protocol is the server's (app/terminal.py); tests/js/terminal.test.cjs.

// A binary frame carries at most this many bytes, both ways.
export const MAX_FRAME = 32 * 1024;
// Bytes xterm has processed before the client acks them (and on idle).
export const ACK_EVERY = 32 * 1024;
export const ACK_IDLE_MS = 100;

const encoder = typeof TextEncoder === "function" ? new TextEncoder() : null;

// Splits bytes into frames of at most `max`, never inside a UTF-8 sequence.
export function chunkBytes(bytes, max = MAX_FRAME) {
  const out = [];
  let at = 0;
  while (at < bytes.length) {
    let end = Math.min(bytes.length, at + max);
    // Back off continuation bytes (10xxxxxx) so a character stays whole.
    if (end < bytes.length) {
      let cut = end;
      while (cut > at && (bytes[cut] & 0xc0) === 0x80) cut -= 1;
      if (cut > at) end = cut;
    }
    out.push(bytes.subarray(at, end));
    at = end;
  }
  return out;
}

// Typed or pasted text as UTF-8 frames.
export function chunkText(text, max = MAX_FRAME) {
  return text ? chunkBytes(encoder.encode(text), max) : [];
}

// xterm's onBinary data: one byte per character (mouse reports in X10 mode).
export function chunkBinary(text, max = MAX_FRAME) {
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i += 1) bytes[i] = text.charCodeAt(i) & 0xff;
  return chunkBytes(bytes, max);
}

// Counts bytes xterm finished writing and sends {t:"ack", n} once ACK_EVERY
// have built up, or after ACK_IDLE_MS of quiet for the rest.
export function createAcker(send, { every = ACK_EVERY, idleMs = ACK_IDLE_MS, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let pending = 0;
  let timer = null;
  const flush = () => {
    if (timer !== null) { clearTimer(timer); timer = null; }
    if (!pending) return;
    const n = pending;
    pending = 0;
    send({ t: "ack", n });
  };
  return {
    written(n) {
      if (!(n > 0)) return;
      pending += n;
      if (pending >= every) flush();
      else if (timer === null) timer = setTimer(flush, idleMs);
    },
    flush,
    // A new socket starts a new count: what the old one did not ack the
    // server counts again in its replay.
    reset() {
      if (timer !== null) { clearTimer(timer); timer = null; }
      pending = 0;
    },
    get pending() { return pending; },
  };
}

// Close codes the server ends a socket with on purpose: no reconnect.
const FINAL = {
  4401: "Signed out. Sign in again to use the terminal.",
  4403: "The terminal refused this page's origin.",
  4404: "This terminal session no longer exists.",
  4409: "This terminal is open in another tab or window.",
  4410: "The session ended.",
};

export function closeOutcome(code) {
  return FINAL[code] ? { retry: false, reason: FINAL[code] } : { retry: true, reason: "" };
}

// 1 s, then one more second per failed try, up to 5 s.
export function retryDelay(attempt) {
  return Math.min(5000, 1000 * Math.max(1, attempt));
}

// Line breaks in a paste run commands before the user sees them: ask first.
export function pasteNeedsConfirm(text) {
  return /[\r\n]/.test(String(text || ""));
}

// Only http(s) links open, and only after a confirm.
export function safeLink(uri) {
  try {
    const url = new URL(String(uri));
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch (_) {
    return "";
  }
}

// Sticky modifiers on the phone key bar: a tap arms the key for the next
// key only, a second tap within DOUBLE_TAP_MS locks it, a tap on a locked
// key (or a late second tap) turns it off.
export const DOUBLE_TAP_MS = 350;

export function tapModifier(mod, now) {
  if (mod.state === "off") return { state: "once", at: now };
  if (mod.state === "once" && now - mod.at <= DOUBLE_TAP_MS) return { state: "lock", at: now };
  return { state: "off", at: now };
}

// After a key went out: a one-shot modifier is spent, a lock stays.
export function spendModifier(mod) {
  return mod.state === "once" ? { state: "off", at: mod.at } : mod;
}

export const modifierOff = () => ({ state: "off", at: 0 });

// Ctrl on a typed character, as a terminal sends it.
export function ctrlChar(ch) {
  if (ch.length !== 1) return ch;
  const code = ch.charCodeAt(0);
  if (code >= 97 && code <= 122) return String.fromCharCode(code - 96);
  if (code >= 64 && code <= 95) return String.fromCharCode(code - 64);
  const extra = { " ": "\x00", "2": "\x00", "3": "\x1b", "4": "\x1c", "5": "\x1d", "6": "\x1e", "7": "\x1f", "8": "\x7f", "/": "\x1f", "?": "\x7f", "-": "\x1f", "~": "\x1e" };
  return extra[ch] ?? ch;
}

// Typed text with the key bar's armed modifiers: only a single character
// takes Ctrl; Alt prefixes ESC.
export function withModifiers(data, { ctrl = false, alt = false } = {}) {
  let out = ctrl ? ctrlChar(data) : data;
  if (alt) out = "\x1b" + out;
  return out;
}

const CSI_KEYS = { up: "A", down: "B", right: "C", left: "D", home: "H", end: "F" };
const TILDE_KEYS = { pgup: "5", pgdn: "6", insert: "2", delete: "3" };

// The bytes one key-bar key sends. appCursor: the program switched the
// arrows to application mode (DECCKM, e.g. vim, less).
export function keySequence(name, { ctrl = false, alt = false, appCursor = false } = {}) {
  const mod = 1 + (alt ? 2 : 0) + (ctrl ? 4 : 0);
  if (CSI_KEYS[name]) {
    if (mod > 1) return `\x1b[1;${mod}${CSI_KEYS[name]}`;
    return (appCursor ? "\x1bO" : "\x1b[") + CSI_KEYS[name];
  }
  if (TILDE_KEYS[name]) return `\x1b[${TILDE_KEYS[name]}${mod > 1 ? ";" + mod : ""}~`;
  if (name === "esc") return alt ? "\x1b\x1b" : "\x1b";
  if (name === "tab") return alt ? "\x1b\t" : "\t";
  if (name === "enter") return alt ? "\x1b\r" : "\r";
  return withModifiers(name, { ctrl, alt });
}

// The terminal's colours from the theme's tokens (#rrggbb each): the
// surface, its text, the accent for the cursor and selection, and the
// theme's own red, green and muted grey. The rest of the 16 come from a
// dark or light set; xterm's minimumContrastRatio keeps them all readable.
const BASE = {
  dark: {
    black: "#3b3f45", red: "#f87171", green: "#4ade80", yellow: "#e5c07b", blue: "#61afef", magenta: "#c678dd", cyan: "#56b6c2", white: "#d0d4d8",
    brightBlack: "#7f868f", brightRed: "#ff7b72", brightGreen: "#7ee787", brightYellow: "#f2cc60", brightBlue: "#79c0ff", brightMagenta: "#d2a8ff", brightCyan: "#a5f3fc", brightWhite: "#ffffff",
  },
  light: {
    black: "#24292f", red: "#c24b40", green: "#2f7a48", yellow: "#9a6700", blue: "#0969da", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781",
    brightBlack: "#57606a", brightRed: "#cf222e", brightGreen: "#1a7f37", brightYellow: "#7d4e00", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#8c959f",
  },
};
const HEX = /^#[0-9a-f]{6}$/i;

// A computed CSS colour ("rgb(…)", "rgba(…)", "color(srgb …)", as
// getComputedStyle reports one) as #rrggbb; "" for transparent or unknown.
export function cssColorHex(value) {
  const text = String(value || "").trim();
  let parts = null;
  let scale = 1;
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(text);
  const srgb = /^color\(srgb\s+([^)]+)\)$/i.exec(text);
  if (rgb) parts = rgb[1].split(/[\s,/]+/).filter(Boolean);
  else if (srgb) { parts = srgb[1].split(/[\s/]+/).filter(Boolean); scale = 255; }
  else return HEX.test(text) ? text.toLowerCase() : "";
  const [r, g, b, a] = parts.map(Number);
  if ([r, g, b].some((n) => !Number.isFinite(n)) || (a !== undefined && !(a > 0.01))) return "";
  return "#" + [r, g, b].map((n) => Math.round(Math.min(255, Math.max(0, n * scale))).toString(16).padStart(2, "0")).join("");
}

export function terminalPalette(tokens, scheme) {
  const light = scheme === "light";
  const base = BASE[light ? "light" : "dark"];
  const pick = (value, fallback) => (HEX.test(value || "") ? value.toLowerCase() : fallback);
  const background = pick(tokens.bg, light ? "#ffffff" : "#000000");
  const foreground = pick(tokens.fg, light ? "#1c1b18" : "#e8ebe8");
  const accent = pick(tokens.accent, foreground);
  return {
    ...base,
    background,
    foreground,
    cursor: pick(tokens.cursor, accent),
    cursorAccent: background,
    selectionBackground: accent + (light ? "40" : "55"),
    selectionInactiveBackground: accent + "2a",
    red: pick(tokens.red, base.red),
    green: pick(tokens.green, base.green),
    brightBlack: pick(tokens.mute, base.brightBlack),
    scrollbarSliderBackground: foreground + "22",
    scrollbarSliderHoverBackground: foreground + "44",
    scrollbarSliderActiveBackground: foreground + "66",
  };
}

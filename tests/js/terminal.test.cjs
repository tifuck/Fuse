// The browser terminal's pure logic (static/chat/terminal/core.js): input
// frames, flow-control acks, reconnects, the phone key bar and the palette.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const core = () => import('../../static/chat/terminal/core.js');

test('input goes out in frames of at most 32 KiB, never splitting a character', async () => {
  const { chunkText, chunkBinary, MAX_FRAME } = await core();
  assert.equal(MAX_FRAME, 32768);
  assert.deepEqual(chunkText(''), []);
  assert.deepEqual(chunkText('ls\r').map((c) => [...c]), [[108, 115, 13]]);
  // 2-byte characters across the boundary: the cut backs off to a whole one.
  const frames = chunkText('é'.repeat(20000));
  assert.deepEqual(frames.map((c) => c.length), [32768, 7232]);
  const text = new TextDecoder('utf-8', { fatal: true });
  assert.equal(frames.map((c) => text.decode(c)).join(''), 'é'.repeat(20000));
  // 4-byte characters with a small frame size.
  const emoji = chunkText('a😀😀', 4);
  assert.deepEqual(emoji.map((c) => c.length), [1, 4, 4]);
  // xterm's binary strings are one byte per character.
  assert.deepEqual([...chunkBinary('\x1b[M !!')[0]], [27, 91, 77, 32, 33, 33]);
});

test('acks are batched every 32 KiB processed, the rest after a quiet moment', async () => {
  const { createAcker } = await core();
  const sent = [];
  const timers = new Map();
  let id = 0;
  const acker = createAcker((msg) => sent.push(msg), {
    every: 32768,
    setTimer: (fn) => { id += 1; timers.set(id, fn); return id; },
    clearTimer: (t) => timers.delete(t),
  });
  acker.written(30000);
  assert.deepEqual(sent, []);
  assert.equal(timers.size, 1);
  acker.written(3000);
  assert.deepEqual(sent, [{ t: 'ack', n: 33000 }]);
  assert.equal(timers.size, 0, 'a sent batch cancels the idle timer');
  acker.written(10);
  acker.written(0);
  [...timers.values()][0]();
  assert.deepEqual(sent.at(-1), { t: 'ack', n: 10 });
  // A new socket forgets what the old one had not acked.
  acker.written(500);
  acker.reset();
  assert.equal(acker.pending, 0);
  acker.flush();
  assert.equal(sent.length, 2);
});

test('close codes the server means are final are not retried; backoff is 1 s up to 5 s', async () => {
  const { closeOutcome, retryDelay } = await core();
  for (const code of [4401, 4403, 4404, 4409, 4410]) assert.equal(closeOutcome(code).retry, false, String(code));
  for (const code of [1000, 1006, 1011, 1012, 4000]) assert.equal(closeOutcome(code).retry, true, String(code));
  assert.deepEqual([1, 2, 3, 4, 5, 6, 20].map(retryDelay), [1000, 2000, 3000, 4000, 5000, 5000, 5000]);
});

test('sticky modifiers: a tap arms one key, a quick second tap locks, a tap on a lock releases', async () => {
  const { tapModifier, spendModifier, modifierOff, DOUBLE_TAP_MS } = await core();
  let ctrl = modifierOff();
  ctrl = tapModifier(ctrl, 1000);
  assert.equal(ctrl.state, 'once');
  assert.equal(spendModifier(ctrl).state, 'off');
  ctrl = tapModifier(ctrl, 1000 + DOUBLE_TAP_MS);
  assert.equal(ctrl.state, 'lock');
  assert.equal(spendModifier(ctrl).state, 'lock', 'a lock outlives a key');
  assert.equal(tapModifier(ctrl, 5000).state, 'off');
  // A slow second tap turns a one-shot off instead of locking it.
  assert.equal(tapModifier({ state: 'once', at: 0 }, DOUBLE_TAP_MS + 1).state, 'off');
});

test('key bar keys and typed characters send what a terminal sends', async () => {
  const { keySequence, withModifiers } = await core();
  assert.equal(keySequence('esc'), '\x1b');
  assert.equal(keySequence('tab'), '\t');
  assert.equal(keySequence('up'), '\x1b[A');
  assert.equal(keySequence('up', { appCursor: true }), '\x1bOA');
  assert.equal(keySequence('left', { ctrl: true }), '\x1b[1;5D');
  assert.equal(keySequence('right', { alt: true }), '\x1b[1;3C');
  assert.equal(keySequence('home', { ctrl: true, alt: true }), '\x1b[1;7H');
  assert.equal(keySequence('end'), '\x1b[F');
  assert.equal(keySequence('pgup'), '\x1b[5~');
  assert.equal(keySequence('pgdn', { ctrl: true }), '\x1b[6;5~');
  assert.equal(keySequence('|'), '|');
  assert.equal(keySequence('/', { ctrl: true }), '\x1f');
  assert.equal(keySequence('-', { alt: true }), '\x1b-');
  assert.equal(withModifiers('c', { ctrl: true }), '\x03');
  assert.equal(withModifiers('C', { ctrl: true }), '\x03');
  assert.equal(withModifiers('[', { ctrl: true }), '\x1b');
  assert.equal(withModifiers(' ', { ctrl: true }), '\x00');
  assert.equal(withModifiers('b', { alt: true }), '\x1bb');
  assert.equal(withModifiers('x', { ctrl: true, alt: true }), '\x1b\x18');
  // A whole word from a keyboard's suggestion takes no Ctrl.
  assert.equal(withModifiers('hello', { ctrl: true }), 'hello');
  assert.equal(withModifiers('ls'), 'ls');
});

test('the palette comes from the theme tokens, with a dark or light set for the rest', async () => {
  const { terminalPalette, cssColorHex } = await core();
  const dark = terminalPalette({ bg: '#000000', fg: '#E8EBE8', accent: '#a5d8ff', red: '#f87171', green: '#4ade80', mute: '#8f978f' }, 'dark');
  assert.equal(dark.background, '#000000');
  assert.equal(dark.foreground, '#e8ebe8');
  assert.equal(dark.cursor, '#a5d8ff');
  assert.equal(dark.cursorAccent, '#000000');
  assert.equal(dark.red, '#f87171');
  assert.equal(dark.brightBlack, '#8f978f');
  assert.match(dark.selectionBackground, /^#a5d8ff[0-9a-f]{2}$/);
  const light = terminalPalette({ bg: '#ffffff', fg: '#1c1b18', accent: 'color-mix(nope)' }, 'light');
  assert.equal(light.background, '#ffffff');
  assert.equal(light.cursor, '#1c1b18', 'a token that is not a colour falls back');
  assert.equal(light.blue, '#0969da');
  assert.notEqual(light.yellow, dark.yellow);
  for (const key of ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white', 'brightWhite']) assert.match(light[key], /^#[0-9a-f]{6}$/, key);
  // Computed colours as browsers report them.
  assert.equal(cssColorHex('rgb(14, 14, 14)'), '#0e0e0e');
  assert.equal(cssColorHex('rgba(255, 0, 128, 0.9)'), '#ff0080');
  assert.equal(cssColorHex('rgba(0, 0, 0, 0)'), '');
  assert.equal(cssColorHex('color(srgb 1 0.5 0)'), '#ff8000');
  assert.equal(cssColorHex('color(srgb 1 1 1 / 0)'), '');
  assert.equal(cssColorHex('#ABCDEF'), '#abcdef');
  assert.equal(cssColorHex('oklch(0.5 0.1 200)'), '');
});

test('only http(s) links open; any paste with a line break asks first', async () => {
  const { safeLink, pasteNeedsConfirm } = await core();
  assert.equal(safeLink('https://example.com/a?b=1'), 'https://example.com/a?b=1');
  assert.equal(safeLink('http://10.0.0.1:8080'), 'http://10.0.0.1:8080/');
  for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'not a url', '']) assert.equal(safeLink(bad), '', bad);
  assert.equal(pasteNeedsConfirm('ls -la'), false);
  assert.equal(pasteNeedsConfirm('rm -rf build\n'), true);
  assert.equal(pasteNeedsConfirm('a\r\nb'), true);
});

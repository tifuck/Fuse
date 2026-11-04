const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

const source = readFileSync(resolve(__dirname, '../static/sounds.js'), 'utf8');

// A fake Web Audio graph: counts started sources so a test can tell whether a
// sound actually played.
function boot({ saved, visible = true, focused = true, state = 'running', activation = false } = {}) {
  const storage = new Map(saved ? [['fuse.sounds.v1', JSON.stringify(saved)]] : []);
  const node = () => ({ connect() {}, start() { world.started += 1; }, stop() {}, frequency: param(), detune: {}, Q: {}, gain: param(), type: '' });
  const param = () => ({ value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} });
  const vibrations = [];
  const copied = [];
  let now = 1_000_000;
  const world = {
    started: 0,
    Date: { now: () => now },
    document: {
      visibilityState: visible ? 'visible' : 'hidden',
      hasFocus: () => focused,
      readyState: 'complete',
      querySelectorAll: () => [],
    },
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    navigator: {
      vibrate: (pattern) => vibrations.push(pattern),
      userActivation: { isActive: activation },
      clipboard: { writeText: async (text) => { copied.push(text); } },
    },
    AudioContext: class {
      constructor() { this.state = state; this.currentTime = 0; this.sampleRate = 8000; this.destination = {}; }
      // Like the browser: the state flips later, not during the call.
      resume() { return Promise.resolve().then(() => { if (activation) this.state = 'running'; }); }
      createOscillator() { return node(); }
      createGain() { return node(); }
      createBiquadFilter() { return node(); }
      createDynamicsCompressor() { return { ...node(), threshold: {}, knee: {}, ratio: {} }; }
      createBufferSource() { return node(); }
      createBuffer(_, length) { return { getChannelData: () => new Float32Array(length) }; }
    },
    addEventListener() {},
  };
  world.window = world;
  vm.runInNewContext(source, world);
  return {
    sound: world.fuseSound,
    world, storage, vibrations, copied,
    tick(ms) { now += ms; },
  };
}

test('key moments mode plays key sounds and holds back the "all" tier', () => {
  const ui = boot();
  assert.equal(ui.sound.play('done'), true);
  ui.tick(1000);
  assert.equal(ui.sound.play('saved'), false);
  ui.sound.setPrefs({ mode: 'all' });
  assert.equal(ui.sound.play('saved'), true);
  assert.equal(ui.sound.play('nope'), false);
});

test('routine actions have no sound', () => {
  const ui = boot({ saved: { mode: 'all', volume: 0.7 } });
  for (const name of ['newChat', 'attach', 'archive', 'offline', 'online', 'undo', 'redo', 'rewind', 'compacted', 'allow', 'deny']) {
    assert.equal(name in ui.sound.SOUNDS, false, name);
  }
});

test('off mode and zero volume are silent', () => {
  assert.equal(boot({ saved: { mode: 'off', volume: 0.7 } }).sound.play('send'), false);
  assert.equal(boot({ saved: { mode: 'key', volume: 0 } }).sound.play('send'), false);
});

test('events play only in a visible, focused window; user actions play anywhere', () => {
  const blurred = boot({ focused: false });
  assert.equal(blurred.sound.play('done', { event: true }), false);
  assert.equal(blurred.sound.play('send'), true);
  assert.equal(boot({ visible: false }).sound.play('permission', { event: true }), false);
});

test('the same sound cannot repeat within its gap, and subagent bursts merge', () => {
  const ui = boot();
  assert.equal(ui.sound.play('copy'), true);
  ui.tick(50);
  assert.equal(ui.sound.play('copy'), false);
  ui.tick(100);
  assert.equal(ui.sound.play('copy'), true);
  ui.tick(1000);
  assert.equal(ui.sound.play('agentDone'), true);
  ui.tick(300);
  assert.equal(ui.sound.play('agentDone'), false);
});

test('a higher-priority sound swallows a lower one right after it', () => {
  const ui = boot();
  assert.equal(ui.sound.play('error'), true);
  ui.tick(100);
  assert.equal(ui.sound.play('done'), false);
  ui.tick(300);
  assert.equal(ui.sound.play('done'), true);
  ui.tick(50);
  assert.equal(ui.sound.play('error'), true, 'a higher one is never swallowed');
});

test('at most four sounds a second', () => {
  const ui = boot();
  const names = ['send', 'copy', 'micOn', 'micOff', 'queue'];
  const played = names.map((name) => { ui.tick(130); return ui.sound.play(name); });
  assert.deepEqual(played, [true, true, true, true, false]);
});

test('a suspended context plays only inside a user gesture, never late', async () => {
  assert.equal(boot({ state: 'suspended' }).sound.play('send'), false);
  const ui = boot({ state: 'suspended', activation: true });
  assert.equal(ui.sound.play('send'), true);
  await new Promise((r) => setImmediate(r));
  assert.ok(ui.world.started > 0);
});

test('stop and permission asks vibrate; plain sounds do not', () => {
  const ui = boot();
  ui.sound.play('stop');
  ui.tick(1000);
  ui.sound.play('send');
  assert.deepEqual(ui.vibrations, [12]);
});

test('every clipboard copy plays the copy sound', async () => {
  const ui = boot();
  await ui.world.navigator.clipboard.writeText('hello');
  assert.deepEqual(ui.copied, ['hello']);
  ui.tick(50);
  assert.equal(ui.sound.play('copy'), false, 'copy just played');
});

test('prefs survive bad storage and clamp volume', () => {
  const ui = boot();
  ui.storage.set('fuse.sounds.v1', '{bad');
  assert.deepEqual({ ...ui.sound.prefs() }, { mode: 'key', volume: 0.7 });
  ui.sound.setPrefs({ mode: 'loud', volume: 4 });
  assert.deepEqual({ ...ui.sound.prefs() }, { mode: 'key', volume: 1 });
});

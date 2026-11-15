// FuseChatAppearance.addWallpaper: a data-only spec is validated
// (validateWallpaper), registered as a scene and listed in the catalog; a bad
// spec, a bad key or a built-in key is refused and registers nothing.
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

function fakeContext() {
  return new Proxy({}, {
    get(target, key) {
      if (key in target) return target[key];
      if (key === 'createLinearGradient' || key === 'createRadialGradient') return () => ({ addColorStop() {} });
      return () => {};
    },
    set(target, key, value) { target[key] = value; return true; },
  });
}

const EXAMPLE = () => {
  const doc = readFileSync(resolve(__dirname, '../docs/wallpapers.md'), 'utf8');
  return JSON.parse(doc.split('## Example')[1].match(/```json\n([\s\S]+?)\n```/)[1]);
};

async function loadEngine() {
  const events = [];
  globalThis.window = globalThis;
  globalThis.document = {
    documentElement: { dataset: {}, style: {} },
    createElement: () => ({ width: 0, height: 0, getContext: () => fakeContext() }),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    dispatchEvent: (event) => events.push(event.type),
  };
  await import('../static/chat-backdrop.js');
  const { SCENES } = await import('../static/backdrops/scenes.js');
  return { engine: globalThis.FuseBackdrop, SCENES, events };
}

function bootAppearance(engine) {
  const html = { dataset: {}, style: { setProperty() {}, removeProperty() {} } };
  const events = [];
  const world = {
    document: {
      cookie: '',
      documentElement: html,
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      dispatchEvent: (event) => events.push(event.type),
    },
    localStorage: { getItem: () => null, setItem() {} },
    CustomEvent: class { constructor(type) { this.type = type; } },
    FuseBackdrop: engine,
  };
  world.window = world;
  vm.runInNewContext(readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8'), world);
  return { appearance: world.FuseChatAppearance, html, events };
}

test('addWallpaper registers a valid spec and lists it for the picker', async () => {
  const { engine, SCENES } = await loadEngine();
  const { appearance, html, events } = bootAppearance(engine);
  const spec = EXAMPLE();
  const result = appearance.addWallpaper('night-reef', spec);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, errors: [] });
  assert.ok(SCENES['night-reef'] && SCENES['night-reef'].spec, 'scene registered');
  const listed = appearance.wallpapers.find((w) => w.id === 'night-reef');
  assert.equal(listed.label, 'Night Reef');
  assert.equal(listed.scheme, 'dark');
  assert.ok(events.includes('fuse-wallpapers'));
  appearance.setBackdrop('night-reef');
  assert.equal(html.dataset.chatBackdrop, 'night-reef');
  // Re-registering an added key replaces it.
  assert.equal(appearance.addWallpaper('night-reef', { ...spec, name: 'Night Reef 2' }).ok, true);
  assert.equal(appearance.wallpapers.find((w) => w.id === 'night-reef').label, 'Night Reef 2');
});

test('addWallpaper refuses bad specs, bad keys and built-in keys', async () => {
  const { engine, SCENES } = await loadEngine();
  const { appearance } = bootAppearance(engine);
  const before = Object.keys(SCENES).length;
  const bad = [
    ['bad-spec', { ...EXAMPLE(), version: 2 }],
    ['bad-color', { ...EXAMPLE(), background: { type: 'solid', stops: ['url(x)'] } }],
    ['not-object', 'hello'],
    ['Bad Key', EXAMPLE()],
    ['none', EXAMPLE()],
    ['honulagoon', EXAMPLE()],
    ['glyphs', EXAMPLE()],
    ['x'.repeat(40), EXAMPLE()],
  ];
  for (const [key, spec] of bad) {
    const result = appearance.addWallpaper(key, spec);
    assert.equal(result.ok, false, key);
    assert.ok(result.errors.length > 0, key);
  }
  assert.equal(Object.keys(SCENES).length, before);
  assert.ok(SCENES.honulagoon.spec.name === 'Honu Lagoon', 'built-in untouched');
  assert.ok(!appearance.wallpapers.some((w) => w.id === 'bad-spec'));
  // A throwing interpreter is caught and leaves nothing behind.
  const throwing = { ...engine, register: () => { throw new Error('boom'); } };
  const other = bootAppearance(throwing).appearance.addWallpaper('boom', EXAMPLE());
  assert.equal(other.ok, false);
  assert.match(other.errors[0], /boom/);
  // Without the engine nothing registers.
  assert.equal(bootAppearance(undefined).appearance.addWallpaper('later', EXAMPLE()).ok, false);
});

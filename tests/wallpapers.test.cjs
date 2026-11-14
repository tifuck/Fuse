const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');

// The interpreter only needs canvases: a recording 2D context is enough.
function fakeContext(log) {
  const props = {};
  return new Proxy(props, {
    get(target, key) {
      if (key in target) return target[key];
      if (key === 'createLinearGradient' || key === 'createRadialGradient') return () => ({ addColorStop() {} });
      return (...args) => {
        if (log && key === 'drawImage') log.push(args.slice(1).map(v => Math.round(v * 100) / 100).join(','));
      };
    },
    set(target, key, value) {
      target[key] = value;
      return true;
    },
  });
}
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => fakeContext(null) }) };

const spec = () => import('../static/backdrops/wallpaper-spec.js');
const interp = () => import('../static/backdrops/wallpaper.js');
const presets = () => import('../static/backdrops/wallpapers.js');
const KEYS = ['spark', 'clouddrift', 'honulagoon', 'koipond', 'jellyfish', 'paperboats', 'fireflies', 'moonlit'];
const clone = (v) => JSON.parse(JSON.stringify(v));

function stage(w, h, { dpr = 1, k = 1 } = {}) {
  const log = [];
  return { s: { ctx: fakeContext(log), w, h, dpr, k }, log };
}

test('every preset passes the validator and stays inside the frame budget', async () => {
  const { validateWallpaper, cellBound, LIMITS } = await spec();
  const { WALLPAPERS } = await presets();
  assert.deepEqual(Object.keys(WALLPAPERS), KEYS);
  for (const [key, wp] of Object.entries(WALLPAPERS)) {
    assert.deepEqual(validateWallpaper(wp), { ok: true, errors: [] }, key);
    assert.ok(cellBound(wp) <= LIMITS.cells, key);
    assert.deepEqual(JSON.parse(JSON.stringify(wp)), wp, `${key} is plain JSON`);
    assert.ok(['light', 'dark'].includes(wp.scheme), key);
  }
  assert.equal(WALLPAPERS.clouddrift.scheme, 'light');
  assert.equal(WALLPAPERS.honulagoon.scheme, 'dark');
  assert.equal(WALLPAPERS.moonlit.scheme, 'dark');
});

test('the validator rejects unsafe or malformed specs', async () => {
  const { validateWallpaper, LIMITS } = await spec();
  const { HONULAGOON } = await presets();
  const bad = (mutate) => {
    const s = clone(HONULAGOON);
    mutate(s);
    return validateWallpaper(s);
  };
  const rejects = (label, result, pattern) => {
    assert.equal(result.ok, false, label);
    assert.ok(result.errors.length > 0, label);
    if (pattern) assert.ok(result.errors.some(e => pattern.test(e)), `${label}: ${result.errors.join(' | ')}`);
  };

  rejects('oversized', bad(s => { s.name = 'x'.repeat(LIMITS.bytes); }), /bytes of JSON/);
  rejects('unknown motion', bad(s => { s.layers[2].motions = [{ type: 'teleport' }]; }), /unknown motion "teleport"/);
  rejects('too many entities', bad(s => { for (let i = 0; i < 4; i += 1) s.layers.push({ sprite: 'fish', count: [50, 50] }); }), /place up to \d+ sprites/);
  rejects('too many tiles a frame', bad(s => { s.layers = [{ sprite: 'turtleDown', count: [40, 40] }]; }), /tiles a frame/);
  rejects('too many particles', bad(s => { s.layers.push({ type: 'particles', sprite: 'bub1', mode: 'stream', rate: 30, life: [20, 30] }); }), /particles alive/);
  rejects('named colour', bad(s => { s.background.stops[0] = 'red'; }), /hex colour/);
  rejects('css function colour', bad(s => { s.sprites.fish.legend.q[1] = 'url(https://x/y.png)'; }), /tile must be a hex colour/);
  rejects('extra top-level key', bad(s => { s.onload = 'alert(1)'; }), /onload: unknown key/);
  rejects('extra motion key', bad(s => { s.layers[2].motions[0].code = 'x'; }), /motions\[0\]\.code: unknown key/);
  rejects('extra sprite key', bad(s => { s.sprites.fish.draw = 'x'; }), /sprites\.fish\.draw: unknown key/);
  rejects('deep nesting', bad(s => {
    let o = {};
    s.ambient.colors = o;
    for (let i = 0; i < 200; i += 1) { o.a = {}; o = o.a; }
  }), /nested deeper/);
  rejects('function value', validateWallpaper({ ...clone(HONULAGOON), setup() {} }));
  const cyclic = clone(HONULAGOON);
  cyclic.layers[0].self = cyclic.layers[0];
  rejects('cycle', validateWallpaper(cyclic));
  rejects('sprite too wide', bad(s => { s.sprites.fish.rows = ['q'.repeat(LIMITS.spriteW + 1)]; }), /wider than/);
  rejects('sprite too tall', bad(s => { s.sprites.fish.rows = Array(LIMITS.spriteH + 1).fill('q'); }), /1 to \d+ strings/);
  rejects('unknown sprite', bad(s => { s.layers[0].sprite = 'shark'; }), /unknown sprite "shark"/);
  rejects('glyph outside alphabet', bad(s => { s.sprites.fish.legend.q[0] = '#'; }), /not in the spec's glyphs/);
  rejects('control glyph', bad(s => { s.glyphs += '\u0007'; }), /not allowed/);
  rejects('range out of bounds', bad(s => { s.layers[2].motions[0].speed = [1, 500]; }), /within 0\.\.20/);
  rejects('reversed range', bad(s => { s.layers[2].count = [5, 1]; }), /min is greater than max/);
  rejects('bad region', bad(s => { s.layers[2].region = [0.5, 0, 0.2, 1]; }), /x0 < x1/);
  rejects('long name', bad(s => { s.name = 'n'.repeat(LIMITS.name + 1); }), /at most/);
  rejects('missing required', bad(s => { delete s.seed; }), /seed: is required/);
  rejects('not an object', validateWallpaper([HONULAGOON]));
  rejects('null', validateWallpaper(null));
  rejects('gradient with one stop', bad(s => { s.background.stops = ['#000000']; }), /2 to 4 colours/);
});

test('the interpreter draws every preset within its cell budget, on a desktop, a phone and a card', async () => {
  const { cellBound } = await spec();
  const { WALLPAPERS, ...scenes } = await presets();
  for (const key of KEYS) {
    const scene = scenes[key];
    const bound = cellBound(WALLPAPERS[key]);
    for (const [w, h, opts] of [[1440, 900, {}], [390, 844, { dpr: 2 }], [168, 96, { k: 0.5, dpr: 2 }]]) {
      const { s, log } = stage(w, h, opts);
      scene.setup(s);
      scene.resize(s);
      for (let i = 0; i < 24; i += 1) {
        scene.draw(s, 40 + i * 0.37, 1 / scene.fps);
        assert.ok(s.drawn <= bound, `${key} ${w}x${h} drew ${s.drawn} > ${bound}`);
      }
      assert.ok(log.length > 24, `${key} ${w}x${h} drew nothing`);
    }
    assert.ok(scene.fps >= 4 && scene.fps <= 20);
  }
});

test('a frame is a pure function of seed, viewport and time', async () => {
  const { wallpaperScene } = await interp();
  const { MOONLIT, SPARK } = await presets();
  const frames = (wp, t) => {
    const scene = wallpaperScene(wp);
    const { s, log } = stage(1440, 900);
    scene.setup(s);
    scene.resize(s);
    scene.draw(s, 30, 0.1);
    log.length = 0;
    scene.draw(s, t, 0.1);
    return log.join('|');
  };
  for (const wp of [MOONLIT, SPARK]) {
    assert.equal(frames(wp, 61.25), frames(wp, 61.25));
    assert.notEqual(frames(wp, 61.25), frames(wp, 62.25), 'it moves');
    assert.notEqual(frames(wp, 61.25), frames({ ...wp, seed: wp.seed + 1 }, 61.25), 'the seed matters');
  }
});

test('the interpreter refuses an invalid spec', async () => {
  const { wallpaperScene } = await interp();
  const { KOIPOND } = await presets();
  assert.throws(() => wallpaperScene({ ...KOIPOND, layers: [{ sprite: 'pad', motions: [{ type: 'eval' }] }] }), /invalid wallpaper/);
});

test('presets are registered as scenes and listed in Appearance', () => {
  const registry = readFileSync(resolve(__dirname, '../static/backdrops/scenes.js'), 'utf8');
  const sceneKeys = registry.match(/export const SCENES = \{([^}]+)\}/)[1].split(',').map(key => key.trim());
  const js = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');
  for (const key of KEYS) {
    assert.ok(sceneKeys.includes(key), key);
    assert.match(js, new RegExp(`\\n    ${key}: \\{ label: "[^"]+", hint: "[^"]+" \\}`), key);
  }
});

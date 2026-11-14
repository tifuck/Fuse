// Wallpaper specs: the data format behind the animated ASCII wallpapers and
// its validator. A spec is plain JSON (never code), so a model can write one
// and the browser can run it without trusting it. docs/wallpapers.md is the
// format reference; wallpaper.js interprets a valid spec as a scene.
// No DOM here: the validator also runs under node.

export const LIMITS = {
  bytes: 32768, // serialized JSON
  depth: 6, // nested objects/arrays, the spec itself is 1
  name: 40,
  glyphs: 64,
  sprites: 24,
  spriteW: 32,
  spriteH: 20,
  legend: 32,
  layers: 12,
  motions: 4,
  entities: 160, // sum of every sprite layer's count max
  particles: 240, // most particles alive at once, over all particle layers
  ambient: 600,
  cells: 6000, // most tiles drawn in one frame
  errors: 20,
};

// Beyond printable ASCII, a few symbols every monospace fallback has.
const EXTRA_GLYPHS = "·•°◦○●★✦";
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const SPRITE_NAME = /^[A-Za-z][A-Za-z0-9_-]{0,23}$/;

// Field descriptors. `range` fields are [min, max] pairs the interpreter
// draws each entity's value from; `def` is the default when omitted.
const R = (lo, hi, def, int = false) => ({ range: true, lo, hi, def, int });
const N = (lo, hi, def, int = false) => ({ lo, hi, def, int });
const ONE = (list, def) => ({ oneOf: list, def });
const BOX = { box: true, def: [0, 0, 1, 1] };

export const MOTIONS = {
  // Travel in a straight line (cells/s, angle in degrees, 0 = right, 90 = down), wrapping at the edges.
  drift: { speed: R(0, 20, [0.5, 1]), angle: R(-360, 360, [0, 0]) },
  // Sway around the anchor on one axis.
  bob: { amp: R(0, 12, [0.5, 1]), period: R(0.5, 60, [3, 6]), axis: ONE(["x", "y"], "y") },
  // Swim back and forth across the region on a wavy path, facing the way it goes.
  swim: { speed: R(0, 20, [1, 2]), amp: R(0, 12, [0.5, 1.5]), period: R(0.5, 60, [4, 8]), climb: R(0, 20, [0, 0]) },
  // Rise (or fall, with a negative speed) with a side-to-side sway, wrapping top and bottom.
  rise: { speed: R(-20, 20, [0.5, 1]), sway: R(0, 12, [0.3, 1]), period: R(0.5, 60, [3, 6]) },
  // Breathe between two scales.
  pulse: { scale: R(0.5, 1.6, [0.92, 1.08]), period: R(0.5, 60, [2, 4]) },
  // Fade in and out, optionally with a random flicker.
  twinkle: { min: N(0, 1, 0.2), period: R(0.2, 60, [1, 3]), flicker: N(0, 1, 0) },
  // Circle the anchor; squash flattens the circle into an ellipse.
  orbit: { radius: R(0, 30, [1, 3]), period: R(1, 120, [6, 12]), squash: N(0.1, 1, 0.6) },
};
// Travel motions move the anchor (and wrap); the rest oscillate around it.
export const TRAVEL = new Set(["drift", "swim", "rise"]);

const SPRITE_REF = { sprite: true };
const MOTION_LIST = { motions: true };

export const SPRITE_LAYER = {
  type: ONE(["sprites"], "sprites"),
  sprite: SPRITE_REF,
  frameRate: N(0, 12, 0),
  count: R(0, 60, [1, 1], true),
  depth: N(0, 1, 0),
  region: BOX,
  alpha: R(0, 1, [1, 1]),
  snap: { bool: true, def: false },
  motions: MOTION_LIST,
};

export const PARTICLE_LAYER = {
  type: ONE(["particles"], "particles"),
  sprite: SPRITE_REF,
  mode: ONE(["burst", "stream"], "stream"),
  region: BOX,
  rate: N(0.05, 30, 2),
  period: N(0.5, 60, 4),
  burst: N(1, 60, 12, true),
  life: R(0.2, 30, [2, 4]),
  speed: R(0, 30, [0.5, 1.5]),
  angle: R(-360, 360, [0, 360]),
  gravity: N(-20, 20, 0),
  drag: N(0, 6, 0),
  sway: N(0, 12, 0),
  depth: N(0, 1, 0),
  alpha: R(0, 1, [1, 1]),
};

const GRID = { color: { hex: true }, alpha: N(0, 1, 0.25), every: N(1, 8, 2, true), edge: N(0.02, 0.5, 0.2) };
const BACKGROUND = { type: ONE(["solid", "linear", "radial"], "solid"), stops: { stops: true }, at: { at: true, def: [0.5, 0.4] }, grid: { nested: GRID } };
const AMBIENT = { glyphs: { glyphs: true }, colors: { colors: true }, count: R(0, LIMITS.ambient, [20, 60], true), alpha: R(0, 1, [0.2, 0.6]), period: R(0.5, 60, [2, 6]), region: BOX };
const TOP = {
  version: ONE([1], 1),
  name: { text: LIMITS.name },
  scheme: ONE(["light", "dark"], "dark"),
  seed: N(0, 2147483647, 1, true),
  cell: N(8, 32, 16, true),
  fps: N(4, 20, 12),
  gap: N(0, 0.3, 0),
  glyphs: { glyphs: true },
  background: { nested: BACKGROUND },
  ambient: { nested: AMBIENT },
  sprites: { sprites: true },
  layers: { layers: true },
};
const REQUIRED = { top: ["version", "scheme", "seed", "cell", "glyphs", "background", "sprites", "layers"], background: ["type", "stops"], grid: ["color"], ambient: ["glyphs", "colors"], layer: ["sprite"] };

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v) && [Object.prototype, null].includes(Object.getPrototypeOf(v));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const chars = (s) => Array.from(s);
const glyphOk = (g) => g.length === 1 && ((g >= "!" && g <= "~") || EXTRA_GLYPHS.includes(g));

// The most nested container in v, without recursion (a hostile spec can be deep).
function depthOf(root) {
  let max = 0;
  const stack = [[root, 1]];
  while (stack.length) {
    const [v, d] = stack.pop();
    if (v === null || typeof v !== "object") continue;
    if (d > max) max = d;
    if (d > LIMITS.depth) return d;
    for (const child of Object.values(v)) stack.push([child, d + 1]);
  }
  return max;
}

// Height × width of a sprite's bounding box and its drawn (non-space) cells.
export function spriteSize(sprite) {
  const rows = sprite.rows.map(chars);
  return { w: Math.max(...rows.map((r) => r.length)), h: rows.length, cells: rows.reduce((n, r) => n + r.filter((c) => c !== " ").length, 0) };
}

// The most particles a layer can have alive at once.
export function particleCap(layer) {
  const life = (layer.life || PARTICLE_LAYER.life.def)[1];
  if ((layer.mode || "stream") === "burst") return (layer.burst ?? PARTICLE_LAYER.burst.def) * (Math.ceil(life / (layer.period ?? PARTICLE_LAYER.period.def)) + 1);
  return Math.ceil((layer.rate ?? PARTICLE_LAYER.rate.def) * life) + 1;
}

function spriteNames(layer) {
  return Array.isArray(layer.sprite) ? layer.sprite : [layer.sprite];
}

// An upper bound on the tiles a valid spec draws in one frame.
export function cellBound(spec) {
  let n = spec.ambient ? (spec.ambient.count || AMBIENT.count.def)[1] : 0;
  for (const layer of spec.layers) {
    const most = Math.max(...spriteNames(layer).map((name) => spriteSize(spec.sprites[name]).cells));
    n += most * (layer.type === "particles" ? particleCap(layer) : (layer.count || SPRITE_LAYER.count.def)[1]);
  }
  return n;
}

export function validateWallpaper(spec) {
  const errors = [];
  const fail = (path, msg) => {
    if (errors.length < LIMITS.errors) errors.push(`${path || "spec"}: ${msg}`);
  };
  const done = () => ({ ok: errors.length === 0, errors });

  if (!isObj(spec)) {
    fail("", "must be a JSON object");
    return done();
  }
  if (depthOf(spec) > LIMITS.depth) {
    fail("", `nested deeper than ${LIMITS.depth} levels`);
    return done();
  }
  let json;
  try {
    json = JSON.stringify(spec);
  } catch {
    fail("", "is not JSON-serializable");
    return done();
  }
  const bytes = new TextEncoder().encode(json).length;
  if (bytes > LIMITS.bytes) {
    fail("", `is ${bytes} bytes of JSON, the limit is ${LIMITS.bytes}`);
    return done();
  }

  const ctx = { sprites: isObj(spec.sprites) ? spec.sprites : {}, alphabet: typeof spec.glyphs === "string" ? chars(spec.glyphs) : [] };

  function field(v, d, path) {
    if (d.nested) return object(v, d.nested, path, REQUIRED[path.split(".").pop()] || []);
    if (d.range) {
      if (!Array.isArray(v) || v.length !== 2 || !v.every(isNum)) return fail(path, "must be a [min, max] pair of numbers");
      if (v[0] > v[1]) return fail(path, "min is greater than max");
      if (v[0] < d.lo || v[1] > d.hi) return fail(path, `must stay within ${d.lo}..${d.hi}`);
      if (d.int && !v.every(Number.isInteger)) fail(path, "must be whole numbers");
      return;
    }
    if (d.oneOf) return d.oneOf.includes(v) ? undefined : fail(path, `must be one of ${d.oneOf.map((x) => JSON.stringify(x)).join(", ")}`);
    if (d.bool) return typeof v === "boolean" ? undefined : fail(path, "must be true or false");
    if (d.hex) return typeof v === "string" && HEX.test(v) ? undefined : fail(path, "must be a hex colour like #1a2b3c");
    if (d.text) return typeof v === "string" && v.length <= d.text && !/[\u0000-\u001f]/.test(v) ? undefined : fail(path, `must be a string of at most ${d.text} characters`);
    if (d.box) {
      if (!Array.isArray(v) || v.length !== 4 || !v.every((x) => isNum(x) && x >= 0 && x <= 1)) return fail(path, "must be [x0, y0, x1, y1] fractions of the screen (0..1)");
      if (v[0] >= v[2] || v[1] >= v[3]) fail(path, "must have x0 < x1 and y0 < y1");
      return;
    }
    if (d.at) return Array.isArray(v) && v.length === 2 && v.every((x) => isNum(x) && x >= 0 && x <= 1) ? undefined : fail(path, "must be [x, y] fractions (0..1)");
    if (d.stops) return Array.isArray(v) && v.length >= 1 && v.length <= 4 && v.every((c) => typeof c === "string" && HEX.test(c)) ? undefined : fail(path, "must be 1 to 4 hex colours");
    if (d.colors) return Array.isArray(v) && v.length >= 1 && v.length <= 4 && v.every((c) => typeof c === "string" && HEX.test(c)) ? undefined : fail(path, "must be 1 to 4 hex colours");
    if (d.glyphs) {
      if (typeof v !== "string" || !v.length) return fail(path, "must be a non-empty string of glyphs");
      const list = chars(v);
      if (list.length > LIMITS.glyphs) return fail(path, `has more than ${LIMITS.glyphs} glyphs`);
      const bad = list.find((g) => !glyphOk(g));
      if (bad !== undefined) return fail(path, `glyph ${JSON.stringify(bad)} is not allowed (printable ASCII or one of ${EXTRA_GLYPHS})`);
      if (path !== "glyphs") {
        const missing = list.find((g) => !ctx.alphabet.includes(g));
        if (missing !== undefined) fail(path, `glyph ${JSON.stringify(missing)} is not in the spec's glyphs`);
      }
      return;
    }
    if (d.sprite) {
      const names = Array.isArray(v) ? v : [v];
      if (!names.length || names.length > 8) return fail(path, "must name a sprite, or list 1 to 8 sprites");
      for (const name of names) if (typeof name !== "string" || !Object.prototype.hasOwnProperty.call(ctx.sprites, name)) fail(path, `unknown sprite ${JSON.stringify(name)}`);
      return;
    }
    if (d.motions) {
      if (!Array.isArray(v) || v.length > LIMITS.motions) return fail(path, `must be a list of at most ${LIMITS.motions} motions`);
      v.forEach((m, i) => {
        const at = `${path}[${i}]`;
        if (!isObj(m)) return fail(at, "must be an object");
        const schema = MOTIONS[m.type];
        if (!schema) return fail(`${at}.type`, `unknown motion ${JSON.stringify(m.type)} (allowed: ${Object.keys(MOTIONS).join(", ")})`);
        object(m, { type: ONE([m.type]), ...schema }, at, ["type"]);
      });
      return;
    }
    if ("lo" in d) {
      if (!isNum(v)) return fail(path, "must be a number");
      if (v < d.lo || v > d.hi) return fail(path, `must be within ${d.lo}..${d.hi}`);
      if (d.int && !Number.isInteger(v)) fail(path, "must be a whole number");
      return;
    }
    if (d.sprites) return sprites(v, path);
    if (d.layers) return layers(v, path);
  }

  function object(o, schema, path, required) {
    if (!isObj(o)) return fail(path, "must be an object");
    for (const key of Object.keys(o)) {
      if (!Object.prototype.hasOwnProperty.call(schema, key)) fail(path ? `${path}.${key}` : key, "unknown key");
    }
    for (const key of required) if (!(key in o)) fail(path ? `${path}.${key}` : key, "is required");
    for (const [key, d] of Object.entries(schema)) if (key in o) field(o[key], d, path ? `${path}.${key}` : key);
  }

  function sprites(v, path) {
    if (!isObj(v)) return fail(path, "must be an object of named sprites");
    const names = Object.keys(v);
    if (!names.length || names.length > LIMITS.sprites) return fail(path, `must hold 1 to ${LIMITS.sprites} sprites`);
    for (const name of names) {
      const at = `${path}.${name}`;
      if (!SPRITE_NAME.test(name)) fail(at, "sprite names are a letter then up to 23 letters, digits, _ or -");
      const sp = v[name];
      if (!isObj(sp)) {
        fail(at, "must be an object with rows and legend");
        continue;
      }
      object(sp, { rows: {}, legend: {} }, at, ["rows", "legend"]);
      const { rows, legend } = sp;
      if (!isObj(legend)) {
        fail(`${at}.legend`, "must be an object");
        continue;
      }
      const keys = Object.keys(legend);
      if (!keys.length || keys.length > LIMITS.legend) fail(`${at}.legend`, `must hold 1 to ${LIMITS.legend} entries`);
      for (const key of keys) {
        const lat = `${at}.legend.${key}`;
        const entry = legend[key];
        if (chars(key).length !== 1 || key === " ") fail(lat, "legend keys are single non-space characters");
        if (!Array.isArray(entry) || entry.length !== 3) {
          fail(lat, "must be [glyph, tile, ink]: a glyph (or \" \"), then a hex colour or null for each of tile and glyph");
          continue;
        }
        const [glyph, tile, ink] = entry;
        if (typeof glyph !== "string" || chars(glyph).length !== 1) fail(lat, "glyph must be one character or \" \"");
        else if (glyph !== " " && !ctx.alphabet.includes(glyph)) fail(lat, `glyph ${JSON.stringify(glyph)} is not in the spec's glyphs`);
        if (tile !== null && !(typeof tile === "string" && HEX.test(tile))) fail(lat, "tile must be a hex colour or null");
        if (ink !== null && !(typeof ink === "string" && HEX.test(ink))) fail(lat, "ink must be a hex colour or null");
        if (glyph !== " " && ink === null) fail(lat, "a glyph needs an ink colour");
        if (glyph === " " && tile === null) fail(lat, "draws nothing: give it a tile or a glyph");
      }
      if (!Array.isArray(rows) || !rows.length || rows.length > LIMITS.spriteH) {
        fail(`${at}.rows`, `must be 1 to ${LIMITS.spriteH} strings`);
        continue;
      }
      rows.forEach((row, j) => {
        if (typeof row !== "string") return fail(`${at}.rows[${j}]`, "must be a string");
        const cells = chars(row);
        if (cells.length > LIMITS.spriteW) fail(`${at}.rows[${j}]`, `is wider than ${LIMITS.spriteW}`);
        const bad = cells.find((c) => c !== " " && !Object.prototype.hasOwnProperty.call(legend, c));
        if (bad !== undefined) fail(`${at}.rows[${j}]`, `uses ${JSON.stringify(bad)}, which is not in the legend`);
      });
      if (rows.every((row) => typeof row === "string" && !row.trim())) fail(`${at}.rows`, "draws nothing");
    }
  }

  function layers(v, path) {
    if (!Array.isArray(v) || !v.length || v.length > LIMITS.layers) return fail(path, `must be a list of 1 to ${LIMITS.layers} layers`);
    v.forEach((layer, i) => {
      const at = `${path}[${i}]`;
      if (!isObj(layer)) return fail(at, "must be an object");
      const schema = layer.type === "particles" ? PARTICLE_LAYER : layer.type === undefined || layer.type === "sprites" ? SPRITE_LAYER : null;
      if (!schema) return fail(`${at}.type`, 'must be "sprites" or "particles"');
      object(layer, schema, at, REQUIRED.layer);
    });
  }

  object(spec, TOP, "", REQUIRED.top);
  if (errors.length) return done();

  const bg = spec.background;
  const want = bg.type === "solid" ? [1, 1] : [2, 4];
  if (bg.stops.length < want[0] || bg.stops.length > want[1]) fail("background.stops", bg.type === "solid" ? "a solid background has exactly 1 colour" : "a gradient has 2 to 4 colours");
  if (bg.at && bg.type !== "radial") fail("background.at", "only applies to a radial background");

  let entities = 0;
  let particles = 0;
  for (const layer of spec.layers) {
    if (layer.type === "particles") particles += particleCap(layer);
    else entities += (layer.count || SPRITE_LAYER.count.def)[1];
  }
  if (entities > LIMITS.entities) fail("layers", `place up to ${entities} sprites, the limit is ${LIMITS.entities}`);
  if (particles > LIMITS.particles) fail("layers", `keep up to ${particles} particles alive, the limit is ${LIMITS.particles}`);
  for (const name of Object.keys(spec.sprites)) {
    const { w, h } = spriteSize(spec.sprites[name]);
    if (w > LIMITS.spriteW || h > LIMITS.spriteH) fail(`sprites.${name}`, `is ${w}×${h}, the limit is ${LIMITS.spriteW}×${LIMITS.spriteH}`);
  }
  if (!errors.length) {
    const cells = cellBound(spec);
    if (cells > LIMITS.cells) fail("layers", `can draw ${cells} tiles a frame, the limit is ${LIMITS.cells}`);
  }
  return done();
}

// Wallpapers: a validated spec (wallpaper-spec.js) becomes an ordinary scene
// module, so the engine's runner, previews, hidden-tab pause and reduced
// motion apply unchanged. Every position is a pure function of the seed, the
// viewport and t: there is no per-frame state and no Math.random, so a frame
// is reproducible and a still frame is as settled as any other.
//
// Cost per frame: one full-screen blit of the pre-painted background, one
// drawImage per sprite (each sprite is pre-rendered once per size and facing)
// and one atlas stamp per ambient glyph.

import { atlas, stamp } from "./kit.js";
import { hash } from "./noise.js";
import { validateWallpaper, spriteSize, MOTIONS, TRAVEL, SPRITE_LAYER, PARTICLE_LAYER } from "./wallpaper-spec.js";

const TAU = Math.PI * 2;
// Counts are interpolated by viewport area: the min on a phone or smaller,
// the max from a 1440×900 window up.
const FULL_AREA = 1440 * 900;
const FONT = '"Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace';
// Glyphs that point one way swap when a sprite turns around.
const MIRROR = { "(": ")", ")": "(", "<": ">", ">": "<", "/": "\\", "\\": "/", "[": "]", "]": "[", "{": "}", "}": "{" };

function mulberry(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const lerp = ([a, b], u) => a + (b - a) * u;
const mod = (v, m) => ((v % m) + m) % m;
const wrap = (v, lo, hi) => lo + mod(v - lo, hi - lo);
const pingpong = (d, span) => {
  const p = mod(d, 2 * span);
  return p < span ? [p, false] : [2 * span - p, true];
};
const withDefaults = (schema, o) => Object.fromEntries(Object.entries(schema).map(([k, d]) => [k, o[k] ?? d.def]));

// One sprite at one tile size and facing, painted in device pixels so tile
// edges land on whole pixels and adjacent tiles never show a seam.
function paintSprite(sp, P, gap, flip) {
  const rows = sp.rows.map((r) => Array.from(r));
  const { w, h, cells } = spriteSize(sp);
  const canvas = document.createElement("canvas");
  canvas.width = w * P;
  canvas.height = h * P;
  const g = canvas.getContext("2d");
  g.font = `700 ${Math.round(P * 0.82)}px ${FONT}`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  const inset = Math.round(P * gap * 0.5);
  rows.forEach((row, j) => {
    row.forEach((key, i) => {
      const entry = key !== " " && sp.legend[key];
      if (!entry) return;
      const [glyph, tile, ink] = entry;
      const x = (flip ? w - 1 - i : i) * P;
      const y = j * P;
      if (tile) {
        g.fillStyle = tile;
        g.fillRect(x + inset, y + inset, P - inset * 2, P - inset * 2);
      }
      if (glyph !== " ") {
        g.fillStyle = ink;
        g.fillText(flip ? MIRROR[glyph] || glyph : glyph, x + P / 2, y + P * 0.54);
      }
    });
  });
  return { canvas, cols: w, rows: h, cells };
}

function paintBackground(s, spec) {
  const { w, h, dpr } = s;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const bg = spec.background;
  if (bg.type === "solid") g.fillStyle = bg.stops[0];
  else {
    let grad;
    if (bg.type === "linear") grad = g.createLinearGradient(0, 0, 0, h);
    else {
      const [ax, ay] = bg.at || [0.5, 0.4];
      grad = g.createRadialGradient(ax * w, ay * h, 0, ax * w, ay * h, Math.hypot(Math.max(ax, 1 - ax) * w, Math.max(ay, 1 - ay) * h));
    }
    bg.stops.forEach((c, i) => grad.addColorStop(i / (bg.stops.length - 1), c));
    g.fillStyle = grad;
  }
  g.fillRect(0, 0, w, h);
  // A faint dot at grid corners, strongest at the screen edges.
  const grid = bg.grid && withDefaults({ color: {}, alpha: { def: 0.25 }, every: { def: 2 }, edge: { def: 0.2 } }, bg.grid);
  if (grid) {
    const step = s.cell * grid.every;
    const band = grid.edge * Math.min(w, h);
    const dot = Math.max(1, Math.round(s.cell * 0.14 * dpr)) / dpr;
    g.fillStyle = grid.color;
    for (let y = step; y < h; y += step) {
      for (let x = step; x < w; x += step) {
        const d = Math.min(x, w - x, y, h - y);
        if (d >= band) continue;
        g.globalAlpha = grid.alpha * (1 - d / band);
        g.fillRect(x - dot / 2, y - dot / 2, dot, dot);
      }
    }
    g.globalAlpha = 1;
  }
  return canvas;
}

// Resolve one motion's ranges into this entity's numbers, in CSS pixels.
function motionFor(m, R, cell, para) {
  const p = withDefaults(MOTIONS[m.type], m);
  const pick = (r) => lerp(r, R());
  const ph = R() * TAU;
  switch (m.type) {
    case "drift": {
      const v = pick(p.speed) * cell * para;
      const a = (pick(p.angle) * Math.PI) / 180;
      return { type: "drift", vx: Math.cos(a) * v, vy: Math.sin(a) * v };
    }
    case "rise":
      return { type: "rise", v: pick(p.speed) * cell * para, sway: pick(p.sway) * cell, period: pick(p.period), ph };
    case "swim":
      return { type: "swim", v: pick(p.speed) * cell * para, climb: pick(p.climb) * cell * para, amp: pick(p.amp) * cell, period: pick(p.period), ph, off: R(), offY: R() };
    case "bob":
      return { type: "bob", amp: pick(p.amp) * cell, period: pick(p.period), axis: p.axis, ph };
    case "pulse":
      return { type: "pulse", lo: p.scale[0], hi: p.scale[1], period: pick(p.period), ph };
    case "twinkle":
      return { type: "twinkle", min: p.min, flicker: p.flicker, period: pick(p.period), ph, salt: Math.floor(R() * 1e6) };
    case "orbit":
      return { type: "orbit", r: pick(p.radius) * cell, squash: p.squash, period: pick(p.period), ph, dir: R() < 0.5 ? -1 : 1 };
  }
  return null;
}

function build(s, spec) {
  const k = s.k || 1;
  const cell = Math.max(3, spec.cell * k);
  s.cell = cell;
  s.cols = Math.ceil(s.w / cell);
  s.rows = Math.ceil(s.h / cell);
  s.bg = paintBackground(s, spec);
  // Previews draw at a fraction of the size; size them as the screen they stand for.
  const fill = Math.min(1, (s.w / k) * (s.h / k) / FULL_AREA);
  const count = (r) => Math.round(lerp(r, fill));
  const cache = new Map();
  const art = (name, P, flip) => {
    const key = `${name}|${P}|${flip ? 1 : 0}`;
    if (!cache.has(key)) cache.set(key, paintSprite(spec.sprites[name], P, spec.gap || 0, flip));
    return cache.get(key);
  };
  s.art = art;
  s.spriteCache = cache;
  const R = mulberry(spec.seed);
  s.layers = spec.layers.map((raw, li) => {
    const particles = raw.type === "particles";
    const L = withDefaults(particles ? PARTICLE_LAYER : SPRITE_LAYER, raw);
    const names = Array.isArray(L.sprite) ? L.sprite : [L.sprite];
    const near = 1 - L.depth;
    // Depth: far layers are smaller, slower and fainter.
    const P = Math.max(2, Math.round(cell * (1 - 0.45 * L.depth) * s.dpr));
    const px = P / s.dpr;
    const para = 0.3 + 0.7 * near;
    const fade = 1 - 0.6 * L.depth;
    const [x0, y0, x1, y1] = L.region;
    const box = { x: x0 * s.w, y: y0 * s.h, w: (x1 - x0) * s.w, h: (y1 - y0) * s.h };
    const base = { li, particles, L, names, P, px, para, fade, box };
    if (particles) {
      const rate = L.mode === "stream" ? L.rate * Math.max(0.25, fill) : 0;
      return { ...base, rate };
    }
    const n = count(L.count);
    const order = [...(L.motions || [])].sort((a, b) => TRAVEL.has(b.type) - TRAVEL.has(a.type));
    const entities = [];
    for (let i = 0; i < n; i += 1) {
      const pickName = names[Math.floor(R() * names.length)];
      const size = spriteSize(spec.sprites[pickName]);
      // Stratified across the region so a layer never clumps on one side.
      const e = {
        x: box.x + ((i + R()) / n) * box.w - (size.w * px) / 2,
        y: box.y + R() * Math.max(0, box.h - size.h * px),
        w: size.w * px,
        h: size.h * px,
        name: pickName,
        box,
        frame: R() * 100,
        alpha: lerp(L.alpha, R()) * fade,
        motions: order.map((m) => motionFor(m, R, px, para)),
      };
      e.wrapX = e.motions.some((m) => m.type === "drift" && Math.abs(m.vx) > 1e-6);
      e.wrapY = e.motions.some((m) => (m.type === "drift" && Math.abs(m.vy) > 1e-6) || m.type === "rise");
      entities.push(e);
    }
    return { ...base, entities };
  });
  if (spec.ambient) {
    const A = withDefaults({ glyphs: {}, colors: {}, count: { def: [20, 60] }, alpha: { def: [0.2, 0.6] }, period: { def: [2, 6] }, region: { def: [0, 0, 1, 1] } }, spec.ambient);
    const glyphs = Array.from(A.glyphs);
    const [x0, y0, x1, y1] = A.region;
    const P = Math.max(2, Math.round(cell * s.dpr)) / s.dpr;
    s.ambient = {
      A: atlas(s, glyphs.join(""), A.colors, P, P, Math.round(P * 0.8)),
      cell: P,
      alpha: A.alpha,
      glyphs: glyphs.length,
      dots: Array.from({ length: count(A.count) }, () => ({
        x: Math.floor((x0 + R() * (x1 - x0)) * s.cols) * cell,
        y: Math.floor((y0 + R() * (y1 - y0)) * s.rows) * cell,
        g: Math.floor(R() * glyphs.length),
        c: Math.floor(R() * A.colors.length),
        period: lerp(A.period, R()),
        ph: R() * TAU,
      })),
    };
  } else s.ambient = null;
}

// Where an entity is at t: travel moves and wraps the anchor, then the
// oscillations play around it.
function place(s, e, t) {
  let x = e.x;
  let y = e.y;
  let scale = 1;
  let alpha = e.alpha;
  let flip = false;
  let wrapped = false;
  for (const m of e.motions) {
    if (!wrapped && !TRAVEL.has(m.type)) {
      wrapped = true;
      if (e.wrapX) x = wrap(x, -e.w, s.w);
      if (e.wrapY) y = wrap(y, -e.h, s.h);
    }
    switch (m.type) {
      case "drift":
        x += m.vx * t;
        y += m.vy * t;
        if (m.vx < 0) flip = true;
        break;
      case "rise":
        y -= m.v * t;
        x += m.sway * Math.sin((TAU * t) / m.period + m.ph);
        break;
      case "swim": {
        const box = e.box;
        const span = Math.max(1, box.w - e.w);
        const [p, back] = pingpong(m.off * 2 * span + m.v * t, span);
        x = box.x + p;
        flip = back;
        if (m.climb) {
          const spanY = Math.max(1, box.h - e.h);
          y = box.y + pingpong(m.offY * 2 * spanY + m.climb * t, spanY)[0];
        }
        y += m.amp * Math.sin((TAU * t) / m.period + m.ph);
        break;
      }
      case "bob": {
        const d = m.amp * Math.sin((TAU * t) / m.period + m.ph);
        if (m.axis === "x") x += d;
        else y += d;
        break;
      }
      case "pulse":
        scale *= m.lo + (m.hi - m.lo) * (0.5 + 0.5 * Math.sin((TAU * t) / m.period + m.ph));
        break;
      case "twinkle": {
        let u = 0.5 + 0.5 * Math.sin((TAU * t) / m.period + m.ph);
        if (m.flicker) u = u * (1 - m.flicker) + m.flicker * hash(m.salt, Math.floor(t * 8));
        alpha *= m.min + (1 - m.min) * u;
        break;
      }
      case "orbit": {
        const a = m.dir * ((TAU * t) / m.period) + m.ph;
        x += m.r * Math.cos(a);
        y += m.r * m.squash * Math.sin(a);
        break;
      }
    }
  }
  if (!wrapped) {
    if (e.wrapX) x = wrap(x, -e.w, s.w);
    if (e.wrapY) y = wrap(y, -e.h, s.h);
  }
  return { x, y, scale, alpha, flip };
}

function drawArt(s, a, x, y, scale, alpha) {
  const w = a.canvas.width / s.dpr;
  const h = a.canvas.height / s.dpr;
  const sw = w * scale;
  const sh = h * scale;
  const dx = x + (w - sw) / 2;
  const dy = y + (h - sh) / 2;
  if (alpha <= 0.01 || dx > s.w || dy > s.h || dx + sw < 0 || dy + sh < 0) return 0;
  s.ctx.globalAlpha = Math.min(1, alpha);
  const snap = (v) => Math.round(v * s.dpr) / s.dpr;
  s.ctx.drawImage(a.canvas, snap(dx), snap(dy), scale === 1 ? w : sw, scale === 1 ? h : sh);
  return a.cells;
}

function drawParticles(s, layer, t) {
  const { L, names, P, px, para, fade, box, li } = layer;
  let drawn = 0;
  const lifeMax = L.life[1];
  const one = (key, salt, t0, ox, oy) => {
    const age = t - t0;
    const life = lerp(L.life, hash(key, salt, 3));
    if (age < 0 || age > life) return;
    const ang = (lerp(L.angle, hash(key, salt, 4)) * Math.PI) / 180;
    const v = lerp(L.speed, hash(key, salt, 5)) * px * para;
    // With drag a particle shoots out and coasts to a stop: a bloom.
    const go = L.drag ? (1 - Math.exp(-L.drag * age)) / L.drag : age;
    let x = ox + Math.cos(ang) * v * go;
    let y = oy + Math.sin(ang) * v * go + 0.5 * L.gravity * px * age * age;
    if (L.sway) x += L.sway * px * Math.sin(age * 1.7 + hash(key, salt, 6) * TAU);
    const u = age / life;
    // A stream eases in; a burst pops at full strength.
    const rise = L.mode === "stream" ? Math.min(1, age / 0.35) : 1;
    const alpha = lerp(L.alpha, hash(key, salt, 7)) * fade * rise * Math.pow(1 - u, 1.1);
    const name = names[Math.min(names.length - 1, Math.floor(u * names.length))];
    const a = s.art(name, P, false);
    drawn += drawArt(s, a, x - a.canvas.width / s.dpr / 2, y - a.canvas.height / s.dpr / 2, 1, alpha);
  };
  if (L.mode === "burst") {
    const now = Math.floor(t / L.period);
    for (let b = now - Math.ceil(lifeMax / L.period); b <= now; b += 1) {
      const key = b * 16 + li;
      // The origin ignores the layer, so burst layers with the same region and
      // period go off together (a flash and its sparks).
      const ox = box.x + hash(b, 1, 1) * box.w;
      const oy = box.y + hash(b, 2, 1) * box.h;
      for (let i = 0; i < L.burst; i += 1) one(key, i + 10, b * L.period, ox, oy);
    }
  } else if (layer.rate > 0) {
    const r = layer.rate;
    for (let j = Math.floor((t - lifeMax) * r) + 1; j <= Math.floor(t * r); j += 1) {
      const key = j * 16 + li;
      one(key, 1, j / r, box.x + hash(key, 8, 1) * box.w, box.y + hash(key, 9, 1) * box.h);
    }
  }
  return drawn;
}

export function wallpaperScene(spec) {
  const check = validateWallpaper(spec);
  if (!check.ok) throw new Error(`invalid wallpaper: ${check.errors.join("; ")}`);
  spec = JSON.parse(JSON.stringify(spec));
  return {
    fps: spec.fps || 12,
    scheme: spec.scheme,
    spec,
    setup() {},
    resize(s) {
      build(s, spec);
      // Sprites painted before the web font arrived carry the fallback face.
      const fonts = typeof document !== "undefined" && document.fonts;
      if (fonts && fonts.status !== "loaded") {
        const built = s.layers;
        fonts.ready.then(() => {
          if (s.layers === built) s.spriteCache.clear();
        });
      }
    },
    draw(s, t) {
      const { ctx } = s;
      ctx.globalAlpha = 1;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(s.bg, 0, 0, s.w, s.h);
      let drawn = 0;
      if (s.ambient) {
        const { A, dots, alpha, glyphs } = s.ambient;
        for (const d of dots) {
          const wave = 0.5 + 0.5 * Math.sin((TAU * t) / d.period + d.ph);
          const a = lerp(alpha, wave);
          if (a < 0.02) continue;
          ctx.globalAlpha = a;
          stamp(ctx, A, (d.g + Math.floor(t / d.period + d.ph)) % glyphs, d.c, d.x, d.y);
          drawn += 1;
        }
      }
      for (const layer of s.layers) {
        if (layer.particles) {
          drawn += drawParticles(s, layer, t);
          continue;
        }
        const { L, names, P } = layer;
        for (const e of layer.entities) {
          const at = place(s, e, t);
          let x = at.x;
          let y = at.y;
          if (L.snap) {
            x = Math.round(x / s.cell) * s.cell;
            y = Math.round(y / s.cell) * s.cell;
          }
          const name = L.frameRate > 0 ? names[Math.floor(t * L.frameRate + e.frame) % names.length] : e.name;
          drawn += drawArt(s, s.art(name, P, at.flip), x, y, at.scale, at.alpha);
        }
      }
      ctx.globalAlpha = 1;
      s.drawn = drawn;
    },
  };
}

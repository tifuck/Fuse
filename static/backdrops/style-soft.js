// Soft: painted light. Smooth gradients, blurred forms, glows and haze, no
// hard edges. Moving light is painted into small soft() buffers and
// stretched over a baked ground; glows are pre-rendered sprites.

import { seeded, noise } from "./noise.js";
import { TAU, clamp01, layer, soft, rgba } from "./art.js";

const wrap = (v, m) => ((v % m) + m) % m;
const ease = (v) => v * v * (3 - 2 * v);
// Fade toward the quiet centre: 1 at the sides, `floor` in the middle.
const sides = (x, floor) => floor + (1 - floor) * (1 - Math.exp(-(((x - 0.5) / 0.22) ** 2)));
const unitOf = (s) => Math.min(s.w, s.h) / 900 / Math.sqrt(s.k || 1);

// A soft buffer whose context draws in CSS px of the scene.
function buffer(s, scale = Math.max(2, Math.min(8, Math.round(Math.min(s.w, s.h) / 100)))) {
  const [c, g] = soft(s, scale);
  g.setTransform(c.width / s.w, 0, 0, c.height / s.h, 0, 0);
  return { c, g };
}

// Faint static grain, baked into grounds so stretched gradients do not band.
let grainTile = null;
function grain(g, s, amount) {
  if (!grainTile) {
    grainTile = document.createElement("canvas");
    grainTile.width = grainTile.height = 128;
    const x = grainTile.getContext("2d");
    const img = x.createImageData(128, 128);
    const r = seeded(9001);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = r() < 0.5 ? 0 : 255;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = Math.round(r() * 255);
    }
    x.putImageData(img, 0, 0);
  }
  const p = g.createPattern(grainTile, "repeat");
  if (p.setTransform && typeof DOMMatrix === "function") p.setTransform(new DOMMatrix().scale(1 / s.dpr));
  g.save();
  g.globalAlpha = amount;
  g.fillStyle = p;
  g.fillRect(0, 0, s.w, s.h);
  g.restore();
}

function ground(s, stops, paint, amount = 0.035) {
  const [c, g] = layer(s);
  const lg = g.createLinearGradient(0, 0, 0, s.h);
  for (const [o, col] of stops) lg.addColorStop(o, col);
  g.fillStyle = lg;
  g.fillRect(0, 0, s.w, s.h);
  paint?.(g);
  grain(g, s, amount);
  return c;
}

// Pre-rendered radial glows, cached by colour and profile.
const GAUSS = [[0, 1], [0.2, 0.8], [0.42, 0.42], [0.65, 0.14], [0.85, 0.03], [1, 0]];
const sprites = new Map();
function glow(hex, stops = GAUSS, size = 64, key = "") {
  const id = hex + key + size;
  if (sprites.has(id)) return sprites.get(id);
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const r = size / 2;
  const rg = g.createRadialGradient(r, r, 0, r, r, r);
  for (const [o, a] of stops) rg.addColorStop(o, rgba(hex, a));
  g.fillStyle = rg;
  g.fillRect(0, 0, size, size);
  sprites.set(id, c);
  return c;
}

// A wide hazy blur of something drawn small: paint at 1/scale, stretch up.
function blurred(s, scale, paint) {
  const c = document.createElement("canvas");
  c.width = Math.max(2, Math.ceil(s.w / scale));
  c.height = Math.max(2, Math.ceil(s.h / scale));
  const g = c.getContext("2d");
  g.setTransform(c.width / s.w, 0, 0, c.height / s.h, 0, 0);
  paint(g);
  return c;
}

function ridge(g, s, y, amp, f, ph, fill) {
  g.beginPath();
  g.moveTo(0, s.h);
  for (let i = 0; i <= 48; i += 1) {
    const u = i / 48;
    g.lineTo(u * s.w, s.h * (y - amp * (0.6 * Math.sin(u * TAU * f + ph) + 0.4 * Math.sin(u * TAU * f * 2.3 + ph * 1.7))));
  }
  g.lineTo(s.w, s.h);
  g.closePath();
  g.fillStyle = fill;
  g.fill();
}

// The still picture (ground plus stretched soft light) changes so slowly
// that one horizontal band of it is repainted per frame, cycling through
// the screen; small sprites on top repaint their old box and redraw. On a
// canvas without a GPU a full smoothed stretch costs several ms a frame.
function settle(s, layers) {
  s.layers = layers;
  s.bands = Math.max(1, Math.ceil((s.w * s.h * s.dpr * s.dpr) / 150000));
  s.band = 0;
  s.full = true;
  s.dirty = [];
}

// Whether the band present() paints next reaches rows y0..y1, so light that
// lives only there can skip its repaint (sprite boxes accept a stale frame).
function due(s, y0, y1) {
  if (s.full) return true;
  const a = (s.h * s.band) / s.bands;
  const b = (s.h * (s.band + 1)) / s.bands;
  return b > y0 && a < y1;
}

function base(s, x0, y0, x1, y1) {
  const { ctx, dpr } = s;
  const a = Math.max(0, Math.floor(x0 * dpr)) / dpr;
  const b = Math.max(0, Math.floor(y0 * dpr)) / dpr;
  const c = Math.min(Math.round(s.w * dpr), Math.ceil(x1 * dpr)) / dpr;
  const d = Math.min(Math.round(s.h * dpr), Math.ceil(y1 * dpr)) / dpr;
  if (c <= a || d <= b) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(a, b, c - a, d - b);
  ctx.clip();
  for (const [img, x, y, w, h] of s.layers) ctx.drawImage(img, x, y, w, h);
  ctx.restore();
}

// sprites: [img, x, y, r, alpha]; drawn additively.
function present(s, sprites) {
  const { ctx, w, h } = s;
  if (s.full) {
    s.full = false;
    base(s, 0, 0, w, h);
  } else {
    const i = s.band;
    s.band = (i + 1) % s.bands;
    base(s, 0, (h * i) / s.bands, w, (h * (i + 1)) / s.bands);
    for (const r of s.dirty) base(s, r[0], r[1], r[2], r[3]);
  }
  s.dirty = [];
  ctx.globalCompositeOperation = "lighter";
  for (const [img, x, y, r, a] of sprites) {
    if (a <= 0.003) continue;
    sprite(img, ctx, x, y, r, a);
    s.dirty.push([x - r - 1, y - r - 1, x + r + 1, y + r + 1]);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
}

function sprite(img, g, x, y, r, a) {
  if (a <= 0.003) return;
  g.globalAlpha = a;
  g.drawImage(img, x - r, y - r, r * 2, r * 2);
}

// Points that stay mostly near the sides and corners.
function edgePoints(seed, n, yMax = 1) {
  const r = seeded(seed);
  const out = [];
  while (out.length < n) {
    const x = r();
    const y = r() * yMax;
    const d = Math.max(Math.abs(x - 0.5) * 2, Math.abs(y - 0.5) * 2);
    if (r() < d ** 1.6 + 0.08) out.push({ x, y, a: r(), z: r(), ph: r() * TAU });
  }
  return out;
}

// —— Aurora ——
const AURORA = [
  { y: 0.22, hgt: 0.2, x0: -0.15, x1: 0.62, low: "#2f8f86", high: "#3c7f9a", a: 0.62, ph: 0.3 },
  { y: 0.15, hgt: 0.18, x0: 0.38, x1: 1.15, low: "#2f8f86", high: "#3c7f9a", a: 0.55, ph: 2.1 },
  { y: 0.3, hgt: 0.13, x0: 0.05, x1: 0.95, low: "#3c7f9a", high: "#2f6f8a", a: 0.2, ph: 4.4 },
];
const curtainCols = new Map();
function curtainCol(low, high) {
  const id = low + high;
  if (curtainCols.has(id)) return curtainCols.get(id);
  const c = document.createElement("canvas");
  c.width = 2;
  c.height = 128;
  const g = c.getContext("2d");
  const lg = g.createLinearGradient(0, 0, 0, 128);
  lg.addColorStop(0, rgba(high, 0));
  lg.addColorStop(0.35, rgba(high, 0.18));
  lg.addColorStop(0.72, rgba(low, 0.55));
  lg.addColorStop(0.9, rgba(low, 1));
  lg.addColorStop(1, rgba(low, 0));
  g.fillStyle = lg;
  g.fillRect(0, 0, 2, 128);
  curtainCols.set(id, c);
  return c;
}

export const aurora = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const hills = blurred(s, Math.max(3, Math.min(22, Math.min(w, h) / 40)), (g) => {
      const mist = g.createLinearGradient(0, h * 0.7, 0, h);
      mist.addColorStop(0, rgba("#16303a", 0));
      mist.addColorStop(0.55, rgba("#16303a", 0.35));
      mist.addColorStop(1, rgba("#16303a", 0.1));
      g.fillStyle = mist;
      g.fillRect(0, h * 0.7, w, h * 0.3);
      ridge(g, s, 0.86, 0.035, 1.3, 0.8, rgba("#0a1820", 0.85));
      ridge(g, s, 0.93, 0.03, 0.9, 2.6, rgba("#05090e", 0.95));
    });
    s.ground = ground(s, [[0, "#070b14"], [0.55, "#0a1520"], [1, "#0c1a24"]], (g) => {
      const r = seeded(71);
      const n = Math.round((w * h) / 6500);
      g.fillStyle = "#cfdde6";
      for (let i = 0; i < n; i += 1) {
        const x = r();
        const y = r() ** 1.4 * 0.62;
        const a = (0.06 + r() ** 2.2 * 0.32) * sides(x, 0.35);
        g.globalAlpha = a;
        g.beginPath();
        g.arc(x * w, y * h, 0.45 + r() * 0.55, 0, TAU);
        g.fill();
      }
      g.globalAlpha = 1;
      g.drawImage(hills, 0, 0, w, h);
    });
    s.buf = buffer(s);
    s.cols = Math.min(96, s.buf.c.width);
    s.twinkle = edgePoints(72, 7, 0.5);
    glow("#d6e6ee");
    for (const c of AURORA) curtainCol(c.low, c.high);
    settle(s, [[s.ground, 0, 0, w, h], [s.buf.c, 0, 0, w, h]]);
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const { g } = s.buf;
    const dx = w / s.cols;
    if (due(s, 0, h * 0.5)) {
      g.clearRect(0, 0, w, h);
      g.globalCompositeOperation = "lighter";
      AURORA.forEach((c, k) => {
        const col = curtainCol(c.low, c.high);
        const breathe = 0.78 + 0.22 * Math.sin(t * 0.11 + c.ph);
        for (let i = -1; i <= s.cols; i += 1) {
          const x = (i + 0.5) * dx;
          const u = (x / w - c.x0) / (c.x1 - c.x0);
          if (u <= 0 || u >= 1) continue;
          const env = Math.sin(u * Math.PI) ** 1.4 * sides(x / w, 0.45);
          const n = noise(u * 4.5 + t * 0.025, k * 7.3 + t * 0.017);
          const fine = noise(u * 26 - t * 0.04, k * 3.1 + t * 0.03);
          const a = c.a * env * breathe * (0.3 + 0.7 * n * n) * (0.7 + 0.3 * fine);
          if (a < 0.004) continue;
          const base = h * (c.y + 0.035 * Math.sin(u * TAU * 1.1 + t * 0.045 + c.ph) + 0.014 * Math.sin(u * TAU * 2.9 - t * 0.07 + c.ph * 2));
          const tall = h * c.hgt * (0.75 + 0.5 * n);
          g.globalAlpha = a;
          g.drawImage(col, x - dx, base - tall, dx * 2, tall * 1.08);
        }
      });
      g.globalAlpha = 1;
      g.globalCompositeOperation = "source-over";
    }
    const star = glow("#d6e6ee");
    const r = 3.2 * Math.max(0.6, unitOf(s));
    present(s, s.twinkle.map((p) => [star, p.x * w, p.y * h, r, 0.1 + 0.22 * (0.5 + 0.5 * Math.sin(t * (0.35 + p.a * 0.3) + p.ph)) ** 2]));
  },
};

// —— Nebula ——
const CLOUD_COLORS = ["#3a2448", "#24305a", "#2f2652", "#43284e"];
let cloudSprites = null;
function clouds() {
  if (cloudSprites) return cloudSprites;
  cloudSprites = [0, 1, 2, 3].map((n) => {
    const size = 160;
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const g = c.getContext("2d");
    const r = seeded(300 + n);
    for (let i = 0; i < 14; i += 1) {
      const ang = r() * TAU;
      const d = r() ** 0.8 * size * 0.24;
      const br = size * (0.1 + r() * 0.16);
      const x = size / 2 + Math.cos(ang) * d * 1.25;
      const y = size / 2 + Math.sin(ang) * d * 0.8;
      const col = CLOUD_COLORS[(n + (r() < 0.6 ? 0 : 1 + Math.floor(r() * 3))) % 4];
      const a = 0.4 + r() * 0.4;
      const rg = g.createRadialGradient(x, y, 0, x, y, br);
      for (const [o, v] of GAUSS) rg.addColorStop(o, rgba(col, v * a));
      g.fillStyle = rg;
      g.fillRect(x - br, y - br, br * 2, br * 2);
    }
    return c;
  });
  return cloudSprites;
}
const NEBULA = [
  { x: 0.1, y: 0.16, r: 0.62, n: 0, a: 1, ph: 0.2, spin: 1 },
  { x: 0.9, y: 0.84, r: 0.66, n: 1, a: 1, ph: 1.9, spin: -1 },
  { x: 0.92, y: 0.1, r: 0.42, n: 2, a: 0.8, ph: 3.3, spin: 1 },
  { x: 0.08, y: 0.92, r: 0.48, n: 3, a: 0.85, ph: 4.1, spin: -1 },
  { x: 0.55, y: 1.08, r: 0.5, n: 0, a: 0.55, ph: 5.6, spin: 1 },
];

export const nebula = {
  fps: 15,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    s.ground = ground(s, [[0, "#0b0914"], [0.6, "#0c0a17"], [1, "#0a0812"]], (g) => {
      const n = Math.round((w * h) / 5200);
      g.fillStyle = "#e4dcef";
      for (const p of edgePoints(81, n)) {
        g.globalAlpha = 0.05 + p.a ** 2.4 * 0.34;
        g.beginPath();
        g.arc(p.x * w, p.y * h, 0.4 + p.z * 0.6, 0, TAU);
        g.fill();
      }
    });
    s.buf = buffer(s);
    s.twinkle = edgePoints(82, 8);
    clouds();
    glow("#ece4f4");
    settle(s, [[s.ground, 0, 0, w, h], [s.buf.c, 0, 0, w, h]]);
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const { g } = s.buf;
    const m = Math.max(w, h);
    const imgs = clouds();
    g.clearRect(0, 0, w, h);
    for (const c of NEBULA) {
      const x = w * (c.x + 0.045 * Math.sin(t * 0.011 + c.ph));
      const y = h * (c.y + 0.035 * Math.cos(t * 0.0083 + c.ph * 1.3));
      const r = m * c.r * (1 + 0.04 * Math.sin(t * 0.02 + c.ph));
      g.save();
      g.translate(x, y);
      g.rotate(c.ph + t * 0.0055 * c.spin);
      g.globalAlpha = c.a * (0.85 + 0.15 * Math.sin(t * 0.045 + c.ph));
      g.drawImage(imgs[c.n], -r, -r, r * 2, r * 2);
      g.restore();
    }
    g.globalAlpha = 1;
    const star = glow("#ece4f4");
    const r = 3.4 * Math.max(0.6, unitOf(s));
    present(s, s.twinkle.map((p) => [star, p.x * w, p.y * h, r, 0.08 + 0.24 * (0.5 + 0.5 * Math.sin(t * (0.3 + p.a * 0.35) + p.ph)) ** 2]));
  },
};

// —— Abyss ——
let shaftSprite = null;
function shaft() {
  if (shaftSprite) return shaftSprite;
  const c = document.createElement("canvas");
  c.width = 64;
  c.height = 256;
  const g = c.getContext("2d");
  const lg = g.createLinearGradient(0, 0, 64, 0);
  for (const [o, a] of [[0, 0], [0.18, 0.12], [0.35, 0.55], [0.5, 1], [0.65, 0.55], [0.82, 0.12], [1, 0]]) lg.addColorStop(o, rgba("#6fb6b0", a));
  g.fillStyle = lg;
  g.fillRect(0, 0, 64, 256);
  g.globalCompositeOperation = "destination-in";
  const vg = g.createLinearGradient(0, 0, 0, 256);
  for (const [o, a] of [[0, 0.5], [0.06, 1], [0.3, 0.7], [0.6, 0.28], [0.85, 0.07], [1, 0]]) vg.addColorStop(o, rgba("#000000", a));
  g.fillStyle = vg;
  g.fillRect(0, 0, 64, 256);
  shaftSprite = c;
  return c;
}
let ripple = null;
function rippleSprite() {
  if (ripple) return ripple;
  const c = document.createElement("canvas");
  c.width = 64;
  c.height = 24;
  const g = c.getContext("2d");
  g.setTransform(1, 0, 0, 24 / 64, 0, 0);
  g.drawImage(glow("#8ccfc6"), 0, 0);
  ripple = c;
  return c;
}
const SHAFTS = [
  { x: 0.03, wd: 0.11, a: 0.34, ph: 0.4 },
  { x: 0.17, wd: 0.06, a: 0.26, ph: 1.7 },
  { x: 0.33, wd: 0.13, a: 0.18, ph: 2.9 },
  { x: 0.68, wd: 0.07, a: 0.2, ph: 4.2 },
  { x: 0.84, wd: 0.12, a: 0.3, ph: 5.1 },
  { x: 0.97, wd: 0.07, a: 0.24, ph: 0.9 },
];

export const abyss = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    s.ground = ground(s, [[0, "#06202a"], [0.4, "#05151c"], [0.75, "#040d12"], [1, "#03090d"]]);
    s.buf = buffer(s);
    const sc = Math.max(1.5, Math.min(4, Math.min(w, h) / 220));
    s.bandH = h * 0.16;
    const c = document.createElement("canvas");
    c.width = Math.max(2, Math.ceil(w / sc));
    c.height = Math.max(2, Math.ceil(s.bandH / sc));
    const g = c.getContext("2d");
    g.setTransform(c.width / w, 0, 0, c.height / s.bandH, 0, 0);
    s.caustic = { c, g };
    const r = seeded(91);
    s.ripples = Array.from({ length: Math.max(8, Math.min(48, Math.round(w / 36))) }, () => ({
      x: r(), y: 0.25 + r() * 0.5, sz: 0.5 + r(), v: r.sign() * (0.002 + r() * 0.004), f: 0.25 + r() * 0.3, ph: r() * TAU,
    }));
    const n = Math.round(8 + 12 * clamp01((w * h) / (1440 * 900)));
    s.motes = edgePoints(92, n).map((p) => ({ ...p, v: 0.006 + p.z * 0.008 }));
    settle(s, [[s.ground, 0, 0, w, h], [s.buf.c, 0, 0, w, h], [c, 0, 0, w, s.bandH]]);
    shaft();
    rippleSprite();
    glow("#a8d8cf");
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const u = unitOf(s);
    const { g } = s.buf;
    const img = shaft();
    g.clearRect(0, 0, w, h);
    g.globalCompositeOperation = "lighter";
    for (const sh of SHAFTS) {
      const wd = w * sh.wd * (0.85 + 0.15 * Math.sin(t * 0.06 + sh.ph)) + h * 0.02;
      const len = h * (0.85 + 0.12 * Math.sin(t * 0.04 + sh.ph * 2));
      g.save();
      g.translate(w * (sh.x + 0.012 * Math.sin(t * 0.05 + sh.ph)), -h * 0.04);
      g.rotate(-0.3 + 0.035 * Math.sin(t * 0.085 + sh.ph));
      g.globalAlpha = sh.a * (0.65 + 0.35 * Math.sin(t * 0.07 + sh.ph * 1.3));
      g.drawImage(img, -wd / 2, 0, wd, len);
      g.restore();
    }
    g.globalAlpha = 1;
    g.globalCompositeOperation = "source-over";
    const b = s.caustic;
    const bh = s.bandH;
    const rip = rippleSprite();
    if (due(s, 0, bh)) {
      b.g.clearRect(0, 0, w, bh);
      b.g.globalCompositeOperation = "lighter";
      const pad = w * 0.1;
      for (const p of s.ripples) {
        const x = wrap(p.x * (w + pad * 2) + t * p.v * w, w + pad * 2) - pad;
        const a = 0.13 * sides(x / w, 0.5) * Math.sin(Math.PI * (p.y - 0.15) / 0.7) * (0.5 + 0.5 * Math.sin(t * p.f + p.ph)) ** 2;
        if (a < 0.004) continue;
        const rx = (w * 0.05 + bh * 0.4) * p.sz;
        b.g.globalAlpha = a;
        const ry = Math.min(bh * 0.3, rx * 0.2);
        b.g.drawImage(rip, x - rx, p.y * bh - ry, rx * 2, ry * 2);
    }
    b.g.globalAlpha = 1;
    }
    const mote = glow("#a8d8cf");
    const L = h * 1.1;
    const dots = [];
    for (const p of s.motes) {
      const q = wrap(p.ph / TAU + (t * p.v * h) / L, 1);
      const y = h * 1.04 - q * L;
      const x = w * (p.x + 0.012 * Math.sin(t * 0.12 + p.ph));
      const a = 0.32 * (0.5 + 0.5 * p.a) * ease(clamp01(q / 0.12)) * (1 - q) ** 1.6 * sides(p.x, 0.4);
      dots.push([mote, x, y, (2.2 + p.z * 2.6) * Math.max(0.6, u), a]);
    }
    present(s, dots);
  },
};

// —— Aura and Bloom: drifting colour fields ——
function mesh({ seed, top, bottom, colors, alpha, fps, scheme, amount }) {
  const r = seeded(seed);
  const blobs = colors.map((col, i) => ({
    col,
    ax: 0.36 + r() * 0.16, ay: 0.34 + r() * 0.18,
    fx: (0.008 + r() * 0.008) * r.sign(), fy: (0.006 + r() * 0.008) * r.sign(),
    px: r() * TAU, py: r() * TAU,
    size: 0.42 + r() * 0.18 + (i === 0 ? 0.08 : 0),
    a: alpha * (0.8 + r() * 0.2), ph: r() * TAU,
  }));
  return {
    fps,
    wallpaper: true,
    scheme,
    previewK: 0.4,
    resize(s) {
      s.ground = ground(s, [[0, top], [1, bottom]], null, amount);
      s.buf = buffer(s);
      settle(s, [[s.ground, 0, 0, s.w, s.h], [s.buf.c, 0, 0, s.w, s.h]]);
      for (const b of blobs) glow(b.col, GAUSS, 96);
    },
    draw(s, t) {
      const { ctx, w, h } = s;
      const { g } = s.buf;
      const m = Math.max(w, h);
      g.clearRect(0, 0, w, h);
      for (const b of blobs) {
        const x = w * (0.5 + b.ax * Math.sin(t * b.fx + b.px));
        const y = h * (0.5 + b.ay * Math.sin(t * b.fy + b.py));
        const rad = m * b.size * (1 + 0.06 * Math.sin(t * 0.03 + b.ph));
        sprite(glow(b.col, GAUSS, 96), g, x, y, rad, b.a * (0.85 + 0.15 * Math.sin(t * 0.025 + b.ph)));
      }
      g.globalAlpha = 1;
      present(s, []);
    },
  };
}

export const aura = mesh({
  seed: 401, top: "#0d0f1a", bottom: "#0c0d18", fps: 15, scheme: "dark", amount: 0.035,
  colors: ["#2a2f6b", "#4a2a5c", "#1f4a5a", "#2a2f6b", "#3a2c62"], alpha: 0.75,
});

export const bloom = mesh({
  seed: 512, top: "#f4efe9", bottom: "#f1ece6", fps: 15, scheme: "light", amount: 0.025,
  colors: ["#f0d6c8", "#ddd4ee", "#d6e6dc", "#d4e2ef", "#ecd9d0"], alpha: 1,
});

// —— Lanterns ——
let lanternSprites = null;
function lanternImgs() {
  if (lanternSprites) return lanternSprites;
  const near = document.createElement("canvas");
  near.width = near.height = 64;
  const g = near.getContext("2d");
  g.drawImage(glow("#e0a060", [[0, 0.55], [0.2, 0.36], [0.45, 0.13], [0.7, 0.04], [1, 0]]), 0, 0);
  g.save();
  g.translate(32, 32);
  g.scale(0.8, 1);
  const core = g.createRadialGradient(0, 0, 0, 0, 0, 12);
  core.addColorStop(0, rgba("#f2c890", 1));
  core.addColorStop(0.45, rgba("#e8ac6c", 0.7));
  core.addColorStop(1, rgba("#e0a060", 0));
  g.fillStyle = core;
  g.fillRect(-13, -13, 26, 26);
  g.restore();
  const far = glow("#e0a060", [[0, 0.62], [0.45, 0.55], [0.7, 0.36], [0.88, 0.1], [1, 0]], 64, "disc");
  lanternSprites = { near, far };
  return lanternSprites;
}

export const lanterns = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    s.ground = ground(s, [[0, "#0d0c18"], [0.55, "#130f1e"], [1, "#1a1222"]], (g) => {
      const haze = blurred(s, Math.max(3, Math.min(22, Math.min(w, h) / 40)), (b) => {
        const hz = b.createRadialGradient(w * 0.5, h * 1.15, 0, w * 0.5, h * 1.15, Math.max(w, h) * 0.75);
        hz.addColorStop(0, rgba("#4a2c34", 0.45));
        hz.addColorStop(0.5, rgba("#3a2230", 0.18));
        hz.addColorStop(1, rgba("#3a2230", 0));
        b.fillStyle = hz;
        b.fillRect(0, 0, w, h);
        ridge(b, s, 0.95, 0.025, 1.1, 1.4, rgba("#0b0912", 0.8));
      });
      g.drawImage(haze, 0, 0, w, h);
    });
    const r = seeded(611);
    const n = Math.round(14 + 10 * clamp01((w * h) / (1440 * 900)));
    s.lanterns = Array.from({ length: n }, () => {
      const side = r() ** 2 * 0.5;
      const x = r() < 0.5 ? side : 1 - side;
      const z = r();
      return { x, z, a: 0.6 + r() * 0.4, q0: r(), ph: r() * TAU, v: 0.007 + z * 0.008, sway: 0.006 + r() * 0.01 };
    }).sort((a, b) => a.z - b.z);
    settle(s, [[s.ground, 0, 0, w, h]]);
    lanternImgs();
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const u = Math.max(0.55, unitOf(s));
    const imgs = lanternImgs();
    const L = h * 1.2;
    const glows = [];
    for (const p of s.lanterns) {
      const q = wrap(p.q0 + (t * p.v * h) / L, 1);
      const y = h * 1.08 - q * L;
      const x = w * (p.x + p.sway * Math.sin(t * 0.14 + p.ph) + 0.004 * Math.sin(t * 0.37 + p.ph * 2));
      const fade = ease(clamp01(q / 0.1)) * (1 - q) ** 1.3;
      const calm = 1 - 0.7 * (1 - sides(x / w, 0)) * clamp01((h * 0.8 - y) / (h * 0.25));
      const flick = 0.92 + 0.08 * Math.sin(t * 0.9 + p.ph);
      if (p.z < 0.45) glows.push([imgs.far, x, y, (14 + (0.45 - p.z) * 44) * u, 0.26 * p.a * fade * calm]);
      else glows.push([imgs.near, x, y, (20 + p.z * 22) * u, 0.75 * p.a * fade * calm * flick]);
    }
    present(s, glows);
  },
};

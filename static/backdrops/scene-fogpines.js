// Scene: Fog Pines. A misty valley at dusk: soft ridges fade into the haze
// one behind another, fog drifts in the folds, and pines drawn in ASCII
// tiles stand on the ridges, small and many far off, tall and few framing
// the sides. Every pine leans with the wind, row by row, tips furthest; a
// gust rolls through now and then; streaks of glyph fog drift in front, and
// a few birds cross.

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";
import { pineCells, mountains } from "./tile-land.js";

const TAU = Math.PI * 2;
const HAZE = "#9fb0aa";
// Painted ridges, far to near: crest (fraction of height), V depth, hills, colour.
const RIDGES = [
  { b: 0.46, a: 0.16, hn: 0.07, col: "#62756f" },
  { b: 0.54, a: 0.17, hn: 0.08, col: "#4c6359" },
  { b: 0.63, a: 0.17, hn: 0.09, col: "#344d40" },
  { b: 0.74, a: 0.15, hn: 0.08, col: "#22392c" },
  { b: 0.88, a: 0.12, hn: 0.07, col: "#14261b" },
];
// Tile pines per ridge: height in tiles, spacing in tiles, tile colours.
const STANDS = [
  null,
  { ridge: 1, h: [3, 4], gap: [3, 7], fill: "#5b7465", ink: "#8aa092", dim: 0.75 },
  { ridge: 2, h: [4, 6], gap: [4, 8], fill: "#3f5e4a", ink: "#7c9d86", dim: 0.45 },
  { ridge: 3, h: [6, 9], gap: [4, 7], fill: "#2a4a35", ink: "#6f9a7b", dim: 0.18, sides: 0.6 },
  { ridge: 4, h: [11, 17], gap: [6, 10], fill: "#1c3826", ink: "#5f8f6c", dim: 0, sides: 1 },
];

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 14 * k));
  const { w, h, cell } = s;
  // Sky and painted ridges, all one still image.
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, h * 0.75);
  lin.addColorStop(0, "#1a2427");
  lin.addColorStop(0.45, "#33413f");
  lin.addColorStop(1, "#6d7c76");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const glow = g.createRadialGradient(w * 0.5, h * 0.5, 0, w * 0.5, h * 0.5, Math.max(w, h) * 0.5);
  glow.addColorStop(0, "rgba(200,212,204,0.3)");
  glow.addColorStop(1, "rgba(200,212,204,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, h);
  dotGrid(g, s, "#b5c4be", 0.2);
  // Mountains far beyond the valley, their tops in cloud.
  mountains(g, s, R, { foot: h * 0.5, haze: HAZE, massifs: [{ x: 0.22, top: 0.2, wd: 0.36, rock: "#687a75" }, { x: 0.79, top: 0.15, wd: 0.42, rock: "#62746f" }], snow: ["#c9d4d0", "#9eacaa"], snowInk: "#7e8f8b" });
  g.save();
  g.filter = `blur(${Math.max(2, 22 * k)}px)`;
  g.fillStyle = "rgba(150,164,158,0.55)";
  for (let i = 0; i < 9; i += 1) {
    g.beginPath();
    g.ellipse(R() * w, h * R.range(0.14, 0.3), R.range(140, 320) * k, R.range(18, 40) * k, 0, 0, TAU);
    g.fill();
  }
  g.restore();
  s.ridges = RIDGES.map((L, li) => {
    const c0 = R.range(0.44, 0.56);
    const off = R() * 100;
    const at = (x) => {
      const u = x / w;
      return h * (L.b - L.a * Math.pow(Math.abs(u - c0) * 2, 1.25) + L.hn * (noise(u * 2.4 + off, li * 3.1) - 0.5) + 0.02 * (noise(u * 10 + off, li) - 0.5));
    };
    const p = new Path2D();
    p.moveTo(-4, h + 4);
    for (let x = -4; x <= w + 4; x += 3 * k) p.lineTo(x, at(x));
    p.lineTo(w + 4, h + 4);
    p.closePath();
    // Paler toward the foot, where the fog lies.
    const top = Math.min(...Array.from({ length: 24 }, (_, i) => at((i / 23) * w)));
    const fill = g.createLinearGradient(0, top, 0, top + h * 0.3);
    fill.addColorStop(0, L.col);
    fill.addColorStop(1, mixc(L.col, HAZE, 0.42 - li * 0.06));
    g.fillStyle = fill;
    g.fill(p);
    return { at, li };
  });
  s.back = back;
  // Fog banks: soft painted veils that drift between the ridges.
  s.fog = Array.from({ length: 12 }, (_, i) => {
    const ri = 1 + (i % 4);
    const fw = R.range(300, 700) * k;
    const fh = R.range(26, 60) * k;
    const blur = Math.max(2, 16 * k);
    const pad = blur * 3;
    const c = document.createElement("canvas");
    c.width = Math.round((fw + pad * 2) * s.dpr);
    c.height = Math.round((fh + pad * 2) * s.dpr);
    const fg = c.getContext("2d");
    fg.scale(s.dpr, s.dpr);
    fg.filter = `blur(${blur}px)`;
    fg.fillStyle = `rgba(196,210,204,${R.range(0.22, 0.4)})`;
    for (let j = 0; j < 5; j += 1) {
      fg.beginPath();
      fg.ellipse(pad + fw * (0.3 + 0.4 * R()), pad + fh * 0.5 + (R() - 0.5) * fh * 0.3, fw * R.range(0.14, 0.28), fh * R.range(0.22, 0.42), 0, 0, TAU);
      fg.fill();
    }
    return { c, w: fw + pad * 2, h: fh + pad * 2, ri, x: R() * w, dy: R.range(-0.04, 0.01) * h, v: R.range(3, 8) * k * (R() < 0.5 ? -1 : 1) };
  });
  // Tile pines on the ridges.
  s.stands = STANDS.map((S, si) => {
    if (!S) return null;
    const rg = s.ridges[S.ridge];
    const trees = [];
    let c = -Math.floor(R() * 3);
    const cols = Math.ceil(w / cell) + 2;
    while (c < cols) {
      const x = c * cell;
      const u = x / w;
      const side = Math.abs(u - 0.5) * 2;
      // The near stands keep to the sides and leave the valley open.
      const keep = S.sides ? side > 1 - S.sides * 0.75 || R() < 0.05 : R() > 0.1;
      if (keep) {
        const th = Math.round(R.range(S.h[0], S.h[1] + 0.99) * (S.sides ? 0.75 + 0.45 * side : 1));
        const shape = pineCells(th, R);
        const foot = Math.round((rg.at(x) + cell * (S.sides ? R.range(0.5, 2.5) : R.range(0.3, 1.2))) / cell);
        const tone = R.range(0.85, 1.12);
        const fill = shade(S.fill, tone);
        const ink = shade(S.ink, tone);
        const fog = (r) => S.dim * clamp01(1 - r / shape.rows + 0.3);
        for (const cl of shape.cells) {
          const f0 = cl.part === "trunk" ? mixc(fill, "#4a3a2c", 0.6) : cl.part === "lit" ? shade(fill, 1.1) : shade(fill, 0.92);
          const f = mixc(f0, HAZE, fog(cl.r) * 0.5);
          cl.id = tile(s, cl.g, f, cl.part === "trunk" ? shade(f, 0.6) : mixc(ink, HAZE, fog(cl.r) * 0.4));
        }
        trees.push({ x, top: (foot - shape.rows) * cell, cells: shape.cells, sway: R.range(0.7, 1.3) });
      }
      c += Math.round(R.range(S.gap[0], S.gap[1]) * (S.sides ? 0.6 + 0.6 * (1 - side) : 1));
    }
    return { S, si, trees };
  });
  // Glyph fog: rows of bare tildes drifting in front of the near ridges.
  const fogInk = ["#c9d6d0", "#b4c3bd", "#dbe4e0"];
  s.streakTiles = ["~", "-", "~", "~", ".", "~"].map((gl, i) => tile(s, gl, null, fogInk[i % 3]));
  s.streaks = Array.from({ length: 9 }, () => ({ ri: 2 + Math.floor(R() * 3), len: Math.round(R.range(6, 16)), x: R() * w, dy: R.range(-1.5, 2.5), v: R.range(4, 10) * k * (R() < 0.5 ? -1 : 1), seed: Math.floor(R() * 100) }));
  s.bird = [tile(s, "v", null, "#121a18"), tile(s, "-", null, "#121a18")];
  // Undergrowth in the near corners: ferns and grass in bare glyphs.
  const greens = ["#6f9a72", "#5d8a62", "#83ad7f", "#4f7a55"];
  const TUFTS = [["\\|/", " \\|/ ", "  |  "], ["v v", " \\|/", "  |"], ["'  '", "\\ /", " ||"], ["\\|//", " \\|/", "  |"]];
  s.tufts = [];
  const cols = Math.ceil(w / cell);
  for (let c = 0; c < cols; c += 1) {
    const u = (c * cell) / w;
    const side = Math.abs(u - 0.5) * 2;
    if (side < 0.55 || R() > 0.5 * side) continue;
    const art = TUFTS[Math.floor(R() * TUFTS.length)];
    const base = h - cell * R.range(0.4, 2.2) * (0.6 + side);
    const cells = [];
    art.forEach((line, r) => Array.from(line.replace(/\\\\/g, "\\")).forEach((ch, i) => {
      if (ch === " ") return;
      cells.push({ dc: i, r, lift: 1 - r / art.length, id: tile(s, ch, null, greens[Math.floor(R() * greens.length)]) });
    }));
    s.tufts.push({ x: c * cell, top: base - art.length * cell, cells, ph: R() * TAU });
  }
  s.windOff = R() * 1000;
  s.flock = R() * 1000;
}

export const fogpines = {
  fps: 12,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.38,
  setup(s) {
    s.seed = newSeed();
  },
  resize(s) {
    build(s);
  },
  draw(s, t) {
    if (s.fontStale) build(s);
    const { ctx, w, h, cell, k } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    birds(ctx, s, t);
    const gustX = ((t * 110 + s.windOff) % (w + 1600)) - 800;
    const wind = (x) => 0.55 * Math.sin(t * 0.85 + x * 0.004) + 0.25 * Math.sin(t * 2.0 + x * 0.015) + 1.3 * Math.exp(-(((x - gustX) / 260) ** 2));
    // Fog in the fold behind each stand, then the stand, then glyph fog.
    for (let si = 1; si < s.stands.length; si += 1) {
      for (const f of s.fog) {
        if (f.ri !== si) continue;
        const span = w + f.w;
        const x = ((((f.x + f.v * t) % span) + span) % span) - f.w;
        const y = s.ridges[f.ri].at(x + f.w / 2) + f.dy - f.h / 2;
        ctx.globalAlpha = 1;
        ctx.drawImage(f.c, x, y, f.w, f.h);
      }
      const st = s.stands[si];
      const amp = (0.6 + si * 0.7) * k;
      for (const tr of st.trees) {
        const bend = amp * tr.sway * wind(tr.x);
        for (const c of tr.cells) {
          const sx = Math.round((tr.x + c.dc * cell + bend * Math.pow(c.lift, 1.6)) * s.dpr) / s.dpr;
          put(ctx, s, c.id, sx, tr.top + c.r * cell);
        }
      }
      for (const sk of s.streaks) {
        if (sk.ri !== si) continue;
        const span = w + sk.len * cell;
        const x0 = ((((sk.x + sk.v * t) % span) + span) % span) - sk.len * cell;
        const y = s.ridges[Math.min(4, si)].at(x0 + (sk.len * cell) / 2) + sk.dy * cell;
        for (let i = 0; i < sk.len; i += 1) {
          const a = Math.sin((Math.PI * (i + 0.5)) / sk.len) * (0.35 + 0.25 * Math.sin(t * 0.7 + i + sk.seed));
          put(ctx, s, s.streakTiles[(i + sk.seed) % s.streakTiles.length], x0 + i * cell, y, a);
        }
      }
    }
    // The undergrowth stirs in the same wind.
    for (const tf of s.tufts) {
      const bend = 2.4 * k * (wind(tf.x) + 0.3 * Math.sin(t * 3.1 + tf.ph));
      for (const c of tf.cells) put(ctx, s, c.id, tf.x + c.dc * cell * 0.62 + bend * c.lift, tf.top + c.r * cell * 0.8, 0.9);
    }
    ctx.globalAlpha = 1;
  },
};

function birds(ctx, s, t) {
  const period = 46;
  const n = Math.floor(t / period);
  const p = (t - n * period) / 28;
  if (p > 1) return;
  const { w, h, cell } = s;
  const r = (j) => {
    const v = Math.sin(n * 12.9898 + s.flock + j * 78.233) * 43758.5453;
    return v - Math.floor(v);
  };
  const dir = r(1) < 0.5 ? 1 : -1;
  const y = h * (0.1 + r(2) * 0.14);
  const x = dir > 0 ? -60 + p * (w + 120) : w + 60 - p * (w + 120);
  const count = 3 + Math.floor(r(3) * 3);
  for (let b = 0; b < count; b += 1) {
    const bx = x - dir * b * cell * 1.4;
    const by = y + (b % 2 ? 1 : -1) * Math.ceil(b / 2) * cell * 0.8 + Math.sin(t * 1.1 + b * 2) * 2;
    put(ctx, s, s.bird[Math.sin(t * 6 + b * 1.3) > 0 ? 0 : 1], bx, by, 0.85);
  }
  ctx.globalAlpha = 1;
}

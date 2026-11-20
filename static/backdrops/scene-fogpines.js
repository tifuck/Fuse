// Scene: Fog Pines. A misty valley at dusk: soft ridges fade into the haze
// one behind another, fog drifts in the folds, and pines drawn in ASCII
// tiles stand on the ridges, small and many far off, tall and few framing
// the sides. Every pine leans with the wind, row by row, tips furthest; a
// gust rolls through now and then; streaks of glyph fog drift in front, and
// a few birds cross.

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";
import { pineCells } from "./tile-land.js";

const TAU = Math.PI * 2;
const HAZE = "#8a9e9a";
// The cold dusk light behind the far range: rim light on every crown edge
// that faces it.
const GLOW = "#c3d3d2";
// Painted ridges, far to near: crest (fraction of height), V depth, hills, colour.
// Far ridges sit pale in the haze, near ones go dark: atmospheric depth.
const RIDGES = [
  { b: 0.46, a: 0.16, hn: 0.07, col: "#5a6d6a" },
  { b: 0.54, a: 0.17, hn: 0.08, col: "#43574f" },
  { b: 0.63, a: 0.17, hn: 0.09, col: "#2c4038" },
  { b: 0.74, a: 0.15, hn: 0.08, col: "#1a2b23" },
  { b: 0.88, a: 0.12, hn: 0.07, col: "#0d1913" },
];
// Tile pines per ridge: height in tiles, spacing in tiles, tile colours,
// how far the haze swallows them, how much rim light they catch.
const STANDS = [
  null,
  { ridge: 1, h: [3, 4], gap: [3, 7], fill: "#50665d", ink: "#6d8479", dim: 0.7, rim: 0.12 },
  { ridge: 2, h: [4, 6], gap: [4, 8], fill: "#344c41", ink: "#587365", dim: 0.42, rim: 0.2 },
  { ridge: 3, h: [6, 9], gap: [5, 8], fill: "#1f3529", ink: "#45634f", dim: 0.16, rim: 0.3, sides: 0.6 },
  { ridge: 4, h: [11, 17], gap: [7, 11], fill: "#10211a", ink: "#3a5a45", dim: 0, rim: 0.36, sides: 1 },
];
// Crown texture: most needles are bare tiles; a few quiet marks only.
const NEEDLE = [" ", " ", " ", "'", " ", " ", ",", " "];

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

// The far range in tiles: clean / and \ ridgelines with ^ summits, faces
// as bare tiles with a sparse dot of snow, sinking into the haze toward
// `foot`. (tile-land's mountains() inks a mark on every tile, which reads
// as noise at this distance.)
function range(g, s, R, foot, massifs) {
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const fr = Math.round(foot / cell);
  for (const M of massifs) {
    const off = R() * 100;
    const np = 4 + Math.floor(R() * 3);
    const peaks = Array.from({ length: np }, (_, i) => ({
      c: ((M.x + (i / (np - 1) - 0.5) * M.wd * 1.3 + (R() - 0.5) * 0.05) * w) / cell,
      top: (h * (M.top + R.range(0, 0.12) + Math.abs(i / (np - 1) - 0.5) * 0.14)) / cell,
      fall: R.range(0.75, 1.3),
    }));
    const ridge = (c) => {
      let best = Infinity;
      for (const pk of peaks) best = Math.min(best, pk.top + Math.abs(c - pk.c) * pk.fall);
      return best < fr - 0.5 ? best + (noise(c * 0.5 + off, 4) - 0.5) * 0.6 : Infinity;
    };
    const top0 = (h * M.top) / cell;
    for (let c = 1; c < cols - 1; c += 1) {
      const rc = ridge(c);
      if (!Number.isFinite(rc)) continue;
      const rl = ridge(c - 1);
      const rr = ridge(c + 1);
      const top = Math.round(rc);
      const slope = (Number.isFinite(rr) ? rr : fr) - (Number.isFinite(rl) ? rl : fr);
      const lit = slope > 0;
      const snowAt = top0 + (fr - top0) * (0.28 + 0.14 * noise(c * 0.35 + off, 9));
      for (let r = top; r < fr; r += 1) {
        const snow = r < snowAt - (lit ? 0 : 0.6);
        const down = (r - top) / Math.max(1, fr - top);
        let fill = snow ? (lit ? mixc("#c4cecb", GLOW, 0.25) : "#7f908d") : shade(M.rock, lit ? 1.12 : 0.8);
        fill = mixc(fill, HAZE, 0.24 + 0.6 * down);
        let glyph = " ";
        if (r === top) glyph = rc <= rl && rc <= rr ? "^" : slope > 0 ? "\\" : "/";
        else if (snow && (c * 5 + r * 3) % 7 === 0) glyph = ".";
        const ink = r === top ? mixc(fill, "#e4ece8", 0.45) : mixc(fill, "#e4ece8", 0.18);
        put(g, s, tile(s, glyph, fill, ink), c * cell, r * cell, 1 - 0.55 * down * down);
      }
    }
  }
  g.globalAlpha = 1;
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 14 * k));
  const { w, h, cell } = s;
  // Sky and painted ridges, all one still image.
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, h * 0.75);
  lin.addColorStop(0, "#0d1517");
  lin.addColorStop(0.4, "#25332f");
  lin.addColorStop(0.75, "#566661");
  lin.addColorStop(1, "#77837a");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  // Dusk light low behind the range, cold and wide, off to one side so the
  // middle of the screen stays quiet.
  const gx = w * R.range(0.62, 0.74);
  const glow = g.createRadialGradient(gx, h * 0.4, 0, gx, h * 0.4, Math.max(w, h) * 0.55);
  glow.addColorStop(0, "rgba(196,214,212,0.42)");
  glow.addColorStop(0.4, "rgba(150,174,172,0.12)");
  glow.addColorStop(1, "rgba(150,174,172,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, h);
  s.glowX = gx;
  dotGrid(g, s, "#b5c4be", 0.14);
  // Mountains far beyond the valley, their tops in cloud.
  range(g, s, R, h * 0.5, [{ x: 0.22, top: 0.2, wd: 0.36, rock: "#5f716d" }, { x: 0.79, top: 0.15, wd: 0.42, rock: "#5a6c68" }]);
  g.save();
  g.filter = `blur(${Math.max(2, 22 * k)}px)`;
  g.fillStyle = "rgba(140,156,150,0.5)";
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
    // A thin rim of dusk light along the crest, brightest toward the glow.
    g.save();
    g.clip(p);
    const rim = g.createLinearGradient(0, 0, w, 0);
    const lx = clamp01(gx / w);
    rim.addColorStop(0, "rgba(195,211,210,0)");
    rim.addColorStop(lx, `rgba(195,211,210,${0.34 - li * 0.05})`);
    rim.addColorStop(1, "rgba(195,211,210,0.04)");
    g.strokeStyle = rim;
    g.lineWidth = Math.max(1, 3 * k);
    g.filter = `blur(${Math.max(0.5, 1.5 * k)}px)`;
    g.stroke(p);
    g.restore();
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
    fg.fillStyle = `rgba(184,198,192,${R.range(0.18, 0.34)})`;
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
        // The side facing the dusk light catches a rim; the far side stays dark.
        const toGlow = x < s.glowX ? 1 : -1;
        const rimK = S.rim * (1 - 0.6 * clamp01(Math.abs(x - s.glowX) / w));
        for (const cl of shape.cells) {
          if (cl.part !== "tip" && cl.part !== "trunk" && cl.g !== "/" && cl.g !== "\\") cl.g = NEEDLE[Math.floor(R() * NEEDLE.length)];
          const facing = cl.dc * toGlow > 0;
          const edge = cl.g === "/" || cl.g === "\\" || cl.part === "tip";
          const f0 = cl.part === "trunk" ? mixc(fill, "#3a2e24", 0.6) : facing ? mixc(fill, GLOW, rimK * (edge ? 0.55 : 0.22)) : shade(fill, 0.86);
          const f = mixc(f0, HAZE, fog(cl.r) * 0.5);
          const i0 = edge ? (facing ? mixc(ink, GLOW, rimK * 1.4) : ink) : mixc(f, ink, 0.45);
          cl.id = tile(s, cl.g, f, cl.part === "trunk" ? shade(f, 0.6) : mixc(i0, HAZE, fog(cl.r) * 0.4));
        }
        trees.push({ x, top: (foot - shape.rows) * cell, cells: shape.cells, sway: R.range(0.7, 1.3) });
      }
      c += Math.round(R.range(S.gap[0], S.gap[1]) * (S.sides ? 0.6 + 0.6 * (1 - side) : 1));
    }
    return { S, si, trees };
  });
  // Glyph fog: rows of bare tildes drifting in front of the near ridges.
  const fogInk = ["#aebdb8", "#9cada7", "#c2cec9"];
  s.streakTiles = ["~", "-", "~", "~", ".", "~"].map((gl, i) => tile(s, gl, null, fogInk[i % 3]));
  s.streaks = Array.from({ length: 6 }, () => ({ ri: 2 + Math.floor(R() * 3), len: Math.round(R.range(6, 16)), x: R() * w, dy: R.range(-1.5, 2.5), v: R.range(4, 10) * k * (R() < 0.5 ? -1 : 1), seed: Math.floor(R() * 100) }));
  s.bird = [tile(s, "v", null, "#121a18"), tile(s, "-", null, "#121a18")];
  // Undergrowth in the near corners: ferns and grass in bare glyphs.
  const greens = ["#4f7356", "#43664b", "#5f8462", "#3a5a42"];
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
          const a = Math.sin((Math.PI * (i + 0.5)) / sk.len) * (0.22 + 0.16 * Math.sin(t * 0.7 + i + sk.seed));
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

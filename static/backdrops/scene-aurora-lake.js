// Scene: Aurora Lake. Northern lights fold over a mountain lake at night:
// a soft glow with rays of glyph tiles shimmering up through it, green at
// the hem and violet at the tops. Tile mountains catch the light, pines
// line the shore and lean in the night wind, smoke curls from a cabin with
// a warm window, the lake holds it all upside down, and now and then a star
// falls.

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";
import { pineCells, mountains } from "./tile-land.js";

const TAU = Math.PI * 2;
const ACELL = 6;
const RAY = ["#59f0a8", "#4fe3c0", "#5fc8e0", "#8f9cf0", "#b98cf0", "#d28ae6"];
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const hz = (s.hz = Math.round((h * 0.62) / cell) * cell);
  const [sky, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, hz);
  lin.addColorStop(0, "#02040c");
  lin.addColorStop(0.5, "#051226");
  lin.addColorStop(0.9, "#0b2633");
  lin.addColorStop(1, "#0f2f3a");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 300 * Math.min(1.2, (w * h) / 1.3e6); i += 1) {
    g.fillStyle = `rgba(225,236,255,${R.range(0.08, 0.45)})`;
    const r = R.range(0.6, 1.3) * k;
    g.fillRect(R() * w, Math.pow(R(), 1.3) * hz * 0.9, r, r);
  }
  dotGrid(g, s, "#5d8fa0", 0.18);
  s.sky = sky;
  // Glyph stars that twinkle.
  const starGlyphs = ["+", "*", ".", "+", "'"];
  s.stars = Array.from({ length: Math.round(46 * Math.min(1.2, (w * h) / 1.3e6)) }, () => ({
    x: Math.floor((R() * w) / cell) * cell,
    y: Math.floor((Math.pow(R(), 1.4) * hz * 0.75) / cell) * cell,
    id: tile(s, starGlyphs[Math.floor(R() * starGlyphs.length)], null, R() < 0.2 ? "#ffe9c4" : "#e8f0ff"),
    p: R.range(2, 6),
    ph: R() * TAU,
  }));
  // Mountains across the far shore, snow lit green by the aurora.
  const [land, lg] = layer(s);
  mountains(lg, s, R, { foot: hz, haze: "#0c2430", hazeTop: 0.08, snowLine: 0.36, fade: 0.2, snow: ["#a6d9c8", "#5f8f96"], snowInk: "#3e6a6e", massifs: [{ x: 0.22, top: 0.33, wd: 0.4, rock: "#1e3644" }, { x: 0.74, top: 0.28, wd: 0.48, rock: "#1b3140" }] });
  s.land = land;
  // Shore pines, tall at the sides, framing the lake.
  s.trees = [];
  const cols = Math.ceil(w / cell) + 2;
  const cabinC = Math.round((w * R.range(0.28, 0.33)) / cell);
  for (let c = -1; c < cols; ) {
    const u = (c * cell) / w;
    const side = Math.abs(u - 0.5) * 2;
    const th = Math.round(2 + 13 * Math.pow(side, 1.6) * R.range(0.7, 1.15));
    if ((side > 0.12 || R() < 0.4) && Math.abs(c - cabinC) > 4) {
      const shape = pineCells(th, R);
      const fill = shade("#0d1e22", R.range(0.85, 1.2));
      // On the shore the trunks stand in the dark; only the crowns show.
      const crown = shape.cells.filter((cl) => cl.part !== "trunk");
      const rowsUp = Math.max(...crown.map((cl) => cl.r)) + 1;
      for (const cl of crown) cl.id = tile(s, cl.g, cl.part === "lit" ? shade(fill, 1.2) : fill, cl.part === "lit" ? "#3f8c76" : "#24504a");
      s.trees.push({ x: c * cell, top: hz - rowsUp * cell, cells: crown, sway: R.range(0.7, 1.3) });
    }
    c += Math.max(2, Math.round((2 + 3 * (1 - side)) * R.range(0.7, 1.3)));
  }
  // The cabin: roof, walls, a lit window, a chimney.
  const cx = cabinC;
  const cr = hz / cell;
  const wall = "#1b1d24";
  const roof = "#2a2026";
  s.cabin = [
    [0, -4, "/", roof, "#6a4a52"], [1, -4, "\\", roof, "#6a4a52"],
    [-1, -3, "/", roof, "#6a4a52"], [0, -3, "=", roof, "#6a4a52"], [1, -3, "=", roof, "#6a4a52"], [2, -3, "\\", roof, "#6a4a52"],
    [-1, -2, "|", wall, "#4a4e60"], [0, -2, "#", "#ffc978", "#8a5a18"], [1, -2, ":", wall, "#4a4e60"], [2, -2, "|", wall, "#4a4e60"],
    [-1, -1, "|", wall, "#4a4e60"], [0, -1, ":", wall, "#4a4e60"], [1, -1, "#", "#ffb862", "#8a5a18"], [2, -1, "|", wall, "#4a4e60"],
    [2, -5, "|", "#3a2c2c", "#7a5a50"],
  ].map(([dc, dr, gl, f, i]) => ({ x: (cx + dc) * cell, y: (cr + dr) * cell, id: tile(s, gl, f, i), win: gl === "#" }));
  s.smoke = { x: (cx + 2) * cell, y: (cr - 6) * cell, ids: ["o", "°", ".", "~"].map((gl) => tile(s, gl, null, "#9fb0c0")) };
  s.windowGlow = { x: (cx + 0.5) * cell, y: (cr - 1.5) * cell };
  // Aurora field: three ribbons, computed at low resolution.
  s.aw = Math.ceil(w / (ACELL * k)) + 1;
  s.ah = Math.ceil(hz / (ACELL * k)) + 1;
  s.acell = ACELL * k;
  s.aur = document.createElement("canvas");
  s.aur.width = s.aw;
  s.aur.height = s.ah;
  s.aurCtx = s.aur.getContext("2d");
  s.aurImg = s.aurCtx.createImageData(s.aw, s.ah);
  s.acc = new Float32Array(s.aw * s.ah * 4);
  s.ribbons = [
    { y: 0.44, amp: 0.08, hr: 0.4, gain: 1.15, f: 0.0042, x0: -0.1, x1: 0.8 },
    { y: 0.3, amp: 0.06, hr: 0.28, gain: 0.85, f: 0.0035, x0: 0.25, x1: 1.1 },
    { y: 0.55, amp: 0.05, hr: 0.26, gain: 0.6, f: 0.0055, x0: 0.1, x1: 0.95 },
  ].map((r) => ({ ...r, ph: R() * 100, f: r.f / k }));
  s.rayIds = RAY.map((c) => ["!", "|", ":", "'", "."].map((gl) => tile(s, gl, null, c)));
  s.top = layer(s);
  s.swellIds = ["~", "-", "~"].map((gl, i) => tile(s, gl, null, ["#3f8f8a", "#2f6f78", "#56b0a0"][i]));
  s.swells = Array.from({ length: Math.round(50 * Math.max(0.4, (w * h) / 1.3e6)) }, () => ({ x: R() * w, y: hz + cell * 2 + Math.pow(R(), 0.9) * (h - hz - cell * 2), len: Math.round(R.range(2, 5)), v: R.range(1.5, 4) * k * (R() < 0.5 ? -1 : 1), ph: R() * TAU }));
  s.meteor = R() * 1000;
  s.meteorIds = ["*", "-", "-", ".", "."].map((gl) => tile(s, gl, null, "#ffffff"));
}

// The aurora field: intensity and colour per low-res cell, in acc (r, g, b, a).
function aurora(s, t) {
  const { aw, ah, acell, acc, hz } = s;
  acc.fill(0);
  for (const [ri, r] of s.ribbons.entries()) {
    const hr = r.hr * hz;
    for (let x = 0; x < aw; x += 1) {
      const px = x * acell;
      const u = px / s.w;
      const env = clamp01((u - r.x0) / 0.15) * clamp01((r.x1 - u) / 0.15);
      if (env <= 0) continue;
      const fold = 0.35 + 0.65 * Math.pow(noise(px * 0.0022 + r.ph + t * 0.02, ri * 3.3), 1.5);
      const rays = 0.35 + 0.65 * Math.pow(noise(px * 0.05 + t * 0.25, ri * 7.1 + t * 0.06), 2);
      const base = hz * r.y + hz * r.amp * Math.sin(px * r.f + t * 0.06 + r.ph) + hz * 0.04 * (noise(px * 0.003 + t * 0.03, r.ph) - 0.5);
      const I = env * fold * r.gain;
      const y0 = Math.max(0, Math.floor((base - hr) / acell));
      const y1 = Math.min(ah - 1, Math.ceil((base + 3 * acell) / acell));
      for (let y = y0; y <= y1; y += 1) {
        const d = base - y * acell;
        let a = d < 0 ? Math.exp(d / (acell * 0.9)) : 0.75 * Math.exp(-d / (hr * 0.22)) + 0.4 * rays * Math.exp(-d / (hr * 0.6)) * (1 - d / hr);
        a *= I * (0.55 + 0.45 * rays);
        if (a <= 0.004) continue;
        const v = clamp01(d / hr);
        const o = (y * aw + x) * 4;
        acc[o] += a * (60 + 170 * v * v);
        acc[o + 1] += a * (255 - 190 * v);
        acc[o + 2] += a * (150 + 90 * v);
        acc[o + 3] = Math.max(acc[o + 3], v + 0.0001);
      }
    }
  }
  const d = s.aurImg.data;
  for (let i = 0; i < acc.length; i += 4) {
    d[i] = acc[i] * 0.8;
    d[i + 1] = acc[i + 1] * 0.8;
    d[i + 2] = acc[i + 2] * 0.8;
    d[i + 3] = 255;
  }
  s.aurCtx.putImageData(s.aurImg, 0, 0);
}

export const auroralake = {
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
    const { ctx, w, h, hz, cell, k, dpr } = s;
    const [top, g] = s.top;
    g.globalAlpha = 1;
    g.globalCompositeOperation = "source-over";
    g.drawImage(s.sky, 0, 0, w, h);
    for (const st of s.stars) put(g, s, st.id, st.x, st.y, 0.2 + 0.7 * (0.5 + 0.5 * Math.sin((TAU * t) / st.p + st.ph)));
    // The glow, then rays of glyphs drawn from the same field.
    aurora(s, t);
    g.globalAlpha = 1;
    g.globalCompositeOperation = "lighter";
    g.imageSmoothingEnabled = true;
    g.drawImage(s.aur, -s.acell / 2, -s.acell / 2, s.aw * s.acell, s.ah * s.acell);
    const { aw, acell, acc } = s;
    const rows = Math.floor(hz / cell);
    const cols = Math.ceil(w / cell);
    for (let r = 0; r < rows; r += 1) {
      const ay = Math.min(s.ah - 1, Math.round(((r + 0.5) * cell) / acell));
      for (let c = 0; c < cols; c += 1) {
        const ax = Math.min(aw - 1, Math.round(((c + 0.5) * cell) / acell));
        const o = (ay * aw + ax) * 4;
        const I = (acc[o + 1] + acc[o + 2] * 0.4) / 255;
        if (I < 0.1) continue;
        const v = acc[o + 3];
        const shimmer = noise(c * 0.9 + t * 1.6, r * 0.25 - t * 0.5);
        if (shimmer < 0.28) continue;
        const ids = s.rayIds[Math.min(RAY.length - 1, Math.floor(v * RAY.length))];
        const gl = I > 0.6 ? 0 : I > 0.42 ? 1 : I > 0.26 ? 2 : I > 0.16 ? 3 : 4;
        put(g, s, ids[gl], c * cell, r * cell, Math.min(0.95, (0.25 + I) * shimmer));
      }
    }
    g.globalCompositeOperation = "source-over";
    // A falling star every half minute or so.
    const P = 29;
    const n = Math.floor(t / P);
    const p = (t - n * P) / 1.1;
    if (p < 1) {
      const rr = (j) => {
        const v = Math.sin(n * 57.3 + s.meteor + j * 9.1) * 43758.5453;
        return v - Math.floor(v);
      };
      const dir = rr(3) < 0.5 ? -1 : 1;
      const hx = w * (0.15 + rr(1) * 0.7) + dir * p * 260 * k;
      const hy = hz * (0.05 + rr(2) * 0.2) + p * 110 * k;
      s.meteorIds.forEach((id, i) => put(g, s, id, hx - dir * i * cell * 0.9, hy - i * cell * 0.38, Math.sin(Math.PI * p) * (1 - i / 5)));
    }
    g.globalAlpha = 1;
    g.drawImage(s.land, 0, 0, w, h);
    // Cabin, smoke, window glow.
    for (const cb of s.cabin) put(g, s, cb.id, cb.x, cb.y, cb.win ? 0.85 + 0.15 * noise(t * 2.5, cb.x) : 1);
    const wg = g.createRadialGradient(s.windowGlow.x + cell / 2, s.windowGlow.y, 0, s.windowGlow.x + cell / 2, s.windowGlow.y, cell * 4);
    wg.addColorStop(0, "rgba(255,190,110,0.22)");
    wg.addColorStop(1, "rgba(255,190,110,0)");
    g.globalAlpha = 1;
    g.fillStyle = wg;
    g.fillRect(s.windowGlow.x - cell * 4, s.windowGlow.y - cell * 4, cell * 9, cell * 8);
    for (let i = 0; i < 6; i += 1) {
      const life = ((t * 0.25 + i / 6) % 1);
      put(g, s, s.smoke.ids[Math.min(3, Math.floor(life * 4))], s.smoke.x + Math.sin(life * 5 + i) * cell * 0.6 + life * cell * 1.5, s.smoke.y - life * cell * 5, 0.5 * (1 - life));
    }
    // Shore pines leaning in the wind.
    const gustX = ((t * 90 + s.meteor) % (w + 1400)) - 700;
    for (const tr of s.trees) {
      const bend = 2.4 * k * tr.sway * (0.5 * Math.sin(t * 0.8 + tr.x * 0.004) + 0.25 * Math.sin(t * 1.9 + tr.x * 0.02) + Math.exp(-(((tr.x - gustX) / 240) ** 2)));
      for (const c of tr.cells) put(g, s, c.id, Math.round((tr.x + c.dc * cell + bend * Math.pow(c.lift, 1.6)) * dpr) / dpr, tr.top + c.r * cell);
    }
    g.globalAlpha = 1;
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(top, 0, 0, hz * dpr > top.height ? top.width : top.width, hz * dpr, 0, 0, w, hz);
    // The lake: dark water holding the sky upside down.
    const water = ctx.createLinearGradient(0, hz, 0, h);
    water.addColorStop(0, "#06141c");
    water.addColorStop(1, "#02070b");
    ctx.fillStyle = water;
    ctx.fillRect(0, hz, w, h - hz);
    const band = Math.max(1, Math.round(2 * k * dpr)) / dpr;
    for (let y = 0; y < h - hz; y += band) {
      const depth = y / (h - hz);
      const a = 0.55 * Math.pow(1 - depth, 1.3);
      if (a < 0.02) break;
      const dx = (0.6 + 5 * depth) * k * (Math.sin(y * 0.1 / k + t * 1.1) + 0.5 * Math.sin(y * 0.27 / k - t * 1.6));
      ctx.globalAlpha = a;
      ctx.drawImage(top, 0, (hz - y - band) * dpr, top.width, band * dpr, dx, hz + y, w, band);
    }
    ctx.globalAlpha = 1;
    for (const sw of s.swells) {
      const span = w + sw.len * cell;
      const x = ((((sw.x + sw.v * t) % span) + span) % span) - sw.len * cell;
      const a = 0.25 + 0.35 * (0.5 + 0.5 * Math.sin(t * 0.7 + sw.ph));
      for (let i = 0; i < sw.len; i += 1) put(ctx, s, s.swellIds[(i + Math.floor(sw.ph)) % 3], x + i * cell, sw.y, a * Math.sin((Math.PI * (i + 0.5)) / sw.len));
    }
    ctx.globalAlpha = 1;
  },
};

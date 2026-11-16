// Scene: Harbor Night. A dense waterfront city under a low harvest moon, in
// ASCII tiles. Window tiles come on and go off, masts blink, vertical neon
// signs hum and now and then stutter, lamps line the promenade, a lit ferry
// crosses the bay pushing a wake of tildes, and every light lies in the
// water as a trembling column of glyphs.

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const NEON = [
  { fill: "#ff4f9e", ink: "#5a0f33", glow: "255,79,158" },
  { fill: "#3fe0ff", ink: "#0b3f4d", glow: "63,224,255" },
  { fill: "#ffd447", ink: "#5a4208", glow: "255,212,71" },
  { fill: "#9dff6e", ink: "#1f4d0b", glow: "157,255,110" },
];
const WORDS = ["HOTEL", "NOODLE", "24H", "BAR", "TEA", "RAMEN", "JAZZ", "OPEN"];
const WARM = ["#ffd58a", "#ffc46a", "#ffe2a8"];
const COOL = ["#cfe2ff", "#b5d2ff", "#e6f0ff"];

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const shore = (s.shore = Math.round((h * 0.66) / cell));
  const sy = shore * cell;
  // Sky, moon glow, hills: one still image.
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, sy);
  lin.addColorStop(0, "#050819");
  lin.addColorStop(0.55, "#121a44");
  lin.addColorStop(0.85, "#2c2a63");
  lin.addColorStop(1, "#4a3570");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const mx = Math.round((w * 0.2) / cell) * cell;
  const my = Math.round((h * 0.24) / cell) * cell;
  const mr = Math.round(Math.min(w, h) * 0.055 / cell);
  const glow = g.createRadialGradient(mx, my, 0, mx, my, mr * cell * 6);
  glow.addColorStop(0, "rgba(255,214,150,0.2)");
  glow.addColorStop(1, "rgba(255,214,150,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, sy);
  for (let i = 0; i < 160 * Math.min(1, (w * h) / 1.3e6); i += 1) {
    g.fillStyle = `rgba(220,226,255,${R.range(0.1, 0.5)})`;
    const r = R.range(0.6, 1.4) * k;
    g.fillRect(R() * w, Math.pow(R(), 1.5) * sy * 0.6, r, r);
  }
  dotGrid(g, s, "#8d97d6", 0.2);
  // The moon, in tiles: a pale gold disc with darker maria.
  for (let r = -mr; r <= mr; r += 1) {
    for (let c = -mr; c <= mr; c += 1) {
      const d = Math.hypot(c, r * 1.0) / (mr + 0.4);
      if (d > 1) continue;
      const mare = noise(c * 0.5 + 3, r * 0.5 + 7) > 0.6;
      const lit = 1 - 0.25 * Math.max(0, (c + r) / mr);
      const fill = shade(mare ? "#e2c98c" : "#f6e6b8", lit);
      put(g, s, tile(s, d > 0.85 ? ":" : mare ? "%" : ".", fill, shade(fill, 0.78)), mx + c * cell - cell / 2, my + r * cell - cell / 2);
    }
  }
  g.globalAlpha = 1;
  // Hills behind the city, with a scatter of house lights.
  const hills = new Path2D();
  hills.moveTo(0, sy);
  const off = R() * 100;
  const hillAt = (x) => sy - h * (0.1 + 0.08 * noise(x * 0.002 / k + off, 1) + 0.03 * noise(x * 0.01 / k, off));
  for (let x = 0; x <= w + 4; x += 4) hills.lineTo(x, hillAt(x));
  hills.lineTo(w, sy);
  hills.closePath();
  g.fillStyle = "#0e1230";
  g.fill(hills);
  for (let i = 0; i < 70 * Math.max(0.4, w / 1440); i += 1) {
    const x = R() * w;
    const y = hillAt(x) + R.range(4, 40) * k;
    if (y > sy - 4) continue;
    g.fillStyle = `rgba(255,${R.range(190, 225) | 0},140,${R.range(0.35, 0.8)})`;
    g.fillRect(x, y, 1.6 * k, 1.6 * k);
  }
  s.back = back;
  // The city: three rows of buildings, far to near, in tiles.
  const [city, cg] = layer(s);
  s.live = [];
  s.masts = [];
  s.signs = [];
  s.sources = [];
  const ROWS = [
    { h: [5, 15], w: [2, 4], body: "#262d63", win: 0.16, base: 0 },
    { h: [7, 26], w: [3, 5], body: "#1c2252", win: 0.22, base: 0 },
    { h: [4, 15], w: [3, 6], body: "#141938", win: 0.28, base: 0, near: true },
  ];
  // One landmark: a tall tower with a lit crown, two-thirds across.
  const landmark = Math.round((w * R.range(0.62, 0.7)) / cell);
  for (const [ri, row] of ROWS.entries()) {
    let c = -Math.floor(R() * 3);
    while (c < cols) {
      const bw = Math.round(R.range(row.w[0], row.w[1] + 0.99));
      const u = (c * cell) / w;
      const side = Math.abs(u - 0.5) * 2;
      let bh = Math.round(R.range(row.h[0], row.h[1]) * (0.6 + 0.7 * noise(c * 0.08 + ri * 5, ri)) * (row.near ? 0.55 + 0.7 * side : 1));
      const isLandmark = ri === 1 && c <= landmark && c + bw > landmark;
      if (isLandmark) bh = Math.round((h * 0.42) / cell);
      const top = shore - bh;
      const body = shade(row.body, R.range(0.85, 1.2));
      const warm = R() < 0.65;
      const office = R() < 0.35;
      const lit = row.win * R.range(0.4, 2);
      // Crowns: stepped, spire, antenna or flat.
      const crown = isLandmark ? "landmark" : R() < 0.18 ? "step" : R() < 0.22 ? "spire" : R() < 0.3 ? "mast" : "flat";
      if (crown === "step" && bw > 2) for (let i = 1; i < bw - 1; i += 1) put(cg, s, tile(s, "=", body, shade(body, 1.6)), (c + i) * cell, (top - 1) * cell);
      if (crown === "spire") {
        const mid = c + Math.floor(bw / 2);
        put(cg, s, tile(s, "|", null, shade(body, 2.6)), mid * cell, (top - 2) * cell);
        put(cg, s, tile(s, "A", body, shade(body, 1.9)), mid * cell, (top - 1) * cell);
      }
      if (crown === "mast" || isLandmark) s.masts.push({ x: (c + Math.floor(bw / 2)) * cell, y: (top - (isLandmark ? 7 : 2)) * cell, ph: R() * TAU });
      if (crown === "mast") put(cg, s, tile(s, "|", null, shade(body, 2.4)), (c + Math.floor(bw / 2)) * cell, (top - 1) * cell);
      if (isLandmark) {
        // A tapering crown of lit lattice, then a mast.
        const mid = c + Math.floor(bw / 2);
        for (let j = 1; j <= 5; j += 1) {
          const half = Math.max(0, Math.floor((5 - j) / 2));
          for (let dc = -half; dc <= half; dc += 1) {
            const glyph = dc === -half ? "/" : dc === half ? "\\" : "X";
            put(cg, s, tile(s, glyph, "#2a3f8a", "#9fd8ff"), (mid + dc) * cell, (top - j) * cell);
          }
        }
        put(cg, s, tile(s, "|", null, "#9fd8ff"), mid * cell, (top - 6) * cell);
        s.crown = { x: mid * cell, y: (top - 3) * cell };
        s.sources.push({ x: mid * cell, c: "#9fd8ff", a: 0.7, wide: 2 });
      }
      for (let r = top; r < shore; r += 1) {
        for (let i = 0; i < bw; i += 1) {
          const x = (c + i) * cell;
          const y = r * cell;
          const edge = i === 0 || i === bw - 1;
          const on = !edge && (office ? (r % 2 === 0 && R() < lit * 2.2) : R() < lit);
          const pal = warm ? WARM : COOL;
          const lightC = pal[Math.floor(R() * pal.length)];
          const offT = tile(s, edge ? "|" : r % 2 ? "." : ":", r === top ? shade(body, 1.25) : body, shade(body, 2.1));
          const onT = tile(s, office ? "=" : "#", mixc(lightC, body, ri === 0 ? 0.45 : 0.15), shade(lightC, 0.62));
          put(cg, s, on ? onT : offT, x, y);
          if (!edge && R() < 0.035) s.live.push({ x, y, on, onT, offT, p: R.range(20, 80), ph: R(), duty: R.range(0.2, 0.5) });
          if (on && ri === 2 && r > shore - 4 && R() < 0.25) s.sources.push({ x, c: lightC, a: 0.35, wide: 1 });
        }
      }
      // Neon signs hang off the near buildings, mostly toward the edges.
      if (row.near && bh > 6 && side > 0.35 && R() < 0.5 && s.signs.length < 5) {
        const word = WORDS[Math.floor(R() * WORDS.length)];
        const n = NEON[s.signs.length % NEON.length];
        const sx = (R() < 0.5 ? c - 1 : c + bw) * cell;
        const sy0 = (top + 1 + Math.floor(R() * Math.max(1, bh - word.length - 1))) * cell;
        s.signs.push({ x: sx, y: sy0, word, n, ids: Array.from(word).map((ch) => tile(s, ch, n.fill, n.ink)), dim: Array.from(word).map((ch) => tile(s, ch, shade(n.fill, 0.35), shade(n.fill, 0.2))), ph: R() * 100 });
        s.sources.push({ x: sx, c: n.fill, a: 0.6, wide: 1, sign: s.signs.length - 1 });
      }
      c += bw + (row.near && side < 0.4 && R() < 0.5 ? Math.round(R.range(1, 3)) : 0);
    }
  }
  cg.globalAlpha = 1;
  s.city = city;
  // The promenade: a lamp every few tiles along the shore.
  s.lamps = [];
  for (let c = 1; c < cols; c += 4) s.lamps.push({ x: c * cell, ph: R() * TAU });
  s.lampT = tile(s, "*", null, "#ffd18a");
  s.sources.push({ x: mx - cell / 2, c: "#f6e6b8", a: 0.55, wide: 2, moon: true });
  // Reflections: glyph columns under each light.
  s.ripples = ["~", "-", "~", "=", "-", "~"];
  s.rippleIds = new Map();
  s.rid = (c) => {
    if (!s.rippleIds.has(c)) s.rippleIds.set(c, s.ripples.map((g2) => tile(s, g2, null, c)));
    return s.rippleIds.get(c);
  };
  // The ferry.
  const hull = "#151a2e";
  const F = [
    "   [##] ",
    " [######]",
    "\\========/",
  ];
  s.ferry = [];
  F.forEach((line, r) => Array.from(line).forEach((ch, i) => {
    if (ch === " ") return;
    const lit = ch === "#";
    s.ferry.push({ i, r, id: tile(s, lit ? "#" : ch, lit ? "#ffd88f" : hull, lit ? "#8a5a10" : "#7c86b8") });
  }));
  s.ferryW = 10;
  s.wake = ["~", "-", "~"].map((g2) => tile(s, g2, null, "#8fa0e0"));
  s.swellIds = ["~", "-", "~", "~"].map((g2, i) => tile(s, g2, null, ["#5a6cc8", "#4658b0", "#7484dc", "#4c5eb8"][i]));
  s.swells = Array.from({ length: Math.round(70 * Math.max(0.4, (w * h) / 1.3e6)) }, () => ({ x: R() * w, y: sy + Math.pow(R(), 0.8) * (h - sy), len: Math.round(R.range(2, 6)), v: R.range(2, 6) * k, ph: R() * TAU }));
  s.mastT = [tile(s, "*", null, "#ff4a3d"), tile(s, ".", null, "#7a2a28")];
  s.ferrySeed = R() * 1000;
}

function signOn(sg, t, i) {
  // Mostly steady; every so often a letter stutters, rarely the whole sign.
  const n = Math.floor(t * 8);
  const v = Math.sin(n * 12.9898 + sg.ph * 7 + i * 3.1) * 43758.5453;
  const r = v - Math.floor(v);
  const whole = Math.sin(Math.floor(t * 0.5) * 3.7 + sg.ph) > 0.97;
  return !(whole || r < 0.015);
}

export const harbor = {
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
    const sy = s.shore * cell;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    ctx.drawImage(s.city, 0, 0, w, h);
    for (const lv of s.live) {
      const flip = ((t / lv.p + lv.ph) % 1) < lv.duty;
      if (flip) put(ctx, s, lv.on ? lv.offT : lv.onT, lv.x, lv.y);
    }
    for (const m of s.masts) put(ctx, s, s.mastT[Math.sin(t * 2.4 + m.ph) > 0.6 ? 0 : 1], m.x, m.y);
    if (s.crown) {
      const a = 0.12 + 0.08 * Math.sin(t * 0.8);
      const gl = ctx.createRadialGradient(s.crown.x + cell / 2, s.crown.y, 0, s.crown.x + cell / 2, s.crown.y, cell * 7);
      gl.addColorStop(0, `rgba(159,216,255,${a})`);
      gl.addColorStop(1, "rgba(159,216,255,0)");
      ctx.globalAlpha = 1;
      ctx.fillStyle = gl;
      ctx.fillRect(s.crown.x - cell * 7, s.crown.y - cell * 7, cell * 15, cell * 14);
    }
    for (const sg of s.signs) {
      let lit = 0;
      sg.ids.forEach((id, i) => {
        const on = signOn(sg, t, i);
        lit += on ? 1 : 0;
        put(ctx, s, on ? id : sg.dim[i], sg.x, sg.y + i * cell);
      });
      const halo = ctx.createRadialGradient(sg.x + cell / 2, sg.y + (sg.word.length * cell) / 2, 0, sg.x + cell / 2, sg.y + (sg.word.length * cell) / 2, cell * (sg.word.length * 0.9 + 2));
      halo.addColorStop(0, `rgba(${sg.n.glow},${0.22 * (lit / sg.word.length)})`);
      halo.addColorStop(1, `rgba(${sg.n.glow},0)`);
      ctx.globalAlpha = 1;
      ctx.fillStyle = halo;
      ctx.fillRect(sg.x - cell * 8, sg.y - cell * 4, cell * 17, sg.word.length * cell + cell * 8);
    }
    // The bay.
    const water = ctx.createLinearGradient(0, sy, 0, h);
    water.addColorStop(0, "#0a0d26");
    water.addColorStop(1, "#04050f");
    ctx.globalAlpha = 1;
    ctx.fillStyle = water;
    ctx.fillRect(0, sy, w, h - sy);
    // The city upside down, dim and trembling.
    const band = Math.max(1, Math.round(3 * k * s.dpr)) / s.dpr;
    for (let y = 0; y < Math.min(h - sy, sy * 0.6); y += band) {
      const depth = y / (h - sy);
      ctx.globalAlpha = 0.22 * Math.max(0, 1 - depth * 1.7);
      if (ctx.globalAlpha < 0.01) break;
      const dx = (1.2 + 7 * depth) * k * (Math.sin(y * 0.11 / k + t * 1.3) + 0.5 * Math.sin(y * 0.31 / k - t * 2.1));
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, sy + y, w, band);
      ctx.clip();
      ctx.setTransform(s.dpr, 0, 0, -s.dpr, dx * s.dpr, (sy * 2) * s.dpr);
      ctx.drawImage(s.city, 0, 0, w, h);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    // Swells drifting across the bay.
    for (const sw of s.swells) {
      const span = w + sw.len * cell;
      const x = ((((sw.x + sw.v * t) % span) + span) % span) - sw.len * cell;
      const a = 0.3 + 0.4 * (0.5 + 0.5 * Math.sin(t * 0.8 + sw.ph));
      for (let i = 0; i < sw.len; i += 1) put(ctx, s, s.swellIds[(i + Math.floor(sw.ph * 3)) % 4], x + i * cell, sw.y, a * Math.sin((Math.PI * (i + 0.5)) / sw.len));
    }
    // Glyph columns under the bright lights.
    for (const src of s.sources) {
      const ids = s.rid(src.c);
      const len = Math.round((h - sy) / cell);
      for (let r = 0; r < len; r += 1) {
        const fade = 1 - r / len;
        const jit = Math.round(Math.sin(r * 1.7 + t * 2.2 + src.x) * 1.2 * (0.3 + r / len));
        for (let i = 0; i < src.wide; i += 1) {
          const on = noise(src.x * 0.1 + i, r * 0.6 - t * 1.4) > 0.42;
          if (!on) continue;
          const lit = src.sign === undefined || signOn(s.signs[src.sign], t, 0);
          put(ctx, s, ids[(r + i + Math.floor(t * 3)) % ids.length], src.x + (jit + i - Math.floor(src.wide / 2)) * cell * (src.moon ? 1 : 0.6), sy + r * cell, src.a * fade * fade * (lit ? 1 : 0.3));
        }
      }
    }
    // Lamps along the promenade.
    for (const l of s.lamps) put(ctx, s, s.lampT, l.x, sy - cell * 0.6, 0.75 + 0.25 * Math.sin(t * 3 + l.ph));
    // The ferry, crossing every so often.
    const P = 70;
    const n = Math.floor(t / P);
    const p = (t - n * P) / 55;
    if (p < 1) {
      const dir = Math.sin(n * 7.1 + s.ferrySeed) > 0 ? 1 : -1;
      const fx = dir > 0 ? -s.ferryW * cell + p * (w + s.ferryW * cell * 2) : w + s.ferryW * cell - p * (w + s.ferryW * cell * 2);
      const fy = sy + cell * (3 + 2 * Math.sin(s.ferrySeed)) + Math.sin(t * 1.2) * 1.2;
      for (const f of s.ferry) put(ctx, s, f.id, fx + (dir > 0 ? f.i : s.ferryW - f.i) * cell, fy + f.r * cell);
      for (let i = 1; i < 9; i += 1) put(ctx, s, s.wake[(i + Math.floor(t * 4)) % 3], fx - dir * i * cell + (dir > 0 ? 0 : s.ferryW * cell), fy + 2 * cell + Math.sin(i + t * 3) * 2, 0.5 * (1 - i / 9));
    }
    ctx.globalAlpha = 1;
  },
};

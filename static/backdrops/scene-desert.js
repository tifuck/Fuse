// Scene: Desert Night. A long exposure over the dunes: the stars wheel
// round the pole star leaving arcs of light, a crescent moon sets into the
// last of the dusk and lights the dune faces warm while their slip faces
// fall into indigo shadow. Crests are drawn in sand tiles, ripples run
// across the lit faces, the wind lifts sand off the crests in gusts, a
// saguaro stands on the near dune, a campfire burns far off, a fox's eyes
// shine now and then, and a meteor falls.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade, rgb, hex } from "./tiles.js";

const TAU = Math.PI * 2;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (u) => u * u * (3 - 2 * u);
const lerp3 = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
// Back to front: base line, peak height, peak spacing (fractions), haze.
const DUNES = [
  { base: 0.64, hgt: [0.02, 0.05], gap: [0.1, 0.2], haze: 0.7 },
  { base: 0.7, hgt: [0.04, 0.09], gap: [0.16, 0.3], haze: 0.45 },
  { base: 0.82, hgt: [0.08, 0.15], gap: [0.28, 0.45], haze: 0.2 },
  { base: 1.02, hgt: [0.14, 0.26], gap: [0.45, 0.75], haze: 0, sides: true },
];
const LIT = rgb("#d89c62");
const RIM = rgb("#f6cf96");
const LIT_DEEP = rgb("#8a5444");
const SHADOW = rgb("#2b2246");
const SHADOW_DEEP = rgb("#43325a");
const HAZE = rgb("#4a3a66");
const HAZE_WARM = rgb("#b06a58");
// Star trail angle: the exposure, and the turn rate (rad/s).
const EXPO = 0.62;
const SPIN = TAU / 900;
// The saguaro, top down to where the trunk carries on alone.
const SAGUARO = [
  "    ###    ",
  "    ###    ",
  "    ###    ",
  "##  ###    ",
  "##  ###    ",
  "##  ###  ##",
  "##  ###  ##",
  "##  ###  ##",
  "#######  ##",
  " ######  ##",
  "    #######",
  "    ###### ",
];
const STAR_COLS = ["#fff4e0", "#dfe8ff", "#bcd0ff", "#ffd9a8", "#ffffff"];

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 12 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const S = Math.max(w, h);
  const portrait = h > w;
  // Dune crests, one sample per CSS px.
  const W = Math.ceil(w) + 2;
  s.dunes = DUNES.map((D, li) => {
    const peaks = [];
    let x = -R.range(0.05, 0.2) * w;
    while (x < w * 1.3) {
      const side = Math.min(1, Math.abs(x / w - 0.5) * 2);
      const hh = R.range(D.hgt[0], D.hgt[1]) * (D.sides ? 0.45 + 0.75 * side : 1) * (portrait ? 0.8 : 1);
      peaks.push({ x, y: h * (D.base - hh), s: R.range(0.15, 0.5), bow: R.range(0.5, 1.4), s2: R.range(0.8, 1.6), wig: R.range(4, 14) * k, ph: R() * TAU });
      x += R.range(D.gap[0], D.gap[1]) * w * (portrait ? 1.6 : 1);
    }
    const base = h * D.base;
    const crest = new Float32Array(W);
    const owner = new Int16Array(W);
    for (let px = 0; px < W; px += 1) {
      let i = 0;
      while (i < peaks.length - 2 && peaks[i + 1].x <= px) i += 1;
      const p = peaks[i];
      const q = peaks[i + 1];
      const v = p.x + (q.x - p.x) * 0.32;
      const vy = base - (Math.min(p.y, q.y) - base) * -0.15;
      let y;
      if (px < v) {
        const u = clamp01((px - p.x) / (v - p.x));
        y = p.y + (vy - p.y) * Math.pow(u, 0.75);
      } else {
        const u = clamp01((px - v) / (q.x - v));
        y = vy + (q.y - vy) * smooth(u);
      }
      crest[px] = y + 0.004 * h * (noise(px / (40 * k) + li * 9, li) - 0.5);
      owner[px] = i;
    }
    const span = Math.max(cell * 4, base - Math.min(...peaks.map((p) => p.y)));
    return { D, li, peaks, crest, base, span };
  });
  const top = Math.min(...s.dunes.map((d) => Math.min(...d.crest)));
  s.skyBottom = top + 2;
  // Sky: indigo above, the last of the dusk low on the left.
  const [sky, g] = layer(s);
  const hy = h * 0.66;
  const lin = g.createLinearGradient(0, 0, 0, hy);
  lin.addColorStop(0, "#05071c");
  lin.addColorStop(0.45, "#0f1440");
  lin.addColorStop(0.8, "#262860");
  lin.addColorStop(1, "#4a3a6c");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const dx = portrait ? w * 0.1 : w * R.range(0.08, 0.2);
  const dusk = g.createRadialGradient(dx, hy, 0, dx, hy, S * 0.6);
  dusk.addColorStop(0, "rgba(236,138,84,0.7)");
  dusk.addColorStop(0.25, "rgba(196,96,96,0.35)");
  dusk.addColorStop(0.6, "rgba(120,70,120,0.12)");
  dusk.addColorStop(1, "rgba(120,70,120,0)");
  g.fillStyle = dusk;
  g.fillRect(0, 0, w, h);
  // Faint fixed stars between the trails.
  for (let i = 0; i < 260 * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    g.fillStyle = `rgba(220,226,255,${R.range(0.08, 0.35)})`;
    const r = R.range(0.5, 1.1) * k;
    g.fillRect(R() * w, Math.pow(R(), 1.2) * top, r, r);
  }
  dotGrid(g, s, "#7a7ac0", 0.18);
  // The moon: a thin crescent in tiles, low over the dusk.
  const mx = Math.round((portrait ? w * 0.24 : dx + w * R.range(0.06, 0.12)) / cell) * cell;
  const my = Math.round((hy - h * (portrait ? 0.2 : 0.24)) / cell) * cell;
  const mr = Math.max(2, Math.round((Math.min(w, h) * 0.04) / cell));
  const mg = g.createRadialGradient(mx, my, 0, mx, my, mr * cell * 7);
  mg.addColorStop(0, "rgba(255,226,170,0.28)");
  mg.addColorStop(1, "rgba(255,226,170,0)");
  g.fillStyle = mg;
  g.fillRect(0, 0, w, h);
  // Earthshine on the dark of the moon.
  g.fillStyle = "rgba(120,130,200,0.14)";
  g.beginPath();
  g.arc(mx, my, (mr + 0.4) * cell, 0, TAU);
  g.fill();
  for (let r = -mr; r <= mr; r += 1) {
    for (let c = -mr; c <= mr; c += 1) {
      const d = Math.hypot(c, r) / (mr + 0.4);
      if (d > 1) continue;
      const lit = Math.hypot(c + mr * 0.55, r + mr * 0.15) / (mr + 0.4) > 0.92;
      if (!lit) continue;
      const fill = d > 0.82 ? "#f8e2a8" : "#f2d48e";
      put(g, s, tile(s, d > 0.82 ? ")" : ":", fill, "#c89a52"), mx + c * cell - cell / 2, my + r * cell - cell / 2);
    }
  }
  g.globalAlpha = 1;
  s.sky = sky;
  // Star trails, painted once at a reduced scale around the pole; the frame
  // turns them.
  const px = (s.poleX = Math.round(w * (portrait ? 0.64 : R.range(0.66, 0.74))));
  const py = (s.poleY = Math.round(h * (portrait ? 0.13 : R.range(0.14, 0.2))));
  const Rmax = Math.max(Math.hypot(px, py), Math.hypot(w - px, py), Math.hypot(px, top - py), Math.hypot(w - px, top - py));
  s.Rmax = Rmax;
  const sc = 0.6;
  const tc = document.createElement("canvas");
  tc.width = tc.height = Math.ceil(Rmax * 2 * sc * s.dpr);
  const tg = tc.getContext("2d");
  tg.scale(sc * s.dpr, sc * s.dpr);
  tg.translate(Rmax, Rmax);
  tg.lineCap = "round";
  s.heads = [];
  const n = Math.round(420 * Math.min(1.4, (w * h) / 1.3e6) + 60);
  for (let i = 0; i < n; i += 1) {
    const r = Math.sqrt(R()) * Rmax;
    const a0 = R() * TAU;
    const b = Math.pow(R(), 2.2);
    const col = STAR_COLS[Math.floor(R() * STAR_COLS.length)];
    const [cr, cg, cb] = rgb(col);
    tg.lineWidth = (0.5 + 1.4 * b) * k;
    const seg = 10;
    for (let j = 0; j < seg; j += 1) {
      tg.strokeStyle = `rgba(${cr},${cg},${cb},${(0.1 + 0.48 * b) * Math.pow(1 - j / seg, 1.4)})`;
      tg.beginPath();
      tg.arc(0, 0, r, a0 + (EXPO * j) / seg, a0 + (EXPO * (j + 1)) / seg + 0.002);
      tg.stroke();
    }
    if (b > 0.18 || R() < 0.12) s.heads.push({ r, a: a0, id: tile(s, b > 0.55 ? "*" : b > 0.3 ? "+" : ".", null, col), b, ph: R() * TAU });
  }
  s.trails = tc;
  s.pole = [tile(s, "*", null, "#ffffff"), tile(s, "+", null, "#dfe8ff")];
  // The dunes, painted at a third of a tile and scaled up smoothly.
  const q = Math.max(0.75, cell / 8);
  const iw = Math.ceil(w / q);
  const ih = Math.ceil(h / q);
  const small = document.createElement("canvas");
  small.width = iw;
  small.height = ih;
  const sg = small.getContext("2d");
  const img = sg.createImageData(iw, ih);
  const shadowAt = (d, x, y) => {
    // The slip face: a band falling down-right from each peak, between a
    // sinuous brink line and the foot of the next dune's lit face.
    const P = d.peaks;
    let best = -1e9;
    for (let i = 0; i < P.length - 1; i += 1) {
      const p = P[i];
      if (p.x > x) break;
      const u = Math.max(0, (y - p.y) / d.span);
      // Concave sweeps: steep under the peak, curling away lower down.
      const b1 = p.x + d.span * (p.s * u + p.bow * u * u) + p.wig * Math.sin(u * 5 + p.ph) * Math.min(1, u * 3);
      const v = p.x + (P[i + 1].x - p.x) * 0.32;
      const u2 = Math.max(0, (y - d.base + d.span * 0.3) / d.span);
      const b2 = v + (P[i + 1].x - v) * 0.2 + d.span * p.s2 * (0.3 * u2 + u2 * u2);
      best = Math.max(best, Math.min(x - b1, b2 - x));
    }
    return best;
  };
  s.shadowAt = shadowAt;
  const duneColor = (d, x, y, xi) => {
    const depth = y - d.crest[xi];
    const fall = clamp01(depth / (d.span + h * 0.06));
    const edge = clamp01(shadowAt(d, x, y) / (2 * q) + 0.5);
    // Moonlit stoss faces: warm, brightest at the crest, glowing more on
    // the side toward the dusk.
    const warm = clamp01(1 - Math.abs(x - dx) / (w * 0.9));
    let lit = lerp3(RIM, LIT, clamp01(depth / (8 * k)));
    lit = lerp3(lit, LIT_DEEP, Math.pow(fall, 0.7) * 0.85);
    lit = lit.map((v) => v * (0.62 + 0.42 * warm));
    const shd = lerp3(SHADOW, SHADOW_DEEP, Math.pow(fall, 0.8) * 0.8);
    let c = lerp3(lit, shd, edge);
    // Ripples ruffle the lit faces.
    const rip = 0.5 + 0.5 * Math.sin(depth / (3.2 * k) + noise(x / (60 * k), y / (30 * k) + d.li) * 6);
    c = lerp3(c, c.map((v) => v * 0.9), rip * (1 - edge) * 0.6 * (1 - fall));
    // Distance haze.
    return lerp3(c, lerp3(HAZE, HAZE_WARM, warm * 0.6), d.D.haze);
  };
  for (let j = 0; j < ih; j += 1) {
    const y = (j + 0.5) * q;
    for (let i = 0; i < iw; i += 1) {
      const x = (i + 0.5) * q;
      const xi = Math.min(W - 1, Math.round(x));
      // Front to back, each crest antialiased over what lies behind it.
      let acc = [0, 0, 0];
      let left = 1;
      for (let li = s.dunes.length - 1; li >= 0 && left > 0.004; li -= 1) {
        const d = s.dunes[li];
        const cov = clamp01((y - d.crest[xi]) / q + 0.5);
        if (cov <= 0) continue;
        const c = duneColor(d, x, y, xi);
        const a = cov * left;
        acc = acc.map((v, n) => v + c[n] * a);
        left -= a;
      }
      const o = (j * iw + i) * 4;
      const al = 1 - left;
      if (al <= 0) {
        img.data[o + 3] = 0;
        continue;
      }
      img.data[o] = acc[0] / al;
      img.data[o + 1] = acc[1] / al;
      img.data[o + 2] = acc[2] / al;
      img.data[o + 3] = al * 255;
    }
  }
  sg.putImageData(img, 0, 0);
  const [land, lg] = layer(s);
  lg.imageSmoothingEnabled = true;
  lg.imageSmoothingQuality = "high";
  lg.drawImage(small, 0, 0, iw * q, ih * q);
  // Crests in tiles on the nearer dunes: glyphs follow the slope.
  for (const d of s.dunes) {
    if (d.li === 0) continue;
    const near = d.li / (s.dunes.length - 1);
    for (let c = 0; c < cols; c += 1) {
      const x = c * cell + cell / 2;
      const xi = Math.min(W - 1, Math.max(0, Math.round(x)));
      const y = d.crest[xi];
      if (y > h) continue;
      const sl = (d.crest[Math.min(W - 1, xi + 3)] - d.crest[Math.max(0, xi - 3)]) / 6;
      const isPeak = d.peaks.some((p) => Math.abs(p.x - x) < cell * 0.6);
      const gl = isPeak ? "^" : sl < -0.16 ? "/" : sl > 0.16 ? "\\" : sl < -0.05 ? "'" : sl > 0.05 ? "." : hash(c, d.li, 1) < 0.5 ? "-" : "~";
      const shadow = shadowAt(d, x, y + cell * 0.5) > 0;
      const base = shadow ? lerp3(SHADOW, RIM, 0.25) : RIM;
      const f = hex(lerp3(base, lerp3(HAZE, HAZE_WARM, 0.5), d.D.haze));
      const fy = Math.round(((y - cell * 0.55) / cell) * 2) / 2 * cell;
      if (d.li >= 2) put(lg, s, tile(s, gl, f, shadow ? hex(lerp3(SHADOW, RIM, 0.55)) : shade(f, 0.62)), c * cell, fy, 0.9);
      else put(lg, s, tile(s, gl, null, f), c * cell, fy, 0.8);
    }
    lg.globalAlpha = 1;
  }
  // The near dune in sand tiles, fading into the paint below the crest.
  const front = s.dunes[s.dunes.length - 1];
  const rows = Math.ceil(h / cell) + 1;
  for (let c = 0; c < cols; c += 1) {
    const x = c * cell + cell / 2;
    const xi = Math.min(W - 1, Math.round(x));
    const r0 = Math.ceil(front.crest[xi] / cell);
    for (let r = r0; r < rows; r += 1) {
      const y = r * cell + cell / 2;
      const depth = (y - front.crest[xi]) / cell;
      const keep = 1 - depth / 4 + 0.5 * (noise(c * 0.3, r * 0.5) - 0.5);
      if (keep < hash(c, r, 5)) continue;
      const sh = shadowAt(front, x, y) > 0;
      const t = clamp01(depth / 10);
      const fill = sh ? mixc("#2e2448", "#3e2e54", t) : mixc("#e0a46a", "#9a5e48", t);
      // Sand grain as bare tiles, a ripple mark now and then.
      const gl = hash(c, r, 6) < 0.25 ? (sh ? "." : "~") : " ";
      put(lg, s, tile(s, gl, fill, sh ? shade(fill, 1.35) : shade(fill, 0.8)), c * cell, r * cell, 0.75 - 0.45 * clamp01(depth / 4));
    }
  }
  lg.globalAlpha = 1;
  // The saguaro on the near dune, rim-lit from the moon side.
  const sgC = Math.round((w * (portrait ? 0.72 : R.range(0.78, 0.86))) / cell);
  const sgBase = Math.min(W - 1, Math.round((sgC + 5) * cell));
  const host = s.dunes[3].crest[sgBase] < h * 0.92 ? s.dunes[3] : s.dunes[2];
  const footR = Math.floor(host.crest[sgBase] / cell) + 2;
  const trunk = Math.max(2, Math.min(portrait ? 4 : 7, Math.round((h / cell) * 0.1)));
  const art = SAGUARO.concat(Array.from({ length: trunk }, () => "    ###    "));
  const on = (r, c) => r >= 0 && r < art.length && c >= 0 && c < 11 && art[r][c] === "#";
  const body = "#1a1630";
  const top0 = footR - art.length;
  art.forEach((line, r) => Array.from(line).forEach((ch, c) => {
    if (ch !== "#") return;
    const L = !on(r, c - 1);
    const Rr = !on(r, c + 1);
    const T = !on(r - 1, c);
    const gl = T ? (L ? "/" : Rr ? "\\" : r < 3 || on(r, c - 1) && on(r, c + 1) && !on(r, c - 2) ? "^" : "-") : L ? "(" : Rr ? ")" : on(r + 1, c) ? " " : "=";
    const ink = L ? "#f0b47a" : T ? "#8a6a7a" : "#3e3a68";
    put(lg, s, tile(s, gl, L ? "#2a2038" : body, ink), (sgC + c) * cell, (top0 + r) * cell);
  }));
  s.cactus = { x: (sgC + 5) * cell, y: (footR - 1) * cell };
  const height = art.length;
  // Its shadow on the sand, long, away from the moon.
  lg.save();
  lg.globalAlpha = 0.35;
  lg.fillStyle = "#140f28";
  lg.beginPath();
  lg.ellipse(s.cactus.x + cell * height * 0.5, s.cactus.y + cell * 0.8, cell * height * 0.5, cell * 0.5, 0.05, 0, TAU);
  lg.fill();
  lg.restore();
  s.land = land;
  // A campfire on a far dune at the other side, with a tent.
  const fd = s.dunes[2];
  // On a shaded face, where its light shows.
  let fireX = Math.round((w * (portrait ? 0.14 : 0.12)) / cell) * cell;
  for (let x = cell * 3; x < w * (portrait ? 0.4 : 0.24); x += cell) {
    const yy = fd.crest[Math.min(W - 1, Math.round(x))] + cell * 1.2;
    if (yy < h * 0.95 && shadowAt(fd, x, yy) > cell * 2) {
      fireX = x;
      break;
    }
  }
  const fy = fd.crest[Math.min(W - 1, Math.round(fireX))] + cell * 1.2;
  s.fire = { x: fireX, y: fy, ids: ["^", "*", "A", "^"].map((gl, i) => tile(s, gl, null, ["#ffb347", "#ffd27a", "#ff8a3a", "#ffe0a0"][i])), flame: ["^", "A", "M"].map((gl) => tile(s, gl, "#ff9a3c", "#fff0b0")), tentCells: [[2, 0, tile(s, "/", "#3a2a40", "#ffb070")], [3, 0, tile(s, "\\", "#1e1630", "#4a3a5a")], [1, 1, tile(s, "/", "#3a2a40", "#ffb070")], [2, 1, tile(s, "_", "#2a2038", "#ff9a50")], [3, 1, tile(s, "_", "#1e1630", "#4a3a5a")], [4, 1, tile(s, "\\", "#1e1630", "#4a3a5a")]], smoke: ["~", "(", ")", "~"].map((gl) => tile(s, gl, null, "#8a7aa0")) };
  // Wind-blown sand off the nearer crests.
  s.sand = ["'", ".", ",", "~", "-"].map((gl, i) => tile(s, gl, null, ["#fff0d4", "#ffe6c0", "#f8dcb0", "#fff4dc", "#ffe8c8"][i]));
  s.spouts = [];
  for (const d of s.dunes.slice(1)) {
    for (const p of d.peaks) {
      if (p.x < 0 || p.x > w) continue;
      s.spouts.push({ x: p.x, y: p.y, li: d.li, ph: R() * 10 });
    }
  }
  const pc = document.createElement("canvas");
  const pw = 200 * k;
  const ph = 36 * k;
  const blur = Math.max(2, 8 * k);
  pc.width = Math.ceil((pw + blur * 6) * s.dpr * 0.5);
  pc.height = Math.ceil((ph + blur * 6) * s.dpr * 0.5);
  const pg = pc.getContext("2d");
  pg.scale(s.dpr * 0.5, s.dpr * 0.5);
  pg.filter = `blur(${blur}px)`;
  pg.fillStyle = "rgba(246,200,150,0.6)";
  pg.beginPath();
  pg.ellipse(blur * 3 + pw * 0.45, blur * 3 + ph * 0.5, pw * 0.45, ph * 0.28, -0.08, 0, TAU);
  pg.fill();
  s.plume = { c: pc, w: pw + blur * 6, h: ph + blur * 6 };
  s.eyes = tile(s, ".", null, "#ffd24a");
  s.meteor = [tile(s, "*", null, "#ffffff"), tile(s, "-", null, "#cfe0ff"), tile(s, ".", null, "#9fb4ff")];
  s.metSeed = R() * 1000;
}

export const desert = {
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
    const { ctx, w, h, cell } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.sky, 0, 0, w, h);
    // The sky turns about the pole.
    const turn = -SPIN * t;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, w, s.skyBottom);
    ctx.clip();
    ctx.translate(s.poleX, s.poleY);
    ctx.rotate(turn);
    ctx.drawImage(s.trails, -s.Rmax, -s.Rmax, s.Rmax * 2, s.Rmax * 2);
    ctx.restore();
    for (const st of s.heads) {
      const a = st.a + turn;
      const x = s.poleX + Math.cos(a) * st.r - cell / 2;
      const y = s.poleY + Math.sin(a) * st.r - cell / 2;
      if (x < -cell || x > w || y < -cell || y > s.skyBottom) continue;
      put(ctx, s, st.id, x, y, (0.55 + 0.45 * st.b) * (0.8 + 0.2 * Math.sin(t * 2.1 + st.ph)));
    }
    const pg = ctx.createRadialGradient(s.poleX, s.poleY, 0, s.poleX, s.poleY, cell * 3);
    pg.addColorStop(0, "rgba(220,230,255,0.35)");
    pg.addColorStop(1, "rgba(220,230,255,0)");
    ctx.globalAlpha = 1;
    ctx.fillStyle = pg;
    ctx.fillRect(s.poleX - cell * 3, s.poleY - cell * 3, cell * 6, cell * 6);
    put(ctx, s, s.pole[0], s.poleX - cell / 2, s.poleY - cell / 2);
    meteor(ctx, s, t);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.land, 0, 0, w, h);
    fire(ctx, s, t);
    sand(ctx, s, t);
    fox(ctx, s, t);
    ctx.globalAlpha = 1;
  },
};

function fire(ctx, s, t) {
  const { cell, k } = s;
  const F = s.fire;
  const fl = 0.75 + 0.15 * Math.sin(t * 9.1) + 0.1 * Math.sin(t * 13.7 + 1);
  const gl = ctx.createRadialGradient(F.x + cell / 2, F.y, 0, F.x + cell / 2, F.y, cell * 10);
  gl.addColorStop(0, `rgba(255,170,80,${0.55 * fl})`);
  gl.addColorStop(0.3, `rgba(255,140,70,${0.2 * fl})`);
  gl.addColorStop(1, "rgba(255,140,60,0)");
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "lighter";
  ctx.fillStyle = gl;
  ctx.fillRect(F.x - cell * 10, F.y - cell * 10, cell * 21, cell * 20);
  ctx.globalCompositeOperation = "source-over";
  const f = Math.floor(t * 6);
  put(ctx, s, F.flame[f % 3], F.x, F.y - cell * 0.5);
  put(ctx, s, F.ids[f % 4], F.x, F.y - cell * 1.4, fl * 0.8);
  for (const [dc, dr, id] of F.tentCells) put(ctx, s, id, F.x + dc * cell, F.y + (dr - 1.5) * cell);
  for (let i = 0; i < 3; i += 1) {
    const life = (t / 1.6 + i / 3) % 1;
    put(ctx, s, F.ids[1], F.x + Math.sin(life * 6 + i * 2) * cell * 0.4, F.y - cell * (1.5 + life * 3), (1 - life) * 0.8);
  }
  for (let i = 0; i < 6; i += 1) {
    const life = (t / 7 + i / 6) % 1;
    const x = F.x + Math.sin(life * 5 + t * 0.4 + i) * cell * 0.5 + life * life * cell * 5 * k;
    put(ctx, s, F.smoke[i % 4], x, F.y - cell * (1.5 + life * 7), 0.45 * (1 - life) * Math.min(1, life * 6));
  }
}

function sand(ctx, s, t) {
  const { cell, k } = s;
  for (const sp of s.spouts) {
    // Gusts roll through from the left; each crest smokes as one passes.
    const g = clamp01((Math.sin(t * 0.3 - sp.x * 0.003 + sp.ph) + 0.25) * 1.6);
    if (g <= 0.02) continue;
    const near = sp.li / 3;
    const sc = 0.6 + near * 0.7;
    ctx.globalAlpha = g * (0.45 + 0.45 * near);
    ctx.drawImage(s.plume.c, sp.x - s.plume.w * 0.08 * sc, sp.y - s.plume.h * 0.6 * sc, s.plume.w * sc, s.plume.h * sc);
    // A ribbon of glyph sand streaming off the crest.
    const len = 6 + Math.round(near * 8);
    const flow = (t * 2.2 + sp.ph) % 1;
    for (let i = 0; i < len * 2; i += 1) {
      const row = i % 2;
      const f = (i >> 1) + flow + row * 0.5;
      const x = sp.x + f * cell * 0.85 * sc;
      const y = sp.y - cell * (0.7 + row * 0.55) - f * cell * (0.1 + row * 0.08) + Math.sin(f * 0.8 - t * 3 + sp.ph + row) * cell * 0.25;
      const a = g * Math.min(1, f) * (1 - f / len) * (0.6 + 0.4 * noise(f * 0.7 - t * 2, sp.ph));
      put(ctx, s, s.sand[3 + ((i + Math.floor(t * 2.2 + sp.ph)) % 2)], x, y, a * (0.7 + 0.3 * near) * (row ? 0.6 : 1));
    }
    const n = 6 + Math.round(near * 10);
    for (let i = 0; i < n; i += 1) {
      const life = (t * 0.5 + i / n + sp.ph) % 1;
      const x = sp.x + life * (70 + 110 * near) * k * (0.7 + 0.6 * hash(i, 1, 7));
      const y = sp.y - cell * 0.8 - Math.sin(life * Math.PI) * cell * (0.8 + 1.4 * hash(i, 2, 3)) + life * life * cell * 2.5;
      put(ctx, s, s.sand[i % 3], x, y, g * (1 - life) * (0.5 + 0.5 * near));
    }
  }
  ctx.globalAlpha = 1;
}

function fox(ctx, s, t) {
  const P = 53;
  const u = t % P;
  if (u < 12 || u > 24) return;
  const blink = Math.abs(u - 17.5) < 0.15 || Math.abs(u - 21.2) < 0.15;
  if (blink) return;
  const { cell } = s;
  const a = Math.min(1, (u - 12) / 0.8, (24 - u) / 0.8);
  const x = s.cactus.x - cell * 6;
  const y = s.cactus.y - cell * 0.3;
  put(ctx, s, s.eyes, x, y, a);
  put(ctx, s, s.eyes, x + cell * 0.7, y, a);
  ctx.globalAlpha = 1;
}

function meteor(ctx, s, t) {
  const P = 31;
  const n = Math.floor(t / P);
  const p = (t - n * P) / 1.4;
  if (p > 1) return;
  const { w, cell } = s;
  const r = (j) => hash(n, j, Math.floor(s.metSeed));
  const x0 = w * (0.15 + r(1) * 0.7);
  const y0 = s.skyBottom * (0.08 + r(2) * 0.35);
  const ang = (r(3) < 0.5 ? 1 : -1) * (0.35 + r(4) * 0.4);
  const len = Math.min(w, s.skyBottom) * (0.25 + r(5) * 0.2);
  const dx = Math.sin(ang) * len;
  const dy = Math.cos(ang) * len * 0.6;
  const hx = x0 + dx * p;
  const hy = y0 + dy * p;
  const a = Math.sin(p * Math.PI);
  const gr = ctx.createLinearGradient(hx, hy, hx - dx * 0.4, hy - dy * 0.4);
  gr.addColorStop(0, `rgba(230,240,255,${0.8 * a})`);
  gr.addColorStop(1, "rgba(230,240,255,0)");
  ctx.globalAlpha = 1;
  ctx.strokeStyle = gr;
  ctx.lineWidth = Math.max(1, 1.5 * s.k);
  ctx.beginPath();
  ctx.moveTo(hx, hy);
  ctx.lineTo(hx - dx * 0.4, hy - dy * 0.4);
  ctx.stroke();
  put(ctx, s, s.meteor[0], hx - cell / 2, hy - cell / 2, a);
  for (let i = 1; i < 4; i += 1) put(ctx, s, s.meteor[Math.min(2, i)], hx - (dx * 0.08 * i) - cell / 2, hy - (dy * 0.08 * i) - cell / 2, a * (1 - i / 4));
}

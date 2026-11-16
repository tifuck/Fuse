// Scene: Sakura Wind. A bright spring day in a temple garden. An old cherry
// stands in a near corner, its gnarled trunk splitting into heavy boughs
// that reach across the top of the sky, loaded with layered blossom in ASCII
// tiles, lit on the sun side and shaded beneath. Below, a pond crosses the
// garden: a vermilion moon bridge arches over it and closes its circle in
// the reflection, stepping stones lead out from the near bank past a stone
// lantern, and beyond, a three-tiered pagoda and a temple wall stand on a
// knoll among distant blossom trees. The wind comes in gusts: the boughs
// rock, petals stream across in drifts, rafts of fallen petals float on the
// pond, rings spread where they land, bees work the flowers, and now and
// then a white-eye drops in to hop about the blossom.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const LOOP = 600;
const HAZE = "#e8eaf2";
const PETAL = ["o", "*", "~", "-", "*", "%"];
const PINK = ["#e8789e", "#f096b4", "#d8668c", "#f4a8c0", "#e4869f"];
// Shade ramps, dark to light: cherry blossom, azalea, evergreen, bark, stone.
const RAMP = ["#b86e8e", "#d488a6", "#e8a0ba", "#f4bccd", "#fad4e0", "#fde8ef", "#fff8fa"];
const AZALEA = ["#8a3a6a", "#a84a80", "#c86098", "#de7cb0", "#ec9cc4", "#f6bcd8", "#fcdcec"];
const GREEN = ["#4f705e", "#5f826c", "#729680", "#88aa92", "#a2c0a8", "#bed4c0", "#d8e8d6"];
const BARK = ["#3a262c", "#543a40", "#74545a", "#9a786e", "#c4a090"];
const STONE = ["#7c7c8e", "#9898a8", "#b6b6c4", "#d4d4dc"];
const VERM = ["#b04a40", "#d4604e", "#ec8068"];

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const pick = (ramp, v) => ramp[Math.round(clamp01(v) * (ramp.length - 1))];
const N8 = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];

// The wind: a steady breeze with gusts every half minute or so.
function windAt(t) {
  return 1 + 0.35 * Math.sin(t * 0.31) + 0.25 * Math.sin(t * 0.83 + 1) + 2.4 * Math.max(0, Math.sin((t * TAU) / 34)) ** 8 + 1.2 * Math.max(0, Math.sin((t * TAU) / 21 + 2)) ** 10;
}

// Each depth has its own tile size, so one atlas per size.
function atlasAt(s, px) {
  tiles(s, px);
  return { atlas: s.atlas, P: s.P, cell: s.cell };
}
function use(s, A) {
  s.atlas = A.atlas;
  s.P = A.P;
  s.cell = A.cell;
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  // Phones get smaller tiles, so the garden still fits round the tree.
  const base = Math.max(4, 13 * k * (s.w < 700 ? 0.8 : 1));
  const A = (s.A = { far: atlasAt(s, Math.max(3, base * 0.7)), near: atlasAt(s, base * 1.3), mid: atlasAt(s, base) });
  const { w, h, dpr } = s;
  const cell = A.mid.cell;
  const side = (s.side = R.sign());
  const lx = -side;
  const L3 = [lx * 0.55, -0.62, 0.56];
  const ux = (s.ux = (u) => (side < 0 ? u * w : w - u * w));
  const uOf = (s.uOf = (x) => (side < 0 ? x / w : (w - x) / w));
  const narrow = w < 700;
  // Integrated wind, so petals can be placed from t alone.
  const dt = 0.1;
  s.wint = new Float32Array(Math.ceil(LOOP / dt) + 2);
  for (let i = 1; i < s.wint.length; i += 1) s.wint[i] = s.wint[i - 1] + windAt(i * dt) * dt;
  s.wdt = dt;

  const bankY = (s.bankY = Math.round((h * (narrow ? 0.7 : 0.69)) / cell) * cell);
  const nOff = R() * 100;
  const nearAt = (s.nearAt = (x) => h * (0.915 + 0.03 * (noise((x * 0.004) / k + nOff, 1) - 0.5)) - h * 0.1 * clamp01(1 - uOf(x) / 0.3) ** 1.5);
  const pagX = Math.round(ux(narrow ? 0.8 : 0.85) / cell) * cell;
  const bn = narrow ? 8 : Math.max(10, Math.min(22, Math.round((w * 0.17) / cell)));
  const bcx = ux(narrow ? 0.6 : 0.71);
  const hOff = R() * 100;
  const midHill = (x) => h * (0.665 - 0.04 * noise((uOf(x) * 2.5) + hOff, 2)) - h * 0.04 * Math.exp(-(((x - pagX) / (w * 0.12)) ** 2));
  const farHill = (x) => h * (0.6 - 0.06 * noise((uOf(x) * 2) + hOff, 5) - 0.02 * noise(uOf(x) * 9 + hOff, 6));

  const [back, g] = layer(s);
  // Sky, sun, clouds.
  const sky = g.createLinearGradient(0, 0, 0, bankY);
  sky.addColorStop(0, "#b6d2ee");
  sky.addColorStop(0.5, "#e2ecf6");
  sky.addColorStop(1, "#f8ecf0");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  const sx = ux(0.62);
  const sun = g.createRadialGradient(sx, h * 0.08, 0, sx, h * 0.08, Math.max(w, h) * 0.6);
  sun.addColorStop(0, "rgba(255,252,236,0.95)");
  sun.addColorStop(1, "rgba(255,252,236,0)");
  g.fillStyle = sun;
  g.fillRect(0, 0, w, h);
  g.save();
  g.filter = `blur(${Math.max(2, 16 * k)}px)`;
  for (let i = 0; i < 7; i += 1) {
    const cx = R() * w;
    const cy = h * R.range(0.1, 0.42);
    g.fillStyle = `rgba(255,255,255,${R.range(0.35, 0.6)})`;
    for (let j = 0; j < 4; j += 1) {
      g.beginPath();
      g.ellipse(cx + R.range(-80, 80) * k, cy + R.range(-8, 8) * k, R.range(50, 130) * k, R.range(12, 28) * k, 0, 0, TAU);
      g.fill();
    }
  }
  g.restore();
  // Far mountains, then the far hill with its groves in small tiles.
  const ridge = (at, col, top) => {
    const p = new Path2D();
    p.moveTo(-4, h);
    for (let x = -4; x <= w + 4; x += 4 * k) p.lineTo(x, at(x));
    p.lineTo(w + 4, h);
    p.closePath();
    const gr = g.createLinearGradient(0, h * top, 0, bankY);
    gr.addColorStop(0, col);
    gr.addColorStop(1, mixc(col, HAZE, 0.55));
    g.fillStyle = gr;
    g.fill(p);
  };
  const mOff = R() * 100;
  ridge((x) => h * (0.53 - 0.13 * noise((x / w) * 1.6 + mOff, 9) - 0.03 * noise((x / w) * 7 + mOff, 3)), "#c6cae4", 0.4);
  ridge(farHill, "#bccbdc", 0.54);
  use(s, A.far);
  for (let x = R() * 30 * k; x < w; x += R.range(14, 40) * k) {
    if (noise((x / w) * 5 + hOff, 11) < 0.5 || R() < 0.3) continue;
    const pink = R() < 0.6;
    const rc = A.far.cell * R.range(1.8, 3.2);
    const y = farHill(x) + rc * 0.2 + R() * Math.max(0, midHill(x) - farHill(x)) * 0.5;
    const blobs = Array.from({ length: 2 + Math.floor(R() * 2) }, () => ({ x: x + R.range(-0.6, 0.6) * rc, y: y - rc * R.range(0.2, 0.6), r: rc * R.range(0.6, 0.9), z: R(), tone: 0 }));
    for (const c of blossom(s, R, blobs, { L: L3, haze: 0.42, lace: 0.25, ramp: pink ? RAMP : GREEN })) put(g, s, c.id, c.dc * A.far.cell, c.dr * A.far.cell);
  }
  g.globalAlpha = 1;
  use(s, A.mid);
  // The near hill down to the pond, a knoll for the pagoda.
  ridge(midHill, "#b2c8b4", 0.6);
  const bankG = g.createLinearGradient(0, bankY - h * 0.05, 0, bankY);
  bankG.addColorStop(0, "rgba(150,186,146,0)");
  bankG.addColorStop(1, "rgba(150,186,146,0.6)");
  g.fillStyle = bankG;
  g.fillRect(0, bankY - h * 0.05, w, h * 0.05);
  // Blossom and evergreen trees on the hill, clear of the pagoda.
  const nTrees = narrow ? 4 : 9;
  for (let i = 0, tries = 0; i < nTrees && tries < 60; tries += 1) {
    const x = ux(R.range(0.18, 1));
    const u = uOf(x);
    if (Math.abs(x - pagX) < cell * 10 || Math.abs(x - bcx) < (bn / 2 + 3) * cell || (u > 0.35 && u < 0.65)) continue;
    i += 1;
    const rc = cell * R.range(2.2, 3.6);
    const foot = Math.min(bankY - cell, midHill(x) + R.range(0.1, 0.45) * (bankY - midHill(x)));
    put(g, s, tile(s, "|", mixc("#7a5a5e", HAZE, 0.3), mixc("#a88a86", HAZE, 0.3)), Math.round(x / cell) * cell, Math.round(foot / cell - 1) * cell);
    const pink = R() < 0.65;
    const cy = Math.round(foot / cell - 1) * cell - rc * 0.55;
    const blobs = Array.from({ length: 3 + Math.floor(R() * 3) }, () => ({ x: x + R.range(-0.8, 0.8) * rc, y: cy + R.range(-0.4, 0.3) * rc, r: rc * R.range(0.55, 0.85), z: R(), tone: 0 }));
    for (const c of blossom(s, R, blobs, { L: L3, haze: 0.28, lace: 0.35, ramp: pink ? RAMP : GREEN })) put(g, s, c.id, c.dc * cell, c.dr * cell);
  }
  // The pagoda on its knoll, and the temple wall running from it.
  const pc = Math.round(pagX / cell);
  const pr = Math.round(midHill(pagX) / cell) + 1;
  for (const c of pagoda(s, lx, 0.12)) put(g, s, c.id, (pc + c.dc) * cell, (pr + c.dr) * cell);
  const wallN = narrow ? 5 : 14;
  for (let i = 7; i < 7 + wallN; i += 1) {
    const c = pc - side * i;
    const r = Math.round(midHill(c * cell) / cell) + 1;
    const end = i === 6 + wallN;
    const roofF = mixc(i % 2 ? "#62668e" : "#6a6e96", HAZE, 0.15);
    put(g, s, tile(s, end ? (side > 0 ? "/" : "\\") : "=", roofF, shade(roofF, 1.45)), c * cell, (r - 2) * cell);
    const wallF = mixc("#f4eee8", HAZE, 0.1);
    put(g, s, tile(s, i % 4 === 0 ? "|" : ":", wallF, "#c0a8a8"), c * cell, (r - 1) * cell);
  }
  g.globalAlpha = 1;
  // The far bank: stones at the water, grass, azaleas in bloom.
  const br = Math.round(bankY / cell) - 1;
  for (let c = 0; c < Math.ceil(w / cell); c += 1) {
    const x = c * cell;
    if (R() < 0.07) {
      const n = 2 + Math.floor(R() * 3);
      for (let j = 0; j < n; j += 1) {
        const lit = (j - n / 2) * lx > 0;
        const f = mixc(STONE[lit ? 3 : 2 - (j % 2)], HAZE, 0.1);
        put(g, s, tile(s, j === 0 ? "(" : j === n - 1 ? ")" : R.pick(["_", "o", "-"]), f, shade(f, 0.7)), (c + j) * cell, br * cell);
      }
      c += n;
    } else if (R() < 0.55) put(g, s, tile(s, R.pick(['"', ",", "v", "'"]), null, R.pick(["#6f9c72", "#82ae80", "#5f8a66"])), x, br * cell);
  }
  for (let i = 0; i < (narrow ? 3 : 7); i += 1) {
    const x = ux(R.range(0.2, 1));
    if (Math.abs(uOf(x) - 0.5) < 0.1) continue;
    const rc = cell * R.range(1.2, 2);
    const blobs = Array.from({ length: 3 }, () => ({ x: x + R.range(-1, 1) * rc, y: bankY - cell - rc * R.range(0.1, 0.4), r: rc * R.range(0.6, 0.9), z: R(), tone: 0 }));
    for (const c of blossom(s, R, blobs, { L: L3, haze: 0.08, lace: 0.3, ramp: AZALEA })) put(g, s, c.id, c.dc * cell, c.dr * cell);
  }
  g.globalAlpha = 1;
  // The pond, holding the garden upside down.
  const water = g.createLinearGradient(0, bankY, 0, h);
  water.addColorStop(0, "#cddff0");
  water.addColorStop(0.5, "#b0cae2");
  water.addColorStop(1, "#98b8d4");
  g.fillStyle = water;
  g.fillRect(0, bankY, w, h - bankY);
  const snap = document.createElement("canvas");
  snap.width = back.width;
  snap.height = back.height;
  snap.getContext("2d").drawImage(back, 0, 0);
  const band = Math.max(1, Math.round(2 * k * dpr)) / dpr;
  for (let y = 0; y < h - bankY; y += band) {
    const v = y / (h - bankY);
    g.globalAlpha = 0.5 * (1 - v) ** 1.3;
    const dx = (1 + 6 * v) * k * (Math.sin((y * 0.23) / k) + 0.6 * Math.sin((y * 0.57) / k + 1));
    g.drawImage(snap, 0, Math.max(0, (bankY - y - band) * dpr), snap.width, band * dpr, dx, bankY + y, w, band);
  }
  g.globalAlpha = 1;
  const glint = g.createRadialGradient(sx, bankY + h * 0.06, 0, sx, bankY + h * 0.06, w * 0.25);
  glint.addColorStop(0, "rgba(255,252,240,0.45)");
  glint.addColorStop(1, "rgba(255,252,240,0)");
  g.save();
  g.translate(sx, bankY + h * 0.06);
  g.scale(1, 0.25);
  g.translate(-sx, -(bankY + h * 0.06));
  g.fillStyle = glint;
  g.fillRect(sx - w * 0.25, bankY + h * 0.06 - w * 0.25, w * 0.5, w * 0.5);
  g.restore();
  // The moon bridge: its reflection closes the circle.
  const wy = (s.wy = Math.round((bankY + h * (narrow ? 0.04 : 0.05)) / cell) * cell);
  const bx = Math.round(bcx / cell - bn / 2) * cell;
  s.bridge = bake(s, bridge(s, R, bn, lx));
  s.bridge.x = bx;
  s.bridge.y = wy;
  const bb = s.bridge;
  for (let y = 0; y < bb.h; y += band) {
    g.globalAlpha = 0.42 * (1 - y / bb.h) ** 0.8;
    const dx = 1.5 * k * Math.sin((y * 0.35) / k);
    const sy = bb.h - y - band;
    if (sy < 0) break;
    g.drawImage(bb.c, 0, sy * dpr, bb.c.width, band * dpr, bx + bb.ox + dx, wy + y, bb.w, band);
  }
  g.globalAlpha = 1;
  // Stepping stones from the near bank out into the pond.
  const nSt = narrow ? 3 : 5;
  s.stones = [];
  for (let i = 0; i < nSt; i += 1) {
    const q = i / (nSt - 1);
    const x = ux((narrow ? 0.3 : 0.27) + q * (narrow ? 0.16 : 0.24));
    const y = nearAt(x) - cell * 0.6 + q * (bankY + (wy - bankY) * 1.3 - nearAt(x) + cell * 0.6);
    const n = Math.round(5 - q * 2);
    const c0 = Math.round(x / cell - n / 2);
    const r0 = Math.round(y / cell);
    for (let j = 0; j < n; j += 1) {
      const lit = (j - n / 2) * lx > 0;
      const f = STONE[lit ? 3 : 2];
      put(g, s, tile(s, j === 0 ? "(" : j === n - 1 ? ")" : R.pick(["_", "-", "_"]), f, shade(f, 0.72)), (c0 + j) * cell, r0 * cell);
      if (j > 0 && j < n - 1) put(g, s, tile(s, R.pick(["~", "-"]), STONE[0], STONE[2]), (c0 + j) * cell, (r0 + 1) * cell, 0.8);
    }
    s.stones.push({ x: c0 * cell, y: (r0 + 1) * cell, n });
  }
  g.globalAlpha = 1;
  // The near bank: grass, moss and fallen petals.
  const nb = new Path2D();
  nb.moveTo(-4, h + 4);
  for (let x = -4; x <= w + 4; x += 4 * k) nb.lineTo(x, nearAt(x));
  nb.lineTo(w + 4, h + 4);
  nb.closePath();
  const ng = g.createLinearGradient(0, h * 0.8, 0, h);
  ng.addColorStop(0, "#b4d2aa");
  ng.addColorStop(1, "#98bc94");
  g.fillStyle = ng;
  g.fill(nb);
  for (let x = 0; x < w; x += cell) {
    const top = nearAt(x);
    if (noise(x * 0.05, 4) > 0.55) put(g, s, tile(s, R.pick(["(", "o", "_", ")"]), STONE[R() < 0.5 ? 1 : 2], STONE[0]), x, top - cell * 0.5, 0.85);
    else if (R() < 0.5) put(g, s, tile(s, R.pick(['"', ",", "v"]), null, "#6f9e70"), x, top - cell * 0.6, 0.85);
    for (let y = top + cell * 0.6; y < h; y += cell) {
      const r = R();
      if (r < 0.14) put(g, s, tile(s, R.pick(['"', ",", "'", "v"]), null, R.pick(["#6c9a6e", "#7aaa78", "#5e8c64"])), x + R.range(-3, 3) * k, y, 0.8);
      else if (r < 0.3) put(g, s, tile(s, R.pick([".", "'", "*", ","]), null, R.pick(["#ec98b4", "#f4b4c8", "#e4809e"])), x + R.range(-3, 3) * k, y, 0.85);
    }
  }
  g.globalAlpha = 1;
  dotGrid(g, s, "#9aa8c8", 0.16);
  s.back = back;

  // The old tree, in near tiles: trunk, then three boughs that rock.
  use(s, A.near);
  const nc = A.near.cell;
  const T = Math.min(w * (narrow ? 0.16 : 0.11), 0.075 * w + 0.045 * h);
  const Rc = Math.max(nc * 2.3, Math.min(w, h * 1.4) * 0.05);
  const P = (u, v, th) => ({ x: ux(u), y: h * v, th: Math.max(nc * 1.1, th) });
  const tm = raster(s, limbPath([P(0.035, 1.08, T * 1.1), P(0.07, 0.88, T), P(0.055, 0.72, T * 0.9), P(0.1, 0.57, T * 0.78), P(0.12, 0.5, T * 0.7)], nc * 0.4, T * 0.12, R() * 100));
  raster(s, limbPath([P(0.06, 0.97, T * 0.4), P(0.13, 1.0, T * 0.2), P(0.2, 1.04, T * 0.1)], nc * 0.4, nc, R() * 100), tm);
  const trunk = barkCells(s, tm, lx, (y) => y > nearAt(ux(0.1)) - nc * 3);
  s.trunk = bake(s, trunk);
  // Shrubs and reeds in the far corner of the near bank.
  const fore = [];
  const sh = (x, y, rc, ramp) => {
    const blobs = Array.from({ length: 4 }, () => ({ x: x + R.range(-1, 1) * rc, y: y - rc * R.range(0.2, 0.6), r: rc * R.range(0.6, 0.9), z: R(), tone: 0 }));
    fore.push(...blossom(s, R, blobs, { L: L3, lace: 0.3, ramp }));
  };
  sh(ux(1.0), nearAt(ux(0.98)) + nc, nc * (narrow ? 2 : 3.2), GREEN);
  sh(ux(narrow ? 0.86 : 0.91), nearAt(ux(0.9)) + nc * 0.8, nc * (narrow ? 1.5 : 2.2), AZALEA);
  if (!narrow) sh(ux(0.84), nearAt(ux(0.84)) + nc * 0.5, nc * 1.5, GREEN);
  for (let u = narrow ? 0.62 : 0.66; u < 0.86; u += R.range(0.01, 0.025)) {
    const x = ux(u);
    const c = Math.round(x / nc);
    const r = Math.round(nearAt(x) / nc);
    const n = 1 + Math.floor(R() * 3);
    for (let j = 0; j < n; j += 1) fore.push({ dc: c, dr: r - j - 1, id: tile(s, j === n - 1 ? R.pick(["'", "/", "\\", "v"]) : "|", null, R.pick(["#6c9a6a", "#7eac76", "#5a8a5e"])) });
  }
  s.fore = bake(s, fore);
  s.flutterIds = ["*", "'", ".", "o"].map((gl, i) => tile(s, gl, null, ["#ec8fae", "#f6bfd0", "#e07a9c", "#f2a6bf"][i]));
  const reachA = narrow ? 0.86 : 0.73;
  const boughs = [
    { pts: [P(0.12, 0.5, T * 0.55), P(0.14, 0.33, T * 0.42), P(0.2, 0.15, T * 0.3), P(0.26, -0.06, T * 0.22)], from: 0.4, piv: P(0.12, 0.5, 0), amp: 0.6 },
    { pts: [P(0.12, 0.5, T * 0.62), P(0.19, 0.36, T * 0.48), P(0.33, 0.22, T * 0.32), P(0.5, 0.15, T * 0.2), P(reachA - 0.09, 0.17, T * 0.11), P(reachA, 0.25, T * 0.05)], from: 0.25, piv: P(0.12, 0.5, 0), amp: 1 },
    { pts: [P(0.06, 0.7, T * 0.38), P(0.14, 0.63, T * 0.26), P(0.25, 0.6, T * 0.15), P(0.34, 0.65, T * 0.06)], from: 0.35, piv: P(0.07, 0.69, 0), amp: 1.3 },
  ];
  s.crowns = boughs.map((B, i) => ({ ...crown(s, R, B, Rc, L3, lx), px: B.piv.x, py: B.piv.y, amp: B.amp, lag: 0.3 * i, ph: R() * TAU }));
  s.clusters = s.crowns.flatMap((c) => c.clusters);
  // The stone lantern on the near bank.
  const lnx = ux(narrow ? 0.24 : 0.2);
  s.lantern = bake(s, lantern(s, lx));
  s.lantern.x = Math.round(lnx / nc) * nc;
  s.lantern.y = Math.round((nearAt(lnx) + nc * 0.3) / nc) * nc;
  s.bigIds = PINK.map((c) => PETAL.map((gl) => tile(s, gl, null, c)));
  use(s, A.mid);
  // Petals on the wind, on the pond, and the small life of the garden.
  const area = Math.max(0.35, (w * h) / 1.3e6);
  s.petalIds = PINK.map((c) => PETAL.map((gl) => tile(s, gl, null, c)));
  s.petals = Array.from({ length: Math.round(230 * area) }, () => ({ x: R() * w, y: R() * h, v: R.range(0.6, 1.4), fall: R.range(6, 16), amp: R.range(6, 22), f: R.range(0.4, 1.2), ph: R() * TAU, spin: R.range(1.5, 4), c: Math.floor(R() * PINK.length), big: R() < 0.16 }));
  const floatIds = ["*", "'", ",", "o", "."].map((gl, i) => tile(s, gl, null, ["#e87ea2", "#f4a8c0", "#ec90b0", "#e07098", "#f8c0d0"][i]));
  s.flow = -side;
  s.floats = [];
  for (let i = 0; i < Math.round(9 * Math.max(0.5, w / 1440)); i += 1) {
    const n = 3 + Math.floor(R() * 6);
    s.floats.push({ x0: R() * w, f: R.range(0.15, 0.9), v: R.range(3, 7) * k, ph: R() * TAU, m: Array.from({ length: n }, () => ({ dx: R.range(-1.4, 1.4) * cell * Math.sqrt(n / 3), df: R.range(-0.04, 0.04), id: R.pick(floatIds) })) });
  }
  for (let i = 0; i < Math.round(36 * Math.max(0.5, w / 1440)); i += 1) s.floats.push({ x0: R() * w, f: R(), v: R.range(3, 8) * k, ph: R() * TAU, m: [{ dx: 0, df: 0, id: R.pick(floatIds) }] });
  s.swellIds = ["~", "-", "~"].map((gl, i) => tile(s, gl, null, ["#f4f8fc", "#dce8f4", "#ffffff"][i]));
  s.swells = Array.from({ length: Math.round(30 * Math.max(0.5, w / 1440)) }, () => ({ x: R() * w, f: R(), len: Math.round(R.range(2, 5)), v: R.range(2, 5) * k, ph: R() * TAU }));
  s.ringIds = ["~", "-"].map((gl) => tile(s, gl, null, "#f6faff"));
  s.lapIds = ["~", "-"].map((gl) => tile(s, gl, null, "#eef4fa"));
  s.beeIds = [tile(s, "8", "#f2c040", "#3a2a10"), tile(s, "'", null, "#8a96aa"), tile(s, ".", null, "#8a96aa")];
  s.bees = Array.from({ length: 2 }, () => ({ c: Math.floor(R() * s.clusters.length), ph: R() * TAU, sp: R.range(0.6, 1) }));
  s.birdIds = { head: tile(s, "o", "#90aa4a", "#ffffff"), body: [tile(s, "<", "#7a9440", "#d8e890"), tile(s, ">", "#7a9440", "#d8e890")], tail: tile(s, "-", null, "#5f7a30"), wing: [tile(s, "v", null, "#6c8a34"), tile(s, "^", null, "#6c8a34")] };
  const perch = s.crowns[1].clusters.filter((c) => uOf(c.x) > 0.25 && uOf(c.x) < 0.75 && c.y > h * 0.08);
  s.perches = (perch.length ? perch : s.clusters).map((c) => ({ x: c.x, y: c.y - c.r * 0.7 }));
  s.birdSeed = Math.floor(R() * 1000);
}

// Blossom (or foliage) in tiles: overlapping domes, each lit from the sun
// side, shadowed where a nearer dome overlaps it, lacy at the outer edge.
function blossom(s, R, blobs, o) {
  const { cell } = s;
  const ramp = o.ramp || RAMP;
  const hz = (c) => (o.haze ? mixc(c, HAZE, o.haze) : c);
  let c0 = Infinity;
  let c1 = -Infinity;
  let r0 = Infinity;
  let r1 = -Infinity;
  for (const b of blobs) {
    c0 = Math.min(c0, Math.floor((b.x - b.r * 1.2) / cell));
    c1 = Math.max(c1, Math.ceil((b.x + b.r * 1.2) / cell));
    r0 = Math.min(r0, Math.floor((b.y - b.r * 1.2) / cell));
    r1 = Math.max(r1, Math.ceil((b.y + b.r * 1.2) / cell));
  }
  const W = c1 - c0 + 1;
  const H = r1 - r0 + 1;
  const own = new Int32Array(W * H).fill(-1);
  for (let r = 0; r < H; r += 1) {
    for (let c = 0; c < W; c += 1) {
      const x = (c0 + c + 0.5) * cell;
      const y = (r0 + r + 0.5) * cell;
      let best = -1;
      for (let i = 0; i < blobs.length; i += 1) {
        const b = blobs[i];
        if (best >= 0 && b.z < blobs[best].z) continue;
        const d = Math.hypot(x - b.x, y - b.y) / b.r + (noise((c0 + c) * 0.7 + i * 3.1, (r0 + r) * 0.7) - 0.5) * 0.45;
        if (d < 1) best = i;
      }
      own[r * W + c] = best;
    }
  }
  const at = (c, r) => (c < 0 || r < 0 || c >= W || r >= H ? -1 : own[r * W + c]);
  const out = [];
  const [Lx, Ly, Lz] = o.L;
  for (let r = 0; r < H; r += 1) {
    for (let c = 0; c < W; c += 1) {
      const i = at(c, r);
      if (i < 0) continue;
      const b = blobs[i];
      const nx = ((c0 + c + 0.5) * cell - b.x) / b.r;
      const ny = ((r0 + r + 0.5) * cell - b.y) / b.r;
      const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
      const lam = clamp01(nx * Lx + ny * Ly + nz * Lz);
      let occ = false;
      let edge = false;
      for (const [dc, dr] of N8) {
        const j = at(c + dc, r + dr);
        if (j < 0) edge = edge || dc === 0 || dr === 0;
        else if (j !== i && blobs[j].z > b.z) occ = true;
      }
      const v = 0.26 + 0.78 * lam - (occ ? 0.24 : 0) + (hash(c0 + c, r0 + r, 5) - 0.5) * 0.08 + (b.tone || 0);
      const dc = c0 + c;
      const dr = r0 + r;
      if (edge && R() < o.lace) {
        out.push({ dc, dr, id: tile(s, R.pick(["*", "'", "o", ".", "*"]), null, hz(pick(ramp, 0.25 + v * 0.35))) });
        continue;
      }
      if (!edge && v < 0.3 && R() < 0.1) continue;
      const f = hz(pick(ramp, v));
      const gl = v > 0.72 ? R.pick(["*", "*", "@", "o"]) : v > 0.42 ? R.pick(["*", "o", "*", "%", "+"]) : R.pick(["%", "*", "o", "%"]);
      const ink = v > 0.5 ? hz(pick(ramp, v - 0.45)) : hz(shade(f, 1.25));
      out.push({ dc, dr, id: tile(s, gl, f, ink) });
    }
  }
  return out;
}

// A smooth spline through points (with thickness), then a gnarled wobble
// across it.
function limbPath(pts, step, wob, seed) {
  const S = [];
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const n = Math.max(1, Math.ceil(Math.hypot(p2.x - p1.x, p2.y - p1.y) / step));
    for (let j = 0; j < n; j += 1) {
      const t = j / n;
      const cr = (a, b, c, d) => 0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (3 * b - a - 3 * c + d) * t * t * t);
      S.push({ x: cr(p0.x, p1.x, p2.x, p3.x), y: cr(p0.y, p1.y, p2.y, p3.y), th: p1.th + (p2.th - p1.th) * t });
    }
  }
  S.push({ ...pts[pts.length - 1] });
  const angles = () => S.forEach((p, i) => {
    const a = S[Math.max(0, i - 1)];
    const b = S[Math.min(S.length - 1, i + 1)];
    p.ang = Math.atan2(b.y - a.y, b.x - a.x);
  });
  angles();
  S.forEach((p, i) => {
    const off = ((noise(i * 0.06 + seed, 3.7) - 0.5) + 0.35 * (noise(i * 0.22 + seed, 8.1) - 0.5)) * wob * Math.min(1, i / 6);
    p.x -= Math.sin(p.ang) * off;
    p.y += Math.cos(p.ang) * off;
  });
  angles();
  return S;
}

// Limbs rasterised to cells: each cell keeps the thickest limb over it, how
// far across that limb it sits, and the limb's normal.
function raster(s, S, map = new Map()) {
  const { cell } = s;
  for (const p of S) {
    const rad = Math.max(cell * 0.55, p.th / 2);
    const sn = Math.sin(p.ang);
    const cs = Math.cos(p.ang);
    for (let r = Math.floor((p.y - rad) / cell); r <= Math.floor((p.y + rad) / cell); r += 1) {
      for (let c = Math.floor((p.x - rad) / cell); c <= Math.floor((p.x + rad) / cell); c += 1) {
        const dx = (c + 0.5) * cell - p.x;
        const dy = (r + 0.5) * cell - p.y;
        const d = Math.hypot(dx, dy);
        if (d > rad) continue;
        const key = (c + 4096) * 8192 + (r + 4096);
        const prev = map.get(key);
        if (prev && prev.th >= p.th) continue;
        map.set(key, { c, r, th: p.th, ang: p.ang, q: (-dx * sn + dy * cs) / rad, nx: -sn, ny: cs, dx, edge: d > rad - cell * 0.8 });
      }
    }
  }
  return map;
}

// Cherry bark: dark and reddish, lit on the sun side, ringed with
// lenticels across the limb.
function barkCells(s, map, lx, mossy) {
  const out = [];
  const Lx = lx * 0.72;
  const Ly = -0.7;
  const has = (c, r) => map.has((c + 4096) * 8192 + (r + 4096));
  for (const b of map.values()) {
    b.edge = !has(b.c - 1, b.r) || !has(b.c + 1, b.r) || !has(b.c, b.r - 1) || !has(b.c, b.r + 1);
    if (b.edge) b.dx = has(b.c - 1, b.r) ? 1 : -1;
    const facing = b.q * (b.nx * Lx + b.ny * Ly);
    const v = clamp01(0.42 + 0.62 * facing + (hash(b.c, b.r, 21) - 0.5) * 0.14);
    let f = pick(BARK, v);
    if (v > 0.85) f = mixc(f, "#f6dccc", 0.25);
    const a = ((b.ang % Math.PI) + Math.PI) % Math.PI;
    const along = a < 0.39 || a > 2.75 ? "-" : a < 1.18 ? "\\" : a < 1.96 ? "|" : "/";
    const across = along === "-" ? "|" : along === "|" ? "=" : along === "\\" ? "/" : "\\";
    const tex = hash(b.c, b.r, 7);
    let gl;
    if (b.edge) gl = along === "|" ? (b.dx < 0 ? "(" : ")") : along === "-" ? (b.q * b.ny < 0 ? "_" : "-") : along;
    else gl = tex < 0.3 ? across : tex < 0.38 ? ":" : tex < 0.4 ? "o" : " ";
    let ink = v > 0.55 ? shade(f, 0.62) : shade(f, 1.55);
    if (mossy(b.r * s.cell) && hash(b.c, b.r, 9) < 0.08 + 0.25 * v) {
      f = mixc(pick(GREEN, 0.3 + v * 0.5), f, 0.25);
      gl = hash(b.c, b.r, 4) < 0.5 ? '"' : ",";
      ink = shade(f, 1.4);
    }
    out.push({ dc: b.c, dr: b.r, id: tile(s, gl, f, ink) });
  }
  return out;
}

// A bough: bark, twigs and heavy clusters of blossom along it, baked to a
// sprite that rocks about its base.
function crown(s, R, B, Rc, L3, lx) {
  const { cell } = s;
  const S = limbPath(B.pts, cell * 0.4, B.pts[0].th * 0.25, R() * 100);
  const map = raster(s, S);
  const blobs = [];
  const clusters = [];
  const add = (x, y, rc, nb) => {
    clusters.push({ x, y, r: rc });
    for (let i = 0; i < nb; i += 1) blobs.push({ x: x + R.range(-1, 1) * rc * 0.7, y: y + R.range(-0.7, 0.6) * rc * 0.55, r: rc * R.range(0.45, 0.8), z: R(), tone: R.range(-0.05, 0.05) });
  };
  let len = 0;
  for (let i = 1; i < S.length; i += 1) len += Math.hypot(S[i].x - S[i - 1].x, S[i].y - S[i - 1].y);
  let arc = 0;
  let next = len * B.from;
  for (let i = 1; i < S.length; i += 1) {
    const p = S[i];
    arc += Math.hypot(p.x - S[i - 1].x, p.y - S[i - 1].y);
    if (arc < next) continue;
    next = arc + Rc * R.range(0.9, 1.5);
    const q = arc / len;
    const nx = -Math.sin(p.ang);
    const ny = Math.cos(p.ang);
    const up = ny > 0 ? -1 : 1;
    const off = R.range(-0.4, 0.6) * Rc * up;
    add(p.x + nx * off, p.y + ny * off - Rc * 0.2, Rc * R.range(0.7, 1.05) * (0.8 + 0.4 * q), 4 + Math.floor(R() * 4));
    // A twig out of the bough, with its own cluster.
    if (R() < 0.55) {
      const sgn = R() < 0.65 ? up : -up;
      const ta = p.ang + sgn * R.range(0.6, 1.2) * (Math.cos(p.ang) < 0 ? -1 : 1);
      const tl = Rc * R.range(1, 1.9);
      const ex = p.x + Math.cos(ta) * tl;
      const ey = p.y + Math.sin(ta) * tl;
      raster(s, limbPath([{ x: p.x, y: p.y, th: cell * 1.1 }, { x: (p.x + ex) / 2, y: (p.y + ey) / 2 - cell * 0.3, th: cell * 0.9 }, { x: ex, y: ey, th: cell * 0.8 }], cell * 0.4, cell * 0.5, R() * 100), map);
      add(ex, ey, Rc * R.range(0.6, 0.9), 3 + Math.floor(R() * 3));
    }
  }
  const tip = S[S.length - 1];
  add(tip.x, tip.y, Rc * 1.2, 8);
  const flowers = blossom(s, R, blobs, { L: L3, lace: 0.35 });
  const lace = flowers.filter(() => R() < 0.05).map((c) => ({ x: c.dc * cell, y: c.dr * cell, id: R.pick(s.flutterIds), ph: R() * TAU }));
  return { sp: bake(s, [...barkCells(s, map, lx, () => false), ...flowers]), clusters, lace };
}

// A three-tiered pagoda: stone base, vermilion and white walls, bracket
// shadow under each eave, slate roofs with upturned tips, a bronze finial.
function pagoda(s, lx, haze) {
  const out = [];
  const hz = (c) => mixc(c, HAZE, haze);
  const lit = (dc) => (dc * lx > 0 ? 2 : dc === 0 ? 1 : 0);
  const roof = ["#4e5278", "#62668e", "#7c82aa"];
  const putc = (dc, r, gl, f, ink) => out.push({ dc, dr: -r, id: tile(s, gl, f ? hz(f) : null, hz(ink)) });
  let r = 0;
  for (let dc = -5; dc <= 5; dc += 1) putc(dc, r, dc === -5 ? "[" : dc === 5 ? "]" : "=", STONE[lit(dc) + 1], STONE[0]);
  r += 1;
  [6, 5, 4].forEach((a, ti) => {
    const half = a - 2;
    for (let k2 = 0; k2 < (ti === 0 ? 2 : 1); k2 += 1, r += 1) {
      for (let dc = -half; dc <= half; dc += 1) {
        const L = lit(dc);
        if (Math.abs(dc) === half) putc(dc, r, "|", VERM[L], shade(VERM[L], 0.6));
        else if (dc === 0 || (ti === 0 && k2 === 0 && Math.abs(dc) === 2)) putc(dc, r, "#", "#6a4a50", "#a88078");
        else putc(dc, r, ti === 0 && k2 === 1 ? "+" : ":", L === 2 ? "#f6f0ea" : "#dcd2d0", "#b8a0a0");
      }
    }
    for (let dc = -(a - 2); dc <= a - 2; dc += 1) putc(dc, r, Math.abs(dc) % 2 ? ":" : "'", "#584450", "#8a7080");
    r += 1;
    for (let dc = -a; dc <= a; dc += 1) putc(dc, r, Math.abs(dc) === a ? "_" : "=", shade(roof[lit(dc)], 0.85), shade(roof[lit(dc)], 1.5));
    r += 1;
    for (let dc = -(a - 2); dc <= a - 2; dc += 1) putc(dc, r, dc === -(a - 2) ? "/" : dc === a - 2 ? "\\" : "=", roof[lit(dc)], shade(roof[lit(dc)], 1.4));
    putc(-a, r, "\\", null, roof[0]);
    putc(a, r, "/", null, roof[0]);
    r += 1;
  });
  putc(-1, r, "/", roof[lit(-1)], shade(roof[lit(-1)], 1.4));
  putc(0, r, "^", roof[1], shade(roof[1], 1.4));
  putc(1, r, "\\", roof[lit(1)], shade(roof[lit(1)], 1.4));
  r += 1;
  ["=", "=", "+", "=", "|", "*"].forEach((gl) => {
    putc(0, r, gl, null, gl === "*" ? "#c8a050" : "#8a7458");
    r += 1;
  });
  return out;
}

// A vermilion moon bridge: deck, posts and rail on a steep arch, stone
// footings at the water.
function bridge(s, R, n, lx) {
  const out = [];
  const hd = Math.round(n * 0.3);
  const rd = (i) => -1 - Math.round(hd * Math.sin((Math.PI * (Math.min(n - 1, Math.max(0, i)) + 0.5)) / n));
  for (let i = 0; i < n; i += 1) {
    const r = rd(i);
    const lt = (i - n / 2) * lx > 0 ? 2 : 1;
    const a = rd(i - 1);
    const b = rd(i + 1);
    const slope = b < a ? "/" : b > a ? "\\" : "=";
    out.push({ dc: i, dr: r, id: tile(s, slope, VERM[lt], shade(VERM[lt], 1.35)) });
    out.push({ dc: i, dr: r + 1, id: tile(s, slope === "=" ? "_" : slope, VERM[0], VERM[2]) });
    const post = i % 3 === 0 || i === n - 1;
    if (post) out.push({ dc: i, dr: r - 1, id: tile(s, "|", VERM[lt], shade(VERM[lt], 0.65)) });
    out.push({ dc: i, dr: r - 2, id: tile(s, "-", VERM[lt + 0], shade(VERM[lt], 1.4)) });
    if (i === 0 || i === n - 1) {
      out.push({ dc: i, dr: r - 3, id: tile(s, "o", null, "#8a6a40") });
      for (let j = r + 2; j < 0; j += 1) out.push({ dc: i, dr: j, id: tile(s, j === -1 ? "#" : "|", STONE[1], STONE[0]) });
    }
  }
  return out;
}

// A stone lantern, moss on its cap.
function lantern(s, lx) {
  const ART = ["   o   ", "  /%\\  ", " /%%%\\ ", "<=====>", "  [#]  ", " [===] ", "  |:|  ", "  |:|  ", " [===] ", "[=====]"];
  const out = [];
  ART.forEach((line, r) => Array.from(line).forEach((ch, i) => {
    if (ch === " ") return;
    const lt = (i - 3) * lx > 0 ? 3 : i === 3 ? 2 : 1;
    let f = STONE[lt];
    let ink = shade(f, 0.62);
    if (ch === "%") {
      f = pick(GREEN, 0.35 + lt * 0.12);
      ink = shade(f, 1.45);
    } else if (ch === "#") {
      f = "#4a4652";
      ink = "#f0c890";
    }
    out.push({ dc: i - 3, dr: r - ART.length, id: ch === "o" ? tile(s, "o", null, STONE[1]) : tile(s, ch, f, ink) });
  }));
  return out;
}

function bake(s, cells) {
  const { cell, dpr } = s;
  const c0 = Math.min(...cells.map((c) => c.dc));
  const r0 = Math.min(...cells.map((c) => c.dr));
  const nc = Math.max(...cells.map((c) => c.dc)) - c0 + 1;
  const nr = Math.max(...cells.map((c) => c.dr)) - r0 + 1;
  const c = document.createElement("canvas");
  c.width = nc * s.P;
  c.height = nr * s.P;
  const g = c.getContext("2d");
  g.scale(dpr, dpr);
  for (const cl of cells) put(g, s, cl.id, (cl.dc - c0) * cell, (cl.dr - r0) * cell);
  g.globalAlpha = 1;
  return { c, ox: c0 * cell, oy: r0 * cell, w: nc * cell, h: nr * cell, x: 0, y: 0 };
}

function wind(s, t) {
  const L = s.wint.length - 1;
  const tt = ((t % LOOP) + LOOP) % LOOP;
  const i = tt / s.wdt;
  const i0 = Math.min(L - 1, Math.floor(i));
  return Math.floor(t / LOOP) * s.wint[L] + s.wint[i0] + (s.wint[i0 + 1] - s.wint[i0]) * (i - i0);
}

const draw1 = (ctx, sp) => ctx.drawImage(sp.c, sp.x + sp.ox, sp.y + sp.oy, sp.w, sp.h);

export const sakura = {
  fps: 12,
  wallpaper: true,
  scheme: "light",
  previewK: 0.38,
  setup(s) {
    s.seed = newSeed();
  },
  resize(s) {
    build(s);
  },
  draw(s, t) {
    if (s.fontStale) build(s);
    const { ctx, w, h, k, dpr, A } = s;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    const W = wind(s, t);
    use(s, A.mid);
    pond(ctx, s, t);
    floats(ctx, s, t, false);
    ctx.globalAlpha = 1;
    draw1(ctx, s.bridge);
    floats(ctx, s, t, true);
    ctx.globalAlpha = 1;
    draw1(ctx, s.lantern);
    draw1(ctx, s.trunk);
    draw1(ctx, s.fore);
    petals(ctx, s, t, W, false);
    // The boughs rock in the wind, each a beat behind the last.
    use(s, A.near);
    for (const cr of s.crowns) {
      const a = cr.amp * s.side * (0.0045 * (windAt(t - cr.lag) - 1.2) + 0.003 * Math.sin(t * 0.9 + cr.ph));
      ctx.setTransform(dpr, 0, 0, dpr, cr.px * dpr, cr.py * dpr);
      ctx.rotate(a);
      ctx.drawImage(cr.sp.c, cr.sp.ox - cr.px, cr.sp.oy - cr.py, cr.sp.w, cr.sp.h);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const cs = Math.cos(a);
      const sn = Math.sin(a);
      const gust = windAt(t);
      for (const l of cr.lace) {
        const dx = l.x - cr.px;
        const dy = l.y - cr.py;
        const fl = (1 + gust) * k * Math.sin(t * 2.6 + l.ph);
        put(ctx, s, l.id, cr.px + dx * cs - dy * sn + fl * 1.5, cr.py + dx * sn + dy * cs + Math.abs(fl) * 0.5);
      }
    }
    ctx.globalAlpha = 1;
    use(s, A.mid);
    bees(ctx, s, t);
    bird(ctx, s, t);
    petals(ctx, s, t, W, true);
    use(s, A.mid);
    ctx.globalAlpha = 1;
  },
};

// Swells drifting on the pond, rings where petals land, water lapping the
// stepping stones.
function pond(ctx, s, t) {
  const { w, cell, k, bankY } = s;
  for (const sw of s.swells) {
    const span = w + sw.len * cell;
    const x = ((((sw.x + s.flow * sw.v * t) % span) + span) % span) - sw.len * cell;
    const y = bankY + cell * 0.6 + sw.f * (s.nearAt(x) - bankY - cell * 1.6);
    const a = (0.25 + 0.3 * Math.sin(t * 0.7 + sw.ph)) * clamp01((s.uOf(x) - 0.1) / 0.08);
    for (let i = 0; i < sw.len; i += 1) put(ctx, s, s.swellIds[(i + Math.floor(sw.ph * 3)) % 3], x + i * cell, y, a * Math.sin((Math.PI * (i + 0.5)) / sw.len));
  }
  const P = 4.7;
  for (let j = 0; j < 2; j += 1) {
    const n = Math.floor(t / P) - j;
    const life = (t - n * P) / (P * 2);
    if (life < 0 || life > 1) continue;
    const cx = s.ux(0.2 + 0.75 * hash(n, 1, 77));
    const cy = bankY + (s.nearAt(cx) - bankY) * (0.25 + 0.55 * hash(n, 2, 77));
    const rx = cell * (0.8 + life * 5);
    const m = Math.round(8 + (rx / cell) * 2);
    for (let i = 0; i < m; i += 1) {
      const a = (TAU * i) / m;
      put(ctx, s, s.ringIds[i % 2], cx + Math.cos(a) * rx - cell / 2, cy + Math.sin(a) * rx * 0.28 - cell / 2, 0.75 * (1 - life));
    }
  }
  for (const st of s.stones) {
    const lap = Math.sin(t * 1.3 + st.x);
    put(ctx, s, s.lapIds[0], st.x - cell + lap * k, st.y, 0.5);
    put(ctx, s, s.lapIds[1], st.x + st.n * cell - lap * k, st.y, 0.5);
  }
  ctx.globalAlpha = 1;
}

// Fallen petals afloat, singly and in rafts, drifting with the current.
function floats(ctx, s, t, front) {
  const { w, cell, k, bankY } = s;
  const span = w + cell * 8;
  for (const f of s.floats) {
    const xb = ((((f.x0 + s.flow * f.v * t) % span) + span) % span) - cell * 4;
    for (const m of f.m) {
      const x = xb + m.dx;
      const y = bankY + cell * 0.8 + clamp01(f.f + m.df) * (s.nearAt(x) - bankY - cell * 1.8) + Math.sin(t * 1.1 + f.ph + m.dx) * 1.2 * k;
      if (y > s.wy !== front) continue;
      put(ctx, s, m.id, x, y, clamp01((s.uOf(x) - 0.1) / 0.08) * (0.65 + 0.35 * f.f));
    }
  }
}

function petals(ctx, s, t, W, big) {
  const { w, h, k } = s;
  use(s, big ? s.A.near : s.A.mid);
  const cell = s.cell;
  const dir = -s.side;
  const sw = big ? 1.5 : 1;
  const span = w + cell * 4;
  const vspan = h + cell * 4;
  for (const p of s.petals) {
    if (p.big !== big) continue;
    const x = ((((p.x + dir * p.v * W * 34 * k * sw) % span) + span) % span) - cell * 2;
    const y = ((((p.y + p.fall * t * k * sw + p.amp * k * Math.sin(t * p.f + p.ph)) % vspan) + vspan) % vspan) - cell * 2;
    // Petals travel in drifts: thick where a gust carries them, thin between.
    const drift = clamp01((noise((x / w) * 2.6 - dir * W * 0.035, (y / h) * 1.8 + 7) - 0.25) * 3);
    const ids = big ? s.bigIds[p.c] : s.petalIds[p.c];
    put(ctx, s, ids[Math.floor(t * p.spin + W * 0.5 + p.ph) % PETAL.length], x, y, (big ? 0.85 : 0.95) * (0.35 + 0.65 * drift));
  }
  ctx.globalAlpha = 1;
}

function bees(ctx, s, t) {
  const { cell } = s;
  for (const b of s.bees) {
    const c = s.clusters[b.c];
    const x = c.x + Math.sin(t * b.sp * 1.7 + b.ph) * c.r * 1.1 + Math.sin(t * 5.3 + b.ph) * cell * 0.3;
    const y = c.y + Math.sin(t * b.sp * 2.3 + b.ph * 2) * c.r * 0.7;
    put(ctx, s, s.beeIds[0], x, y);
    put(ctx, s, s.beeIds[Math.floor(t * 12) % 2 ? 1 : 2], x, y - cell * 0.6, 0.8);
  }
  ctx.globalAlpha = 1;
}

// A white-eye drops in now and then, hops about the blossom, and leaves.
function bird(ctx, s, t) {
  const P = 47;
  const n = Math.floor(t / P);
  const p = t - n * P - 6;
  if (p < 0 || p > 20) return;
  const { w, h, cell } = s;
  const B = s.birdIds;
  const perch = s.perches[Math.floor(hash(n, 3, s.birdSeed) * s.perches.length)];
  const ox = s.ux(1.05);
  if (p < 2.5 || p > 17) {
    const q = p < 2.5 ? 1 - p / 2.5 : (p - 17) / 3;
    const e = q * q;
    const x = perch.x + (ox - perch.x) * e;
    const y = perch.y + (h * 0.12 - perch.y) * e - Math.sin(Math.PI * q) * h * 0.05;
    put(ctx, s, B.head, x, y);
    put(ctx, s, B.wing[Math.floor(t * 10) % 2], x - s.side * cell * 0.5, y - cell * 0.5);
  } else {
    const hop = Math.floor((p - 2.5) / 2.1);
    const hp = ((p - 2.5) % 2.1) / 2.1;
    const hx = (hash(n, hop, 9) - 0.5) * cell * 3;
    const face = hash(n, hop, 8) < 0.5 ? 1 : -1;
    const y = perch.y - (hp < 0.2 ? Math.sin((hp / 0.2) * Math.PI) * cell * 0.6 : 0);
    put(ctx, s, B.tail, perch.x + hx - face * cell * 0.9, y);
    put(ctx, s, B.body[face > 0 ? 1 : 0], perch.x + hx, y);
    put(ctx, s, B.head, perch.x + hx + face * cell * 0.85, y - cell * 0.35);
  }
  ctx.globalAlpha = 1;
}

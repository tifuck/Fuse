// Scene: Waterfall Gorge. Deep in a gorge on a bright morning: a tall
// cascade plunges off the rim of the dark headwall into a churning pool,
// while the gorge bends away into light on the left. The near cliffs are
// stacked ASCII rock blocks, warm where the sun reaches and cool teal in the
// shade, with moss and ferns on the ledges; the headwall is wet dark rock
// traced with glyph strata. Water streams down in falling glyphs, foam boils
// at the foot, spray and mist rise and drift with a rainbow in them, rings
// spread across the pool, ferns and vines sway in the draught off the falls,
// and now and then swallows swoop over the water.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const ROCK = "#3e5052";
const WARM = "#c8a878";
const MOSS = ["#4f8a3c", "#3e7432", "#6aa44a", "#2f5f2c"];
const WATER = ["#f6feff", "#d4f1f6", "#a6dce6", "#78bccb"];
const FALL = ["|", "!", ":", "'", "|", "!", "."];

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  const base = Math.max(4, 13 * k);
  tiles(s, base);
  const { w, h, cell } = s;
  const C = Math.ceil(w / cell) + 1;
  const lipY = (s.lipY = Math.round((h * 0.12) / cell) * cell);
  const poolY = (s.poolY = Math.round((h * 0.78) / cell) * cell);
  // The falls: a broad main fall and a thin strand beside it.
  const fw = Math.max(5, Math.round((w * 0.085) / cell));
  const fc = Math.round((w * R.range(0.7, 0.73)) / cell);
  s.falls = [
    { c0: fc - Math.floor(fw / 2), n: fw, y0: lipY, sp: 12, dens: 0 },
    { c0: fc + Math.ceil(fw / 2) + 3, n: 2, y0: lipY + h * 0.09, sp: 9, dens: 0.12 },
  ];
  const fx = (s.fx = fc * cell + cell / 2);
  const fwPx = fw * cell;
  const slot = w * 0.42;
  const rimOff = R() * 100;
  const rim = (x) => lipY - clamp01((Math.abs(x - fx) - fwPx * 0.6) / (fwPx * 1.5)) * h * (0.06 * noise(x * 0.006 / k + rimOff, 4.2) + 0.04 * clamp01((x - fx) / (w * 0.2))) + h * 0.04 * clamp01((slot + w * 0.1 - x) / (w * 0.1));
  const slotEdge = (y) => slot - w * 0.015 * Math.sin(y * 0.01 / k) - w * 0.025 * (1 - y / poolY);

  const [back, g] = layer(s);
  // Sky over the rim, sun to the upper left.
  const sky = g.createLinearGradient(0, 0, 0, h * 0.7);
  sky.addColorStop(0, "#f6e9c8");
  sky.addColorStop(0.5, "#d6e6da");
  sky.addColorStop(1, "#9ec0bc");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  const sun = g.createRadialGradient(w * 0.3, -h * 0.05, 0, w * 0.3, -h * 0.05, w * 0.6);
  sun.addColorStop(0, "rgba(255,244,210,0.95)");
  sun.addColorStop(1, "rgba(255,244,210,0)");
  g.fillStyle = sun;
  g.fillRect(0, 0, w, h);
  // Where the gorge bends away: far walls stepping back into the haze.
  const FAR = [
    { x: 0.2, col: "#b4ccc4", top: 0.42 },
    { x: 0.27, col: "#97b6ae", top: 0.32 },
    { x: 0.33, col: "#7a9e98", top: 0.22 },
    { x: 0.38, col: "#5e837e", top: 0.12 },
  ];
  FAR.forEach((F, i) => {
    const off = R() * 50;
    const top = (x) => h * F.top + h * 0.05 * noise(x * 0.01 / k + off, i) + h * 0.015 * noise(x * 0.06 / k + off, i + 2);
    const edge = (y) => w * F.x - w * 0.025 * ((y - h * F.top) / (poolY - h * F.top)) + w * 0.01 * (noise(y * 0.02 / k + off, i + 5) - 0.5);
    const p = new Path2D();
    p.moveTo(w, top(w));
    for (let x = w; x >= edge(h * F.top); x -= 6 * k) p.lineTo(x, top(x));
    for (let y = h * F.top; y <= poolY; y += 6 * k) p.lineTo(edge(y), y);
    p.lineTo(w, poolY);
    p.closePath();
    const fg = g.createLinearGradient(0, h * F.top, 0, poolY);
    fg.addColorStop(0, mixc(F.col, "#f0e6c8", 0.2));
    fg.addColorStop(1, mixc(F.col, "#d6e6da", 0.55));
    g.fillStyle = fg;
    g.fill(p);
    // Soft vertical fluting on the far rock.
    g.save();
    g.clip(p);
    for (let x = edge(poolY); x < slot + w * 0.05; x += R.range(8, 30) * k) {
      g.globalAlpha = R.range(0.03, 0.08);
      g.fillStyle = R() < 0.5 ? "#ffffff" : "#203030";
      g.fillRect(x, top(x) + R() * h * 0.1, R.range(1, 4) * k, h);
    }
    g.restore();
    g.globalAlpha = 1;
    if (i === 1) {
      const tx = edge(h * 0.45) + w * 0.012;
      const tg = g.createLinearGradient(0, top(tx), 0, poolY);
      tg.addColorStop(0, "rgba(248,252,252,0.85)");
      tg.addColorStop(1, "rgba(248,252,252,0.15)");
      g.fillStyle = tg;
      g.fillRect(tx, top(tx), Math.max(1, 2.5 * k), poolY - top(tx));
    }
  });
  // The headwall: wet dark rock behind the falls.
  const hw = new Path2D();
  hw.moveTo(slotEdge(poolY), poolY);
  for (let y = poolY; y >= rim(slot); y -= 6 * k) hw.lineTo(slotEdge(y), y);
  for (let x = slotEdge(rim(slot)); x <= w + 4; x += 4 * k) hw.lineTo(x, rim(x));
  hw.lineTo(w + 4, poolY);
  hw.closePath();
  const hg = g.createLinearGradient(0, lipY - h * 0.05, 0, poolY);
  hg.addColorStop(0, "#6a6450");
  hg.addColorStop(0.2, "#3c4642");
  hg.addColorStop(0.55, "#1e2a2a");
  hg.addColorStop(1, "#121c1c");
  g.fillStyle = hg;
  g.fill(hw);
  g.save();
  g.clip(hw);
  const wet = g.createRadialGradient(fx, h * 0.5, 0, fx, h * 0.5, w * 0.16);
  wet.addColorStop(0, "rgba(6,14,16,0.6)");
  wet.addColorStop(1, "rgba(6,14,16,0)");
  g.fillStyle = wet;
  g.fillRect(0, 0, w, h);
  g.restore();
  s.ledges = [];
  // Headwall rock in tiles: sharp in the sun along the rim, dissolving into
  // the wet shadow below, clear of the falls; boulders at the foot.
  const headFade = (x, y) => {
    if (x < slotEdge(y) + cell * 0.5 || y < rim(x + cell / 2) || y > poolY - cell) return 0;
    const clearFalls = clamp01((Math.abs(x + cell / 2 - fx) - fwPx * 0.62) / (cell * 3));
    const upper = clamp01(1.15 - (y - lipY) / (h * 0.3) + 0.9 * (noise(x * 0.012 / k, y * 0.01 / k + 3) - 0.5));
    const foot = clamp01((y - (poolY - cell * (4 + 4 * noise(x * 0.02 / k, 9)))) / (cell * 2)) * 0.9;
    return clearFalls * Math.max(upper, foot);
  };
  voro(g, s, R, { side: "H", sx: 5, sy: 3.5, fade: headFade, tone: "#4a5856", lit: (x, y) => clamp01(1 - (y - lipY) / (h * 0.28)), moist: (x, y) => 0.2 + 0.5 * clamp01((y - lipY) / (h * 0.5)) });
  // Shrubs and grass along the rim against the sky.
  for (let x = slotEdge(rim(slot)); x < w; x += R.range(1, 4) * cell) {
    if (Math.abs(x - fx) < fwPx * 0.9) continue;
    const bw = Math.round(R.range(1, 5));
    const bh = Math.round(R.range(1, 3.4));
    for (let r = 0; r < bh; r += 1) {
      const half = Math.ceil((bw * Math.sqrt(1 - (r / bh) ** 2)) / 2);
      for (let c = -half; c <= half; c += 1) {
        const xx = Math.round((x + c * cell) / cell) * cell;
        const y = Math.floor(rim(xx) / cell) * cell - r * cell;
        const tip = r === bh - 1 || Math.abs(c) === half;
        const f = shade(R.pick(["#3a5a2e", "#4a6c34", "#2e4a2a"]), 1 + 0.1 * r);
        if (tip) put(g, s, tile(s, R.pick(["^", "*", "'", "v", "%"]), null, R.pick(["#5f8a3c", "#7aa04a", "#4a7032"])), xx, y);
        else put(g, s, tile(s, R.pick(["%", "*", "&", "#"]), f, mixc(f, "#d8dc80", 0.4)), xx, y);
      }
    }
  }
  g.globalAlpha = 1;
  // The brink, where the river tips over.
  const brink = s.falls[0];
  for (let c = brink.c0 - 1; c <= brink.c0 + brink.n; c += 1) {
    const edge = c === brink.c0 - 1 || c === brink.c0 + brink.n;
    put(g, s, tile(s, edge ? "~" : R.pick(["=", "~", "-"]), edge ? null : "#dff4f4", edge ? "#b8e0e4" : "#6aa8b4"), c * cell, lipY - cell);
  }
  g.globalAlpha = 1;
  // The pool.
  const pool = g.createLinearGradient(0, poolY, 0, h);
  pool.addColorStop(0, "#3c7472");
  pool.addColorStop(0.25, "#1a4446");
  pool.addColorStop(1, "#0a1c1e");
  g.fillStyle = pool;
  g.fillRect(0, poolY, w, h - poolY);
  // The pool holds the gorge upside down, dim and broken by ripples.
  const snap = document.createElement("canvas");
  snap.width = back.width;
  snap.height = back.height;
  snap.getContext("2d").drawImage(back, 0, 0);
  const depth = h - poolY;
  const band = Math.max(1, Math.round(2 * k * s.dpr)) / s.dpr;
  for (let y = 0; y < depth; y += band) {
    const v = y / depth;
    g.globalAlpha = 0.5 * (1 - v) ** 1.4;
    const dx = (1 + 7 * v) * k * (Math.sin(y * 0.21 / k) + 0.6 * Math.sin(y * 0.53 / k + 1));
    g.drawImage(snap, 0, Math.max(0, (poolY - y - band) * s.dpr), snap.width, band * s.dpr, dx, poolY + y, w, band);
  }
  g.globalAlpha = 1;
  g.save();
  g.translate(fx, poolY);
  g.scale(1, 2.2);
  const refl = g.createRadialGradient(0, 0, 0, 0, 0, fwPx * 1.4);
  refl.addColorStop(0, "rgba(225,248,250,0.5)");
  refl.addColorStop(1, "rgba(225,248,250,0)");
  g.fillStyle = refl;
  g.fillRect(-fwPx * 1.5, 0, fwPx * 3, fwPx * 1.5);
  g.restore();
  // The left cliff, near and in shade, its top in the sun.
  const lOff = R() * 100;
  const leftEdge = (r) => {
    const v = (r * cell) / h;
    return C * (0.17 + 0.3 * Math.max(0, 0.3 - v) + 0.2 * Math.max(0, v - 0.8)) + 4 * (noise(r * 0.18, lOff) - 0.5) + 2 * (noise(r * 0.6, lOff + 3) - 0.5);
  };
  // In shadow the rock is smooth and dark; tiles pick out the lit edge.
  cliffShape(g, s, (r) => leftEdge(r) * cell, -1, "#1e2a28", "#0c1414");
  const fadeL = (x, y) => {
    const c = x / cell;
    const r = y / cell;
    const d = leftEdge(r) - c;
    if (d <= 0) return 0;
    return Math.max(clamp01(1.3 - d / (4 + 8 * noise(r * 0.12, 4.4))), clamp01(1 - y / (h * 0.22)) * 0.9);
  };
  voro(g, s, R, { side: "L", sx: 5, sy: 4, fade: fadeL, tone: "#3a4846", lit: (x, y) => clamp01(1 - y / (h * 0.4)) * 0.8, moist: (x, y) => 0.25 + 0.45 * clamp01(y / h - 0.3) });
  // The near buttress on the right, in bigger tiles.
  tiles(s, base * 1.5);
  const bc = s.cell;
  const BC = Math.ceil(w / bc) + 1;
  const rEdge = (r) => {
    const v = (r * bc) / h;
    return BC * (0.93 - 0.03 * Math.sin(v * 5 + lOff) - 0.06 * Math.max(0, v - 0.75) * 3) + 1.5 * (noise(r * 0.3, lOff + 9) - 0.5);
  };
  cliffShape(g, s, (r) => rEdge(r) * bc, 1, "#1a2624", "#0a1212");
  const fadeR = (x, y) => {
    const d = x / bc - rEdge(y / bc) + 1;
    if (d <= 0) return 0;
    return clamp01(1.3 - d / (3 + 5 * noise(y * 0.01, 7.7)));
  };
  voro(g, s, R, { side: "R", sx: 5, sy: 3.5, fade: fadeR, tone: "#2c3a38", lit: (x, y) => clamp01(1 - y / (h * 0.5)) * 0.6, moist: (x, y) => 0.4 + 0.4 * clamp01(y / h - 0.2) });
  tiles(s, base);
  dotGrid(g, s, "#dcece6", 0.14);
  // Spray haze round the plunge.
  const sp = g.createRadialGradient(fx, poolY, 0, fx, poolY, w * 0.22);
  sp.addColorStop(0, "rgba(232,246,246,0.55)");
  sp.addColorStop(1, "rgba(232,246,246,0)");
  g.save();
  g.translate(fx, poolY);
  g.scale(1.3, 0.55);
  g.translate(-fx, -poolY);
  g.fillStyle = sp;
  g.fillRect(fx - w * 0.22, poolY - w * 0.22, w * 0.44, w * 0.44);
  g.restore();
  s.back = back;
  // The glow of the falling water, painted once.
  const [glowC, gg] = layer(s);
  for (const F of s.falls) {
    const x0 = F.c0 * cell;
    const x1 = (F.c0 + F.n) * cell;
    gg.save();
    gg.filter = `blur(${Math.max(1, 4 * k)}px)`;
    const sg = gg.createLinearGradient(x0, 0, x1, 0);
    sg.addColorStop(0, "rgba(170,220,228,0)");
    sg.addColorStop(0.25, `rgba(200,236,240,${0.3 - F.dens})`);
    sg.addColorStop(0.5, `rgba(240,252,252,${0.45 - F.dens})`);
    sg.addColorStop(0.75, `rgba(200,236,240,${0.3 - F.dens})`);
    sg.addColorStop(1, "rgba(170,220,228,0)");
    gg.fillStyle = sg;
    gg.fillRect(x0 - cell, F.y0, x1 - x0 + cell * 2, poolY - F.y0 + cell);
    gg.restore();
  }
  s.glowL = glowC;
  s.glowBox = [(s.falls[0].c0 - 2) * cell, lipY - cell, (s.falls[1].c0 + s.falls[1].n + 2 - s.falls[0].c0 + 2) * cell, poolY - lipY + cell * 2];
  // Water glyphs.
  s.waterIds = WATER.map((c) => FALL.map((gl) => tile(s, gl, null, c)));
  s.foamIds = [["#f2fcfd", "#7ab8c6"], ["#d6f0f4", "#5e9eae"], ["#b2dde6", "#4a8696"]].map(([f, i]) => FALL.map((gl) => tile(s, gl, f, i)));
  s.churnIds = ["o", "O", "°", ".", "*", "o", "'"].map((gl, i) => tile(s, gl, null, WATER[i % 3]));
  s.ringIds = ["~", "-", "~"].map((gl, i) => tile(s, gl, null, ["#b8e6ee", "#8ccad8", "#e0f6f8"][i]));
  s.boilIds = ["o", "~", "O", "°", "*", "~", "o"].map((gl) => [tile(s, gl, "#eefafb", "#7ab4c2"), tile(s, gl, null, "#e4f6f8")]);
  // Ferns on the ledges and vines from the overhangs.
  const FERNS = [["\\ | /", "\\\\|//", " \\|/"], ["  ,  ", "\\ | /", " \\|/ "], ["\\ \\ / /", " \\\\|// "], [" '  '", "\\\\ //", " \\|/"]];
  s.ferns = [];
  s.vines = [];
  for (const L of s.ledges) {
    if (L.y > poolY - cell) continue;
    if (R() < 0.55) {
      const art = R.pick(FERNS);
      const cells = [];
      const shadeK = 0.75 + 0.5 * L.lit;
      art.forEach((line, r) => Array.from(line).forEach((ch, i) => {
        if (ch === " ") return;
        cells.push({ dc: i - Math.floor(line.length / 2), r, lift: 1 - r / art.length, id: tile(s, ch, null, shade(R.pick(["#7cc25a", "#5fa84a", "#94d068", "#4f9a44"]), shadeK)) });
      }));
      s.ferns.push({ x: L.x, y: L.y - art.length * cell * 0.82, cells, ph: R() * TAU });
    } else {
      const len = Math.round(R.range(3, 9));
      const cells = Array.from({ length: len }, (_, j) => ({ r: j, id: tile(s, j === len - 1 ? R.pick(["'", ","]) : R.pick([";", "|", ":", "!"]), null, shade(R.pick(MOSS), 1.3 - j * 0.04)) }));
      s.vines.push({ x: L.x + (L.side === "L" ? -cell : L.side === "R" ? cell : 0), y: L.y + cell, cells, ph: R() * TAU });
    }
  }
  // Rocks in the pool, baked into small images.
  s.rocks = [];
  const spots = [R.range(0.08, 0.22), R.range(0.3, 0.42), R.range(0.52, 0.62), R.range(0.82, 0.9)];
  for (const u of spots.slice(0, w < 700 ? 2 : 4)) {
    const rw = Math.round(R.range(4, 9) * (w < 700 ? 0.7 : 1));
    const rh = Math.round(R.range(2, 3.6));
    const cells = [];
    for (let r = 0; r < rh; r += 1) {
      const half = (rw / 2) * (0.5 + (0.5 * (r + 1)) / rh);
      for (let c = Math.round(-half); c <= Math.round(half); c += 1) {
        const lit = r === 0 || c <= -half + 1;
        const f = shade(lit ? "#6a7c74" : "#2e3c3a", R.range(0.9, 1.1));
        const top = r === 0;
        const gl = top ? (c <= -half + 0.5 ? "/" : c >= half - 0.5 ? "\\" : R.pick(['"', "_", "-"])) : R.pick(["%", ":", "#", " ", "."]);
        cells.push({ dc: c, dr: r, id: tile(s, gl, top && R() < 0.5 ? mixc(f, MOSS[0], 0.5) : f, shade(f, lit ? 1.45 : 0.6)) });
      }
    }
    const y = poolY + (h - poolY) * R.range(0.35, 0.75);
    s.rocks.push({ x: u * w, y, w: rw * cell, sp: bake(s, cells), ph: R() * TAU });
  }
  s.rocks.sort((a, b) => a.y - b.y);
  // Mist puffs share one soft image.
  const mp = document.createElement("canvas");
  const mr = 64;
  mp.width = mp.height = mr * 2;
  const mg = mp.getContext("2d");
  const mgr = mg.createRadialGradient(mr, mr, 0, mr, mr, mr);
  mgr.addColorStop(0, "rgba(250,254,254,0.8)");
  mgr.addColorStop(0.45, "rgba(244,252,252,0.32)");
  mgr.addColorStop(1, "rgba(240,250,250,0)");
  mg.fillStyle = mgr;
  mg.fillRect(0, 0, mr * 2, mr * 2);
  s.puff = mp;
  s.puffs = Array.from({ length: 12 }, () => ({ p: R.range(9, 16), ph: R(), dx: R.range(-1.2, 0.3), sz: R.range(90, 170) * k, a: R.range(0.2, 0.4) }));
  s.churn = Array.from({ length: Math.round(80 * Math.max(0.6, k)) }, () => ({ u: R(), p: R.range(1.2, 2.6), ph: R(), hgt: R.range(1, 8), wide: R.range(0.8, 2.4) }));
  s.spray = Array.from({ length: Math.round(40 * Math.max(0.6, k)) }, () => ({ u: R(), p: R.range(3, 6), ph: R(), dx: R.range(-1, 0.6) }));
  s.sprayIds = [".", "'", "°", "."].map((gl, i) => tile(s, gl, null, WATER[i % 2]));
  // The rainbow in the spray, painted once.
  const rb = document.createElement("canvas");
  const rbr = w * 0.17;
  const rcx = fx - w * 0.04;
  const rcy = poolY + h * 0.02;
  rb.width = Math.ceil(rbr * 2.4 * s.dpr);
  rb.height = Math.ceil(rbr * 1.3 * s.dpr);
  const bg = rb.getContext("2d");
  bg.scale(s.dpr, s.dpr);
  bg.filter = `blur(${Math.max(1, 3 * k)}px)`;
  ["#ff6a5a", "#ffb04a", "#fff07a", "#7fe08a", "#6ab8ff", "#a88cff"].forEach((col, i) => {
    bg.strokeStyle = col;
    bg.globalAlpha = 0.75;
    bg.lineWidth = Math.max(1.5, 4.5 * k);
    bg.beginPath();
    bg.arc(rbr * 1.2, rbr * 1.2, rbr - i * Math.max(1.5, 4.5 * k), Math.PI * 1.05, Math.PI * 1.7);
    bg.stroke();
  });
  bg.globalCompositeOperation = "destination-in";
  bg.filter = "none";
  const fade = bg.createLinearGradient(0, rbr * 0.2, 0, rbr * 1.3);
  fade.addColorStop(0, "rgba(0,0,0,0.15)");
  fade.addColorStop(0.6, "rgba(0,0,0,1)");
  fade.addColorStop(1, "rgba(0,0,0,0.6)");
  bg.fillStyle = fade;
  bg.fillRect(0, 0, rbr * 2.4, rbr * 1.3);
  s.rainbow = { c: rb, x: rcx - rbr * 1.2, y: rcy - rbr * 1.2, w: rbr * 2.4, h: rbr * 1.3 };
  s.bird = [tile(s, "v", null, "#1a2628"), tile(s, "^", null, "#1a2628"), tile(s, "-", null, "#1a2628")];
  s.windOff = R() * 1000;
  s.birdSeed = R() * 1000;
}

// Rock in tiles: cells grouped round jittered seeds into slabs and
// boulders, each lit on its upper left with dark seams below and right.
function voro(g, s, R, o) {
  const { w, h, cell } = s;
  const C = Math.ceil(w / cell) + 1;
  const RW = Math.ceil(h / cell) + 1;
  const gx = Math.ceil(C / o.sx) + 2;
  const gy = Math.ceil(RW / o.sy) + 2;
  const seeds = [];
  for (let j = 0; j < gy; j += 1) for (let i = 0; i < gx; i += 1) seeds.push({ id: seeds.length, x: (i - 1 + R()) * o.sx, y: (j - 1 + R()) * o.sy, tone: R.range(0.8, 1.15), style: Math.floor(R() * 3) });
  const alpha = (c, r) => o.fade(c * cell, r * cell);
  const owner = new Int32Array(C * RW).fill(-1);
  for (let r = 0; r < RW; r += 1) {
    for (let c = 0; c < C; c += 1) {
      if (alpha(c, r) <= 0) continue;
      const gi = Math.floor(c / o.sx) + 1;
      const gj = Math.floor(r / o.sy) + 1;
      let best = -1;
      let bd = 1e9;
      for (let j = gj - 1; j <= gj + 1; j += 1) {
        for (let i = gi - 1; i <= gi + 1; i += 1) {
          if (i < 0 || j < 0 || i >= gx || j >= gy) continue;
          const sd = seeds[j * gx + i];
          const dx = (c + 0.5 - sd.x) / o.sx;
          const dy = (r + 0.5 - sd.y) / o.sy;
          const d = dx * dx + dy * dy;
          if (d < bd) {
            bd = d;
            best = sd.id;
          }
        }
      }
      owner[r * C + c] = best;
    }
  }
  const at = (c, r) => (c < 0 || r < 0 || c >= C || r >= RW ? -2 : owner[r * C + c]);
  for (let r = 0; r < RW; r += 1) {
    for (let c = 0; c < C; c += 1) {
      const id = at(c, r);
      if (id < 0) continue;
      const sd = seeds[id];
      const up = at(c, r - 1) !== id;
      const down = at(c, r + 1) !== id;
      const left = at(c - 1, r) !== id;
      const right = at(c + 1, r) !== id;
      // Seams: the lower and right edges of each block fall into shadow.
      if ((down && at(c, r + 1) >= 0) || (right && at(c + 1, r) >= 0 && !up)) continue;
      const x = c * cell;
      const y = r * cell;
      const rx = (c + 0.5 - sd.x) / o.sx;
      const ry = (r + 0.5 - sd.y) / o.sy;
      const dome = clamp01(0.55 - 0.45 * (rx * 0.8 + ry * 1.1));
      const sun = o.lit(x, y);
      let f = shade(o.tone, sd.tone * (0.62 + 0.62 * dome));
      f = mixc(f, WARM, sun * (0.25 + 0.5 * dome));
      let gl;
      let ink;
      const open = at(c, r - 1) < 0;
      if (up && R() < (open ? 0.35 : 0.12) + o.moist(x, y) * 0.6) {
        f = mixc(mixc(R.pick(MOSS), WARM, sun * 0.3), "#1c2a26", (1 - dome) * 0.3);
        gl = R.pick(['"', ",", ";", "'"]);
        ink = shade(f, 1.55);
        if (open && R() < 0.3) s.ledges.push({ x, y, side: o.side, lit: sun });
      } else if (up) {
        f = shade(f, 1.15);
        gl = left ? "/" : right ? "\\" : R.pick(["-", "_", "^", "-"]);
        ink = shade(f, 1.45);
      } else if (left) {
        gl = R() < 0.6 ? "|" : "(";
        ink = shade(f, 1.4);
      } else if (right || down) {
        f = shade(f, 0.8);
        gl = down ? (right ? "/" : "_") : "|";
        ink = shade(f, 0.62);
      } else {
        const n = noise(c * 0.45, r * 0.45 + 11);
        gl = sd.style === 0 ? (n < 0.35 ? "%" : n < 0.5 ? ":" : " ") : sd.style === 1 ? (n < 0.3 ? "/" : n < 0.45 ? "\\" : n < 0.6 ? "." : " ") : n < 0.3 ? "#" : n < 0.5 ? "=" : " ";
        ink = shade(f, dome > 0.5 ? 1.3 : 0.66);
        if (R() < o.moist(x, y) * 0.18) {
          f = mixc(f, R.pick(MOSS), 0.45);
          gl = R.pick(['"', ",", "%"]);
          ink = shade(f, 1.4);
        }
      }
      put(g, s, tile(s, gl, f, ink), x, y, alpha(c, r));
    }
  }
  g.globalAlpha = 1;
}

// A cliff's smooth silhouette from the screen edge to its face, darker
// toward the edge of the screen.
function cliffShape(g, s, edgeX, dir, lit, dark) {
  const { w, h, cell } = s;
  const p = new Path2D();
  const x0 = dir < 0 ? 0 : w;
  p.moveTo(x0, -cell);
  for (let y = -cell; y <= h + cell; y += cell / 2) p.lineTo(edgeX(y / cell), y);
  p.lineTo(x0, h + cell);
  p.closePath();
  const gr = g.createLinearGradient(x0, 0, dir < 0 ? w * 0.3 : w * 0.7, 0);
  gr.addColorStop(0, dark);
  gr.addColorStop(1, lit);
  g.fillStyle = gr;
  g.fill(p);
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
  return { c, ox: c0 * cell, oy: r0 * cell, w: nc * cell, h: nr * cell };
}

export const waterfall = {
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
    const { ctx, w, h, cell, k, poolY, fx, dpr } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    const [bx, by, bw, bh] = s.glowBox;
    ctx.drawImage(s.glowL, Math.max(0, bx * dpr), by * dpr, bw * dpr, bh * dpr, Math.max(0, bx), by, bw, bh);
    // Falling water: glyph streams scroll down each column.
    for (const [fi, F] of s.falls.entries()) {
      const off = t * F.sp;
      const shift = (off % 1) * cell;
      const top = Math.floor(F.y0 / cell);
      const rows = Math.ceil((poolY - F.y0) / cell);
      for (let i = 0; i < F.n; i += 1) {
        const c = F.c0 + i;
        const core = Math.sin((Math.PI * (i + 0.5)) / F.n);
        for (let r = -1; r < rows; r += 1) {
          const y = (top + r) * cell + shift;
          if (y < F.y0 - cell * 0.5 || y > poolY - cell * 0.5) continue;
          const q = r - Math.floor(off);
          const dens = noise(c * 0.9, q * 0.16) * 0.75 + core * 0.55 - F.dens;
          if (dens < 0.42) continue;
          const hs = hash(c, q, fi);
          const gl = Math.floor(hs * FALL.length);
          const id = dens > 0.78 && hs < 0.8 ? s.foamIds[dens > 1 ? 0 : dens > 0.88 ? 1 : 2][gl] : s.waterIds[dens > 0.7 ? 0 : dens > 0.6 ? 1 : dens > 0.5 ? 2 : 3][gl];
          // The sheet widens a little as it falls.
          const spread = 1 + 0.4 * (r / rows) ** 1.5;
          put(ctx, s, id, fx + (c * cell - fx) * spread + (hs - 0.5) * cell * 0.2, y, 0.7 + 0.3 * clamp01(dens - 0.4));
        }
      }
    }
    // Rings spreading across the pool from the plunge.
    const fwPx = s.falls[0].n * cell;
    for (let i = 0; i < 5; i += 1) {
      const life = (t * 0.07 + i / 5) % 1;
      const rx = fwPx * 0.7 + life * w * 0.32;
      const ry = rx * 0.13;
      const n = Math.round(6 + rx / (cell * 2.2));
      for (let j = 0; j <= n; j += 1) {
        const a = Math.PI * (j / n);
        const x = fx + Math.cos(a) * rx;
        const y = poolY + cell * 0.8 + Math.sin(a) * ry;
        if (x < 0 || x > w) continue;
        put(ctx, s, s.ringIds[(i + j) % 3], x, y, 0.8 * (1 - life) * (0.4 + 0.6 * Math.sin(a)));
      }
    }
    // Rocks in the pool, foam lapping at them.
    for (const rk of s.rocks) {
      ctx.globalAlpha = 1;
      ctx.drawImage(rk.sp.c, rk.x + rk.sp.ox, rk.y + rk.sp.oy, rk.sp.w, rk.sp.h);
      const lap = Math.sin(t * 1.4 + rk.ph);
      for (let j = -1; j <= rk.w / cell; j += 2) put(ctx, s, s.ringIds[(j + 3) & 1 ? 2 : 0], rk.x - rk.w / 2 + j * cell + lap * 2 * k, rk.y + rk.sp.h + rk.sp.oy - cell * 0.4, 0.45 + 0.2 * lap);
    }
    // Foam boiling at the foot, spray thrown up and drifting off.
    const boilN = Math.round((fwPx * 2.2) / cell);
    const tick = Math.floor(t * 6);
    for (let i = 0; i < boilN; i += 1) {
      const u = (i + 0.5) / boilN;
      const core = Math.sin(Math.PI * u);
      for (let r = 0; r < 2; r += 1) {
        const hs = hash(i, tick + r * 7, 11);
        if (hs > 0.25 + core * 0.75) continue;
        put(ctx, s, s.boilIds[Math.floor(hs * 7) % s.boilIds.length][core > 0.6 && r === 0 ? 0 : 1], fx - fwPx * 1.1 + i * cell + (hs - 0.5) * cell * 0.4, poolY - cell * (0.6 - r * 0.8), 0.6 + 0.4 * core);
      }
    }
    for (const p of s.churn) {
      const life = (t / p.p + p.ph) % 1;
      const n = Math.floor(t / p.p + p.ph);
      const x = fx + (hash(n, Math.floor(p.u * 1e4), 3) - 0.5) * fwPx * p.wide;
      const y = poolY - life * p.hgt * cell * 0.7;
      put(ctx, s, s.churnIds[Math.min(6, Math.floor(life * 4 + p.u * 3))], x, y, 0.95 * Math.sin(Math.PI * life));
    }
    for (const p of s.spray) {
      const life = (t / p.p + p.ph) % 1;
      const x = fx + (p.u - 0.5) * fwPx * 1.5 + p.dx * life * w * 0.12;
      const y = poolY - Math.sin(life * Math.PI * 0.8) * h * 0.18 - life * h * 0.05;
      put(ctx, s, s.sprayIds[Math.floor(p.u * 4)], x, y, 0.7 * (1 - life));
    }
    for (const m of s.puffs) {
      const life = (t / m.p + m.ph) % 1;
      const sz = m.sz * (0.8 + 1.8 * life);
      const x = fx + m.dx * life * w * 0.18 - sz / 2 + Math.sin(t * 0.2 + m.ph * 9) * 10 * k;
      const y = poolY - life * h * 0.34 - sz * 0.35;
      ctx.globalAlpha = m.a * Math.sin(Math.PI * life);
      ctx.drawImage(s.puff, x, y, sz, sz * 0.7);
    }
    const rb = s.rainbow;
    ctx.globalAlpha = 0.3 + 0.2 * noise(t * 0.05, 3.3);
    ctx.drawImage(rb.c, rb.x, rb.y, rb.w, rb.h);
    ctx.globalAlpha = 1;
    // Ferns and vines in the draught off the falls.
    const gust = (x) => 0.6 * Math.sin(t * 0.9 + x * 0.006) + 0.4 * Math.sin(t * 2.3 + x * 0.02) + 0.9 * Math.max(0, Math.sin(t * 0.21 + s.windOff)) ** 6;
    for (const f of s.ferns) {
      const b = 2.2 * k * gust(f.x);
      for (const c of f.cells) put(ctx, s, c.id, f.x + c.dc * cell * 0.62 + b * c.lift + Math.sin(t * 3 + f.ph + c.r) * 0.6 * k * c.lift, f.y + c.r * cell * 0.82);
    }
    for (const v of s.vines) {
      const b = 1.6 * k * (gust(v.x) + 0.5 * Math.sin(t * 1.3 + v.ph));
      for (const c of v.cells) put(ctx, s, c.id, v.x + b * (c.r / 3) ** 1.3, v.y + c.r * cell * 0.85, 0.95);
    }
    birds(ctx, s, t);
    ctx.globalAlpha = 1;
  },
};

// A few swallows hawking over the pool now and then.
function birds(ctx, s, t) {
  const period = 41;
  const n = Math.floor(t / period);
  const p = (t - n * period) / 14;
  if (p > 1) return;
  const { w, h, cell } = s;
  const r = (j) => hash(n, j, Math.floor(s.birdSeed));
  for (let b = 0; b < 3; b += 1) {
    const q = clamp01(p * 1.15 - b * 0.06);
    if (q <= 0 || q >= 1) continue;
    const dir = r(1) < 0.5 ? 1 : -1;
    const x = dir > 0 ? -cell + q * (w + 2 * cell) : w + cell - q * (w + 2 * cell);
    const y = s.poolY - h * (0.12 + 0.18 * r(2 + b)) + Math.sin(q * TAU * 2.2 + b * 2) * h * 0.07;
    const flap = Math.sin(t * 11 + b * 2);
    put(ctx, s, s.bird[flap > 0.3 ? 0 : flap < -0.3 ? 1 : 2], x, y, 0.95);
  }
  ctx.globalAlpha = 1;
}

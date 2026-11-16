// Scene: Bamboo Grove. Standing inside a dense grove: culms rise out of the
// frame in ASCII tiles, rank behind rank fading into a green-gold haze. Sun
// shafts slant through from the bright side and breathe, dust motes turn in
// the light, the culms creak and lean with the wind (a gust rolls through
// now and then), leaf sprays flutter, leaves spiral down past a stone
// lantern, and once in a while a small bird darts across.

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const HAZE = "#dccf86";
const SUN = "#f4de8c";
// Tile ranks, far to near: width and gap in tiles, colour, haze, foot
// (fraction of height), sway, segment length, leaf-spray chance. The far
// rank is still and painted into the background.
const RANKS = [
  { wd: [1, 2], gap: [3, 8], col: "#7e9c58", haze: 0.42, foot: 0.7, sway: 0, seg: [4, 6], leaf: 0.35, mist: 0.12, sz: 0.72 },
  { wd: [2, 2], gap: [7, 16], col: "#3f6b30", haze: 0.1, foot: 0.86, sway: 1, seg: [6, 8], leaf: 0.55, mist: 0.05, sz: 1 },
  { wd: [2, 3], gap: [4, 8], col: "#1f3d1a", haze: 0, foot: 1.06, sway: 1.6, seg: [8, 11], leaf: 0.8, sides: true, sz: 1.8 },
];

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const q = (v, n = 10) => Math.round(clamp01(v) * n) / n;

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const sd = (s.sd = R.sign());
  const sunX = (s.sunX = w * (0.5 + sd * R.range(0.18, 0.3)));
  // Light through the canopy, the sun's glow, the floor: one still image.
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, h);
  lin.addColorStop(0, "#5d763a");
  lin.addColorStop(0.4, "#7f9248");
  lin.addColorStop(0.64, "#a4a660");
  lin.addColorStop(0.72, "#56682e");
  lin.addColorStop(0.88, "#26361a");
  lin.addColorStop(1, "#111a0c");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const glow = g.createRadialGradient(sunX, -h * 0.08, 0, sunX, -h * 0.08, Math.max(w, h) * 0.7);
  glow.addColorStop(0, "rgba(255,242,186,0.95)");
  glow.addColorStop(0.3, "rgba(236,220,148,0.45)");
  glow.addColorStop(1, "rgba(236,220,148,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, h);
  // The farthest culms: soft painted bars dissolving in the haze.
  g.save();
  g.filter = `blur(${Math.max(1, 4 * k)}px)`;
  for (let x = R() * 20 * k; x < w; x += R.range(14, 40) * k) {
    const bw = R.range(4, 9) * k;
    const fb = g.createLinearGradient(0, 0, 0, h * 0.68);
    const a = R.range(0.18, 0.4);
    fb.addColorStop(0, `rgba(110,136,74,${a * 0.6})`);
    fb.addColorStop(0.7, `rgba(110,136,74,${a})`);
    fb.addColorStop(1, "rgba(150,160,90,0)");
    g.fillStyle = fb;
    g.fillRect(x, -10, bw, h * 0.68);
  }
  g.restore();
  // A band of haze where the far floor meets the culms.
  const band = g.createLinearGradient(0, h * 0.52, 0, h * 0.8);
  band.addColorStop(0, "rgba(220,207,134,0)");
  band.addColorStop(0.55, "rgba(220,207,134,0.42)");
  band.addColorStop(1, "rgba(220,207,134,0)");
  g.fillStyle = band;
  g.fillRect(0, h * 0.52, w, h * 0.3);
  const vig = g.createRadialGradient(sunX, h * 0.3, Math.min(w, h) * 0.3, sunX, h * 0.3, Math.max(w, h) * 0.95);
  vig.addColorStop(0, "rgba(20,34,12,0)");
  vig.addColorStop(1, "rgba(20,34,12,0.55)");
  g.fillStyle = vig;
  g.fillRect(0, 0, w, h);
  dotGrid(g, s, "#efe6b4", 0.16);
  // Sun shafts: where each lands, light pools on the floor.
  s.shafts = [];
  const nShafts = Math.max(3, Math.round(5 * Math.min(1.3, w / 1440)));
  const slant = -sd * R.range(0.28, 0.4);
  for (let i = 0; i < nShafts; i += 1) {
    const x0 = sunX + sd * w * 0.04 + (i / (nShafts - 1) - 0.5) * w * 0.5 + R.range(-0.04, 0.04) * w;
    const y1 = h * R.range(0.8, 0.95);
    s.shafts.push({ x0, y0: -h * 0.05, x1: x0 + slant * y1, y1, w0: R.range(16, 46) * k, w1: R.range(60, 130) * k, a: R.range(0.55, 1) });
  }
  for (const sh of s.shafts) {
    g.save();
    g.translate(sh.x1, sh.y1);
    g.scale(1, 0.26);
    const pool = g.createRadialGradient(0, 0, 0, 0, 0, sh.w1 * 1.3);
    pool.addColorStop(0, `rgba(246,222,130,${0.4 * sh.a})`);
    pool.addColorStop(1, "rgba(246,222,130,0)");
    g.fillStyle = pool;
    g.fillRect(-sh.w1 * 1.4, -sh.w1 * 1.4, sh.w1 * 2.8, sh.w1 * 2.8);
    g.restore();
  }
  const litter = ["#6a5a2c", "#8a7434", "#4f5a26", "#a08a40", "#5c4a24"];
  const lg = [".", ",", "~", "-", "'", "_"];
  for (let y = h * 0.8; y < h; y += cell * 0.9) {
    const dens = (y - h * 0.8) / (h * 0.2);
    for (let x = 0; x < w; x += cell) {
      if (R() > 0.12 + 0.3 * dens) continue;
      const lit = s.shafts.some((sh) => Math.abs(x - sh.x1) < sh.w1 && Math.abs(y - sh.y1) < sh.w1 * 0.3);
      const c = litter[Math.floor(R() * litter.length)];
      put(g, s, tile(s, R.pick(lg), null, lit ? mixc(c, SUN, 0.6) : c), x + (R() - 0.5) * cell * 0.5, y, 0.45 + 0.4 * dens);
    }
  }
  g.globalAlpha = 1;
  // Shafts of light: soft beams in two sets that breathe apart.
  s.beams = [0, 1].map((set) => {
    const sc = 0.25;
    const c = document.createElement("canvas");
    c.width = Math.ceil(w * sc);
    c.height = Math.ceil(h * sc);
    const bg = c.getContext("2d");
    bg.scale(sc, sc);
    bg.filter = `blur(${Math.max(1, 10 * k * sc)}px)`;
    s.shafts.forEach((sh, i) => {
      if (i % 2 !== set) return;
      const gr = bg.createLinearGradient(sh.x0, sh.y0, sh.x1, sh.y1);
      gr.addColorStop(0, `rgba(255,240,180,${0.7 * sh.a})`);
      gr.addColorStop(0.55, `rgba(255,232,150,${0.38 * sh.a})`);
      gr.addColorStop(1, "rgba(255,226,140,0)");
      bg.fillStyle = gr;
      bg.beginPath();
      bg.moveTo(sh.x0 - sh.w0 / 2, sh.y0);
      bg.lineTo(sh.x0 + sh.w0 / 2, sh.y0);
      bg.lineTo(sh.x1 + sh.w1 / 2, sh.y1);
      bg.lineTo(sh.x1 - sh.w1 / 2, sh.y1);
      bg.closePath();
      bg.fill();
    });
    const xs = s.shafts.filter((sh, i) => i % 2 === set).flatMap((sh) => [sh.x0 - sh.w0, sh.x0 + sh.w0, sh.x1 - sh.w1, sh.x1 + sh.w1]);
    const x0 = Math.max(0, Math.floor(Math.min(...xs) * sc));
    const x1 = Math.min(c.width, Math.ceil(Math.max(...xs) * sc));
    return { c, sx: x0, sw: Math.max(1, x1 - x0), sc };
  });
  // The culms, each painted once into its own strip; the far rank is still
  // and goes into the background, the others lean per frame. Each rank has
  // its own tile size: small and pale far off, big and dark up close.
  s.ranks = RANKS.map((L, li) => {
    tiles(s, Math.max(3, 13 * k * L.sz));
    const cl = s.cell;
    const top = -3 * cl;
    const culms = [];
    const sprays = [];
    let x = -R() * 4 * cl;
    while (x < w + cl) {
      const side = Math.abs(x / w - 0.5) * 2;
      const W = Math.round(R.range(L.wd[0], L.wd[1] + 0.99));
      const keep = L.sides ? side > 0.62 : li === 1 ? side > 0.3 || R() < 0.5 : true;
      if (keep) culms.push(culm(s, R, L, li, x, W, top, sprays));
      x += (W + Math.round(R.range(L.gap[0], L.gap[1]))) * cl;
    }
    if (L.sides) {
      // The near rank always frames both edges.
      if (!culms.some((c) => c.x < w * 0.12)) culms.push(culm(s, R, L, li, R.range(0.01, 0.06) * w, 3, top, sprays));
      if (!culms.some((c) => c.x > w * 0.86)) culms.push(culm(s, R, L, li, R.range(0.9, 0.95) * w - 3 * cl, 3, top, sprays));
      s.canopy = canopy(s, R, sunX);
    }
    return { L, li, culms, sprays };
  });
  // A stone lantern on the shady side, in a gap between the middle culms.
  tiles(s, Math.max(3, 13 * k * 1.3));
  const lc = s.cell;
  const stone = "#7a7a5e";
  const ART = ["   ^   ", "  /%\\  ", " /%%%\\ ", "/=====\\", "  [o]  ", "  |=|  ", "  |:|  ", " [===] "];
  let lx = w * (0.5 - sd * 0.31) - 3.5 * lc;
  const clear = (x) => [...s.ranks[1].culms, ...s.ranks[2].culms].every((st) => x + 7 * lc < st.x - lc || x > st.x + (st.W + 1) * st.cell);
  for (let i = 0; i < 40 && !clear(lx); i += 1) lx += sd * lc * 0.5;
  const cells = [];
  ART.forEach((line, r) => Array.from(line).forEach((ch, i) => {
    if (ch === " ") return;
    const lit = sd > 0 ? i >= 3 : i <= 3;
    const bare = ch === "^";
    const f = ch === "%" ? (lit ? "#6f8a3c" : "#4f6a2c") : ch === "o" ? "#3a3420" : shade(stone, lit ? 1.2 : 0.74);
    const ink = ch === "o" ? "#ffd27a" : ch === "%" ? "#a8c460" : shade(f, lit ? 0.62 : 0.55);
    cells.push({ dc: i, dr: r - ART.length, id: tile(s, ch, bare ? null : f, bare ? shade(stone, 1.3) : ink) });
  }));
  s.lantern = { sp: bake(s, cells), x: lx, y: h * 0.91, lx: lx + 3.5 * lc, ly: h * 0.91 - 3.5 * lc, lc };
  tiles(s, Math.max(4, 13 * k));
  const far = s.ranks[0];
  for (const st of far.culms) {
    g.setTransform(s.dpr, 0, -st.lean * s.dpr, s.dpr, (st.x + st.lean * st.foot) * s.dpr, 0);
    g.drawImage(st.c, 0, st.top, st.W * st.cell, st.rows * st.cell);
    if (st.leaf) g.drawImage(st.leaf.c, st.leaf.ox, st.leaf.oy, st.leaf.w, st.leaf.h);
  }
  g.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
  g.globalAlpha = 1;
  s.back = back;
  // Shoots at the near feet.
  s.shoots = [];
  for (let c = 0; c < Math.ceil(w / cell); c += 1) {
    const side = Math.abs((c * cell) / w - 0.5) * 2;
    if (side < 0.4 || R() > 0.2 * side) continue;
    const n = 1 + Math.floor(R() * 3);
    const base = h - cell * R.range(0.8, 2.2);
    for (let j = 0; j < n; j += 1) s.shoots.push({ x: c * cell, y: base - j * cell, lift: j / 3, id: tile(s, j === n - 1 ? "^" : j === 0 ? "%" : "|", j === n - 1 ? null : j === 0 ? "#5a4c22" : "#2f5a24", j === n - 1 ? "#9ac458" : j === 0 ? "#a08a40" : "#7aa648"), ph: R() * TAU });
  }
  // Falling leaves, dust motes, the bird.
  const fall = ["#d8c050", "#b0c854", "#e8d070", "#94b448"];
  s.fallIds = fall.map((c) => ["/", "-", "\\", "|"].map((gl) => tile(s, gl, null, c)));
  s.falling = Array.from({ length: Math.round(12 * Math.max(0.5, w / 1440)) }, () => ({ x: R() * w, p: R.range(14, 24), ph: R(), sw: R.range(20, 60) * k, spin: R.range(1.5, 3.5), c: Math.floor(R() * fall.length), dr: R.range(-12, 12) * k }));
  s.motes = Array.from({ length: Math.round(110 * Math.max(0.5, w / 1440)) }, () => ({ b: Math.floor(R() * s.shafts.length), u: R(), l: R() - 0.5, v: R.range(-0.006, 0.006), ph: R() * TAU, r: R.range(0.9, 2) * k }));
  s.bird = [tile(s, "v", null, "#1a2210"), tile(s, "-", null, "#1a2210"), tile(s, "^", null, "#1a2210")];
  s.windOff = R() * 1000;
  s.birdSeed = R() * 1000;
}

// Clumps of leaves hang in fans from the canopy overhead, thick in the
// corners and parted where the sun comes through.
function canopy(s, R, sunX) {
  const { w, k } = s;
  const out = [];
  const leafC = ["#2c5422", "#3a6a2a", "#24461c", "#4a7c30"];
  for (let x = -R() * 60 * k; x < w + 40 * k; x += R.range(40, 100) * k) {
    const u = x / w;
    const side = Math.abs(u - 0.5) * 2;
    if (Math.abs(u - sunX / w) < 0.1 || R() > 0.25 + side) continue;
    const span = Math.max(3, Math.round(R.range(4, 9) * (0.6 + side * 0.6)));
    const deep = (2 + 8 * side * side) * R.range(0.6, 1.2);
    const cells = [];
    for (let j = 0; j < span; j += 1) {
      const dir = j < span / 2 ? -1 : 1;
      const len = Math.max(1, Math.round(deep * (0.45 + 0.55 * Math.sin((Math.PI * (j + 0.5)) / span)) * R.range(0.7, 1.2)));
      let dc = j;
      const r0 = -Math.floor(R() * 3);
      for (let m = 0; m < len; m += 1) {
        if (m > 0 && m % 2 === 0) dc += dir;
        if (m + r0 < 0) continue;
        const end = m === len - 1;
        const filled = m > 0 && m < len * 0.6 && R() < 0.4;
        const f = shade(R.pick(leafC), R.range(0.85, 1.15));
        const lit = R() < 0.22;
        const gl = end ? R.pick(["v", "'"]) : dir > 0 ? "\\" : "/";
        cells.push({ dc, dr: m + r0, id: tile(s, gl, filled ? (lit ? mixc(f, "#a4c858", 0.45) : f) : null, filled ? shade(f, lit ? 0.6 : 1.7) : mixc(R.pick(["#4f8a34", "#3c7028"]), SUN, lit ? 0.45 : 0)) });
      }
    }
    if (cells.length) out.push({ x, sp: bake(s, cells), ph: R() * TAU, deep });
  }
  return out;
}

// One culm: a strip canvas of tiles plus its leaf sprays (drawn live).
function culm(s, R, L, li, x, W, top, sprays) {
  const { h, cell, dpr } = s;
  const foot = h * (L.foot + R.range(-0.025, 0.025));
  const rows = Math.ceil((foot - top) / cell);
  const c = document.createElement("canvas");
  c.width = W * s.P;
  c.height = rows * s.P;
  const g = c.getContext("2d");
  g.scale(dpr, dpr);
  // The far ranks carry their sprays in a second still image that leans
  // with the culm; the near rank's sprays flutter on their own.
  const own = [];
  const old = R() < 0.15;
  const col = shade(old ? mixc(L.col, "#b4a24a", 0.4) : L.col, R.pick([0.9, 1, 1.1]));
  // Round shading across the culm, brightest on the sun side.
  const across = Array.from({ length: W }, (_, i) => {
    const u = W === 1 ? 0.6 : i / (W - 1);
    return s.sd > 0 ? u : 1 - u;
  });
  const segLen = Math.round(R.range(L.seg[0], L.seg[1] + 0.99));
  const node = Math.floor(R() * segLen);
  const sheath = li >= 1 && R() < 0.5 ? Math.round(R.range(3, 7)) : 0;
  const st = { c, x, W, top, rows, cell, foot, lean: R.range(-0.012, 0.012), amp: L.sway * R.range(0.7, 1.3), ph: R() * TAU, sx: 0 };
  for (let r = 0; r < rows; r += 1) {
    const y = top + r * cell;
    const v = y / h;
    const hz = q(L.haze * (0.7 + 0.6 * (1 - v)) + 0.25 * Math.pow(1 - clamp01(v / 0.45), 2) * (1 - li * 0.3), 8);
    const isNode = (r - node) % segLen === 0 && r >= node;
    const below = (r - node) % segLen === 1 && r > node;
    const inSheath = rows - r <= sheath;
    for (let i = 0; i < W; i += 1) {
      const a = across[i];
      let f = inSheath ? mixc(col, "#7a6630", 0.6) : col;
      f = a > 0.85 ? mixc(shade(f, 1.45), SUN, 0.3) : shade(f, 0.5 + 0.55 * a);
      f = mixc(f, HAZE, hz);
      const tex = R();
      let gl = a > 0.85 || a < 0.15 ? "|" : tex < 0.25 ? ":" : tex < 0.38 ? "|" : tex < 0.46 ? "'" : " ";
      let ink = a > 0.5 ? shade(f, 1.45) : shade(f, 0.6);
      if (W === 1) gl = "|";
      if (isNode) {
        gl = "=";
        f = shade(f, 1.15);
        ink = shade(f, 0.55);
      } else if (below) {
        gl = "-";
        ink = shade(f, 0.72);
      } else if (inSheath) {
        gl = R() < 0.5 ? "%" : ":";
        ink = shade(f, 0.62);
      }
      put(g, s, tile(s, gl, f, ink), i * cell, r * cell);
    }
    // Leaf sprays from nodes high in the grove.
    if (isNode && r > 0) {
      const lim = li === 2 ? 0.55 : 0.6;
      const want = v < lim ? L.leaf * (1 - (v / lim) * 0.5) : 0;
      if (R() < want) (L.sides ? sprays : own).push(spray(s, R, li, st, r, W, hz, col));
    }
  }
  if (own.length) {
    const lb = bake(s, own.flatMap((sp) => sp.cells.map((cl) => ({ ...cl, dr: sp.r + cl.dr }))));
    st.leaf = { ...lb, oy: lb.oy + top };
  }
  g.globalAlpha = 1;
  // The foot dissolves in the floor mist.
  if (L.mist) {
    g.globalCompositeOperation = "destination-out";
    const fh = h * L.mist;
    const gr = g.createLinearGradient(0, rows * cell - fh, 0, rows * cell);
    gr.addColorStop(0, "rgba(0,0,0,0)");
    gr.addColorStop(1, "rgba(0,0,0,0.85)");
    g.fillStyle = gr;
    g.fillRect(0, rows * cell - fh, W * cell, fh);
    g.globalCompositeOperation = "source-over";
  }
  return st;
}

// A twig up and out from a node, leaves drooping from it in a fan.
function spray(s, R, li, st, r, W, hz, col) {
  const dir = R.sign();
  const sunward = dir === s.sd;
  const leafF = mixc(li === 2 ? "#2f6224" : "#4a7e34", HAZE, hz * 0.9);
  const leafLit = mixc(mixc(leafF, "#bcd468", 0.5), HAZE, hz * 0.4);
  const twig = mixc(shade(col, 1.2), HAZE, hz);
  const cells = [];
  const n = 2 + Math.floor(R() * (li === 2 ? 4 : 2));
  const edge = dir > 0 ? W - 1 : 0;
  const down = dir > 0 ? "\\" : "/";
  for (let i = 1; i <= n; i += 1) {
    const dc = edge + dir * i;
    const dr = -Math.round(i * 0.7);
    cells.push({ dc, dr, id: tile(s, dir > 0 ? "/" : "\\", null, shade(twig, 1.2)) });
    if (i === 1 && n > 2) continue;
    const len = 2 + Math.floor(R() * (li === 2 ? 3 : 2));
    for (let m = 1; m <= len; m += 1) {
      const filled = m === 1 && R() < 0.45;
      const lit = (sunward ? 0.65 : 0.25) > R();
      const f = lit ? leafLit : shade(leafF, R.range(0.85, 1.12));
      const gl = m === len ? R.pick(["v", "'", down]) : down;
      cells.push({ dc: dc + dir * Math.ceil(m * 0.6), dr: dr + m, id: tile(s, gl, filled ? f : null, filled ? shade(f, lit ? 0.6 : 1.5) : lit ? shade(leafLit, 1.15) : shade(leafF, 1.5)) });
    }
  }
  return { st, r, y: st.top + r * s.cell, cells, sp: li === 2 ? bake(s, cells) : null, ph: R() * TAU };
}

// Cells painted once into a small canvas, drawn as one image.
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

// A baked sprite anchored at (ax, ay), leaning by `shear` below the anchor.
function sprite(ctx, s, sp, ax, ay, shear) {
  ctx.setTransform(s.dpr, 0, shear * s.dpr, s.dpr, ax * s.dpr, ay * s.dpr);
  ctx.drawImage(sp.c, sp.ox, sp.oy, sp.w, sp.h);
}

export const bamboo = {
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
    const { ctx, w, cell, k, dpr } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, s.h);
    const gustX = ((t * 85 + s.windOff) % (w + 1800)) - 900;
    const wind = (x) => 0.5 * Math.sin(t * 0.45 + x * 0.0025) + 0.3 * Math.sin(t * 1.05 + x * 0.009) + 1.4 * Math.exp(-(((x - gustX) / 320) ** 2));
    beams(ctx, s, t);
    for (const rk of s.ranks) {
      if (rk.li === 0) continue;
      if (rk.li === 2) {
        motes(ctx, s, t);
        leaves(ctx, s, t, 0);
        const L = s.lantern;
        sprite(ctx, s, L.sp, L.x, L.y, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const fl = 0.16 + 0.08 * noise(t * 3, 5.5);
        const lg = ctx.createRadialGradient(L.lx, L.ly, 0, L.lx, L.ly, L.lc * 3);
        lg.addColorStop(0, `rgba(255,200,110,${fl})`);
        lg.addColorStop(1, "rgba(255,200,110,0)");
        ctx.globalAlpha = 1;
        ctx.fillStyle = lg;
        ctx.fillRect(L.lx - L.lc * 3, L.ly - L.lc * 3, L.lc * 6, L.lc * 6);
      }
      for (const st of rk.culms) {
        st.sx = st.lean + 0.0045 * st.amp * (wind(st.x) + 0.25 * Math.sin(t * 0.7 + st.ph));
        ctx.setTransform(dpr, 0, -st.sx * dpr, dpr, (st.x + st.sx * st.foot) * dpr, 0);
        ctx.drawImage(st.c, 0, st.top, st.W * st.cell, st.rows * st.cell);
        if (st.leaf) ctx.drawImage(st.leaf.c, st.leaf.ox, st.leaf.oy, st.leaf.w, st.leaf.h);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const sp of rk.sprays) {
        const st = sp.st;
        const fl = Math.sin(t * 1.6 + sp.ph) + 0.6 * wind(st.x);
        sprite(ctx, s, sp.sp, st.x + st.sx * (st.foot - sp.y) + fl * (0.6 + rk.li * 0.6) * k, sp.y, 0.04 * fl);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    for (const c of s.canopy) sprite(ctx, s, c.sp, c.x, 0, (0.04 + 0.01 * c.deep) * (0.6 * Math.sin(t * 1.1 + c.ph) + wind(c.x)) / (1 + c.deep * 0.3));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    leaves(ctx, s, t, 1);
    for (const sh of s.shoots) put(ctx, s, sh.id, sh.x + 1.5 * k * sh.lift * Math.sin(t * 1.3 + sh.ph), sh.y);
    bird(ctx, s, t);
    ctx.globalAlpha = 1;
  },
};

function beams(ctx, s, t) {
  ctx.globalCompositeOperation = "source-over";
  s.beams.forEach((b, i) => {
    ctx.globalAlpha = 0.5 + 0.5 * noise(t * 0.08 + i * 17, s.windOff);
    ctx.drawImage(b.c, b.sx, 0, b.sw, b.c.height, b.sx / b.sc, 0, b.sw / b.sc, b.c.height / b.sc);
  });
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = 1;
}

function motes(ctx, s, t) {
  ctx.fillStyle = "#fff4c8";
  for (const m of s.motes) {
    const sh = s.shafts[m.b];
    const u = (((m.u + m.v * t) % 1) + 1) % 1;
    const x = sh.x0 + (sh.x1 - sh.x0) * u + m.l * (sh.w0 + (sh.w1 - sh.w0) * u) + 8 * s.k * Math.sin(t * 0.3 + m.ph);
    const y = sh.y0 + (sh.y1 - sh.y0) * u + 6 * s.k * Math.sin(t * 0.23 + m.ph * 2);
    const a = 0.9 * Math.sin(Math.PI * u) * (0.3 + 0.7 * (0.5 + 0.5 * Math.sin(t * 1.3 + m.ph))) * (1 - Math.abs(m.l) * 1.4);
    if (a < 0.03) continue;
    ctx.globalAlpha = a;
    ctx.fillRect(x, y, m.r, m.r);
  }
  ctx.globalAlpha = 1;
}

// Leaves spiralling down; half pass behind the near culms, half in front.
function leaves(ctx, s, t, front) {
  const { w, h, cell } = s;
  s.falling.forEach((L, i) => {
    if (i % 2 !== front) return;
    const life = (t / L.p + L.ph) % 1;
    const n = Math.floor(t / L.p + L.ph);
    const x = ((L.x + n * 397) % w) + L.dr * life * 10 + L.sw * Math.sin(life * TAU * 1.6 + i);
    const y = -cell + life * (h * 0.95 + cell);
    const id = s.fallIds[L.c][Math.floor(t * L.spin + i) % 4];
    put(ctx, s, id, x, y, 0.95 * Math.min(1, (1 - life) * 8));
  });
  ctx.globalAlpha = 1;
}

function bird(ctx, s, t) {
  const period = 53;
  const n = Math.floor(t / period);
  const p = (t - n * period) / 7;
  if (p > 1) return;
  const { w, h, cell } = s;
  const r = (j) => {
    const v = Math.sin(n * 12.9898 + s.birdSeed + j * 78.233) * 43758.5453;
    return v - Math.floor(v);
  };
  const dir = r(1) < 0.5 ? 1 : -1;
  const x = dir > 0 ? -cell + p * (w + cell * 2) : w + cell - p * (w + cell * 2);
  // Bounding flight: flap up, glide down.
  const ph = (p * 9) % 1;
  const y = h * (0.3 + r(2) * 0.28) - Math.sin(p * Math.PI) * h * 0.05 + (ph < 0.5 ? -ph : ph - 1) * cell * 1.2;
  put(ctx, s, s.bird[ph < 0.5 ? (Math.floor(t * 12) % 2 ? 0 : 2) : 1], x, y, 0.95);
  ctx.globalAlpha = 1;
}

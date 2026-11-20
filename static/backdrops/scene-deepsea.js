// Scene: Deep Sea. Looking up through open water toward a bright, rippling
// surface: god rays slant down and sway, marine snow drifts through them,
// schools of small fish wheel and turn together, kelp sways at both edges
// over a reef of coral, a jellyfish pulses up now and then, and every minute
// or so a humpback whale glides across overhead.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const WATER = "#0c4a66";
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const lerpTable = (xs, ys, u) => {
  if (u <= xs[0]) return ys[0];
  for (let i = 1; i < xs.length; i += 1) if (u <= xs[i]) return ys[i - 1] + ((ys[i] - ys[i - 1]) * (u - xs[i - 1])) / (xs[i] - xs[i - 1]);
  return ys[ys.length - 1];
};
const FLIP = { "/": "\\", "\\": "/", "<": ">", ">": "<", "(": ")", ")": "(" };

// The humpback, head to the right, as cells {i, r, glyph, fill, ink, part}:
// an arched back, a long flat head with knobs and a mouth line, pale
// throat pleats, a broad fluke and a long white pectoral fin.
function whaleCells(L) {
  const th = Math.max(3, L * 0.105);
  const U = [0, 0.08, 0.2, 0.35, 0.5, 0.6, 0.72, 0.84, 0.93, 1];
  const TOP = [0.1, 0.12, 0.17, 0.38, 0.75, 0.9, 0.88, 0.66, 0.4, 0.12];
  const BOT = [0.1, 0.12, 0.18, 0.42, 0.86, 1.04, 1.1, 0.92, 0.58, 0.2];
  const mid = (u) => th * (-0.34 * Math.sin(Math.PI * clamp01((u - 0.05) / 0.9)) + (0.32 * Math.max(0, u - 0.8)) / 0.2);
  const topRow = (i) => Math.round(mid(i / (L - 1)) - lerpTable(U, TOP, i / (L - 1)) * th);
  const botRow = (i) => Math.round(mid(i / (L - 1)) + lerpTable(U, BOT, i / (L - 1)) * th);
  const mouth = (u) => Math.round(mid(u) + th * (0.3 - 1.1 * (u - 0.78)));
  const cells = [];
  const fN = Math.max(3, Math.round(L * 0.1));
  for (let i = fN; i < L; i += 1) {
    const u = i / (L - 1);
    const t0 = topRow(i);
    const b0 = botRow(i);
    for (let r = t0; r <= b0; r += 1) {
      const v = (r - t0) / Math.max(1, b0 - t0);
      let glyph;
      let fill = mixc("#2c5878", "#0f2236", clamp01(v * 1.3));
      let ink = shade(fill, 1.5);
      let part = "body";
      const hsh = ((i * 73856093) ^ (r * 19349663)) >>> 0;
      if (r === t0) {
        const nx = i + 1 < L ? topRow(i + 1) : t0;
        const pv = topRow(i - 1);
        glyph = nx < t0 ? "/" : pv < t0 ? "\\" : u > 0.86 && hsh % 3 === 0 ? "o" : "-";
        fill = "#5296b4";
        ink = "#c8f0f8";
      } else if (r === b0) {
        const nx = i + 1 < L ? botRow(i + 1) : b0;
        const pv = botRow(i - 1);
        glyph = nx > b0 ? "\\" : pv > b0 ? "/" : "_";
        fill = u > 0.45 ? "#5a8aa0" : "#14283e";
        ink = u > 0.45 ? "#c0e4ec" : "#3c6480";
      } else if (u > 0.78 && r === mouth(u)) {
        glyph = "-";
        fill = "#0c1828";
        ink = "#6a98b0";
      } else if (i === Math.round(0.77 * (L - 1)) && r === mouth(0.79) - 1) {
        glyph = "o";
        fill = "#1c3a52";
        ink = "#e8fbff";
      } else if (u > 0.45 && r > mid(u) + th * 0.22 && (u < 0.78 || r > mouth(u))) {
        glyph = (r + Math.floor(i / 5)) % 2 ? "=" : "-";
        fill = mixc("#4e8098", "#2a4e66", clamp01((b0 - r) / (th * 0.8)));
        ink = "#b4dce6";
        part = "pleat";
      } else if (u > 0.84 && r < mouth(u) && hsh % 4 === 0) {
        glyph = "o";
        ink = "#9cc8d6";
      } else if (r === t0 + 1) {
        glyph = hsh % 5 === 0 ? "~" : "-";
        fill = "#36688a";
        ink = "#78b0c8";
      } else {
        glyph = [".", " ", " ", "'", " ", ".", "~", " ", ":"][hsh % 9];
      }
      cells.push({ i, r, glyph, fill, ink, part });
    }
  }
  // Small dorsal fin on the hump.
  const di = Math.round(L * 0.34);
  cells.push({ i: di, r: topRow(di) - 1, glyph: "/", fill: "#4a8aa8", ink: "#c8f0f8", part: "body" });
  cells.push({ i: di + 1, r: topRow(di + 1) - 1, glyph: "^", fill: "#4a8aa8", ink: "#c8f0f8", part: "body" });
  // The fluke: a broad crescent fanning out behind the tail stock.
  const m0 = Math.round(mid(0));
  const S = th * 0.95;
  for (let i = 0; i < fN; i += 1) {
    const spread = Math.round(1 + ((fN - 1 - i) / fN) * S + 0.3);
    for (let r = -spread; r <= spread; r += 1) {
      const notch = Math.round((1 - Math.abs(r) / (S + 1)) * 2.2);
      if (i < notch) continue;
      const edge = Math.abs(r) === spread;
      const trail = i === notch;
      const glyph = edge ? (r < 0 ? "\\" : "/") : trail ? (r < 0 ? "\\" : r > 0 ? "/" : "<") : r === 0 ? "=" : "-";
      cells.push({ i, r: r + m0, glyph, fill: edge || trail ? "#6aa0b8" : "#1a344c", ink: edge || trail ? "#e4f8fc" : "#4a7894", part: "fluke" });
    }
  }
  // The long pale pectoral fin, swept back and down, knobbed on its edge.
  const fi = Math.round(L * 0.7);
  const fr = botRow(fi) - 1;
  const fl = Math.round(L * 0.27);
  for (let j = 0; j < fl; j += 1) {
    const r = fr + Math.round(j * 0.45);
    const wd = Math.max(1, Math.round(3.4 * (1 - j / fl) + 0.4));
    for (let q = 0; q < wd; q += 1) {
      const lead = q === wd - 1;
      const back = q === 0 && wd > 1;
      cells.push({ i: fi - j, r: r + q, glyph: lead ? (j % 2 ? "o" : "/") : back ? "/" : "=", fill: lead ? "#c4e2e8" : back ? "#6e9cb0" : "#9cc6d4", ink: lead ? "#3c6a80" : "#2a5068", part: "fin", d: j / fl });
    }
  }
  return { cells, th };
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const rows = Math.ceil(h / cell) + 1;
  const port = (s.port = h > w * 1.1);
  // The light comes down from a sun above the surface, left of centre.
  s.src = { x: w * (port ? 0.42 : 0.38), y: -h * 0.35 };
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, h);
  lin.addColorStop(0, "#2ea2b4");
  lin.addColorStop(0.1, "#147e9a");
  lin.addColorStop(0.32, "#0a5070");
  lin.addColorStop(0.62, "#052c4a");
  lin.addColorStop(1, "#010a18");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const win = g.createRadialGradient(s.src.x + w * 0.04, 0, 0, s.src.x + w * 0.04, 0, Math.max(w, h) * 0.55);
  win.addColorStop(0, "rgba(200,255,240,0.5)");
  win.addColorStop(0.3, "rgba(120,220,220,0.16)");
  win.addColorStop(1, "rgba(80,200,220,0)");
  g.fillStyle = win;
  g.fillRect(0, 0, w, h);
  // Far reef silhouettes, soft in the blue.
  for (const [li, col, top] of [[0, "#062a44", 0.8], [1, "#04203a", 0.86]]) {
    const p = new Path2D();
    const o = R() * 50;
    p.moveTo(0, h);
    for (let x = 0; x <= w + 6; x += 6) {
      const u = x / w;
      const side = Math.abs(u - 0.5) * 2;
      p.lineTo(x, h * (top + 0.06 * (1 - side) - 0.06 * side * side) - h * 0.05 * noise(u * 5 + o, li));
    }
    p.lineTo(w, h);
    p.closePath();
    g.fillStyle = col;
    g.fill(p);
  }
  dotGrid(g, s, "#7fd8e0", 0.14);
  s.back = back;
  // God rays: long soft wedges from the light, drawn small and scaled up.
  const nRays = port ? 7 : 9;
  s.rays = Array.from({ length: nRays }, (_, i) => {
    const ang = Math.PI / 2 + (i / (nRays - 1) - 0.5) * (port ? 0.7 : 0.95) + R.range(-0.04, 0.04);
    const half = R.range(0.012, 0.035);
    const len = Math.hypot(w, h) * 1.3;
    const sc = 4;
    const c = document.createElement("canvas");
    const cw = Math.ceil(len / sc);
    const ch = Math.ceil((len * half * 2.6) / sc) + 4;
    c.width = cw;
    c.height = ch;
    const cg = c.getContext("2d");
    cg.filter = `blur(${Math.max(1, 2 * k)}px)`;
    const gr = cg.createLinearGradient(0, 0, cw, 0);
    const y0 = -s.src.y / sc;
    gr.addColorStop(0, "rgba(190,255,240,0)");
    gr.addColorStop(clamp01((y0 / cw) * 0.95), "rgba(190,255,240,0.55)");
    gr.addColorStop(clamp01((y0 / cw) + 0.25), "rgba(150,240,230,0.22)");
    gr.addColorStop(0.85, "rgba(110,210,220,0)");
    cg.fillStyle = gr;
    cg.beginPath();
    cg.moveTo(0, ch / 2);
    cg.lineTo(cw, ch / 2 - (len * half) / sc);
    cg.lineTo(cw, ch / 2 + (len * half) / sc);
    cg.closePath();
    cg.fill();
    return { c, ang, half, len, ch: ch * sc, ph: R() * 100, a: R.range(0.35, 0.8) };
  });
  // Most rays are still light baked into the water; a few of them breathe
  // and sway on top.
  g.globalCompositeOperation = "lighter";
  for (const [i, ry] of s.rays.entries()) {
    ry.live = i % 3 === 1;
    g.save();
    g.translate(s.src.x, s.src.y);
    g.rotate(ry.ang);
    g.globalAlpha = ry.a * (ry.live ? 0.3 : 0.75);
    g.drawImage(ry.c, 0, -ry.ch / 2, ry.len, ry.ch);
    g.restore();
  }
  g.globalCompositeOperation = "source-over";
  g.globalAlpha = 1;
  // Surface ripple glyphs.
  s.ripIds = ["~", "-", "=", "~", "^"].map((gl, i) => tile(s, gl, null, ["#e8fffa", "#bff4f0", "#d8fff8", "#a8ece8", "#ffffff"][i]));
  s.ripRows = Math.max(3, Math.round((h * 0.07) / cell));
  // Marine snow.
  const snowIds = [".", "'", ".", ",", ":", "*"].map((gl) => tile(s, gl, null, "#d8f4f0"));
  s.snow = Array.from({ length: Math.round(100 * Math.min(1.3, (w * h) / 1.3e6)) + 16 }, () => {
    const near = R() < 0.25;
    return { x: R() * w, y: R() * h, v: R.range(3, 9) * k * (near ? 1.6 : 1), wob: R.range(4, 14) * k, ph: R() * TAU, id: snowIds[Math.floor(R() * (near ? 6 : 4))], a: near ? 0.55 : 0.28 };
  });
  // The whale.
  const L = Math.max(30, Math.min(64, Math.round((w * (port ? 1.2 : 0.5)) / cell)));
  const wh = whaleCells(L);
  s.whaleL = L;
  s.whale = [];
  // The front of the body never bends: it is painted once, both ways round.
  const rigid = (c) => c.i / (L - 1) >= 0.3 && c.part !== "fin";
  const rMin = Math.min(...wh.cells.map((c) => c.r));
  const rMax = Math.max(...wh.cells.map((c) => c.r));
  s.whaleSprite = [0, 1].map((flip) => {
    const c = document.createElement("canvas");
    c.width = Math.ceil(L * cell * s.dpr);
    c.height = Math.ceil((rMax - rMin + 1) * cell * s.dpr);
    const cg = c.getContext("2d");
    cg.scale(s.dpr, s.dpr);
    for (const wc of wh.cells) {
      if (!rigid(wc)) continue;
      const i = flip ? L - 1 - wc.i : wc.i;
      put(cg, s, tile(s, flip ? FLIP[wc.glyph] || wc.glyph : wc.glyph, wc.fill, wc.ink), i * cell, (wc.r - rMin) * cell);
    }
    return c;
  });
  s.whaleR0 = rMin;
  s.whaleH = rMax - rMin + 1;
  for (const c of wh.cells) if (!rigid(c)) s.whale.push({ ...c, id: tile(s, c.glyph, c.fill, c.ink), idF: tile(s, FLIP[c.glyph] || c.glyph, c.fill, c.ink) });
  s.whaleY = h * (port ? 0.3 : 0.3);
  s.whaleT = R() * 40;
  // Fish schools.
  const school = (n, cx, cy, rx, ry, col, period, size) => ({
    cx, cy, rx, ry, period, ph: R() * TAU, size,
    ids: { r: tile(s, ">", null, col), l: tile(s, "<", null, col), t: tile(s, "-", null, shade(col, 1.25)), g: tile(s, ">", null, "#ffffff"), gl: tile(s, "<", null, "#ffffff") },
    fish: Array.from({ length: n }, () => ({ ox: (R() + R() - 1) * size * 1.6, oy: (R() + R() - 1) * size * 0.7, d: R.range(0, 1.4), ph: R() * TAU })),
  });
  s.schools = port
    ? [school(34, w * 0.5, h * 0.55, w * 0.35, h * 0.06, "#cfeef4", 44, 70 * k), school(22, w * 0.55, h * 0.18, w * 0.3, h * 0.04, "#ffd890", 36, 50 * k), school(26, w * 0.5, h * 0.72, w * 0.3, h * 0.03, "#8fd0ff", 30, 46 * k)]
    : [school(28, w * 0.2, h * 0.24, w * 0.1, h * 0.05, "#cfeef4", 46, 60 * k), school(28, w * 0.84, h * 0.24, w * 0.1, h * 0.06, "#ffd890", 38, 60 * k), school(30, w * 0.8, h * 0.6, w * 0.13, h * 0.05, "#8fd0ff", 32, 52 * k)];
  // A bait ball: a slowly turning ring of silver fish.
  s.ball = {
    x: w * (port ? 0.5 : 0.17),
    y: h * (port ? 0.58 : 0.4),
    ids: { r: tile(s, ">", null, "#d8f4f8"), l: tile(s, "<", null, "#d8f4f8"), v: tile(s, "-", null, "#e8fcff"), g: tile(s, "*", null, "#ffffff"), dr: tile(s, ">", null, "#7fb0c0"), dl: tile(s, "<", null, "#7fb0c0") },
    fish: Array.from({ length: port ? 90 : 120 }, () => ({ a: R() * TAU, rad: (port ? 56 : 70) * k * Math.sqrt(R.range(0.45, 1.05)), w: R.range(0.26, 0.34), dy: (R() + R() - 1) * 16 * k, ph: R() * TAU })),
  };
  // Reef: rock rising toward both corners, crowded with coral.
  const [reef, rg] = layer(s);
  s.kelp = [];
  s.anem = [];
  s.lights = [];
  const deep = (c, a) => mixc(c, "#0a3a54", a);
  const sideOf = (c) => Math.abs((c * cell) / w - 0.5) * 2;
  const rockTop = (c) => rows - 1 - Math.round((2 + 11 * Math.pow(sideOf(c), 2.4)) * (0.7 + 0.6 * noise(c * 0.13, 4.2)) * (port ? 0.75 : 1));
  for (let c = 0; c < cols; c += 1) {
    const top = rockTop(c);
    for (let r = top; r < rows; r += 1) {
      const d = r - top;
      const lit = d === 0 ? 1 : d === 1 ? 0.55 : d === 2 ? 0.25 : 0;
      const fill = mixc("#0a2234", "#2a6478", lit * (0.6 + 0.4 * noise(c * 0.4, r * 0.4)));
      const glyph = d === 0 ? (rockTop(c + 1) < top ? "/" : rockTop(c - 1) < top ? "\\" : R.pick(["^", "-", "~", "n"])) : R.pick([".", ":", "%", " ", "o", "."]);
      if (d > 2 && R() < 0.6) {
        if (glyph !== " ") put(rg, s, tile(s, glyph, null, "#163648"), c * cell, r * cell);
        continue;
      }
      put(rg, s, tile(s, glyph, fill, shade(fill, 1.6)), c * cell, r * cell);
    }
  }
  const CORAL = ["#ff7a8a", "#ffa860", "#b890ff", "#f0d870", "#ff8ac0", "#ff6a5a"];
  const branch = (c, r, len, dir, col, depth) => {
    for (let j = 0; j < len; j += 1) {
      const cc = c + Math.round(dir * j * 0.7);
      const gl = dir === 0 ? "|" : dir < 0 ? "\\" : "/";
      put(rg, s, tile(s, j === len - 1 && depth === 0 ? "*" : gl, null, col), cc * cell, (r - j) * cell);
    }
    if (depth > 0) {
      const tc = c + Math.round(dir * (len - 1) * 0.7);
      const tr = r - len;
      put(rg, s, tile(s, "Y", null, shade(col, 1.15)), tc * cell, tr * cell);
      branch(tc - 1, tr - 1, Math.max(2, len - Math.floor(R() * 2)), -1, col, depth - 1);
      branch(tc + 1, tr - 1, Math.max(2, len - Math.floor(R() * 2)), 1, col, depth - 1);
      if (R() < 0.5) branch(tc, tr - 1, Math.max(2, len - 1), 0, col, 0);
    }
  };
  for (let c = 1; c < cols - 1; c += 1) {
    const side = sideOf(c);
    if (R() > 0.06 + 0.5 * side * side) continue;
    const top = rockTop(c);
    const col = deep(R.pick(CORAL), R.range(0.3, 0.48) + 0.2 * (1 - side));
    const kind = R();
    if (kind < 0.3 && side > 0.3) {
      branch(c, top - 1, Math.round(R.range(2, 4)), 0, col, side > 0.7 ? 2 : 1);
      c += 3;
    } else if (kind < 0.5) {
      const wd = Math.round(R.range(2, 4));
      for (let i = -wd; i <= wd; i += 1) {
        const hh = Math.round(Math.sqrt(1 - (i / (wd + 0.5)) ** 2) * wd * 0.6) + 1;
        for (let j = 0; j < hh; j += 1) {
          const rim = j === hh - 1;
          const gl = rim ? (i === -wd ? "(" : i === wd ? ")" : "~") : (i + j) % 2 ? "~" : "S";
          put(rg, s, tile(s, gl, shade(col, rim ? 0.85 : 0.6 - 0.05 * (hh - j)), shade(col, rim ? 1.5 : 1.25)), (c + i) * cell, (rockTop(c + i) - j) * cell);
        }
      }
      c += wd + 1;
    } else if (kind < 0.68 && side > 0.4) {
      // A sea fan: a lattice of glyphs spread on a short stem.
      const rad = Math.round(R.range(3, 6));
      put(rg, s, tile(s, "|", null, shade(col, 0.8)), c * cell, (top - 1) * cell);
      for (let dr = 1; dr <= rad; dr += 1) {
        for (let dc = -rad; dc <= rad; dc += 1) {
          const dd = Math.hypot(dc, dr * 1.15) / rad;
          if (dd > 1 || hash(c + dc, dr, 5) < 0.25) continue;
          put(rg, s, tile(s, dd > 0.85 ? "*" : (dc + dr) % 2 ? "x" : "+", null, mixc(col, "#ffffff", dd > 0.85 ? 0.25 : 0)), (c + dc) * cell, (top - 1 - dr) * cell);
        }
      }
      c += rad + 1;
    } else if (kind < 0.82) {
      // Tube sponges.
      for (let i = 0; i < 3; i += 1) {
        const hh = Math.round(R.range(2, 5));
        for (let j = 0; j < hh; j += 1) put(rg, s, tile(s, j === hh - 1 ? "O" : "|", shade(col, j === hh - 1 ? 1 : 0.7), shade(col, j === hh - 1 ? 0.5 : 1.4)), (c + i) * cell, (rockTop(c + i) - 1 - j) * cell);
      }
      c += 3;
    } else {
      s.anem.push({ x: c * cell, y: (top - 1) * cell, ids: ["w", "W", "v"].map((gl) => tile(s, gl, null, col)), ph: R() * TAU });
    }
    if (R() < 0.4) s.lights.push({ x: (c + Math.round(R.range(-2, 2))) * cell, y: (top - Math.round(R.range(0, 4))) * cell, ph: R() * TAU, p: R.range(3, 7) });
  }
  rg.globalAlpha = 1;
  s.reef = reef;
  s.floor = Array.from({ length: cols }, (_, c) => ({ x: c * cell, y: rockTop(c) * cell }));
  s.causticIds = ["~", "-", "~", "="].map((gl) => tile(s, gl, null, "#b8fff4"));
  s.lightId = tile(s, "o", null, "#8ffff0");
  s.dotId = tile(s, ".", null, "#8ffff0");
  // Kelp: a far row in shadow, a near row of golden fronds, at the edges.
  for (const far of [true, false]) {
    for (let c = 0; c < cols; c += far ? 3 : 4) {
      const side = sideOf(c);
      if (side < (port ? 0.5 : 0.6) || R() < (far ? 0.35 : 0.5)) continue;
      const base = rockTop(c) + 1;
      const H = Math.round(rows * R.range(0.3, far ? 0.6 : 0.8) * Math.pow(side, 1.6));
      if (H < 6) continue;
      const stalkInk = far ? "#1e5048" : "#646034";
      const leaf = far ? ["#123c3c", "#2a6660", "#1e5450"] : ["#242c1a", "#867e44", "#5e6034"];
      const cells = [];
      const ph = R() * TAU;
      const dx = (j) => Math.round(1.4 * Math.sin(j * 0.28 + ph));
      for (let j = 0; j < H; j += 1) {
        const lift = j / H;
        const d0 = dx(j);
        const d1 = dx(j + 1);
        cells.push({ dc: d0, j, lift, id: tile(s, d1 > d0 ? "/" : d1 < d0 ? "\\" : "|", far ? null : "#262e16", stalkInk) });
        if (j > 1 && j % 2 === 0) {
          const sd = (j / 2) % 2 ? 1 : -1;
          const big = j > H * 0.25 && R() < 0.8;
          cells.push({ dc: d0 + sd, j, lift, id: tile(s, sd > 0 ? ")" : "(", leaf[0], leaf[1]) });
          if (big) cells.push({ dc: d0 + sd * 2, j: j + 1, lift, id: tile(s, "~", null, leaf[2]) });

        }
      }
      for (let i = 0; i < 3; i += 1) cells.push({ dc: dx(H) + (i - 1), j: H + (i === 1 ? 1 : 0), lift: 1, id: tile(s, "~", leaf[0], leaf[1]) });
      // Painted once into a strip, then drawn in bands that bend.
      const d0 = Math.min(...cells.map((q) => q.dc));
      const d1 = Math.max(...cells.map((q) => q.dc));
      const top = H + 2;
      const sp = document.createElement("canvas");
      sp.width = Math.ceil((d1 - d0 + 1) * cell * s.dpr);
      sp.height = Math.ceil(top * cell * s.dpr);
      const sg = sp.getContext("2d");
      sg.scale(s.dpr, s.dpr);
      for (const q of cells) put(sg, s, q.id, (q.dc - d0) * cell, (top - 1 - q.j) * cell);
      s.kelp.push({ x: (c + d0) * cell, base: base * cell, sp, top, H, wd: d1 - d0 + 1, far, ph: R() * TAU, amp: R.range(0.8, 1.3) });
    }
  }
  // Bubbles from a vent in the reef.
  const vc = Math.round((w * (port ? 0.82 : 0.9)) / cell);
  s.vent = { x: vc * cell, y: rockTop(vc) * cell, ids: [".", "o", "O", "o"].map((gl) => tile(s, gl, null, "#cfffff")) };
  // Jellyfish.
  s.jellyIds = {
    bell: [".", "-", "~", "-", "."].map((gl) => tile(s, gl, null, "#ffd8f4")),
    body: [tile(s, "(", "#a85aa8", "#ffd0f4"), tile(s, "o", "#d07ac8", "#fff0ff"), tile(s, "~", "#e898dc", "#fff4ff"), tile(s, "o", "#d07ac8", "#fff0ff"), tile(s, ")", "#a85aa8", "#ffd0f4")],
    rim: ["/", "W", "\\"].map((gl) => tile(s, gl, null, "#ffb8ec")),
    ten: ["|", "(", ")", "'", ":"].map((gl) => tile(s, gl, null, "#f0a8e8")),
  };
  s.jx = [w * (port ? 0.18 : 0.08 + R() * 0.1), w * (port ? 0.85 : 0.86 + R() * 0.08)];
  s.seedT = R() * 1000;
  // The deep: everything near the floor sinks into the dark.
  s.deepFog = g.createLinearGradient(0, h * 0.55, 0, h);
  s.deepFog.addColorStop(0, "rgba(1,10,24,0)");
  s.deepFog.addColorStop(1, "rgba(1,10,24,0.5)");
}

function whale(ctx, s, t) {
  const { w, cell, k } = s;
  const P = 80;
  const tt = t + s.whaleT;
  const n = Math.floor(tt / P);
  const p = (tt - n * P) / 62;
  if (p >= 1) return;
  const dir = hash(n, 3, 1) < 0.5 ? 1 : -1;
  const span = w + s.whaleL * cell * 1.2;
  const x0 = dir > 0 ? -s.whaleL * cell * 1.1 + p * span : w + cell * 2 - p * span;
  const y0 = s.whaleY + Math.sin(tt * 0.12) * 12 * k + (hash(n, 5, 2) - 0.5) * 40 * k;
  const beat = Math.sin((tt * TAU) / 7);
  const L = s.whaleL;
  ctx.globalAlpha = 1;
  ctx.drawImage(s.whaleSprite[dir > 0 ? 0 : 1], Math.round(x0), Math.round(y0 + s.whaleR0 * cell), L * cell, s.whaleH * cell);
  for (const c of s.whale) {
    const u = c.i / (L - 1);
    let dy = u < 0.3 ? beat * cell * 1.6 * ((0.3 - u) / 0.3) ** 2 : 0;
    if (c.part === "fin") dy += Math.sin(tt * 0.5) * c.d * cell * 1.6;
    const i = dir > 0 ? c.i : L - 1 - c.i;
    put(ctx, s, dir > 0 ? c.id : c.idF, Math.round(x0 + i * cell), Math.round(y0 + c.r * cell + dy));
  }
  ctx.globalAlpha = 1;
}

export const deepsea = {
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
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(s.back, 0, 0, w, h);
    // A few rays sway and breathe over the still ones.
    ctx.globalCompositeOperation = "lighter";
    const rayNow = [];
    for (const [i, ry] of s.rays.entries()) {
      if (!ry.live) {
        rayNow.push({ ang: ry.ang, half: ry.half, a: ry.a * 0.75 });
        continue;
      }
      const ang = ry.ang + 0.035 * Math.sin(t * 0.13 + ry.ph) + 0.015 * Math.sin(t * 0.31 + ry.ph * 2);
      const a = ry.a * (0.2 + 0.8 * noise(t * 0.15 + ry.ph, i));
      rayNow.push({ ang, half: ry.half, a });
      ctx.save();
      ctx.translate(s.src.x, s.src.y);
      ctx.rotate(ang);
      ctx.globalAlpha = a;
      ctx.drawImage(ry.c, 0, -ry.ch / 2, ry.len * 0.8, ry.ch);
      ctx.restore();
    }
    ctx.globalCompositeOperation = "source-over";
    const lightAt = (x, y) => {
      const an = Math.atan2(y - s.src.y, x - s.src.x);
      let b = 0;
      for (const r of rayNow) {
        const d = Math.abs(an - r.ang) / (r.half * 1.3);
        if (d < 1) b += (1 - d) * r.a;
      }
      return b;
    };
    // The surface, rippling bright overhead.
    const cols = Math.ceil(w / cell) + 1;
    for (let r = 0; r < s.ripRows; r += 1) {
      const fade = 1 - r / s.ripRows;
      for (let c = 0; c < cols; c += 1) {
        const n = noise(c * 0.35 + t * 0.5, r * 0.8 - t * 0.2) * 0.65 + noise(c * 0.9 - t * 0.7, r * 1.3 + t * 0.15) * 0.35;
        if (n < 0.53) continue;
        const near = 1 - Math.min(1, Math.abs(c * cell - s.src.x) / (w * 0.6));
        put(ctx, s, s.ripIds[(c + r * 2 + Math.floor(n * 9)) % 5], c * cell, r * cell, (n - 0.45) * 1.5 * fade * (0.3 + 0.7 * near));
      }
    }
    // Marine snow, glinting where it crosses a ray.
    for (const m of s.snow) {
      const y = ((m.y + m.v * t) % (h + 20)) - 10;
      const x = m.x + m.wob * Math.sin(t * 0.3 + m.ph);
      put(ctx, s, m.id, x, y, m.a * (0.6 + 0.4 * Math.sin(t * 1.3 + m.ph)) + 0.9 * lightAt(x, y));
    }
    whale(ctx, s, t);
    const B = s.ball;
    const by = B.y + 10 * k * Math.sin(t * 0.2);
    for (const f of B.fish) {
      const a = f.a + t * f.w;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      const x = B.x + ca * f.rad * 1.25 + 4 * k * Math.sin(t * 0.9 + f.ph);
      const y = by + sa * f.rad * 0.55 + f.dy + ca * f.rad * 0.18;
      const back = sa < 0;
      const vx = -sa;
      const glint = !back && Math.abs(vx) < 0.35 && Math.sin(t * 5 + f.ph) > 0.5;
      const id = glint ? B.ids.g : Math.abs(vx) < 0.22 ? B.ids.v : vx > 0 ? (back ? B.ids.dr : B.ids.r) : back ? B.ids.dl : B.ids.l;
      put(ctx, s, id, x, y, back ? 0.55 : 0.9);
    }
    // Schools: a lead path, each fish trailing it a little; when the school
    // turns the turn ripples through it and the flanks flash.
    for (const sc of s.schools) {
      for (const f of sc.fish) {
        const tt = t - f.d;
        const a = (tt * TAU) / sc.period + sc.ph;
        const vx = -Math.sin(a);
        const cx = sc.cx + sc.rx * Math.cos(a);
        const cy = sc.cy + sc.ry * Math.sin(a * 2) + 6 * k * Math.sin(tt * 0.9 + f.ph);
        const squeeze = 0.55 + 0.45 * Math.abs(vx);
        const x = cx + f.ox * squeeze + 5 * k * Math.sin(t * 0.7 + f.ph);
        const y = cy + f.oy * (1.4 - squeeze * 0.6) + 3 * k * Math.sin(t * 1.1 + f.ph * 2);
        const turning = Math.abs(vx) < 0.25;
        const glint = turning && Math.sin(t * 6 + f.ph * 3) > 0.4;
        const id = turning && Math.abs(vx) < 0.08 ? sc.ids.t : vx > 0 ? (glint ? sc.ids.g : sc.ids.r) : glint ? sc.ids.gl : sc.ids.l;
        put(ctx, s, id, x, y, 0.75 + 0.25 * lightAt(x, y));
      }
    }
    // Jellyfish rising slowly, pulsing.
    for (const [ji, jx] of s.jx.entries()) {
      const P = 70;
      const tt = t + s.seedT * (ji + 1) * 0.37;
      const n = Math.floor(tt / P);
      const p = (tt - n * P) / 60;
      if (p >= 1) continue;
      const pulse = Math.max(0, Math.sin((tt * TAU) / 3.2));
      const x = jx + 30 * k * Math.sin(tt * 0.15) + (hash(n, ji, 9) - 0.5) * w * 0.06;
      const y = h * (0.85 - 0.75 * p) - pulse * cell * 0.4;
      const fade = Math.min(1, p * 6, (1 - p) * 6);
      const cx = x + cell * 2.5;
      const gl = ctx.createRadialGradient(cx, y + cell, 0, cx, y + cell, cell * 7);
      gl.addColorStop(0, `rgba(255,160,240,${0.26 * fade * (0.6 + 0.4 * pulse)})`);
      gl.addColorStop(1, "rgba(255,160,240,0)");
      ctx.globalAlpha = 1;
      ctx.fillStyle = gl;
      ctx.fillRect(cx - cell * 7, y - cell * 6, cell * 14, cell * 14);
      // The bell squeezes in on each pulse; the tentacles trail and curl.
      const sq = pulse * 0.22;
      const J = s.jellyIds;
      for (let i = 0; i < 5; i += 1) {
        const dx = (i - 2) * cell * (1 - sq);
        put(ctx, s, J.bell[i], cx - cell / 2 + dx * 0.8, y - cell * 0.9, fade * 0.9);
        put(ctx, s, J.body[i], cx - cell / 2 + dx, y, fade * 0.95);
        if (i > 0 && i < 4) put(ctx, s, J.rim[i - 1], cx - cell / 2 + dx * 1.1, y + cell, fade * 0.85);
      }
      for (let i = 0; i < 4; i += 1) {
        for (let j = 0; j < 6; j += 1) {
          const sway = Math.sin(tt * 1.3 - j * 0.8 + i * 1.7) * cell * 0.22 * (j + 1);
          put(ctx, s, J.ten[(i + j + Math.floor(tt * 0.7)) % 5], cx - cell / 2 + (i - 1.5) * cell * (0.9 - sq) + sway, y + cell * (2 + j * (0.85 + 0.15 * pulse)), fade * (0.85 - j * 0.12));
        }
      }
    }
    // Kelp and reef: far stalks, the reef, near stalks; a wave of current
    // rolls up each stalk.
    const surge = (x) => Math.sin(t * 0.45 + x * 0.003) + 0.4 * Math.sin(t * 1.1 + x * 0.011);
    const kelp = (far) => {
      const band = 4;
      for (const kp of s.kelp) {
        if (kp.far !== far) continue;
        const P = s.dpr * cell;
        ctx.globalAlpha = far ? 0.8 : 1;
        const foot = Math.floor(kp.H * 0.2);
        for (let j = 0; j < kp.top; ) {
          const lift = Math.min(1, (j + band / 2) / kp.H);
          const bend = kp.amp * k * (14 * Math.pow(lift, 1.4) * surge(kp.x - lift * 300) + 5 * lift * Math.sin(t * 0.8 + kp.ph - lift * 3));
          const nb = Math.min(j === 0 && foot > band ? foot : band, kp.top - j);
          const y = kp.base - (j + nb) * cell;
          ctx.drawImage(kp.sp, 0, (kp.top - j - nb) * P, kp.sp.width, nb * P, Math.round((kp.x + bend) * s.dpr) / s.dpr, y, kp.wd * cell, nb * cell);
          j += nb;
        }
      }
    };
    kelp(true);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.reef, 0, 0, w, h);
    // Light dancing on the reef tops.
    for (const [c, f] of s.floor.entries()) {
      const n = noise(c * 0.45 + t * 0.7, t * 0.25 + f.y * 0.01);
      if (n > 0.58) put(ctx, s, s.causticIds[(c + Math.floor(t * 2)) & 3], f.x, f.y - cell * 0.35, (n - 0.58) * 2.4 * (0.4 + Math.min(1.2, lightAt(f.x, f.y))));
    }
    for (const an of s.anem) put(ctx, s, an.ids[Math.floor(t * 1.5 + an.ph) % 3], an.x + Math.sin(t * 0.9 + an.ph) * 1.5 * k, an.y);
    for (const l of s.lights) {
      const a = Math.pow(0.5 + 0.5 * Math.sin((t * TAU) / l.p + l.ph), 4);
      put(ctx, s, a > 0.5 ? s.lightId : s.dotId, l.x, l.y, 0.15 + 0.85 * a);
    }
    for (let i = 0; i < 9; i += 1) {
      const life = ((t * 0.07 + i / 9) % 1);
      const y = s.vent.y - life * (s.vent.y + cell);
      const x = s.vent.x + Math.sin(t * 2 + i * 1.7) * 3 * k + life * 30 * k;
      put(ctx, s, s.vent.ids[Math.min(3, Math.floor(life * 6))], x, y, 0.7 * Math.min(1, (1 - life) * 3));
    }
    kelp(false);
    ctx.globalAlpha = 1;
    ctx.fillStyle = s.deepFog;
    ctx.fillRect(0, h * 0.55, w, h * 0.45);
  },
};

// Scene: Rice Terraces. A hillside of flooded paddies seen from high up at
// sunrise: the terraces wrap round the spur of the hill in long curves, each
// a bright mirror of the gold and rose sky held by a grass lip, with a dark
// bank of earth and stone dropping to the next one below, so the steps
// cascade down the screen. The sun's path glitters down through the paddies,
// a breeze ruffles the water in passing patches, young rice sways on the
// planted steps, smoke rises from a thatched hut, a farmer in a conical hat
// plants seedlings, ducks paddle along a terrace, a heron waits on a bund and
// now and then takes off, and a thin mist lies along the far terraces.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade, rgb } from "./tiles.js";

const TAU = Math.PI * 2;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const lerp3 = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const quant = (v, n) => Math.round(v * n) / n;
// The morning sky as the water mirrors it.
const ROSE = rgb("#f49c9a");
const PINK = rgb("#e8949e");
const LILAC = rgb("#b8a2d6");
const GOLD = rgb("#ffc45e");
const SUN = rgb("#fff6dc");
const PALE = rgb("#ffe8d2");
// Banks: earth, stone, moss.
const BANK = ["#6e4e3c", "#7a6652", "#5e6038"];
// The morning haze that far terraces, and the quiet middle of the screen,
// sink into.
const HAZE = "#f4cdb6";
const RIP_INK = ["#b45a66", "#a8742c", "#7a5c9c"];

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 12 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const rows = Math.ceil(h / cell) + 1;
  const S = Math.max(w, h);
  const portrait = h > w;
  const top = h * (portrait ? 0.05 : 0.06);
  const su = (s.su = portrait ? 0.72 : R.range(0.72, 0.8));
  const sx = su * w;
  const cx = R.range(0.42, 0.58);
  const C = portrait ? 2.6 : 4.2;
  const off = R() * 100;
  const sunLit = (u) => Math.exp(-(((u - su) / 0.45) ** 2));
  // Spacing between lips grows toward the viewer.
  const spAt = (y) => cell * (3.6 + 11 * Math.pow(clamp01((y - top) / (h - top)), 1.25));
  // How far a tile at (x, y) sinks into the haze: with distance up the hill,
  // and in the middle of the screen, behind the transcript.
  const calm = (x, y) => 0.42 * Math.exp(-(((x - w / 2) / (0.3 * w)) ** 2) - (((y - h * 0.5) / (0.32 * h)) ** 2));
  const veil = (x, y) => clamp01(0.4 * (1 - clamp01((y - top) / (h * 0.55))) + calm(x, y));
  const bowl = (u) => Math.pow(clamp01(1 - ((u - cx) / 0.62) ** 2), 1.4);
  // Lips, far to near, as curves wrapping round the spur; T[0] is the crest.
  const T = [];
  let y = top;
  for (let j = 0; j < 90; j += 1) {
    const sp = spAt(y);
    const L = new Float32Array(cols + 2);
    let lo = Infinity;
    for (let c = -1; c <= cols; c += 1) {
      const u = ((c + 0.5) * cell) / w;
      L[c + 1] = y + sp * (C * Math.min(1, 0.4 + 0.12 * j) * bowl(u) + 1.8 * (noise(u * 3 + off, j * 0.06) - 0.5) + 0.3 * (noise(u * 10 + off, j * 0.25) - 0.5));
      lo = Math.min(lo, L[c + 1]);
    }
    T.push({ sp, L });
    if (lo > h + cell) break;
    y += sp * R.range(0.88, 1.12);
  }
  const row = (j, c) => Math.round(T[j].L[Math.max(0, Math.min(cols + 1, c + 1))] / cell);
  // A lip fills down the column where it steps steeply, to stay unbroken.
  const lipEnd = (j, c) => Math.max(row(j, c), Math.max(row(j, c - 1), row(j, c + 1)) - 1);
  const wallN = (j) => Math.max(1, Math.round((T[j].sp * 0.3) / cell));
  const crestAt = (x) => T[0].L[Math.max(0, Math.min(cols + 1, Math.round(x / cell - 0.5) + 1))];
  const kind = T.map((_, j) => {
    const v = hash(j, 5, s.seed & 0xffff);
    return j === 0 ? "crest" : v < 0.74 ? "flood" : "plant";
  });
  // The hut stands on a grassy terrace on the left.
  const hutC = Math.round(cols * (portrait ? 0.14 : R.range(0.08, 0.13)));
  let hutJ = 1;
  for (let j = 1; j < T.length; j += 1) if (T[j].L[hutC + 1] < h * (portrait ? 0.4 : 0.44)) hutJ = j;
  // Only the hut's yard is dry grass.
  const yard = Math.max(3, Math.round((4.5 * Math.max(5, 17 * k)) / cell));
  const kindAt = (j, c) => (j === hutJ && Math.abs(c - hutC) < yard ? "green" : kind[j]);
  // Per column: the bank under each lip, then the paddy down to the next lip.
  const water = [];
  const walls = [];
  for (let c = 0; c < cols; c += 1) {
    for (let j = 1; j < T.length; j += 1) {
      const a = lipEnd(j - 1, c) + 1;
      const next = row(j, c);
      const wb = Math.min(a + wallN(j - 1), next);
      if (wb > a) walls.push([c, j - 1, a, wb]);
      if (next > wb) water.push([c, j, wb, next]);
    }
  }
  const [back, g] = layer(s);
  // Sky, sun and far ridges above the crest.
  const skyH = Math.max(...T[0].L);
  const sky = g.createLinearGradient(0, 0, 0, skyH);
  sky.addColorStop(0, "#eca4a4");
  sky.addColorStop(0.45, "#f8c29e");
  sky.addColorStop(1, "#ffe2aa");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // The far ridges, then the sun just risen over the nearer one.
  const ridgeY = ["#cfa6bc", "#b796b4"].map((col, i) => {
    const o = R() * 50;
    const at = (x) => crestAt(x) - top * (0.85 - i * 0.4) - top * 0.4 * noise(x / (150 * Math.max(0.4, k)) + o, i);
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w + 6; x += 6) g.lineTo(x, at(x));
    g.lineTo(w, h);
    g.closePath();
    g.fillStyle = col;
    g.fill();
    return at;
  });
  const sr = Math.max(3, 0.016 * S);
  const sy = Math.max(sr * 1.2, ridgeY[1](sx) - sr * 0.35);
  const glow = g.createRadialGradient(sx, sy, 0, sx, sy, S * 0.4);
  glow.addColorStop(0, "rgba(255,244,210,1)");
  glow.addColorStop(0.1, "rgba(255,222,160,0.5)");
  glow.addColorStop(1, "rgba(255,210,160,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, skyH + cell);
  g.fillStyle = "#fff8e4";
  g.beginPath();
  g.arc(sx, sy, sr, Math.PI, 0);
  g.lineTo(sx + sr, sy + sr * 0.35);
  g.lineTo(sx - sr, sy + sr * 0.35);
  g.fill();
  // The hill under the crest.
  g.beginPath();
  g.moveTo(0, h);
  for (let c = 0; c <= cols; c += 1) g.lineTo(c * cell, T[0].L[c + 1]);
  g.lineTo(w, h);
  g.closePath();
  g.fillStyle = "#6e7e4a";
  g.fill();
  // The paddies mirror the sky: painted once at half a tile, scaled up.
  const q = Math.max(2, cell / 2);
  const iw = Math.ceil(w / q) + 1;
  const ih = Math.ceil(h / q) + 1;
  const rc = document.createElement("canvas");
  rc.width = iw;
  rc.height = ih;
  const rcg = rc.getContext("2d");
  const img = rcg.createImageData(iw, ih);
  for (let j = 0; j < ih; j += 1) {
    for (let i = 0; i < iw; i += 1) {
      const u = (i * q) / w;
      const v = (j * q) / h;
      let c = lerp3(ROSE, LILAC, clamp01(v * 1.4 - 0.5));
      c = lerp3(c, PINK, 0.45 * noise(u * 2.2 + off, v * 2.5));
      c = lerp3(c, GOLD, Math.exp(-(((u - su) / 0.34) ** 2)) * (0.9 - 0.4 * v));
      c = lerp3(c, PALE, 0.5 * clamp01((noise(u * 4 + off, v * 7 + 3) - 0.45) * 3));
      c = lerp3(c, SUN, 0.92 * Math.exp(-(((u - su) / (0.022 + 0.05 * v)) ** 2)));
      const o = (j * iw + i) * 4;
      img.data[o] = c[0];
      img.data[o + 1] = c[1];
      img.data[o + 2] = c[2];
      img.data[o + 3] = 255;
    }
  }
  rcg.putImageData(img, 0, 0);
  const strip = document.createElement("canvas");
  strip.width = 1;
  strip.height = 32;
  const stg = strip.getContext("2d");
  const sgr = stg.createLinearGradient(0, 0, 0, 32);
  sgr.addColorStop(0, "rgba(80,44,48,0.42)");
  sgr.addColorStop(0.3, "rgba(96,60,66,0.16)");
  sgr.addColorStop(1, "rgba(90,56,60,0)");
  stg.fillStyle = sgr;
  stg.fillRect(0, 0, 1, 32);
  const mirror = new Path2D();
  for (const [c, j, a, b] of water) if (kindAt(j, c) !== "green") mirror.rect(c * cell, a * cell, cell, (b - a) * cell);
  g.save();
  g.clip(mirror);
  g.imageSmoothingEnabled = true;
  g.drawImage(rc, 0, 0, iw * q, ih * q);
  // Each bank lies reflected along the back of the paddy below it, traced
  // along the smooth curve so it does not stair-step.
  const sw = Math.max(1, 3 * k);
  for (let j = 1; j < T.length; j += 1) {
    const hh = wallN(j - 1) * cell * 1.5 + cell * 0.6;
    for (let x = 0; x < w; x += sw) {
      const fx = x / cell - 0.5;
      const i0 = Math.max(0, Math.min(cols, Math.floor(fx) + 1));
      const yb = T[j - 1].L[i0] + (T[j - 1].L[i0 + 1] - T[j - 1].L[i0]) * (fx - Math.floor(fx));
      g.drawImage(strip, x, yb + (wallN(j - 1) + 0.5) * cell, sw, hh);
    }
  }
  g.restore();
  s.water = [];
  s.shoots = [];
  s.glints = [];
  for (const [c, j, a, b] of water) {
    const x = c * cell;
    const u = (x + cell / 2) / w;
    if (kindAt(j, c) === "green") {
      const f = mixc(shade(mixc("#6a9448", "#7ca456", quant(noise(c * 0.2 + off, j), 4)), quant(0.88 + 0.22 * sunLit(u), 6)), HAZE, 0.2);
      g.fillStyle = f;
      g.fillRect(x, a * cell, cell, (b - a) * cell);
      for (let r = a; r < b; r += 1) if (hash(c, r, 6) < 0.22) put(g, s, tile(s, hash(c, r, 7) < 0.5 ? "\"" : ",", null, shade(f, 0.8)), x, r * cell);
      continue;
    }
    if (kind[j] === "plant") {
      g.fillStyle = "rgba(80,150,60,0.14)";
      g.fillRect(x, a * cell, cell, (b - a) * cell);
    }
    // The bank above lies reflected along the back of the water; the brim
    // catches the light.
    g.globalAlpha = 1;
    g.fillStyle = "rgba(255,250,236,0.55)";
    g.fillRect(x, b * cell - Math.max(1, 2 * k), cell, Math.max(1, 2 * k));
    for (let r = a; r < b; r += 1) {
      const yy = r * cell;
      if (yy > h) break;
      const v = yy / h;
      const ri = Math.abs(u - su) < 0.2 ? 1 : v > 0.55 ? 2 : 0;
      const p = { x, y: yy, ri, j, c, a, b };
      s.water.push(p);
      if (Math.abs(u - su) < 0.022 + 0.05 * v + 0.01) s.glints.push(p);
      if (kind[j] === "plant" && r > a && (c + r) % 2 === 0) s.shoots.push({ x, y: yy, g: hash(c, r, 12) });
    }
  }
  if (s.shoots.length > 420) s.shoots = s.shoots.filter((_, i) => i % Math.ceil(s.shoots.length / 420) === 0);
  // Banks: grass hanging over the top, earth and stone below.
  for (const [c, j, a, b] of walls) {
    const u = ((c + 0.5) * cell) / w;
    const d = row(j, c + 1) - row(j, c - 1);
    const face = d < 0 ? 0.12 : d > 0 ? -0.1 : 0;
    const base = BANK[Math.floor(noise(c * 0.15 + off, j * 0.7) * 2.99)];
    for (let r = a; r < b; r += 1) {
      const lit = quant(0.8 + 0.26 * sunLit(u) + face + (r === a ? 0.06 : 0) - 0.12 * ((r - a) / Math.max(1, b - a)), 10);
      const fill = mixc(shade(base, lit), HAZE, quant(veil(c * cell, r * cell), 6));
      // Stone courses show as a sparse mark; most of the bank is bare earth.
      const gl = r === a ? (hash(c, r, 1) < 0.3 ? "," : " ") : hash(c, r, 2) < 0.16 ? ":" : " ";
      put(g, s, tile(s, gl, fill, r === a ? mixc(fill, "#8ab860", 0.6) : shade(fill, 1.22)), c * cell, r * cell);
    }
  }
  // Lips: a bund of grass tiles, glyphs running with the contour; the crest
  // is a line of trees.
  for (let j = 0; j < T.length; j += 1) {
    for (let c = 0; c < cols; c += 1) {
      const r0 = row(j, c);
      const r1 = lipEnd(j, c);
      if (r0 * cell > h) continue;
      const u = ((c + 0.5) * cell) / w;
      const d = row(j, c + 1) - row(j, c - 1);
      for (let r = r0; r <= r1; r += 1) {
        if (j === 0) {
          const f = mixc(shade("#5c7c52", quant(0.88 + 0.24 * noise(c * 0.4, r), 6)), HAZE, 0.38);
          put(g, s, tile(s, hash(c, r, 3) < 0.2 ? "^" : " ", f, shade(f, 1.25)), c * cell, r * cell);
          if (r === r0 && hash(c, 0, 4) < 0.4) put(g, s, tile(s, "^", null, mixc("#6e8a66", HAZE, 0.3)), c * cell, (r - 1) * cell);
          continue;
        }
        // Glyphs only where the bund steps: the slope reads, the flat runs stay calm.
        const gl = r1 > r0 ? (d > 0 ? "\\" : d < 0 ? "/" : "|") : d >= 2 ? "\\" : d <= -2 ? "/" : hash(c, j, 8) < 0.12 ? "-" : " ";
        const lit = quant(0.88 + 0.24 * sunLit(u) + 0.06 * (hash(c, r, 9) - 0.5), 8);
        // Far bunds pale with distance, near ones a deep green.
        const near = quant(clamp01((r * cell) / h), 4);
        const fill = mixc(shade(mixc(mixc("#94bc6a", "#aecb7c", quant(noise(c * 0.3, j), 3)), "#4c7a38", near * 0.7), lit), HAZE, quant(veil(c * cell, r * cell), 6));
        put(g, s, tile(s, gl, fill, shade(fill, 0.7)), c * cell, r * cell);
      }
    }
  }
  g.globalAlpha = 1;
  const minCrest = Math.min(...T[0].L);
  dotGrid(g, s, "#7a5048", 0.18);
  // Figures get their own, larger tiles so they read at a glance.
  const fig = (s.fig = { w, h, dpr: s.dpr });
  tiles(fig, Math.max(5, 17 * k));
  const F = fig.cell;
  // The hut: thatch lit from the right, a warm window.
  const HUT = ["   ^   ", "  /%\\  ", " /%%%\\ ", "/%%%%%\\", " |#|=| "];
  let hutBase = rows;
  for (let i = -3; i <= 3; i += 1) hutBase = Math.min(hutBase, row(hutJ, hutC + i));
  hutBase -= 1;
  HUT.forEach((line, r) => Array.from(line).forEach((ch, i) => {
    if (ch === " ") return;
    const lit = i >= 3;
    let fill = lit ? "#e8be6a" : "#a87a3c";
    if (ch === "^") fill = "#f4d080";
    if (ch === "|") fill = "#6e4c2e";
    if (ch === "#") fill = "#ffd070";
    if (ch === "=") fill = "#4e3220";
    const ink = ch === "#" ? "#b0601a" : ch === "|" || ch === "=" ? "#c09062" : shade(fill, ch === "%" ? 0.84 : 0.6);
    put(g, fig, tile(fig, ch === "%" ? "'" : ch, fill, ink), hutC * cell + (i - 3) * F, (hutBase + 1) * cell - (5 - r) * F);
  }));
  s.hut = { x: hutC * cell, y: (hutBase + 1) * cell - 6 * F, win: { x: hutC * cell - F / 2, y: (hutBase + 1) * cell - F / 2 } };
  // Palms standing on the bunds near the edges: trunks painted, crowns sway.
  s.palms = [];
  const palmCols = portrait ? [hutC + 5, Math.round(cols * 0.88)] : [hutC - 4, hutC + 6, Math.round(cols * 0.9), Math.round(cols * 0.95), Math.round(cols * 0.84)];
  for (const pc of palmCols) {
    if (pc < 1 || pc > cols - 2) continue;
    const ty = h * R.range(0.3, 0.78);
    let best = 1;
    for (let j = 1; j < T.length; j += 1) if (Math.abs(T[j].L[pc + 1] - ty) < Math.abs(T[best].L[pc + 1] - ty)) best = j;
    const base = row(best, pc);
    const th = 3 + Math.floor(R() * 3);
    const lean = R.range(-0.6, 0.6) * F;
    for (let r = 1; r <= th; r += 1) put(g, fig, tile(fig, r % 2 ? ")" : "(", null, "#8a6640"), pc * cell + lean * (r / th) ** 2, base * cell - r * F);
    s.palms.push({ x: pc * cell + lean, y: base * cell - (th + 1) * F, ph: R() * TAU });
  }
  g.globalAlpha = 1;
  s.back = back;
  const CROWN = [",\\|/,", "=-*-=", "'/|\\'"];
  s.crown = CROWN.flatMap((line, r) => Array.from(line).map((ch, i) => (ch === " " ? null : { dc: i - 2, dr: r - 1, lift: r === 1 && i === 2 ? 0 : 1, id: ch === "*" ? tile(fig, "*", "#5a3e1c", "#f0c060") : tile(fig, ch, null, i > 2 || (i === 2 && r === 0) ? "#78b848" : "#2c642a") })).filter(Boolean));
  // Ripples, glints, young rice.
  s.rip = RIP_INK.map((ink) => ["~", "-", "~", "="].map((gl) => tile(s, gl, null, ink)));
  s.glint = [tile(s, "*", null, "#fffbe8"), tile(s, "+", null, "#fff0c0"), tile(s, "-", null, "#ffffff"), tile(s, "=", null, "#fff4d0")];
  s.shoot = ["\"", "'", ",", "v"].map((gl, i) => tile(s, gl, null, ["#3a7e22", "#4a8e2a", "#3a7420", "#56a034"][i]));
  s.smokeIds = ["~", "(", ")", "~", "."].map((gl) => tile(fig, gl, null, "#f6eee6"));
  // The farmer, wading in a paddy on the right.
  const inBox = (p, x0, x1, y0, y1) => p.x > w * x0 && p.x < w * x1 && p.y > h * y0 && p.y < h * y1;
  const fc = s.water.filter((p) => kind[p.j] === "flood" && p.b - p.a >= 5 && p.y === (p.b - 2) * cell && inBox(p, portrait ? 0.55 : 0.74, portrait ? 0.82 : 0.9, 0.58, 0.86));
  const fp = fc.length ? fc[Math.floor(R() * fc.length)] : { x: w * 0.8, y: h * 0.72, j: -1, c: Math.round(cols * 0.8) };
  s.farmer = {
    x: fp.x,
    y: fp.y,
    hat: [[-1, -2, "/", "#e8c87a"], [0, -2, "^", "#fbe6a6"], [1, -2, "\\", "#c49a52"], [-2, -1, "/", "#b8904a"], [-1, -1, "_", "#d4b064"], [0, -1, "_", "#e6c474"], [1, -1, "_", "#f0d488"], [2, -1, "\\", "#c8a058"]].map(([dc, dr, gl, f]) => ({ dc, dr, id: tile(fig, gl, f, shade(f, 0.55)) })),
    body: [[-1, "(", "#3a5a9a"], [0, "#", "#2c4c88"], [1, ")", "#3a5a9a"]].map(([dc, gl, f]) => ({ dc, id: tile(fig, gl, f, "#9ab8e8") })),
    arm: tile(fig, "/", null, "#2c4c88"),
    bundle: tile(fig, ",", null, "#4f9a2e"),
    refl: tile(fig, "#", null, "#7a8ab8"),
  };
  // Seedlings already planted behind the farmer.
  for (const p of s.water) if (p.j === fp.j && p.c < fp.c - 1 && p.c > fp.c - 9 && (p.c + Math.round(p.y / cell)) % 2 === 0) s.shoots.push({ x: p.x, y: p.y, g: hash(p.c, p.y, 13) });
  // Ducks: the longest open stretch of water on a terrace at the lower left.
  let run = [];
  const byJ = new Map();
  for (const [c, j, a, b] of water) {
    if (kind[j] !== "flood" || b - a < 2 || c > cols * (portrait ? 0.5 : 0.34) || a * cell < h * 0.5 || b * cell > h * 0.95) continue;
    if (!byJ.has(j)) byJ.set(j, []);
    byJ.get(j).push([c, ((a + b) / 2 - 0.5) * cell]);
  }
  // Prefer a long, level stretch.
  const score = (r) => r.length - (r.length ? Math.abs(r[r.length - 1][1] - r[0][1]) / cell : 0) * 1.5;
  for (const list of byJ.values()) {
    let cur = [];
    for (const p of list) {
      if (cur.length && (p[0] !== cur[cur.length - 1][0] + 1 || Math.abs(p[1] - cur[cur.length - 1][1]) > cell)) cur = [];
      cur.push(p);
      if (score(cur) > score(run)) run = cur.slice();
    }
  }
  s.duckPath = run.map(([c, yy]) => [c * cell, yy]);
  s.duck = { body: tile(fig, "=", "#fbf6ea", "#c8c0b0"), head: tile(fig, "o", "#fbf6ea", "#3a3632"), beakR: tile(fig, ">", null, "#e89a2a"), beakL: tile(fig, "<", null, "#e89a2a") };
  // The heron on a bund at the upper right.
  const hcC = Math.round(cols * (portrait ? 0.8 : 0.87));
  let hj = 1;
  for (let j = 1; j < T.length; j += 1) if (Math.abs(T[j].L[hcC + 1] - h * 0.36) < Math.abs(T[hj].L[hcC + 1] - h * 0.36)) hj = j;
  s.heronAt = { x: hcC * cell, y: row(hj, hcC) * cell };
  s.heron = {
    head: tile(fig, "o", "#f8f8f2", "#4a5058"),
    beak: tile(fig, "-", null, "#e8b030"),
    neck: tile(fig, "S", "#f8f8f2", "#8a9098"),
    tail: tile(fig, "<", "#e2e4e0", "#8a9098"),
    body: tile(fig, "O", "#f8f8f4", "#9aa2aa"),
    legs: tile(fig, "|", null, "#3a3a34"),
    up: tile(fig, "/", null, "#ffffff"),
    dn: tile(fig, "\\", null, "#ffffff"),
    tip: tile(fig, "-", null, "#ffffff"),
  };
  // A thin mist lying along the far terraces.
  s.mist = Array.from({ length: 6 }, () => {
    const mw = R.range(260, 520) * k;
    const mh = mw * R.range(0.1, 0.16);
    const blur = Math.max(2, 14 * k);
    const pad = blur * 3;
    const c = document.createElement("canvas");
    c.width = Math.ceil((mw + pad * 2) * 0.5);
    c.height = Math.ceil((mh + pad * 2) * 0.5);
    const mg = c.getContext("2d");
    mg.scale(0.5, 0.5);
    mg.filter = `blur(${blur}px)`;
    mg.fillStyle = `rgba(255,246,238,${R.range(0.45, 0.65)})`;
    for (let i = 0; i < 5; i += 1) {
      mg.beginPath();
      mg.ellipse(pad + mw * R.range(0.2, 0.8), pad + mh * 0.5, mw * R.range(0.15, 0.3), mh * R.range(0.3, 0.45), 0, 0, TAU);
      mg.fill();
    }
    return { c, w: mw + pad * 2, h: mh + pad * 2, x: R() * w, y: minCrest + R.range(0.5, 1) * h * 0.09, v: R.range(4, 9) * k * R.sign(), ph: R() };
  });
  s.bird = [tile(s, "v", null, "#7a5468"), tile(s, "-", null, "#7a5468")];
  s.skyY = top * 0.6;
  s.flight = R() * 1000;
}

export const terraces = {
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
    const { ctx, w, h, cell, k } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    // A breeze ruffles the water in patches that drift across.
    const n = s.water.length;
    if (n) {
      for (let i = 0; i < 190; i += 1) {
        const u = t / 4 + i * 0.618;
        const slot = Math.floor(u);
        const p = s.water[Math.floor(hash(i, slot, 3) * n)];
        const br = noise((p.x * 0.004) / k - t * 0.06, (p.y * 0.006) / k + t * 0.01);
        if (br < 0.4) continue;
        const life = u - slot;
        put(ctx, s, s.rip[p.ri][(i + slot) % 4], p.x + (life - 0.5) * cell * 0.7, p.y, Math.min(0.9, Math.sin(life * Math.PI) * (br - 0.38) * 4));
      }
    }
    // The sun's path glitters down through the paddies.
    const gn = s.glints.length;
    if (gn) {
      for (let i = 0; i < 46; i += 1) {
        const u = t / 1.8 + i * 0.37;
        const slot = Math.floor(u);
        const p = s.glints[Math.floor(hash(i, slot, 9) * gn)];
        put(ctx, s, s.glint[(i + slot) % 4], p.x, p.y, Math.sin((u - slot) * Math.PI));
      }
    }
    // Young rice leans as the breeze rolls down the hill.
    for (const p of s.shoots) {
      const wv = Math.sin(t * 1.3 - (p.x * 0.01) / k - (p.y * 0.006) / k) + 0.4 * Math.sin(t * 2.3 + p.g * 9);
      put(ctx, s, s.shoot[wv > 0.7 ? 1 : wv < -0.7 ? 2 : p.g > 0.5 ? 0 : 3], p.x + wv * 1.4 * k, p.y, 0.95);
    }
    for (const p of s.palms) {
      const sw = Math.sin(t * 0.9 + p.ph) * 1.2 * k;
      for (const c of s.crown) put(ctx, s.fig, c.id, p.x + c.dc * s.fig.cell + sw * c.lift, p.y + c.dr * s.fig.cell + sw * 0.4 * c.lift * c.dr);
    }
    hut(ctx, s, t);
    farmer(ctx, s, t);
    ducks(ctx, s, t);
    heron(ctx, s, t);
    birds(ctx, s, t);
    for (const m of s.mist) {
      const span = w + m.w;
      const x = ((((m.x + m.v * t) % span) + span) % span) - m.w;
      ctx.globalAlpha = 0.55 + 0.35 * Math.sin(t * 0.05 + m.ph * TAU);
      ctx.drawImage(m.c, x, m.y - m.h / 2, m.w, m.h);
    }
    ctx.globalAlpha = 1;
  },
};

function hut(ctx, s, t) {
  const cell = s.fig.cell;
  const W = s.hut.win;
  const fl = 0.8 + 0.12 * Math.sin(t * 7.3) + 0.08 * Math.sin(t * 11.1);
  const gl = ctx.createRadialGradient(W.x, W.y, 0, W.x, W.y, cell * 2.5);
  gl.addColorStop(0, `rgba(255,200,110,${0.35 * fl})`);
  gl.addColorStop(1, "rgba(255,200,110,0)");
  ctx.globalAlpha = 1;
  ctx.fillStyle = gl;
  ctx.fillRect(W.x - cell * 2.5, W.y - cell * 2.5, cell * 5, cell * 5);
  for (let i = 0; i < 7; i += 1) {
    const life = (t / 8 + i / 7) % 1;
    const x = s.hut.x + Math.sin(life * 4 + t * 0.4 + i) * cell * 0.6 + life * life * cell * 4;
    put(ctx, s.fig, s.smokeIds[i % 5], x, s.hut.y - life * cell * 8, 0.75 * (1 - life) * Math.min(1, life * 8));
  }
  ctx.globalAlpha = 1;
}

function farmer(ctx, s, t) {
  const { k } = s;
  const cell = s.fig.cell;
  const f = s.farmer;
  // Bends to plant every few seconds; each dip sends out a ring.
  const dip = Math.max(0, Math.sin(((t % 4.5) / 4.5) * TAU));
  const y = f.y + dip * cell * 0.3;
  ctx.strokeStyle = "#fffaf0";
  ctx.lineWidth = Math.max(0.6, 1.2 * k);
  for (let i = 0; i < 2; i += 1) {
    const age = ((t + i * 2.25) % 4.5) / 4.5;
    ctx.globalAlpha = 0.65 * (1 - age);
    ctx.beginPath();
    ctx.ellipse(f.x + cell * 0.5, f.y + cell, cell * (1.6 + age * 4), cell * (0.5 + age * 1.3), 0, 0, TAU);
    ctx.stroke();
  }
  put(ctx, s.fig, f.refl, f.x, f.y + cell * 1.1, 0.3);
  for (const b of f.body) put(ctx, s.fig, b.id, f.x + b.dc * cell, y);
  put(ctx, s.fig, f.arm, f.x + cell * (1.6 + dip * 0.2), y + cell * (0.3 + dip * 0.5));
  if (dip < 0.3) put(ctx, s.fig, f.bundle, f.x - cell * 1.7, y + cell * 0.2);
  for (const c of f.hat) put(ctx, s.fig, c.id, f.x + c.dc * cell, y + c.dr * cell * 0.85);
  ctx.globalAlpha = 1;
}

function ducks(ctx, s, t) {
  const P = s.duckPath;
  if (P.length < 8) return;
  const cell = s.fig.cell;
  const n = P.length - 1;
  // Along the terrace and back, slowly.
  const u = (t / (n * 1.4)) % 2;
  const fwd = u < 1;
  const head = (fwd ? u : 2 - u) * (n - 6) + (fwd ? 6 : 0);
  const at = (f) => {
    const i = Math.max(0, Math.min(n - 1, Math.floor(f)));
    const fr = Math.max(0, Math.min(1, f - i));
    return [P[i][0] + (P[i + 1][0] - P[i][0]) * fr, P[i][1] + (P[i + 1][1] - P[i][1]) * fr];
  };
  const D = s.duck;
  for (let j = 0; j < 5; j += 1) {
    const [x, y0] = at(head + (fwd ? -j * 1.4 : j * 1.4));
    const y = y0 + Math.sin(t * 3 + j) * 0.6;
    const dir = fwd ? 1 : -1;
    put(ctx, s, s.rip[0][(j + Math.floor(t * 2)) % 4], x - dir * cell * 1.3, y + cell * 0.2, 0.55);
    put(ctx, s.fig, D.body, x - dir * cell * 0.55, y);
    put(ctx, s.fig, D.head, x + dir * cell * 0.4, y - cell * 0.35);
    put(ctx, s.fig, fwd ? D.beakR : D.beakL, x + dir * cell * 1.05, y - cell * 0.35);
  }
  ctx.globalAlpha = 1;
}

function heron(ctx, s, t) {
  const { w, h } = s;
  const cell = s.fig.cell;
  const P = 64;
  const n = Math.floor((t + s.flight) / P);
  const p = (t + s.flight - n * P) / 22;
  const H = s.heron;
  const at = s.heronAt;
  if (p >= 1) {
    const nod = Math.sin(t * 0.7 + s.flight) > 0.85 ? cell * 0.3 : 0;
    put(ctx, s.fig, H.beak, at.x - cell, at.y - cell * 4 + nod);
    put(ctx, s.fig, H.head, at.x, at.y - cell * 4 + nod * 0.5);
    put(ctx, s.fig, H.neck, at.x, at.y - cell * 3);
    put(ctx, s.fig, H.tail, at.x + cell, at.y - cell * 2);
    put(ctx, s.fig, H.body, at.x, at.y - cell * 2);
    put(ctx, s.fig, H.legs, at.x, at.y - cell);
    ctx.globalAlpha = 1;
    return;
  }
  // Out over the paddies in a long loop and back to the bund.
  const ex = w * (0.25 + hash(n, 1, 77) * 0.25);
  const ey = h * (0.5 + hash(n, 2, 77) * 0.25);
  const q = Math.sin(p * Math.PI);
  const x = at.x + (ex - at.x) * q;
  const y = at.y - cell * 3 + (ey - at.y) * q - Math.sin(p * TAU) * h * 0.06 - q * h * 0.04;
  const dir = p < 0.5 ? -1 : 1;
  const up = Math.sin(t * 6) > 0;
  ctx.globalAlpha = 0.2;
  ctx.fillStyle = "#3a2a30";
  ctx.beginPath();
  ctx.ellipse(x + cell * 3, y + h * 0.07, cell * 2.4, cell * 0.6, 0, 0, TAU);
  ctx.fill();
  const wy = up ? -cell * 0.45 : cell * 0.3;
  put(ctx, s.fig, H.tip, x - cell * 2.2, y + wy, 0.95);
  put(ctx, s.fig, up ? H.dn : H.up, x - cell * 1.1, y + wy * 0.5);
  put(ctx, s.fig, H.body, x, y);
  put(ctx, s.fig, up ? H.up : H.dn, x + cell * 1.1, y + wy * 0.5);
  put(ctx, s.fig, H.tip, x + cell * 2.2, y + wy, 0.95);
  put(ctx, s.fig, H.head, x + dir * cell * 1.2, y - cell * 0.6);
  put(ctx, s.fig, H.beak, x + dir * cell * 2, y - cell * 0.6);
  ctx.globalAlpha = 1;
}

function birds(ctx, s, t) {
  const P = 47;
  const n = Math.floor(t / P);
  const p = (t - n * P) / 26;
  if (p > 1) return;
  const { w, cell } = s;
  const dir = hash(n, 1, 5) < 0.5 ? 1 : -1;
  const x = dir > 0 ? -60 + p * (w + 120) : w + 60 - p * (w + 120);
  const y = s.skyY * (0.6 + hash(n, 2, 5));
  for (let b = 0; b < 5; b += 1) {
    const by = y + (b % 2 ? 1 : -1) * Math.ceil(b / 2) * cell * 0.6 + Math.sin(t * 1.1 + b * 2) * 2;
    put(ctx, s, s.bird[Math.sin(t * 6 + b * 1.3) > 0 ? 0 : 1], x - dir * b * cell * 1.2, by, 0.8);
  }
  ctx.globalAlpha = 1;
}

// Scene: Alpine Meadow. Down in the grass at golden hour, eye to eye with the
// flowers: big poppies, daisies, lupine spikes and cornflowers in tile art
// stand in the bottom corners, single blades of grass rise as glyph strokes
// silhouetted against the low sun, their tips glowing where it shines
// through them. Waves of wind roll across the meadow as a passing sheen and
// bend every blade, streaks of glyph cloud glow gold underneath, a jagged
// range far off catches the last pink light, seeds drift up through the
// light and butterflies wander between the flowers.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const quant = (v, n) => Math.round(v * n) / n;
const atlas = (s, cell) => {
  const A = { w: s.w, h: s.h, dpr: s.dpr };
  tiles(A, cell);
  return A;
};

// Flower heads as tile art. Each art is rows of keys; the key maps to
// [glyph, fill or null, ink]. Row 0 is the top of the head.
const FLOWERS = {
  poppy: {
    art: [",(v),", "(c@c)", " \\_/ "],
    key: { ",": [",", null, "#ffa070"], "(": ["(", "#f04a2c", "#ffb090"], ")": [")", "#f04a2c", "#ffb090"], v: ["v", "#ff8a50", "#ffe0c0"], c: ["~", "#ff5a34", "#ffb894"], "@": ["@", "#2a1216", "#ffd860"], "\\": ["\\", "#c42a1a", "#ff8060"], _: ["_", "#b02216", "#ff7050"], "/": ["/", "#c42a1a", "#ff8060"] },
    edge: { 0: [0, 4], 1: [0, 4] },
  },
  daisy: {
    art: [" \\ | / ", "--(O)--", " / | \\ "],
    key: { "\\": ["\\", null, "#ffffff"], "/": ["/", null, "#ffffff"], "|": ["|", null, "#fffaf0"], "-": ["-", null, "#ffffff"], "(": ["(", "#ffc81a", "#a06a00"], ")": [")", "#ffc81a", "#a06a00"], O: ["O", "#ffb80a", "#8a5000"] },
  },
  lupine: {
    art: [" . ", " a ", "(b)", "(c)", "(d)", "(e)", "{f}"],
    key: { ".": [".", null, "#f0dcff"], a: ["o", "#e2c4ff", "#ffffff"], b: ["o", "#c8a2f8", "#f8f0ff"], c: ["8", "#b088f0", "#f4e8ff"], d: ["8", "#9a70e0", "#ece0ff"], e: ["8", "#8660d0", "#e4d4ff"], f: ["%", "#7048b8", "#d8c8f8"], "(": ["(", null, "#d4b0ff"], ")": [")", null, "#b090f0"], "{": ["{", null, "#a07ce8"], "}": ["}", null, "#8a64d8"] },
  },
  corn: {
    art: ["\\*/", "-@-"],
    key: { "\\": ["\\", null, "#7aa4ff"], "/": ["/", null, "#7aa4ff"], "*": ["*", null, "#a0c0ff"], "-": ["-", null, "#6a96ff"], "@": ["@", "#2a48b0", "#c4d8ff"] },
  },
};
const KINDS = ["poppy", "daisy", "lupine", "corn", "poppy", "daisy"];

function flowerCells(A, name) {
  const F = FLOWERS[name];
  const cells = [];
  F.art.forEach((line, r) => Array.from(line).forEach((ch, i) => {
    if (ch === " " || !F.key[ch]) return;
    const [gl0, fill, ink] = F.key[ch];
    const gl = F.flip && F.flip[ch] && i > line.length / 2 ? F.flip[ch] : gl0;
    cells.push({ dr: r - F.art.length, dc: i - Math.floor(line.length / 2), id: tile(A, gl, fill, ink) });
  }));
  return cells;
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 10 * k));
  const { w, h } = s;
  const S = Math.max(w, h);
  const portrait = h > w;
  const area = Math.min(1.5, (w * h) / 1.3e6);
  const hz = (s.hz = Math.round(h * (portrait ? 0.56 : 0.6)));
  const su = (s.su = portrait ? 0.74 : R.range(0.74, 0.8));
  const sx = (s.sx = su * w);
  const sy = hz - h * 0.06;
  const glowAt = (x) => Math.exp(-(((x - sx) / (0.32 * w)) ** 2));
  s.glowAt = glowAt;
  // Sky: lilac overhead, peach, then gold at the horizon round the sun.
  const [back, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, hz);
  lin.addColorStop(0, "#9fb0e4");
  lin.addColorStop(0.35, "#d4b8dc");
  lin.addColorStop(0.7, "#fac8a8");
  lin.addColorStop(1, "#ffdca0");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const glow = g.createRadialGradient(sx, sy, 0, sx, sy, S * 0.6);
  glow.addColorStop(0, "rgba(255,250,224,1)");
  glow.addColorStop(0.06, "rgba(255,230,160,0.8)");
  glow.addColorStop(0.3, "rgba(255,200,130,0.3)");
  glow.addColorStop(1, "rgba(255,190,130,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, h);
  dotGrid(g, s, "#fff4e0", 0.22);
  // The range: horns with hollow flanks and broken ridges, the slopes
  // facing the sun pink with alpenglow, the rest in violet shadow.
  const mt = atlas(s, Math.max(3, 8 * k));
  const mc = mt.cell;
  const peaks = (portrait
    ? [[0.25, 0.24, 0.4, 0.3], [0.62, 0.12, 0.2, 0.2], [0.98, 0.16, 0.25, 0.2]]
    : [[0.2, 0.3, 0.26, 0.2], [0.42, 0.17, 0.14, 0.14], [0.6, 0.12, 0.1, 0.1], [0.92, 0.2, 0.16, 0.2]]
  ).map(([x, hh, l, r]) => [(x + R.range(-0.02, 0.02)) * w, hh * h * R.range(0.88, 1.12), l * w, r * w]);
  const foot = hz - h * 0.02;
  const ridged = (x, sc, o) => 1 - Math.abs(2 * noise(x / sc + o, o) - 1);
  const prof = (x) => {
    let v = h * 0.02;
    for (const [px, ph, l, r] of peaks) v = Math.max(v, ph * Math.pow(Math.max(0, 1 - (x < px ? (px - x) / l : (x - px) / r)), 0.9));
    return v * (0.55 + 0.35 * ridged(x, (portrait ? 5 : 9) * mc, 3) + 0.1 * ridged(x, 4 * mc, 11));
  };
  const mcols = Math.ceil(w / mc) + 1;
  for (let c = 0; c < mcols; c += 1) {
    const x = (c + 0.5) * mc;
    const p = prof(x);
    const top = foot - p;
    // Faces and snow follow the broad slope, not each column's jag, so the
    // range reads as lit and shaded flanks rather than vertical stripes.
    const slope = prof(x + mc * 4) - prof(x - mc * 4);
    const lit = slope < 0;
    const peak = prof(x - mc) < p && prof(x + mc) < p;
    const gully = noise(c * 0.22, 9);
    const snowLine = p * (0.2 + 0.22 * noise(c * 0.12, 2) + 0.25 * Math.max(0, gully - 0.55) * 2);
    for (let y = Math.round(top / mc) * mc; y < foot; y += mc) {
      const d = y - top;
      const fade = clamp01(1 - (d / Math.max(mc, p)) * 0.9);
      if (fade < 0.05) break;
      const snow = d < snowLine && p > h * 0.05;
      const rim = d < mc * 1.5;
      let fill = rim ? (lit ? "#ffd0b8" : "#d8a0b8") : snow ? (lit ? "#f0c0cc" : "#8a7cb8") : lit ? "#8a6290" : "#4a3c6c";
      fill = mixc(fill, "#f6c4aa", quant(Math.min(0.8, 0.1 + 0.45 * (1 - fade) + 0.35 * glowAt(x)), 8));
      const gl = d < mc ? (peak ? "^" : slope > 0 ? "/" : "\\") : snow ? (lit ? "/" : ":") : ["%", ";", "#", ":"][Math.floor(hash(c, y, 3) * 4)];
      put(g, mt, tile(mt, gl, fill, shade(fill, snow && lit ? 0.82 : 1.3)), c * mc, y, 0.35 + 0.65 * fade);
    }
  }
  g.globalAlpha = 1;
  // The sun, low over the range.
  const sr = Math.max(4, 0.026 * S);
  g.fillStyle = "rgba(255,248,226,0.95)";
  g.beginPath();
  g.arc(sx, sy - sr * 0.3, sr, 0, TAU);
  g.fill();
  // Foothills and the meadow running up to them.
  const ridge = (y0, amp, sc, col, o) => {
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w + 6; x += 6) g.lineTo(x, y0 - amp * noise(x / sc + o, o));
    g.lineTo(w, h);
    g.closePath();
    g.fillStyle = col;
    g.fill();
  };
  ridge(hz - h * 0.005, h * 0.04, 240 * k, "#a690b8", R() * 50);
  ridge(hz + h * 0.012, h * 0.03, 180 * k, "#a8927a", R() * 50);
  const mead = g.createLinearGradient(0, hz, 0, h);
  mead.addColorStop(0, "#e0c47a");
  mead.addColorStop(0.2, "#b8b456");
  mead.addColorStop(0.55, "#6e8a34");
  mead.addColorStop(1, "#2c4418");
  ridge(hz + h * 0.03, h * 0.012, 300 * k, mead, R() * 50);
  const sheen = g.createRadialGradient(sx, hz + h * 0.05, 0, sx, hz + h * 0.05, S * 0.35);
  sheen.addColorStop(0, "rgba(255,226,150,0.6)");
  sheen.addColorStop(1, "rgba(255,226,150,0)");
  g.fillStyle = sheen;
  g.fillRect(0, hz, w, h - hz);
  const SPECK = ["#ffffff", "#ffd23a", "#ff6a48", "#c8a0f0", "#fff4c0"];
  for (let i = 0; i < 1500 * area; i += 1) {
    const u = Math.pow(R(), 1.7);
    const y = hz + h * 0.04 + u * (h - hz) * 0.45;
    g.globalAlpha = 0.4 + 0.5 * u;
    g.fillStyle = SPECK[Math.floor(R() * SPECK.length)];
    const r = (0.7 + 2 * u) * k;
    g.fillRect(R() * w, y, r, r);
  }
  g.globalAlpha = 1;
  // Down among the stems it is dark.
  for (const cx of [0, w]) {
    const sh = g.createRadialGradient(cx, h, 0, cx, h, S * 0.45);
    sh.addColorStop(0, "rgba(20,30,12,0.75)");
    sh.addColorStop(1, "rgba(20,30,12,0)");
    g.fillStyle = sh;
    g.fillRect(0, hz, w, h - hz);
  }
  s.back = back;
  // Clouds in glyph tiles: long streaks, gold underneath, lilac on top.
  const cc = s.cell;
  s.clouds = Array.from({ length: portrait ? 4 : 6 }, (_, i) => {
    const nr = 3 + Math.floor(R() * 4);
    const ncl = Math.round(R.range(26, 64) * (portrait ? 0.55 : 1));
    const c = document.createElement("canvas");
    const cw = (ncl + 12) * cc;
    c.width = Math.ceil(cw * s.dpr);
    c.height = Math.ceil(nr * cc * s.dpr);
    const cg = c.getContext("2d");
    cg.scale(s.dpr, s.dpr);
    const slant = R.range(-1.6, 1.6);
    const y = h * (0.05 + 0.3 * ((i * 0.37 + R() * 0.2) % 1)) * (portrait ? 0.9 : 1);
    const warm = clamp01(y / hz) * 0.6;
    for (let r = 0; r < nr; r += 1) {
      const hw = (ncl / 2) * (0.45 + 0.55 * Math.sin((Math.PI * (r + 0.5)) / nr)) * R.range(0.8, 1.1);
      const mid = 6 + ncl / 2 + slant * (r - nr / 2) * 2;
      const under = nr === 1 ? 1 : r / (nr - 1);
      const base = mixc(mixc("#e4bcd6", "#ffd6a6", under), "#fff0c8", quant(warm * under, 4));
      for (let ci = Math.floor(mid - hw); ci <= mid + hw; ci += 1) {
        const e = Math.abs(ci - mid) / hw + 0.25 * (hash(ci, r, i) - 0.5);
        if (e > 1) continue;
        const fill = shade(base, quant(0.94 + 0.08 * (hash(ci, r, 7) - 0.5), 8));
        if (e > 0.42 || under < 0.5) put(cg, s, tile(s, ["-", "~", "=", "~"][Math.floor(hash(ci, r, 2) * 4)], null, shade(base, under < 0.5 ? 0.92 : 1.04)), ci * cc, r * cc, 0.95 - 0.4 * clamp01(e - 0.5));
        else put(cg, s, tile(s, ["~", "=", "~", ":", "-"][Math.floor(hash(ci, r, 4) * 5)], fill, shade(fill, under > 0.5 ? 0.86 : 1.08)), ci * cc, r * cc, 0.92);
      }
    }
    cg.globalAlpha = 1;
    return { c, w: cw, h: nr * cc, x: R() * (w + cw) - cw, y, v: R.range(2.5, 5.5) * k };
  });
  // Middle meadow: two bands of glyph grass in sheared segments.
  const BANDS = [
    { cell: 8, at: 0.16, len: [2, 4], haze: 0.35 },
    { cell: 12, at: 0.42, len: [3, 6], haze: 0.12 },
  ];
  s.bands = BANDS.map((B) => {
    const A = atlas(s, Math.max(3, B.cell * k));
    const cl = A.cell;
    const y = hz + (h - hz) * B.at + h * 0.03;
    const segW = Math.round(cl * 8);
    const H = (B.len[1] + 4) * cl;
    const segs = [];
    for (let x0 = -segW; x0 < w + segW; x0 += segW) {
      const c = document.createElement("canvas");
      c.width = Math.ceil((segW + cl * 2) * s.dpr);
      c.height = Math.ceil(H * s.dpr);
      const cg = c.getContext("2d");
      cg.scale(s.dpr, s.dpr);
      for (let x = 0; x < segW; x += cl * 0.36) {
        const len = Math.round(R.range(B.len[0], B.len[1]));
        const lean = R.range(-0.3, 0.3);
        const y0 = H - cl * (1 + R() * 3);
        const gw = quant(glowAt(x0 + x), 4);
        for (let r = 0; r < len; r += 1) {
          const lift = (r + 1) / len;
          const col = mixc(mixc("#3a4e1e", mixc("#86a03a", "#ffe07a", gw), Math.pow(lift, 0.9)), "#e8c890", B.haze);
          const gl = r === len - 1 ? R.pick(["'", ",", "^"]) : Math.abs(lean) < 0.12 ? "|" : lean > 0 ? "/" : "\\";
          put(cg, A, tile(A, gl, null, col), x + lean * r * cl * 0.5, y0 - r * cl, 0.75 + 0.25 * lift);
        }
        if (R() < 0.05) put(cg, A, tile(A, R.pick(["*", "o", "@", "*"]), null, mixc(R.pick(["#ffffff", "#ffd23a", "#ff5a3a", "#b080f0"]), "#ffe8c0", B.haze)), x + lean * len * cl * 0.5, y0 - len * cl);
      }
      cg.globalAlpha = 1;
      segs.push({ c, x: x0, w: segW + cl * 2, h: H });
    }
    return { B, A, y, segs };
  });
  // Near grass: two dense layers of glyph blades, tall at the sides where
  // they stand against the sky, dark at the root and glowing at the tip
  // where the sun comes through. Painted in segments, bent in slices.
  const NEAR = [
    { cell: 14, dens: 0.3, tall: [0.1, 0.62], back: true },
    { cell: 19, dens: 0.34, tall: [0.07, 0.5] },
  ];
  s.near = NEAR.map((N) => {
    const A = atlas(s, Math.max(4, N.cell * k));
    const cl = A.cell;
    const segW = Math.round(cl * 7);
    const Hmax = h * (N.tall[1] + 0.06);
    const segs = [];
    for (let x0 = -segW; x0 < w + segW; x0 += segW) {
      const side = Math.min(1, Math.abs((x0 + segW / 2) / w - 0.5) * 2);
      const tallest = h * (N.tall[0] + (N.tall[1] - N.tall[0]) * Math.pow(side, 1.4));
      const H = Math.min(Hmax, tallest * 1.15 + cl * 2);
      const c = document.createElement("canvas");
      c.width = Math.ceil((segW + cl * 2) * s.dpr);
      c.height = Math.ceil(H * s.dpr);
      const cg = c.getContext("2d");
      cg.scale(s.dpr, s.dpr);
      for (let x = 0; x < segW; x += cl * N.dens) {
        const len = Math.max(2, Math.round((tallest / (cl * 0.68)) * R.range(0.35, 1.05)));
        const lean = R.range(-0.4, 0.4);
        const gw = quant(glowAt(x0 + x), 4);
        const tipCol = mixc(N.back ? "#7a8a30" : "#5a6a24", "#ffe080", gw);
        const head = R() < 0.1 && len > 5;
        for (let r = 0; r < len; r += 1) {
          const lift = (r + 1) / len;
          const off = lean * lift * lift * len * cl * 0.12;
          const prev = lean * Math.pow(r / len, 2) * len * cl * 0.12;
          const d = (off - prev) / (cl * 0.68);
          const top = head && r >= len - 2;
          const col = top ? mixc("#8a6a28", "#fff0b0", gw) : mixc(N.back ? "#24301a" : "#141c0c", tipCol, quant(Math.pow(lift, 2.2), 6));
          const gl = top ? "%" : r === len - 1 ? R.pick(["'", "^", ","]) : d > 0.25 ? "/" : d < -0.25 ? "\\" : "|";
          const yy = H - cl - r * cl * 0.68;
          const id = tile(A, gl, null, col);
          put(cg, A, id, x + cl * 0.5 + off, yy);
          if (lift < 0.6) put(cg, A, id, x + cl * 0.5 + off + Math.max(1, cl * 0.08), yy);
        }
      }
      cg.globalAlpha = 1;
      segs.push({ c, x: x0, w: segW + cl * 2, h: H });
    }
    return { N, A, segs, Hmax };
  });
  // Flowers: big ones in the corner clusters, smaller ones further in.
  const fl = (s.fl = atlas(s, Math.max(4, Math.min(26 * k, h / 30))));
  const fm = (s.fm = atlas(s, Math.max(3, Math.min(14 * k, h / 55))));
  s.flowers = [];
  const addFlower = (A, name, x, headY, stem) => {
    const cells = flowerCells(A, name);
    const stemIds = Array.from({ length: stem }, (_, r) => tile(A, "|", null, mixc("#24361a", "#6a8a30", quant(r / stem, 3))));
    const leaves = [];
    for (let r = 1; r < stem - 1; r += 2 + Math.floor(R() * 2)) {
      const sd = R.sign();
      leaves.push({ r, dc: sd, id: tile(A, sd > 0 ? (R() < 0.5 ? "/" : "~") : R() < 0.5 ? "\\" : "~", null, mixc("#2c4a1c", "#7a9a34", quant(r / stem, 3))) });
    }
    s.flowers.push({ A, x, headY, cells, stem: stemIds, leaves, ph: R() * TAU, big: A === fl });
  };
  const per = portrait ? 2 : 3;
  const reach = portrait ? 0.34 : 0.27;
  for (const side of [0, 1]) {
    for (let i = 0; i < per; i += 1) {
      // Each corner gets a poppy; lupines sometimes give way to cornflowers.
      let name = (side ? ["lupine", "poppy", "daisy"] : ["poppy", "daisy", "lupine"])[i % 3];
      if (name === "lupine" && R() < 0.3) name = "corn";
      const f = ((i + 0.5 + R.range(-0.12, 0.12)) / per) * reach;
      const u = side ? 1 - f : f;
      const headY = h - fl.cell * (portrait ? 3.5 + ((i * 2.3) % 4) : [5.5, 3.2, 7][i % 3] * R.range(0.9, 1.1));
      addFlower(fl, name, u * w, headY, Math.ceil((h - headY) / fl.cell) + 1);
    }
  }
  const nm = portrait ? 6 : 12;
  for (let i = 0; i < nm; i += 1) {
    const u = R() < 0.5 ? R.range(0.04, 0.32) : R.range(0.68, 0.96);
    const headY = h * R.range(0.66, 0.84);
    addFlower(fm, KINDS[Math.floor(R() * KINDS.length)], u * w, headY, Math.ceil((h - headY) / fm.cell) + 1);
  }
  s.flowers.sort((a, b) => a.headY - b.headY + (a.big - b.big) * 1000);
  s.big = s.near[1].A;
  // Butterflies, seeds and motes in the light.
  s.flies = [["#ff8a1a", "#3a1a08"], ["#fff2c0", "#5a4a20"]].map(([a, b2], i) => ({ L: tile(fm, "}", a, b2), Rr: tile(fm, "{", a, b2), l: tile(fm, ")", null, a), r: tile(fm, "(", null, a), body: tile(fm, "|", null, b2), ph: R() * 100, i }));
  s.seeds = [tile(s, "*", null, "#fffaf0"), tile(s, "+", null, "#fff4d8")];
  s.mote = tile(s, ".", null, "#fff2b0");
  s.windOff = R() * 1000;
  s.sy = sy;
}

export const meadow = {
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
    const { ctx, w, h, k, dpr } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.back, 0, 0, w, h);
    for (const c of s.clouds) {
      const span = w + c.w;
      const x = ((((c.x + c.v * t) % span) + span) % span) - c.w;
      ctx.drawImage(c.c, Math.round(x * dpr) / dpr, c.y, c.w, c.h);
    }
    // Wind: a steady breeze plus gusts that roll across from the left.
    const gw = w + 1600 * k;
    const g1 = ((t * 130 * k + s.windOff) % gw) - 800 * k;
    const g2 = ((t * 130 * k + s.windOff + gw * 0.55) % gw) - 800 * k;
    const wind = (x) => 0.3 + 0.25 * Math.sin(t * 0.9 + (x * 0.006) / k) + 0.12 * Math.sin(t * 2.3 + (x * 0.02) / k) + Math.exp(-(((x - g1) / (240 * k)) ** 2)) + 0.8 * Math.exp(-(((x - g2) / (200 * k)) ** 2));
    // The middle meadow leans in stepped slices, tops furthest.
    for (const bd of s.bands) {
      const amp = bd.A.cell * (1 + bd.B.at * 2);
      for (const sg of bd.segs) {
        const bend = amp * (wind(sg.x + sg.w / 2) - 0.3);
        const sh = sg.c.height / 4;
        for (let i = 0; i < 4; i += 1) {
          const dx = Math.round(bend * (1 - (i + 0.5) / 4) * dpr) / dpr;
          ctx.drawImage(sg.c, 0, i * sh, sg.c.width, sh, sg.x + dx, bd.y - sg.h + (i * sg.h) / 4, sg.w, sg.h / 4);
        }
      }
    }
    // A sheen runs over the meadow with each gust.
    for (const gx of [g1, g2]) {
      const gy = s.hz + (h - s.hz) * 0.3;
      const sh = ctx.createRadialGradient(gx, gy, 0, gx, gy, 320 * k);
      sh.addColorStop(0, "rgba(255,240,180,0.3)");
      sh.addColorStop(1, "rgba(255,240,180,0)");
      ctx.fillStyle = sh;
      ctx.fillRect(gx - 320 * k, s.hz + h * 0.03, 640 * k, h);
    }
    motes(ctx, s, t);
    for (const f of s.flowers) if (!f.big) flower(ctx, s, f, t, wind);
    nearGrass(ctx, s, s.near[0], wind);
    for (const f of s.flowers) if (f.big) flower(ctx, s, f, t, wind);
    nearGrass(ctx, s, s.near[1], wind);
    seeds(ctx, s, t, wind);
    butterflies(ctx, s, t);
    ctx.globalAlpha = 1;
  },
};

// A layer of near grass bends in horizontal slices: the higher the slice,
// the further it leans.
function nearGrass(ctx, s, L, wind) {
  const { h, dpr, k } = s;
  const n = 9;
  for (const sg of L.segs) {
    const bend = L.A.cell * 2.2 * k * (wind(sg.x + sg.w / 2) - 0.2);
    const sh = sg.c.height / n;
    for (let i = 0; i < n; i += 1) {
      const above = (sg.h * (n - i - 0.5)) / n;
      const dx = Math.round(bend * Math.pow(above / L.Hmax, 1.6) * 4 * dpr) / dpr;
      ctx.drawImage(sg.c, 0, i * sh, sg.c.width, sh, sg.x + dx, h - sg.h + (i * sg.h) / n, sg.w, sg.h / n);
    }
  }
}

function flower(ctx, s, f, t, wind) {
  const A = f.A;
  const cl = A.cell;
  const n = f.stem.length;
  const sway = (wind(f.x) * 0.9 + 0.25 * Math.sin(t * 1.7 + f.ph)) * cl * (f.big ? 0.5 : 0.35) * s.k;
  for (let r = 0; r < n; r += 1) {
    const lift = (r + 1) / n;
    put(ctx, A, f.stem[r], f.x + sway * lift * lift - cl / 2, h0(s) - (r + 1) * cl);
  }
  for (const l of f.leaves) {
    const lift = (l.r + 1) / n;
    put(ctx, A, l.id, f.x + sway * lift * lift - cl / 2 + l.dc * cl * 0.7, h0(s) - (l.r + 1) * cl);
  }
  const hx = f.x + sway - cl / 2;
  const hy = h0(s) - n * cl;
  for (const c of f.cells) put(ctx, A, c.id, hx + c.dc * cl, hy + c.dr * cl + cl);
}

const h0 = (s) => s.h + s.fl.cell * 0.3;

function butterflies(ctx, s, t) {
  const { w, h, k, fm } = s;
  const cl = fm.cell;
  for (const f of s.flies) {
    // Each wanders over its own corner, flitting.
    const cx = w * (f.i === 0 ? 0.13 : 0.86);
    const x = cx + w * 0.11 * (noise(t * 0.2 + f.ph, 1) - 0.5) * 2;
    const y = h * (0.6 + 0.22 * noise(t * 0.25 + f.ph, 2)) + Math.sin(t * 8 + f.ph) * 4 * k;
    const open = Math.sin(t * 17 + f.ph) > -0.3;
    put(ctx, fm, open ? f.L : f.l, x - cl * 0.75, y);
    put(ctx, fm, f.body, x, y);
    put(ctx, fm, open ? f.Rr : f.r, x + cl * 0.75, y);
  }
  ctx.globalAlpha = 1;
}

function seeds(ctx, s, t, wind) {
  const { w, h, k } = s;
  for (let i = 0; i < 10; i += 1) {
    const P = 26 + i * 3;
    const u = ((t + i * 11.3) % P) / P;
    const n = Math.floor((t + i * 11.3) / P);
    const x = w * hash(i, n, 1) + u * w * 0.45 + 30 * k * Math.sin(t * 0.7 + i);
    const y = h * (0.8 - 0.5 * u - 0.1 * hash(i, n, 2)) + 10 * k * Math.sin(t * 1.3 + i * 2);
    const a = Math.sin(u * Math.PI) * (0.55 + 0.45 * s.glowAt(x));
    put(ctx, s, s.seeds[i % 2], x + wind(x) * 4 * k, y, a);
  }
  ctx.globalAlpha = 1;
}

function motes(ctx, s, t) {
  const { h, k } = s;
  // Midges dancing in the low sun.
  for (let i = 0; i < 18; i += 1) {
    const x = s.sx + (noise(t * 0.15 + i * 3.1, 1) - 0.5) * 360 * k;
    const y = s.hz + h * 0.02 - noise(t * 0.18 + i * 5.3, 2) * h * 0.22;
    put(ctx, s, s.mote, x, y, 0.4 + 0.5 * Math.sin(t * 3 + i * 1.7) ** 2);
  }
  ctx.globalAlpha = 1;
}

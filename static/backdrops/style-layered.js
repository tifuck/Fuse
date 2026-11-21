// Layered: paper-cut scenes. Flat silhouettes stacked back to front, each
// baked once per resize into its own strip, then drawn at whole device
// pixels (cheap, seamless wrap) drifting at parallax speeds.

import { seeded, hash } from "./noise.js";
import { TAU, rgb, rgba, mixHex } from "./art.js";

// —— Strips and tiles ——
// A strip is one period P (CSS px) of a horizontally periodic picture over
// screen rows top..bottom, painted once per resize. crop() then cuts it into
// column tiles that keep only rows holding an edge, so a frame copies the
// thin band where layers meet and fills the flat body below with fillRect.
// Every copy is an unscaled drawImage at whole device pixels: cheap and
// seamless.
function canvas(W, H) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, W);
  c.height = Math.max(1, H);
  return c;
}

const TILE = 160;

function strip(s, top, bottom = s.h, period = 1.3) {
  const { dpr } = s;
  const SW = Math.round(s.w * dpr);
  const W = Math.max(SW, Math.ceil(s.w * dpr * period));
  const Y0 = Math.max(0, Math.floor(top * dpr));
  const Y1 = Math.max(Y0 + 1, Math.min(Math.round(s.h * dpr), Math.ceil(bottom * dpr)));
  const c = canvas(W, Y1 - Y0);
  const g = c.getContext("2d", { willReadFrequently: true });
  g.setTransform(dpr, 0, 0, dpr, 0, -Y0);
  return { c, g, W, P: W / dpr, X0: 0, Y0, Y1, y1: Y1 / dpr };
}

// A fixed rectangle of the screen (CSS px), for static overlays.
function patch(s, x0, y0, x1, y1, wide = false) {
  const { dpr } = s;
  const X0 = wide ? Math.floor(x0 * dpr) : Math.max(0, Math.floor(x0 * dpr));
  const Y0 = Math.max(0, Math.floor(y0 * dpr));
  const X1 = Math.max(X0 + 1, wide ? Math.ceil(x1 * dpr) : Math.min(Math.round(s.w * dpr), Math.ceil(x1 * dpr)));
  const Y1 = Math.max(Y0 + 1, Math.min(Math.round(s.h * dpr), Math.ceil(y1 * dpr)));
  const c = canvas(X1 - X0, Y1 - Y0);
  const g = c.getContext("2d", { willReadFrequently: true });
  g.setTransform(dpr, 0, 0, dpr, -X0, -Y0);
  return { c, g, W: 0, X0, Y0, Y1, y1: Y1 / dpr };
}

// Cut into column tiles cropped to the rows that are not empty and, when
// col is given, not plain col (below a tile's crop the layer is all col).
// span(x0, x1) gives the CSS rows [top, bottom] the paint covers over those
// columns (ext pads it for the shadow); without it the pixels are read.
function crop(L, col, span, ext = 0, dpr = 1) {
  const { c } = L;
  const W = c.width;
  const H = c.height;
  let d = null;
  let solid = 0;
  if (!span) {
    d = new Uint32Array(L.g.getImageData(0, 0, W, H).data.buffer);
    // The layer colour as one pixel word, in this machine's byte order.
    const px = new Uint8ClampedArray(4);
    if (col) px.set([...rgb(col), 255]);
    solid = new Uint32Array(px.buffer)[0];
  }
  L.col = col;
  L.tiles = [];
  for (let X = 0; X < W; X += TILE) {
    const w = Math.min(TILE, W - X);
    let top = 0;
    let bot = H;
    if (span) {
      const [a, b] = span(X / dpr - ext, (X + w) / dpr + ext);
      top = Math.max(0, Math.floor((a - ext) * dpr) - L.Y0);
      bot = Math.min(H, Math.ceil(b * dpr) + 1 - L.Y0);
    } else {
      const empty = (y) => {
        for (let i = y * W + X, e = i + w; i < e; i += 1) if (d[i]) return false;
        return true;
      };
      const plain = (y) => {
        for (let i = y * W + X, e = i + w; i < e; i += 1) if (d[i] !== solid) return false;
        return true;
      };
      while (top < H && empty(top)) top += 1;
      if (col) while (bot > top && plain(bot - 1)) bot -= 1;
      else while (bot > top && empty(bot - 1)) bot -= 1;
    }
    if (top >= H) continue;
    if (bot < top) bot = top;
    let tc = null;
    if (bot > top) {
      tc = canvas(w, bot - top);
      tc.getContext("2d").drawImage(c, X, top, w, bot - top, 0, 0, w, bot - top);
    }
    L.tiles.push({ c: tc, X, w, top, bot });
  }
  L.c = null;
  L.g = null;
  return L;
}

// The rows a periodic top line spans over x0..x1.
const along = (top, step = 2) => (x0, x1) => {
  let a = Infinity;
  let b = -Infinity;
  for (let x = x0; ; x += step) {
    const y = top(Math.min(x, x1));
    if (y < a) a = y;
    if (y > b) b = y;
    if (x >= x1) break;
  }
  return [a, b];
};

// Draw a cropped strip shifted left by off CSS px; a layer's flat body is
// filled from each tile's crop down to device row fillTo.
function blit(s, L, off = 0, fillTo = 0, dy = 0) {
  const { ctx, dpr } = s;
  const SW = Math.round(s.w * dpr);
  const W = L.W;
  let ox = 0;
  if (W) {
    ox = Math.round(off * dpr) % W;
    if (ox < 0) ox += W;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (L.col) ctx.fillStyle = L.col;
  for (const tl of L.tiles) {
    let x = L.X0 + tl.X - (W ? 0 : Math.round(off * dpr));
    if (W) {
      x = (tl.X - ox) % W;
      if (x < 0) x += W;
    }
    for (let k = 0; k < 2; k += 1) {
      const xx = k ? x - W : x;
      if (k && !(W && x + tl.w > W)) break;
      if (xx >= SW || xx + tl.w <= 0) continue;
      if (tl.c) ctx.drawImage(tl.c, xx, L.Y0 + dy + tl.top);
      if (L.up) {
        // A reflection: its flat body is above the crop, up to the shore.
        if (L.col && tl.top > 0) ctx.fillRect(xx, L.Y0 + dy, tl.w, tl.top);
        continue;
      }
      const yb = L.Y0 + dy + tl.bot;
      if (L.col && fillTo > yb) ctx.fillRect(xx, yb, tl.w, fillTo - yb);
    }
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// Paint a silhouette into a strip over a soft paper shadow. The shadow is
// blurred at quarter resolution (blur cost grows with area) and stretched.
// paint(g, x0, x1) must cover x0..x1 (periodic shapes repeat by P).
function paper(s, L, paint, shade) {
  const { dpr } = s;
  const q = 0.25;
  const pad = Math.ceil(shade.blur * 3);
  const padD = Math.round(pad * dpr * q);
  const W = Math.ceil(L.W * q) + 2 * padD;
  const H = Math.ceil(L.c.height * q);
  const tmp = canvas(W, H);
  const g = tmp.getContext("2d");
  g.setTransform(dpr * q, 0, 0, dpr * q, padD, -L.Y0 * q);
  paint(g, -pad, L.P + pad);
  const sh = canvas(W, H);
  const sg = sh.getContext("2d");
  sg.shadowColor = shade.color;
  sg.shadowBlur = shade.blur * dpr * q;
  sg.shadowOffsetX = W;
  sg.shadowOffsetY = (shade.dy || 0) * dpr * q;
  sg.drawImage(tmp, -W, 0);
  const o = L.g;
  o.save();
  o.setTransform(1, 0, 0, 1, 0, 0);
  o.drawImage(sh, -padD / q, 0, W / q, H / q);
  o.restore();
  paint(o, 0, L.P);
}

// A periodic sum of sines: waves are [wavelength, amplitude] pairs.
function harmonics(rng, P, waves) {
  const terms = waves.map(([wl, a]) => ({ n: Math.max(1, Math.round(P / wl)), a, ph: rng() * TAU }));
  return (x) => {
    let v = 0;
    for (const q of terms) v += q.a * Math.sin((TAU * q.n * x) / P + q.ph);
    return v;
  };
}

// Normalise a periodic function to 0..1 over its period.
function normalise(f, P, step = 2) {
  let lo = Infinity;
  let hi = -Infinity;
  for (let x = 0; x < P; x += step) {
    const v = f(x);
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const d = hi - lo || 1;
  return (x) => (f(x) - lo) / d;
}

// The silhouette below y = top(x), from x0 to x1, down to bottom.
function profile(g, top, x0, x1, bottom, step = 1.5) {
  g.beginPath();
  g.moveTo(x0, bottom);
  for (let x = x0; ; x += step) {
    const xx = Math.min(x, x1);
    g.lineTo(xx, top(xx));
    if (xx >= x1) break;
  }
  g.lineTo(x1, bottom);
  g.closePath();
  g.fill();
}

// Lowest and highest points of a periodic top line.
function lowest(top, P, step = 2) {
  let y = -Infinity;
  for (let x = 0; x <= P; x += step) y = Math.max(y, top(x));
  return Math.ceil(y + 1);
}
function highest(top, P, step = 2) {
  let y = Infinity;
  for (let x = 0; x <= P; x += step) y = Math.min(y, top(x));
  return y;
}

// A layer: its edge band painted with a paper shadow, cropped to tiles.
// Strip bottoms never rise from back to front, so each layer's flat body
// only has to reach the next layer's strip bottom.
function ridge(s, st, top, solid, col, shade, paint, v, span, bob) {
  const prev = st.layers[st.layers.length - 1];
  const ext = shade.blur * 1.6 + Math.abs(shade.dy || 0);
  const L = strip(s, top - ext, Math.max(solid, prev ? prev.L.y1 : 0));
  paper(s, L, paint, shade);
  st.layers.push({ L: crop(L, col, span, ext, s.dpr), v, bob });
}

// A sky: a vertical gradient as runs of device rows of one colour (what a
// gradient quantises to anyway), filled flat each frame; plus sparse stars
// and small sprites (moon, sun) that are copied whole.
function sky(s, stops, bottom = s.h) {
  const { dpr } = s;
  const cols = stops.map(([at, c]) => [at, rgb(c)]);
  const runs = [];
  const H = Math.round(s.h * dpr);
  const end = Math.min(H, Math.ceil(bottom * dpr));
  let last = "";
  for (let Y = 0; Y < end; Y += 1) {
    const f = Y / H;
    let j = 0;
    while (j < cols.length - 2 && f > cols[j + 1][0]) j += 1;
    const [a0, c0] = cols[j];
    const [a1, c1] = cols[j + 1];
    const u = Math.max(0, Math.min(1, (f - a0) / (a1 - a0 || 1)));
    const c = "rgb(" + c0.map((v, i) => Math.round(v + (c1[i] - v) * u)).join(",") + ")";
    if (c === last) runs[runs.length - 1][1] = Y + 1;
    else runs.push([Y, Y + 1, c]);
    last = c;
  }
  return { runs, stars: [], sprites: [] };
}

function drawSky(s, sk) {
  const { ctx, dpr } = s;
  const SW = Math.round(s.w * dpr);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  for (const [a, b, c] of sk.runs) {
    ctx.fillStyle = c;
    ctx.fillRect(0, a, SW, b - a);
  }
  for (const st of sk.stars) {
    ctx.fillStyle = st[3];
    ctx.fillRect(st[0], st[1], st[2], st[2]);
  }
  for (const sp of sk.sprites) ctx.drawImage(sp.c, sp.X0, sp.Y0);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// A soft disc with flat paper halo rings (moon or sun), as a sprite.
function orb(s, sk, x, y, r, disc, halo, rings = [2.2, 3.6], ringA = [0.05, 0.03]) {
  const R = r * 4.5;
  const sp = patch(s, x - R, y - R, x + R, y + R);
  const g = sp.g;
  const gr = g.createRadialGradient(x, y, r * 0.8, x, y, R);
  gr.addColorStop(0, rgba(halo, ringA[0] * 1.4));
  gr.addColorStop(1, rgba(halo, 0));
  g.fillStyle = gr;
  g.fillRect(x - R, y - R, R * 2, R * 2);
  rings.forEach((k, i) => {
    g.fillStyle = rgba(halo, ringA[i]);
    g.beginPath();
    g.arc(x, y, r * k, 0, TAU);
    g.fill();
  });
  g.fillStyle = disc;
  g.beginPath();
  g.arc(x, y, r, 0, TAU);
  g.fill();
  sk.sprites.push(sp);
  return sp;
}

// Sparse static stars (a device pixel or two), quieter mid-screen.
function stars(s, sk, rng, density, below, tint = "#c8d2e6", amax = 0.3) {
  const n = Math.round((s.w * below) / density);
  const [r, g, b] = rgb(tint);
  for (let i = 0; i < n; i += 1) {
    const x = rng() * s.w;
    const y = rng() * below;
    const mid = Math.abs(x / s.w - 0.5) < 0.3 && y < s.h * 0.7 ? 0.45 : 1;
    const a = rng.range(0.25, 1) * amax * mid;
    const size = rng() < 0.15 ? 2 : 1;
    sk.stars.push([Math.round(x * s.dpr), Math.round(y * s.dpr), size, "rgba(" + r + "," + g + "," + b + "," + a.toFixed(3) + ")"]);
  }
}

// A soft mist band: vertical falloff times a periodic horizontal density.
function mist(s, rng, y, thick, color, alpha) {
  const L = strip(s, y - thick, y + thick, 1.3);
  const { g, P } = L;
  const along = normalise(harmonics(rng, P, [[P / 2, 1], [s.w * 0.4, 0.7], [s.w * 0.17, 0.4]]), P);
  const gx = g.createLinearGradient(0, 0, P, 0);
  const steps = 48;
  for (let i = 0; i <= steps; i += 1) {
    const v = along((i / steps) * P);
    gx.addColorStop(i / steps, rgba(color, alpha * (0.15 + 0.85 * v * v)));
  }
  g.fillStyle = gx;
  g.fillRect(0, y - thick, P, thick * 2);
  g.globalCompositeOperation = "destination-in";
  const gy = g.createLinearGradient(0, y - thick, 0, y + thick);
  gy.addColorStop(0, "rgba(0,0,0,0)");
  gy.addColorStop(0.5, "rgba(0,0,0,1)");
  gy.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = gy;
  g.fillRect(0, y - thick, P, thick * 2);
  g.globalCompositeOperation = "source-over";
  return crop(L, null, () => [0, s.h]);
}

// A moon's x: right of the centred reading column (about 816 px) on wide
// screens, still on screen on phones.
const moonX = (s, r) => Math.min(s.w - r * 2.2, Math.max(s.w * 0.88, s.w / 2 + 408 + r * 3.5));

const speed = (s, v) => (v * Math.min(s.w, s.h)) / 900;
const span = (s) => Math.min(s.h, s.w * 1.1);
const wrapX = (x, lo, hi) => lo + ((((x - lo) % (hi - lo)) + (hi - lo)) % (hi - lo));

// Draw a stack: sky, then each layer's edge tiles and flat body, with mist
// bands after the layers they follow.
function stack(s, t, st, extra) {
  const { layers } = st;
  drawSky(s, st.sky);
  extra?.(-1);
  const end = Math.round(s.h * s.dpr);
  layers.forEach((ly, i) => {
    const next = layers[i + 1];
    const dy = ly.bob ? Math.round(ly.bob.a * s.dpr * Math.sin(t * ly.bob.w + ly.bob.ph)) : 0;
    blit(s, ly.L, t * ly.v, next ? next.L.Y1 + Math.ceil((next.bob?.a || 0) * s.dpr) : end, dy);
    for (const m of st.mists) if (m.after === i) blit(s, m.L, t * m.v);
    extra?.(i);
  });
}

// —— Ranges ——
const RANGES = {
  sky: [[0, "#0e1626"], [0.55, "#16223a"], [0.75, "#1b2a3e"]],
  layers: ["#1c2a40", "#17233a", "#121c31", "#0e1728", "#0a1220"],
};

export const ranges = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(4107);
    const st = { layers: [], mists: [] };
    const base = [0.64, 0.71, 0.78, 0.86, 0.94];
    const amp = [0.21, 0.19, 0.17, 0.14, 0.11];
    const P = strip(s, 0, 1).P;
    RANGES.layers.forEach((col, i) => {
      const lr = seeded(4200 + i);
      const wl = m * (0.75 + 0.3 * i) * (s.w > s.h ? 1.2 : 0.9);
      const terms = [[wl, 1], [wl / 2.1, 0.45], [wl / 4.4, 0.22], [wl / 9.3, 0.1], [wl / 19, 0.045]].map(([l, a]) => ({
        n: Math.max(1, Math.round(P / l)), a, ph: lr() * TAU,
      }));
      const raw = (x) => {
        let v = 0;
        for (const q of terms) {
          const sn = Math.sin((Math.PI * q.n * x) / P + q.ph);
          v += q.a * (1 - Math.sqrt(sn * sn + 0.015));
        }
        return v;
      };
      const n01 = normalise(raw, P);
      const top = (x) => h * base[i] - A * amp[i] * n01(x);
      ridge(s, st, highest(top, P), lowest(top, P), col, { color: "rgba(2,6,14,0.55)", blur: m * 0.022, dy: -m * 0.004 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, top, x0, x1, h + 2);
      }, speed(s, [1, 1.8, 3, 4.6, 6.8][i]), along(top));
    });
    st.sky = sky(s, RANGES.sky, st.layers[0].L.y1);
    stars(s, st.sky, rng, 5200, h * 0.75);
    orb(s, st.sky, moonX(s, m * 0.038), h * 0.2, m * 0.038, "rgba(205,214,232,0.55)", "#9fb2d4");
    [[1, 0.74, 3.4, 0.045], [3, 0.88, -5, 0.035]].forEach(([after, y, v, th]) => {
      st.mists.push({ after, v: speed(s, v), L: mist(s, rng, h * y, h * th, "#3a4c68", 0.3) });
    });
    st.wisps = [0, 1].map((i) => ({
      len: m * rng.range(0.22, 0.32), y: h * rng.range(0.12, 0.3) + i * h * 0.06, x0: rng() * w, v: speed(s, rng.range(3, 5)),
    }));
    s.st = st;
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const m = Math.min(w, h);
    const st = s.st;
    stack(s, t, st, (i) => {
      if (i !== -1) return;
      ctx.fillStyle = "rgba(38,52,76,0.7)";
      for (const c of st.wisps) {
        const x = wrapX(c.x0 + t * c.v, -c.len * 1.2, w + c.len * 0.2);
        const hh = m * 0.014;
        ctx.beginPath();
        ctx.roundRect(x, c.y, c.len, hh, hh / 2);
        ctx.roundRect(x + c.len * 0.18, c.y - hh * 0.9, c.len * 0.55, hh, hh / 2);
        ctx.roundRect(x - c.len * 0.12, c.y + hh * 0.9, c.len * 0.5, hh, hh / 2);
        ctx.fill();
      }
    });
  },
};

// —— Pines ——
const PINES = {
  sky: [[0, "#0b1413"], [0.5, "#152220"], [0.72, "#24332f"]],
  layers: ["#2a3a36", "#1f2e2a", "#151f1c", "#0c1512"],
};

function pine(g, x, yb, H, rng) {
  const tiers = 4 + Math.floor(rng() * 3);
  const W = H * rng.range(0.3, 0.38);
  const top = yb - H;
  g.beginPath();
  g.moveTo(x - H * 0.012, top + H * 0.02);
  g.lineTo(x, top - H * 0.04);
  g.lineTo(x + H * 0.012, top + H * 0.02);
  g.fill();
  for (let i = 0; i < tiers; i += 1) {
    const a = top + H * 0.8 * (i / tiers);
    const b = top + H * (0.24 + 0.74 * ((i + 1) / tiers));
    const hw = (W / 2) * (0.34 + 0.66 * ((i + 1) / tiers)) * rng.range(0.9, 1.1);
    const d = b - a;
    const jl = d * rng.range(-0.06, 0.06);
    const jr = d * rng.range(-0.06, 0.06);
    g.beginPath();
    g.moveTo(x, a);
    g.quadraticCurveTo(x - hw * 0.32, a + d * 0.72, x - hw, b + jl);
    g.quadraticCurveTo(x - hw * 0.4, b - d * 0.22, x, b - d * 0.12);
    g.quadraticCurveTo(x + hw * 0.4, b - d * 0.22, x + hw, b + jr);
    g.quadraticCurveTo(x + hw * 0.32, a + d * 0.72, x, a);
    g.fill();
  }
  g.fillRect(x - H * 0.02, yb - H * 0.12, H * 0.04, H * 0.14);
}

export const pines = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(5303);
    const st = { layers: [], mists: [] };
    const base = [0.63, 0.72, 0.82, 0.94];
    const ridgeA = [0.05, 0.055, 0.06, 0.07];
    const treeH = [0.07, 0.1, 0.14, 0.2];
    const P = strip(s, 0, 1).P;
    PINES.layers.forEach((col, i) => {
      const lr = seeded(5400 + i);
      const wl = m * (1.1 + 0.3 * i);
      const roll = normalise(harmonics(lr, P, [[wl, 1], [wl / 2.3, 0.4], [wl / 5, 0.12]]), P);
      const ground = (x) => h * base[i] - A * ridgeA[i] * roll(x);
      const H = A * treeH[i];
      const trees = [];
      for (let x = lr() * H * 0.3; x < P; x += H * lr.range(0.16, 0.42)) {
        if (lr() < 0.07) {
          x += H * lr.range(0.15, 0.35);
          continue;
        }
        trees.push({ x, H: H * lr.range(0.55, 1.2), seed: Math.floor(lr() * 1e9) });
      }
      ridge(s, st, highest(ground, P) - H * 1.3, lowest(ground, P), col, { color: "rgba(0,6,4,0.32)", blur: m * 0.016, dy: -m * 0.003 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, ground, x0, x1, h + 2);
        for (const tr of trees) {
          for (const k of [-1, 0, 1]) {
            const x = tr.x + k * P;
            if (x < x0 - H || x > x1 + H) continue;
            pine(pg, x, ground(tr.x) + tr.H * 0.06, tr.H, seeded(tr.seed));
          }
        }
      }, speed(s, [0.9, 1.8, 3.2, 5.5][i]), (x0, x1) => {
        const r = along(ground)(x0, x1);
        for (const tr of trees) {
          for (let k = -1; k <= 1; k += 1) {
            const x = tr.x + k * P;
            if (x < x0 - tr.H * 0.22 || x > x1 + tr.H * 0.22) continue;
            r[0] = Math.min(r[0], ground(tr.x) - tr.H * 0.99);
          }
        }
        return r;
      });
    });
    st.sky = sky(s, PINES.sky, st.layers[0].L.y1);
    stars(s, st.sky, rng, 7000, h * 0.7, "#cfe0d8", 0.22);
    {
      const r = m * 0.032;
      const x = w * 0.17;
      const y = h * 0.17;
      const sp = orb(s, st.sky, x, y, r, "rgba(0,0,0,0)", "#a9c4b8", [2.3, 3.8], [0.04, 0.025]);
      const c = canvas(sp.c.width, sp.c.height);
      const cg = c.getContext("2d");
      cg.setTransform(s.dpr, 0, 0, s.dpr, -sp.X0, -sp.Y0);
      cg.fillStyle = "rgba(208,224,214,0.5)";
      cg.beginPath();
      cg.arc(x, y, r, 0, TAU);
      cg.fill();
      cg.globalCompositeOperation = "destination-out";
      cg.beginPath();
      cg.arc(x + r * 0.42, y - r * 0.18, r * 0.92, 0, TAU);
      cg.fill();
      sp.g.setTransform(1, 0, 0, 1, 0, 0);
      sp.g.drawImage(c, 0, 0);
    }
    [[1, 0.78, -4.2, 0.04]].forEach(([after, y, v, th]) => {
      st.mists.push({ after, v: speed(s, v), L: mist(s, rng, h * y, h * th, "#3e554e", 0.28) });
    });
    s.st = st;
  },
  draw(s, t) {
    stack(s, t, s.st);
  },
};

// —— Dunes ——
const DUNES = {
  sky: [[0, "#f6f1e9"], [0.45, "#f3ece2"], [0.7, "#f0e5d5"]],
  layers: ["#ece1cf", "#e8d9c2", "#e0ceb3", "#d6c09f"],
};

export const dunes = {
  fps: 20,
  wallpaper: true,
  scheme: "light",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(6101);
    const st = { layers: [], mists: [] };
    const base = [0.64, 0.72, 0.81, 0.92];
    const duneH = [0.05, 0.075, 0.1, 0.14];
    const gap = [0.4, 0.55, 0.75, 1.05];
    const P = strip(s, 0, 1).P;
    DUNES.layers.forEach((col, i) => {
      const lr = seeded(6200 + i);
      const n = Math.max(2, Math.round(P / (m * gap[i] * (s.w > s.h ? 1.25 : 1))));
      const sp = P / n;
      const H0 = A * duneH[i];
      const list = [];
      for (let j = 0; j < n; j += 1) {
        list.push({ c: (j + lr.range(-0.2, 0.2)) * sp, H: H0 * lr.range(0.5, 1.1), a: sp * lr.range(0.65, 0.95), b: sp * lr.range(0.28, 0.42) });
      }
      const und = harmonics(lr, P, [[P / 2, A * 0.008], [sp * 1.7, A * 0.006]]);
      const bump = (d, u) => {
        if (u <= -d.a || u >= d.b) return 0;
        if (u <= 0) return d.H * Math.pow(0.5 + 0.5 * Math.cos((Math.PI * u) / d.a), 1.3);
        return d.H * Math.pow(1 - u / d.b, 1.7);
      };
      const lift = (x) => {
        let v = 0;
        for (const d of list) {
          for (let k = -1; k <= 1; k += 1) {
            const b = bump(d, x - d.c - k * P);
            if (b > v) v = b;
          }
        }
        return v;
      };
      const top = (x) => h * base[i] + und(x) - lift(x);
      const shadeCol = mixHex(col, "#a88c64", 0.22 + 0.04 * i);
      ridge(s, st, highest(top, P), lowest(top, P) + A * 0.01, col, { color: "rgba(150,118,76,0.16)", blur: m * 0.022, dy: -m * 0.003 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, top, x0, x1, h + 2);
        pg.fillStyle = shadeCol;
        for (const d of list) {
          for (let k = -1; k <= 1; k += 1) {
            const c = d.c + k * P;
            if (c + d.b < x0 || c > x1) continue;
            const yc = top(c);
            if (yc > h * base[i] + und(c) - d.H + 0.5) continue;
            pg.beginPath();
            pg.moveTo(c, yc);
            const ex = c + d.b * 0.82;
            for (let u = 1; c + u < ex; u += 1.5) pg.lineTo(c + u, top(c + u));
            pg.lineTo(ex, top(ex));
            pg.bezierCurveTo(c + d.b * 0.35, top(ex) - d.H * 0.05, c + d.b * 0.04, yc + d.H * 0.75, c, yc);
            pg.fill();
          }
        }
      }, speed(s, [0.8, 1.5, 2.6, 4.2][i]), (x0, x1) => {
        const r = along(top)(x0, x1);
        for (const d of list) {
          for (let k = -1; k <= 1; k += 1) {
            const c = d.c + k * P;
            if (c + d.b >= x0 && c <= x1) r[1] = Math.max(r[1], top(c) + d.H * 0.75);
          }
        }
        return r;
      });
    });
    st.sky = sky(s, DUNES.sky, st.layers[0].L.y1);
    orb(s, st.sky, w * 0.77, h * 0.17, m * 0.05, "rgba(253,250,244,0.95)", "#fffaf0", [2, 3.3], [0.35, 0.2]);
    const n = Math.max(4, Math.min(16, Math.round((w * h) / 90000)));
    st.wisps = Array.from({ length: n }, () => ({
      y: h * rng.range(0.6, 0.97), life: rng.range(7, 12), off: rng() * 20, v: speed(s, rng.range(8, 14)), len: m * rng.range(0.06, 0.14), seed: Math.floor(rng() * 1e6),
    }));
    s.st = st;
  },
  draw(s, t) {
    const { ctx, w } = s;
    stack(s, t, s.st);
    ctx.lineWidth = Math.max(0.8, 1.3 * s.k);
    ctx.lineCap = "round";
    for (const p of s.st.wisps) {
      const u = (t + p.off) / p.life;
      const cyc = Math.floor(u);
      const ph = u - cyc;
      const x = hash(cyc, p.seed) * w * 1.1 - w * 0.05 + p.v * ph * p.life;
      const y = p.y + (hash(cyc, p.seed, 1) - 0.5) * p.len;
      const a = 0.55 * Math.sin(Math.PI * ph) ** 2;
      ctx.strokeStyle = rgba("#fffbf3", a);
      ctx.beginPath();
      ctx.moveTo(x - p.len, y + p.len * 0.05);
      ctx.quadraticCurveTo(x - p.len * 0.45, y - p.len * 0.06, x, y);
      ctx.stroke();
    }
  },
};

// —— Tide ——
const TIDE = {
  sky: [[0, "#070d1a"], [0.35, "#0c1528"], [0.58, "#16233a"]],
  bands: ["#15233a", "#112033", "#0e1a2c", "#0b1525", "#08101d", "#060c16"],
};

export const tide = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const rng = seeded(7211);
    const hz = h * 0.58;
    const st = { layers: [], mists: [] };
    const sea = h - hz;
    const at = [0, 0.06, 0.15, 0.29, 0.48, 0.72];
    const amp = [0.004, 0.009, 0.015, 0.023, 0.034, 0.046];
    const wl = [0.13, 0.19, 0.27, 0.37, 0.5, 0.68];
    const P = strip(s, 0, 1).P;
    TIDE.bands.forEach((col, i) => {
      const A = m * amp[i];
      const L = m * wl[i];
      const wave = harmonics(rng, P, [[L, A], [L * 0.53, A * 0.45], [L * 0.31, A * 0.22]]);
      const sum = A * 1.67;
      const y = i ? hz + sea * at[i] + sum : hz + sum * 0.5;
      const top = (x) => y + wave(x);
      ridge(s, st, y - sum, y + sum + 1, col, { color: "rgba(1,4,10,0.6)", blur: m * (0.008 + 0.003 * i), dy: -m * 0.002 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, top, x0, x1, h + 2, 2);
      }, speed(s, rng.range(2, 4) * (1 + i * 0.6)) * (i % 2 ? -1 : 1), along(top), { a: sum * 0.3, w: rng.range(0.25, 0.45), ph: rng() * TAU });
    });
    st.sky = sky(s, TIDE.sky, st.layers[0].L.y1 + 2);
    stars(s, st.sky, rng, 6500, hz * 0.9);
    st.moon = { x: moonX(s, m * 0.036), y: h * 0.2 };
    orb(s, st.sky, st.moon.x, st.moon.y, m * 0.036, "rgba(206,216,236,0.55)", "#9fb3d8");
    const n = Math.max(18, Math.min(90, Math.round((w * h) / 16000)));
    st.glints = Array.from({ length: n }, () => {
      const d = Math.pow(rng(), 0.85);
      return {
        d, y: hz + sea * (0.015 + d * 0.95), dx: (rng() - 0.5) * (0.04 + 0.18 * d) * m * 1.4, len: m * (0.008 + 0.035 * d) * rng.range(0.5, 1.3), w: rng.range(0.4, 1.1), ph: rng() * TAU,
      };
    });
    s.st = st;
  },
  draw(s, t) {
    const { ctx } = s;
    const st = s.st;
    stack(s, t, st);
    const mx = st.moon.x;
    for (const g of st.glints) {
      const sh = Math.sin(t * g.w + g.ph);
      const a = (0.06 + 0.2 * (1 - 0.55 * g.d)) * (0.25 + 0.75 * sh * sh);
      const x = mx + g.dx + Math.sin(t * 0.21 + g.ph) * g.len * 0.4;
      ctx.fillStyle = rgba("#c4d2ea", a);
      ctx.fillRect(x - g.len / 2, g.y, g.len, Math.max(0.8, (1 + g.d) * s.k));
    }
  },
};

// —— Ring planet ——
const RING = {
  bands: ["#3a2d2c", "#4a3830", "#3f302e", "#52403a", "#3a2c2e", "#47372f", "#3d2f2d", "#4c3b33", "#36292b"],
  ring: [[1.34, 1.5, "#5c4a40", 0.32], [1.53, 1.8, "#6a5646", 0.46], [1.85, 2.04, "#584840", 0.3]],
};

export const ringplanet = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const rng = seeded(8317);
    const R = m * 0.44;
    const p = { x: w * 0.1, y: h - R * 0.32, R, tilt: -0.3, flat: 0.24 };
    const st = { p };
    st.back = sky(s, [[0, "#0a0b14"], [1, "#0d0d19"]]);
    stars(s, st.back, rng, 4200, h, "#d4d0e4", 0.42);
    const out = R * 2.1;
    const rb = patch(s, p.x - out, p.y - out, p.x + out, p.y + R * 0.2);
    const ringHalf = (gg, front) => {
      gg.save();
      gg.translate(p.x, p.y);
      gg.rotate(p.tilt);
      gg.beginPath();
      if (front) gg.rect(-R * 3, 0, R * 6, R * 3);
      else gg.rect(-R * 3, -R * 3, R * 6, R * 3);
      gg.clip();
      for (const [a, b, col, al] of RING.ring) {
        gg.fillStyle = rgba(col, al);
        gg.beginPath();
        gg.ellipse(0, 0, R * b, R * b * p.flat, 0, 0, TAU);
        gg.ellipse(0, 0, R * a, R * a * p.flat, 0, 0, TAU, true);
        gg.fill();
      }
      gg.restore();
    };
    ringHalf(rb.g, false);
    st.ringBack = crop(rb);
    // The shade and the front half of the ring.
    const fp = patch(s, p.x - out, p.y - out, p.x + out, p.y + out);
    const f = fp.g;
    const lx = p.x + R * 0.55;
    const ly = p.y - R * 0.5;
    const gr = f.createRadialGradient(lx, ly, 0, lx, ly, R * 1.75);
    gr.addColorStop(0, "rgba(4,4,10,0)");
    gr.addColorStop(0.5, "rgba(4,4,10,0)");
    gr.addColorStop(0.68, "rgba(4,4,10,0.45)");
    gr.addColorStop(0.85, "rgba(4,4,10,0.7)");
    gr.addColorStop(1, "rgba(4,4,10,0.78)");
    f.fillStyle = gr;
    f.beginPath();
    f.arc(p.x, p.y, R, 0, TAU);
    f.fill();
    ringHalf(f, true);
    st.front = crop(fp);
    const N = 84;
    st.cos = Float32Array.from({ length: N + 1 }, (_, j) => Math.cos(Math.PI - (Math.PI * j) / N));
    st.sin = Float32Array.from({ length: N + 1 }, (_, j) => Math.sin(Math.PI - (Math.PI * j) / N));
    st.ya = new Float32Array(N + 1);
    st.yb = new Float32Array(N + 1);
    const nb = RING.bands.length;
    st.edges = Array.from({ length: nb - 1 }, (_, i) => ({
      y: R * (-1 + (2 * (i + 1 + rng.range(-0.3, 0.3))) / nb),
      a: R * rng.range(0.012, 0.03), k: rng.range(2.5, 6) / R, w: rng.range(0.05, 0.12) * (i % 2 ? 1 : -1), ph: rng() * TAU,
      a2: R * rng.range(0.004, 0.01), k2: rng.range(9, 16) / R, w2: rng.range(0.08, 0.2) * (i % 2 ? -1 : 1),
    }));
    st.moons = [
      { A: R * 2.75, r: R * 0.055, T: 320, ph: -0.68 },
      { A: R * 3.05, r: R * 0.04, T: 470, ph: -1.02 },
    ];
    s.st = st;
  },
  draw(s, t) {
    const { ctx } = s;
    const st = s.st;
    const { p } = st;
    const R = p.R;
    drawSky(s, st.back);
    blit(s, st.ringBack);
    const moon = (mo, back) => {
      const th = (t * TAU) / mo.T + mo.ph;
      const ly = mo.A * p.flat * Math.sin(th);
      if (ly < 0 !== back) return;
      const lx = mo.A * Math.cos(th);
      const x = p.x + lx * Math.cos(p.tilt) - ly * Math.sin(p.tilt);
      const y = p.y + lx * Math.sin(p.tilt) + ly * Math.cos(p.tilt);
      // Fade through the middle of the screen, where the reading column sits.
      const off = Math.abs(x / s.w - 0.5);
      ctx.globalAlpha = 0.2 + 0.8 * Math.min(1, Math.max(0, (off - 0.22) / 0.1));
      ctx.fillStyle = "#2a2329";
      ctx.beginPath();
      ctx.arc(x, y, mo.r, 0, TAU);
      ctx.fill();
      ctx.save();
      ctx.clip();
      ctx.fillStyle = "#5e5054";
      ctx.beginPath();
      ctx.arc(x + mo.r * 0.4, y - mo.r * 0.35, mo.r, 0, TAU);
      ctx.fill();
      ctx.restore();
      ctx.globalAlpha = 1;
    };
    for (const mo of st.moons) moon(mo, true);
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.tilt);
    const { cos, sin, edges } = st;
    const N = cos.length;
    const line = (e, ys) => {
      for (let j = 0; j < N; j += 1) {
        const x = cos[j] * R;
        const y = e ? e.y + e.a * Math.sin(e.k * x + e.w * t + e.ph) + e.a2 * Math.sin(e.k2 * x + e.w2 * t) : R + 1;
        const lim = sin[j] * R;
        ys[j] = y < -lim ? -lim : y > lim ? lim : y;
      }
    };
    let top = st.ya;
    let bot = st.yb;
    line(null, top);
    for (let j = 0; j < N; j += 1) top[j] = -sin[j] * R;
    for (let i = 0; i < RING.bands.length; i += 1) {
      line(edges[i], bot);
      ctx.fillStyle = RING.bands[i];
      ctx.beginPath();
      ctx.moveTo(cos[0] * R, top[0]);
      for (let j = 1; j < N; j += 1) ctx.lineTo(cos[j] * R, top[j]);
      for (let j = N - 1; j >= 0; j -= 1) ctx.lineTo(cos[j] * R, bot[j] + 0.7);
      ctx.fill();
      [top, bot] = [bot, top];
    }
    ctx.restore();
    blit(s, st.front);
    for (const mo of st.moons) moon(mo, false);
  },
};

// —— Cloudscape ——
const CLOUDS = {
  sky: [[0, "#e9eff4"], [0.6, "#f0f2f2"], [1, "#f4f2ee"]],
  layers: ["#f9f9f8", "#f1f4f6", "#e8edf2", "#dfe6ed"],
};

function puffs(g, list, x0, x1, P, bottom) {
  g.beginPath();
  for (const c of list) {
    for (let k = -1; k <= 1; k += 1) {
      const x = c.x + k * P;
      if (x + c.r < x0 || x - c.r > x1) continue;
      g.moveTo(x + c.r, c.y);
      g.arc(x, c.y, c.r, 0, TAU);
    }
  }
  g.fill();
  if (bottom) g.fillRect(x0, bottom.y, x1 - x0, bottom.to - bottom.y);
}

export const cloudscape = {
  fps: 20,
  wallpaper: true,
  scheme: "light",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(9029);
    const st = { layers: [], mists: [] };
    const base = [0.6, 0.7, 0.8, 0.91];
    const rad = [0.05, 0.068, 0.09, 0.12];
    const P = strip(s, 0, 1).P;
    CLOUDS.layers.forEach((col, i) => {
      const lr = seeded(9100 + i);
      const r0 = A * rad[i];
      const hump = normalise(harmonics(lr, P, [[m * (1.2 + 0.3 * i), 1], [m * 0.5, 0.35]]), P);
      const list = [];
      for (let x = 0; x < P; x += r0 * lr.range(0.55, 0.95)) {
        const r = r0 * lr.range(0.45, 1);
        list.push({ x, r, y: h * base[i] - r * lr.range(0.1, 0.5) - r0 * 1.6 * Math.pow(hump(x), 1.5) });
      }
      const top = Math.min(...list.map((c) => c.y - c.r));
      ridge(s, st, top, h * base[i] + 1, col, { color: "rgba(96,122,156,0.16)", blur: m * 0.022, dy: -m * 0.004 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        puffs(pg, list, x0, x1, P, { y: h * base[i], to: h + 2 });
      }, speed(s, [1.1, 2, 3.2, 4.8][i]), along((x) => {
        let y = h * base[i];
        for (const c of list) {
          for (let k = -1; k <= 1; k += 1) {
            const dx = x - c.x - k * P;
            if (dx > -c.r && dx < c.r) y = Math.min(y, c.y - Math.sqrt(c.r * c.r - dx * dx));
          }
        }
        return y;
      }));
    });
    st.sky = sky(s, CLOUDS.sky, st.layers[0].L.y1);
    orb(s, st.sky, w * 0.2, h * 0.16, m * 0.045, "rgba(255,255,252,0.9)", "#ffffff", [2.1, 3.5], [0.35, 0.22]);
    st.small = [0, 1, 2].map((i) => {
      const r = m * rng.range(0.018, 0.028);
      return {
        y: h * (0.1 + 0.13 * i) + rng() * h * 0.05, x0: rng() * w, v: speed(s, rng.range(2.5, 4.5)), r,
        c: Array.from({ length: 4 }, (_, j) => ({ dx: (j - 1.5) * r * 1.1, r: r * (j === 1 || j === 2 ? 1.25 : 0.85) })),
      };
    });
    s.st = st;
  },
  draw(s, t) {
    const { ctx, w } = s;
    const st = s.st;
    stack(s, t, st, (i) => {
      if (i !== -1) return;
      for (const p of st.small) {
        const span4 = p.r * 6;
        const x = wrapX(p.x0 + t * p.v, -span4, w + span4);
        for (const [fill, dy] of [["rgba(110,134,162,0.08)", -p.r * 0.22], ["rgba(252,253,253,0.95)", 0]]) {
          ctx.fillStyle = fill;
          ctx.beginPath();
          for (const c of p.c) {
            ctx.moveTo(x + c.dx + c.r, p.y - dy);
            ctx.arc(x + c.dx, p.y - dy - (c.r - p.r), c.r, 0, TAU);
          }
          ctx.rect(x - p.r * 2.5, p.y - dy, p.r * 5, p.r * 0.85);
          ctx.fill();
        }
      }
    });
  },
};

// —— Aurora ——
// Aurora bands: flat translucent colour stacked in steps from a crisp lower
// edge, each step reaching less far up, so the curtain is bright at its foot
// and fades upward in paper-thin layers. Baked per band into a patch a little
// wider than the screen (it sways), dimmed through the reading column.
function curtain(s, bands, shore = 0, steps = 4, dim = 1) {
  const { w, h } = s;
  const pad = Math.min(w, h) * 0.04;
  let lo = Infinity;
  let hi = -Infinity;
  for (const { edge, th } of bands) {
    for (let x = -pad; x <= w + pad; x += 4) {
      lo = Math.min(lo, edge(x) - th(x));
      hi = Math.max(hi, edge(x));
    }
  }
  lo = Math.max(0, lo - 2);
  hi += 2;
  // A reflection is the same band flipped about the shore line.
  const L = shore ? patch(s, -pad, Math.max(shore, 2 * shore - hi), w + pad, Math.min(h, 2 * shore - lo), true) : patch(s, -pad, lo, w + pad, hi, true);
  const g = L.g;
  if (shore) {
    g.translate(0, 2 * shore);
    g.scale(1, -1);
  }
  for (const { edge, th, col, alpha } of bands) {
    g.fillStyle = rgba(col, alpha * dim);
    for (let i = 0; i < steps; i += 1) {
      const k = 1 - i / steps;
      g.beginPath();
      g.moveTo(-pad, edge(-pad));
      for (let x = -pad; x <= w + pad; x += 3) g.lineTo(x, edge(x));
      for (let x = w + pad; x >= -pad; x -= 3) g.lineTo(x, edge(x) - th(x) * k);
      g.fill();
    }
  }
  // Nothing through the middle, where the reading column sits; a phone is
  // all column, so there the middle only dims.
  const floor = w < 700 ? 0.45 : 0;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalCompositeOperation = "destination-in";
  const gr = g.createLinearGradient(0, 0, L.c.width, 0);
  for (let i = 0; i <= 24; i += 1) {
    const f = i / 24;
    const d = Math.abs((-pad + f * (w + 2 * pad)) / w - 0.5);
    const t = Math.max(0, Math.min(1, (d - 0.15) / 0.26));
    gr.addColorStop(f, "rgba(0,0,0," + (floor + (1 - floor) * t * t * (3 - 2 * t)).toFixed(3) + ")");
  }
  g.fillStyle = gr;
  g.fillRect(0, 0, L.c.width, L.c.height);
  return crop(L);
}

// A band's motion: slow sway, bob and breathing, all pure functions of t.
function sway(s, L, t, mo, flip) {
  const { ctx } = s;
  const m = Math.min(s.w, s.h);
  const x = m * 0.025 * Math.sin(t * mo.ws + mo.ph);
  const y = m * 0.012 * Math.sin(t * mo.wb + mo.ph * 1.7) * (flip ? -1 : 1);
  if (mo.wa) ctx.globalAlpha = 0.72 + 0.28 * Math.sin(t * mo.wa + mo.ph * 2.3);
  blit(s, L, x, 0, Math.round(y * s.dpr));
  ctx.globalAlpha = 1;
}

const LAKE = {
  sky: [[0, "#060c11"], [0.35, "#0a161c"], [0.62, "#112329"]],
  aurora: [["#2f8f76", 0.055], ["#3c8f9a", 0.05], ["#2f8f76", 0.045]],
  hills: ["#1a2b31", "#101e23"],
  lake: "#060d10",
};

export const auroralake = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(10403);
    const shore = Math.round(h * 0.64 * s.dpr) / s.dpr;
    const st = { shore, hills: [] };
    st.sky = sky(s, LAKE.sky, shore);
    stars(s, st.sky, rng, 6000, shore * 0.8, "#cfe4de", 0.26);
    // The lake mirrors the sky, darker toward the viewer.
    const H = Math.round(h * s.dpr);
    const S = Math.round(shore * s.dpr);
    const above = sky(s, LAKE.sky, shore).runs;
    st.water = [];
    for (const [a, b, c] of above) {
      const ya = 2 * S - b;
      const yb = 2 * S - a;
      if (yb <= S || ya >= H) continue;
      const mid = ((ya + yb) / 2 - S) / (H - S);
      const [r, g, bl] = c.slice(4, -1).split(",").map(Number);
      const k = 0.62 - 0.3 * mid;
      st.water.push([Math.max(S, ya), Math.min(H, yb), "rgb(" + [r, g, bl].map((v) => Math.round(v * k)).join(",") + ")"]);
    }
    const last = st.water.length ? st.water[st.water.length - 1][1] : S;
    if (last < H) st.water.push([last, H, LAKE.lake]);
    st.water.sort((a, b) => a[0] - b[0]);
    const bands = LAKE.aurora.map(([col, alpha], i) => {
      const br = seeded(10500 + i);
      const f = harmonics(br, w * 3, [[w * 1.3, 1], [w * 0.55, 0.45], [w * 0.23, 0.18]]);
      const g2 = harmonics(br, w * 3, [[w * 0.8, 1], [w * 0.3, 0.4]]);
      const edge = (x) => h * (0.4 + 0.035 * i) - h * 0.1 * Math.sin((x / w) * Math.PI) * 0.6 + h * 0.035 * f(x);
      const th = (x) => h * (0.16 - 0.025 * i) * (0.75 + 0.3 * g2(x));
      return { edge, th, col, alpha };
    });
    // Two groups that sway apart, so the curtain shifts within itself.
    st.bands = [[bands[0], bands[2]], [bands[1]]].map((group, i) => ({
      L: curtain(s, group),
      R: curtain(s, group, shore, 4, 0.42),
      mo: { ws: [0.06, 0.085][i], wb: [0.1, 0.13][i], wa: [0, 0.17][i], ph: rng() * TAU },
    }));
    const P = strip(s, 0, 1).P;
    const amp = [0.17, 0.09];
    LAKE.hills.forEach((col, i) => {
      const lr = seeded(10600 + i);
      const wl = m * (0.8 + 0.4 * i) * (w > h ? 1.2 : 0.9);
      const terms = [[wl, 1], [wl / 2.2, 0.45], [wl / 4.6, 0.2], [wl / 10, 0.08]].map(([l, a]) => ({ n: Math.max(1, Math.round(P / l)), a, ph: lr() * TAU }));
      const n01 = normalise((x) => {
        let v = 0;
        for (const q of terms) {
          const sn = Math.sin((Math.PI * q.n * x) / P + q.ph);
          v += q.a * (1 - Math.sqrt(sn * sn + 0.02));
        }
        return v;
      }, P);
      const top = (x) => shore - A * amp[i] * (0.12 + 0.88 * n01(x));
      const shade = { color: "rgba(0,4,6,0.5)", blur: m * 0.018, dy: -m * 0.003 };
      const ext = shade.blur * 1.6 + Math.abs(shade.dy);
      const hiY = highest(top, P);
      const up = strip(s, hiY - ext, shore);
      paper(s, up, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, top, x0, x1, shore + 2);
      }, shade);
      const rcol = mixHex(col, LAKE.lake, 0.5);
      const down = strip(s, shore, Math.min(h, 2 * shore - hiY + ext));
      down.g.translate(0, 2 * shore);
      down.g.scale(1, -1);
      down.g.fillStyle = rcol;
      profile(down.g, top, 0, down.P, shore + 2);
      down.up = true;
      const span2 = along(top);
      st.hills.push({
        L: crop(up, col, span2, ext, s.dpr),
        R: crop(down, rcol, (x0, x1) => {
          const [a, b] = span2(x0, x1);
          return [2 * shore - b, 2 * shore - a];
        }, 1, s.dpr),
        v: speed(s, [0.6, 1.4][i]),
      });
    });
    const n = Math.max(16, Math.min(48, Math.round((w * (h - shore)) / 8000)));
    st.ripples = Array.from({ length: n }, () => {
      const d = rng();
      return {
        y: shore + (h - shore) * (0.02 + 0.96 * d), x: rng() * w, len: w * rng.range(0.03, 0.16) * (0.5 + d),
        light: rng() < 0.45, w: rng.range(0.3, 0.8), ph: rng() * TAU, v: speed(s, rng.range(-3, 3)),
      };
    });
    s.st = st;
  },
  draw(s, t) {
    const { ctx, w, dpr } = s;
    const st = s.st;
    drawSky(s, st.sky);
    for (const b of st.bands) sway(s, b.L, t, b.mo, false);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const SW = Math.round(w * dpr);
    for (const [a, b, c] of st.water) {
      ctx.fillStyle = c;
      ctx.fillRect(0, a, SW, b - a);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    for (const b of st.bands) sway(s, b.R, t, { ...b.mo, wa: 0 }, true);
    const S = Math.round(st.shore * dpr);
    for (const hl of st.hills) {
      blit(s, hl.L, t * hl.v, S);
      blit(s, hl.R, t * hl.v);
    }
    for (const r of st.ripples) {
      const sh = Math.sin(t * r.w + r.ph);
      const x = wrapX(r.x + t * r.v, -r.len, w);
      if (r.light) ctx.fillStyle = rgba("#9fd3c6", 0.02 + 0.06 * sh * sh);
      else ctx.fillStyle = rgba(LAKE.lake, 0.45 + 0.25 * sh);
      ctx.fillRect(x, r.y, r.len, Math.max(0.7, s.k));
    }
  },
};

const RIDGE = {
  sky: [[0, "#070a14"], [0.45, "#0b1220"], [0.7, "#131d2c"]],
  // Foot to crown: teal at the lower edge to violet at the top.
  aurora: [["#3a9a8a", 0.05], ["#3c8098", 0.045], ["#4b6596", 0.04], ["#5a4a8a", 0.038]],
  layers: ["#25334a", "#1b273a", "#131c2b", "#0b121d"],
};

export const auroraridge = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const A = span(s);
    const rng = seeded(11411);
    const st = { layers: [], mists: [] };
    const base = [0.66, 0.75, 0.85, 0.97];
    const ridgeA = [0.1, 0.11, 0.1, 0.08];
    const treeH = [0.045, 0.07, 0.1, 0.15];
    const P = strip(s, 0, 1).P;
    RIDGE.layers.forEach((col, i) => {
      const lr = seeded(11500 + i);
      const wl = m * (0.9 + 0.35 * i) * (w > h ? 1.2 : 0.9);
      const roll = normalise(harmonics(lr, P, [[wl, 1], [wl / 2.4, 0.5], [wl / 5.5, 0.15]]), P);
      const ground = (x) => h * base[i] - A * ridgeA[i] * Math.pow(roll(x), 1.4);
      const H = A * treeH[i];
      const trees = [];
      for (let x = lr() * H; x < P; x += H * lr.range(0.22, 0.6)) {
        // Trees gather on the crests and thin out in the hollows.
        if (lr() > 0.25 + 0.75 * roll(x)) continue;
        trees.push({ x, H: H * lr.range(0.5, 1.15), seed: Math.floor(lr() * 1e9) });
      }
      ridge(s, st, highest(ground, P) - H * 1.25, lowest(ground, P), col, { color: "rgba(0,3,10,0.4)", blur: m * 0.016, dy: -m * 0.003 }, (pg, x0, x1) => {
        pg.fillStyle = col;
        profile(pg, ground, x0, x1, h + 2);
        for (const tr of trees) {
          for (const k of [-1, 0, 1]) {
            const x = tr.x + k * P;
            if (x < x0 - H || x > x1 + H) continue;
            pine(pg, x, ground(tr.x) + tr.H * 0.06, tr.H, seeded(tr.seed));
          }
        }
      }, speed(s, [0.5, 1.1, 2, 3.4][i]), (x0, x1) => {
        const r = along(ground)(x0, x1);
        for (const tr of trees) {
          for (let k = -1; k <= 1; k += 1) {
            const x = tr.x + k * P;
            if (x < x0 - tr.H * 0.22 || x > x1 + tr.H * 0.22) continue;
            r[0] = Math.min(r[0], ground(tr.x) - tr.H * 0.99);
          }
        }
        return r;
      });
    });
    st.sky = sky(s, RIDGE.sky, st.layers[0].L.y1);
    stars(s, st.sky, rng, 4800, h * 0.75, "#d6dcf0", 0.4);
    // One broad arc around the top of the screen: high in the middle,
    // sweeping down toward the sides.
    const cy = h * 1.3;
    const rx = w * 0.78;
    const ry = h * 1.18;
    const bands = RIDGE.aurora.map(([col, alpha], i) => {
      const br = seeded(11600 + i);
      const f = harmonics(br, w * 3, [[w * 0.9, 1], [w * 0.37, 0.4], [w * 0.15, 0.15]]);
      const edge = (x) => {
        const u = Math.min(1, Math.abs(x - w / 2) / rx);
        return cy - ry * Math.sqrt(1 - u * u) - h * 0.075 * i + h * 0.02 * f(x);
      };
      const th = () => h * (0.085 + 0.01 * i);
      return { edge, th, col, alpha };
    });
    st.bands = [[bands[0], bands[1]], [bands[2], bands[3]]].map((group, i) => ({
      L: curtain(s, group),
      mo: { ws: [0.05, 0.075][i], wb: [0.08, 0.11][i], wa: [0, 0.15][i], ph: rng() * TAU },
    }));
    s.st = st;
  },
  draw(s, t) {
    const st = s.st;
    stack(s, t, st, (i) => {
      if (i === -1) for (const b of st.bands) sway(s, b.L, t, b.mo, false);
    });
  },
};

// Dots: scenes made only of points — crisp dots, soft glow points and small
// monospace glyphs, one hue per scene. Every point is a sprite pre-rendered
// once per resize at a few radius buckets and stamped with drawImage.

import { seeded, noise, hash } from "./noise.js";
import { TAU, clamp01, layer, rgba, mixHex } from "./art.js";

const smooth = (a, b, v) => {
  const x = clamp01((v - a) / (b - a));
  return x * x * (3 - 2 * x);
};
const wrap = (v, m) => ((v % m) + m) % m;

// How far a point sits from the quiet middle: 0 in the centre, ~1 at the
// edges, a little more in the corners and toward the bottom.
function edge(s, x, y) {
  const dx = (x / s.w - 0.5) * 2;
  const dy = (y / s.h - 0.42) * 2;
  return Math.sqrt(dx * dx * 0.9 + dy * dy * 0.8);
}

// A round point of radius r (CSS px). `glow` adds a soft falloff that far,
// `core` is how much of the radius stays solid. Returns { c, h } where h is
// the half-size to draw it at.
function dot(s, r, color, alpha, glow = 0, core = 1) {
  const h = r + glow + 1;
  const [c, g] = layer(s, h * 2, h * 2);
  if (glow > 0) {
    const grad = g.createRadialGradient(h, h, 0, h, h, r + glow);
    grad.addColorStop(0, rgba(color, alpha));
    grad.addColorStop(clamp01((r * core) / (r + glow)), rgba(color, alpha * 0.85));
    grad.addColorStop(clamp01(r / (r + glow)), rgba(color, alpha * 0.35));
    grad.addColorStop(1, rgba(color, 0));
    g.fillStyle = grad;
    g.fillRect(0, 0, h * 2, h * 2);
  } else {
    g.fillStyle = rgba(color, alpha);
    g.beginPath();
    g.arc(h, h, r, 0, TAU);
    g.fill();
  }
  return { c, h, d: s.dpr, n: c.width / s.dpr };
}
// Snapped to device pixels at the sprite's own size: no resampling, which
// keeps thousands of stamps cheap.
function stamp(ctx, sp, x, y) {
  const d = sp.d;
  ctx.drawImage(sp.c, Math.round((x - sp.h) * d) / d, Math.round((y - sp.h) * d) / d, sp.n, sp.n);
}

// Particle size scale: full size on a desktop, smaller (with a floor) in the
// picker cards.
const unit = (s) => Math.max(0.5, Math.min(1.5, Math.min(s.w, s.h) / 820));

// —— Halftone ——
// Each frame repaints only every Nth row of the canvas (interleaved, ground
// and dots), so the whole grid refreshes about once a second while a frame
// stays a few hundred stamps. A fresh canvas or a jump in t repaints all
// rows. (Redrawing an offscreen layer and blitting it costs ~5 ms a frame.)
const HT_BUCKETS = 48;
const HT_GROUND = "#0b0e14";
function htRow(s, d, j, t) {
  const g = s.ctx;
  const { rows, pts, sprites, f } = d;
  const R = rows[j];
  g.fillStyle = HT_GROUND;
  g.fillRect(0, R.y0, s.w, R.y1 - R.y0);
  const tx = t * 0.045;
  const ty = t * 0.03;
  for (let i = R.a; i < R.b; i += 3) {
    const x = pts[i];
    const y = pts[i + 1];
    const wave = noise(x * f + tx, y * f - ty) * 0.7 + noise(x * f * 2.3 - ty, y * f * 2.3 + tx) * 0.3;
    const b = Math.round(pts[i + 2] * smooth(0.18, 0.85, wave) * HT_BUCKETS) - 1;
    if (b >= 0) stamp(g, sprites[b], x, y);
  }
}
export const halftone = {
  fps: 15, wallpaper: true, scheme: "dark", previewK: 0.4,
  resize(s) {
    const pitch = Math.max(3.2, Math.min(s.w, s.h) / 55);
    const dy = pitch * 0.866;
    const maxR = pitch * 0.42;
    const sprites = [];
    for (let i = 1; i <= HT_BUCKETS; i += 1) {
      const f = i / HT_BUCKETS;
      sprites.push(dot(s, maxR * f, mixHex("#263244", "#3c4f6a", f), 0.6 + 0.4 * f));
    }
    const pts = [];
    const rows = [];
    const cols = Math.ceil(s.w / pitch) + 2;
    const nrows = Math.ceil(s.h / dy) + 2;
    for (let j = 0; j < nrows; j += 1) {
      const y = j * dy - pitch * 0.5;
      const a = pts.length;
      for (let i = 0; i < cols; i += 1) {
        const x = (i + (j & 1) * 0.5) * pitch - pitch * 0.5;
        const m = smooth(0.4, 1.3, edge(s, x, y));
        if (m > 0.02) pts.push(x, y, m);
      }
      const snap = (v) => Math.round(v * s.dpr) / s.dpr;
      rows.push({ a, b: pts.length, y0: snap(y - dy / 2), y1: snap(y + dy / 2) });
    }
    s.dots = { pts, rows, sprites, f: 3.2 / Math.max(s.w, s.h), n: Math.max(1, Math.ceil(pts.length / 3 / 260)), next: 0, t: -1e9 };
  },
  draw(s, t) {
    const d = s.dots;
    if (Math.abs(t - d.t) > 1.5) {
      for (let j = 0; j < d.rows.length; j += 1) htRow(s, d, j, t);
    } else {
      for (let j = d.next; j < d.rows.length; j += d.n) htRow(s, d, j, t);
      d.next = (d.next + 1) % d.n;
    }
    d.t = t;
  },
};

// —— Starfield ——
export const starfield = {
  fps: 15, wallpaper: true, scheme: "dark", previewK: 0.4,
  resize(s) {
    const u = unit(s);
    const rnd = seeded(7110);
    const [bg, g] = layer(s);
    const grad = g.createLinearGradient(0, 0, s.w * 0.3, s.h);
    grad.addColorStop(0, "#080a11");
    grad.addColorStop(1, "#0a0d15");
    g.fillStyle = grad;
    g.fillRect(0, 0, s.w, s.h);
    // A milky band through the upper-right corner, baked from soft blobs and dust.
    const m = Math.min(s.w, s.h);
    const ax = s.w * 0.42, ay = -s.h * 0.08, bx = s.w * 1.08, by = s.h * 0.62;
    g.save();
    g.globalCompositeOperation = "lighter";
    for (let i = 0; i < 46; i += 1) {
      const p = rnd();
      const off = rnd.range(-1, 1) * m * 0.07;
      const x = ax + (bx - ax) * p - off * 0.7;
      const y = ay + (by - ay) * p + off * 0.7;
      const r = m * rnd.range(0.08, 0.2);
      const blob = g.createRadialGradient(x, y, 0, x, y, r);
      blob.addColorStop(0, rgba("#8c98b8", 0.022));
      blob.addColorStop(1, rgba("#8c98b8", 0));
      g.fillStyle = blob;
      g.fillRect(x - r, y - r, r * 2, r * 2);
    }
    g.fillStyle = rgba("#b8c2d8", 0.16);
    for (let i = 0; i < (s.w * s.h) / 900; i += 1) {
      const p = rnd();
      const off = (rnd() + rnd() + rnd() - 1.5) * m * 0.1;
      const x = ax + (bx - ax) * p - off * 0.7;
      const y = ay + (by - ay) * p + off * 0.7;
      g.fillRect(x, y, 0.6, 0.6);
    }
    g.restore();
    // Stars: three layers, each a few sizes; alpha set per star.
    const layers = [
      { n: 320, r: [0.5, 0.8], a: 0.5, v: 0.0016 },
      { n: 110, r: [0.7, 1.2], a: 0.62, v: 0.0032 },
      { n: 36, r: [1.1, 1.8], a: 0.7, v: 0.0055 },
    ];
    const scale = Math.max(0.25, (s.w * s.h) / (1440 * 900));
    const stars = layers.map((L, li) => {
      const sprites = [0, 1, 2].map((q) => {
        const r = (L.r[0] + (L.r[1] - L.r[0]) * (q / 2)) * u;
        return dot(s, r, "#c8d0e0", 1, r * (li === 2 ? 1.6 : 0.8), 0.45);
      });
      const n = Math.round(L.n * Math.sqrt(scale) * Math.max(1, Math.sqrt(scale)));
      const list = [];
      for (let i = 0; i < n; i += 1) {
        list.push({
          x: rnd(), y: rnd(), q: Math.floor(rnd() * 3),
          a: L.a * rnd.range(0.45, 1),
          tw: rnd() < 0.12 ? rnd.range(0.25, 0.6) : 0, ph: rnd() * TAU,
        });
      }
      // The far layer is too small to need round sprites: plain squares are cheaper.
      const sz = li === 0 ? [0.7, 0.9, 1.1].map((v) => v * u) : null;
      return { ...L, sprites, sz, list };
    });
    s.dots = { bg, stars };
  },
  draw(s, t) {
    const { ctx } = s;
    const { bg, stars } = s.dots;
    ctx.drawImage(bg, 0, 0, s.w, s.h);
    ctx.fillStyle = "#c8d0e0";
    const W = s.w + 8;
    const H = s.h + 8;
    for (const L of stars) {
      const dx = t * L.v * s.w;
      const dy = t * L.v * s.w * 0.38;
      for (const p of L.list) {
        const x = wrap(p.x * W + dx, W) - 4;
        const y = wrap(p.y * H + dy, H) - 4;
        let a = p.a * (0.45 + 0.55 * smooth(0.2, 0.9, edge(s, x, y)));
        if (p.tw) a *= 1 - p.tw * (0.5 + 0.5 * Math.sin(t * p.tw * 2.2 + p.ph));
        ctx.globalAlpha = a;
        if (L.sz) ctx.fillRect(x, y, L.sz[p.q], L.sz[p.q]);
        else stamp(ctx, L.sprites[p.q], x, y);
      }
    }
    ctx.globalAlpha = 1;
  },
};

// —— Fireflies ——
export const fireflies = {
  fps: 20, wallpaper: true, scheme: "dark", previewK: 0.4,
  resize(s) {
    const u = unit(s);
    const m = Math.min(s.w, s.h);
    const rnd = seeded(4242);
    const [bg, g] = layer(s);
    const grad = g.createLinearGradient(0, 0, 0, s.h);
    grad.addColorStop(0, "#090f0c");
    grad.addColorStop(0.6, "#0b140f");
    grad.addColorStop(1, "#0e1b13");
    g.fillStyle = grad;
    g.fillRect(0, 0, s.w, s.h);
    // A low, faint haze over the meadow.
    const haze = g.createLinearGradient(0, s.h * 0.62, 0, s.h);
    haze.addColorStop(0, rgba("#2a3f2c", 0));
    haze.addColorStop(0.7, rgba("#2a3f2c", 0.16));
    haze.addColorStop(1, rgba("#2a3f2c", 0.05));
    g.fillStyle = haze;
    g.fillRect(0, s.h * 0.62, s.w, s.h * 0.38);
    // Grass: fine curved blades in three rows, back ones lighter and shorter.
    const rows = [
      { c: "#0b170f", hgt: 0.11, n: 1.1, wid: 1.1 },
      { c: "#08110b", hgt: 0.085, n: 1.4, wid: 1.0 },
      { c: "#050b07", hgt: 0.06, n: 1.8, wid: 0.9 },
    ];
    g.lineCap = "round";
    for (const R of rows) {
      g.strokeStyle = R.c;
      g.fillStyle = R.c;
      const base = s.h + 2;
      const step = Math.max(1.2, 3.2 * u) / R.n;
      g.fillRect(0, base - m * R.hgt * 0.25, s.w, m * R.hgt * 0.25 + 2);
      for (let x = -10; x < s.w + 10; x += step * rnd.range(0.5, 1.5)) {
        const hh = m * R.hgt * (0.35 + 0.65 * noise(x * 0.01, R.hgt * 50)) * rnd.range(0.5, 1.15);
        const lean = rnd.range(-0.35, 0.35) * hh;
        g.lineWidth = rnd.range(0.6, 1.4) * R.wid * u;
        g.beginPath();
        g.moveTo(x, base);
        g.quadraticCurveTo(x + lean * 0.15, base - hh * 0.6, x + lean, base - hh);
        g.stroke();
      }
    }
    const sprites = [0.8, 1.1, 1.5].map((f) => {
      const r = 1.6 * f * u;
      const halo = dot(s, r * 2, "#c8d880", 0.5, m * 0.02 * f + 4 * u, 0.2);
      const core = dot(s, r, "#dfe8a8", 0.9, r, 0.6);
      halo.c.getContext("2d").drawImage(core.c, halo.h - core.h, halo.h - core.h, core.h * 2, core.h * 2);
      return halo;
    });
    const n = Math.round(Math.max(20, Math.min(40, (s.w * s.h) / 40000)));
    const flies = [];
    for (let i = 0; i < n; i += 1) {
      // Mostly low and toward the sides; never the upper middle.
      let x, y;
      do {
        x = rnd();
        y = 0.25 + 0.7 * Math.sqrt(rnd());
      } while (Math.abs(x - 0.5) < 0.3 && y < 0.75);
      flies.push({ x, y, q: Math.floor(rnd() * 3), p: rnd.range(2.2, 4), ph: rnd(), sx: rnd() * 100, sy: rnd() * 100 });
    }
    s.dots = { bg, sprites, flies, amp: m * 0.12 };
  },
  draw(s, t) {
    const { ctx } = s;
    const { bg, sprites, flies, amp } = s.dots;
    ctx.drawImage(bg, 0, 0, s.w, s.h);
    for (const f of flies) {
      // Pulse: a slow swell, then a dark pause.
      const u = wrap(t / f.p + f.ph, 1.4);
      const a = 0.12 + 0.88 * (u < 1 ? Math.sin(Math.PI * u) ** 2 : 0);
      const x = f.x * s.w + (noise(f.sx + t * 0.06, f.sy) - 0.5) * amp * 2;
      const y = f.y * s.h + (noise(f.sy, f.sx + t * 0.05) - 0.5) * amp * 1.4;
      ctx.globalAlpha = a;
      stamp(ctx, sprites[f.q], x, y);
    }
    ctx.globalAlpha = 1;
  },
};

// —— Snowfall ——
export const snowfall = {
  fps: 20, wallpaper: true, scheme: "dark", previewK: 0.4,
  resize(s) {
    const u = unit(s);
    const rnd = seeded(9001);
    const [bg, g] = layer(s);
    const grad = g.createLinearGradient(0, 0, 0, s.h);
    grad.addColorStop(0, "#121722");
    grad.addColorStop(0.55, "#161d2a");
    grad.addColorStop(1, "#1d2534");
    g.fillStyle = grad;
    g.fillRect(0, 0, s.w, s.h);
    const glow = g.createRadialGradient(s.w * 0.5, s.h * 1.15, 0, s.w * 0.5, s.h * 1.15, Math.max(s.w, s.h) * 0.7);
    glow.addColorStop(0, rgba("#33405a", 0.35));
    glow.addColorStop(1, rgba("#33405a", 0));
    g.fillStyle = glow;
    g.fillRect(0, 0, s.w, s.h);
    const area = Math.max(0.2, (s.w * s.h) / (1440 * 900));
    const depths = [
      { n: 220, r: 0.7, glow: 0.6, a: 0.3, v: 0.011, sway: 0.006, sq: true },
      { n: 100, r: 1.3, glow: 1.4, a: 0.38, v: 0.018, sway: 0.01 },
      { n: 26, r: 2.4, glow: 3.2, a: 0.3, v: 0.03, sway: 0.016, sides: true },
    ].map((D) => {
      const sprite = dot(s, D.r * u, "#dbe3f0", 1, D.glow * u, 0.4);
      const list = [];
      const n = Math.round(D.n * Math.max(0.35, area));
      for (let i = 0; i < n; i += 1) {
        let x = rnd();
        if (D.sides) x = x < 0.5 ? x * 0.42 : 1 - (1 - x) * 0.42;
        list.push({ x, y: rnd(), a: D.a * rnd.range(0.55, 1), f: rnd.range(0.15, 0.35), ph: rnd() * TAU, v: rnd.range(0.8, 1.2) });
      }
      // The far flakes are specks: plain squares are cheaper than sprites.
      return { ...D, sprite, list, sq: D.sq && D.r * u * 1.6 };
    });
    s.dots = { bg, depths };
  },
  draw(s, t) {
    const { ctx } = s;
    const { bg, depths } = s.dots;
    ctx.drawImage(bg, 0, 0, s.w, s.h);
    const m = Math.min(s.w, s.h);
    ctx.fillStyle = "#dbe3f0";
    for (const D of depths) {
      const pad = D.sprite.h * 2;
      const H = s.h + pad * 2;
      for (const p of D.list) {
        const y = wrap(p.y * H + t * D.v * p.v * m, H) - pad;
        const x = p.x * s.w + Math.sin(t * p.f + p.ph) * D.sway * s.w + Math.sin(t * p.f * 0.37 + p.ph * 2) * D.sway * s.w * 0.6;
        ctx.globalAlpha = p.a * (0.4 + 0.6 * smooth(0.25, 0.95, edge(s, x, y)));
        if (D.sq) ctx.fillRect(x, y, D.sq, D.sq);
        else stamp(ctx, D.sprite, x, y);
      }
    }
    ctx.globalAlpha = 1;
  },
};

// —— Glyph field ——
const GLYPHS = ".:+*-=~.:+-·.:*=~01ax7k".split("");
const FONT = '"Red Hat Mono", ui-monospace, monospace';
function glyphAtlas(s, size) {
  const cw = Math.ceil(size * 1.1 * s.dpr) / s.dpr;
  const [c, g] = layer(s, cw * GLYPHS.length, cw);
  g.font = size + "px " + FONT;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = "#4a6a70";
  GLYPHS.forEach((ch, i) => g.fillText(ch, i * cw + cw / 2, cw / 2));
  return { c, cw, px: cw * s.dpr };
}
export const glyphfield = {
  fps: 15, wallpaper: true, scheme: "dark", previewK: 0.4,
  resize(s) {
    const cell = Math.max(10, Math.min(s.w, s.h) / 36);
    const small = cell > Math.min(s.w, s.h) / 30;
    const size = Math.round(cell * (small ? 0.8 : 0.62));
    const [bg, g] = layer(s);
    const grad = g.createRadialGradient(s.w * 0.5, s.h * 0.45, 0, s.w * 0.5, s.h * 0.45, Math.hypot(s.w, s.h) * 0.6);
    grad.addColorStop(0, "#0c1214");
    grad.addColorStop(1, "#0a0e10");
    g.fillStyle = grad;
    g.fillRect(0, 0, s.w, s.h);
    const cells = [];
    const cols = Math.ceil(s.w / cell);
    const rows = Math.ceil(s.h / cell);
    const ox = (s.w - cols * cell) / 2 + cell / 2;
    const oy = (s.h - rows * cell) / 2 + cell / 2;
    for (let j = 0; j < rows; j += 1) {
      for (let i = 0; i < cols; i += 1) {
        const x = ox + i * cell;
        const y = oy + j * cell;
        const e = smooth(0.35, 1.3, edge(s, x, y));
        if (hash(i, j, 3) > (small ? 0.08 + 0.6 * e * e : 0.02 + 0.42 * e * e)) continue;
        const snap = (v) => Math.round((v - size * 0.55) * s.dpr) / s.dpr;
        cells.push({ x: snap(x), y: snap(y), i, j, e, p: 7 + hash(i, j, 5) * 9, ph: hash(i, j, 7) });
      }
    }
    // Tiny picker cards get a brighter ink so the few glyphs still read.
    const d = { bg, cells, atlas: glyphAtlas(s, size), t: 50, boost: small ? 2.2 : 1 };
    s.dots = d;
    // The bundled face may still be loading: redo the atlas once it is in.
    if (document.fonts && !document.fonts.check(size + 'px "Red Hat Mono"')) {
      document.fonts.load(size + 'px "Red Hat Mono"').then(() => {
        if (s.dots !== d) return;
        d.atlas = glyphAtlas(s, size);
        glyphfield.draw(s, d.t);
      }, () => {});
    }
  },
  draw(s, t) {
    const { ctx } = s;
    const d = s.dots;
    d.t = t;
    const { bg, cells, atlas } = d;
    ctx.drawImage(bg, 0, 0, s.w, s.h);
    const { c, cw, px } = atlas;
    for (const k of cells) {
      // Visible for the first 60% of each cycle; the glyph changes in the dark.
      const v = t / k.p + k.ph;
      const u = v - Math.floor(v);
      if (u >= 0.6) continue;
      const a = Math.min(1, Math.sin((Math.PI * u) / 0.6) ** 2 * (0.25 + 0.6 * k.e) * d.boost);
      if (a < 0.03) continue;
      const gi = Math.floor(hash(k.i, k.j, Math.floor(v)) * GLYPHS.length);
      ctx.globalAlpha = a;
      ctx.drawImage(c, gi * px, 0, px, px, k.x, k.y, cw, cw);
    }
    ctx.globalAlpha = 1;
  },
};

// —— Pollen ——
export const pollen = {
  fps: 20, wallpaper: true, scheme: "light", previewK: 0.4,
  resize(s) {
    const u = unit(s);
    const m = Math.min(s.w, s.h);
    const rnd = seeded(2718);
    const [bg, g] = layer(s);
    const grad = g.createLinearGradient(0, 0, 0, s.h);
    grad.addColorStop(0, "#f3f0e4");
    grad.addColorStop(0.6, "#efeddf");
    grad.addColorStop(1, "#e6e8d4");
    g.fillStyle = grad;
    g.fillRect(0, 0, s.w, s.h);
    const R = Math.max(s.w, s.h) * 0.75;
    const sun = g.createRadialGradient(0, 0, 0, 0, 0, R);
    sun.addColorStop(0, rgba("#fffaf0", 0.9));
    sun.addColorStop(0.25, rgba("#fbf4dc", 0.55));
    sun.addColorStop(1, rgba("#f6eed2", 0));
    g.fillStyle = sun;
    g.fillRect(0, 0, s.w, s.h);
    for (let i = 0; i < 7; i += 1) {
      const r = m * rnd.range(0.05, 0.11);
      const [bc, bg2] = layer(s, r * 2 + 4, r * 2 + 4);
      const c = r + 2;
      const fill = bg2.createRadialGradient(c, c, r * 0.6, c, c, r);
      fill.addColorStop(0, rgba("#fffbea", 0.28));
      fill.addColorStop(0.9, rgba("#fffbea", 0.4));
      fill.addColorStop(1, rgba("#fffbea", 0));
      bg2.fillStyle = fill;
      bg2.beginPath();
      bg2.arc(c, c, r, 0, TAU);
      bg2.fill();
      const side = rnd() < 0.5;
      const x = (side ? rnd.range(0, 0.3) : rnd.range(0.7, 1)) * s.w;
      g.drawImage(bc, x - c, rnd.range(0.05, 0.95) * s.h - c, c * 2, c * 2);
    }
    const sprites = [];
    for (const col of ["#c9b77a", "#b8c49a"]) {
      for (const r of [1.6, 2.3, 3.2]) sprites.push(dot(s, r * u, col, 0.9, r * u * 0.8, 0.6));
    }
    const area = Math.max(0.3, (s.w * s.h) / (1440 * 900));
    const motes = [];
    for (let i = 0; i < Math.round(170 * area); i += 1) {
      motes.push({ x: rnd(), y: rnd(), q: Math.floor(rnd() * sprites.length), a: rnd.range(0.5, 0.95), v: rnd.range(0.6, 1.3), d: rnd.range(-1, 1), f: rnd.range(0.2, 0.45), ph: rnd() * TAU });
    }
    s.dots = { bg, sprites, motes };
  },
  draw(s, t) {
    const { ctx } = s;
    const { bg, sprites, motes } = s.dots;
    const m = Math.min(s.w, s.h);
    ctx.drawImage(bg, 0, 0, s.w, s.h);
    const W = s.w + 20;
    const H = s.h + 20;
    for (const p of motes) {
      const y = wrap(p.y * H - t * 0.012 * p.v * m, H) - 10;
      const x = wrap(p.x * W + t * 0.006 * p.d * m + Math.sin(t * p.f + p.ph) * m * 0.02, W) - 10;
      ctx.globalAlpha = p.a * (0.3 + 0.7 * smooth(0.25, 0.95, edge(s, x, y)));
      stamp(ctx, sprites[p.q], x, y);
    }
    ctx.globalAlpha = 1;
  },
};

// Line: fine line art. 1-1.5 px strokes in one ink hue at low alpha on a
// flat or near-flat ground; strokes are batched into one path per alpha.

import { seeded, noise, field, hash } from "./noise.js";
import { TAU, layer, rgba } from "./art.js";

// One device pixel on a large canvas: hairlines rasterise ~10x faster than
// wider strokes on a software canvas. Cards and phones draw 1 CSS px.
const lineW = (s) => (s.k < 1 || s.w * s.h < 500000 ? 1 : 1 / s.dpr);

// 0 in the quiet middle (centre ~70% width, upper ~85% height), 1 around
// it, 2 toward the edges and bottom: strokes take their alpha from it.
function zoneOf(s, x, y) {
  const dx = (x - s.w / 2) / (s.w * (s.w < s.h ? 0.5 : 0.36));
  const dy = (y - s.h * 0.4) / (s.h * 0.46);
  const d = dx * dx + dy * dy;
  return d < 0.45 ? 0 : d < 1 ? 1 : 2;
}
const ZONE = [0.5, 0.75, 1];

// Like curve/loop, but each quadratic piece goes to paths[zone] by its
// control point, so one line fades toward the middle.
function chain(s, paths, px, py, n, closed) {
  let cur = null;
  const seg = (sx, sy, cx, cy, ex, ey) => {
    const p = paths[zoneOf(s, cx, cy)];
    if (p !== cur) {
      p.moveTo(sx, sy);
      cur = p;
    }
    p.quadraticCurveTo(cx, cy, ex, ey);
  };
  if (closed) {
    for (let i = 0; i < n; i += 1) {
      const a = i ? i - 1 : n - 1;
      const b = i + 1 < n ? i + 1 : 0;
      seg((px[a] + px[i]) / 2, (py[a] + py[i]) / 2, px[i], py[i], (px[i] + px[b]) / 2, (py[i] + py[b]) / 2);
    }
  } else if (n === 2) {
    seg(px[0], py[0], (px[0] + px[1]) / 2, (py[0] + py[1]) / 2, px[1], py[1]);
  } else {
    for (let i = 1; i < n - 1; i += 1) {
      const sx = i === 1 ? px[0] : (px[i - 1] + px[i]) / 2;
      const sy = i === 1 ? py[0] : (py[i - 1] + py[i]) / 2;
      const ex = i === n - 2 ? px[n - 1] : (px[i] + px[i + 1]) / 2;
      const ey = i === n - 2 ? py[n - 1] : (py[i] + py[i + 1]) / 2;
      seg(sx, sy, px[i], py[i], ex, ey);
    }
  }
}

function pen(ctx, style, lw) {
  ctx.strokeStyle = style;
  ctx.lineWidth = lw;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
}

// A smooth open curve through n points (quadratic midpoints), added to the path.
function curve(ctx, px, py, n) {
  ctx.moveTo(px[0], py[0]);
  for (let i = 1; i < n - 1; i += 1) ctx.quadraticCurveTo(px[i], py[i], (px[i] + px[i + 1]) / 2, (py[i] + py[i + 1]) / 2);
  ctx.lineTo(px[n - 1], py[n - 1]);
}

// —— Contours ——
// Marching squares over a coarse grid of a drifting noise height field; the
// crossings are chained into polylines and drawn as smooth curves.
const CONTOUR = { ground: "#0e1416", ink: "#5f8f8a" };

export const contours = {
  fps: 15,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const cell = Math.max(3, Math.min(s.w, s.h) / 45);
    const nx = Math.ceil(s.w / cell) + 1;
    const ny = Math.ceil(s.h / cell) + 1;
    const C = nx + 1;
    const R = ny + 1;
    const nH = nx * R;
    const E = nH + C * ny;
    const damp = new Float32Array(C * R);
    for (let j = 0; j < R; j += 1) {
      for (let i = 0; i < C; i += 1) {
        const dx = (i * cell - s.w / 2) / (s.w * 0.36);
        const dy = (j * cell - s.h * 0.4) / (s.h * 0.46);
        const d = Math.min(1, Math.sqrt(dx * dx + dy * dy));
        damp[j * C + i] = 1 - 0.6 * (1 - d * d) * (1 - d * d);
      }
    }
    s.L = {
      cell, nx, ny, C, nH, damp,
      v: new Float32Array(C * R),
      ex: new Float32Array(E), ey: new Float32Array(E),
      l0: new Int32Array(E), l1: new Int32Array(E),
      seen: new Int32Array(E), walked: new Int32Array(E),
      touched: new Int32Array(E),
      px: new Float32Array(E + 2), py: new Float32Array(E + 2),
      stamp: 0,
      tq: NaN,
      minor: ZONE.map((z) => rgba(CONTOUR.ink, 0.3 * z)),
      major: ZONE.map((z) => rgba(CONTOUR.ink, 0.55 * z)),
    };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    ctx.fillStyle = CONTOUR.ground;
    ctx.fillRect(0, 0, w, h);
    // The field drifts ~2 px/s: tracing it 3 times a second is invisible,
    // and the frames between only stroke the cached paths.
    const tq = Math.floor(t * 3) / 3;
    if (L.tq !== tq) contourPaths(s, tq);
    const lw = lineW(s);
    for (let z = 0; z < 3; z += 1) {
      pen(ctx, L.minor[z], lw);
      ctx.stroke(L.paths[1][z]);
      pen(ctx, L.major[z], lw);
      ctx.stroke(L.paths[0][z]);
    }
  },
};

function contourPaths(s, tq) {
  const { w, h } = s;
  const L = s.L;
  const { cell, C, v, damp } = L;
  L.tq = tq;
  const sc = 3 / Math.min(w, h);
  const tt = tq * 0.02;
  for (let j = 0, n = 0; j <= L.ny; j += 1) {
    for (let i = 0; i < C; i += 1, n += 1) {
      const x = i * cell * sc;
      const y = j * cell * sc;
      const f = field(x, y, tt) + 0.35 * (noise(x * 0.4 + 7.3 + tt * 0.1, y * 0.4 - 3.1) - 0.5);
      v[n] = 0.5 + (f - 0.5) * damp[n];
    }
  }
  const major = [new Path2D(), new Path2D(), new Path2D()];
  const minor = [new Path2D(), new Path2D(), new Path2D()];
  for (let li = 0; li < 16; li += 1) contourLevel(s, li % 5 ? minor : major, 0.12 + li * 0.048);
  L.paths = [major, minor];
}

function contourLevel(s, paths, lv) {
  const L = s.L;
  const { cell, nx, ny, C, nH, v, ex, ey, l0, l1, seen, walked, touched, px, py } = L;
  const stamp = (L.stamp += 1);
  let count = 0;
  const edge = (e, p, q, horiz, i, j) => {
    if (seen[e] !== stamp) {
      seen[e] = stamp;
      l0[e] = -1;
      l1[e] = -1;
      touched[count] = e;
      count += 1;
      const f = (lv - v[p]) / (v[q] - v[p]);
      ex[e] = (horiz ? i + f : i) * cell;
      ey[e] = (horiz ? j : j + f) * cell;
    }
    return e;
  };
  const link = (a, b) => {
    if (l0[a] < 0) l0[a] = b;
    else l1[a] = b;
    if (l0[b] < 0) l0[b] = a;
    else l1[b] = a;
  };
  for (let j = 0; j < ny; j += 1) {
    for (let i = 0; i < nx; i += 1) {
      const pa = j * C + i;
      const pb = pa + 1;
      const pc = pa + C + 1;
      const pd = pa + C;
      const a = v[pa] > lv;
      const b = v[pb] > lv;
      const c = v[pc] > lv;
      const d = v[pd] > lv;
      if (a === b && b === c && c === d) continue;
      const top = a !== b ? edge(j * nx + i, pa, pb, true, i, j) : -1;
      const bot = d !== c ? edge((j + 1) * nx + i, pd, pc, true, i, j + 1) : -1;
      const lef = a !== d ? edge(nH + j * C + i, pa, pd, false, i, j) : -1;
      const rig = b !== c ? edge(nH + j * C + i + 1, pb, pc, false, i + 1, j) : -1;
      if (top >= 0 && bot >= 0 && lef >= 0 && rig >= 0) {
        const mid = (v[pa] + v[pb] + v[pc] + v[pd]) / 4 > lv;
        // Saddle: the centre decides which corners are cut off.
        if (mid === a) {
          link(top, rig);
          link(bot, lef);
        } else {
          link(top, lef);
          link(bot, rig);
        }
        continue;
      }
      const e1 = top >= 0 ? top : bot >= 0 ? bot : lef;
      const e2 = rig >= 0 ? rig : lef >= 0 && e1 !== lef ? lef : bot;
      link(e1, e2);
    }
  }
  // Open chains start at the grid border, then closed loops.
  for (const open of [true, false]) {
    for (let k = 0; k < count; k += 1) {
      const start = touched[k];
      if (walked[start] === stamp || (open && l1[start] >= 0)) continue;
      let prev = -1;
      let cur = start;
      let n = 0;
      let closed = false;
      while (cur >= 0) {
        walked[cur] = stamp;
        px[n] = ex[cur];
        py[n] = ey[cur];
        n += 1;
        const next = l0[cur] !== prev ? l0[cur] : l1[cur];
        prev = cur;
        if (next < 0) break;
        if (walked[next] === stamp) {
          closed = next === start;
          break;
        }
        cur = next;
      }
      if (n >= 2) chain(s, paths, px, py, n, closed && n > 2);
    }
  }
}

// —— Currents ——
// Long flow lines across the screen, bunched toward the bottom, thin above.
const CURRENT = { ground: "#0a1018", ink: "#4f7fa0" };

export const currents = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const rng = seeded(4211);
    const n = Math.max(14, Math.min(46, Math.round(s.h / 21)));
    const lines = [];
    for (let i = 0; i < n; i += 1) {
      const r = rng();
      let y;
      let band;
      if (i < Math.round(n * 0.6)) {
        y = 0.64 + 0.38 * Math.sqrt(r);
        band = 2;
      } else if (i < Math.round(n * 0.86)) {
        y = -0.02 + 0.26 * r * r;
        band = 1;
      } else {
        y = 0.3 + 0.3 * r;
        band = 0;
      }
      const a = band === 2 ? (y > 0.8 ? 2 : 1) : band === 1 ? (y < 0.1 ? 1 : 0) : 0;
      lines.push({ y: y * s.h, a, ph: rng.range(0, TAU), amp: band === 0 ? 0.6 : 1 });
    }
    const steps = Math.max(16, Math.round(s.w / 28));
    s.L = {
      lines, steps,
      px: new Float32Array(steps + 3), py: new Float32Array(steps + 3),
      inks: [0.16, 0.28, 0.44].map((a) => rgba(CURRENT.ink, a)),
    };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    const { px, py, steps } = L;
    ctx.fillStyle = CURRENT.ground;
    ctx.fillRect(0, 0, w, h);
    const k1 = TAU / (w * 0.85);
    const k2 = TAU / (w * 0.37);
    const q = 1 / h;
    const amp = h * 0.032;
    const dx = (w + 40) / steps;
    const lw = lineW(s);
    for (let a = 0; a < 3; a += 1) {
      ctx.beginPath();
      for (const ln of L.lines) {
        if (ln.a !== a) continue;
        const yb = ln.y;
        const A = amp * ln.amp * (0.6 + 0.6 * yb * q);
        for (let i = 0; i <= steps; i += 1) {
          const x = -20 + i * dx;
          px[i] = x;
          py[i] = yb + A * (0.55 * Math.sin(x * k1 + yb * q * 5.0 - t * 0.09)
            + 0.3 * Math.sin(x * k2 - yb * q * 7.0 + t * 0.06 + 1.3)
            + 0.1 * Math.sin(x * k2 * 2.1 + ln.ph + t * 0.11)
            + 0.7 * (noise(x * k1 * 0.6 - t * 0.012, yb * q * 4.0) - 0.5));
        }
        curve(ctx, px, py, steps + 1);
      }
      pen(ctx, L.inks[a], lw);
      ctx.stroke();
    }
  },
};

// —— Constellations ——
// A star chart turning very slowly about a pole off the top-right corner.
const STARS = { ground: "#0b0e18", ink: "#8fa0c8" };
const CHART_SPIN = TAU / 5400;

export const constellations = {
  fps: 15,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const cx = w + m * 0.3;
    const cy = -m * 0.5;
    const corners = [[0, 0], [w, 0], [0, h], [w, h]];
    const rmax = Math.max(...corners.map(([x, y]) => Math.hypot(x - cx, y - cy))) + m * 0.1;
    const rmin = Math.max(0, Math.hypot(Math.max(0, cx - w), Math.max(0, cy)) - m * 0.1);
    const angles = corners.map(([x, y]) => Math.atan2(y - cy, x - cx));
    const a0 = Math.min(...angles) - 0.3;
    const a1 = Math.max(...angles) + 0.3;
    const rng = seeded(9087);
    const ring = Math.PI * (rmax * rmax - rmin * rmin);
    const polar = () => {
      const r = Math.sqrt(rmin * rmin + rng() * (rmax * rmax - rmin * rmin));
      const a = rng() * TAU;
      return [Math.cos(a) * r, Math.sin(a) * r];
    };
    const dens = (s.k < 1 ? 45 : 80) / (m * m);
    const stars = [];
    const nStars = Math.round(ring * dens);
    for (let i = 0; i < nStars; i += 1) {
      const [x, y] = polar();
      stars.push(x, y, rng() < 0.75 ? 0 : 1);
    }
    const figures = [];
    const nFig = Math.round(ring * 7 / (m * m) * (s.k < 1 ? 0.6 : 1));
    for (let f = 0; f < nFig; f += 1) {
      const [x0, y0] = polar();
      const pts = [x0, y0];
      const segs = [];
      const count = 4 + Math.floor(rng() * 4);
      let dir = rng() * TAU;
      for (let i = 1; i < count; i += 1) {
        const from = i > 2 && rng() < 0.25 ? Math.floor(rng() * (i - 1)) : i - 1;
        dir += rng.range(-1.3, 1.3);
        const len = m * rng.range(0.045, 0.1);
        pts.push(pts[from * 2] + Math.cos(dir) * len, pts[from * 2 + 1] + Math.sin(dir) * len);
        segs.push(from, i);
      }
      if (count > 4 && rng() < 0.3) segs.push(count - 1, count - 4);
      figures.push({ x: x0, y: y0, pts, segs, reach: m * 0.4 });
    }
    s.L = {
      cx, cy, rmin, rmax, a0, a1, stars, figures,
      dot: Math.max(0.55, m / 1100),
      grid: rgba(STARS.ink, 0.1),
      line: ZONE.map((z) => rgba(STARS.ink, 0.34 * z)),
      dim: ZONE.map((z) => rgba(STARS.ink, 0.42 * z)),
      bright: ZONE.map((z) => rgba(STARS.ink, 0.72 * z)),
    };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    const m = Math.min(w, h);
    const rot = t * CHART_SPIN;
    const cs = Math.cos(rot);
    const sn = Math.sin(rot);
    const { cx, cy } = L;
    ctx.fillStyle = STARS.ground;
    ctx.fillRect(0, 0, w, h);
    const lw = lineW(s);
    // Grid: circles of declination and meridians every 15°.
    ctx.beginPath();
    const step = m * 0.3;
    for (let r = Math.ceil(L.rmin / step) * step; r < L.rmax; r += step) {
      ctx.moveTo(cx + Math.cos(L.a0) * r, cy + Math.sin(L.a0) * r);
      ctx.arc(cx, cy, r, L.a0, L.a1);
    }
    const mer = TAU / 24;
    for (let a = L.a0 - ((L.a0 - rot) % mer + mer) % mer; a < L.a1; a += mer) {
      if (a < L.a0) continue;
      ctx.moveTo(cx + Math.cos(a) * L.rmin, cy + Math.sin(a) * L.rmin);
      ctx.lineTo(cx + Math.cos(a) * L.rmax, cy + Math.sin(a) * L.rmax);
    }
    pen(ctx, L.grid, lw);
    ctx.stroke();
    // Figures, then stars: one path per zone (and dot size).
    const lines = [new Path2D(), new Path2D(), new Path2D()];
    const dots = [0, 1, 2].map(() => [new Path2D(), new Path2D(), new Path2D()]);
    const dot = (size, x, y) => {
      const r = L.dot * [0.8, 1.25, 1.6][size];
      const p = dots[size][zoneOf(s, x, y)];
      p.moveTo(x + r, y);
      p.arc(x, y, r, 0, TAU);
    };
    const gap = L.dot * 3.2;
    for (const f of L.figures) {
      const fx = cx + cs * f.x - sn * f.y;
      const fy = cy + sn * f.x + cs * f.y;
      if (fx < -f.reach || fx > w + f.reach || fy < -f.reach || fy > h + f.reach) continue;
      const path = lines[zoneOf(s, fx, fy)];
      const p = f.pts;
      const q = [];
      for (let i = 0; i < p.length; i += 2) {
        const x = cx + cs * p[i] - sn * p[i + 1];
        const y = cy + sn * p[i] + cs * p[i + 1];
        q.push(x, y);
        dot(2, x, y);
      }
      for (let i = 0; i < f.segs.length; i += 2) {
        const ax = q[f.segs[i] * 2];
        const ay = q[f.segs[i] * 2 + 1];
        const bx = q[f.segs[i + 1] * 2];
        const by = q[f.segs[i + 1] * 2 + 1];
        const d = Math.hypot(bx - ax, by - ay);
        if (d < gap * 2.5) continue;
        const ux = (bx - ax) / d * gap;
        const uy = (by - ay) / d * gap;
        path.moveTo(ax + ux, ay + uy);
        path.lineTo(bx - ux, by - uy);
      }
    }
    const st = L.stars;
    for (let i = 0; i < st.length; i += 3) {
      const x = cx + cs * st[i] - sn * st[i + 1];
      const y = cy + sn * st[i] + cs * st[i + 1];
      if (x > -2 && x < w + 2 && y > -2 && y < h + 2) dot(st[i + 2], x, y);
    }
    for (let z = 0; z < 3; z += 1) {
      pen(ctx, L.line[z], lw);
      ctx.stroke(lines[z]);
      ctx.fillStyle = L.dim[z];
      ctx.fill(dots[0][z]);
      ctx.fill(dots[1][z]);
      ctx.fillStyle = L.bright[z];
      ctx.fill(dots[2][z]);
    }
  },
};

// —— Rainfall ——
// Thin slanted streaks at three depths over a dark sky, ripples at the bottom.
const RAIN = { top: "#0d1117", ground: "#121a22", ink: "#8ea4bc" };
const SLANT = 0.16;

export const rainfall = {
  fps: 20,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const rng = seeded(3301);
    const area = w * h;
    const scale = 1;
    const depths = [
      { n: area / 9000, len: 0.016, speed: 0.07, a: 0.16 },
      { n: area / 15000, len: 0.028, speed: 0.11, a: 0.25 },
      { n: area / 26000, len: 0.042, speed: 0.16, a: 0.36 },
    ].map((d) => {
      const n = Math.max(6, Math.round(d.n * scale));
      const drops = new Float32Array(n * 3);
      for (let i = 0; i < n; i += 1) {
        drops[i * 3] = rng();
        drops[i * 3 + 1] = rng();
        drops[i * 3 + 2] = rng.range(0.8, 1.2);
      }
      return { drops, len: d.len * m, speed: d.speed * h, style: ZONE.map((z) => rgba(RAIN.ink, d.a * z)) };
    });
    const [bg, g] = layer(s);
    const gr = g.createLinearGradient(0, 0, 0, h);
    gr.addColorStop(0, RAIN.top);
    gr.addColorStop(1, RAIN.ground);
    g.fillStyle = gr;
    g.fillRect(0, 0, w, h);
    s.L = {
      depths, bg,
      ripples: Math.max(3, Math.min(10, Math.round(w / 170))),
      rings: [0.1, 0.18, 0.28, 0.4].map((a) => rgba(RAIN.ink, a)),
    };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    const m = Math.min(w, h);
    ctx.drawImage(L.bg, 0, 0, w, h);
    const lw = lineW(s);
    const span = w + h * SLANT + 20;
    for (let k = 0; k < 3; k += 1) {
      const d = L.depths[k];
      const period = h + d.len * 2;
      const paths = [new Path2D(), new Path2D(), new Path2D()];
      for (let i = 0; i < d.drops.length; i += 3) {
        const v = d.drops[i + 2];
        const y = ((d.drops[i + 1] * period + t * d.speed * v) % period) - d.len;
        const x = ((d.drops[i] * span + y * SLANT) % span) - 10;
        const l = d.len * v;
        const p = paths[zoneOf(s, x, y)];
        p.moveTo(x, y);
        p.lineTo(x + l * SLANT, y + l);
      }
      for (let z = 0; z < 3; z += 1) {
        pen(ctx, d.style[z], lw);
        ctx.stroke(paths[z]);
      }
    }
    // Ripples: each slot rings at a new spot every cycle.
    const R = m * 0.05;
    const buckets = [[], [], [], []];
    for (let i = 0; i < L.ripples; i += 1) {
      const period = 3.2 + hash(i, 1, 5) * 3;
      const ph = t / period + hash(i, 2, 5);
      const cyc = Math.floor(ph);
      const a = ph - cyc;
      const x = (0.03 + 0.94 * hash(i, cyc, 11)) * w;
      const y = h * (0.88 + 0.1 * hash(i, cyc, 13));
      for (let ring = 0; ring < 2; ring += 1) {
        const age = a - ring * 0.18;
        if (age <= 0) continue;
        const life = 1 - age;
        const b = Math.min(3, Math.floor(life * life * 4.6 * (ring ? 0.6 : 1)));
        if (b < 0 || life <= 0.04) continue;
        const rx = R * (1 - (1 - age) * (1 - age)) + 1;
        buckets[b].push(x, y, rx);
      }
    }
    for (let b = 0; b < 4; b += 1) {
      const list = buckets[b];
      if (!list.length) continue;
      ctx.beginPath();
      for (let i = 0; i < list.length; i += 3) {
        ctx.moveTo(list[i] + list[i + 2], list[i + 1]);
        ctx.ellipse(list[i], list[i + 1], list[i + 2], list[i + 2] * 0.24, 0, 0, TAU);
      }
      pen(ctx, L.rings[b], lw);
      ctx.stroke();
    }
  },
};

// —— Blueprint ——
// A drafting sheet: a fine grid, two meshing gear trains in the corners,
// centre lines, dimensions and a compass arc. Only the gears move.
const PRINT = { ground: "#0e1a2c", grid: "#3a5a80", ink: "#6f90b8" };
const PRESSURE = (20 * Math.PI) / 180;
const inv = (a) => Math.tan(a) - a;

function gearOutline(g, N, mod) {
  const r = (mod * N) / 2;
  const ra = r + mod;
  const rf = r - 1.25 * mod;
  const rb = r * Math.cos(PRESSURE);
  const half = Math.PI / (2 * N) + inv(PRESSURE);
  const flank = (rho) => half - inv(Math.acos(Math.min(1, rb / rho)));
  const r0 = Math.max(rb, rf);
  const b0 = flank(r0);
  const bt = flank(ra);
  const pitch = TAU / N;
  const at = (rho, a) => [Math.cos(a) * rho, Math.sin(a) * rho];
  g.moveTo(...at(rf, -b0));
  for (let k = 0; k < N; k += 1) {
    const c = k * pitch;
    g.lineTo(...at(rf, c - b0));
    for (let i = 0; i <= 5; i += 1) {
      const rho = r0 + ((ra - r0) * i) / 5;
      g.lineTo(...at(rho, c - flank(rho)));
    }
    g.arc(0, 0, ra, c - bt, c + bt);
    for (let i = 5; i >= 0; i -= 1) {
      const rho = r0 + ((ra - r0) * i) / 5;
      g.lineTo(...at(rho, c + flank(rho)));
    }
    g.lineTo(...at(rf, c + b0));
    g.arc(0, 0, rf, c + b0, c + pitch - b0);
  }
  g.closePath();
  return { r, ra, rf };
}

// Records a path as points (arcs flattened), so the gears can be turned in
// JS: a rotated canvas transform loses the fast hairline path.
function recorder() {
  const pts = [];
  return {
    pts,
    moveTo(x, y) { pts.push(x, y); },
    lineTo(x, y) { pts.push(x, y); },
    arc(cx, cy, r, a0, a1) {
      const n = Math.max(2, Math.ceil((a1 - a0) / 0.08));
      for (let i = 1; i <= n; i += 1) {
        const a = a0 + ((a1 - a0) * i) / n;
        pts.push(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
      }
    },
    closePath() { pts.push(pts[0], pts[1]); },
  };
}

// A gear in its own frame: toothed outline, keyed bore, hub and rim
// circles, web holes and the pitch circle (drawn dashed).
function gearShape(N, mod) {
  const body = recorder();
  const { r, ra, rf } = gearOutline(body, N, mod);
  const hub = Math.max(r * 0.22, mod * 2.5);
  const bore = hub * 0.5;
  const kw = bore * 0.32;
  const ka = Math.asin(kw / bore);
  const key = recorder();
  key.moveTo(Math.cos(-Math.PI / 2 + ka) * bore, Math.sin(-Math.PI / 2 + ka) * bore);
  key.arc(0, 0, bore, -Math.PI / 2 + ka, -Math.PI / 2 - ka + TAU);
  key.lineTo(-kw, -bore - kw * 0.9);
  key.lineTo(kw, -bore - kw * 0.9);
  key.closePath();
  const rim = rf - mod * 1.4;
  const circles = [hub];
  const holes = [];
  if (N >= 18) {
    circles.push(rim);
    const count = N >= 30 ? 6 : 5;
    for (let i = 0; i < count; i += 1) holes.push((i * TAU) / count);
  }
  return {
    polys: [body.pts, key.pts], circles, holes,
    hole: { d: (rim + hub) / 2, r: (rim - hub) * 0.15 },
    r, ra,
  };
}

function arrow(g, x, y, ang, len) {
  g.moveTo(x + Math.cos(ang + 0.32) * len, y + Math.sin(ang + 0.32) * len);
  g.lineTo(x, y);
  g.lineTo(x + Math.cos(ang - 0.32) * len, y + Math.sin(ang - 0.32) * len);
}

// A dimension between two points, offset sideways by `off`, with its label.
function dimension(g, ax, ay, bx, by, off, label, fs) {
  const d = Math.hypot(bx - ax, by - ay);
  const ux = (bx - ax) / d;
  const uy = (by - ay) / d;
  const nx = -uy * off;
  const ny = ux * off;
  const ext = Math.sign(off) * fs * 0.5;
  g.moveTo(ax + nx * 0.15, ay + ny * 0.15);
  g.lineTo(ax + nx + -uy * ext, ay + ny + ux * ext);
  g.moveTo(bx + nx * 0.15, by + ny * 0.15);
  g.lineTo(bx + nx + -uy * ext, by + ny + ux * ext);
  g.moveTo(ax + nx, ay + ny);
  g.lineTo(bx + nx, by + ny);
  const ang = Math.atan2(uy, ux);
  arrow(g, ax + nx, ay + ny, ang, fs * 0.7);
  arrow(g, bx + nx, by + ny, ang + Math.PI, fs * 0.7);
  if (!label) return;
  g.save();
  g.translate((ax + bx) / 2 + nx + -uy * fs * 0.25 * Math.sign(off), (ay + by) / 2 + ny + ux * fs * 0.25 * Math.sign(off));
  g.rotate(ang > Math.PI / 2 || ang < -Math.PI / 2 ? ang + Math.PI : ang);
  g.fillText(label, 0, 0);
  g.restore();
}

function centreMark(g, x, y, r) {
  g.moveTo(x - r, y);
  g.lineTo(x + r, y);
  g.moveTo(x, y - r);
  g.lineTo(x, y + r);
}

export const blueprint = {
  fps: 15,
  wallpaper: true,
  scheme: "dark",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const lw = lineW(s);
    const mod = m * (w < h ? 0.015 : 0.0115);
    // Trains: each gear meshes with an earlier one at angle `at`.
    const spec = [
      { N: 36, x: w - m * 0.1, y: h - m * 0.08, spin: 0.045 },
      { N: 18, of: 0, at: Math.PI + 0.42 },
      { N: 12, of: 0, at: -Math.PI / 2 - 0.32 },
      { N: 24, x: m * 0.05, y: m * 0.06, spin: -0.06 },
      { N: 14, of: 3, at: 0.38 },
    ];
    const gears = [];
    for (const sp of spec) {
      const sprite = gearShape(sp.N, mod);
      const gear = { ...sp, sprite };
      if (sp.of !== undefined) {
        const p = gears[sp.of];
        const dist = p.sprite.r + sprite.r;
        gear.x = p.x + Math.cos(sp.at) * dist;
        gear.y = p.y + Math.sin(sp.at) * dist;
      }
      gears.push(gear);
    }
    const [bg, g] = layer(s);
    g.fillStyle = PRINT.ground;
    g.fillRect(0, 0, w, h);
    const minor = m / 36;
    g.beginPath();
    for (let x = minor; x < w; x += minor) {
      g.moveTo(Math.round(x) + 0.5, 0);
      g.lineTo(Math.round(x) + 0.5, h);
    }
    for (let y = minor; y < h; y += minor) {
      g.moveTo(0, Math.round(y) + 0.5);
      g.lineTo(w, Math.round(y) + 0.5);
    }
    pen(g, rgba(PRINT.grid, 0.07), lw);
    g.stroke();
    g.beginPath();
    for (let x = minor * 5; x < w; x += minor * 5) {
      g.moveTo(Math.round(x) + 0.5, 0);
      g.lineTo(Math.round(x) + 0.5, h);
    }
    for (let y = minor * 5; y < h; y += minor * 5) {
      g.moveTo(0, Math.round(y) + 0.5);
      g.lineTo(w, Math.round(y) + 0.5);
    }
    pen(g, rgba(PRINT.grid, 0.14), lw);
    g.stroke();
    // Centre lines, dash-dot, through every gear.
    g.beginPath();
    g.setLineDash([mod * 4, mod * 1.2, mod * 0.6, mod * 1.2]);
    for (const gear of gears) {
      const e = gear.sprite.ra + mod * 2.5;
      centreMark(g, gear.x, gear.y, e);
    }
    pen(g, rgba(PRINT.ink, 0.2), lw);
    g.stroke();
    g.setLineDash([]);
    const fs = Math.max(6, m * 0.0145);
    g.font = "400 " + fs.toFixed(1) + "px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
    g.textAlign = "center";
    g.textBaseline = "alphabetic";
    g.fillStyle = rgba(PRINT.ink, 0.38);
    const text = m >= 260;
    g.beginPath();
    for (const gear of gears) centreMark(g, gear.x, gear.y, mod * 1.4);
    // Centre distances (module 3 mm).
    const [A, B, Cg, D, E] = gears;
    dimension(g, D.x, D.y, E.x, E.y, -m * 0.09, text && "a = " + ((D.N + E.N) * 1.5).toFixed(1), fs);
    dimension(g, A.x, A.y, B.x, B.y, m * 0.11, text && "a = " + ((A.N + B.N) * 1.5).toFixed(1), fs);
    dimension(g, A.x, A.y, Cg.x, Cg.y, -m * 0.08, text && "a = " + ((A.N + Cg.N) * 1.5).toFixed(1), fs);
    // Compass arc: a construction arc swung from a pricked centre, top right.
    const ox = w - m * 0.06;
    const oy = m * 0.05;
    const cr = m * 0.26;
    centreMark(g, ox, oy, mod * 1.4);
    g.moveTo(ox + Math.cos(Math.PI * 0.56) * cr, oy + Math.sin(Math.PI * 0.56) * cr);
    g.arc(ox, oy, cr, Math.PI * 0.56, Math.PI * 0.98);
    g.moveTo(ox, oy);
    g.lineTo(ox + Math.cos(Math.PI * 0.7) * cr * 1.06, oy + Math.sin(Math.PI * 0.7) * cr * 1.06);
    g.moveTo(ox + Math.cos(Math.PI * 0.62) * cr * 0.35, oy + Math.sin(Math.PI * 0.62) * cr * 0.35);
    g.arc(ox, oy, cr * 0.35, Math.PI * 0.62, Math.PI * 0.7);
    pen(g, rgba(PRINT.ink, 0.32), lw);
    g.stroke();
    if (text) {
      const ta = Math.PI * 0.66;
      g.fillText("R " + (cr / mod * 3).toFixed(0), ox + Math.cos(ta) * cr * 0.62, oy + Math.sin(ta) * cr * 0.62 - fs * 0.3);
      g.textAlign = "left";
      g.fillText("z" + A.N + " / z" + B.N + " / z" + Cg.N + "   m 3", m * 0.04, h - m * 0.04);
    }
    // Construction circle, faint, bottom left.
    g.beginPath();
    g.setLineDash([mod * 1.5, mod * 1.5]);
    g.arc(m * 0.08, h - m * 0.02, m * 0.17, -Math.PI * 0.5, 0);
    pen(g, rgba(PRINT.ink, 0.16), lw);
    g.stroke();
    g.setLineDash([]);
    s.L = { bg, gears, body: rgba(PRINT.ink, 0.45), pitch: rgba(PRINT.ink, 0.22) };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    ctx.drawImage(L.bg, 0, 0, w, h);
    const lw = lineW(s);
    const ang = [];
    L.gears.forEach((gear, i) => {
      const p = L.gears[gear.of];
      ang[i] = p ? gear.at + Math.PI - (p.N / gear.N) * (ang[gear.of] - gear.at) - Math.PI / gear.N : t * gear.spin;
    });
    ctx.beginPath();
    L.gears.forEach((gear, i) => {
      const sh = gear.sprite;
      const c = Math.cos(ang[i]);
      const sn = Math.sin(ang[i]);
      const { x, y } = gear;
      for (const q of sh.polys) {
        ctx.moveTo(x + c * q[0] - sn * q[1], y + sn * q[0] + c * q[1]);
        for (let k = 2; k < q.length; k += 2) ctx.lineTo(x + c * q[k] - sn * q[k + 1], y + sn * q[k] + c * q[k + 1]);
      }
      for (const r of sh.circles) {
        ctx.moveTo(x + r, y);
        ctx.arc(x, y, r, 0, TAU);
      }
      for (const a of sh.holes) {
        const hx = x + Math.cos(a + ang[i]) * sh.hole.d;
        const hy = y + Math.sin(a + ang[i]) * sh.hole.d;
        ctx.moveTo(hx + sh.hole.r, hy);
        ctx.arc(hx, hy, sh.hole.r, 0, TAU);
      }
    });
    pen(ctx, L.body, lw);
    ctx.stroke();
    ctx.beginPath();
    L.gears.forEach((gear, i) => {
      const dashes = gear.N * 2;
      const r = gear.sprite.r;
      for (let k = 0; k < dashes; k += 1) {
        const a = ang[i] + (k * TAU) / dashes;
        ctx.moveTo(gear.x + Math.cos(a) * r, gear.y + Math.sin(a) * r);
        ctx.arc(gear.x, gear.y, r, a, a + (TAU / dashes) * 0.6);
      }
    });
    pen(ctx, L.pitch, lw);
    ctx.stroke();
  },
};

// —— Paper planes ——
// Outlined paper planes gliding on gentle curves, each with a fading dashed trail.
const PAPER = { ground: "#f1ede6", ink: "#8a8478" };
const DASH = 0.4;
const TRAIL = 10;

function planeAt(p, t, w, h) {
  const span = w + p.margin * 2;
  const x = ((((p.x0 + p.v * t) % span) + span) % span) - p.margin;
  const y = h * (p.y0 + p.a1 * Math.sin(p.f1 * t + p.p1) + p.a2 * Math.sin(p.f2 * t + p.p2));
  return [x, y];
}

export const paperplanes = {
  fps: 20,
  wallpaper: true,
  scheme: "light",
  previewK: 0.4,
  resize(s) {
    const { w, h } = s;
    const m = Math.min(w, h);
    const rng = seeded(5150);
    const rows = [0.1, 0.8, 0.9, 0.7, 0.17, 0.84, 0.76];
    const n = w * h > 600000 ? 7 : w > 300 ? 5 : 4;
    const planes = [];
    for (let i = 0; i < n; i += 1) {
      planes.push({
        x0: rng() * w,
        v: w * rng.range(0.012, 0.02),
        margin: m * 0.12,
        y0: rows[i],
        a1: rng.range(0.025, 0.045), f1: rng.range(0.1, 0.17), p1: rng.range(0, TAU),
        a2: rng.range(0.008, 0.015), f2: rng.range(0.25, 0.4), p2: rng.range(0, TAU),
        size: Math.max(m * rng.range(0.03, 0.042), s.k < 1 ? 0 : rng.range(17, 22)) * (s.k < 1 ? 1.4 : 1),
      });
    }
    const [bg, g] = layer(s);
    g.fillStyle = PAPER.ground;
    g.fillRect(0, 0, w, h);
    const gap = m / 26;
    g.beginPath();
    const dr = Math.max(0.5, m / 1300);
    for (let y = gap / 2; y < h; y += gap) {
      for (let x = gap / 2; x < w; x += gap) {
        g.moveTo(x + dr, y);
        g.arc(x, y, dr, 0, TAU);
      }
    }
    g.fillStyle = rgba(PAPER.ink, 0.3);
    g.fill();
    s.L = {
      planes, bg,
      body: rgba(PAPER.ink, 0.62),
      fold: rgba(PAPER.ink, 0.4),
      trail: [0.1, 0.18, 0.27, 0.36].map((a) => rgba(PAPER.ink, a)),
    };
  },
  draw(s, t) {
    const { ctx, w, h } = s;
    const L = s.L;
    ctx.drawImage(L.bg, 0, 0, w, h);
    const lw = lineW(s);
    // Trails: dashes stay where they were laid and fade with age.
    for (let b = 0; b < 4; b += 1) {
      ctx.beginPath();
      for (const p of L.planes) {
        const now = Math.floor(t / DASH);
        for (let j = 1; j * DASH < TRAIL; j += 1) {
          const t0 = (now - j) * DASH;
          const age = (t - t0) / TRAIL;
          const bucket = Math.min(3, Math.floor((1 - age) * (1 - age) * 4.4));
          if (bucket !== b || age >= 1) continue;
          const t1 = Math.min(t0 + DASH * 0.5, t - p.size / p.v * 1.3);
          if (t1 <= t0) continue;
          const [ax, ay] = planeAt(p, t0, w, h);
          const [bx, by] = planeAt(p, t1, w, h);
          if (bx < ax) continue;
          const [mx, my] = planeAt(p, (t0 + t1) / 2, w, h);
          ctx.moveTo(ax, ay);
          ctx.quadraticCurveTo(2 * mx - (ax + bx) / 2, 2 * my - (ay + by) / 2, bx, by);
        }
      }
      pen(ctx, L.trail[b], lw);
      ctx.stroke();
    }
    const body = new Path2D();
    const fold = new Path2D();
    for (const p of L.planes) {
      const [x, y] = planeAt(p, t, w, h);
      const dy = h * (p.a1 * p.f1 * Math.cos(p.f1 * t + p.p1) + p.a2 * p.f2 * Math.cos(p.f2 * t + p.p2));
      const ddy = -h * (p.a1 * p.f1 * p.f1 * Math.sin(p.f1 * t + p.p1) + p.a2 * p.f2 * p.f2 * Math.sin(p.f2 * t + p.p2));
      const head = Math.atan2(dy, p.v);
      const bank = Math.max(-0.45, Math.min(0.45, (ddy / (h * p.a1 * p.f1 * p.f1)) * 0.35));
      const c = Math.cos(head) * p.size;
      const sn = Math.sin(head) * p.size;
      const P = (u, v) => [x + c * u - sn * v, y + sn * u + c * v];
      const nose = P(1, 0);
      const near = P(-0.95, 0.62 * (1 + bank));
      const far = P(-0.8, -0.42 * (1 - bank));
      const tail = P(-0.7, 0.04);
      const keel = P(-0.66, 0.2 + 0.05 * bank);
      body.moveTo(...nose);
      body.lineTo(...far);
      body.lineTo(...tail);
      body.lineTo(...near);
      body.closePath();
      fold.moveTo(...nose);
      fold.lineTo(...tail);
      fold.moveTo(P(0.55, 0.03)[0], P(0.55, 0.03)[1]);
      fold.lineTo(...keel);
      fold.lineTo(...tail);
    }
    pen(ctx, L.fold, lw);
    ctx.stroke(fold);
    pen(ctx, L.body, lw);
    ctx.stroke(body);
  },
};

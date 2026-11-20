// Scenes: the aurora skies. Six night skies where the aurora is the whole
// picture, all drawn by one renderer: curtains whose hem folds and ripples,
// rays of light running up from it (green at the hem, teal, then violet at
// the tops, a pink fringe below), painted as a smooth low-resolution glow
// with rays of glyph tiles shimmering through it. A curtain lies along a
// line across the sky, or around a centre: overhead (the corona, rays
// converging on the zenith) or the planet itself (seen from orbit).

import { noise, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, rgb } from "./tiles.js";

const TAU = Math.PI * 2;
const ACELL = 6;
const LUT = 64;
const BUCKETS = 6;
const LO = -0.16;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
// Soft clip for the summed light, so overlapping curtains saturate instead of burning out.
const TONE = Float32Array.from({ length: 1024 }, (_, i) => 255 * (1 - Math.exp(-i / 2 / 190)));
// exp(-x) for x in [0, 16), in steps of 1/32.
const EXP = Float32Array.from({ length: 512 }, (_, i) => Math.exp(-i / 32));
const ex = (x) => (x < 16 ? EXP[(x * 32) | 0] : 0);

// Colour along a curtain, v from LO (the fringe under the hem) to 1 (the tops).
function lut(stops) {
  const out = new Float32Array(LUT * 3);
  const cs = stops.map(([v, c]) => [v, rgb(c)]);
  for (let i = 0; i < LUT; i += 1) {
    const v = LO + ((1 - LO) * i) / (LUT - 1);
    let j = 0;
    while (j < cs.length - 2 && v > cs[j + 1][0]) j += 1;
    const [v0, a] = cs[j];
    const [v1, b] = cs[j + 1];
    const f = clamp01((v - v0) / (v1 - v0 || 1));
    for (let ch = 0; ch < 3; ch += 1) out[i * 3 + ch] = a[ch] + (b[ch] - a[ch]) * f;
  }
  return out;
}
const hexOf = (c) => "#" + Array.from(c, (v) => Math.max(0, Math.min(255, Math.round(v / 4) * 4)).toString(16).padStart(2, "0")).join("");

export const GREEN = [[-0.16, "#0a1a2a"], [-0.07, "#5a3a7a"], [-0.015, "#f070b0"], [0.03, "#7dffb4"], [0.22, "#3cf09c"], [0.5, "#2ed2b8"], [0.75, "#4f8ef0"], [1, "#9a6ae6"]];
export const TEAL = [[-0.16, "#062a2a"], [-0.04, "#3fd8c8"], [0.06, "#6ff8e0"], [0.4, "#38c8d8"], [0.75, "#4f78e8"], [1, "#7a5ad8"]];
const VIOLET = [[-0.16, "#200a3a"], [-0.04, "#c070f0"], [0.06, "#b48cff"], [0.4, "#8a74f0"], [0.75, "#5f60d8"], [1, "#d070d0"]];
const CRIMSON = [[-0.16, "#0a0a10"], [-0.04, "#1a4a38"], [-0.005, "#40a070"], [0.035, "#a04a5a"], [0.14, "#e0304e"], [0.45, "#e82a5a"], [0.75, "#c0309a"], [1, "#7a3ae0"]];
const CORONA = [[-0.16, "#0a1a2a"], [-0.06, "#6a3a7a"], [-0.01, "#ff5fa8"], [0.03, "#9affc0"], [0.25, "#44f0a0"], [0.55, "#30d0c8"], [0.8, "#7a84f0"], [1, "#c27ae8"]];

// —— The sky: gradient, painted stars, the Milky Way, a planet ——
function paintSky(s, R, cfg) {
  const { w, h, k } = s;
  const [sky, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, h);
  cfg.sky.forEach(([at, c]) => lin.addColorStop(at, c));
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const area = Math.min(1.4, (w * h) / 1.3e6);
  const P = s.planet;
  const inPlanet = (x, y) => P && (x - P.x) ** 2 + (y - P.y) ** 2 < P.r * P.r;
  if (cfg.milky) {
    const M = cfg.milky;
    const L = Math.hypot(w, h);
    g.save();
    g.translate(w * M.x, h * M.y);
    g.rotate(M.angle);
    const band = M.width * Math.min(w, h);
    g.filter = `blur(${Math.max(2, band * 0.35)}px)`;
    g.fillStyle = `rgba(150,160,215,${M.alpha})`;
    g.fillRect(-L, -band / 2, L * 2, band);
    for (let i = 0; i < 70; i += 1) {
      const r = band * R.range(0.15, 0.45);
      g.fillStyle = `rgba(${R() < 0.4 ? "200,180,210" : "160,175,230"},${M.alpha * R.range(0.4, 1.1)})`;
      g.beginPath();
      g.ellipse(R.range(-L * 0.6, L * 0.6), (R() - 0.5) * band * 0.7, r * 1.4, r, 0, 0, TAU);
      g.fill();
    }
    g.filter = `blur(${Math.max(1, band * 0.08)}px)`;
    g.fillStyle = `rgba(2,3,10,${Math.min(0.6, M.alpha * 3)})`;
    for (let i = 0; i < 30; i += 1) {
      g.beginPath();
      g.ellipse(R.range(-L * 0.6, L * 0.6), band * (0.04 + (R() - 0.5) * 0.12), band * R.range(0.3, 0.8), band * R.range(0.03, 0.07), 0, 0, TAU);
      g.fill();
    }
    g.filter = "none";
    for (let i = 0; i < 900 * area; i += 1) {
      const x = R.range(-L / 2, L / 2);
      const y = (R() + R() + R() - 1.5) * band * 0.5;
      g.fillStyle = `rgba(225,230,255,${R.range(0.08, 0.5)})`;
      const r = R.range(0.5, 1.1) * k;
      g.fillRect(x, y, r, r);
    }
    g.restore();
  }
  for (let i = 0; i < 380 * cfg.stars * area; i += 1) {
    const x = R() * w;
    const y = Math.pow(R(), 1.15) * h * (cfg.starFloor || 1);
    const b = Math.pow(R(), 2.6);
    if (inPlanet(x, y)) continue;
    g.fillStyle = `rgba(${R() < 0.15 ? "255,228,196" : R() < 0.25 ? "196,212,255" : "232,238,255"},${0.1 + 0.7 * b})`;
    const r = (0.5 + 1.2 * b) * k;
    g.fillRect(x, y, r, r);
  }
  if (cfg.zenith) {
    const Z = s.center;
    const zg = g.createRadialGradient(Z.x, Z.y, 0, Z.x, Z.y, s.L * 0.5);
    zg.addColorStop(0, "rgba(120,80,180,0.16)");
    zg.addColorStop(1, "rgba(60,40,120,0)");
    g.fillStyle = zg;
    g.fillRect(0, 0, w, h);
  }
  if (cfg.horizon) {
    const hg = g.createLinearGradient(0, h * 0.6, 0, h);
    hg.addColorStop(0, "rgba(40,200,140,0)");
    hg.addColorStop(1, cfg.horizon);
    g.fillStyle = hg;
    g.fillRect(0, h * 0.6, w, h * 0.4);
  }
  if (cfg.wash) {
    g.save();
    g.globalCompositeOperation = "lighter";
    for (const [x, y, rx, ry, c] of cfg.wash) {
      const R0 = Math.max(rx * w, 1);
      const wg = g.createRadialGradient(0, 0, 0, 0, 0, R0);
      wg.addColorStop(0, c);
      wg.addColorStop(1, "rgba(0,0,0,0)");
      g.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
      g.translate(x * w, y * h);
      g.scale(1, (ry * h) / R0);
      g.fillStyle = wg;
      g.fillRect(-R0, -R0, R0 * 2, R0 * 2);
    }
    g.restore();
  }
  dotGrid(g, s, cfg.grid || "#5d8fa0", 0.16);
  if (P) paintPlanet(s, g, R);
  return sky;
}

// The night side from orbit: a dark disc with city lights, a thin bright
// line of atmosphere and a fainter green airglow just above it.
function paintPlanet(s, g, R) {
  const { w, h, k, cell } = s;
  const P = s.planet;
  g.save();
  g.beginPath();
  g.arc(P.x, P.y, P.r, 0, TAU);
  g.clip();
  const body = g.createRadialGradient(P.x, P.y, P.r * 0.9, P.x, P.y, P.r);
  body.addColorStop(0, "#010307");
  body.addColorStop(0.92, "#030a16");
  body.addColorStop(1, "#0a1c34");
  g.fillStyle = body;
  g.fillRect(0, 0, w, h);
  const lights = ["#ffd080", "#ffbe60", "#ffe2a8"];
  for (let i = 0; i < 220 * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    const x = Math.floor((R() * w) / cell) * cell;
    const y = Math.floor((P.y - Math.sqrt(Math.max(0, P.r * P.r - (x - P.x) ** 2)) + Math.pow(R(), 1.6) * h * 0.3) / cell) * cell;
    if (noise(x * 0.012 / k, y * 0.012 / k) < 0.55) continue;
    put(g, s, tile(s, R.pick([".", ".", ":", "+"]), null, R.pick(lights)), x, y, R.range(0.15, 0.55));
  }
  g.globalAlpha = 1;
  g.restore();
  g.save();
  g.lineWidth = Math.max(4, 14 * k);
  g.filter = `blur(${Math.max(2, 8 * k)}px)`;
  g.strokeStyle = "rgba(70,140,255,0.35)";
  g.beginPath();
  g.arc(P.x, P.y, P.r + 4 * k, 0, TAU);
  g.stroke();
  g.filter = "none";
  g.lineWidth = Math.max(1, 1.6 * k);
  g.strokeStyle = "rgba(150,205,255,0.75)";
  g.beginPath();
  g.arc(P.x, P.y, P.r, 0, TAU);
  g.stroke();
  g.lineWidth = Math.max(1, 1.2 * k);
  g.strokeStyle = "rgba(120,255,170,0.22)";
  g.beginPath();
  g.arc(P.x, P.y, P.r + 7 * k, 0, TAU);
  g.stroke();
  g.restore();
}

// —— The aurora field ——
// Each low-res cell knows its bin (a column, or an angle around the centre)
// and rho (its y, or its distance from the centre). Per frame each curtain
// computes its hem, brightness and rays once per bin, then lights the cells.
export function buildField(s, cfg, R) {
  const { w, h, k } = s;
  const acell = (s.acell = ACELL * k);
  const aw = (s.aw = Math.ceil(w / acell) + 1);
  const ah = (s.ah = Math.ceil(h / acell) + 1);
  const n = aw * ah;
  s.bin = new Uint16Array(n);
  s.rho = new Float32Array(n);
  s.acc = new Float32Array(n * 3);
  s.wmax = new Float32Array(n);
  s.dom = new Uint8Array(n);
  s.aur = document.createElement("canvas");
  s.aur.width = aw;
  s.aur.height = ah;
  s.aurCtx = s.aur.getContext("2d");
  s.aurImg = s.aurCtx.createImageData(aw, ah);
  s.aurImg.data.fill(255);
  if (cfg.mode === "line") {
    s.nb = aw;
    s.bx = new Float32Array(aw);
    s.by = new Float32Array(aw);
    s.bp = new Float32Array(aw);
    for (let x = 0; x < aw; x += 1) {
      s.bx[x] = x * acell;
      s.bp[x] = (x * acell) / w;
    }
    for (let y = 0; y < ah; y += 1) for (let x = 0; x < aw; x += 1) {
      s.bin[y * aw + x] = x;
      s.rho[y * aw + x] = y * acell;
    }
    s.L = h;
  } else {
    const C = s.center;
    const th = new Float32Array(n);
    let t0 = Infinity;
    let t1 = -Infinity;
    for (let y = 0; y < ah; y += 1) for (let x = 0; x < aw; x += 1) {
      const dx = x * acell - C.x;
      const dy = y * acell - C.y;
      const i = y * aw + x;
      th[i] = Math.atan2(dx, -dy);
      s.rho[i] = Math.hypot(dx, dy);
      t0 = Math.min(t0, th[i]);
      t1 = Math.max(t1, th[i]);
    }
    const full = t1 - t0 > 5.5;
    if (full) [t0, t1] = [-Math.PI, Math.PI];
    const nb = (s.nb = Math.max(16, Math.min(4000, Math.ceil(((t1 - t0) * s.L) / acell))));
    s.bx = new Float32Array(nb);
    s.by = new Float32Array(nb);
    s.bp = new Float32Array(nb);
    for (let b = 0; b < nb; b += 1) {
      const a = t0 + ((t1 - t0) * b) / (nb - (full ? 0 : 1));
      s.bx[b] = C.x + Math.sin(a) * s.L;
      s.by[b] = C.y - Math.cos(a) * s.L;
      s.bp[b] = full ? 0.5 : s.bx[b] / w;
    }
    for (let i = 0; i < n; i += 1) s.bin[i] = Math.min(nb - 1, Math.round(((th[i] - t0) / (t1 - t0)) * (nb - (full ? 0 : 1)))) % nb;
  }
  // Cells grouped by bin, each group sorted by rho, so a curtain visits only
  // the cells between its hem and its tops.
  const count = new Uint32Array(s.nb + 1);
  for (let i = 0; i < n; i += 1) count[s.bin[i] + 1] += 1;
  for (let b = 0; b < s.nb; b += 1) count[b + 1] += count[b];
  s.b0 = count.slice();
  const fill = count.slice();
  const cs = (s.cs = new Uint32Array(n));
  for (let i = 0; i < n; i += 1) cs[fill[s.bin[i]]++] = i;
  if (cfg.mode !== "line") for (let b = 0; b < s.nb; b += 1) cs.subarray(s.b0[b], s.b0[b + 1]).sort((x, y) => s.rho[x] - s.rho[y]);
  s.cr = Float32Array.from(cs, (i) => s.rho[i]);
  s.curt = cfg.curtains.map((c, ci) => ({
    ...c,
    ph: R() * 100,
    ci,
    lut: lut(c.lut),
    hem: new Float32Array(s.nb),
    tall: new Float32Array(s.nb),
    I: new Float32Array(s.nb),
    ray: new Float32Array(s.nb),
  }));
}

export function field(s, cfg, t) {
  const { acc, wmax, dom, cs, cr, b0, nb, bx, by, bp, k, L, acell } = s;
  acc.fill(0);
  wmax.fill(0);
  const dir = cfg.mode === "orbit" ? -1 : 1;
  const breathe = cfg.breathe ? 1 + 0.035 * Math.sin(t * 0.32) + 0.015 * Math.sin(t * 0.71) : 1;
  for (const c of s.curt) {
    const { hem, tall, I, ray, ph } = c;
    const sp = c.drift * k * t;
    const fw = 0.0016 / k;
    const ff = (c.fold || 0.0024) / k;
    const fr = (c.rays || 0.045) / k;
    const fp = 0.0011 / k;
    let pulse = 1;
    let pc = 0;
    let surge = 0;
    if (cfg.storm) {
      pulse = 0.6 + 1.5 * Math.pow(noise(t * 0.22 + ph, c.ci * 4.1), 3);
      pc = noise(t * 0.05, ph) * 1.6 - 0.3;
      surge = 1.4 * Math.pow(noise(t * 0.4, ph + 9), 2);
    }
    const gain = c.gain * pulse * (cfg.breathe ? 0.85 + 0.25 * Math.sin(t * 0.32 + c.ci) : 1);
    for (let b = 0; b < nb; b += 1) {
      const p = bp[b];
      const u = bx[b] + sp;
      const v = by[b];
      let wave = 1.3 * (noise(u * fw + ph, v * fw + t * 0.025) - 0.5);
      if (cfg.mode === "line") wave += 0.6 * Math.sin((u * (c.f || 0.004)) / k + t * 0.07 + ph) + 0.25 * Math.sin((u * 0.011) / k - t * 0.21 + ph * 2);
      hem[b] = L * (c.y + c.amp * wave) * breathe;
      let env = clamp01((p - c.x0) / 0.3) * clamp01((c.x1 - p) / 0.3);
      env *= env * (3 - 2 * env);
      if (c.patch && env > 0) env *= clamp01((noise(u * fp + ph * 1.7, v * fp + t * 0.012) - 0.5 + c.patch) / 0.5);
      if (env <= 0) {
        I[b] = 0;
        continue;
      }
      const fold = Math.pow(noise(u * ff + ph + t * 0.018, v * ff + c.ci * 3.3), 1.4);
      const rr = 0.62 * noise(u * fr + t * 0.22, v * fr + c.ci * 7.1 + t * 0.07) + 0.38 * noise(u * fr * 0.31 - t * 0.09, v * fr * 0.31 + ph);
      ray[b] = clamp01((rr - 0.2) * 1.5) ** 1.5;
      let gl = env * (0.5 + 0.5 * fold) * gain;
      if (surge) gl *= 1 + surge * Math.exp(-(((p - pc) / 0.16) ** 2));
      I[b] = gl;
      tall[b] = L * c.hr * (0.7 + 0.5 * fold) * (0.8 + 0.4 * ray[b]) * (0.5 + 0.5 * env);
    }
    // Where the hem turns edge-on to us the curtain folds over itself and glows brighter.
    for (let b = 1; b < nb - 1; b += 1) if (I[b] > 0) I[b] *= 1 + Math.min(1, (Math.abs(hem[b + 1] - hem[b - 1]) / (2 * acell)) * 0.7);
    const lu = c.lut;
    const dk = c.ci * BUCKETS;
    const pink = c.fringe ?? 0.7;
    const soft = c.soft || 0.03;
    const base = c.base || 0.04;
    const body = c.body ?? 0.22;
    for (let b = 0; b < nb; b += 1) {
      const Ib = I[b];
      if (Ib <= 0) continue;
      const H = tall[b];
      const rb = ray[b];
      const fe = H * soft + acell * 1.5;
      const hm = hem[b];
      const lo = dir > 0 ? hm - H : hm - fe * 3;
      const hi = dir > 0 ? hm + fe * 3 : hm + H;
      let j0 = b0[b];
      let j1 = b0[b + 1];
      while (j0 < j1) {
        const m = (j0 + j1) >> 1;
        if (cr[m] < lo) j0 = m + 1;
        else j1 = m;
      }
      const amp = Ib * (0.4 + 0.6 * rb);
      const ife = 1 / fe;
      const ic = 1 / (0.08 * H + acell);
      const ib = 1 / (0.4 * H);
      for (let j = j0, end = b0[b + 1]; j < end && cr[j] <= hi; j += 1) {
        const d = dir * (hm - cr[j]);
        const v = d / H;
        let a;
        if (d < 0) a = (0.55 + base + 0.8 * rb) * ex(-d * ife) * pink;
        else a = 0.55 * ex(d * ic) + (base + 0.8 * rb) * (1 - v) * (1 - v) * (0.35 + 0.65 * ex(d * ib)) + body * (1 - v) * (1 - v);
        a *= amp;
        if (a < 0.004) continue;
        let li = Math.round(((v - LO) / (1 - LO)) * (LUT - 1));
        if (li < 0) li = 0;
        const i = cs[j];
        const o = i * 3;
        acc[o] += a * lu[li * 3];
        acc[o + 1] += a * lu[li * 3 + 1];
        acc[o + 2] += a * lu[li * 3 + 2];
        if (a > wmax[i]) {
          wmax[i] = a;
          dom[i] = dk + Math.min(BUCKETS - 1, Math.floor((li / LUT) * BUCKETS));
        }
      }
    }
  }
  const px = s.aurImg.data;
  // A phone's transcript covers the whole width and dims it all: glow harder there.
  const gain = 3.6 * (cfg.glow || 1) * (s.w < 700 ? 1.45 : 1);
  for (let i = 0, j = 0; i < acc.length; i += 3, j += 4) {
    px[j] = TONE[Math.min(1023, (acc[i] * gain) | 0)];
    px[j + 1] = TONE[Math.min(1023, (acc[i + 1] * gain) | 0)];
    px[j + 2] = TONE[Math.min(1023, (acc[i + 2] * gain) | 0)];
  }
  s.aurCtx.putImageData(s.aurImg, 0, 0);
}

// Glyph rays: each tile reads the field under it; the glyph leans along the
// ray (radial around a centre), its colour comes from the brightest curtain
// there, and a slow noise lets it shimmer.
export function buildRays(s, cfg) {
  const { w, h, cell, acell, aw, ah } = s;
  const cols = Math.ceil(w / cell);
  const rows = Math.ceil(h / cell);
  s.rt = [];
  const C = s.center;
  for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) {
    const x = (c + 0.5) * cell;
    const y = (r + 0.5) * cell;
    if (cfg.rayFloor && y > h * cfg.rayFloor) continue;
    const ax = Math.min(aw - 1, Math.round(x / acell));
    const ay = Math.min(ah - 1, Math.round(y / acell));
    let d = 0;
    if (C) {
      const dx = Math.abs(x - C.x);
      const dy = Math.abs(y - C.y);
      d = dx < dy * 0.41 ? 0 : dy < dx * 0.41 ? 2 : (x - C.x) * (y - C.y) > 0 ? 3 : 1;
    }
    // Glyph rays thin out behind the centre column, where the text sits.
    const calm = w < 700 ? 0.6 : clamp01((Math.abs(x / w - 0.5) - 0.1) / 0.22);
    s.rt.push(ay * aw + ax, c, r, d, Math.round(100 * (0.12 + 0.88 * calm)));
  }
  s.rt = Int32Array.from(s.rt);
  const GL = [["|", "!", ":", "'", "."], ["/", "/", ":", "'", "."], ["-", "-", "~", "'", "."], ["\\", "\\", ":", "'", "."]];
  s.rayIds = s.curt.flatMap((cu) => Array.from({ length: BUCKETS }, (_, bk) => {
    const li = Math.min(LUT - 1, Math.round(((bk + 0.5) / BUCKETS) * LUT));
    const ink = mixc(hexOf(cu.lut.subarray(li * 3, li * 3 + 3)), "#ffffff", 0.18);
    return GL.map((set) => set.map((gl) => tile(s, gl, null, ink)));
  }));
}

export function drawRays(s, g, t, cfg) {
  const { rt, acc, wmax, dom, cell, rayIds } = s;
  const thr = cfg.rayThr || 0.13;
  const ga = cfg.rayAlpha || 1;
  for (let j = 0; j < rt.length; j += 5) {
    const o = rt[j];
    const I = (acc[o * 3] + acc[o * 3 + 1] + acc[o * 3 + 2]) / 420;
    if (I < thr || wmax[o] <= 0) continue;
    const c = rt[j + 1];
    const r = rt[j + 2];
    const sh = noise(c * 0.85 + t * 1.3, r * 0.22 - t * 0.45);
    if (sh < 0.4) continue;
    const lv = I > 0.75 ? 0 : I > 0.5 ? 1 : I > 0.3 ? 2 : I > 0.2 ? 3 : 4;
    put(g, s, rayIds[dom[o]][rt[j + 3]][lv], c * cell, r * cell, Math.min(0.9, (0.18 + I * 0.8) * (sh - 0.28) * 1.9 * ga) * rt[j + 4] * 0.01);
  }
}

// A low ridge in tiles along the very bottom: one dark continuous
// silhouette, its crest lit faintly by the aurora.
function paintRidge(s, R) {
  const { w, h, cell } = s;
  const [front, g] = layer(s);
  const cols = Math.ceil(w / cell) + 1;
  const foot = Math.ceil(h / cell);
  const lift = Math.max(4, Math.round((h * 0.13) / cell));
  // Sharp peaks falling away at about a row per column, as in tile-land's ranges.
  const peaks = [];
  for (let c = -R.range(2, 8); c < cols + 10; c += R.range(5, 13)) peaks.push({ c, up: Math.round(lift * R.range(0.3, 1)), fall: R.range(0.6, 1.1) });
  const ridge = Array.from({ length: cols + 2 }, (_, i) => {
    let best = Infinity;
    for (const pk of peaks) best = Math.min(best, foot - pk.up + Math.abs(i - 1 - pk.c) * pk.fall);
    return Math.min(foot - 1, Math.round(best));
  });
  for (let c = 0; c < cols; c += 1) {
    const top = ridge[c + 1];
    const slope = ridge[c + 2] - ridge[c];
    const peak = top < ridge[c] && top <= ridge[c + 2];
    g.fillStyle = "#03070a";
    g.fillRect(c * cell, (top + 0.5) * cell, cell, h);
    for (let r = top; r <= foot; r += 1) {
      const down = (r - top) / Math.max(1, foot - top + 1);
      const crest = r === top;
      const fill = crest ? (slope >= 0 ? "#173232" : "#112628") : mixc(slope > 0 ? "#0c181e" : "#09141a", "#04080b", down);
      let glyph;
      if (crest) glyph = peak ? "^" : slope > 0 ? "\\" : slope < 0 ? "/" : "-";
      else glyph = (c * 7 + r * 11) % 5 === 0 ? "%" : (c + r * 2) % 3 === 0 ? ";" : ":";
      put(g, s, tile(s, glyph, fill, crest ? "#5fae96" : mixc(fill, "#3a5a60", 0.4)), c * cell, r * cell);
    }
  }
  g.globalAlpha = 1;
  return front;
}

// —— Scenes ——
function build(s, cfg) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  s.center = null;
  s.planet = null;
  if (cfg.mode === "corona") {
    s.center = { x: w * 0.5, y: h * (h > w ? 0.4 : 0.38) };
    s.L = 0.5 * Math.hypot(w, h);
  } else if (cfg.mode === "orbit") {
    const r = Math.max(w, h) * (h > w ? 1.1 : 1.25);
    s.planet = { x: w * 0.5, y: h * (h > w ? 0.76 : 0.73) + r, r };
    s.center = s.planet;
    s.L = r;
  }
  s.sky = paintSky(s, R, cfg);
  const starGlyphs = ["+", "*", ".", "+", "'", "."];
  s.stars = [];
  for (let i = 0; i < (cfg.glyphStars || 50) * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    const x = Math.floor((R() * w) / cell) * cell;
    const y = Math.floor((Math.pow(R(), 1.2) * h * (cfg.starFloor || 0.9)) / cell) * cell;
    if (s.planet && (x - s.planet.x) ** 2 + (y - s.planet.y) ** 2 < (s.planet.r + cell) ** 2) continue;
    s.stars.push({ x, y, id: tile(s, starGlyphs[Math.floor(R() * starGlyphs.length)], null, R() < 0.2 ? "#ffe9c4" : R() < 0.3 ? "#cfe0ff" : "#eef3ff"), p: R.range(2, 7), ph: R() * TAU, a: R.range(0.4, 0.95) });
  }
  buildField(s, cfg, R);
  buildRays(s, cfg);
  s.front = null;
  if (cfg.ridge) s.front = paintRidge(s, R);
  s.meteor = R() * 1000;
  s.meteorIds = ["*", "-", "-", ".", "."].map((gl) => tile(s, gl, null, "#ffffff"));
}

function draw(s, t, cfg) {
  if (s.fontStale) build(s, cfg);
  const { ctx, w, h, cell, k } = s;
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(s.sky, 0, 0, w, h);
  for (const st of s.stars) put(ctx, s, st.id, st.x, st.y, st.a * (0.25 + 0.75 * (0.5 + 0.5 * Math.sin((TAU * t) / st.p + st.ph))));
  field(s, cfg, t);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "lighter";
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(s.aur, 0, 0, s.aw, s.ah, -s.acell / 2, -s.acell / 2, s.aw * s.acell, s.ah * s.acell);
  drawRays(s, ctx, t, cfg);
  ctx.globalCompositeOperation = "source-over";
  if (cfg.meteor) {
    const P = 31;
    const n = Math.floor(t / P);
    const p = (t - n * P) / 1.1;
    if (p < 1) {
      const rr = (j) => {
        const v = Math.sin(n * 57.3 + s.meteor + j * 9.1) * 43758.5453;
        return v - Math.floor(v);
      };
      const sd = rr(3) < 0.5 ? -1 : 1;
      const hx = w * (0.15 + rr(1) * 0.7) + sd * p * 260 * k;
      const hy = h * (0.04 + rr(2) * 0.16) + p * 110 * k;
      s.meteorIds.forEach((id, i) => put(ctx, s, id, hx - sd * i * cell * 0.9, hy - i * cell * 0.38, Math.sin(Math.PI * p) * (1 - i / 5)));
    }
  }
  ctx.globalAlpha = 1;
  if (s.front) ctx.drawImage(s.front, 0, 0, w, h);
}

function scene(cfg) {
  return {
    fps: cfg.fps || 12,
    wallpaper: true,
    scheme: "dark",
    previewK: 0.38,
    setup(s) {
      s.seed = newSeed();
    },
    resize(s) {
      build(s, cfg);
    },
    draw(s, t) {
      draw(s, t, cfg);
    },
  };
}

// Classic curtains: three layers folding over a dense starfield.
export const auroraveil = scene({
  mode: "line",
  sky: [[0, "#010309"], [0.4, "#030c1e"], [0.8, "#06192a"], [1, "#0a242e"]],
  stars: 1.6,
  glyphStars: 70,
  horizon: "rgba(30,150,110,0.16)",
  wash: [[0.5, 0.62, 0.7, 0.32, "rgba(40,200,140,0.12)"], [0.25, 0.3, 0.35, 0.2, "rgba(60,110,200,0.06)"]],
  meteor: true,
  curtains: [
    { y: 0.4, amp: 0.06, hr: 0.32, gain: 0.7, x0: -0.25, x1: 1.25, drift: 3, lut: GREEN, rays: 0.06 },
    { y: 0.58, amp: 0.08, hr: 0.46, gain: 0.95, x0: -0.3, x1: 0.95, drift: 6, lut: GREEN },
    { y: 0.8, amp: 0.08, hr: 0.62, gain: 1.25, x0: -0.2, x1: 1.3, drift: 9, lut: GREEN, rays: 0.04 },
  ],
});

// Overhead: the curtains ring the zenith and their rays converge on it.
export const auroracorona = scene({
  mode: "corona",
  sky: [[0, "#03040e"], [0.5, "#050a1c"], [1, "#040d18"]],
  stars: 1.3,
  glyphStars: 55,
  zenith: true,
  wash: [[0.5, 0.4, 0.55, 0.6, "rgba(60,190,150,0.1)"], [0.5, 0.38, 0.22, 0.32, "rgba(140,100,230,0.1)"]],
  breathe: true,
  grid: "#6a6aa8",
  glow: 1.5,
  curtains: [
    { y: 0.9, amp: 0.14, hr: 0.85, gain: 1.35, x0: -1, x1: 2, drift: 0, lut: CORONA, rays: 0.028, fold: 0.003, patch: 0.35, soft: 0.2 },
    { y: 0.62, amp: 0.12, hr: 0.6, gain: 1.05, x0: -1, x1: 2, drift: 0, lut: CORONA, rays: 0.036, fold: 0.004, patch: 0.25, soft: 0.2 },
    { y: 0.36, amp: 0.1, hr: 0.34, gain: 0.7, x0: -1, x1: 2, drift: 0, lut: VIOLET, rays: 0.05, fold: 0.005, patch: 0.2, soft: 0.25, fringe: 0.5 },
  ],
});

// A rare red storm: tall crimson and magenta curtains, a faint green base,
// surges that flare and pass along them.
export const auroracrimson = scene({
  mode: "line",
  sky: [[0, "#060208"], [0.45, "#100516"], [0.85, "#140818"], [1, "#0c0e14"]],
  stars: 1.1,
  glyphStars: 45,
  horizon: "rgba(160,30,70,0.14)",
  wash: [[0.5, 0.55, 0.75, 0.4, "rgba(200,30,80,0.12)"], [0.7, 0.2, 0.4, 0.25, "rgba(120,50,200,0.07)"]],
  grid: "#a06080",
  storm: true,
  glow: 1.25,
  curtains: [
    { y: 0.5, amp: 0.06, hr: 0.48, gain: 0.8, x0: -0.2, x1: 1.3, drift: 4, lut: CRIMSON, rays: 0.05, base: 0.14 },
    { y: 0.8, amp: 0.07, hr: 0.75, gain: 1.05, x0: -0.3, x1: 1.0, drift: 7, lut: CRIMSON, rays: 0.035, base: 0.14 },
    { y: 0.93, amp: 0.05, hr: 0.66, gain: 0.85, x0: 0.2, x1: 1.3, drift: 10, lut: CRIMSON, rays: 0.04, base: 0.14 },
  ],
});

// Ribbons: bands of teal and violet at different heights, each drifting
// at its own speed, the Milky Way behind them.
export const aurorabands = scene({
  mode: "line",
  sky: [[0, "#02030a"], [0.5, "#050a1a"], [1, "#071420"]],
  stars: 1.2,
  glyphStars: 50,
  milky: { x: 0.5, y: 0.45, angle: -0.42, width: 0.26, alpha: 0.09 },
  wash: [[0.5, 0.5, 0.75, 0.45, "rgba(70,120,210,0.08)"]],
  grid: "#6080b0",
  rayThr: 0.16,
  rayAlpha: 0.9,
  glow: 1.5,
  curtains: [
    { y: 0.22, amp: 0.04, hr: 0.16, gain: 0.8, x0: -0.3, x1: 1.3, drift: 2, lut: VIOLET, rays: 0.06, f: 0.006, soft: 0.3, fringe: 0.6, body: 0.35 },
    { y: 0.38, amp: 0.05, hr: 0.18, gain: 0.95, x0: -0.3, x1: 1.3, drift: 5, lut: TEAL, rays: 0.05, f: 0.005, soft: 0.3, fringe: 0.6, body: 0.35 },
    { y: 0.54, amp: 0.05, hr: 0.2, gain: 1.0, x0: -0.3, x1: 1.3, drift: 9, lut: VIOLET, rays: 0.045, f: 0.0045, soft: 0.3, fringe: 0.6, body: 0.35 },
    { y: 0.7, amp: 0.055, hr: 0.22, gain: 1.1, x0: -0.3, x1: 1.3, drift: 14, lut: TEAL, rays: 0.04, f: 0.004, soft: 0.3, fringe: 0.6, body: 0.35 },
    { y: 0.87, amp: 0.05, hr: 0.24, gain: 1.0, x0: -0.3, x1: 1.3, drift: 20, lut: GREEN, rays: 0.035, f: 0.0035, soft: 0.25, fringe: 0.5, body: 0.35 },
  ],
});

// From orbit: the aurora oval along the limb of the night side, rays
// standing out from the planet into space.
export const auroraorbit = scene({
  mode: "orbit",
  sky: [[0, "#010206"], [0.6, "#020410"], [1, "#040a18"]],
  stars: 1.5,
  glyphStars: 45,
  starFloor: 1,
  wash: [[0.5, 0.72, 0.75, 0.3, "rgba(40,210,150,0.12)"]],
  grid: "#5a6aa0",
  glow: 1.7,
  curtains: [
    { y: 1.004, amp: 0.006, hr: 0.22, gain: 1.5, base: 0.25, x0: -0.3, x1: 1.3, drift: 5, lut: GREEN, rays: 0.05, fold: 0.003, soft: 0.06, body: 0.35 },
    { y: 1.0, amp: 0.004, hr: 0.09, gain: 1.0, x0: -0.3, x1: 1.3, drift: 8, lut: TEAL, rays: 0.06, fold: 0.004, soft: 0.2, fringe: 0.3 },
    { y: 1.07, amp: 0.008, hr: 0.18, gain: 0.5, x0: 0.1, x1: 1.3, drift: 3, lut: VIOLET, rays: 0.04, fold: 0.003, soft: 0.3, fringe: 0.2 },
  ],
});

// A wide aurora sky over a thin dark ridge at the very bottom.
export const auroraridge = scene({
  mode: "line",
  sky: [[0, "#010308"], [0.45, "#03101e"], [0.85, "#072030"], [1, "#0a2a34"]],
  stars: 1.4,
  glyphStars: 60,
  horizon: "rgba(40,190,130,0.2)",
  wash: [[0.5, 0.7, 0.8, 0.35, "rgba(40,200,140,0.12)"]],
  rayFloor: 0.95,
  ridge: true,
  meteor: true,
  curtains: [
    { y: 0.42, amp: 0.05, hr: 0.34, gain: 0.7, x0: -0.3, x1: 1.0, drift: 3, lut: TEAL, rays: 0.06 },
    { y: 0.62, amp: 0.07, hr: 0.5, gain: 0.95, x0: 0.0, x1: 1.3, drift: 6, lut: GREEN },
    { y: 0.85, amp: 0.06, hr: 0.7, gain: 1.25, x0: -0.3, x1: 1.3, drift: 9, lut: GREEN, rays: 0.035 },
  ],
});

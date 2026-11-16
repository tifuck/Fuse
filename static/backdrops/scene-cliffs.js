// Scene: Coastal Cliffs. Sea cliffs at dusk, seen along the coast: headland
// behind headland fades into the rose haze toward a setting sun, a striped
// lighthouse stands on the nearest cliff and sweeps its beam across the
// evening, swells roll in and burst into foam against the rocks and through
// a sea arch, gulls hang on the updraft, and a small sail crosses the sun's
// path now and then.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const HAZE = "#b08494";
const ROCK = { dark: "#1e1622", shadow: "#33242f", mid: "#5a3e4a", lit: "#e8906c", wet: "#1c2230" };
const GRASS = { dark: "#34402a", lit: "#a8984c" };
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const q = (v, n) => Math.round(clamp01(v) * n) / n;

// A rock mass from a cell mask, lit from the right (the sunset side): a dark
// painted silhouette under the tiles, a warm rim on the sea faces, bands of
// strata inside, wet rock at the waterline, shadow deepening inland.
const BANDS = ["#5a3e4a", "#704a52", "#4a3442", "#7c5658", "#62424c"];
function rockMass(dst, s, R, m, cfg) {
  const { cell } = s;
  const c0 = Math.floor(m.c0);
  const c1 = Math.ceil(m.c1);
  const r0 = Math.floor(m.r0);
  const r1 = Math.ceil(m.r1);
  const W = c1 - c0 + 1;
  const occ = new Uint8Array(W * (r1 - r0 + 2));
  const at = (c, r) => c >= c0 && c <= c1 && r >= r0 && r <= r1 && occ[(r - r0) * W + (c - c0)] === 1;
  for (let r = r0; r <= r1; r += 1) for (let c = c0; c <= c1; c += 1) if (m.inside(c, r)) occ[(r - r0) * W + (c - c0)] = 1;
  const [cv, g] = layer(s);
  const hz = cfg.haze;
  const H = (col) => mixc(col, HAZE, hz);
  g.fillStyle = H(ROCK.dark);
  for (let r = r0; r <= r1; r += 1) for (let c = c0; c <= c1; c += 1) if (at(c, r)) g.fillRect(c * cell, r * cell, cell, cell);
  const faceR = (r) => {
    for (let c = c1; c >= c0; c -= 1) if (at(c, r)) return c;
    return c0;
  };
  const bandOf = (c, r) => Math.floor((r + c * cfg.tilt + 1000) / cfg.bh);
  const tall = r1 - r0 > 6;
  const tops = [];
  const faces = [];
  let maxX = 0;
  for (let r = r0; r <= r1; r += 1) {
    for (let c = Math.max(c0, -1); c <= c1; c += 1) {
      if (!at(c, r)) continue;
      maxX = Math.max(maxX, c);
      let dR = 0;
      while (dR < 9 && at(c + dR + 1, r)) dR += 1;
      let dL = 0;
      while (dL < 9 && at(c - dL - 1, r)) dL += 1;
      let depth = 0;
      while (depth < 9 && at(c, r - depth - 1)) depth += 1;
      const wet = tall && cfg.wet ? r1 - r : 9;
      const rim = Math.exp(-(cfg.left ? dL : dR) / cfg.rim) * cfg.lit;
      const n = noise(c * 0.31 + cfg.off, r * 0.27);
      const band = bandOf(c, r);
      const seam = band !== bandOf(c, r - 1);
      let glyph;
      let fill;
      let ink;
      if (depth === 0) tops.push({ c, r, dR });
      if (cfg.grass && depth <= 1 && dR > 0 && wet > 3) {
        fill = H(mixc(GRASS.dark, GRASS.lit, q(rim * 0.9 + 0.15 + (depth === 0 ? 0.15 : 0), 5)));
        glyph = depth === 0 ? R.pick(["^", "^", "'", '"', "v"]) : R.pick(["'", ".", ",", ";", ":"]);
        ink = shade(fill, 1.45);
      } else {
        const lift = q((depth === 0 ? 0.6 : depth === 1 ? 0.3 : 0) + 0.3 * n, 3);
        fill = mixc(mixc(ROCK.shadow, BANDS[band % BANDS.length], lift), ROCK.lit, q(rim * 0.92, 6));
        if (dL === 0 && dR > 0) fill = shade(fill, 0.8);
        if (dR === 0) {
          const below = faceR(r + 1);
          glyph = r === r1 ? "%" : below > c ? "\\" : below < c ? "/" : "|";
          if (depth === 0) glyph = below > c ? "\\" : "^";
          faces.push({ c, r });
        } else if (dL === 0) {
          glyph = depth === 0 ? "^" : "|";
        } else if (depth === 0) {
          glyph = cfg.grass ? "-" : R.pick(["^", "-", "_"]);
        } else if (seam && dR < cfg.rim * 4) {
          glyph = n > 0.5 ? "=" : "_";
          fill = shade(fill, 1.08);
        } else if (noise(c * 0.9 + cfg.off, r * 0.14) > 0.72) {
          glyph = "|";
        } else {
          glyph = R.pick([".", ":", "%", "'", ".", ";", " "]);
        }
        if (wet < 2) {
          fill = mixc(fill, ROCK.wet, 0.5);
          if (dR > 0) glyph = R.pick(["%", "o", "%", "#"]);
        }
        fill = H(fill);
        ink = rim > 0.35 ? shade(fill, 0.68) : shade(fill, 1.6);
        // Deep inside, the rock is only painted dark with a faint glyph on it.
        if (dR > cfg.rim * 2 && depth > 2 && dL > 0 && wet > 1 && hash(c, r, cfg.off | 0) < (seam ? 0.85 : cfg.sparse)) {
          if (seam) glyph = "-";
          if (glyph !== " ") put(g, s, tile(s, glyph, null, H(shade(BANDS[band % BANDS.length], 0.8))), c * cell, r * cell);
          continue;
        }
      }
      put(g, s, tile(s, glyph, fill, ink), c * cell, r * cell);
    }
  }
  g.globalAlpha = 1;
  // Inland the rock falls into shadow; low down it sinks into the sea haze.
  g.globalCompositeOperation = "source-atop";
  const x1 = (maxX + 1) * cell;
  const span = Math.min(x1 - c0 * cell, cell * 30);
  const sh = g.createLinearGradient(x1 - span, 0, x1 - cell * 4, 0);
  sh.addColorStop(0, `rgba(18,10,24,${cfg.shadow})`);
  sh.addColorStop(1, "rgba(18,10,24,0)");
  g.fillStyle = sh;
  g.fillRect(0, r0 * cell, x1, (r1 - r0 + 1) * cell);
  if (cfg.mist) {
    const my = (r1 + 1) * cell;
    const mh = Math.min((r1 - r0) * cell, cell * cfg.mistRows);
    const mg = g.createLinearGradient(0, my - mh, 0, my);
    mg.addColorStop(0, "rgba(200,150,160,0)");
    mg.addColorStop(1, `rgba(200,150,160,${cfg.mist})`);
    g.fillStyle = mg;
    g.fillRect(0, my - mh, x1, mh);
  }
  dst.globalAlpha = 1;
  dst.drawImage(cv, 0, 0, s.w, s.h);
  return { tops, faces, at, r1 };
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const rows = Math.ceil(h / cell) + 1;
  const port = (s.port = h > w * 1.1);
  const C = (u) => Math.round((w * u) / cell);
  const Rw = (v) => Math.round((h * v) / cell);
  const hzR = (s.hzR = Rw(port ? 0.5 : 0.54));
  const hy = (s.hy = hzR * cell);
  s.sunX = Math.round(w * (port ? 0.66 : 0.6));
  // Sky, sun and sea: one still image.
  const [back, g] = layer(s);
  const sky = g.createLinearGradient(0, 0, 0, hy);
  sky.addColorStop(0, "#15142e");
  sky.addColorStop(0.3, "#3a2852");
  sky.addColorStop(0.58, "#8c4466");
  sky.addColorStop(0.82, "#e07a62");
  sky.addColorStop(1, "#ffbe7c");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, hy);
  const sr = Math.min(w, h) * 0.034;
  const glow = g.createRadialGradient(s.sunX, hy, 0, s.sunX, hy, Math.max(w, h) * 0.55);
  glow.addColorStop(0, "rgba(255,214,150,0.55)");
  glow.addColorStop(0.2, "rgba(255,170,110,0.22)");
  glow.addColorStop(1, "rgba(255,140,110,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, w, hy);
  g.save();
  g.beginPath();
  g.rect(0, 0, w, hy);
  g.clip();
  const disc = g.createRadialGradient(s.sunX, hy, 0, s.sunX, hy, sr * 1.6);
  disc.addColorStop(0, "rgba(255,248,224,1)");
  disc.addColorStop(0.6, "rgba(255,226,170,1)");
  disc.addColorStop(1, "rgba(255,190,120,0)");
  g.fillStyle = disc;
  g.beginPath();
  g.arc(s.sunX, hy + sr * 0.35, sr * 1.6, 0, TAU);
  g.fill();
  g.restore();
  for (let i = 0; i < 90 * Math.min(1.2, (w * h) / 1.3e6); i += 1) {
    g.fillStyle = `rgba(255,236,240,${R.range(0.1, 0.45)})`;
    const r = R.range(0.6, 1.3) * k;
    g.fillRect(R() * w, Math.pow(R(), 2.2) * hy * 0.45, r, r);
  }
  // The sea: sky-coloured at the horizon, slate below.
  const sea = g.createLinearGradient(0, hy, 0, h);
  sea.addColorStop(0, "#c08486");
  sea.addColorStop(0.08, "#7a6478");
  sea.addColorStop(0.35, "#3e4560");
  sea.addColorStop(1, "#141826");
  g.fillStyle = sea;
  g.fillRect(0, hy, w, h - hy);
  const path = g.createRadialGradient(s.sunX, hy, 0, s.sunX, hy, (h - hy) * 1.1);
  path.addColorStop(0, "rgba(255,190,130,0.45)");
  path.addColorStop(1, "rgba(255,160,120,0)");
  g.save();
  g.translate(s.sunX, hy);
  g.scale(0.32, 1);
  g.translate(-s.sunX, -hy);
  g.fillStyle = path;
  g.fillRect(0, hy, w * 4, h - hy);
  g.restore();
  // A young crescent moon above the afterglow.
  const mx = w * (port ? 0.8 : 0.87);
  const my = h * (port ? 0.12 : 0.15);
  const mr = Math.max(3, 13 * k);
  const mg = g.createRadialGradient(mx, my, 0, mx, my, mr * 5);
  mg.addColorStop(0, "rgba(255,230,220,0.16)");
  mg.addColorStop(1, "rgba(255,230,220,0)");
  g.fillStyle = mg;
  g.fillRect(mx - mr * 5, my - mr * 5, mr * 10, mr * 10);
  const [mc, mcg] = layer(s);
  mcg.fillStyle = "#fff0e0";
  mcg.beginPath();
  mcg.arc(mx, my, mr, 0, TAU);
  mcg.fill();
  mcg.globalCompositeOperation = "destination-out";
  mcg.beginPath();
  mcg.arc(mx + mr * 0.42, my - mr * 0.3, mr * 0.95, 0, TAU);
  mcg.fill();
  g.drawImage(mc, 0, 0, w, h);
  dotGrid(g, s, "#e8b0b8", 0.16);
  s.back = back;
  // Twinkling glyph stars high up.
  s.stars = Array.from({ length: Math.round(18 * Math.min(1.2, (w * h) / 1.3e6)) + 4 }, () => ({
    x: Math.floor((R() * w) / cell) * cell,
    y: Math.floor((Math.pow(R(), 1.6) * hy * 0.3) / cell) * cell,
    id: tile(s, R.pick(["+", "*", ".", "'"]), null, "#ffe6ee"),
    p: R.range(2, 6),
    ph: R() * TAU,
  }));
  // Clouds: soft bands, plum on top and lit from below.
  s.clouds = Array.from({ length: port ? 6 : 9 }, () => {
    const cy = h * R.range(0.06, 0.44);
    const v = cy / hy;
    const fw = R.range(260, 620) * k;
    const fh = R.range(14, 34) * k;
    const blur = Math.max(2, 10 * k);
    const pad = blur * 3;
    const c = document.createElement("canvas");
    c.width = Math.round((fw + pad * 2) * s.dpr);
    c.height = Math.round((fh + pad * 2) * s.dpr);
    const cg = c.getContext("2d");
    cg.scale(s.dpr, s.dpr);
    cg.filter = `blur(${blur}px)`;
    for (let j = 0; j < 5; j += 1) {
      const ex = pad + fw * (0.2 + 0.6 * R());
      const ey = pad + fh * 0.5;
      const rx = fw * R.range(0.16, 0.3);
      const ry = fh * R.range(0.25, 0.45);
      cg.fillStyle = `rgba(${Math.round(70 + 90 * v)},${Math.round(40 + 30 * v)},${Math.round(80 + 10 * v)},${0.5})`;
      cg.beginPath();
      cg.ellipse(ex, ey, rx, ry, 0, 0, TAU);
      cg.fill();
      cg.fillStyle = `rgba(255,${Math.round(130 + 50 * v)},${Math.round(110 + 10 * v)},${0.25 + 0.3 * v})`;
      cg.beginPath();
      cg.ellipse(ex, ey + ry * 0.55, rx * 0.85, ry * 0.45, 0, 0, TAU);
      cg.fill();
    }
    return { c, w: fw + pad * 2, h: fh + pad * 2, x: R() * w, y: cy - (fh + pad * 2) / 2, v: -R.range(2, 5) * k, lit: v };
  });
  s.streakIds = ["-", "~", "-", "=", "-"].map((gl, i) => tile(s, gl, null, ["#f4b0a0", "#e89aa8", "#ffc8a0", "#d890a8", "#f0a898"][i]));
  s.streaks = Array.from({ length: port ? 5 : 8 }, () => ({ y: Math.floor((h * R.range(0.1, 0.46)) / cell) * cell, len: Math.round(R.range(5, 14)), x: R() * w, v: -R.range(3, 7) * k, seed: Math.floor(R() * 100) }));
  // The land, far to near.
  const [land, lg] = layer(s);
  const off = R() * 100;
  // A headland juts out to the right; its sea face steps in and out with
  // the strata (ledges), with a sea cave low down and talus at the foot.
  const headland = (tip, topR, baseR, rise, o, flatTo, bh, cave, hill = 0) => {
    const steps = Array.from({ length: 40 }, () => Math.round(R.range(-1.6, 1.6)));
    const top = (c) => {
      const inland = Math.max(0, tip - flatTo - c);
      return topR - Math.round(rise * (1 - Math.exp(-inland / 12)) + hill * Math.exp(-(((c - tip + 11) / 7) ** 2)) + (c < tip - flatTo ? 1.8 * (noise(c * 0.16 + o, 1) - 0.5) : 0));
    };
    const edge = (r) => {
      const u = (r - topR) / Math.max(1, baseR - topR);
      const d = r - topR;
      let e = tip + steps[Math.floor(d / bh) % 40] + Math.round(1.2 * (noise(r * 0.35 + o, 5) - 0.5) + 2.5 * u);
      if (d === 0) e = tip - 3;
      else if (d === 1) e = tip - 1;
      if (u > 0.88) e += Math.round(((u - 0.88) / 0.12) * 3);
      if (cave && u > 0.68 && u < 0.82) e -= 2 + (u > 0.72 && u < 0.78 ? 1 : 0);
      return e;
    };
    return { c0: -2, c1: tip + 10, r0: topR - rise - 3, r1: baseR, inside: (c, r) => r >= top(c) && c <= edge(r), top };
  };
  const tips = port ? [0.42, 0.62, 0.76, 0.86] : [0.23, 0.35, 0.45, 0.53];
  const L = [
    { m: headland(C(tips[3]), hzR - Math.max(2, Rw(0.03)), hzR, 2, off + 40, 0, 2, false), haze: 0.86, near: 0.1, mist: 0.5, sparse: 1 },
    { m: headland(C(tips[2]), hzR - Rw(0.06), hzR + 1, 3, off + 30, 0, 2, false, 2), haze: 0.7, near: 0.2, mist: 0.45, sparse: 0.95 },
    { m: headland(C(tips[1]), hzR - Rw(0.12), hzR + Rw(0.035), 4, off + 20, 2, 3, true, 4), haze: 0.46, near: 0.4, mist: 0.35, sparse: 0.85 },
  ];
  const t0 = C(tips[0]);
  const top0 = Math.max(Rw(port ? 0.32 : 0.3), 17);
  const near = headland(t0, top0, Rw(0.87), 6, off + 10, 14, 4, true);
  s.contacts = [];
  for (const { m, haze, near: nf, mist, sparse } of L) {
    const res = rockMass(lg, s, R, m, { haze, grass: true, rim: 2, lit: 0.8 - haze * 0.3, tilt: 0.18, bh: 3, sparse, shadow: 0.3, mist, mistRows: 5, off: m.r1 + off });
    const base = res.faces.filter((f) => f.r === m.r1)[0];
    if (base) s.contacts.push({ x: (base.c + 1) * cell, y: m.r1 * cell, near: nf, P: R.range(7, 11), ph: R() * 20 });
  }
  // Sea mist lying along the horizon in front of the far headlands.
  lg.save();
  lg.filter = `blur(${Math.max(2, 9 * k)}px)`;
  for (let i = 0; i < 10; i += 1) {
    lg.fillStyle = `rgba(226,168,166,${R.range(0.16, 0.3)})`;
    lg.beginPath();
    lg.ellipse(w * R.range(0.15, 0.6), hy - cell * R.range(0, 1.5), R.range(120, 300) * k, R.range(5, 12) * k, 0, 0, TAU);
    lg.fill();
  }
  lg.restore();
  // Offshore: a needle stack and a sea arch toward the right edge.
  const stack = (cx, wd, topR, baseR, o) => ({
    c0: cx - wd - 3, c1: cx + wd + 3, r0: topR - 1, r1: baseR,
    inside: (c, r) => {
      const u = (r - topR) / Math.max(1, baseR - topR);
      const half = wd * (0.55 + 0.45 * u) + 1.2 * (noise(r * 0.4 + o, 2) - 0.5);
      const tp = topR + Math.round(1.5 * noise(c * 0.6 + o, 3) + Math.abs(c - cx) * 0.6);
      return r >= tp && Math.abs(c - cx + 0.5 * u) <= half;
    },
  });
  const arch = (a0, a1, topR, baseR, o) => {
    const mid = (a0 + a1) / 2;
    const rx = (a1 - a0) * 0.27;
    const ry = (baseR - topR) * 0.62;
    return {
      c0: a0 - 4, c1: a1 + 4, r0: topR - 3, r1: baseR,
      inside: (c, r) => {
        const u = (r - topR) / Math.max(1, baseR - topR);
        const e0 = a0 + Math.round(1.6 * (noise(r * 0.35 + o, 4) - 0.5) - u * 1.5);
        const e1 = a1 + Math.round(1.6 * (noise(r * 0.35 + o, 6) - 0.5) + u * 1.5);
        const tp = topR + Math.round(2 * noise(c * 0.25 + o, 7) + Math.max(0, (c - (a1 - 3)) * 1.2) + Math.max(0, (a0 + 2 - c) * 1.4));
        const hole = ((c - mid - 1) / rx) ** 2 + ((r - baseR - 0.5) / ry) ** 2 < 1;
        return r >= tp && c >= e0 && c <= e1 && !hole;
      },
    };
  };
  if (!port) rockMass(lg, s, R, { c0: C(0.93), c1: cols + 2, r0: hzR - 4, r1: hzR, inside: (c, r) => r >= hzR - Math.round(Math.min(3, (c - C(0.93)) * 0.5) + noise(c * 0.3, 8)) }, { haze: 0.78, grass: true, rim: 2, lit: 0.6, tilt: 0.2, bh: 2, sparse: 0.6, shadow: 0, mist: 0.4, mistRows: 3, off: off + 70 });
  const offshore = port
    ? [{ m: stack(C(0.6), 1.6, Rw(0.57), Rw(0.66), off + 50), haze: 0.3, near: 0.45 }, { m: arch(C(0.72), C(0.72) + Math.max(10, C(0.3)), Rw(0.63), Rw(0.76), off + 60), haze: 0.08, near: 0.7 }]
    : [{ m: stack(C(0.765), 1.8, Rw(0.57), Rw(0.68), off + 50), haze: 0.3, near: 0.45 }, { m: arch(C(0.84), C(0.84) + Math.max(12, C(0.13)), Rw(0.55), Rw(0.76), off + 60), haze: 0.06, near: 0.75 }];
  for (const { m, haze, near: nf } of offshore) {
    const res = rockMass(lg, s, R, m, { haze, grass: false, rim: 1.6, lit: 0.9, tilt: -0.25, bh: 3, sparse: 0.4, shadow: 0.2, mist: 0.25, mistRows: 3, off: m.r1 + off });
    const bases = res.faces.filter((f) => f.r === Math.ceil(m.r1));
    for (const b of bases.slice(0, 2)) s.contacts.push({ x: (b.c + 1) * cell, y: Math.ceil(m.r1) * cell, near: nf, P: R.range(6, 10), ph: R() * 20 });
  }
  // The near cliff, with the lighthouse, and boulders at its foot.
  const res0 = rockMass(lg, s, R, near, { haze: 0, grass: true, wet: true, rim: 2.6, lit: 1, tilt: 0.12, bh: 4, sparse: 0.75, shadow: 0.55, mist: 0.18, mistRows: 8, off: off + 10 });
  const b0 = res0.faces.filter((f) => f.r === near.r1)[0];
  const foot = (b0 ? b0.c : t0) + 1;
  s.contacts.push({ x: foot * cell, y: near.r1 * cell, near: 1, P: 8.5, ph: R() * 20 });
  s.surf = { c1: foot + 2, y: (near.r1 + 1) * cell };
  // A low shelf of rock in the near right corner, waves washing over it.
  const sc0 = C(port ? 0.7 : 0.8);
  const shelf = { c0: sc0, c1: cols + 1, r0: rows - 10, r1: rows, inside: (c, r) => r >= rows - Math.round(2 + 4 * noise(c * 0.22 + off, 9) + Math.min(5, (c - sc0) * 0.4)) };
  const resS = rockMass(lg, s, R, shelf, { haze: 0, grass: false, rim: 1.2, lit: 0.55, left: true, tilt: 0.3, bh: 3, sparse: 0.8, shadow: 0, mist: 0, off: off + 90 });
  s.contacts.push({ x: (sc0 + 1) * cell, y: (rows - 3) * cell, near: 0.9, P: 9.5, ph: R() * 20, dir: -1 });
  s.shelf = resS.tops.filter((tp) => hash(tp.c, tp.r, 4) < 0.5).map((tp) => ({ x: tp.c * cell, y: (tp.r - 1) * cell, ph: R() * TAU }));
  s.boulders = [];
  for (let i = 0; i < 4; i += 1) {
    const c = foot + Math.round(R.range(0, 5));
    const r = near.r1 - Math.round(R.range(-1, 1.5));
    const lit = R.range(0.3, 0.9);
    const fill = mixc(mixc(ROCK.wet, ROCK.mid, 0.4), ROCK.lit, q(lit * 0.6, 4));
    put(lg, s, tile(s, R.pick(["o", "O", "@", "%"]), fill, shade(fill, 0.6)), c * cell, r * cell);
    if (R() < 0.5) put(lg, s, tile(s, R.pick(["o", "%"]), shade(fill, 0.8), shade(fill, 0.55)), (c + 1) * cell, r * cell);
    s.boulders.push({ x: c * cell, y: r * cell, ph: R() * TAU });
  }
  lg.globalAlpha = 1;
  s.tufts = [];
  const tuftInk = ["#b4a058", "#8e8a46", "#c8b064", "#76763c"];
  for (const tp of res0.tops) {
    if (tp.dR === 0 || R() > 0.55) continue;
    s.tufts.push({ x: tp.c * cell, y: (tp.r - 1) * cell, id: tile(s, R.pick(["\\", "|", "/", "'", ",", "v"]), null, R.pick(tuftInk)), ph: R() * TAU, lift: R.range(0.6, 1.2) });
  }
  const lhC = t0 - 6;
  const baseR0 = near.top(lhC);
  const WHITE = ["#8a7884", "#eadcd2", "#ffd6b4"];
  const RED = ["#5e2430", "#b8443c", "#f07850"];
  const LH = [
    ["  |  ", "D"],
    [" /^\\ ", "R"],
    [" [#] ", "L"],
    ["+-=-+", "G"],
    [" |o| ", "W"],
    [" |:| ", "W"],
    [" |:| ", "X"],
    [" |o| ", "X"],
    [" |:| ", "W"],
    [" |:| ", "W"],
    ["/|:|\\", "X"],
    ["/:::\\", "X"],
    ["[=n=]", "B"],
  ];
  LH.forEach(([art, band], i) => {
    const r = baseR0 - LH.length + i;
    Array.from(art).forEach((ch, j) => {
      if (ch === " ") return;
      const side = j <= 1 ? 0 : j >= 3 ? 2 : 1;
      let fill;
      let ink;
      if (band === "W" || band === "X") {
        const p = band === "W" ? WHITE : RED;
        fill = p[side];
        ink = shade(fill, side === 0 ? 1.5 : 0.62);
        if (ch === "o") fill = "#ffcf7a";
      } else if (band === "R") {
        fill = ["#3a1c26", "#7a2c30", "#a8463a"][side];
        ink = shade(fill, 1.7);
      } else if (band === "L") {
        fill = ch === "#" ? "#fff2c4" : "#2a2230";
        ink = ch === "#" ? "#c08a2a" : "#ffe2a0";
      } else if (band === "G") {
        fill = "#2e2230";
        ink = "#d8b0a0";
      } else if (band === "B") {
        fill = ["#4a3c44", "#8a7068", "#c08a70"][side];
        ink = shade(fill, 0.6);
      } else {
        fill = null;
        ink = "#e0b0a0";
      }
      put(lg, s, tile(s, ch, fill, ink), (lhC - 2 + j) * cell, r * cell);
    });
  });
  s.lantern = { x: lhC * cell + cell / 2, y: (baseR0 - LH.length + 2) * cell + cell / 2, id: tile(s, "#", "#fff2c4", "#c08a2a") };
  // The keeper's cottage and a fence along the path.
  const kc = lhC - 9;
  const kr = near.top(kc);
  const COT = [
    ["  |    ", "c"],
    [" /===\\ ", "r"],
    ["/=====\\", "r"],
    ["|#:|:#|", "w"],
  ];
  s.windows = [];
  COT.forEach(([art, part], i) => {
    const r = kr - COT.length + i;
    Array.from(art).forEach((ch, j) => {
      if (ch === " ") return;
      const lit = j >= 4;
      let fill = part === "r" ? (lit ? "#5a4458" : "#3a2c3c") : part === "c" ? "#4a3a40" : lit ? "#e0b49c" : "#9a8088";
      let ink = part === "r" ? "#8a7090" : shade(fill, 0.62);
      if (ch === "#") {
        fill = "#ffc070";
        ink = "#7a4a10";
        s.windows.push({ x: (kc + j - 3) * cell, y: r * cell, id: tile(s, "#", fill, ink), dim: tile(s, "#", "#e09850", "#6a3a10"), ph: R() * 100 });
      }
      put(lg, s, tile(s, ch, fill, ink), (kc + j - 3) * cell, r * cell);
    });
  });
  for (let c = kc - 12; c < kc - 4; c += 1) if (c > 0) put(lg, s, tile(s, c % 2 ? "+" : "-", null, "#2e2430"), c * cell, (near.top(c) - 1) * cell);
  for (let c = kc + 4; c < lhC - 2; c += 1) put(lg, s, tile(s, ".", "#7a6a48", "#c8b078"), c * cell, near.top(c) * cell);
  lg.globalAlpha = 1;
  s.land = land;
  s.chimney = { x: (kc - 1) * cell, y: (kr - COT.length - 1) * cell, ids: ["o", ".", "'", "~"].map((gl) => tile(s, gl, null, "#c8a8b4")) };
  // Beam glyphs.
  s.beamIds = ["-", "=", "-", "~"].map((gl) => tile(s, gl, null, "#fff0c0"));
  s.moteId = tile(s, ".", null, "#ffe8b0");
  s.flareId = tile(s, "*", null, "#ffffff");
  s.beamPh = R() * TAU;
  // The sea: glitter under the sun, swells rolling in.
  s.glintIds = ["-", "=", "~", "-"].map((gl, i) => tile(s, gl, null, ["#ffd8a0", "#ffc080", "#fff0d0", "#f8a878"][i]));
  s.swellIds = ["~", "-", "~", "="].map((gl, i) => tile(s, gl, null, ["#8a8cb0", "#6a7094", "#a4a0c4", "#7a7ca4"][i]));
  s.swells = Array.from({ length: Math.round(70 * Math.max(0.4, (w * h) / 1.3e6)) }, () => {
    const d = Math.pow(R(), 0.8);
    return { x: R() * w, y: Math.floor((hy + cell + d * (h - hy - cell)) / cell) * cell, len: Math.round(R.range(2, 4 + 5 * d)), v: -R.range(2, 5) * k * (0.4 + d), ph: R() * TAU, d };
  });
  s.rows = rows;
  // Foam and breaking waves.
  s.foamIds = ["*", "o", "O", "'", ".", ":"].map((gl) => tile(s, gl, null, "#fff4ee"));
  s.washIds = ["~", "=", "-", "~"].map((gl) => tile(s, gl, null, "#f4ece8"));
  s.crestIds = { cap: tile(s, "^", null, "#ffffff"), body: tile(s, "~", null, "#d8d4e8"), back: tile(s, "-", null, "#9a9cc0") };
  // Gulls: some riding the updraft by the cliffs, some passing.
  const gullAt = [
    [t0 + 4, top0 - 3], [t0 + 1, top0 + 6], [t0 + 9, top0 - 7], [C(port ? 0.85 : 0.88), Rw(0.4)], [C(port ? 0.7 : 0.8), Rw(0.3)], [C(port ? 0.2 : 0.12), Rw(0.16)],
  ];
  s.gulls = gullAt.map(([c, r], i) => ({ x: c * cell, y: r * cell, rx: R.range(30, 90) * k, ry: R.range(8, 22) * k, w: R.range(0.05, 0.12) * R.sign(), ph: R() * TAU, big: i < 3 ? 1 : 0.8 }));
  s.gullIds = {
    big: [["\\", "v", "/"], ["-", "v", "-"], ["/", "v", "\\"]].map((fr) => fr.map((gl) => tile(s, gl, null, "#f6e6e2"))),
    dark: [["\\", "v", "/"], ["-", "v", "-"], ["/", "v", "\\"]].map((fr) => fr.map((gl) => tile(s, gl, null, "#3a2838"))),
  };
  // A small sail on the horizon.
  s.boat = [
    [1, 0, tile(s, "|", null, "#3a2a36")], [2, 0, tile(s, "\\", null, "#3a2a36")],
    [0, 1, tile(s, "\\", null, "#2a2030")], [1, 1, tile(s, "_", null, "#2a2030")], [2, 1, tile(s, "/", null, "#2a2030")],
  ];
  s.seedT = R() * 1000;
}

function putS(ctx, s, id, x, y, a, sc) {
  if (a <= 0.01) return;
  const P = s.P;
  ctx.globalAlpha = a > 1 ? 1 : a;
  ctx.drawImage(s.atlas.c, (id % 64) * P, Math.floor(id / 64) * P, P, P, x, y, s.cell * sc, s.cell * sc);
}

export const cliffs = {
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
    const { ctx, w, h, cell, k, hy, dpr } = s;
    const snap = (v) => Math.round(v * dpr) / dpr;
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(s.back, 0, 0, w, h);
    for (const st of s.stars) put(ctx, s, st.id, st.x, st.y, 0.15 + 0.6 * (0.5 + 0.5 * Math.sin((TAU * t) / st.p + st.ph)));
    // Clouds drift inland on the sea wind.
    for (const cl of s.clouds) {
      const span = w + cl.w;
      const x = ((((cl.x + cl.v * t) % span) + span) % span) - cl.w;
      ctx.globalAlpha = 1;
      ctx.drawImage(cl.c, x, cl.y, cl.w, cl.h);
    }
    for (const sk of s.streaks) {
      const span = w + sk.len * cell;
      const x0 = ((((sk.x + sk.v * t) % span) + span) % span) - sk.len * cell;
      for (let i = 0; i < sk.len; i += 1) put(ctx, s, s.streakIds[(i + sk.seed) % 5], x0 + i * cell, sk.y, Math.sin((Math.PI * (i + 0.5)) / sk.len) * (0.28 + 0.14 * Math.sin(t * 0.6 + i + sk.seed)));
    }
    // Long swells: soft lit lines rolling in toward us, spreading apart.
    const sh = h - hy;
    for (let i = 0; i < 7; i += 1) {
      const p = ((t / 9 + i / 7) % 1);
      const y = hy + sh * p * p;
      const lw = Math.max(1, (1 + 3 * p) * k);
      const lgr = ctx.createLinearGradient(0, 0, w, 0);
      const a = 0.16 * Math.sin(Math.PI * p);
      lgr.addColorStop(0, "rgba(255,190,170,0)");
      lgr.addColorStop(s.sunX / w, `rgba(255,200,160,${a})`);
      lgr.addColorStop(1, `rgba(255,190,170,${a * 0.2})`);
      ctx.globalAlpha = 1;
      ctx.fillStyle = lgr;
      ctx.fillRect(0, y, w, lw);
    }
    // Glitter on the sun's path.
    const rows = Math.ceil((h - hy) / cell);
    for (let r = 1; r < rows; r += 1) {
      const d = r / rows;
      const half = 1 + r * 0.42;
      const y = hy + r * cell;
      const c0 = Math.floor(s.sunX / cell - half);
      for (let c = c0; c <= c0 + half * 2; c += 1) {
        const u = Math.abs(c + 0.5 - s.sunX / cell) / half;
        const n = noise(c * 0.8 + t * 0.9, r * 1.1 - t * 1.6);
        if (n < 0.42 + 0.28 * u) continue;
        put(ctx, s, s.glintIds[(c + r + Math.floor(t * 2)) & 3], c * cell, y, (1 - u * u) * (1 - d * 0.7) * (n - 0.3) * 2.6);
      }
    }
    for (const sw of s.swells) {
      const span = w + sw.len * cell;
      const x = ((((sw.x + sw.v * t) % span) + span) % span) - sw.len * cell;
      const a = (0.16 + 0.3 * sw.d) * (0.6 + 0.4 * Math.sin(t * 0.7 + sw.ph));
      for (let i = 0; i < sw.len; i += 1) put(ctx, s, s.swellIds[(i + Math.floor(sw.ph * 3)) & 3], x + i * cell, sw.y, a * Math.sin((Math.PI * (i + 0.5)) / sw.len));
    }
    // A sail crossing the sun's path, every few minutes.
    const BP = 150;
    const bn = Math.floor((t + s.seedT) / BP);
    const bp = ((t + s.seedT) - bn * BP) / 120;
    if (bp < 1) {
      const bx = snap(-4 * cell + bp * (w + 8 * cell));
      const by = hy - cell * 1.4 + Math.sin(t * 0.9) * 0.6 * k;
      for (const [dc, dr, id] of s.boat) putS(ctx, s, id, bx + dc * cell * 0.6, by + dr * cell * 0.6, 0.85, 0.6);
    }
    ctx.globalAlpha = 1;
    ctx.drawImage(s.land, 0, 0, w, h);
    // The lighthouse beam turns: long when it points across, a flash when
    // it faces us.
    const L = s.lantern;
    const ang = (TAU * t) / 12 + s.beamPh;
    const cx = Math.cos(ang);
    const toward = Math.max(0, Math.sin(ang));
    const dir = cx >= 0 ? 1 : -1;
    const len = Math.max(cell * 3, w * 0.95 * Math.abs(cx));
    const I = 0.14 + 0.3 * toward;
    const ex = L.x + dir * len;
    const ey = L.y + len * 0.05;
    const spread = len * (0.05 + 0.1 * (1 - Math.abs(cx))) + cell;
    ctx.globalCompositeOperation = "lighter";
    const bg = ctx.createLinearGradient(L.x, L.y, ex, ey);
    bg.addColorStop(0, `rgba(255,232,170,${I})`);
    bg.addColorStop(0.5, `rgba(255,214,150,${I * 0.35})`);
    bg.addColorStop(1, "rgba(255,200,140,0)");
    ctx.globalAlpha = 1;
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.moveTo(L.x, L.y - cell * 0.2);
    ctx.lineTo(ex, ey - spread);
    ctx.lineTo(ex, ey + spread);
    ctx.lineTo(L.x, L.y + cell * 0.2);
    ctx.closePath();
    ctx.fill();
    const n = Math.ceil(len / cell);
    for (let i = 2; i < n; i += 1) {
      const f = i / n;
      const bx = Math.round((L.x + dir * i * cell) / cell) * cell - cell / 2;
      const by = Math.round((L.y + i * cell * 0.05) / cell) * cell - cell / 2;
      const fl = noise(i * 0.5 - dir * t * 2, t * 0.7);
      put(ctx, s, s.beamIds[(i + Math.floor(t * 3)) & 3], bx, by, I * 2.2 * (1 - f) * (1 - f) * (0.4 + fl));
      if (hash(i, Math.floor(t * 2), 3) < 0.3) put(ctx, s, s.moteId, bx, by + (hash(i, 7, Math.floor(t * 2)) - 0.5) * spread * f * 1.6, I * 2 * (1 - f));
    }
    const flash = Math.pow(toward, 10);
    const gr = cell * (3 + 14 * flash);
    const lg = ctx.createRadialGradient(L.x, L.y, 0, L.x, L.y, gr);
    lg.addColorStop(0, `rgba(255,240,200,${0.5 + 0.5 * flash})`);
    lg.addColorStop(0.3, `rgba(255,214,150,${0.18 + 0.3 * flash})`);
    lg.addColorStop(1, "rgba(255,200,140,0)");
    ctx.globalAlpha = 1;
    ctx.fillStyle = lg;
    ctx.fillRect(L.x - gr, L.y - gr, gr * 2, gr * 2);
    ctx.globalCompositeOperation = "source-over";
    put(ctx, s, L.id, L.x - cell / 2, L.y - cell / 2);
    if (flash > 0.2) {
      for (let i = 1; i < 4; i += 1) {
        const a = flash * (1 - i / 4);
        put(ctx, s, s.beamIds[0], L.x - cell / 2 + i * cell, L.y - cell / 2, a);
        put(ctx, s, s.beamIds[0], L.x - cell / 2 - i * cell, L.y - cell / 2, a);
        put(ctx, s, s.flareId, L.x - cell / 2, L.y - cell / 2 - i * cell * 0.8, a * 0.6);
      }
    }
    for (const wn of s.windows) put(ctx, s, noise(t * 0.8, wn.ph) > 0.62 ? wn.dim : wn.id, wn.x, wn.y);
    for (let i = 0; i < 5; i += 1) {
      const life = (t * 0.18 + i / 5) % 1;
      put(ctx, s, s.chimney.ids[Math.min(3, Math.floor(life * 4))], s.chimney.x - life * cell * 3 + Math.sin(life * 6 + i) * cell * 0.4, s.chimney.y - life * cell * 3.5, 0.45 * (1 - life));
    }
    // Swells rise, run in and burst against the rocks.
    for (const [ci, ct] of s.contacts.entries()) {
      const nr = ct.near;
      const tau = (t + ct.ph) % ct.P;
      const sc = 0.45 + 0.55 * nr;
      const runIn = 3.2;
      const lead = ct.P - tau;
      if (lead < runIn) {
        const p = 1 - lead / runIn;
        const dir = ct.dir || 1;
        const cxp = ct.x + dir * (1 - p) * cell * (6 + 18 * nr);
        const crest = Math.round(3 + 9 * nr * Math.min(1, p * 1.5));
        for (let i = 0; i < crest; i += 1) {
          const x = snap(cxp + dir * i * cell * sc);
          const y = ct.y - cell * (0.2 + 0.9 * nr * p * Math.sin((Math.PI * (i + 0.5)) / crest));
          putS(ctx, s, i < 2 + nr * 2 ? s.crestIds.cap : s.crestIds.body, x, y, (0.4 + 0.6 * nr) * Math.min(1, p * 2), sc);
          putS(ctx, s, s.crestIds.back, x + dir * cell * sc, y + cell * sc * 0.7, 0.4 * p * nr, sc);
        }
      }
      if (tau < 4) {
        // The wash spreading back out from the rock.
        const spread = Math.round((3 + 7 * nr) * Math.min(1, tau / 0.8));
        const fade = 1 - tau / 4;
        for (let i = 0; i < spread; i += 1) {
          const a = fade * (0.85 - i / (spread + 2)) * (0.5 + 0.5 * nr);
          putS(ctx, s, s.washIds[(i + ci + Math.floor(t * 4)) & 3], snap(ct.x + (ct.dir || 1) * i * cell * sc), ct.y - cell * sc * 0.3 + Math.sin(i * 1.7 + t * 3) * k, a, sc);
        }
      }
      if (tau < 2.4) {
        // The burst: foam thrown up the face, falling back.
        const N = Math.round(6 + 16 * nr);
        const g = 26 * cell * sc;
        for (let j = 0; j < N; j += 1) {
          const hj = hash(j, ci, Math.floor((t + ct.ph) / ct.P));
          const hk = hash(ci, j, 77);
          const vy = (0.45 + 0.55 * hj) * Math.sqrt(2 * g * cell * sc * (4 + 9 * nr));
          const vx = (hk - 0.62) * cell * 7 * sc * (ct.dir || 1);
          const tt = tau - hk * 0.25;
          if (tt < 0) continue;
          const y = ct.y - (vy * tt - 0.5 * g * tt * tt);
          if (y > ct.y + cell) continue;
          const life = tt / 2.2;
          const gl = life < 0.25 ? (hj < 0.4 ? 2 : 0) : life < 0.55 ? (hj < 0.5 ? 1 : 0) : hj < 0.5 ? 4 : 3;
          putS(ctx, s, s.foamIds[gl], snap(ct.x + vx * tt), y, (1 - life) * (0.5 + 0.5 * nr), sc);
        }
      }
    }
    // Surf along the cliff foot; the swash over the shelf.
    for (let c = 0; c < s.surf.c1; c += 1) {
      const n = noise(c * 0.5 - t * 0.6, t * 0.3);
      if (n > 0.38) put(ctx, s, s.washIds[(c + Math.floor(t * 2)) & 3], c * cell, s.surf.y - cell * 0.6 + Math.sin(c + t * 2) * k, (n - 0.38) * 1.6);
    }
    for (const sh of s.shelf) {
      const p = ((t + 4) % 9.5) / 9.5;
      const front = (sh.x - s.shelf[0].x) / (w - s.shelf[0].x + 1);
      const a = Math.max(0, 1 - Math.abs(p * 2.2 - front - 0.3) * 4) * 0.8 + 0.12 * Math.sin(t * 2 + sh.ph);
      put(ctx, s, s.washIds[Math.floor(t * 3 + sh.ph) & 3], sh.x, sh.y + cell * 0.5, a);
    }
    for (const b of s.boulders) {
      const a = 0.55 * Math.max(0, Math.sin(t * 1.3 + b.ph));
      put(ctx, s, s.washIds[Math.floor(t * 3 + b.ph) & 3], b.x - cell, b.y + cell * 0.3, a);
      put(ctx, s, s.washIds[Math.floor(t * 3 + b.ph + 1) & 3], b.x + cell, b.y + cell * 0.4, a * 0.8);
    }
    // Grass on the near cliff leans in the sea wind.
    const gustX = ((t * 70 + s.seedT) % (w + 1200)) - 600;
    for (const tf of s.tufts) {
      const bend = k * tf.lift * (1.2 + 1.1 * Math.sin(t * 1.1 + tf.x * 0.02) + 0.6 * Math.sin(t * 2.7 + tf.ph) + 2.2 * Math.exp(-(((tf.x - gustX) / 220) ** 2)));
      put(ctx, s, tf.id, snap(tf.x - bend), tf.y + cell * 0.25, 0.95);
    }
    // Gulls hang on the wind, now and then a few wingbeats.
    for (const [i, gu] of s.gulls.entries()) {
      const a = t * gu.w * TAU * 0.25 + gu.ph;
      const x = gu.x + gu.rx * Math.cos(a) + 14 * k * (noise(t * 0.15, i * 3) - 0.5);
      const y = gu.y + gu.ry * Math.sin(a * 2) + 10 * k * (noise(t * 0.2 + 5, i * 3) - 0.5);
      const flap = noise(t * 0.35, i * 7 + 1) > 0.6;
      const fr = flap ? (Math.sin(t * 9 + i) > 0.3 ? 0 : Math.sin(t * 9 + i) < -0.3 ? 2 : 1) : Math.sin(t * 0.7 + i) > 0.85 ? 0 : 1;
      const set = y < hy * 0.55 ? s.gullIds.big : s.gullIds.dark;
      const sc = gu.big;
      for (let j = 0; j < 3; j += 1) putS(ctx, s, set[fr][j], snap(x + (j - 1) * cell * sc * 0.62), snap(y + (j === 1 ? 0 : -cell * 0.12)), 0.92, sc);
    }
    // One gull flock crossing the sky, every minute or so.
    const FP = 64;
    const fn = Math.floor((t + s.seedT * 0.3) / FP);
    const fp = ((t + s.seedT * 0.3) - fn * FP) / 26;
    if (fp < 1) {
      const fy = h * (0.12 + 0.12 * hash(fn, 1, 5));
      const fx = w + 40 * k - fp * (w + 80 * k);
      for (let b = 0; b < 4; b += 1) {
        const bx = fx + b * cell * 1.3;
        const by = fy + (b % 2 ? 1 : -1) * Math.ceil(b / 2) * cell * 0.7 + Math.sin(t + b) * 2 * k;
        const fr = Math.sin(t * 8 + b * 1.5) > 0 ? 0 : 2;
        for (let j = 0; j < 3; j += 1) putS(ctx, s, s.gullIds.big[fr][j], snap(bx + (j - 1) * cell * 0.34), snap(by), 0.75, 0.55);
      }
    }
    ctx.globalAlpha = 1;
  },
};

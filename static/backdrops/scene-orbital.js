// Scene: Orbital. The curved limb of a planet from orbit: city lights strung
// along dark coasts on the night side, continents and drifting cloud glyphs
// (and one turning storm) on the day side, a thin atmosphere line, and the
// sunrise at the terminator, whose glow runs out along the curve each time
// the sun crests. A space station turns slowly on its truss, satellites
// cross, and a meteor sometimes burns into the air below.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (v) => {
  const x = clamp01(v);
  return x * x * (3 - 2 * x);
};
const q = (v, n) => Math.round(clamp01(v) * n) / n;

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 13 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const rows = Math.ceil(h / cell) + 1;
  const port = (s.port = h > w * 1.1);
  // The planet: a huge disc whose top edge crosses the lower screen.
  const Rp = Math.max(w, h) * (port ? 1.1 : 1.15);
  const P = (s.P0 = { x: w * (port ? 0.42 : 0.4), y: h * (port ? 0.64 : 0.6) + Rp, r: Rp });
  // Where the terminator meets the limb: the sunrise.
  const tx = w * (port ? 0.74 : 0.7);
  const ta = Math.asin((tx - P.x) / Rp);
  s.T = { a: ta, x: tx, y: P.y - Rp * Math.cos(ta) };
  const soft = w * 0.07 + 40 * k;
  const dayAt = (x, y) => smooth(0.5 + ((x - s.T.x) + (y - s.T.y) * 0.35) / soft);
  s.dayAt = dayAt;
  // Space, stars, glow over the limb.
  const [back, g] = layer(s);
  const sp = g.createLinearGradient(0, 0, 0, h);
  sp.addColorStop(0, "#010208");
  sp.addColorStop(0.5, "#030614");
  sp.addColorStop(1, "#071230");
  g.fillStyle = sp;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 520 * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    const b = Math.pow(R(), 3);
    g.fillStyle = `rgba(${R() < 0.2 ? "255,228,200" : R() < 0.3 ? "190,210,255" : "235,240,255"},${0.18 + 0.75 * b})`;
    const r = (0.5 + 1.1 * b) * k;
    g.fillRect(R() * w, R() * h * 0.85, r, r);
  }
  // A faint band of the galaxy, high and diagonal.
  g.save();
  g.translate(w * 0.55, h * 0.18);
  g.rotate(-0.32);
  g.filter = `blur(${Math.max(2, 30 * k)}px)`;
  g.fillStyle = "rgba(120,140,210,0.1)";
  g.fillRect(-w, -h * 0.07, w * 2, h * 0.14);
  g.filter = "none";
  for (let i = 0; i < 260 * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    g.fillStyle = `rgba(220,226,255,${R.range(0.08, 0.35)})`;
    g.fillRect(R.range(-w, w), (R() + R() + R() - 1.5) * h * 0.08, 0.8 * k, 0.8 * k);
  }
  g.restore();
  dotGrid(g, s, "#5a6aa0", 0.16);
  const halo = g.createRadialGradient(P.x, P.y, Rp * 0.98, P.x, P.y, Rp * 1.12);
  halo.addColorStop(0, "rgba(60,120,220,0.22)");
  halo.addColorStop(1, "rgba(40,80,200,0)");
  g.fillStyle = halo;
  g.fillRect(0, 0, w, h);
  // Glyph stars, steady (no air to twinkle through).
  for (let i = 0; i < 26 * Math.min(1.3, (w * h) / 1.3e6); i += 1) {
    put(g, s, tile(s, R.pick(["+", "*", "."]), null, R.pick(["#ffffff", "#cfe0ff", "#ffe4c8"])), Math.floor((R() * w) / cell) * cell, Math.floor((R() * h * 0.6) / cell) * cell, R.range(0.35, 0.9));
  }
  g.globalAlpha = 1;
  // The planet body: night ocean, day ocean, haze toward the limb.
  g.save();
  g.beginPath();
  g.arc(P.x, P.y, Rp, 0, TAU);
  g.clip();
  const night = g.createRadialGradient(P.x, P.y, Rp * 0.85, P.x, P.y, Rp);
  night.addColorStop(0, "#020814");
  night.addColorStop(0.9, "#06142a");
  night.addColorStop(1, "#0c2448");
  g.fillStyle = night;
  g.fillRect(0, 0, w, h);
  const day = g.createLinearGradient(s.T.x - soft * 0.6, 0, s.T.x + soft * 1.4, 0);
  day.addColorStop(0, "rgba(30,90,170,0)");
  day.addColorStop(1, "rgba(30,96,176,1)");
  g.fillStyle = day;
  g.fillRect(0, 0, w, h);
  const haze = g.createRadialGradient(P.x, P.y, Rp * 0.93, P.x, P.y, Rp);
  haze.addColorStop(0, "rgba(90,170,255,0)");
  haze.addColorStop(1, "rgba(110,190,255,0.5)");
  g.globalCompositeOperation = "lighter";
  g.fillStyle = haze;
  g.globalAlpha = 0.6;
  g.fillRect(0, 0, w, h);
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";
  // Sun glint on the day ocean.
  const gx = s.T.x + soft * 1.2;
  const gy = s.T.y + h * 0.1;
  const glint = g.createRadialGradient(gx, gy, 0, gx, gy, w * 0.12);
  glint.addColorStop(0, "rgba(200,230,255,0.35)");
  glint.addColorStop(1, "rgba(200,230,255,0)");
  g.fillStyle = glint;
  g.fillRect(0, 0, w, h);
  g.restore();
  // Surface tiles: land by day, coasts strung with lights by night, a faint
  // graticule. Each tile keeps its place on the sphere for the clouds.
  const off = R() * 100;
  s.cells = [];
  s.twinkle = [];
  const cityC = ["#ffd080", "#ffbe60", "#ffe2a8", "#fff0d0"];
  const top0 = Math.floor((P.y - Rp) / cell) - 1;
  for (let r = Math.max(0, top0); r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const x = (c + 0.5) * cell;
      const y = (r + 0.5) * cell;
      const dx = x - P.x;
      const dy = y - P.y;
      const rho = Math.hypot(dx, dy);
      if (rho > Rp - cell * 0.3) continue;
      const sr = Rp * Math.acos(Math.min(1, rho / Rp));
      const st = Rp * Math.atan2(dx, -dy);
      const lim = clamp01(sr / (Rp * 0.1));
      const d = dayAt(x, y);
      const u = st / (260 * k) + off;
      const v = sr / (150 * k);
      const land = noise(u, v) * 0.7 + noise(u * 3.1, v * 3.1 + 7) * 0.3;
      const cl = { x: c * cell, y: r * cell, u: st / (120 * k), v: sr / (60 * k), d, lim, land: land > 0.58 };
      s.cells.push(cl);
      const grat = (Math.abs(((st / (220 * k)) % 1 + 1) % 1 - 0.5) > 0.48 || Math.abs(((sr / (110 * k)) % 1) - 0.5) > 0.46) && (c + r) % 2 === 0;
      if (land > 0.58) {
        const coast = land < 0.61;
        const high = land > 0.7 && noise(u * 5, v * 5 + 3) > 0.5;
        const bio = noise(u * 0.8 + 30, v * 0.8);
        // Day: forest, grass, desert in broad zones; hazy toward the limb.
        if (d > 0.12) {
          let fill = high ? "#8a8478" : bio > 0.58 ? "#b4a074" : bio > 0.45 ? "#7c8a5a" : "#4e6e4c";
          const gl = high ? "^" : coast ? "~" : hash(c, r, 1) < 0.22 ? (bio > 0.58 ? "." : bio > 0.45 ? "'" : ":") : " ";
          fill = mixc(fill, "#2a5a90", 0.22);
          fill = mixc(fill, "#ff9a60", q((1 - d) * 0.5, 3));
          fill = mixc(fill, "#7ab4f0", q((1 - lim) * 0.85, 5));
          put(g, s, tile(s, gl, fill, shade(fill, 0.82)), c * cell, r * cell, 0.82 * smooth((d - 0.12) / 0.55));
        } else {
          put(g, s, tile(s, coast ? "~" : hash(c, r, 2) < 0.5 ? "." : "'", null, "#2a3c5c"), c * cell, r * cell, 0.5 * lim);
        }
        // Night: cities, strung along the coasts.
        const pop = noise(u * 4 + 50, v * 4) * (coast ? 1.4 : 0.95) * (high ? 0.4 : 1);
        if (d < 0.6 && pop > 0.52) {
          const a = (1 - d / 0.6) * clamp01(0.35 + lim);
          const col = cityC[Math.floor(hash(c, r, 3) * 4)];
          const big = pop > 0.86;
          const gl = big ? "#" : pop > 0.76 ? "*" : pop > 0.64 ? "+" : pop > 0.58 ? ":" : ".";
          const id = tile(s, gl, big ? "#5a3c14" : null, col);
          if (big) {
            const cg = g.createRadialGradient(x, y, 0, x, y, cell * 3);
            cg.addColorStop(0, `rgba(255,190,100,${0.2 * a})`);
            cg.addColorStop(1, "rgba(255,190,100,0)");
            g.globalAlpha = 1;
            g.fillStyle = cg;
            g.fillRect(x - cell * 3, y - cell * 3, cell * 6, cell * 6);
          }
          put(g, s, id, c * cell, r * cell, a * (0.45 + 0.55 * clamp01((pop - 0.56) * 3)));
          if (hash(c, r, 8) < 0.12) s.twinkle.push({ x: c * cell, y: r * cell, id, a, ph: hash(c, r, 9) * TAU });
        }
      } else if (grat && lim > 0.3) {
        put(g, s, tile(s, ".", null, d > 0.5 ? "#8ac0f0" : "#3a5a8a"), c * cell, r * cell, 0.35);
      }
      // Highways of light between cities.
      if (land > 0.6 && d < 0.4 && Math.abs(noise(u * 1.5 + 90, v * 1.5) - 0.5) < 0.012 * (1 + 1 / Math.max(0.2, lim))) put(g, s, tile(s, ".", null, "#ffc070"), c * cell, r * cell, 0.7 * (1 - d / 0.4));
    }
  }
  g.globalAlpha = 1;
  // Atmosphere: a thin bright line along the limb, blue by day, a faint
  // green airglow by night, warm at the sunrise.
  const conic = (alpha) => {
    const cg = g.createConicGradient(-Math.PI / 2 + s.T.a - Math.PI, P.x, P.y);
    cg.addColorStop(0, `rgba(80,200,140,${alpha * 0.35})`);
    cg.addColorStop(0.46, `rgba(80,210,150,${alpha * 0.4})`);
    cg.addColorStop(0.482, `rgba(220,90,80,${alpha * 0.7})`);
    cg.addColorStop(0.495, `rgba(255,150,80,${alpha})`);
    cg.addColorStop(0.5, `rgba(255,220,170,${alpha})`);
    cg.addColorStop(0.508, `rgba(150,215,255,${alpha})`);
    cg.addColorStop(0.54, `rgba(110,190,255,${alpha * 0.9})`);
    cg.addColorStop(1, `rgba(80,200,140,${alpha * 0.35})`);
    return cg;
  };
  g.save();
  g.globalCompositeOperation = "lighter";
  for (const [wd, a, blur, rr] of [[18 * k, 0.25, 12 * k, 1.004], [6 * k, 0.5, 3 * k, 1.002], [1.6 * k, 0.9, 0, 1.0005]]) {
    g.filter = blur > 0.5 ? `blur(${blur}px)` : "none";
    g.strokeStyle = conic(a);
    g.lineWidth = Math.max(1, wd);
    g.beginPath();
    g.arc(P.x, P.y, Rp * rr, 0, TAU);
    g.stroke();
  }
  g.restore();
  s.back = back;
  // Clouds and the storm, drawn live.
  s.cloudIds = [];
  for (let lv = 0; lv <= 6; lv += 1) {
    const day = lv / 6;
    const ink = mixc("#1a2a44", mixc("#ffb890", "#f4f8ff", clamp01(day * 1.6 - 0.2)), day);
    const ids = ["'", "-", "~", "=", "%", "#"].map((gl) => tile(s, gl, null, ink));
    ids.fill = ["#", "%"].map((gl) => tile(s, gl, mixc("#0a1830", "#dfe8f4", day * 0.9), ink));
    s.cloudIds.push(ids);
  }
  s.flashIds = ["~", "%", "#"].map((gl) => tile(s, gl, null, "#e8f0ff"));
  // The storm sits on the day side, away from the centre column.
  const sc = s.cells.filter((cl) => cl.d > 0.9 && cl.lim > 0.6);
  const storm = sc.length ? sc[Math.floor(R() * sc.length)] : null;
  s.storm = storm ? { u: storm.u, v: storm.v } : null;
  s.cloudOff = R() * 100;
  // A coarse canvas, one pixel per tile, for the soft body of the clouds.
  s.cr0 = Math.max(0, top0);
  s.cloudC = document.createElement("canvas");
  s.cloudC.width = cols;
  s.cloudC.height = Math.max(1, rows - s.cr0);
  s.cloudG = s.cloudC.getContext("2d");
  s.cloudImg = s.cloudG.createImageData(s.cloudC.width, s.cloudC.height);
  for (const cl of s.cells) cl.px = ((Math.round(cl.y / cell) - s.cr0) * cols + Math.round(cl.x / cell)) * 4;
  // City lights that flicker, and a lightning storm on the night side.
  const nc = s.cells.filter((cl) => cl.d < 0.1 && cl.lim > 0.4);
  s.lightning = nc.length ? nc[Math.floor(R() * nc.length)] : null;
  // The space station.
  buildStation(s, R);
  s.station = { x: w * (port ? 0.5 : 0.22), y: h * (port ? 0.26 : 0.27) };
  // Satellites.
  s.satIds = ["-", "[", "o", "]", "-"].map((gl, i) => tile(s, gl, i === 2 ? "#c8d0dc" : null, i === 2 ? "#3a4250" : "#9ab0d0"));
  s.blinkId = tile(s, ".", null, "#ff5a4a");
  s.dotId = tile(s, "*", null, "#ffffff");
  s.meteorIds = ["*", "=", "-", "-", "."].map((gl, i) => tile(s, gl, null, ["#ffffff", "#ffe0a0", "#ffb070", "#a0ffb0", "#80d0a0"][i]));
  s.flareIds = { h: tile(s, "-", null, "#fff4e0"), v: tile(s, "|", null, "#fff4e0"), c: tile(s, "*", null, "#ffffff"), o: tile(s, "o", null, "#ffd0a0"), O: tile(s, "O", null, "#a0c8ff") };
  s.seedT = R() * 1000;
}

// The station: a long truss with solar wings at both ends, modules and
// radiators at the centre. Cells are (c, r) around the truss axis; the
// station rolls about the truss, so rows shrink with cos(roll).
function buildStation(s, R) {
  const cells = [];
  const add = (c, r, gl, kind) => cells.push({ c, r, gl, kind });
  const span = 16;
  for (let c = -span; c <= span; c += 1) add(c, 0, Math.abs(c) === span ? "|" : c % 4 === 0 ? "#" : "=", "truss");
  for (const wc of [-14, -10, 10, 14]) {
    for (const side of [-1, 1]) {
      for (let j = 1; j <= 6; j += 1) {
        for (let i = -1; i <= 1; i += 1) add(wc + i, side * j, i === 0 ? "|" : j === 6 ? "=" : j % 3 === 0 ? "=" : "#", "panel");
      }
    }
  }
  const mods = [
    [-3, -1, "["], [-2, -1, "="], [-1, -1, "O"], [0, -1, "="], [1, -1, "="], [2, -1, "]"],
    [-1, -2, "["], [0, -2, "o"], [1, -2, "]"],
    [-4, 1, "["], [-3, 1, "="], [-2, 1, "="], [-1, 1, "O"], [0, 1, "="], [1, 1, "="], [2, 1, "="], [3, 1, "]"],
    [0, 2, "H"], [0, 3, "|"], [-1, 4, "["], [0, 4, "O"], [1, 4, "]"], [0, 5, "V"],
    [3, -2, "/"], [4, -3, "o"], [3, -4, "\\"], [2, -5, "<"],
  ];
  for (const [c, r, gl] of mods) add(c, r, gl, "module");
  for (const c of [-3, -2, 2, 3]) add(c, 4, "=", "panel");
  for (const c of [5, 6, -6, -7]) for (const r of [-1, -2, -3]) add(c, r, "|", "radiator");
  s.st = cells.map((cl) => ({ ...cl }));
  s.stIds = {
    truss: [tile(s, "=", "#7a808c", "#2a2e38"), tile(s, "#", "#8a909c", "#2a2e38"), tile(s, "|", "#9aa0ac", "#2a2e38")],
    module: new Map(),
    radiator: [tile(s, "|", "#c4cad4", "#7a8698"), tile(s, "|", "#7a8494", "#3a4250"), tile(s, "-", "#c8d0dc", "#6a7484")],
  };
  for (const gl of ["[", "]", "=", "O", "o", "H", "/", "\\", "A", "|", "V", "<"]) s.stIds.module.set(gl, [tile(s, gl, "#c2c6ce", "#4a505c"), tile(s, gl, "#8a8e98", "#2e323a")]);
  // Panels: lit front (gold), dim front, back side (dark blue), edge-on.
  s.panelIds = [];
  for (let lv = 0; lv <= 5; lv += 1) {
    const f = mixc("#22305a", "#eab058", (lv / 5) ** 1.3);
    s.panelIds.push(["#", "=", "|"].map((gl) => tile(s, gl, gl === "|" ? "#9aa0ac" : f, gl === "|" ? "#2a2e38" : shade(f, 0.62))));
  }
  s.panelBack = ["#", "=", "|"].map((gl) => tile(s, gl, gl === "|" ? "#5a606c" : "#141c34", gl === "|" ? "#1a1e28" : "#2a3a64"));
  s.panelEdge = tile(s, "-", null, "#c8b070");
  s.navIds = [tile(s, ".", null, "#ff4a3a"), tile(s, ".", null, "#4aff7a"), tile(s, "*", null, "#ffffff")];
  s.stRollPh = R() * TAU;
}

function putS(ctx, s, id, x, y, a, sc) {
  if (a <= 0.01) return;
  const P = s.P;
  ctx.globalAlpha = a > 1 ? 1 : a;
  ctx.drawImage(s.atlas.c, (id % 64) * P, Math.floor(id / 64) * P, P, P, x, y, s.cell * sc, s.cell * sc);
}

function station(ctx, s, t) {
  const { k } = s;
  // On narrow screens the station is drawn smaller, to fit.
  const sc = Math.min(1, (s.w * 0.92) / (36 * s.cell));
  const cell = s.cell * sc;
  const roll = (t * TAU) / 75 + s.stRollPh;
  const cr = Math.cos(roll);
  const sr = Math.sin(roll);
  const x0 = s.station.x + (s.port ? 0.25 : 1) * (40 * k * Math.sin(t * 0.021) + 10 * k * Math.sin(t * 0.07));
  const y0 = s.station.y + 14 * k * Math.sin(t * 0.03 + 1);
  // The sun is down-right: panels are brightest when their face turns to it.
  const face = Math.cos(roll - 0.6);
  const lv = Math.round(clamp01(face) * 5);
  const sorted = s.st.map((cl) => ({ cl, z: cl.r * sr })).sort((a, b) => a.z - b.z);
  for (const { cl, z } of sorted) {
    const y = y0 + Math.round(cl.r * cr) * cell;
    const x = x0 + cl.c * cell;
    const far = z < 0;
    let id;
    if (cl.kind === "truss") id = s.stIds.truss[cl.gl === "=" ? 0 : cl.gl === "#" ? 1 : 2];
    else if (cl.kind === "panel") {
      if (Math.abs(cr) < 0.18) id = s.panelEdge;
      else {
        const gi = cl.gl === "#" ? 0 : cl.gl === "=" ? 1 : 2;
        id = face > 0 ? s.panelIds[lv][gi] : s.panelBack[gi];
      }
    } else if (cl.kind === "module") id = s.stIds.module.get(cl.gl)[cl.r * cr < 0 ? 0 : sr * cl.r > 0 ? 0 : 1];
    else id = Math.abs(cr) < 0.25 ? s.stIds.radiator[2] : s.stIds.radiator[far ? 1 : 0];
    putS(ctx, s, id, Math.round(x * s.dpr) / s.dpr, Math.round(y * s.dpr) / s.dpr, far ? 0.85 : 1, sc);
  }
  // Glint off the arrays as they swing through the sun.
  const gl = Math.pow(clamp01(face), 40);
  if (gl > 0.02) {
    ctx.globalCompositeOperation = "lighter";
    for (const wc of [-12, 12]) {
      const gx = x0 + wc * cell + cell / 2;
      const gy = y0 + cell / 2;
      const rg = ctx.createRadialGradient(gx, gy, 0, gx, gy, cell * 6);
      rg.addColorStop(0, `rgba(255,236,190,${0.6 * gl})`);
      rg.addColorStop(1, "rgba(255,236,190,0)");
      ctx.globalAlpha = 1;
      ctx.fillStyle = rg;
      ctx.fillRect(gx - cell * 6, gy - cell * 6, cell * 12, cell * 12);
    }
    ctx.globalCompositeOperation = "source-over";
  }
  // Navigation lights and a strobe.
  putS(ctx, s, s.navIds[0], x0 - 17 * cell, y0, Math.sin(t * 2.2) > 0 ? 1 : 0.2, sc);
  putS(ctx, s, s.navIds[1], x0 + 17 * cell, y0, Math.sin(t * 2.2 + 1) > 0 ? 1 : 0.2, sc);
  putS(ctx, s, s.navIds[2], x0, y0 - 3 * cell * cr, (t % 2.6) < 0.12 ? 1 : 0, sc);
  ctx.globalAlpha = 1;
}

export const orbital = {
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
    // City lights breathing.
    for (const tw of s.twinkle) put(ctx, s, tw.id, tw.x, tw.y, tw.a * 0.5 * (0.5 + 0.5 * Math.sin(t * 1.7 + tw.ph)));
    // Clouds: bands streaming slowly east, a storm turning on the day side.
    const drift = t * 0.02 + s.cloudOff;
    const st = s.storm;
    const lt = s.lightning;
    const flashOn = lt && (t % 6.3 < 0.12 || (t % 6.3 > 0.3 && t % 6.3 < 0.38) || (t % 17.1 < 0.1));
    const img = s.cloudImg.data;
    img.fill(0);
    for (const cl of s.cells) {
      let n = noise(cl.u * 0.5 + drift, cl.v * 1.4) * 0.65 + noise(cl.u * 1.6 - drift * 1.7, cl.v * 3.2 + 4) * 0.35;
      if (st) {
        const du = cl.u - st.u;
        const dv = (cl.v - st.v) * 1.6;
        const d = Math.hypot(du, dv);
        if (d < 4) {
          const arm = 0.5 + 0.5 * Math.cos(Math.atan2(dv, du) * 2 - d * 1.9 + t * 0.25);
          n = Math.max(n, (1 - d / 4) * (0.35 + 0.65 * arm) + (d < 0.6 ? -0.4 : 0));
        }
      }
      cl.n = n;
      if (n > 0.4 && cl.d > 0.1) {
        const o = cl.px;
        const warm = 1 - cl.d;
        img[o] = 255;
        img[o + 1] = 240 - 50 * warm;
        img[o + 2] = 235 - 80 * warm;
        img[o + 3] = 255 * clamp01((n - 0.4) * 2.6) * 0.6 * smooth((cl.d - 0.1) / 0.5) * (0.5 + 0.5 * cl.lim);
      }
    }
    // Soft cloud body first, then the glyphs on it.
    s.cloudG.putImageData(s.cloudImg, 0, 0);
    ctx.globalAlpha = 1;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(s.cloudC, 0, s.cr0 * cell, s.cloudC.width * cell, s.cloudC.height * cell);
    for (const cl of s.cells) {
      const n = cl.n;
      const lit = lt && flashOn && Math.abs(cl.u - lt.u) < 1.6 && Math.abs(cl.v - lt.v) < 1.2;
      if (n < 0.5 || (cl.d < 0.12 && !lit)) continue;
      const ids = s.cloudIds[Math.round(cl.d * 6)];
      const dens = (n - 0.5) / 0.38;
      const a = (0.25 + 0.75 * cl.d) * (0.35 + 0.65 * clamp01(0.3 + cl.lim));
      put(ctx, s, ids[Math.min(5, Math.floor(dens * 6))], cl.x, cl.y, a * (cl.d > 0.3 ? 1 : 0.6));
      if (lit) put(ctx, s, s.flashIds[Math.floor(dens * 3)], cl.x, cl.y, 0.9);
    }
    if (flashOn) {
      const fg = ctx.createRadialGradient(lt.x, lt.y, 0, lt.x, lt.y, cell * 6);
      fg.addColorStop(0, "rgba(200,220,255,0.35)");
      fg.addColorStop(1, "rgba(200,220,255,0)");
      ctx.globalAlpha = 1;
      ctx.fillStyle = fg;
      ctx.fillRect(lt.x - cell * 6, lt.y - cell * 6, cell * 12, cell * 12);
    }
    // The sunrise: a warm glow at the terminator that wanders a little and,
    // when the sun crests, runs out along the curve both ways.
    const P = s.P0;
    const SP = 48;
    const sp = ((t + s.seedT) % SP) / SP;
    const burst = sp < 0.4 ? Math.pow(Math.sin(Math.PI * (sp / 0.4)), 1.5) : 0;
    const a0 = s.T.a + 0.004 * Math.sin(t * 0.05);
    const sx = P.x + P.r * Math.sin(a0);
    const sy = P.y - P.r * Math.cos(a0);
    ctx.globalCompositeOperation = "lighter";
    const run = (0.04 + 0.3 * smooth(Math.min(1, sp / 0.4))) * (w / P.r);
    for (const [lw, al, col] of [[14 * k, 0.12, "255,160,90"], [5 * k, 0.3, "255,190,130"], [2 * k, 0.6, "255,230,200"]]) {
      ctx.globalAlpha = 1;
      ctx.lineWidth = Math.max(1, lw);
      for (const dir of [-1, 1]) {
        const a1 = a0 + dir * run;
        const gx0 = sx;
        const gx1 = P.x + P.r * Math.sin(a1);
        const gy1 = P.y - P.r * Math.cos(a1);
        const lg = ctx.createLinearGradient(gx0, sy, gx1, gy1);
        lg.addColorStop(0, `rgba(${col},${al * (0.5 + 0.9 * burst)})`);
        lg.addColorStop(1, `rgba(${col},0)`);
        ctx.strokeStyle = lg;
        ctx.beginPath();
        ctx.arc(P.x, P.y, P.r * 1.0015, -Math.PI / 2 + Math.min(a0, a1), -Math.PI / 2 + Math.max(a0, a1));
        ctx.stroke();
      }
    }
    const gr = cell * (4 + 8 * burst);
    const sg = ctx.createRadialGradient(sx, sy, 0, sx, sy, gr * 3);
    sg.addColorStop(0, `rgba(255,244,228,${0.55 + 0.45 * burst})`);
    sg.addColorStop(0.12, `rgba(255,200,150,${0.22 + 0.3 * burst})`);
    sg.addColorStop(0.45, `rgba(255,150,120,${0.05 + 0.08 * burst})`);
    sg.addColorStop(1, "rgba(255,140,120,0)");
    ctx.globalAlpha = 1;
    ctx.fillStyle = sg;
    ctx.fillRect(sx - gr * 3, sy - gr * 3, gr * 6, gr * 6);
    ctx.globalCompositeOperation = "source-over";
    // Glyph flare on the cresting sun, ghosts strung across the frame.
    const F = s.flareIds;
    const fx = Math.round(sx / cell) * cell - cell / 2;
    const fy = Math.round(sy / cell) * cell - cell;
    const fl = 0.35 + 0.65 * burst;
    put(ctx, s, F.c, fx, fy, fl);
    const arm = Math.round(3 + 9 * burst);
    for (let i = 1; i <= arm; i += 1) {
      const a = fl * (1 - i / (arm + 1));
      put(ctx, s, F.h, fx + i * cell, fy, a);
      put(ctx, s, F.h, fx - i * cell, fy, a);
      if (i < arm * 0.5) {
        put(ctx, s, F.v, fx, fy - i * cell, a * 0.8);
        put(ctx, s, F.v, fx, fy + i * cell, a * 0.5);
      }
    }
    if (burst > 0.05) {
      for (const [f, id, sc] of [[0.35, F.o, 0.25], [0.6, F.O, 0.2], [0.85, F.o, 0.15], [1.15, F.O, 0.12]]) {
        const gx = sx + (w * 0.42 - sx) * f;
        const gy = sy + (h * 0.3 - sy) * f;
        put(ctx, s, id, gx, gy, burst * sc * 2);
      }
    }
    station(ctx, s, t);
    // Satellites cross; one now and then flares.
    for (const [j, per] of [[0, 23], [1, 37]].values()) {
      const tt = t + s.seedT * (j + 1);
      const n = Math.floor(tt / per);
      const p = (tt - n * per) / (per * 0.8);
      if (p >= 1) continue;
      const ya = h * (0.06 + 0.4 * hash(n, j, 1));
      const yb = h * (0.06 + 0.4 * hash(n, j, 2));
      const dir = hash(n, j, 3) < 0.5 ? 1 : -1;
      const x = dir > 0 ? -cell * 6 + p * (w + cell * 12) : w + cell * 6 - p * (w + cell * 12);
      const y = ya + (yb - ya) * p;
      if (j === 0) {
        for (let i = 0; i < 5; i += 1) put(ctx, s, s.satIds[i], x + (i - 2) * cell * 0.7, y, 0.85);
        put(ctx, s, s.blinkId, x, y - cell * 0.6, Math.sin(t * 4) > 0.6 ? 1 : 0);
      } else {
        const fl = Math.exp(-(((p - 0.5) / 0.06) ** 2));
        put(ctx, s, s.dotId, x, y, 0.35 + 0.65 * fl);
        if (fl > 0.1) {
          const rg = ctx.createRadialGradient(x + cell / 2, y + cell / 2, 0, x + cell / 2, y + cell / 2, cell * 3);
          rg.addColorStop(0, `rgba(255,255,255,${0.5 * fl})`);
          rg.addColorStop(1, "rgba(255,255,255,0)");
          ctx.globalAlpha = 1;
          ctx.fillStyle = rg;
          ctx.fillRect(x - cell * 3, y - cell * 3, cell * 7, cell * 7);
        }
      }
    }
    // A meteor burning into the air over the night side.
    const MP = 41;
    const mn = Math.floor((t + s.seedT * 0.5) / MP);
    const mp = ((t + s.seedT * 0.5) - mn * MP) / 1.6;
    if (mp < 1) {
      const ma = s.T.a - 0.05 - 0.25 * hash(mn, 4, 4) * (w / P.r);
      const mr = P.r * (0.985 - 0.01 * hash(mn, 5, 5));
      const hx = P.x + mr * Math.sin(ma) + mp * 120 * k;
      const hy = P.y - mr * Math.cos(ma) + mp * 30 * k;
      s.meteorIds.forEach((id, i) => put(ctx, s, id, hx - i * cell * 0.8, hy - i * cell * 0.2, Math.sin(Math.PI * mp) * (1 - i / 5)));
    }
    ctx.globalAlpha = 1;
  },
};

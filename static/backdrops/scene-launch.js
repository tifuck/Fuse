// Scene: Launch Site. A rocket stands on its pad at dawn beside a lattice
// gantry, venting cold vapour that tumbles down its flanks and drifts off
// over the scrub. Searchlights sweep up into a sky going pink and orange
// over the sea. Every ninety seconds the access arms swing back, the engines
// light, steam boils out across the pad, and the rocket climbs on a plume of
// glyph fire, leaving a trail that catches the sun high up and slowly
// spreads; then a new rocket stands ready in the settling steam.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const PERIOD = 90;
const ARMS_OUT = 57;
const IGNITE = 61;
const LIFT = 64;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (v) => {
  const x = clamp01(v);
  return x * x * (3 - 2 * x);
};
const WARM = "#ffb48a";
const COOL = "#5a5a9a";

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 12 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const portrait = h > w * 1.15;
  const hy = (s.hy = Math.round((h * (portrait ? 0.66 : 0.68)) / cell) * cell);
  const padRow = (s.padRow = Math.round((h * (portrait ? 0.84 : 0.85)) / cell));
  const RH = (s.RH = Math.max(14, Math.round((h * (portrait ? 0.4 : 0.47)) / cell)));
  const rc = (s.rc = Math.round((w * (portrait ? 0.5 : 0.78)) / cell));
  const sunX = (s.sunX = w * (portrait ? 0.2 : 0.3));

  // Sky, sun glow, clouds, sea and the far shore: one still image.
  const [back, g] = layer(s);
  const sky = g.createLinearGradient(0, 0, 0, hy);
  sky.addColorStop(0, "#14143a");
  sky.addColorStop(0.32, "#352a5e");
  sky.addColorStop(0.6, "#7a4876");
  sky.addColorStop(0.8, "#c8687a");
  sky.addColorStop(0.93, "#f09a74");
  sky.addColorStop(1, "#ffc890");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  const sun = g.createRadialGradient(sunX, hy, 0, sunX, hy, Math.max(w, h) * 0.55);
  sun.addColorStop(0, "rgba(255,226,170,0.75)");
  sun.addColorStop(0.18, "rgba(255,180,120,0.35)");
  sun.addColorStop(0.5, "rgba(240,120,110,0.1)");
  sun.addColorStop(1, "rgba(240,120,110,0)");
  g.fillStyle = sun;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 120 * Math.min(1, (w * h) / 1.3e6); i += 1) {
    const y = Math.pow(R(), 2) * hy * 0.4;
    g.fillStyle = `rgba(230,226,255,${R.range(0.1, 0.5) * (1 - y / (hy * 0.4))})`;
    const r = R.range(0.6, 1.3) * k;
    g.fillRect(R() * w, y, r, r);
  }
  // Long clouds, lit from below on the sun side.
  g.save();
  g.filter = `blur(${Math.max(1.5, 9 * k)}px)`;
  for (let i = 0; i < 14; i += 1) {
    const y = hy * R.range(0.18, 0.78);
    const x = R() * w;
    const near = 1 - Math.min(1, Math.abs(x - sunX) / (w * 0.6));
    const low = y / hy;
    g.fillStyle = `rgba(${Math.round(150 + 105 * low)},${Math.round(90 + 80 * low * near)},${Math.round(130 - 30 * low)},${R.range(0.18, 0.4)})`;
    g.beginPath();
    g.ellipse(x, y, R.range(120, 380) * k, R.range(4, 12) * k, 0, 0, TAU);
    g.fill();
  }
  g.restore();
  dotGrid(g, s, "#d8b0c8", 0.18);
  // The sea: a band of dusky water lit along the sun's path.
  const sea = g.createLinearGradient(0, hy, 0, hy + h * 0.06);
  sea.addColorStop(0, "#9a5a78");
  sea.addColorStop(1, "#4a3050");
  g.fillStyle = sea;
  g.fillRect(0, hy, w, h - hy);
  const path = g.createRadialGradient(sunX, hy, 0, sunX, hy, w * 0.3);
  path.addColorStop(0, "rgba(255,210,150,0.55)");
  path.addColorStop(1, "rgba(255,210,150,0)");
  g.fillStyle = path;
  g.fillRect(0, hy, w, h * 0.06);
  // The flats: low scrubland rising to the pad, cooler toward the viewer.
  const landY = hy + Math.round((h * 0.035) / cell) * cell;
  s.landY = landY;
  const landAt = (x) => landY + h * 0.008 * (noise(x * 0.004 / k, 3.3) - 0.5);
  const land = new Path2D();
  land.moveTo(0, h);
  for (let x = 0; x <= w + 4; x += 4) land.lineTo(x, landAt(x));
  land.lineTo(w, h);
  land.closePath();
  const lf = g.createLinearGradient(0, landY, 0, h);
  lf.addColorStop(0, "#5a3a58");
  lf.addColorStop(0.35, "#2e2238");
  lf.addColorStop(1, "#120e1c");
  g.fillStyle = lf;
  g.fill(land);
  g.save();
  g.clip(land);
  g.translate(sunX, landY);
  g.scale(1, 0.18);
  const rim = g.createRadialGradient(0, 0, 0, 0, 0, w * 0.45);
  rim.addColorStop(0, "rgba(255,170,120,0.3)");
  rim.addColorStop(1, "rgba(255,170,120,0)");
  g.fillStyle = rim;
  g.fillRect(-w, -w * 0.5, w * 2, w);
  g.restore();
  // Distant structures on the flats, hazy violet: the assembly hall, a
  // water tower, a second pad with its own small rocket.
  const haze = "#6a4a6e";
  const far = (c, r, gl, f, ink) => put(g, s, tile(s, gl, f, ink), c * cell, r * cell);
  const lr = Math.round(landY / cell);
  const vabC = Math.round((w * (portrait ? 0.04 : 0.06)) / cell);
  const vabH = Math.max(5, Math.min(portrait ? 8 : 12, Math.round(RH * 0.28)));
  const vabW = Math.round(vabH * 0.75);
  for (let r = 0; r < vabH; r += 1) {
    for (let c = 0; c < vabW; c += 1) {
      const door = c >= 2 && c <= vabW - 3 && r > 1;
      const lit = c === 0 ? 1.12 : c === vabW - 1 ? 0.9 : 1;
      const f = shade(mixc("#5e4468", "#b07084", 0.2 + 0.3 * (r / vabH)), lit);
      const win = !door && r > 0 && R() < 0.05;
      far(vabC + c, lr - vabH + r, door ? (c % 2 ? "|" : " ") : r === 0 ? "=" : win ? ":" : " ", f, door ? shade(f, 1.12) : win ? "#e8a888" : shade(f, 1.1));
    }
  }
  const wt = vabC + vabW + (portrait ? 2 : 4);
  [["/", "O", "\\"], [" ", "|", " "], ["/", "|", "\\"], ["/", " ", "\\"]].forEach((row, r) => row.forEach((gl, c) => {
    if (gl === " ") return;
    const f = r === 0 ? "#7a5a7a" : null;
    put(g, s, tile(s, gl, f, r === 0 ? "#b88aa0" : "#5a4060"), (wt + c) * cell, (lr - 4 + r) * cell);
  }));
  if (!portrait) {
    const fp = Math.round((w * 0.44) / cell);
    for (let r = 0; r < 6; r += 1) far(fp, lr - 6 + r, r === 0 ? "^" : "|", r === 0 ? null : "#8a6a88", r === 0 ? "#b090a8" : "#6a4a68");
    for (let r = 0; r < 8; r += 1) far(fp + 2, lr - 8 + r, r % 3 === 0 ? "=" : "X", null, "#6a4a6a");
    for (let c = -2; c <= 4; c += 1) far(fp + c, lr, "=", "#4a3450", "#6a5070");
  }
  // A thin old moon, low in the east of the night side.
  const mc = Math.round((w * (portrait ? 0.8 : 0.07)) / cell);
  const mrw = Math.round((h * 0.12) / cell);
  // Painted on its own canvas so the shadowed part cuts only the moon.
  const mR = cell * 2.6;
  const mcv = document.createElement("canvas");
  mcv.width = mcv.height = Math.ceil(mR * 2.4 * s.dpr);
  const mg = mcv.getContext("2d");
  mg.scale(s.dpr, s.dpr);
  const mo = mR * 1.2;
  const lit = mg.createLinearGradient(mo + mR, mo - mR, mo - mR, mo + mR);
  lit.addColorStop(0, "#fbefe2");
  lit.addColorStop(1, "#d8c0bc");
  mg.fillStyle = lit;
  mg.beginPath();
  mg.arc(mo, mo, mR, 0, TAU);
  mg.fill();
  mg.globalCompositeOperation = "destination-out";
  mg.beginPath();
  mg.arc(mo - mR * 0.42, mo - mR * 0.36, mR * 0.92, 0, TAU);
  mg.fill();
  g.globalAlpha = 0.92;
  g.drawImage(mcv, (mc + 0.5) * cell - mo, (mrw + 0.5) * cell - mo, mo * 2, mo * 2);
  const moon = g.createRadialGradient((mc + 0.5) * cell, (mrw + 0.5) * cell, 0, (mc + 0.5) * cell, (mrw + 0.5) * cell, cell * 9);
  moon.addColorStop(0, "rgba(240,220,230,0.14)");
  moon.addColorStop(1, "rgba(240,220,230,0)");
  g.globalAlpha = 1;
  g.fillStyle = moon;
  g.fillRect((mc - 9) * cell, (mrw - 9) * cell, cell * 19, cell * 19);
  // The crawlerway: a pale gravel road from the hall out to the pad.
  const cx0 = (vabC + vabW / 2) * cell;
  const cy0 = landY + cell * 0.3;
  const cx1 = (rc - 4) * cell;
  const cy1 = (padRow + 2.6) * cell;
  const road = new Path2D();
  road.moveTo(cx0 - 2 * k, cy0);
  road.lineTo(cx0 + 6 * k, cy0);
  road.lineTo(cx1 + cell * 2, cy1 - cell * 0.3);
  road.lineTo(cx1 - cell * 1, cy1 + cell * 0.6);
  road.closePath();
  const rf = g.createLinearGradient(cx0, cy0, cx1, cy1);
  rf.addColorStop(0, "rgba(220,150,150,0.05)");
  rf.addColorStop(1, "rgba(200,150,170,0.12)");
  g.fillStyle = rf;
  g.fill(road);
  const steps = Math.round(Math.hypot(cx1 - cx0, cy1 - cy0) / (cell * 2.2));
  for (let i = 1; i < steps; i += 1) {
    const u = i / steps;
    const x = cx0 + (cx1 - cx0) * u;
    const y = cy0 + (cy1 - cy0) * u + cell * 0.9 * u;
    put(g, s, tile(s, u > 0.6 ? ":" : ".", null, "#c89aa8"), Math.round(x / cell) * cell, Math.round(y / cell) * cell, 0.25 + 0.4 * u);
  }
  g.globalAlpha = 1;
  // Marsh pools holding the sky.
  for (let i = 0; i < (portrait ? 2 : 5); i += 1) {
    const x = w * R.range(0.08, portrait ? 0.9 : 0.62);
    const y = landY + (padRow * cell - landY) * R.range(0.2, 0.95);
    const pw = R.range(40, 140) * k * (0.6 + (y - landY) / (padRow * cell - landY));
    const pool = g.createLinearGradient(0, y - 3 * k, 0, y + 3 * k);
    pool.addColorStop(0, "rgba(255,190,150,0.45)");
    pool.addColorStop(1, "rgba(200,110,130,0.25)");
    g.fillStyle = pool;
    g.beginPath();
    g.ellipse(x, y, pw, Math.max(1, 2.6 * k), 0, 0, TAU);
    g.fill();
    for (let j = 0; j < Math.round(pw / cell); j += 2) put(g, s, tile(s, "~", null, "#ffd0b0"), Math.round((x - pw * 0.6 + j * cell) / cell) * cell, Math.round(y / cell - 0.5) * cell, 0.35);
  }
  g.globalAlpha = 1;
  // Low scrub across the flats, darker and larger toward the viewer.
  const SCRUB = ["^", "m", "w", "n", "^", ","];
  for (let r = lr + 1; r < padRow + 6; r += 1) {
    const depth = (r - lr) / (padRow + 6 - lr);
    for (let c = 0; c < cols; c += 1) {
      const v = noise(c * 0.18 + 7, r * 0.5);
      if (v < 0.58 || R() > 0.5) continue;
      if (Math.abs(c - rc) < 13 && r >= padRow - 2) continue;
      const ink = mixc("#7a6070", "#3e4a3a", depth);
      put(g, s, tile(s, SCRUB[Math.floor(R() * SCRUB.length)], null, ink), c * cell, r * cell, 0.35 + 0.5 * depth);
    }
  }
  g.globalAlpha = 1;
  // Lightning masts around the pad, wires sagging between them.
  const mastH = RH + 7;
  const masts = [rc - 13, rc + 15].filter((c) => c > 1 && c < cols - 2);
  g.strokeStyle = "rgba(40,24,48,0.6)";
  g.lineWidth = Math.max(0.6, k);
  for (let i = 0; i < masts.length - 1; i += 1) {
    const x0 = (masts[i] + 0.5) * cell;
    const x1 = (masts[i + 1] + 0.5) * cell;
    const y = (padRow - mastH) * cell;
    g.beginPath();
    g.moveTo(x0, y);
    g.quadraticCurveTo((x0 + x1) / 2, y + cell * 6, x1, y);
    g.stroke();
  }
  for (const m of masts) for (let r = 0; r < mastH; r += 1) put(g, s, tile(s, r % 5 === 0 ? "+" : "|", null, "#2a1c30"), m * cell, (padRow - mastH + r) * cell);
  s.mastLights = masts.map((m) => ({ x: m * cell, y: (padRow - mastH - 1) * cell }));
  // The gantry: a lattice tower, see-through bracing, lit rim on the sun side.
  const gx = (s.gx = rc + 5);
  const gH = RH + 5;
  const gTop = padRow - gH;
  const gw = 4;
  for (let r = 0; r < gH; r += 1) {
    for (let c = 0; c < gw; c += 1) {
      const y = (gTop + r) * cell;
      const x = (gx + c) * cell;
      const floor = r % 4 === 0;
      if (c === 0) put(g, s, tile(s, "|", "#4a2c40", "#f0a080"), x, y);
      else if (c === gw - 1) put(g, s, tile(s, "|", "#20182a", "#4a3a58"), x, y);
      else if (floor) put(g, s, tile(s, "=", "#2a2034", "#7a5a70"), x, y);
      else put(g, s, tile(s, (r + c) % 2 ? "/" : "\\", null, "#241a2e"), x, y);
    }
  }
  // Hammerhead crane on top, and a lightning rod.
  for (let c = -3; c < gw + 1; c += 1) put(g, s, tile(s, "=", "#2a2034", c < 0 ? "#c88070" : "#6a5070"), (gx + c) * cell, (gTop - 1) * cell);
  put(g, s, tile(s, "|", null, "#2a2034"), (gx - 3) * cell, gTop * cell);
  put(g, s, tile(s, "|", null, "#2a2034"), (gx - 3) * cell, (gTop + 1) * cell);
  for (let r = 2; r < 6; r += 1) put(g, s, tile(s, "|", null, "#2a1c30"), (gx + 1) * cell, (gTop - r) * cell);
  s.gantryLight = { x: (gx + 1) * cell, y: (gTop - 6) * cell };
  s.gLights = [0.3, 0.6, 0.85].map((f) => ({ x: (gx + gw) * cell, y: (gTop + Math.round(gH * f)) * cell, ph: R() * TAU }));
  // Small huts and tanks by the pad, and a fence along the flats.
  const tanks = portrait ? [] : [rc - 10, rc + 12];
  for (const tc of tanks) {
    ["/=\\", "|O|", "|_|"].forEach((row, r) => Array.from(row).forEach((gl, c) => {
      const f = shade("#8a7088", c === 0 ? 1.2 : c === 2 ? 0.75 : 1);
      put(g, s, tile(s, gl, f, shade(f, 0.6)), (tc + c) * cell, (padRow - 3 + r) * cell);
    }));
  }
  g.globalAlpha = 1;
  s.back = back;

  // The pad: a concrete deck under the rocket with its flame trench.
  const [front, fg] = layer(s);
  const padL = rc - (portrait ? 8 : 11);
  const padR = rc + (portrait ? 12 : 14);
  for (let r = 0; r < 3; r += 1) {
    for (let c = padL - r * 2; c <= padR + r * 2; c += 1) {
      const trench = r > 0 && Math.abs(c - rc) <= 2;
      const lit = 1.15 - 0.4 * ((c - padL) / (padR - padL)) - r * 0.12;
      const f = shade("#7a6070", lit);
      put(fg, s, trench ? tile(s, "V", "#1a1018", "#3a2028") : tile(s, r === 0 ? "=" : c % 3 === 0 ? "#" : ":", f, shade(f, r === 0 ? 1.35 : 0.7)), c * cell, (padRow + r) * cell);
    }
  }
  // Fence posts along the flats in front of the pad.
  const fenceR = padRow + 5;
  for (let c = 0; c < cols; c += 1) {
    const post = c % 5 === 0;
    put(fg, s, tile(s, post ? "+" : "-", null, post ? "#3a2c40" : "#2e2436"), c * cell, fenceR * cell, post ? 0.9 : 0.6);
  }
  fg.globalAlpha = 1;
  s.front = front;

  // The rockets, each into its own sprite: a slim core, maybe two strap-on
  // boosters, lit warm on the sun side and cool in shadow. A new one rolls
  // out after every launch.
  const LIT = { "-2": [0.42, 1.04], "-1": [0.26, 1.0], 0: [0, 0.9], 1: [-0.3, 0.72], 2: [-0.42, 0.6] };
  const body = (dc, base = "#e2d4da") => {
    const [m, l] = LIT[Math.max(-2, Math.min(2, dc))];
    return shade(m >= 0 ? mixc(base, WARM, m) : mixc(base, COOL, -m), l);
  };
  const makeRocket = (V) => {
  const cells = [];
  const add = (dc, r, gl, fill, ink) => cells.push({ dc, r, id: tile(s, gl, fill, ink) });
  const fairing = Math.max(4, Math.round(RH * 0.15));
  const upper = Math.max(3, Math.round(RH * 0.17));
  const inter = fairing + upper + 1;
  const boosterTop = V.boosters ? Math.round(RH * 0.5) : RH + 1;
  const label = V.label;
  const labelAt = inter + 3;
  for (let r = 0; r < RH; r += 1) {
    if (r === 0) {
      add(0, r, "^", null, "#f6e6e6");
      continue;
    }
    for (let dc = -1; dc <= 1; dc += 1) {
      let f = body(dc, r > inter + 1 ? V.tank : "#e2d4da");
      let gl = r === 1 ? (dc < 0 ? "/" : dc > 0 ? "\\" : ":") : dc === 0 ? ":" : "|";
      let ink = shade(f, 0.7);
      if (r === fairing || r === inter) {
        gl = "=";
        f = shade(f, 0.7);
        ink = shade(f, 1.45);
      } else if (r === inter + 1) {
        f = body(dc, "#3a3040");
        gl = "#";
        ink = shade(f, 1.6);
      } else if (r === RH - 2) {
        f = body(dc, "#5a4a56");
        gl = "=";
        ink = shade(f, 1.6);
      } else if (r === RH - 1) {
        f = body(dc, "#6a5a62");
        gl = "V";
        ink = shade(f, 0.5);
      } else if (dc === 0 && r >= labelAt && r < labelAt + label.length) {
        gl = label[r - labelAt];
        ink = "#3a2a4a";
      } else if (V.roll && r > inter + 1 && dc === 1 && (r - inter) % 6 < 2) {
        f = body(dc, "#3a3242");
        ink = shade(f, 1.5);
      } else if (r === fairing + 2 && dc === 0) {
        gl = "o";
        ink = "#5a6a9a";
      }
      add(dc, r, gl, f, ink);
    }
    // Strap-on boosters with pointed noses, and fins.
    if (r >= boosterTop) {
      for (const side of [-1, 1]) {
        const dc = side * 2;
        let f = body(dc);
        let gl = "|";
        let ink = shade(f, 0.68);
        if (r === boosterTop) {
          gl = "^";
          f = null;
          ink = body(dc);
        } else if (r === boosterTop + 1 || r === RH - 2) {
          f = shade(f, 0.72);
          gl = "=";
          ink = shade(f, 1.45);
        } else if (r === RH - 1) {
          f = body(dc, "#6a5a62");
          gl = "V";
          ink = shade(f, 0.5);
        }
        add(dc, r, gl, f, ink);
        if (r >= RH - 3 && r < RH - 1) {
          const ff = body(side * 2, "#8a7a86");
          add(side * 3, r, side < 0 ? "/" : "\\", ff, shade(ff, 0.55));
        }
      }
    }
    // Without boosters, fins on the core.
    if (!V.boosters && r >= RH - 4 && r < RH - 1) {
      for (const side of [-1, 1]) {
        const ff = body(side * 2, "#8a7a86");
        add(side * 2, r, r === RH - 4 ? (side < 0 ? "/" : "\\") : "|", r === RH - 4 ? null : ff, r === RH - 4 ? ff : shade(ff, 0.55));
      }
    }
  }
  const rw = 7;
  const rs = document.createElement("canvas");
  rs.width = Math.round(rw * cell * s.dpr);
  rs.height = Math.round(RH * cell * s.dpr);
  const rg = rs.getContext("2d");
  rg.scale(s.dpr, s.dpr);
  for (const c of cells) put(rg, s, c.id, (c.dc + 3) * cell, c.r * cell);
  rg.globalAlpha = 1;
  const rocket = { c: rs, w: rw * cell, h: RH * cell, x: (rc - 3) * cell, y: (padRow - RH) * cell };
  // Access arms: a white room at the upper stage, two service arms below.
  const armRows = [fairing + 1, Math.round(RH * 0.38), Math.round(RH * 0.66)];
  rocket.arms = armRows.map((r, i) => {
    const edge = r >= boosterTop ? 3 : 2;
    const len = gx - (rc + edge);
    const ids = Array.from({ length: len }, (_, j) => (i === 0 && j === 0 ? tile(s, "[", "#c8b8c0", "#5a4a58") : i === 0 && j === 1 ? tile(s, "#", "#ffd8a0", "#8a5a20") : tile(s, "=", "#3a2c40", "#a08098")));
    return { y: (padRow - RH + r) * cell, x0: (rc + edge) * cell, len, ids };
  });

  // Vapour vents on the rocket's flanks.
  rocket.vents = [
    { r: inter - 1, side: 1, n: 7 },
    { r: inter - 1, side: -1, n: 5 },
    { r: boosterTop + 3, side: 1, n: 6 },
    { r: RH - 3, side: -1, n: 7 },
    { r: RH - 3, side: 1, n: 6 },
  ].map((v) => ({ ...v, x: (rc + 0.5 + v.side * (v.r >= boosterTop ? 2.6 : 1.6)) * cell, y: (padRow - RH + v.r) * cell, ph: R() * 10 }));
  return rocket;
  };
  const VARIANTS = [
    { boosters: true, label: "FUSE", tank: "#e2d4da", roll: true },
    { boosters: false, label: "ARC", tank: "#e8dcd0", roll: true },
    { boosters: true, label: "NOVA", tank: "#d8946a", roll: false },
  ];
  const v0 = Math.floor(R() * 3);
  s.rockets = [0, 1, 2].map((i) => makeRocket(VARIANTS[(v0 + i) % 3]));
  s.lightIds = [tile(s, "*", null, "#ff5a48"), tile(s, ".", null, "#8a3a38")];
  s.puffGlyph = ["o", "O", "~", ".", "o"].map((gl) => tile(s, gl, null, "#f6dce4"));
  s.flame = {
    hot: ["V", "W"].map((gl) => tile(s, gl, "#fff2c8", "#ff9a40")),
    core: ["|", "V", "!", "|"].map((gl) => tile(s, gl, null, "#fff6dc")),
    mid: ["*", "v", ":", "'"].map((gl) => tile(s, gl, null, "#ffcc70")),
    tail: ["'", ".", ",", ":"].map((gl) => tile(s, gl, null, "#ff8a50")),
  };
  // Soft sprites for vapour and glow.
  const sprite = (inner, outer) => {
    const c = document.createElement("canvas");
    c.width = c.height = 96;
    const sg = c.getContext("2d");
    const gr = sg.createRadialGradient(48, 48, 0, 48, 48, 48);
    gr.addColorStop(0, "rgba(255,255,255,1)");
    gr.addColorStop(inner, `rgba(255,255,255,${outer})`);
    gr.addColorStop(1, "rgba(255,255,255,0)");
    sg.fillStyle = gr;
    sg.fillRect(0, 0, 96, 96);
    return c;
  };
  s.puffBase = sprite(0.55, 0.55);
  s.glowBase = sprite(0.3, 0.3);
  s.tints = new Map();
  // Foreground scrub: palmetto fans and grass, swaying.
  const greens = ["#4a5a44", "#56664a", "#3e4c40", "#66724e"];
  const ART = [["\\ | /", " \\|/ ", "  |  "], ["\\|/", " | "], ["' , '", " \\|/ ", "  |"], ["v  v", " \\/ ", " || "]];
  const PALM = [["\\  |  /", " \\ | / ", "--\\|/--", " _\\|/_ ", "   |   "], ["  \\|/  ", "-\\\\|//-", " -\\|/- ", "   |   "], ["\\ \\|/ /", " \\\\|// ", "--\\|/--", "   |   "]];
  s.tufts = [];
  for (let c = -1; c < cols; c += 1) {
    const u = (c * cell) / w;
    const side = Math.abs(u - 0.5) * 2;
    if (R() > (portrait ? 0.4 : 0.22 + 0.5 * side)) continue;
    const big = side > 0.6 && R() < 0.45;
    const art = big ? PALM[Math.floor(R() * PALM.length)] : ART[Math.floor(R() * ART.length)];
    const base = h - cell * (big ? R.range(-0.5, 1.5) : R.range(0.2, 4.5) * (portrait ? 1 : 0.6 + side));
    const tc = [];
    const rim = R() < 0.4;
    art.forEach((line, r) => Array.from(line).forEach((ch, i) => {
      if (ch === " ") return;
      tc.push({ dc: i, r, lift: 1 - r / art.length, id: tile(s, ch, null, rim && r === 0 ? "#b07a70" : greens[Math.floor(R() * greens.length)]) });
    }));
    s.tufts.push({ x: c * cell, top: base - art.length * cell, cells: tc, ph: R() * TAU, sp: big ? 0.85 : 0.7 });
  }
  // Mid-distance scrub on the flats, static and small.
  s.beams = [
    { x: (rc - 12) * cell, y: padRow * cell, a0: -1.2, sw: 0.1, sp: 0.07, len: h * 0.8, wd: 0.05, a: 0.11 },
    { x: (rc + 16) * cell, y: padRow * cell, a0: -1.95, sw: 0.1, sp: 0.06, len: h * 0.8, wd: 0.05, a: 0.1 },
    { x: w * (portrait ? 0.05 : 0.12), y: landY, a0: -1.35, sw: 0.35, sp: 0.05, len: h * 1.1, wd: 0.035, a: 0.07 },
    { x: w * (portrait ? 0.95 : 0.6), y: landY, a0: -1.75, sw: 0.3, sp: 0.04, len: h * 1.1, wd: 0.035, a: 0.06 },
  ].map((b) => ({ ...b, ph: R() * TAU }));
  s.seaIds = ["~", "-", "~", "="].map((gl, i) => tile(s, gl, null, ["#ffd0a0", "#f0a090", "#ffe0b8", "#d88a90"][i]));
  s.seaR = Math.round(hy / cell);
  s.seaRows = Math.max(1, Math.round((landY - hy) / cell));
  s.birdT = [tile(s, "v", null, "#2a1a2a"), tile(s, "-", null, "#2a1a2a")];
  s.off = R() * 1000;
}

// A soft sprite tinted one colour, cached.
function tinted(s, base, col) {
  const key = (base === s.puffBase ? "p" : "g") + col;
  if (!s.tints.has(key)) {
    const c = document.createElement("canvas");
    c.width = c.height = 96;
    const g = c.getContext("2d");
    g.drawImage(base, 0, 0);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = col;
    g.fillRect(0, 0, 96, 96);
    s.tints.set(key, c);
  }
  return s.tints.get(key);
}

// Height climbed and sideways lean, tau seconds after liftoff.
const riseAt = (s, tau) => 0.5 * 0.0065 * s.h * tau * tau + tau * s.cell * 0.4;
const driftAt = (s, tau) => 0.00012 * s.w * Math.pow(tau, 2.2);

// Soft smoke is painted at a third of the resolution, then laid on whole.
const FXQ = 0.34;
function fxBegin(s) {
  if (!s.fx || s.fx.w !== s.w || s.fx.h !== s.h) {
    const c = document.createElement("canvas");
    c.width = Math.ceil(s.w * s.dpr * FXQ);
    c.height = Math.ceil(s.h * s.dpr * FXQ);
    s.fx = { c, g: c.getContext("2d"), w: s.w, h: s.h };
  }
  const g = s.fx.g;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";
  g.clearRect(0, 0, s.fx.c.width, s.fx.c.height);
  g.setTransform(s.dpr * FXQ, 0, 0, s.dpr * FXQ, 0, 0);
  s.fx.box = g.box = [Infinity, Infinity, -Infinity, -Infinity];
  return g;
}
function fxEnd(s, ctx) {
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  ctx.imageSmoothingEnabled = true;
  const q = s.dpr * FXQ;
  const [x0, y0, x1, y1] = s.fx.box.map((v, i) => (i < 2 ? Math.max(0, Math.floor(v * q)) : Math.min(i === 2 ? s.fx.c.width : s.fx.c.height, Math.ceil(v * q))));
  if (x1 <= x0 || y1 <= y0) return;
  ctx.drawImage(s.fx.c, x0, y0, x1 - x0, y1 - y0, x0 / q, y0 / q, (x1 - x0) / q, (y1 - y0) / q);
}

function blob(ctx, img, x, y, r, a) {
  if (a <= 0.004 || r <= 0) return;
  const b = ctx.box;
  if (b) {
    b[0] = Math.min(b[0], x - r);
    b[1] = Math.min(b[1], y - r);
    b[2] = Math.max(b[2], x + r);
    b[3] = Math.max(b[3], y + r);
  }
  ctx.globalAlpha = Math.min(1, a);
  ctx.drawImage(img, x - r, y - r, r * 2, r * 2);
}

export const launch = {
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
    const { ctx, w, h, cell, k, dpr } = s;
    const c = (t + 20) % PERIOD;
    const n = Math.floor((t + 20) / PERIOD);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(s.back, 0, 0, w, h);
    // Ripples of light on the sea under the sun.
    for (let r = 0; r < s.seaRows; r += 1) {
      for (let i = 0; i < 26; i += 1) {
        const x = s.sunX + (hash(i, r, 5) - 0.5) * w * (0.25 + r * 0.12);
        const tw = 0.5 + 0.5 * Math.sin(t * (1.2 + hash(i, r) * 1.5) + i * 2.1);
        const near = 1 - Math.min(1, Math.abs(x - s.sunX) / (w * 0.32));
        put(ctx, s, s.seaIds[(i + r) % 4], Math.round((x + Math.sin(t * 0.3 + i) * cell) / cell) * cell, (s.seaR + r) * cell, 0.75 * tw * near);
      }
    }
    let fx = null;
    // Ignition and climb.
    const ign = smooth((c - IGNITE) / 3);
    const tau = Math.max(0, c - LIFT);
    const R0 = s.rockets[n % 3];
    const R1 = s.rockets[(n + 1) % 3];
    const rise = riseAt(s, tau);
    const drift = driftAt(s, tau);
    const gone = rise > R0.y + R0.h + h * 0.1;
    const ready = c < IGNITE ? 1 : c > 83 ? smooth((c - 83) / 5) : 0;
    const flying = c >= IGNITE && !gone;
    const baseX = R0.x + R0.w / 2 + drift;
    const baseY = R0.y + R0.h - rise;
    // Searchlights sweeping up.
    ctx.globalCompositeOperation = "lighter";
    for (const b of s.beams) {
      const a = b.a0 + b.sw * Math.sin(t * b.sp * TAU * 0.25 + b.ph);
      const x1 = b.x + Math.cos(a - b.wd) * b.len;
      const y1 = b.y + Math.sin(a - b.wd) * b.len;
      const x2 = b.x + Math.cos(a + b.wd) * b.len;
      const y2 = b.y + Math.sin(a + b.wd) * b.len;
      const gr = ctx.createLinearGradient(b.x, b.y, b.x + Math.cos(a) * b.len, b.y + Math.sin(a) * b.len);
      gr.addColorStop(0, `rgba(255,236,214,${b.a})`);
      gr.addColorStop(1, "rgba(255,236,214,0)");
      ctx.globalAlpha = 1;
      ctx.fillStyle = gr;
      ctx.beginPath();
      ctx.moveTo(b.x, b.y);
      ctx.lineTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
    // The trail: puffs left along the climb. Up high they catch the sun
    // and the winds aloft shear them into a slow golden ribbon.
    if (c >= LIFT) {
      const fade = 1 - smooth((c - 79) / 10.5);
      fx = fxBegin(s);
      for (let tt = 0; tt < tau; tt += 0.22) {
        const age = tau - tt;
        const y0 = R0.y + R0.h - riseAt(s, tt) + cell;
        if (y0 < -cell * 12) break;
        const high = clamp01(1 - y0 / (s.hy * 0.9));
        const x = R0.x + R0.w / 2 + driftAt(s, tt) + age * k * (high * 14 - 3) + Math.sin(tt * 1.7 + n) * age * 0.8 * k;
        const y = y0 + age * 1.5 * k;
        const r = (cell * 0.9 + age * cell * 0.32) * (1 + high * 1.1);
        const a = fade * Math.max(0, 0.6 - age * 0.012) * (0.75 + 0.25 * noise(tt * 2.3, n));
        const col = high > 0.75 ? "#ffd6a0" : high > 0.5 ? "#f8bca0" : high > 0.25 ? "#e0a8b4" : "#b8a0b4";
        blob(fx, tinted(s, s.puffBase, high > 0.5 ? "#7a4a5a" : "#4a3050"), x + r * 0.2, y + r * 0.25, r, a * 0.4);
        blob(fx, tinted(s, s.puffBase, col), x, y, r, a);
        if (high > 0.55 && Math.floor(tt / 0.22) % 3 === 0) {
          fx.globalCompositeOperation = "lighter";
          blob(fx, tinted(s, s.glowBase, "#ff9a5a"), x, y, r * 1.6, a * 0.25 * high);
          fx.globalCompositeOperation = "source-over";
        }
      }
      fxEnd(s, ctx);
    }
    // Floodlights make a soft halo round the rocket on the pad.
    const halo = c < IGNITE ? 1 - ign : ready;
    if (halo > 0.01) {
      const Rh = c > 75 ? R1 : R0;
      ctx.globalCompositeOperation = "lighter";
      blob(ctx, tinted(s, s.glowBase, "#ffd8c8"), Rh.x + Rh.w / 2, Rh.y + Rh.h * 0.55, Rh.h * 0.5, 0.14 * halo);
      ctx.globalCompositeOperation = "source-over";
    }
    // Access arms: swing back before ignition, return with the new rocket.
    const armOut = c < ARMS_OUT ? 0 : c < IGNITE ? smooth((c - ARMS_OUT) / 3) : c > 85 ? 1 - smooth((c - 85) / 4) : 1;
    for (const a of (c > 75 ? R1 : R0).arms) {
      const show = Math.round(a.len * (1 - armOut));
      for (let j = a.len - show; j < a.len; j += 1) put(ctx, s, a.ids[j - (a.len - show)], a.x0 + j * cell, a.y);
    }
    // The rocket; after a launch the next one stands up out of the steam.
    if (flying || c < IGNITE) {
      ctx.globalAlpha = 1;
      ctx.drawImage(R0.c, Math.round((R0.x + drift) * dpr) / dpr, Math.round((R0.y - rise) * dpr) / dpr, R0.w, R0.h);
    }
    if (c > 83) {
      ctx.globalAlpha = ready;
      ctx.drawImage(R1.c, R1.x, R1.y, R1.w, R1.h);
    }
    ctx.globalAlpha = 1;
    ctx.drawImage(s.front, 0, 0, w, h);
    // Lights on the gantry and masts.
    const gl = s.gantryLight;
    put(ctx, s, s.lightIds[Math.sin(t * 2.2) > 0.5 ? 0 : 1], gl.x, gl.y);
    for (const m of s.mastLights) put(ctx, s, s.lightIds[Math.sin(t * 2.2 + 1.6) > 0.5 ? 0 : 1], m.x, m.y);
    for (const l of s.gLights) put(ctx, s, s.lightIds[Math.sin(t * 1.4 + l.ph) > 0.3 ? 0 : 1], l.x, l.y, 0.8);
    // Plume: glyph fire under the engines, and its glow on everything.
    if (flying && c >= IGNITE) {
      const len = Math.round(2 + 6 * ign + Math.min(12, tau * 1.6));
      const f = Math.floor(t * 12);
      const gx0 = Math.round((baseX - cell / 2) / cell) * cell;
      const floor = (s.padRow + 1) * cell;
      const fl = 0.85 + 0.15 * Math.sin(t * 23);
      ctx.globalCompositeOperation = "lighter";
      blob(ctx, tinted(s, s.glowBase, "#ffb060"), baseX, baseY + cell * len * 0.4, cell * (6 + len * 0.9), 0.7 * ign * fl);
      const near = clamp01(1 - rise / (h * 0.45));
      blob(ctx, tinted(s, s.glowBase, "#ff7a40"), R0.x + R0.w / 2, R0.y + R0.h, cell * 46, 0.4 * ign * near * fl);
      ctx.globalCompositeOperation = "source-over";
      for (let j = 0; j < len; j += 1) {
        const y = baseY + j * cell;
        if (y > floor) break;
        const lifeJ = j / len;
        const wd = Math.min(4, Math.floor(1 + j * 0.45));
        for (let i = -wd; i <= wd; i += 1) {
          const hsh = hash(f, j * 13 + i, 3);
          const edge = Math.abs(i) / (wd + 0.5);
          if (hsh < 0.1 + 0.55 * edge * edge + 0.35 * lifeJ) continue;
          const core = Math.abs(i) <= 1 && lifeJ < 0.4;
          const set = j === 0 && Math.abs(i) <= 1 ? s.flame.hot : core ? s.flame.core : lifeJ < 0.65 && edge < 0.7 ? s.flame.mid : s.flame.tail;
          put(ctx, s, set[Math.floor(hsh * 97) % set.length], gx0 + i * cell, y, ign * (1 - lifeJ * 0.55) * (core ? 1 : 0.85));
        }
      }
      // Fire spilling sideways along the deck while the rocket is low.
      if (baseY + len * cell > floor) {
        const spill = Math.round(clamp01((baseY + len * cell - floor) / (cell * 8)) * 8);
        for (let i = -spill; i <= spill; i += 1) {
          const hsh = hash(f, i, 8);
          if (hsh < 0.35 + (Math.abs(i) / (spill + 1)) * 0.4) continue;
          put(ctx, s, s.flame.tail[Math.floor(hsh * 31) % 4], gx0 + i * cell, floor - cell * (hsh > 0.8 ? 1 : 0), ign * 0.8);
        }
      }
    }
    // Ground steam: boils out across the pad at ignition, rolls and lingers,
    // and is gone by the time the next count begins.
    if (c >= IGNITE - 0.5) {
      const st = c - IGNITE + 0.5;
      const fade = smooth(st / 1.5) * (1 - smooth((c - 78) / 11.5));
      fx = fxBegin(s);
      const glyphs = [];
      const px = R0.x + R0.w / 2;
      const py = (s.padRow + 1) * cell;
      const reach = Math.min(w * 0.3, cell * 34);
      const lit = c < 86 && !gone ? clamp01(ign * (1 - rise / (h * 0.5))) : 0;
      for (let i = 0; i < 42; i += 1) {
        const dir = i % 2 ? 1 : -1;
        const hd = hash(i, n, 1);
        const d = hd * reach * (1 - Math.exp(-st / 3.2)) + st * 4 * k * (dir > 0 ? 0.3 : 1);
        const mound = 1 - 0.55 * hd;
        const x = px + dir * d - st * 2.5 * k;
        const climb = i % 3 === 0 ? st * cell * 0.22 * (1 - hd) : 0;
        const y = py - cell * hash(i, n, 2) * 5 * mound - climb - st * 0.1 * cell;
        const r = cell * (1.3 + st * 0.22) * (0.6 + 0.8 * hash(i, n, 3)) * (0.7 + 0.6 * mound);
        const warm = lit * (1 - hd * 0.7);
        blob(fx, tinted(s, s.puffBase, "#4a3450"), x + r * 0.15, y + r * 0.3, r, 0.45 * fade);
        blob(fx, tinted(s, s.puffBase, warm > 0.5 ? "#ffd2a8" : warm > 0.2 ? "#f2cfc4" : "#dcc4d4"), x, y, r, 0.62 * fade);
        if (i % 2 === 0) glyphs.push([s.puffGlyph[(i >> 1) % 5], Math.round(x / cell) * cell, Math.round(y / cell) * cell]);
      }
      fxEnd(s, ctx);
      for (const [id, x, y] of glyphs) put(ctx, s, id, x, y, 0.6 * fade);
    }
    // Venting vapour: cold puffs tumble down the flanks and drift off.
    const vent = c < IGNITE ? 1 : c > 85 ? ready : 0;
    if (vent > 0) {
      for (const v of (c > 75 ? R1 : R0).vents) {
        for (let i = 0; i < v.n; i += 1) {
          const life = (t / 5 + i / v.n + v.ph) % 1;
          const out = 1 - Math.exp(-life * 4);
          const x = v.x + v.side * out * cell * 2.4 - life * cell * 5 * (0.6 + 0.4 * noise(t * 0.2, v.ph));
          const y = v.y + life * life * cell * 7 + Math.sin(life * 6 + i) * cell * 0.3;
          const r = cell * (0.8 + life * 3.2);
          const a = vent * Math.sin(Math.PI * life) * 0.55;
          blob(ctx, tinted(s, s.puffBase, "#8a7090"), x + r * 0.2, y + r * 0.2, r, a * 0.4);
          blob(ctx, tinted(s, s.puffBase, v.side < 0 ? "#ffe6e0" : "#e0d0e6"), x, y, r, a);
          if (i % 2 === 0 && life > 0.15 && life < 0.8) put(ctx, s, s.puffGlyph[(i + Math.floor(life * 5)) % 5], Math.round(x / cell) * cell, Math.round(y / cell) * cell, a * 0.8);
        }
      }
    }
    ctx.globalAlpha = 1;
    // Pelicans cross low over the flats now and then.
    const bp = 43;
    const bn = Math.floor((t + s.off) / bp);
    const bq = ((t + s.off) % bp) / 24;
    if (bq < 1) {
      const dir = hash(bn, 1, 9) < 0.5 ? 1 : -1;
      const y = s.hy - h * (0.06 + hash(bn, 2, 9) * 0.12);
      const x = dir > 0 ? -60 + bq * (w + 120) : w + 60 - bq * (w + 120);
      for (let b = 0; b < 5; b += 1) put(ctx, s, s.birdT[Math.sin(t * 4 + b * 1.1) > 0 ? 0 : 1], x - dir * b * cell * 1.3, y + Math.abs(b - 2) * cell * 0.7 + Math.sin(t * 0.9 + b) * 2, 0.8);
    }
    // Scrub in the foreground, swaying in the sea breeze.
    const gustX = ((t * 95 + s.off) % (w + 1400)) - 700;
    for (const tf of s.tufts) {
      const bend = 2.2 * k * (0.5 * Math.sin(t * 0.9 + tf.x * 0.005) + 0.25 * Math.sin(t * 2.1 + tf.ph) + Math.exp(-(((tf.x - gustX) / 240) ** 2)));
      for (const cl of tf.cells) put(ctx, s, cl.id, tf.x + cl.dc * cell * tf.sp + bend * cl.lift, tf.top + cl.r * cell * 0.85);
    }
    ctx.globalAlpha = 1;
  },
};

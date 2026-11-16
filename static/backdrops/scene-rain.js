// Scene: Rain District. Street level in a narrow alley at night, in rain.
// The walls converge on a lit cross street at the far end: tile facades,
// fire escapes, AC units and windows drawn in tiles that shrink with depth,
// blade signs humming in teal, magenta and amber, red lanterns strung
// overhead. Slanted rain falls in glyphs, the wet ground holds the alley
// upside down, puddles ring with drops, steam leaks from a grate, one sign
// stutters, and now and then someone with an umbrella crosses the far end.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const FONT = '"Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace';
// Tile kits, near to far: one atlas per size so tiles shrink with depth.
const SIZES = [34, 26, 19, 14, 10, 7, 5];
const GF = 0.9; // ground floor height, in world units
const FL = 0.8; // upper floor height
const EYE = 0.62;
const TS = 0.07; // tile size per px-per-unit of depth
const HAZE = "#3c4a5e";
const WALL_L = ["#1a2b32", "#203238", "#18262e", "#242a34"];
const WALL_R = ["#2a1d2e", "#30222e", "#241b2b", "#2a2530"];
const CLAD = ["#2c6461", "#9a8f78", "#6a3838", "#3b6450", "#3a5674"];
const GLASS = ["#ffbf6a", "#ffd69a", "#ffb062", "#7fe0d0", "#ff8fb8"];
const NEON = [
  { c: "#ff4fa3", glow: "255,79,163" },
  { c: "#3fe6d6", glow: "63,230,214" },
  { c: "#ffb547", glow: "255,181,71" },
  { c: "#f2e6c8", glow: "242,230,200" },
];
const BLADES = ["BAR", "HOTEL", "RAMEN", "SAKE", "OPEN", "CAFE", "KARAOKE", "MAHJONG", "UDON", "TATTOO", "NOODLE", "JAZZ", "PAWN", "24H"];
const FASCIA = ["RAMEN", "TOBACCO", "DRUGS", "LAUNDRY", "SUSHI", "BOOKS", "TEA", "PHO", "GYOZA", "LIQUOR"];
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const frac = (v) => v - Math.floor(v);

// —— Multi-size tile atlases (same tile look as tiles.js) ——
function kit(s, cell) {
  const P = Math.max(3, Math.round(cell * s.dpr));
  const slots = Math.max(16, Math.min(48, Math.floor(1600 / P)));
  const c = document.createElement("canvas");
  c.width = slots * P;
  c.height = slots * P;
  const g = c.getContext("2d");
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = `700 ${Math.round(P * 0.74)}px ${FONT}`;
  return { c, g, P, cell: P / s.dpr, slots, map: new Map(), n: 0, gap: Math.max(1, Math.round(P * 0.07)) };
}
function tk(K, glyph, fill, ink) {
  const key = `${glyph}|${fill}|${ink}`;
  let id = K.map.get(key);
  if (id !== undefined) return id;
  id = K.n % (K.slots * K.slots);
  K.n += 1;
  const P = K.P;
  const x = (id % K.slots) * P;
  const y = Math.floor(id / K.slots) * P;
  K.g.clearRect(x, y, P, P);
  if (fill) {
    K.g.fillStyle = fill;
    K.g.fillRect(x + K.gap / 2, y + K.gap / 2, P - K.gap, P - K.gap);
  }
  if (glyph && glyph !== " " && ink) {
    K.g.fillStyle = ink;
    K.g.fillText(glyph, x + P / 2, y + P * 0.54);
  }
  K.map.set(key, id);
  return id;
}
function pk(ctx, K, id, x, y, a = 1) {
  if (a <= 0.01) return;
  const P = K.P;
  ctx.globalAlpha = a > 1 ? 1 : a;
  ctx.drawImage(K.c, (id % K.slots) * P, Math.floor(id / K.slots) * P, P, P, x, y, K.cell, K.cell);
}
function pick(s, px) {
  let best = s.kits[0];
  let bd = 1e9;
  for (const K of s.kits) {
    const d = Math.abs(Math.log(K.cell / Math.max(1, px)));
    if (d < bd) {
      bd = d;
      best = K;
    }
  }
  return best;
}
// A soft round glow, one per colour, drawn scaled.
function glowSprite(rgbStr) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, `rgba(${rgbStr},1)`);
  gr.addColorStop(0.35, `rgba(${rgbStr},0.35)`);
  gr.addColorStop(1, `rgba(${rgbStr},0)`);
  g.fillStyle = gr;
  g.fillRect(0, 0, 64, 64);
  return c;
}

// —— The alley: buildings along each wall ——
function street(R, G, side) {
  const out = [];
  let z = G.zE * 0.55;
  let n = 0;
  while (z < G.zEnd - 0.2) {
    let len = R.range(2.3, 4.6) * G.U;
    if (G.zEnd - (z + len) < 1.4 * G.U) len = G.zEnd - z;
    const shopRoll = n < 2 ? 0.3 + R() * 0.55 : R();
    const b = {
      id: side * 1000 + n,
      z0: z,
      z1: z + len,
      top: GF + FL * Math.round(R.range(4, 8.99)) + (R() < 0.4 ? 0.14 : 0),
      wall: shade(R.pick(side < 0 ? WALL_L : WALL_R), R.range(0.85, 1.15)),
      clad: R() < 0.45 ? shade(R.pick(CLAD), R.range(0.38, 0.5)) : null,
      cladLow: R() < 0.5,
      ww: R.range(0.4, 0.52),
      wh: R.range(0.36, 0.48),
      ws: R.range(0.66, 0.9),
      lit: R.range(0.3, 0.65),
      warm: R() < 0.75,
      fe: R() < 0.45,
      pipe: R() < 0.7 ? R.range(0.06, 0.12) : 0,
      ac: R.range(0.15, 0.5),
      shop: shopRoll < 0.15 ? "shutter" : shopRoll < 0.6 ? "shop" : shopRoll < 0.85 ? "noren" : "door",
      word: FASCIA[(n * 3 + (side > 0 ? 5 : 0) + Math.floor(R() * 2)) % FASCIA.length],
      fasciaC: R.pick(NEON).c,
      shopC: R.pick(["#ffc070", "#ffd9a0", "#9fe8dc", "#ffb27a"]),
    };
    b.wo = R.range(0.25, 0.5);
    const nw = Math.max(1, Math.floor((len - b.wo - 0.15) / b.ws) + 1);
    if (b.fe) {
      b.feM0 = Math.floor(R() * Math.max(1, nw - 1));
      b.feM1 = Math.min(nw - 1, b.feM0 + 1);
    }
    out.push(b);
    z += len;
    n += 1;
  }
  return out;
}

function fogAt(G, z) {
  return Math.round(Math.pow(clamp01((z - G.zE) / (G.zEnd - G.zE)), 0.85) * 0.72 * 12) / 12;
}

// What sits on a wall at depth z, height Y. dz/dY: one tile in world units.
function feature(G, b, z, Y, dz, dY, side) {
  const lz = z - b.z0;
  const len = b.z1 - b.z0;
  if (Y > b.top) return null;
  const amb = 0.62 + 0.45 * Math.exp(-Y / 1.4);
  const fog = fogAt(G, z);
  const dim = (c) => mixc(shade(c, amb), HAZE, fog);
  const glow = (c) => mixc(c, HAZE, fog * 0.55);
  if (Y > b.top - Math.max(0.07, dY)) return ["=", dim(shade(b.wall, 1.6)), dim(shade(b.wall, 2.6))];
  if (b.pipe && Math.abs(lz - b.pipe) < Math.max(0.03, dz * 0.5)) return [frac(Y / 0.6) < 0.1 ? "=" : "|", dim("#2c343a"), dim("#6a7780")];
  if (lz < Math.max(0.02, dz * 0.5) || len - lz < Math.max(0.02, dz * 0.5)) return ["|", dim(shade(b.wall, 0.7)), dim(shade(b.wall, 1.4))];
  if (Y < GF) {
    if (Y < Math.max(0.05, dY * 0.7)) return ["_", dim("#2a2e33"), dim("#55606a")];
    if (Y > 0.66 && Y < 0.84 && b.shop !== "door") return [" ", dim(shade(b.fasciaC, 0.32)), null, "fascia"];
    const inZ = lz > 0.18 && lz < len - 0.18;
    if (inZ && Y < 0.62) {
      if (b.shop === "shutter") return frac(Y / 0.1) < 0.5 ? ["-", dim("#2e353c"), dim("#4e5862")] : null;
      if (b.shop === "shop") {
        if (lz < 0.38) return [Y < 0.56 ? "|" : "=", dim("#141a1e"), dim("#3a4650")];
        if (Y < 0.14 || frac((lz - 0.38) / 0.7) < Math.max(0.05, dz / 0.7)) return ["|", dim("#1c2228"), dim("#4a5660")];
        const sh = Math.floor(Y / 0.12);
        const it = hash(Math.floor(lz / 0.12), sh, b.id);
        if (it < 0.18) return null;
        return [sh % 2 ? "_" : it < 0.4 ? "o" : it < 0.6 ? "8" : it < 0.8 ? "#" : "%", glow(shade(b.shopC, sh % 2 ? 0.45 : 0.68)), glow(shade(b.shopC, 0.3))];
      }
      if (b.shop === "noren" && frac((lz - 0.3) / 1.2) < 0.6) {
        if (Y > 0.36) return [frac(lz / 0.14) < 0.15 ? "|" : "~", dim("#283a6e"), dim("#8ea2e0")];
        return [":", glow(shade(b.shopC, 0.55)), glow(shade(b.shopC, 0.35))];
      }
    }
    if (b.shop === "door" && lz > 0.3 && lz < 0.62 && Y < 0.66) return [lz < 0.34 || lz > 0.58 ? "|" : ".", dim("#101418"), dim("#3a444c")];
    if (b.clad && b.cladLow) return cladTile(b, lz, Y, dz, dim);
    return null;
  }
  const fl = Math.floor((Y - GF) / FL);
  const ly = Y - GF - fl * FL;
  const m = Math.round((lz - b.wo) / b.ws);
  const zc = b.wo + m * b.ws;
  const hasWin = m >= 0 && zc + b.ww / 2 < len - 0.06;
  // Fire escape: platform, rail, balusters, ladder; it overlays the wall.
  if (b.fe) {
    const a0 = b.wo + b.feM0 * b.ws - b.ww * 0.85;
    const a1 = b.wo + b.feM1 * b.ws + b.ww * 0.85;
    if (lz > a0 && lz < a1 && fl < Math.round((b.top - GF) / FL) - 1) {
      const iron = dim(shade("#7c8e98", 1.1));
      if (ly < Math.max(0.05, dY)) return ["=", null, iron];
      if (Math.abs(ly - 0.36) < Math.max(0.025, dY * 0.5)) return ["-", null, iron];
      const u = (lz - a0) / (a1 - a0);
      if (dz < 0.12 && Math.abs(ly - u * FL) < dY * 0.55) return [side < 0 ? "/" : "\\", null, iron];
      if (ly < 0.36 && dz < 0.09 && frac((lz - a0) / 0.16) < dz / 0.16) return ["|", null, iron];
    }
  }
  if (hasWin) {
    const ux = Math.abs(lz - zc);
    const wy0 = 0.24;
    if (ux < b.ww / 2 && ly > wy0 && ly < wy0 + b.wh) {
      const wT = b.ww / dz;
      const hT = b.wh / dY;
      const frame = (wT >= 3 && ux > b.ww / 2 - dz) || (hT >= 3 && (ly < wy0 + dY || ly > wy0 + b.wh - dY));
      const hv = hash(b.id, fl, m);
      if (frame) return [ux > b.ww / 2 - dz ? "|" : "-", hv < b.lit ? dim(shade(b.wall, 0.6)) : null, dim(shade(b.wall, 2.2))];
      if (hv < b.lit) {
        const c = b.warm ? GLASS[Math.floor(hash(b.id, fl, m + 50) * 3)] : GLASS[3 + Math.floor(hash(b.id, fl, m + 50) * 2)];
        const kind = Math.floor(hash(m, fl, b.id + 9) * 4);
        const gl = ["#", "=", "%", "#"][kind];
        const fill = glow(shade(c, 0.85 + 0.15 * (ly - wy0) / b.wh));
        return [gl, fill, glow(shade(c, 0.5)), "win", fl, m, c];
      }
      return [".", null, dim("#5a6a78"), "win", fl, m, null];
    }
    if (ux < b.ww / 2 + dz * 0.4 && ly > wy0 - Math.max(0.04, dY) && ly <= wy0) return ["_", null, dim("#8a949a")];
    // An AC unit under some windows.
    if (hash(b.id, fl, m + 7) < b.ac && ly > 0.03 && ly < 0.2) {
      const az = zc + b.ww * 0.15 * (hash(b.id, m, fl) < 0.5 ? -1 : 1);
      if (Math.abs(lz - az) < 0.17) return [Math.abs(lz - az) < Math.max(0.05, dz * 0.6) ? "@" : "%", dim("#7e868c"), dim("#3e464c"), "ac"];
    }
  }
  if (b.clad && Y < GF + FL * 2) return cladTile(b, lz, Y, dz, dim);
  return null;
}

function cladTile(b, lz, Y, dz, dim) {
  // Glazed tiles: a two-tone checker in world space reads in perspective.
  if (dz > 0.12) return ["+", dim(b.clad), dim(shade(b.clad, 1.35))];
  const odd = (Math.floor(lz / 0.15) + Math.floor(Y / 0.15)) % 2;
  if (hash(Math.floor(lz / 0.15), Math.floor(Y / 0.15), b.id) < 0.06) return null;
  return [odd ? "+" : ".", dim(shade(b.clad, odd ? 1.08 : 0.94)), dim(shade(b.clad, 1.3))];
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  const { w, h, dpr } = s;
  s.kits = SIZES.map((c) => kit(s, Math.max(3, c * k)));
  const kits = s.kits;
  s.fontStale = false;
  const fonts = typeof document !== "undefined" && document.fonts;
  if (fonts && fonts.status !== "loaded") fonts.ready.then(() => { if (s.kits === kits) s.fontStale = true; });
  s.cell = Math.max(4, 13 * k);
  // Geometry: one-point perspective onto the far end of the alley.
  const VPx = Math.round(w * 0.5);
  const VPy = Math.round(h * 0.42);
  const W = Math.max(1.3, w / 2 / (0.32 * h));
  const qE = w / 2 / W;
  const F = 1.6 * W * qE;
  const zE = F / qE;
  const zEnd = 6.5 * zE;
  const zFar = zEnd * 1.25;
  const G = (s.G = { VPx, VPy, W, qE, F, zE, zEnd, zFar, U: Math.max(0.6, W / 2.5), qEnd: F / zEnd, qF: F / zFar });
  const yAt = (q, Y) => VPy - q * (Y - EYE);
  const xL = VPx - W * G.qEnd;
  const xR = VPx + W * G.qEnd;
  G.xL = xL;
  G.xR = xR;

  // —— Back: sky strip, far facade, cross street, wet ground ——
  const [back, g] = layer(s);
  const sky = g.createLinearGradient(0, 0, 0, VPy + h * 0.1);
  sky.addColorStop(0, "#070a14");
  sky.addColorStop(0.6, "#141c30");
  sky.addColorStop(1, "#3a3048");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // City glow low in the sky, magenta to amber.
  const cg = g.createRadialGradient(VPx, VPy * 0.75, 0, VPx, VPy * 0.75, Math.max(w, h) * 0.45);
  cg.addColorStop(0, "rgba(255,120,150,0.28)");
  cg.addColorStop(0.5, "rgba(120,80,150,0.12)");
  cg.addColorStop(1, "rgba(60,40,90,0)");
  g.fillStyle = cg;
  g.fillRect(0, 0, w, h);
  // Low cloud lit from below.
  g.save();
  g.filter = `blur(${Math.max(2, 18 * k)}px)`;
  for (let i = 0; i < 10; i += 1) {
    g.fillStyle = `rgba(${R() < 0.5 ? "120,90,140" : "70,100,130"},${R.range(0.15, 0.3)})`;
    g.beginPath();
    g.ellipse(VPx + R.range(-0.4, 0.4) * w, R.range(0.02, 0.3) * h, R.range(80, 240) * k, R.range(16, 40) * k, 0, 0, TAU);
    g.fill();
  }
  g.restore();
  // Distant towers in the rain haze above the far facade.
  const qF = G.qF;
  for (let i = 0; i < 5; i += 1) {
    const tx = VPx + R.range(-1.6, 1.6) * W * G.qEnd;
    const tw = R.range(0.6, 1.4) * W * G.qEnd * 0.35;
    const ty = yAt(qF, GF + FL * 4) - R.range(0.2, 1) * h * 0.18;
    g.fillStyle = mixc("#2a2c44", "#5a4a66", R() * 0.5);
    g.globalAlpha = R.range(0.45, 0.75);
    g.fillRect(tx - tw / 2, ty, tw, h);
    g.globalAlpha = 1;
    for (let j = 0; j < 14; j += 1) {
      g.fillStyle = `rgba(255,${R.range(170, 220) | 0},140,${R.range(0.2, 0.6)})`;
      g.fillRect(tx - tw / 2 + R() * tw, ty + R() * h * 0.2, 1.5 * k, 1.5 * k);
    }
    if (i === 0) {
      g.fillStyle = "rgba(255,70,60,0.9)";
      g.fillRect(tx - k, ty - 3 * k, 2.4 * k, 2.4 * k);
    }
  }
  // Far facade across the cross street.
  const farTop = GF + FL * 4;
  const yFT = yAt(qF, farTop);
  const yFG = yAt(qF, 0);
  const fw0 = VPx - W * G.qEnd * 1.6;
  const fw1 = VPx + W * G.qEnd * 1.6;
  const ff = g.createLinearGradient(0, yFT, 0, yFG);
  ff.addColorStop(0, "#3a3446");
  ff.addColorStop(1, "#5a4448");
  g.fillStyle = ff;
  g.fillRect(fw0, yFT, fw1 - fw0, yFG - yFT);
  const KF = pick(s, qF * TS);
  const fc = KF.cell;
  const farWord = R.pick(["KISSATEN", "HOTEL", "NOODLES", "ARCADE", "CINEMA"]);
  for (let y = Math.floor(yFT / fc) * fc; y < yFG; y += fc) {
    for (let x = Math.floor(fw0 / fc) * fc; x < fw1; x += fc) {
      const X = (x + fc / 2 - VPx) / qF;
      const Y = EYE + (VPy - y - fc / 2) / qF;
      if (Y > farTop || Y < 0) continue;
      let t0 = null;
      if (Y < 0.62) {
        if (X > -1.5 && X < 1.1) t0 = [["#", "o", "=", "#"][Math.floor(Y / 0.15) % 4], "#ffc27a", "#a8642a"];
        else if (X > 1.25 && X < 1.6) t0 = ["|", "#1a1a22", "#4a4450"];
      } else if (Y < 0.84) {
        if (X > -1.5 && X < 1.1) t0 = [" ", "#f0e2c0", null];
      } else if (Y > GF) {
        const ly = frac((Y - GF) / FL) * FL;
        const m = Math.round(X / 0.62);
        if (Math.abs(X - m * 0.62) < 0.16 && ly > 0.24 && ly < 0.66) {
          const lit = hash(m, Math.floor((Y - GF) / FL), 77) < 0.55;
          t0 = lit ? ["#", "#ffcf8a", "#9a6a30"] : [".", "#2a2836", "#4a4658"];
        }
      }
      if (t0) pk(g, KF, tk(KF, t0[0], mixc(t0[1], "#6a5a6a", 0.25), t0[2] && mixc(t0[2], "#6a5a6a", 0.2)), x, y);
    }
  }
  // Its sign letters on the lightbox.
  const lyS = Math.floor(yAt(qF, 0.75) / fc) * fc;
  const lx0 = Math.round((VPx - 0.2 * qF - (farWord.length * fc) / 2) / fc) * fc;
  Array.from(farWord).forEach((ch, i) => pk(g, KF, tk(KF, ch, mixc("#f0e2c0", "#6a5a6a", 0.25), "#9a3a3a"), lx0 + i * fc, lyS));
  g.globalAlpha = 1;
  // Wet ground, brighter toward the lit far end.
  const yGE = yAt(G.qEnd, 0);
  const gr = g.createLinearGradient(0, yFG, 0, h);
  gr.addColorStop(0, "#5a4a52");
  gr.addColorStop(0.06, "#3a3440");
  gr.addColorStop(0.3, "#181c26");
  gr.addColorStop(1, "#0a0d14");
  g.fillStyle = gr;
  const ground = new Path2D();
  ground.moveTo(0, yAt(qE, 0));
  ground.lineTo(xL, yGE);
  ground.lineTo(xL, yFG);
  ground.lineTo(xR, yFG);
  ground.lineTo(xR, yGE);
  ground.lineTo(w, yAt(qE, 0));
  ground.lineTo(w, h);
  ground.lineTo(0, h);
  ground.closePath();
  g.fill(ground);
  s.ground = ground;
  dotGrid(g, s, "#8fa6c8", 0.16);
  s.back = back;

  // —— The walls, sampled tile by tile in perspective ——
  const [walls, wg] = layer(s);
  s.L = street(R, G, -1);
  s.Rb = street(R, G, 1);
  const live = new Map();
  const shopLights = [];
  for (const side of [-1, 1]) {
    const list = side < 0 ? s.L : s.Rb;
    // Smooth wall planes first.
    for (const b of list) {
      const z0 = Math.max(b.z0, zE * 0.55);
      const q0 = F / z0;
      const q1 = F / b.z1;
      const x0 = VPx + side * W * q0;
      const x1 = VPx + side * W * q1;
      const p = new Path2D();
      p.moveTo(x0, yAt(q0, b.top));
      p.lineTo(x1, yAt(q1, b.top));
      p.lineTo(x1, yAt(q1, 0));
      p.lineTo(x0, yAt(q0, 0));
      p.closePath();
      const lg = wg.createLinearGradient(x0, 0, x1, 0);
      lg.addColorStop(0, mixc(b.wall, HAZE, fogAt(G, z0)));
      lg.addColorStop(1, mixc(b.wall, HAZE, fogAt(G, b.z1)));
      wg.fillStyle = lg;
      wg.fill(p);
      if (b.shop !== "shutter" && b.shop !== "door") shopLights.push({ side, z: (b.z0 + b.z1) / 2, c: b.shopC, b });
    }
    // Darker toward the roofs.
    wg.save();
    wg.globalCompositeOperation = "source-atop";
    const vg = wg.createLinearGradient(0, 0, 0, VPy + qE * EYE);
    vg.addColorStop(0, "rgba(4,6,12,0.55)");
    vg.addColorStop(0.7, "rgba(4,6,12,0.1)");
    vg.addColorStop(1, "rgba(4,6,12,0)");
    wg.fillStyle = vg;
    wg.fillRect(0, 0, w, h);
    wg.restore();
    // Then the tiles.
    let x = side < 0 ? 0 : w;
    for (let guard = 0; guard < 2000; guard += 1) {
      const qx = side < 0 ? (VPx - x) / W : (x - VPx) / W;
      const K = pick(s, qx * TS);
      const c = K.cell;
      const xc = x + (side < 0 ? c / 2 : -c / 2);
      const q = Math.abs(xc - VPx) / W;
      const z = F / q;
      if (z > zEnd) break;
      const b = list.find((bb) => z >= bb.z0 && z < bb.z1);
      const tx = side < 0 ? x : x - c;
      if (b) {
        const dz = (c * z * z) / (F * W);
        const dY = c / q;
        const yT = Math.max(0, yAt(q, b.top));
        const yG = Math.min(h, yAt(q, 0));
        for (let r = Math.floor(yT / c); r * c < yG; r += 1) {
          const yc = r * c + c / 2;
          if (yc < yAt(q, b.top) || yc > yAt(q, 0)) continue;
          const Y = EYE + (VPy - yc) / q;
          const f = feature(G, b, z, Y, dz, dY, side);
          if (!f) continue;
          const id = tk(K, f[0], f[1], f[2]);
          pk(wg, K, id, tx, r * c);
          if (f[3] === "win" && hash(b.id, f[4], f[5] + 31) < 0.22) {
            const key = `${b.id}:${f[4]}:${f[5]}`;
            if (!live.has(key)) {
              const lc = f[6] || GLASS[Math.floor(hash(b.id, f[4], f[5] + 3) * GLASS.length)];
              live.set(key, { tiles: [], tv: hash(b.id, f[5], f[4] + 5) < 0.3, lc, fog: fogAt(G, z), p: 20 + hash(b.id, f[4], f[5] + 8) * 60, ph: hash(f[5], f[4], b.id), duty: 0.25 + 0.3 * hash(f[4], b.id, f[5]) });
            }
            const L = live.get(key);
            const alt = f[6] ? tk(K, ".", mixc("#0e1418", HAZE, L.fog), mixc("#2e3a44", HAZE, L.fog)) : tk(K, "#", mixc(L.lc, HAZE, L.fog * 0.55), mixc(shade(L.lc, 0.5), HAZE, L.fog * 0.55));
            const tv = tk(K, "#", mixc("#8fd8ff", HAZE, L.fog * 0.5), mixc("#3a6a9a", HAZE, L.fog * 0.5));
            L.tiles.push({ K, x: tx, y: r * c, alt, tv, lit: !!f[6] });
          }
        }
      }
      x += side < 0 ? c : -c;
    }
    // Fascia lettering: one tile per letter along the sloping fascia line.
    for (const b of list) {
      if (b.shop === "door") continue;
      const zs = Math.max(b.z0 + 0.25, zE * 1.02);
      if (zs > b.z1 - 0.3) continue;
      let xx = VPx + side * W * (F / zs);
      const spots = [];
      while (spots.length < b.word.length) {
        const q = Math.abs(xx - VPx) / W;
        const z = F / q;
        if (z > b.z1 - 0.2 || z > zEnd) break;
        const K = pick(s, q * TS);
        spots.push({ K, z, x: side < 0 ? xx : xx - K.cell, y: yAt(q, 0.75) - K.cell / 2 });
        xx += side * -K.cell;
      }
      // Read left to right on both walls.
      if (spots.length < b.word.length) continue;
      if (side > 0) spots.reverse();
      spots.forEach((sp, i) => {
        const fog = fogAt(G, sp.z);
        pk(wg, sp.K, tk(sp.K, b.word[i], mixc(shade(b.fasciaC, 0.32), HAZE, fog), mixc(b.fasciaC, HAZE, fog * 0.5)), sp.x, sp.y);
      });
    }
  }
  wg.globalAlpha = 1;
  s.live = [...live.values()].filter((L) => L.tiles.length);
  // Gutters and a steaming grate on the ground.
  const gx = -0.55 * W;
  const gz = zE * 1.28;
  s.grate = { x: VPx + gx * (F / gz), y: yAt(F / gz, 0), q: F / gz };
  for (let y = Math.floor(VPy + G.qEnd * EYE); y < h; ) {
    const q = (y - VPy) / EYE;
    const K = pick(s, q * TS);
    const c = K.cell;
    const yy = Math.floor(y / c) * c;
    const qc = (yy + c / 2 - VPy) / EYE;
    const z = F / qc;
    for (let x = Math.floor((VPx - W * qc) / c) * c; x < VPx + W * qc; x += c) {
      const X = (x + c / 2 - VPx) / qc;
      let f = null;
      if (Math.abs(X) > W - 0.1 && Math.abs(X) < W) f = ["=", "#20262c", "#4a5560"];
      else if (Math.abs(X - gx) < 0.2 && Math.abs(z - gz) < 0.3) f = ["#", "#1a2026", "#46525c"];
      if (f) pk(wg, K, tk(K, f[0], mixc(f[1], HAZE, fogAt(G, z)), mixc(f[2], HAZE, fogAt(G, z))), x, yy);
    }
    y = yy + c * 1.001;
  }
  wg.globalAlpha = 1;
  // Blade signs off the walls, facing down the alley.
  s.signs = [];
  const sd = [1.12, 1.7, 2.5, 3.5];
  const words = BLADES.map((wd) => [R(), wd]).sort((a, b) => a[0] - b[0]).map((p) => p[1]);
  for (const side of [-1, 1]) {
    for (let i = 0; i < sd.length; i += 1) {
      const d = sd[i] * R.range(0.95, 1.08) + (side > 0 ? 0.25 : 0);
      const z = zE * d;
      if (z > zEnd * 0.92) continue;
      const q = F / z;
      const K = pick(s, q * 0.2);
      const c = K.cell;
      const word = words[s.signs.length % words.length];
      const n = NEON[(i * 2 + (side > 0 ? 1 : 0) + Math.floor(R() * 2)) % NEON.length];
      const box = n.c === "#f2e6c8" || R() < 0.25;
      const Yt = GF + FL * R.range(0.6, 2.2);
      const xw = VPx + side * W * q;
      const x = Math.round(side < 0 ? xw + q * 0.12 : xw - q * 0.12 - c);
      const y0 = Math.round(yAt(q, Yt));
      const fog = fogAt(G, z);
      const on = Array.from(word).map((ch) => (box ? tk(K, ch, mixc(n.c, HAZE, fog * 0.4), "#2a1420") : tk(K, ch, mixc(shade(n.c, 0.22), HAZE, fog * 0.5), mixc(n.c, HAZE, fog * 0.3))));
      const off = Array.from(word).map((ch) => tk(K, ch, mixc(shade(n.c, 0.18), HAZE, fog * 0.5), mixc(shade(n.c, 0.4), HAZE, fog * 0.5)));
      const cap = tk(K, "=", mixc("#2a3038", HAZE, fog), mixc("#6a7680", HAZE, fog));
      s.signs.push({ K, x, y0, word, n, on, off, cap, side, q, z, flick: false, ph: R() * 100 });
    }
  }
  s.signs.sort((a, b) => b.z - a.z);
  // The nearest sign on one side is the one that stutters.
  const near = s.signs.filter((sg) => sg.z < zE * 1.9);
  if (near.length) near[Math.floor(R() * near.length)].flick = true;
  s.glows = new Map();
  s.glow = (rgbStr) => {
    if (!s.glows.has(rgbStr)) s.glows.set(rgbStr, glowSprite(rgbStr));
    return s.glows.get(rgbStr);
  };
  // Light: shop light pools, sign halos, the vending machine.
  wg.save();
  wg.globalCompositeOperation = "lighter";
  for (const L of shopLights) {
    const q = F / L.z;
    const x = VPx + L.side * W * q;
    const y = yAt(q, 0.3);
    const r = q * 0.9;
    wg.globalAlpha = 0.22 * (1 - fogAt(G, L.z) * 0.6);
    wg.drawImage(s.glow(hexRgb(L.c)), x - r, y - r, r * 2, r * 2);
  }
  for (const sg of s.signs) {
    if (sg.flick) continue;
    drawSign(wg, s, sg, 1, true);
  }
  wg.restore();
  for (const sg of s.signs) if (!sg.flick) drawSign(wg, s, sg, 1, false);
  wg.globalAlpha = 1;
  // A vending machine against the right wall, glowing.
  const vz = zE * 1.18;
  const vq = F / vz;
  const VK = pick(s, vq * TS);
  const vc = VK.cell;
  const vx = Math.round((VPx + W * vq - vq * 0.42) / vc) * vc;
  const vRows = Math.round((0.78 * vq) / vc);
  const vCols = Math.max(2, Math.round((0.36 * vq) / vc));
  const vy = Math.round(yAt(vq, 0) / vc) * vc - vRows * vc;
  wg.save();
  wg.globalCompositeOperation = "lighter";
  wg.globalAlpha = 0.5;
  const vr = vq * 0.9;
  wg.drawImage(s.glow("150,230,255"), vx + (vCols * vc) / 2 - vr, vy + (vRows * vc) / 2 - vr, vr * 2, vr * 2);
  wg.restore();
  const cans = ["#ff5a5a", "#ffd24a", "#5ad1ff", "#7dff8a", "#ff9ad8", "#ffffff"];
  for (let r = 0; r < vRows; r += 1) {
    for (let c = 0; c < vCols; c += 1) {
      let t0;
      if (r === 0) t0 = ["=", "#e04a5a", "#ffd0d6"];
      else if (r < vRows * 0.62) t0 = r % 2 ? ["o", "#d8f4ff", cans[Math.floor(hash(r, c, 5) * cans.length)]] : ["_", "#bfe6f6", "#6a9ab0"];
      else if (r === vRows - 2) t0 = ["[", "#1a2228", "#5a6a74"];
      else t0 = [c === vCols - 1 ? ":" : "|", "#c0d0da", "#5a6a78"];
      pk(wg, VK, tk(VK, t0[0], t0[1], t0[2]), vx + c * vc, vy + r * vc);
    }
  }
  wg.globalAlpha = 1;
  s.vend = { x: vx + (vCols * vc) / 2, y: vy + (vRows * vc) / 2, r: vr };
  s.walls = walls;

  // Lantern strings and wires overhead.
  s.strings = [];
  const ld = [1.22, 1.85, 2.75, 3.9];
  ld.forEach((d, i) => {
    const z = zE * d * R.range(0.96, 1.05);
    const q = F / z;
    const Y = GF + FL * R.range(1.4, 2.1) + (i === 0 ? FL * 0.6 : 0);
    const lanterns = i !== 2 || R() < 0.5;
    const K = pick(s, q * 0.11);
    const n = Math.max(3, Math.round((2 * W) / 0.55));
    const fog = fogAt(G, z);
    const items = [];
    if (lanterns) {
      for (let j = 1; j < n; j += 1) {
        const red = (j + i) % 3 !== 0;
        const body = red ? "#ff5a3c" : "#ffb23c";
        items.push({
          u: j / n,
          drop: R.range(0.1, 0.25),
          ids: [
            tk(K, "=", mixc("#3a1a14", HAZE, fog), mixc("#a0503a", HAZE, fog)),
            tk(K, "O", mixc(body, HAZE, fog * 0.4), mixc(shade(body, 0.45), HAZE, fog * 0.4)),
            ...(K.cell >= 12 * k ? [tk(K, "#", mixc(shade(body, 0.9), HAZE, fog * 0.4), mixc(shade(body, 0.45), HAZE, fog * 0.4))] : []),
            tk(K, "'", null, mixc(body, HAZE, fog * 0.4)),
          ],
          glow: red ? "255,90,60" : "255,178,60",
          ph: R() * TAU,
        });
      }
    }
    s.strings.push({ q, Y, sag: R.range(0.15, 0.3), K, items, fog, ph: R() * TAU });
  });

  // —— The wet ground holds the alley upside down ——
  const [up, ug] = layer(s);
  ug.drawImage(back, 0, 0, w, h);
  ug.drawImage(walls, 0, 0, w, h);
  drawStrings(ug, s, 0, true);
  const [mir, mg] = layer(s);
  const regions = [
    { x0: 0, x1: xL, a: VPy + (EYE * VPx) / W, b: -EYE / W },
    { x0: xR, x1: w, a: VPy - (EYE * VPx) / W, b: EYE / W },
    { x0: xL, x1: xR, a: yFG, b: 0 },
  ];
  for (const rg of regions) {
    mg.save();
    mg.beginPath();
    mg.rect(rg.x0, 0, rg.x1 - rg.x0, h);
    mg.clip();
    mg.setTransform(dpr, 2 * rg.b * dpr, 0, -dpr, 0, 2 * rg.a * dpr);
    mg.drawImage(up, 0, 0, w, h);
    mg.restore();
  }
  mg.save();
  mg.globalCompositeOperation = "destination-in";
  mg.fill(ground);
  mg.restore();
  // A faint smeared sheen everywhere on the ground.
  g.save();
  g.clip(ground);
  // Smeared downward, as wet asphalt does: squash, then stretch back.
  const sm = document.createElement("canvas");
  sm.width = Math.max(1, Math.round(w / 2));
  sm.height = Math.max(1, Math.round(h / 10));
  const smg = sm.getContext("2d");
  smg.imageSmoothingEnabled = true;
  smg.drawImage(mir, 0, 0, sm.width, sm.height);
  g.imageSmoothingEnabled = true;
  g.globalAlpha = 0.3;
  g.drawImage(sm, 0, 0, w, h);
  g.globalAlpha = 0.1;
  g.drawImage(mir, 0, 0, w, h);
  g.globalAlpha = 1;
  // Warm spill from the cross street, the vending machine and shops.
  g.globalCompositeOperation = "lighter";
  const sp = g.createRadialGradient(VPx, yGE, 0, VPx, yGE, W * G.qEnd * 3);
  sp.addColorStop(0, "rgba(255,170,100,0.3)");
  sp.addColorStop(1, "rgba(255,170,100,0)");
  g.fillStyle = sp;
  g.fillRect(0, 0, w, h);
  g.globalAlpha = 0.35;
  g.drawImage(s.glow("150,230,255"), s.vend.x - vr * 1.6, yAt(vq, 0) - vr * 0.5, vr * 3.2, vr);
  g.restore();
  // Puddles: mirror-sharp patches that ripple.
  s.puddles = [];
  const np = Math.round(6 + 3 * Math.min(1, w / 1440));
  for (let i = 0; i < np; i += 1) {
    const z = zE * R.range(0.42, 2.6);
    const q = F / z;
    const X = R.range(-0.85, 0.85) * W;
    const rx = R.range(0.25, 0.6) * W * (0.6 + 0.4 * Math.abs(X / W));
    const cx = VPx + X * q;
    const cy = yAt(q, 0);
    if (cy > h + 10) continue;
    s.puddles.push({ cx, cy, rx: rx * q, ry: (rx * q * EYE) / z * R.range(0.5, 0.9), q, z, n: 3 + Math.floor(R() * 3), seed: R() * 1000 });
  }
  const [pud, pg] = layer(s);
  pg.fillStyle = "rgba(6,8,14,0.6)";
  for (const p of s.puddles) {
    pg.beginPath();
    pg.ellipse(p.cx, p.cy, p.rx, p.ry, 0, 0, TAU);
    pg.fill();
  }
  pg.globalCompositeOperation = "source-atop";
  pg.drawImage(mir, 0, 0, w, h);
  pg.globalCompositeOperation = "destination-in";
  pg.filter = `blur(${Math.max(1, 4 * k)}px)`;
  pg.fillStyle = "#fff";
  for (const p of s.puddles) {
    pg.beginPath();
    pg.ellipse(p.cx, p.cy, p.rx * 0.92, p.ry * 0.85, 0, 0, TAU);
    pg.fill();
  }
  pg.filter = "none";
  pg.globalCompositeOperation = "source-over";
  s.pud = pud;
  s.pudStrips = [];
  const band = Math.max(1, Math.round(3 * k * dpr)) / dpr;
  for (const p of s.puddles) {
    for (let y = Math.floor(p.cy - p.ry); y < Math.min(h, p.cy + p.ry); y += band) {
      const u = (y + band / 2 - p.cy) / p.ry;
      const hw = p.rx * Math.sqrt(Math.max(0, 1 - u * u)) + 6 * k;
      s.pudStrips.push({ y, x0: Math.max(0, p.cx - hw), x1: Math.min(w, p.cx + hw), band, amp: (1.5 + 4 * (p.q / qE)) * k });
    }
  }
  s.ripK = s.puddles.map((p) => pick(s, p.q * 0.045));
  s.ripIds = s.ripK.map((K) => ({ ring: ["(", ")", "-", "~", "."].map((gl) => tk(K, gl, null, "#d6f0f6")), dot: tk(K, "o", null, "#eef8fb") }));

  // Rain: three depths of slanted glyph streaks.
  const area = Math.min(1.6, (w * h) / 1.3e6);
  s.rain = [
    { K: pick(s, 7 * k), n: 260, v: 260, ink: "#9ab8cc", a: 0.6, len: 1 },
    { K: pick(s, 10 * k), n: 130, v: 380, ink: "#bcd8e6", a: 0.7, len: 2 },
    { K: pick(s, 14 * k), n: 34, v: 520, ink: "#e2f2f8", a: 0.6, len: 3 },
  ].map((L) => ({ ...L, n: Math.round(L.n * Math.max(0.35, area)), v: L.v * k, ids: ["/", "/", "'"].map((gl) => tk(L.K, gl, null, L.ink)), drops: [] }));
  for (const L of s.rain) for (let i = 0; i < L.n; i += 1) L.drops.push({ x: R() * (w + h * 0.3), y: R() * (h + 40 * k), v: R.range(0.85, 1.15), a: R.range(0.5, 1) });
  s.splashK = pick(s, 8 * k);
  s.splashIds = [".", "'", "o"].map((gl) => tk(s.splashK, gl, null, "#bcd6e2"));
  // Steam.
  const puff = document.createElement("canvas");
  puff.width = puff.height = 64;
  const pf = puff.getContext("2d");
  const pgr = pf.createRadialGradient(32, 32, 0, 32, 32, 32);
  pgr.addColorStop(0, "rgba(210,226,236,0.55)");
  pgr.addColorStop(0.6, "rgba(190,210,224,0.18)");
  pgr.addColorStop(1, "rgba(190,210,224,0)");
  pf.fillStyle = pgr;
  pf.fillRect(0, 0, 64, 64);
  s.puff = puff;
  s.steamK = pick(s, s.grate.q * 0.07);
  s.steamIds = ["~", "(", ")", ".", "~"].map((gl) => tk(s.steamK, gl, null, "#c8dae4"));
  // The walker crossing the far end.
  const qW = F / (zEnd + (zFar - zEnd) * 0.4);
  s.walkK = pick(s, Math.max(7 * k, (qW * 0.8) / 5));
  s.walk = { q: qW, y: yAt(qW, 0) };
}

function hexRgb(c) {
  return `${parseInt(c.slice(1, 3), 16)},${parseInt(c.slice(3, 5), 16)},${parseInt(c.slice(5, 7), 16)}`;
}

function drawSign(g, s, sg, lit, halo) {
  const { K } = sg;
  const c = K.cell;
  if (halo) {
    const r = c * (sg.word.length * 0.7 + 2.5);
    g.globalAlpha = 0.5 * lit * (1 - fogAt(s.G, sg.z) * 0.5);
    g.drawImage(s.glow(sg.n.glow), sg.x + c / 2 - r, sg.y0 + (sg.word.length * c) / 2 - r, r * 2, r * 2);
    return;
  }
  if (!sg.flick) sg.on.forEach((id, i) => pk(g, K, id, sg.x, sg.y0 + i * c));
  pk(g, K, sg.cap, sg.x, sg.y0 - c);
  pk(g, K, sg.cap, sg.x, sg.y0 + sg.word.length * c);
  // Brackets back to the wall.
  const xw = s.G.VPx + sg.side * s.G.W * sg.q;
  g.globalAlpha = 1;
  g.fillStyle = mixc("#3a444c", HAZE, fogAt(s.G, sg.z));
  const bx = sg.side < 0 ? xw : sg.x + c;
  const bw = sg.side < 0 ? sg.x - xw : xw - sg.x - c;
  const th = Math.max(1, c * 0.12);
  g.fillRect(bx, sg.y0 - c * 0.6, bw, th);
  g.fillRect(bx, sg.y0 + sg.word.length * c + c * 0.4, bw, th);
}

function signLetterOn(sg, t, i) {
  const n = Math.floor(t * 10);
  const v = Math.sin(n * 12.9898 + sg.ph * 7 + i * 3.1) * 43758.5453;
  const r = v - Math.floor(v);
  const burst = Math.sin(Math.floor(t * 0.4) * 3.7 + sg.ph) > 0.82;
  const whole = burst && Math.sin(t * 23 + sg.ph) > 0.2;
  return !(whole || r < (burst ? 0.3 : 0.04));
}

function drawStrings(g, s, t, still) {
  const { VPx, VPy, W } = s.G;
  const gust = still ? 0 : 0.6 * Math.sin(t * 0.37) + 0.4 * Math.sin(t * 0.91 + 1.3);
  for (const st of s.strings) {
    const { q, K } = st;
    const c = K.cell;
    const xa = VPx - W * q;
    const xb = VPx + W * q;
    const ya = VPy - q * (st.Y - EYE);
    const sag = q * st.sag;
    const wy = (u) => ya + 4 * sag * u * (1 - u);
    g.globalAlpha = 1;
    g.strokeStyle = mixc("#0c0e14", HAZE, st.fog * 0.6);
    g.lineWidth = Math.max(1, q * 0.012);
    g.beginPath();
    g.moveTo(xa, ya);
    g.quadraticCurveTo(VPx, ya + 2 * sag, xb, ya);
    g.stroke();
    for (const it of st.items) {
      const x = xa + (xb - xa) * it.u;
      const y = wy(it.u) + q * 0.02;
      const swing = still ? 0 : q * 0.035 * (Math.sin(t * 1.1 + it.ph) * 0.5 + gust * Math.sin(t * 0.8 + it.u * 3));
      g.globalCompositeOperation = "lighter";
      const r = c * 3.2;
      g.globalAlpha = 0.45 * (1 - st.fog * 0.5) * (0.9 + 0.1 * Math.sin(t * 3 + it.ph));
      g.drawImage(s.glow(it.glow), x + swing - r, y + c * 1.4 - r, r * 2, r * 2);
      g.globalCompositeOperation = "source-over";
      it.ids.forEach((id, j) => pk(g, K, id, Math.round(x + swing * (0.5 + j * 0.25) - c / 2), Math.round(y + j * c)));
    }
  }
  g.globalAlpha = 1;
}

export const rain = {
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
    const { ctx, w, h, k, G } = s;
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(s.back, 0, 0, w, h);
    // Lightning, far off, very now and then.
    const LP = 73;
    const ln = Math.floor(t / LP);
    const lp = t - ln * LP;
    if (lp < 0.9 && hash(ln, 3, s.seed & 1023) < 0.7) {
      const a = (lp < 0.12 ? 1 : lp > 0.3 && lp < 0.42 ? 0.7 : 0) * 0.35;
      if (a > 0) {
        ctx.fillStyle = `rgba(200,210,255,${a})`;
        ctx.fillRect(0, 0, w, G.VPy);
      }
    }
    walker(ctx, s, t);
    // Puddles: the mirror trembles in bands.
    for (const st of s.pudStrips) {
      const dx = st.amp * (Math.sin(st.y * 0.21 / k + t * 2.3) + 0.6 * Math.sin(st.y * 0.53 / k - t * 3.1));
      const sx = Math.max(0, st.x0 - dx);
      const sw = Math.min(w, st.x1 - dx) - sx;
      if (sw <= 0) continue;
      ctx.globalAlpha = 0.9;
      ctx.drawImage(s.pud, sx * s.dpr, st.y * s.dpr, sw * s.dpr, st.band * s.dpr, sx + dx, st.y, sw, st.band);
    }
    ripples(ctx, s, t);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.walls, 0, 0, w, h);
    // Windows that come on and go off; a few TVs flicker blue.
    for (const L of s.live) {
      if (L.tv) {
        const a = 0.35 + 0.5 * noise(t * 3.1, L.ph * 50) * (noise(t * 0.7, L.ph * 9) > 0.35 ? 1 : 0);
        for (const tl of L.tiles) pk(ctx, tl.K, tl.tv, tl.x, tl.y, a);
      } else if (((t / L.p + L.ph) % 1) < L.duty) {
        for (const tl of L.tiles) pk(ctx, tl.K, tl.alt, tl.x, tl.y);
      }
    }
    // The stuttering sign.
    for (const sg of s.signs) {
      if (!sg.flick) continue;
      let lit = 0;
      sg.on.forEach((id, i) => {
        const on = signLetterOn(sg, t, i);
        lit += on ? 1 : 0;
        pk(ctx, sg.K, on ? id : sg.off[i], sg.x, sg.y0 + i * sg.K.cell);
      });
      ctx.globalCompositeOperation = "lighter";
      drawSign(ctx, s, sg, lit / sg.word.length, true);
      ctx.globalCompositeOperation = "source-over";
      drawSign(ctx, s, sg, 1, false);
    }
    // Vending machine hum.
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.08 + 0.05 * Math.sin(t * 2.2) * Math.sin(t * 0.7);
    const v = s.vend;
    ctx.drawImage(s.glow("150,230,255"), v.x - v.r, v.y - v.r, v.r * 2, v.r * 2);
    ctx.globalCompositeOperation = "source-over";
    steam(ctx, s, t);
    drawStrings(ctx, s, t, false);
    rainfall(ctx, s, t);
    ctx.globalAlpha = 1;
  },
};

function walker(ctx, s, t) {
  const P = 41;
  const n = Math.floor(t / P);
  const p = (t - n * P) / 17;
  if (p >= 1) return;
  const { G } = s;
  const K = s.walkK;
  const c = K.cell;
  const dir = hash(n, 1, 7) < 0.5 ? 1 : -1;
  const span = G.xR - G.xL + 10 * c;
  const x0 = dir > 0 ? G.xL - 5 * c + p * span : G.xR + 5 * c - p * span;
  const step = Math.floor(t * 4) % 2;
  const umb = ["#ff4fa3", "#3fe6d6", "#ffb547"][Math.floor(hash(n, 2, 9) * 3)];
  const body = "#141820";
  const art = [
    [" .^. ", umb],
    ["/===\\", umb],
    ["  |o ", body],
    ["  /#\\", body],
    [step ? "  / \\" : "   | ", body],
  ];
  const yTop = s.walk.y - art.length * c;
  art.forEach(([line, col], r) => {
    Array.from(line).forEach((ch, i) => {
      if (ch === " ") return;
      const isUmb = col === umb;
      const id = isUmb ? tk(K, ch, mixc(col, "#3a3048", 0.35), shade(col, 0.4)) : tk(K, ch, ch === "O" ? "#3a2a2a" : "#1a1e28", "#4a5060");
      const xi = dir > 0 ? i : 4 - i;
      pk(ctx, K, id, Math.round(x0 + xi * c), Math.round(yTop + r * c + (r > 1 && step ? 1 : 0)));
      // Its reflection in the wet cross street.
      pk(ctx, K, id, Math.round(x0 + xi * c), Math.round(s.walk.y + (art.length - 1 - r) * c + 2), 0.18);
    });
  });
  ctx.globalAlpha = 1;
}

function ripples(ctx, s, t) {
  s.puddles.forEach((p, pi) => {
    const K = s.ripK[pi];
    const ids = s.ripIds[pi];
    const c = K.cell;
    for (let j = 0; j < p.n; j += 1) {
      const per = 1.3 + 0.4 * j;
      const tt = t / per + p.seed + j * 0.37;
      const n = Math.floor(tt);
      const age = tt - n;
      const u = hash(n, j, pi) * TAU;
      const rr = Math.sqrt(hash(n, j + 9, pi)) * 0.7;
      const cx = p.cx + Math.cos(u) * rr * p.rx;
      const cy = p.cy + Math.sin(u) * rr * p.ry;
      const R = (0.2 + age * 0.8) * Math.min(p.rx * 0.3, p.q * 0.1);
      const Ry = R * (p.ry / p.rx);
      const a = 0.75 * (1 - age);
      if (age < 0.15) pk(ctx, K, ids.dot, cx - c / 2, cy - c / 2, 0.7);
      const m = Math.max(6, Math.min(14, Math.round((TAU * R) / (c * 1.1))));
      for (let i = 0; i < m; i += 1) {
        const ang = (i / m) * TAU;
        const ca = Math.cos(ang);
        const gl = Math.abs(ca) > 0.8 ? 4 : Math.sin(ang) < 0 ? 2 : 3;
        pk(ctx, K, ids.ring[gl], cx + ca * R - c / 2, cy + Math.sin(ang) * Ry - c / 2, a);
      }
    }
  });
  ctx.globalAlpha = 1;
}

function steam(ctx, s, t) {
  const { x, y, q } = s.grate;
  for (let i = 0; i < 9; i += 1) {
    const life = (t / 7 + i / 9) % 1;
    const sx = x + Math.sin(life * 4 + i * 2.1) * q * 0.08 + life * q * 0.35;
    const sy = y - life * q * 1.5;
    const r = q * (0.1 + life * 0.35);
    ctx.globalAlpha = 0.6 * Math.sin(Math.PI * life) * (0.7 + 0.3 * noise(i, t * 0.4));
    ctx.drawImage(s.puff, sx - r, sy - r, r * 2, r * 2);
  }
  const K = s.steamK;
  for (let i = 0; i < 6; i += 1) {
    const life = (t / 4.5 + i / 6) % 1;
    const sx = x + Math.sin(life * 6 + i) * q * 0.1 + life * q * 0.25;
    const sy = y - life * q * 1.1;
    pk(ctx, K, s.steamIds[(i + Math.floor(life * 3)) % s.steamIds.length], sx - K.cell / 2, sy, 0.6 * Math.sin(Math.PI * life));
  }
  ctx.globalAlpha = 1;
}

function rainfall(ctx, s, t) {
  const { w, h } = s;
  const slant = 0.32;
  for (const L of s.rain) {
    const c = L.K.cell;
    const H = h + c * L.len * 2;
    const span = w + h * slant;
    for (const d of L.drops) {
      const y = ((d.y + L.v * d.v * t) % H) - c * L.len;
      const x = ((((d.x - y * slant) % span) + span) % span) - c;
      for (let i = 0; i < L.len; i += 1) pk(ctx, L.K, L.ids[i === L.len - 1 && L.len > 1 ? 2 : 0], x + i * c * slant, y - i * c, L.a * d.a * (1 - i / (L.len + 1)));
    }
  }
  // Splashes on the ground.
  const { VPy } = s.G;
  const K = s.splashK;
  for (let i = 0; i < 40; i += 1) {
    const tt = t * 2.5 + i * 0.618;
    const n = Math.floor(tt);
    const age = tt - n;
    if (age > 0.35) continue;
    const xx = hash(n, i, 4) * w;
    const yg = VPy + (EYE * Math.abs(xx - s.G.VPx)) / s.G.W;
    if (yg > h) continue;
    const yy = yg + (h - yg) * Math.sqrt(hash(n, i, 3));
    pk(ctx, K, s.splashIds[age < 0.12 ? 2 : age < 0.24 ? 1 : 0], xx, yy, 0.5 * (1 - age / 0.35));
  }
  ctx.globalAlpha = 1;
}

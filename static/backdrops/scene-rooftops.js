// Scene: Rooftops. Close rooftops at dusk, seen from a higher roof: clay
// tile roofs and flat roofs step away into a warm haze, water towers stand
// on stilts against a low sun, chimneys smoke, laundry flaps on its line,
// and every object throws a long shadow to the right. Windows light up one
// by one, a cat sits on our ledge with a twitching tail among the potted
// plants, and now and then the pigeons take off and wheel round.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const FONT = '"Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace';
const HAZE = "#f0a88a";
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
// Rows of roofs, far to near: tile size, front-edge height (fraction of h),
// rise range, width in cells, haze, share of pitched roofs.
const ROWS = [
  { cell: 7, base: 0.5, rise: 1, wc: [6, 12], haze: 0.64, pitch: 0.55, band: 2, things: 0.3 },
  { cell: 10, base: 0.56, rise: 1, wc: [6, 12], haze: 0.46, pitch: 0.5, band: 3, things: 0.5 },
  { cell: 14, base: 0.66, rise: 1, wc: [7, 12], haze: 0.26, pitch: 0.45, band: 4, things: 0.8 },
  { cell: 20, base: 0.85, rise: 1, wc: [8, 14], haze: 0.06, pitch: 0.4, band: 6, things: 1, near: true },
];
const STUCCO = ["#c98a72", "#b8706a", "#d49a7a", "#a86a6e", "#c47e66", "#b98a80", "#9a6070"];
const SHUTTER = ["#4f7a6a", "#5a6f94", "#8a5a4a", "#6a8a7a", "#7a5a7a"];
const CLOTH = ["#f4ede4", "#9cc4e4", "#f2a2b0", "#f0c86a", "#a8dcc0", "#e86a5a", "#c4b0e8"];

// —— Tile kits at several sizes (the tiles.js look) ——
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
function glowSprite(rgbStr) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, `rgba(${rgbStr},1)`);
  gr.addColorStop(0.4, `rgba(${rgbStr},0.3)`);
  gr.addColorStop(1, `rgba(${rgbStr},0)`);
  g.fillStyle = gr;
  g.fillRect(0, 0, 64, 64);
  return c;
}

// Roof furniture as tile art. Letters in the map name a part; the glyph
// drawn is the second string at the same place.
const ART = {
  tower: {
    shape: ["  /\\  ", " /^^\\ ", "|====|", "|||||:", "|====|", " |\\/| ", " |/\\| ", " |  | "],
    part: ["  rr  ", " rrrr ", "bwwwwb", "wwwwww", "bwwwwb", " llll ", " llll ", " l  l "],
  },
  tallTower: {
    shape: ["  /\\  ", " /^^\\ ", "|====|", "|||||:", "|||||:", "|====|", " |\\/| ", " |/\\| ", " |\\/| ", " |/\\| ", " |\\/| ", " |  | "],
    part: ["  rr  ", " rrrr ", "bwwwwb", "wwwwww", "wwwwww", "bwwwwb", " llll ", " llll ", " llll ", " llll ", " llll ", " l  l "],
  },
  chimney: { shape: ["nn", "##", "##"], part: ["cc", "kk", "kk"] },
  hut: { shape: ["====", "|[]|", "|[]|"], part: ["tttt", "hddh", "hddh"] },
  dish: { shape: ["(o", " |"], part: ["ss", " l"] },
  aerial: { shape: ["-+-", "=+=", " | ", " | ", " | "], part: ["lll", "lll", " l ", " l ", " l "] },
  vent: { shape: ["@"], part: ["v"] },
  plant: { shape: ["\\|/", "[_]"], part: ["ggg", "ppp"] },
};
const PARTS = {
  r: ["#4a3038", "#8a6070"],
  w: ["#8a5a3e", "#5a3a2a"],
  b: ["#5a4040", "#c08a6a"],
  l: [null, "#3a2232"],
  c: ["#6a4a4a", "#c4907a"],
  k: ["#a0503e", "#6a2e26"],
  t: ["#7a6a70", "#b8a4a8"],
  h: ["#9a8a8c", "#5a4a50"],
  d: ["#5a4048", "#a07a70"],
  s: [null, "#d8d0d4"],
  v: ["#8a7a80", "#4a3a40"],
  g: [null, "#5a8a50"],
  p: ["#b8603e", "#7a3a24"],
};

function stamp(g, K, art, x, y, haze, sun) {
  // Paint one piece of art, its left column lit by the sun.
  const out = [];
  art.shape.forEach((line, r) => {
    Array.from(line).forEach((ch, i) => {
      const p = art.part[r][i];
      if (ch === " " || p === " ") return;
      const [f, ink] = PARTS[p];
      const lit = i === 0 || art.shape[r][i - 1] === " " ? 1.22 : i === line.length - 1 ? 0.82 : 1;
      const fill = f ? mixc(shade(f, lit * sun), HAZE, haze) : null;
      const id = tk(K, ch, fill, mixc(shade(ink, f ? lit : 1), HAZE, haze));
      if (g) pk(g, K, id, x + i * K.cell, y + r * K.cell);
      out.push({ i, r, id });
    });
  });
  return out;
}

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  const { w, h } = s;
  s.kits = new Map();
  const kits = s.kits;
  const K = (px) => {
    const c = Math.max(3, Math.round(px * k * 2) / 2);
    if (!kits.has(c)) kits.set(c, kit(s, c));
    return kits.get(c);
  };
  s.K = K;
  s.fontStale = false;
  const fonts = typeof document !== "undefined" && document.fonts;
  if (fonts && fonts.status !== "loaded") fonts.ready.then(() => { if (s.kits === kits) s.fontStale = true; });
  s.cell = Math.max(4, 13 * k);
  const hz = (s.hz = Math.round(h * 0.47));
  const narrow = w < h;
  const sunX = (s.sunX = w * (narrow ? 0.3 : 0.2));
  const sunY = (s.sunY = hz - h * 0.01);
  const sunR = (s.sunR = Math.min(w, h) * (narrow ? 0.09 : 0.065));

  // —— Sky: dusk gradient, the low sun, still cloud banks ——
  const [sky, g] = layer(s);
  const lin = g.createLinearGradient(0, 0, 0, hz + h * 0.05);
  lin.addColorStop(0, "#3a2c50");
  lin.addColorStop(0.32, "#7a4470");
  lin.addColorStop(0.62, "#d06a74");
  lin.addColorStop(0.85, "#f6a078");
  lin.addColorStop(1, "#ffd49a");
  g.fillStyle = lin;
  g.fillRect(0, 0, w, h);
  const sg = g.createRadialGradient(sunX, sunY, 0, sunX, sunY, Math.max(w, h) * 0.55);
  sg.addColorStop(0, "rgba(255,226,160,0.75)");
  sg.addColorStop(0.15, "rgba(255,190,130,0.35)");
  sg.addColorStop(0.5, "rgba(255,140,120,0.1)");
  sg.addColorStop(1, "rgba(255,140,120,0)");
  g.fillStyle = sg;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 26; i += 1) {
    g.fillStyle = `rgba(255,236,220,${R.range(0.15, 0.5)})`;
    const r = R.range(0.7, 1.3) * k;
    g.fillRect(w * R.range(0.45, 1), Math.pow(R(), 2) * hz * 0.35, r, r);
  }
  dotGrid(g, s, "#ffd2c0", 0.14);
  // The sun: a tile disc, white-gold at the core, half behind the roofs.
  const SK = K(13);
  const sc = SK.cell;
  const sr = Math.max(2, Math.round(sunR / sc));
  for (let r = -sr; r <= sr; r += 1) {
    for (let c = -sr; c <= sr; c += 1) {
      const d = Math.hypot(c, r) / (sr + 0.4);
      if (d > 1) continue;
      const fill = mixc("#fff4d6", "#ffb878", Math.pow(d, 1.6));
      pk(g, SK, tk(SK, d > 0.82 ? ":" : r % 2 ? "-" : "=", fill, shade(fill, 0.9)), Math.round(sunX + c * sc - sc / 2), Math.round(sunY + r * sc - sc / 2));
    }
  }
  g.globalAlpha = 1;
  // Rays fanning up from the sun, and thin cloud bars drawn across it.
  g.save();
  g.globalCompositeOperation = "lighter";
  for (let i = 0; i < 7; i += 1) {
    const a = -Math.PI / 2 + R.range(-1.1, 1.1);
    const spread = R.range(0.025, 0.06);
    const L = Math.max(w, h) * R.range(0.5, 0.9);
    const rg = g.createRadialGradient(sunX, sunY, sunR, sunX, sunY, L);
    rg.addColorStop(0, `rgba(255,214,160,${R.range(0.035, 0.07)})`);
    rg.addColorStop(1, "rgba(255,214,160,0)");
    g.fillStyle = rg;
    g.beginPath();
    g.moveTo(sunX, sunY);
    g.lineTo(sunX + Math.cos(a - spread) * L, sunY + Math.sin(a - spread) * L);
    g.lineTo(sunX + Math.cos(a + spread) * L, sunY + Math.sin(a + spread) * L);
    g.closePath();
    g.fill();
  }
  g.restore();
  g.save();
  g.filter = `blur(${Math.max(1, 3 * k)}px)`;
  for (let i = 0; i < 4; i += 1) {
    const y = sunY - sunR * R.range(-0.2, 0.9);
    g.fillStyle = `rgba(200,96,110,${R.range(0.35, 0.6)})`;
    g.beginPath();
    g.ellipse(sunX + R.range(-1, 1) * sunR * 2, y, sunR * R.range(1.6, 3.4), Math.max(1.5, R.range(2, 4) * k), 0, 0, TAU);
    g.fill();
  }
  g.restore();
  s.sky = sky;
  // Cloud streaks lit from below; they drift.
  s.clouds = Array.from({ length: 7 }, () => {
    const cw = R.range(180, 420) * k;
    const ch = R.range(8, 18) * k;
    const blur = Math.max(2, 8 * k);
    const pad = blur * 3;
    const c = document.createElement("canvas");
    c.width = Math.round((cw + pad * 2) * s.dpr);
    c.height = Math.round((ch + pad * 2) * s.dpr);
    const cg = c.getContext("2d");
    cg.scale(s.dpr, s.dpr);
    cg.filter = `blur(${blur}px)`;
    const y = R.range(0.08, 0.36);
    cg.fillStyle = y > 0.25 ? `rgba(255,190,150,${R.range(0.35, 0.6)})` : `rgba(230,140,160,${R.range(0.3, 0.5)})`;
    for (let j = 0; j < 4; j += 1) {
      cg.beginPath();
      cg.ellipse(pad + cw * R.range(0.2, 0.8), pad + ch / 2, cw * R.range(0.18, 0.35), ch * R.range(0.3, 0.5), 0, 0, TAU);
      cg.fill();
    }
    return { c, w: cw + pad * 2, h: ch + pad * 2, x: R() * w, y: y * h, v: R.range(1.5, 4) * k };
  });

  // —— The roofs, far to near ——
  const [city, cg] = layer(s);
  s.wins = [];
  const covers = [];
  s.smokes = [];
  s.lines = [];
  s.perch = null;
  s.towerAt = false;
  const shadowC = "rgba(70,24,60,";
  ROWS.forEach((row, ri) => {
    const RK = K(row.cell);
    const c = RK.cell;
    const base = Math.round((h * row.base) / c) * c;
    let x = -Math.floor(R() * 4) * c;
    while (x < w) {
      const u = x / w;
      const side = Math.abs(u - 0.5) * 2;
      const wc = Math.round(R.range(row.wc[0], row.wc[1] + 0.99));
      const top = base - Math.floor(R() * (row.rise + 1)) * c; // front edge of the roof
      const bw = wc * c;
      const wall = mixc(shade(R.pick(STUCCO), R.range(0.85, 1.08)), HAZE, row.haze);
      const sunHere = ri >= 2 && !s.towerAt && wc >= 8 && sunX > x + 3 * c && sunX < x + bw - 3 * c;
      const towerI = sunHere ? Math.max(1, Math.min(wc - 8, Math.round((sunX - x) / c) - 3)) : -1;
      const pitched = !sunHere && R() < row.pitch;
      // Front wall: smooth, lit from the left, darker low down.
      const wg = cg.createLinearGradient(x, 0, x + bw, 0);
      wg.addColorStop(0, shade(wall, 1.08));
      wg.addColorStop(1, shade(wall, 0.8));
      cg.globalAlpha = 1;
      cg.fillStyle = wg;
      cg.fillRect(x, top, bw, h - top);
      const dk = cg.createLinearGradient(0, top, 0, top + h * 0.25);
      dk.addColorStop(0, "rgba(60,20,50,0)");
      dk.addColorStop(1, "rgba(60,20,50,0.35)");
      cg.fillStyle = dk;
      cg.fillRect(x, top, bw, h - top);
      // Windows with shutters; they light up one by one later.
      const shutter = mixc(R.pick(SHUTTER), HAZE, row.haze);
      const wstep = R() < 0.5 ? 3 : 4;
      const fstep = ri < 2 ? 2 : 3;
      for (let fr = 1; top + fr * c < h; fr += fstep) {
        for (let wi = 1; wi < wc - 1; wi += wstep) {
          const wx = x + wi * c;
          const wy = top + fr * c + (pitched ? 0 : c);
          if (wy > h) continue;
          const shut = R() < 0.3;
          const glass = tk(RK, shut ? "=" : ":", shut ? shutter : mixc("#3a2a3a", HAZE, row.haze * 0.6), shut ? shade(shutter, 1.4) : mixc("#6a5060", HAZE, row.haze * 0.5));
          pk(cg, RK, glass, wx, wy);
          if (wstep === 4 && wi + 1 < wc - 1) pk(cg, RK, tk(RK, "|", shutter, shade(shutter, 1.4)), wx + c, wy);
          if (!shut) {
            const lc = R.pick(["#ffd27a", "#ffc068", "#ffe0a0"]);
            s.wins.push({ ri, K: RK, x: wx, y: wy, id: tk(RK, "#", mixc(lc, HAZE, row.haze * 0.35), mixc(shade(lc, 0.55), HAZE, row.haze * 0.3)), p: R.range(70, 160), ph: R(), duty: R.range(0.45, 0.8), g: row.haze });
          }
        }
      }
      // The roof itself.
      const sun = 1 - 0.18 * clamp01((x - sunX) / w);
      if (pitched) {
        const rows = Math.max(2, Math.min(Math.floor(wc * 0.7), row.band + 1 + Math.floor(R() * 2)));
        covers.push({ ri, x0: x, x1: x + bw, y0: top - rows * c });
        const clay = mixc(R.pick(["#c0603e", "#b05a46", "#c87050", "#a0524a"]), HAZE, row.haze);
        for (let r = 0; r < rows; r += 1) {
          const y = top - (r + 1) * c;
          const i0 = Math.max(0, r - (rows - 3));
          const i1 = wc - 1 - i0;
          for (let i = i0; i <= i1; i += 1) {
            const lit = sun * (1.15 - 0.3 * ((i - i0) / Math.max(1, i1 - i0)));
            const f = shade(clay, lit * (r % 2 ? 0.94 : 1.04));
            const gl = i === i0 ? "/" : i === i1 ? "\\" : r === 0 ? "_" : r === rows - 1 ? "=" : (i + r) % 2 ? "~" : "^";
            pk(cg, RK, tk(RK, gl, f, shade(f, i === i0 ? 1.45 : 0.68)), x + i * c, y);
          }
        }
        // A chimney on the slope, smoking.
        if (R() < 0.6) {
          const ci = 1 + Math.floor(R() * Math.max(1, wc - 3));
          const cy = top - (rows + 2) * c;
          stamp(cg, RK, ART.chimney, x + ci * c, cy, row.haze, sun);
          if (R() < 0.7) s.smokes.push({ x: x + ci * c + c, y: cy, c, ph: R() * 10, haze: row.haze });
        }
        // Long shadow down the slope to the right.
        cg.fillStyle = `${shadowC}${0.22 - row.haze * 0.15})`;
        cg.beginPath();
        cg.moveTo(x + (wc - rows) * c, top - rows * c);
        cg.lineTo(x + bw, top);
        cg.lineTo(x + bw + rows * c * 2.5, top);
        cg.closePath();
        cg.fill();
      } else {
        // Flat roof: a band of tar seen from above, a coping row in front.
        const band = Math.max(1, Math.round(row.band * R.range(0.8, 1.3)));
        const bt = top - band * c;
        covers.push({ ri, x0: x, x1: x + bw, y0: bt - c });
        const tar = mixc(R.pick(["#8a6070", "#9a6a6a", "#7a5a6a"]), HAZE, row.haze * 0.8);
        const rg = cg.createLinearGradient(x, 0, x + bw, 0);
        rg.addColorStop(0, shade(tar, 1.2 * sun));
        rg.addColorStop(1, shade(tar, 0.9 * sun));
        cg.fillStyle = rg;
        cg.fillRect(x, bt, bw, band * c);
        for (let i = 0; i < wc; i += 1) {
          const f = mixc(shade(wall, i === 0 ? 1.35 : 1.15 - 0.2 * (i / wc)), HAZE, row.haze * 0.3);
          pk(cg, RK, tk(RK, "=", f, shade(f, 0.7)), x + i * c, top - c);
          pk(cg, RK, tk(RK, "=", f, shade(f, 1.3)), x + i * c, bt - c);
        }
        // Furniture on the roof, each with a long shadow to the right.
        const spots = [];
        let i = 1;
        while (i < wc - 2) {
          let roll = R() / row.things;
          if (towerI >= 0 && !s.towerAt && i >= towerI - 1) {
            roll = 0;
            s.towerAt = true;
          } else if (towerI >= 0 && !s.towerAt && i + 6 >= towerI - 1) roll = 2;
          if (roll > 1) {
            i += 2;
            continue;
          }
          let art = null;
          if (roll === 0 && towerI >= 0) art = ART.tallTower;
          else if (ri >= 1 && roll < 0.14 && wc - i >= 7) art = ART.tower;
          else if (roll < 0.36) art = ART.chimney;
          else if (roll < 0.5) art = ART.hut;
          else if (roll < 0.6) art = ART.dish;
          else if (roll < 0.7 && ri < 3) art = ART.aerial;
          else if (roll < 0.8) art = ART.vent;
          else if (roll < 0.9 && ri >= 2) art = ART.plant;
          if (!art) {
            i += 2;
            continue;
          }
          const aw = Math.max(...art.shape.map((l) => l.length));
          const ah = art.shape.length;
          if (i + aw > wc - 1) break;
          const ay = top - c - ah * c - Math.floor(R() * band) * c * 0.5;
          spots.push({ art, i, aw, ah, ay });
          i += aw + 1 + Math.floor(R() * 3);
        }
        for (const sp of spots) {
          const sx = x + sp.i * c;
          const hgt = sp.ah * c;
          cg.fillStyle = `${shadowC}${0.3 - row.haze * 0.2})`;
          cg.beginPath();
          cg.moveTo(sx + sp.aw * c, sp.ay + hgt);
          cg.lineTo(sx + sp.aw * c + hgt * 2.6, sp.ay + hgt + c * 0.3);
          cg.lineTo(sx + sp.aw * c + hgt * 2.6, sp.ay + hgt + c);
          cg.lineTo(sx + c * 0.3, sp.ay + hgt + c * 0.6);
          cg.closePath();
          cg.fill();
        }
        for (const sp of spots) {
          stamp(cg, RK, sp.art, x + sp.i * c, sp.ay, row.haze, sun);
          covers.push({ ri, x0: x + sp.i * c, x1: x + (sp.i + sp.aw) * c, y0: sp.ay });
          if (sp.art === ART.chimney && R() < 0.6) s.smokes.push({ x: x + sp.i * c + c, y: sp.ay, c, ph: R() * 10, haze: row.haze });
        }
        // Laundry on the near flat roofs.
        if (row.near && wc >= 6 && side > 0.3 && s.lines.length < 3) {
          const lx0 = x + c * 0.5;
          const lx1 = x + bw - c * 0.5;
          const ly = top - c - Math.round(R.range(3.5, 5)) * c;
          for (const px of [lx0, lx1]) {
            cg.fillStyle = mixc("#4a3440", HAZE, 0.05);
            cg.fillRect(px - Math.max(1, c * 0.08), ly, Math.max(2, c * 0.16), top - c - ly);
          }
          const items = [];
          let ix = lx0 + c * 0.6;
          while (ix < lx1 - c * 1.6) {
            const kind = R.pick(["shirt", "towel", "sock", "pants", "shirt"]);
            const cw = kind === "sock" ? 1 : kind === "towel" ? 2 : kind === "pants" ? 2 : 3;
            if (ix + cw * c > lx1 - c * 0.4) break;
            const col = R.pick(CLOTH);
            const rows2 = kind === "shirt" ? ["/#\\", " # ", " # "] : kind === "towel" ? ["##", "##", "##", "::"] : kind === "pants" ? ["##", "||", "||"] : ["#", "#", "'"];
            const cells = [];
            rows2.forEach((ln, r) => Array.from(ln).forEach((ch, j) => {
              if (ch === " ") return;
              const bare = ch === "/" || ch === "\\";
              const f = shade(col, j === 0 ? 1.04 : 0.94 - r * 0.03);
              cells.push({ j, r, id: tk(RK, ch === "#" ? (r % 2 ? "=" : "-") : ch, bare ? null : f, bare ? f : shade(f, 0.78)) });
            }));
            items.push({ x: ix, cells, rows: rows2.length, ph: R() * TAU });
            ix += (cw + 0.4 + R() * 0.8) * c;
          }
          s.lines.push({ x0: lx0, x1: lx1, y: ly, sag: c * 0.6, K: RK, items });
        }
      }
      // Gaps between buildings are narrow alleys of shadow.
      x += bw + (R() < 0.25 ? c * Math.round(R.range(1, 2)) : 0);
    }
    // Haze between rows, thickest far off.
    const hg = cg.createLinearGradient(0, base - h * 0.12, 0, base + h * 0.06);
    hg.addColorStop(0, "rgba(255,190,150,0)");
    hg.addColorStop(1, `rgba(255,190,150,${0.18 * (1 - ri / 3)})`);
    cg.globalAlpha = 1;
    cg.fillStyle = hg;
    cg.fillRect(0, base - h * 0.12, w, h * 0.18);
  });
  cg.globalAlpha = 1;
  s.city = city;
  s.wins = s.wins.filter((wn) => !covers.some((cv) => cv.ri > wn.ri && cv.x0 < wn.x + wn.K.cell && cv.x1 > wn.x && cv.y0 < wn.y + wn.K.cell));
  // —— Foreground: our own parapet with pots, the cat ——
  const [fore, fg] = layer(s);
  const FK = K(20);
  const fc = FK.cell;
  const fy = (s.foreY = Math.round(h - fc * 1.6));
  // The drop to the street between the near roofs and our ledge.
  const nearBase = h * ROWS[3].base;
  const drop = cg.createLinearGradient(0, nearBase + h * 0.02, 0, fy);
  drop.addColorStop(0, "rgba(70,30,60,0)");
  drop.addColorStop(1, "rgba(60,30,70,0.6)");
  cg.globalAlpha = 1;
  cg.fillStyle = drop;
  cg.fillRect(0, nearBase + h * 0.02, w, fy - nearBase);
  // Our own water tower stands at the left edge, backlit by the sun.
  const TK = K(Math.min(narrow ? 16 : 24, (fy - h * 0.45) / 16 / k));
  const tc = TK.cell;
  const TOWER = [
    "    /\\    ",
    "   /^^\\   ",
    "  /^^^^\\  ",
    " (======) ",
    " (||||||) ",
    " (||||||) ",
    " (======) ",
    " (||||||) ",
    " (======) ",
    "  |\\/\\/|  ",
    "  |/\\/\\|  ",
    "  |\\/\\/|  ",
    "  |/\\/\\|  ",
    "  |\\/\\/|  ",
    "  |    |  ",
    "  |    |  ",
  ];
  const tx0 = Math.round(-tc * (narrow ? 3 : 0.5));
  const ty0 = fy - TOWER.length * tc;
  TOWER.forEach((ln, r) => Array.from(ln).forEach((ch, i) => {
    if (ch === " ") return;
    const roof = r < 3;
    const leg = r >= 9;
    const edge = i === 0 || ln[i - 1] === " ";
    const base = roof ? "#3a2434" : "#5e3a36";
    const post = leg && ch === "|";
    const f = leg && !post ? null : mixc(shade(post ? "#3a2430" : base, edge ? 1.6 : 1 - 0.05 * i), "#ff9a6a", edge ? 0.35 : 0);
    const ink = leg && !post ? "#3a2232" : edge ? "#ffb890" : shade(base, 2);
    pk(cg, TK, tk(TK, ch, f, ink), tx0 + i * tc, ty0 + r * tc);
  }));
  // A TV aerial on our roof at the right, wires running off; pigeons sit on one.
  const ax = Math.round(w * (narrow ? 0.9 : 0.93));
  const atop = Math.round(h * (narrow ? 0.3 : 0.24));
  const AK = K(14);
  cg.fillStyle = "#3a2434";
  cg.fillRect(ax - Math.max(1, 2 * k), atop, Math.max(2, 4 * k), fy - atop);
  for (const [dy, half] of [[0, 3], [AK.cell * 1.4, 2], [AK.cell * 2.6, 1]]) {
    for (let i = -half; i <= half; i += 1) pk(cg, AK, tk(AK, i === 0 ? "+" : "-", null, "#3a2434"), ax - AK.cell / 2 + i * AK.cell * 0.8, atop + dy - AK.cell / 2);
  }
  cg.globalAlpha = 1;
  const wireA = { x0: ax, y0: atop + AK.cell * 0.3, x1: w + 20, y1: h * 0.1, sag: h * 0.03 };
  const wireB = { x0: ax, y0: atop + AK.cell * 0.3, x1: w * 0.58, y1: h * 0.02, sag: h * 0.05 };
  cg.strokeStyle = "#3a2434";
  cg.lineWidth = Math.max(1, 1.4 * k);
  for (const wr of [wireA, wireB]) {
    cg.beginPath();
    cg.moveTo(wr.x0, wr.y0);
    cg.quadraticCurveTo((wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2 + wr.sag * 2, wr.x1, wr.y1);
    cg.stroke();
  }
  const wireAt = (wr, u) => [wr.x0 + (wr.x1 - wr.x0) * u, wr.y0 + (wr.y1 - wr.y0) * u + 4 * wr.sag * u * (1 - u)];
  s.perch = { K: K(10), n: 7, at: (u) => wireAt(wireB, u) };
  // Laundry behind our own tower would hang in front of it: drop it.
  s.lines = s.lines.filter((ln) => ln.x0 > tx0 + 10 * tc);
  for (let x = 0, i = 0; x < w; x += fc, i += 1) {
    const sun = 1.15 - 0.35 * (x / w);
    const top = mixc("#d89078", "#ffc8a0", 0.3 * clamp01(1 - x / w));
    pk(fg, FK, tk(FK, "=", shade(top, sun), shade(top, sun * 0.72)), x, fy);
    const brick = shade("#8a4a48", sun * (i % 3 === 0 ? 0.92 : 1));
    pk(fg, FK, tk(FK, i % 2 ? "#" : "=", brick, shade(brick, 0.7)), x, fy + fc);
    pk(fg, FK, tk(FK, "#", shade(brick, 0.8), shade(brick, 0.6)), x, fy + fc * 2);
  }
  fg.globalAlpha = 1;
  s.fore = fore;
  // Potted plants on the ledge, mostly toward the corners.
  const PLANTS = [
    { g: [" *o* ", "*\\|/*", "\\\\|//"], t: [" fff ", "flllf", "lllll"], fl: "#e8506a" },
    { g: [" ^ ^ ", "^\\|/^", " \\|/ "], t: [" l l ", "lllll", " lll "], fl: "#e8506a" },
    { g: ["  o  ", " o|o ", "o\\|/o"], t: ["  f  ", " flf ", "flllf"], fl: "#f2c04a" },
    { g: [" )|( ", "( | )", " \\|/ "], t: [" lll ", "l l l", " lll "], fl: "#e8506a" },
    { g: [" * * ", "*\\*/*", " \\|/ "], t: [" f f ", "flflf", " lll "], fl: "#f49ac0" },
  ];
  s.pots = [];
  const cols = Math.floor(w / fc);
  const catCol = (s.catCol = Math.round(cols * (narrow ? 0.7 : 0.8)));
  let pc = 0;
  while (pc < cols) {
    const u = (pc * fc) / w;
    const side = Math.abs(u - 0.5) * 2;
    if ((side > 0.42 || R() < 0.08) && (pc < catCol - 3 || pc > catCol + 7) && R() < 0.55) {
      const pl = R.pick(PLANTS);
      const PK = side > 0.6 ? FK : K(16);
      const pcell = PK.cell;
      const px = pc * fc;
      const potW = 3;
      const pot = Array.from({ length: potW }, (_, j) => tk(PK, j === 0 ? "[" : j === potW - 1 ? "]" : "=", shade("#c06a44", j === 0 ? 1.15 : 0.95), "#6a3020"));
      const leaves = [];
      pl.g.forEach((ln, r) => Array.from(ln).forEach((ch, j) => {
        const tt = pl.t[r][j];
        if (ch === " " && tt === " ") return;
        const lit = 1.12 - 0.08 * j - 0.04 * r;
        const edge = j === 0 || j === ln.length - 1 || pl.t[r][j - 1] === " " || pl.t[r][j + 1] === " ";
        const f = tt === "f" ? shade(pl.fl, lit) : tt === "l" && !edge ? shade(["#5a9a50", "#4a8a48", "#6aa85a"][(j + r) % 3], lit) : null;
        leaves.push({ j: j - 1, r, lift: 1 - r / pl.g.length, id: tk(PK, ch === " " ? "." : ch, f, f ? shade(f, tt === "f" ? 0.55 : 0.62) : "#4a8a48") });
      }));
      s.pots.push({ x: px, y: fy - pcell, K: PK, pot, leaves, rows: pl.g.length, ph: R() * TAU });
      pc += potW + 1 + Math.floor(R() * 3);
    } else pc += 1;
  }
  // The cat: a sitting silhouette in tile pixels, facing the sun.
  const CK = (s.catK = K(narrow ? 9 : 11));
  const fur = "#3a2636";
  const rim = "#d08068";
  const CAT = [
    ".^..^...",
    ".####...",
    "o####...",
    ".####...",
    "..###...",
    "..####..",
    "..#####.",
    "..######",
    "..######",
    "..######",
    "..#.####",
  ];
  s.cat = [];
  CAT.forEach((ln, r) => Array.from(ln).forEach((ch, j) => {
    if (ch === ".") return;
    const edge = j === 0 || ln[j - 1] === ".";
    const f = edge ? mixc(fur, rim, r < 4 ? 0.55 : 0.4) : shade(fur, 1 - 0.04 * (j - 2));
    const eye = r === 2 && j === 1;
    const gl = ch === "^" ? "^" : eye ? "o" : r === 10 && j === 2 ? "m" : (j + r) % 3 ? ":" : ".";
    s.cat.push({ j, r, eye, id: tk(CK, gl, f, eye ? "#ffd27a" : shade(f, 1.5)), shut: eye ? tk(CK, "-", f, "#ffd27a") : null });
  }));
  s.catTail = ["~", "_", ")", "/"].map((gl) => tk(CK, gl, fur, shade(fur, 1.6)));
  s.catX = catCol * fc;
  s.catY = fy - CAT.length * CK.cell;
  s.catW = 8;
  // Pigeons.
  const PKk = s.perch.K;
  s.pig = {
    sit: tk(PKk, "o", "#4e4862", "#8a84a0"),
    head: tk(PKk, "'", null, "#3a3048"),
    fly: ["v", "-", "^", "-"].map((gl) => tk(PKk, gl, null, "#4a3a58")),
  };
  s.birds = Array.from({ length: s.perch.n }, (_, i) => {
    const [px, py] = s.perch.at(0.12 + i * 0.075 + R() * 0.03);
    return { x: px - PKk.cell / 2, y: py - PKk.cell * 0.9, d: i * 0.18 + R() * 0.3, rx: R.range(0.85, 1.15), ry: R.range(0.85, 1.15), ph: R() * TAU };
  });
  s.smokeK = K(10);
  s.smokeIds = ["~", "(", ")", ".", "~", "o"].map((gl) => tk(s.smokeK, gl, null, "#f4e0dc"));
  const puff = document.createElement("canvas");
  puff.width = puff.height = 48;
  const pf = puff.getContext("2d");
  const pg = pf.createRadialGradient(24, 24, 0, 24, 24, 24);
  pg.addColorStop(0, "rgba(240,214,214,0.5)");
  pg.addColorStop(1, "rgba(240,214,214,0)");
  pf.fillStyle = pg;
  pf.fillRect(0, 0, 48, 48);
  s.puff = puff;
  s.glowW = glowSprite("255,200,120");
  s.swiftK = K(8);
  s.swift = [tk(s.swiftK, "v", null, "#4a2a48"), tk(s.swiftK, "-", null, "#4a2a48")];
  s.seedF = R() * 1000;
}

export const rooftops = {
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
    const { ctx, w, h } = s;
    ctx.globalAlpha = 1;
    ctx.drawImage(s.sky, 0, 0, w, h);
    for (const cl of s.clouds) {
      const span = w + cl.w;
      const x = ((((cl.x + cl.v * t) % span) + span) % span) - cl.w;
      ctx.drawImage(cl.c, x, cl.y - cl.h / 2, cl.w, cl.h);
    }
    swifts(ctx, s, t);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.city, 0, 0, w, h);
    // Windows coming on one by one.
    ctx.globalCompositeOperation = "lighter";
    for (const wn of s.wins) {
      const on = ((t / wn.p + wn.ph) % 1) < wn.duty;
      if (!on) continue;
      const r = wn.K.cell * 2.2;
      ctx.globalAlpha = 0.18 * (1 - wn.g);
      ctx.drawImage(s.glowW, wn.x + wn.K.cell / 2 - r, wn.y + wn.K.cell / 2 - r, r * 2, r * 2);
    }
    ctx.globalCompositeOperation = "source-over";
    for (const wn of s.wins) {
      const f = (t / wn.p + wn.ph) % 1;
      if (f >= wn.duty) continue;
      pk(ctx, wn.K, wn.id, wn.x, wn.y, Math.min(1, f * wn.p * 1.5));
    }
    smoke(ctx, s, t);
    laundry(ctx, s, t);
    pigeons(ctx, s, t, false);
    ctx.globalAlpha = 1;
    ctx.drawImage(s.fore, 0, s.foreY * s.dpr, w * s.dpr, (h - s.foreY) * s.dpr, 0, s.foreY, w, h - s.foreY);
    plants(ctx, s, t);
    cat(ctx, s, t);
    pigeons(ctx, s, t, true);
    ctx.globalAlpha = 1;
  },
};

const wind = (s, t, x) => 0.6 * Math.sin(t * 0.7 + x * 0.003) + 0.3 * Math.sin(t * 1.7 + x * 0.011) + 0.9 * Math.exp(-(((x - (((t * 120 + s.seedF) % (s.w + 1400)) - 700)) / 240) ** 2));

function smoke(ctx, s, t) {
  for (const sm of s.smokes) {
    for (let i = 0; i < 5; i += 1) {
      const life = (t / 6 + i / 5 + sm.ph) % 1;
      const r = sm.c * (0.8 + life * 3);
      ctx.globalAlpha = 0.7 * Math.sin(Math.PI * life) * (1 - sm.haze * 0.6);
      ctx.drawImage(s.puff, sm.x + life * sm.c * 5 + Math.sin(life * 5 + i) * sm.c * 0.4 - r, sm.y - life * sm.c * 4 - r, r * 2, r * 2);
    }
    const K = s.smokeK;
    for (let i = 0; i < 3; i += 1) {
      const life = (t / 4 + i / 3 + sm.ph) % 1;
      pk(ctx, K, s.smokeIds[(i + Math.floor(life * 4)) % s.smokeIds.length], sm.x + life * sm.c * 4 - K.cell / 2, sm.y - life * sm.c * 3.5 - K.cell, 0.55 * Math.sin(Math.PI * life) * (1 - sm.haze));
    }
  }
  ctx.globalAlpha = 1;
}

function laundry(ctx, s, t) {
  for (const ln of s.lines) {
    const c = ln.K.cell;
    const yAt = (x) => ln.y + 4 * ln.sag * ((x - ln.x0) / (ln.x1 - ln.x0)) * (1 - (x - ln.x0) / (ln.x1 - ln.x0));
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#5a4050";
    ctx.lineWidth = Math.max(1, c * 0.06);
    ctx.beginPath();
    ctx.moveTo(ln.x0, ln.y);
    ctx.quadraticCurveTo((ln.x0 + ln.x1) / 2, ln.y + ln.sag * 2, ln.x1, ln.y);
    ctx.stroke();
    for (const it of ln.items) {
      const wv = wind(s, t, it.x);
      const y0 = yAt(it.x + c);
      for (const cl of it.cells) {
        const lift = cl.r / it.rows;
        const dx = (wv * 0.45 + 0.25 * Math.sin(t * 3.1 + it.ph + cl.r)) * c * lift;
        pk(ctx, ln.K, cl.id, Math.round(it.x + cl.j * c + dx), Math.round(y0 + cl.r * c - c * 0.15 * lift * Math.abs(wv)));
      }
    }
  }
  ctx.globalAlpha = 1;
}

function plants(ctx, s, t) {
  for (const p of s.pots) {
    const c = p.K.cell;
    const bend = (wind(s, t, p.x) * 0.5 + 0.2 * Math.sin(t * 2.3 + p.ph)) * c * 0.5;
    for (const lf of p.leaves) pk(ctx, p.K, lf.id, Math.round(p.x + lf.j * c * 0.8 + bend * lf.lift), p.y - (p.rows - lf.r) * c * 0.85);
    p.pot.forEach((id, j) => pk(ctx, p.K, id, p.x + j * c - c * 0.6, p.y));
  }
  ctx.globalAlpha = 1;
}

function cat(ctx, s, t) {
  const K = s.catK;
  const c = K.cell;
  // Eyes blink now and then, an ear flicks, the tail twitches.
  const blink = (t % 6.3) < 0.18 || (t % 17.1) < 0.15;
  const flick = Math.sin(t * 0.9) * Math.sin(t * 0.37) > 0.82;
  for (const p of s.cat) pk(ctx, K, blink && p.eye ? p.shut : p.id, s.catX + p.j * c, s.catY + p.r * c - (flick && p.r === 0 && p.j === 4 ? c * 0.3 : 0));
  // The tail lies along the ledge and curls up at the tip.
  const tw = noise(t * 0.8, 3.3);
  const lift = tw > 0.6 ? 2 : tw > 0.45 ? 1 : 0;
  const ty = s.catY + 10 * c;
  const tx = s.catX + s.catW * c;
  pk(ctx, K, s.catTail[1], tx, ty);
  pk(ctx, K, s.catTail[1], tx + c, ty);
  pk(ctx, K, s.catTail[lift === 0 ? 0 : 2], tx + 2 * c, ty - (lift === 0 ? 0 : c));
  if (lift === 2) pk(ctx, K, s.catTail[3], tx + 2 * c, ty - 2 * c);
  ctx.globalAlpha = 1;
}

function pigeons(ctx, s, t, flying) {
  const pr = s.perch;
  const K = pr.K;
  const c = K.cell;
  const P = 58;
  const n = Math.floor(t / P);
  const tt = t - n * P;
  const dir = hash(n, 5, 1) < 0.5 ? 1 : -1;
  const Rx = s.w * 0.22;
  const Ry = s.h * 0.2;
  for (const b of s.birds) {
    const u = clamp01((tt - b.d) / 16);
    const air = u > 0 && u < 1;
    if (air !== flying) continue;
    const bx0 = b.x;
    const by0 = b.y;
    if (!air) {
      const bob = Math.sin(t * 1.3 + b.ph) > 0.85 ? 1 : 0;
      pk(ctx, K, s.pig.sit, bx0, by0);
      pk(ctx, K, s.pig.head, bx0 - c * 0.5, by0 - c * 0.45 - bob);
      continue;
    }
    const e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
    const th = e * TAU;
    // Out over the roofs in a wide dipping loop, then back to the wire.
    const x = bx0 - Rx * b.rx * Math.sin(th) * (1.2 + 0.4 * dir) + Math.sin(t * 2 + b.ph) * c * 0.4;
    const y = by0 + Ry * b.ry * (1 - Math.cos(th)) * 0.5 - Math.sin(th) * Ry * 0.35 * dir;
    pk(ctx, K, s.pig.fly[Math.floor(t * 8 + b.ph * 3) % 4], x, y, 0.95);
  }
  ctx.globalAlpha = 1;
}

function swifts(ctx, s, t) {
  const P = 37;
  const n = Math.floor(t / P);
  const p = (t - n * P) / 22;
  if (p > 1) return;
  const dir = hash(n, 1, 2) < 0.5 ? 1 : -1;
  const y = s.hz * (0.2 + hash(n, 2, 2) * 0.45);
  const x = dir > 0 ? -40 + p * (s.w + 80) : s.w + 40 - p * (s.w + 80);
  for (let i = 0; i < 3; i += 1) {
    const bx = x - dir * i * s.swiftK.cell * 2.2;
    const by = y + Math.sin(t * 1.4 + i * 2) * 6 * s.k + (i % 2) * s.swiftK.cell;
    pk(ctx, s.swiftK, s.swift[Math.sin(t * 7 + i) > 0 ? 0 : 1], bx, by, 0.8);
  }
  ctx.globalAlpha = 1;
}

// Landforms in tiles, shared by the ASCII landscapes: pines and mountain
// ranges.

import { noise } from "./noise.js";
import { tile, put, mixc, shade } from "./tiles.js";

const BODY = ["%", "t", "S", "%", "t", "%", "X"];

// A pine in tiles: tiers that widen downward and step back in at each new
// tier, a ^ tip, / and \ flanks, textured middles and a trunk. Each cell has
// `lift` (1 at the tip, 0 at the foot) for leaning in the wind.
export function pineCells(th, R) {
  const cells = [];
  const trunk = th > 6 ? 2 : 1;
  const crown = th - trunk;
  const tiers = Math.max(1, Math.round(crown / 3.2));
  let r = 0;
  cells.push({ dc: 0, r: r++, g: "^", part: "tip" });
  for (let t = 0; t < tiers && r < crown; t += 1) {
    const rowsIn = Math.max(2, Math.round((crown - 1) / tiers));
    const start = 1 + Math.floor(t * 0.6);
    for (let j = 0; j < rowsIn && r < crown; j += 1) {
      const half = Math.min(start + j, 1 + Math.floor(r * 0.55));
      for (let dc = -half; dc <= half; dc += 1) {
        const g = dc === -half ? "/" : dc === half ? "\\" : BODY[Math.floor(R() * BODY.length)];
        cells.push({ dc, r, g, part: dc < 0 ? "lit" : "shade" });
      }
      r += 1;
    }
  }
  for (let j = 0; j < trunk; j += 1) cells.push({ dc: 0, r: r++, g: "|", part: "trunk" });
  for (const c of cells) c.lift = 1 - c.r / r;
  return { cells, rows: r };
}

// Mountain ranges in tiles, painted into `g`: each massif is several sharp
// peaks falling away at about a row per column, so ridgelines run as clean
// / and \ diagonals with ^ on the summits; snow above a ragged line, lit
// and shaded faces, everything fading into `haze` toward `foot` (px).
// Returns each column's ridge row (Infinity where there is no mountain).
export function mountains(g, s, R, o) {
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell) + 1;
  const foot = Math.round(o.foot / cell);
  const ridgeAll = new Float32Array(cols).fill(Infinity);
  for (const M of o.massifs) {
    const off = R() * 100;
    const np = 4 + Math.floor(R() * 3);
    const peaks = Array.from({ length: np }, (_, i) => ({
      c: ((M.x + (i / (np - 1) - 0.5) * M.wd * 1.3 + (R() - 0.5) * 0.05) * w) / cell,
      top: (h * (M.top + R.range(0, 0.12) + Math.abs(i / (np - 1) - 0.5) * 0.14)) / cell,
      fall: R.range(0.65, 1.35),
    }));
    const ridge = new Float32Array(cols).fill(Infinity);
    for (let c = 0; c < cols; c += 1) {
      let best = Infinity;
      for (const pk of peaks) best = Math.min(best, pk.top + Math.abs(c - pk.c) * pk.fall);
      if (best < foot - 0.5) ridge[c] = best + (noise(c * 0.5 + off, 4) - 0.5) * 0.6;
      ridgeAll[c] = Math.min(ridgeAll[c], ridge[c]);
    }
    const snowAt = (c) => (h * M.top) / cell + (foot - (h * M.top) / cell) * ((o.snowLine || 0.3) + 0.14 * noise(c * 0.35 + off, 9));
    for (let c = 1; c < cols - 1; c += 1) {
      if (!Number.isFinite(ridge[c])) continue;
      const top = Math.round(ridge[c]);
      const slope = (Number.isFinite(ridge[c + 1]) ? ridge[c + 1] : foot) - (Number.isFinite(ridge[c - 1]) ? ridge[c - 1] : foot);
      const peak = ridge[c] <= ridge[c - 1] && ridge[c] <= ridge[c + 1];
      const lit = slope > 0;
      for (let r = top; r < foot; r += 1) {
        const snow = r < snowAt(c) - (lit ? 0 : 0.6);
        const down = (r - top) / Math.max(1, foot - top);
        let fill = snow ? (lit ? o.snow[0] : o.snow[1]) : lit ? shade(M.rock, 1.1) : shade(M.rock, 0.86);
        fill = mixc(fill, o.haze, (o.hazeTop ?? 0.28) + 0.5 * down);
        let glyph;
        if (r === top) glyph = peak ? "^" : slope > 0 ? "\\" : "/";
        else if (snow) glyph = (c * 5 + r * 3) % 4 === 0 ? "*" : (c + r) % 3 === 0 ? ":" : ".";
        else glyph = (c * 7 + r * 11) % 5 === 0 ? "%" : (c + r * 2) % 3 === 0 ? ";" : ":";
        const ink = snow ? mixc(fill, o.snowInk, 0.55) : shade(fill, 0.74);
        put(g, s, tile(s, glyph, fill, ink), c * cell, r * cell, 1 - (o.fade ?? 0.5) * down * down);
      }
    }
  }
  g.globalAlpha = 1;
  return ridgeAll;
}

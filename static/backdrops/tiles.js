// Tile kit for the ASCII landscapes: the chunky look of the wallpapers (a
// square tile in a fill colour with a glyph inked on it, a hairline gap
// between tiles) at landscape scale. Every fill/ink/glyph combination is
// painted once into a growing atlas, so a frame is only drawImage calls;
// static layers are painted once into their own canvas.

const FONT = '"Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace';
const SLOTS = 64;

export function tiles(s, cell) {
  const P = Math.max(3, Math.round(cell * s.dpr));
  s.P = P;
  s.cell = P / s.dpr;
  s.cols = Math.ceil(s.w / s.cell) + 1;
  s.rows = Math.ceil(s.h / s.cell) + 1;
  const c = document.createElement("canvas");
  c.width = SLOTS * P;
  c.height = SLOTS * P;
  const g = c.getContext("2d");
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = `700 ${Math.round(P * 0.74)}px ${FONT}`;
  s.atlas = { c, g, map: new Map(), n: 0, gap: Math.max(1, Math.round(P * 0.07)) };
  s.fontStale = false;
  // Tiles painted before the web font arrived carry the fallback face: the
  // scene rebuilds on its next frame once the font is in.
  const fonts = typeof document !== "undefined" && document.fonts;
  if (fonts && fonts.status !== "loaded") {
    const atlas = s.atlas;
    fonts.ready.then(() => {
      if (s.atlas === atlas) s.fontStale = true;
    });
  }
}

// The atlas slot for one tile. `fill` null = a bare glyph.
export function tile(s, glyph, fill, ink) {
  const A = s.atlas;
  const key = `${glyph}|${fill}|${ink}`;
  let id = A.map.get(key);
  if (id !== undefined) return id;
  id = A.n % (SLOTS * SLOTS);
  A.n += 1;
  const P = s.P;
  const x = (id % SLOTS) * P;
  const y = Math.floor(id / SLOTS) * P;
  A.g.clearRect(x, y, P, P);
  if (fill) {
    A.g.fillStyle = fill;
    A.g.fillRect(x + A.gap / 2, y + A.gap / 2, P - A.gap, P - A.gap);
  }
  if (glyph && glyph !== " " && ink) {
    A.g.fillStyle = ink;
    A.g.fillText(glyph, x + P / 2, y + P * 0.54);
  }
  A.map.set(key, id);
  return id;
}

export function put(ctx, s, id, x, y, alpha = 1) {
  if (alpha <= 0.01) return;
  const P = s.P;
  ctx.globalAlpha = alpha > 1 ? 1 : alpha;
  ctx.drawImage(s.atlas.c, (id % SLOTS) * P, Math.floor(id / SLOTS) * P, P, P, x, y, s.cell, s.cell);
}

export function layer(s) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(s.w * s.dpr));
  c.height = Math.max(1, Math.round(s.h * s.dpr));
  const g = c.getContext("2d");
  g.scale(s.dpr, s.dpr);
  return [c, g];
}

// A faint dot at tile corners, strongest toward the screen edges.
export function dotGrid(g, s, color, alpha, every = 2, edge = 0.16) {
  const step = s.cell * every;
  const band = edge * Math.min(s.w, s.h);
  const dot = Math.max(1, Math.round(s.cell * 0.12 * s.dpr)) / s.dpr;
  g.fillStyle = color;
  for (let y = step; y < s.h; y += step) {
    for (let x = step; x < s.w; x += step) {
      const d = Math.min(x, s.w - x, y, s.h - y);
      if (d >= band) continue;
      g.globalAlpha = alpha * (1 - d / band);
      g.fillRect(x - dot / 2, y - dot / 2, dot, dot);
    }
  }
  g.globalAlpha = 1;
}

// Colour maths on #rrggbb, quantised so shaded tiles share atlas slots.
export const rgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
export function hex(c) {
  return "#" + c.map((v) => Math.max(0, Math.min(255, Math.round(v / 4) * 4)).toString(16).padStart(2, "0")).join("");
}
export const mixc = (a, b, t) => hex(rgb(a).map((v, i) => v + (rgb(b)[i] - v) * t));
export const shade = (a, k) => hex(rgb(a).map((v) => v * k));

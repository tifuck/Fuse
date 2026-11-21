// Shared helpers for the built-in scenes (style-*.js): offscreen layers,
// low-resolution soft buffers and hex colour maths. Randomness and noise
// come from noise.js (seeded, noise, hash).

export const TAU = Math.PI * 2;
export const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

// An offscreen canvas of w x h CSS pixels at the scene's pixel ratio, its
// context already scaled: paint static parts once per resize, then one
// drawImage(c, x, y, w, h) per frame.
export function layer(s, w = s.w, h = s.h) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w * s.dpr));
  c.height = Math.max(1, Math.round(h * s.dpr));
  const g = c.getContext("2d");
  g.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
  return [c, g];
}

// A small canvas (1 px per `scale` CSS px) for soft, blurry light: paint
// gradients or pixels into it, then stretch it over the screen with
// smoothing on. Cheap enough to repaint every frame.
export function soft(s, scale = 8) {
  const c = document.createElement("canvas");
  c.width = Math.max(2, Math.ceil(s.w / scale));
  c.height = Math.max(2, Math.ceil(s.h / scale));
  return [c, c.getContext("2d")];
}

export function rgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
export const rgba = (hex, a = 1) => {
  const [r, g, b] = rgb(hex);
  return "rgba(" + r + "," + g + "," + b + "," + a + ")";
};
export function mixHex(a, b, t) {
  const x = rgb(a);
  const y = rgb(b);
  const c = x.map((v, i) => Math.round(v + (y[i] - v) * t));
  return "#" + ((1 << 24) | (c[0] << 16) | (c[1] << 8) | c[2]).toString(16).slice(1);
}

// The scene registry. Engine and entry read it by key; the Appearance
// catalog (BACKDROPS in chat-appearance.js) gives the order, labels,
// category and art style.

// Built-in scenes live one module per art style (style-<style>.js), each
// exporting its scenes by key. A module loads when one of its scenes is
// first shown (loadScene); this table holds what the page asks before that.
// Every one is a wallpaper; `scheme` repeats the scene's own.
const soft = () => import("./style-soft.js");
const layered = () => import("./style-layered.js");
const line = () => import("./style-line.js");
const dots = () => import("./style-dots.js");

export const BUILTIN = {
  aurora: { scheme: "dark", load: soft },
  auroracrimson: { scheme: "dark", load: soft },
  auroracorona: { scheme: "dark", load: soft },
  aurorabands: { scheme: "dark", load: soft },
  auroraorbit: { scheme: "dark", load: soft },
  auroralake: { scheme: "dark", load: layered },
  auroraridge: { scheme: "dark", load: layered },
  auroraveil: { scheme: "dark", load: line },
  aurorastipple: { scheme: "dark", load: dots },
  nebula: { scheme: "dark", load: soft },
  abyss: { scheme: "dark", load: soft },
  aura: { scheme: "dark", load: soft },
  bloom: { scheme: "light", load: soft },
  lanterns: { scheme: "dark", load: soft },
  ranges: { scheme: "dark", load: layered },
  pines: { scheme: "dark", load: layered },
  dunes: { scheme: "light", load: layered },
  tide: { scheme: "dark", load: layered },
  ringplanet: { scheme: "dark", load: layered },
  cloudscape: { scheme: "light", load: layered },
  contours: { scheme: "dark", load: line },
  currents: { scheme: "dark", load: line },
  constellations: { scheme: "dark", load: line },
  rainfall: { scheme: "dark", load: line },
  blueprint: { scheme: "dark", load: line },
  paperplanes: { scheme: "light", load: line },
  halftone: { scheme: "dark", load: dots },
  starfield: { scheme: "dark", load: dots },
  fireflies: { scheme: "dark", load: dots },
  snowfall: { scheme: "dark", load: dots },
  glyphfield: { scheme: "dark", load: dots },
  pollen: { scheme: "light", load: dots },
};

// Scenes ready to draw: built-ins once loaded, and registered wallpapers.
export const SCENES = {};

export const hasScene = (kind) => Boolean(SCENES[kind] || Object.hasOwn(BUILTIN, kind));
export const isWallpaper = (kind) => Boolean(Object.hasOwn(BUILTIN, kind) || SCENES[kind]?.spec || SCENES[kind]?.wallpaper);

export async function loadScene(kind) {
  if (!SCENES[kind] && Object.hasOwn(BUILTIN, kind)) SCENES[kind] = (await BUILTIN[kind].load())[kind];
  return SCENES[kind];
}

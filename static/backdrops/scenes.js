// The scene registry. Engine and entry read it by key; the Appearance
// catalog (BACKDROPS in chat-appearance.js) gives the order and labels.

// A built-in scene's module loads when the scene is first shown (loadScene);
// this table holds what the page asks before that. Every one is a wallpaper;
// `scheme` repeats the scene module's own.
export const BUILTIN = {
  fogpines: { scheme: "dark", load: () => import("./scene-fogpines.js") },
  harbor: { scheme: "dark", load: () => import("./scene-harbor.js") },
  auroralake: { scheme: "dark", load: () => import("./scene-aurora-lake.js") },
  cliffs: { scheme: "dark", load: () => import("./scene-cliffs.js") },
  deepsea: { scheme: "dark", load: () => import("./scene-deepsea.js") },
  orbital: { scheme: "dark", load: () => import("./scene-orbital.js") },
  rain: { scheme: "dark", load: () => import("./scene-rain.js") },
  rooftops: { scheme: "dark", load: () => import("./scene-rooftops.js") },
  circuit: { scheme: "dark", load: () => import("./scene-circuit.js") },
  launch: { scheme: "dark", load: () => import("./scene-launch.js") },
  bamboo: { scheme: "dark", load: () => import("./scene-bamboo.js") },
  waterfall: { scheme: "dark", load: () => import("./scene-waterfall.js") },
  desert: { scheme: "dark", load: () => import("./scene-desert.js") },
  sakura: { scheme: "light", load: () => import("./scene-sakura.js") },
  terraces: { scheme: "light", load: () => import("./scene-terraces.js") },
  meadow: { scheme: "light", load: () => import("./scene-meadow.js") },
};

// Scenes ready to draw: built-ins once loaded, and registered wallpapers.
export const SCENES = {};

export const hasScene = (kind) => Boolean(SCENES[kind] || Object.hasOwn(BUILTIN, kind));
export const isWallpaper = (kind) => Boolean(Object.hasOwn(BUILTIN, kind) || SCENES[kind]?.spec || SCENES[kind]?.wallpaper);

export async function loadScene(kind) {
  if (!SCENES[kind] && Object.hasOwn(BUILTIN, kind)) SCENES[kind] = (await BUILTIN[kind].load())[kind];
  return SCENES[kind];
}

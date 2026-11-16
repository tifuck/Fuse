/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme
   (the garden family keeps natural plant colours, pitched to it), renders on
   its own frame budget, pauses while the tab is hidden, and holds a
   single still frame under prefers-reduced-motion. Scenes run on their own and
   never follow the pointer; the picker cards render a real frame of each
   scene, animating while hovered. */

/* The drawing lives in ./backdrops/: kits (env/color/noise/kit/tiles/tile-land),
   one module per scene family, the registry in scenes.js, and the runner, picker
   previews and sync in engine.js. Load this entry as a module:
   <script type="module" src="/static/chat-backdrop.js?v=N"></script>
   Chunk map and extraction record: backdrop-manifest.json in the snapshot parent. */

import { html } from "./backdrops/env.js";
import { SCENES } from "./backdrops/scenes.js";
import { sync, scanPreviews } from "./backdrops/engine.js";
import { validateWallpaper } from "./backdrops/wallpaper-spec.js";
import { wallpaperScene } from "./backdrops/wallpaper.js";

// A data-only wallpaper spec (docs/wallpapers.md) becomes a scene under `key`.
// Built-in scenes cannot be replaced; an invalid spec is refused with its errors.
const BUILTIN = new Set(Object.keys(SCENES));
function register(key, spec) {
  if (typeof key !== "string" || !/^[a-z][a-z0-9-]{0,31}$/.test(key) || key === "none") {
    return { ok: false, errors: ["key: lowercase letters, digits and dashes, at most 32"] };
  }
  if (BUILTIN.has(key)) return { ok: false, errors: [`key: "${key}" is a built-in backdrop`] };
  const checked = validateWallpaper(spec);
  if (!checked.ok) return { ok: false, errors: checked.errors };
  try {
    SCENES[key] = wallpaperScene(spec);
  } catch (error) {
    delete SCENES[key];
    return { ok: false, errors: [String(error && error.message || error)] };
  }
  return { ok: true, errors: [] };
}

window.FuseBackdrop = {
  sync,
  previews: scanPreviews,
  animated: (kind) => Boolean(SCENES[kind]),
  isWallpaper: (kind) => Boolean(SCENES[kind]?.spec || SCENES[kind]?.wallpaper),
  scheme: (kind) => (SCENES[kind]?.spec || SCENES[kind]?.wallpaper ? SCENES[kind].scheme || "" : ""),
  register,
};
scanPreviews();
// chat-appearance.js may have applied a backdrop before this file loaded.
const scene = document.querySelector("[data-chat-scene]");
if (scene) sync(scene, html.dataset.chatBackdrop);
document.dispatchEvent(new CustomEvent("fuse-backdrop-ready"));

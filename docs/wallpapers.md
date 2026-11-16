# Wallpapers

Animated ASCII wallpapers for the chat background: chunky glyphs on coloured
square tiles, layered on a tinted gradient, always gently moving. A wallpaper
is **plain JSON data, never code**, so a model can write one ("an underwater
castle with fish and sharks, spooky") and the browser can run it without
trusting it.

| File | Role |
| --- | --- |
| `static/backdrops/wallpaper-spec.js` | Limits, motion vocabulary and `validateWallpaper(spec)`. No DOM; runs under node too. |
| `static/backdrops/wallpaper.js` | `wallpaperScene(spec)`: validates, then returns a normal scene module `{ fps, setup, resize, draw }`. |
| `static/backdrops/scenes.js`, `static/chat-appearance.js` | Registration (`SCENES`) and the Appearance catalog (`BACKDROPS`). |
| `static/chat-backdrop.js` | `FuseBackdrop.register(key, spec)`, behind `FuseChatAppearance.addWallpaper`. |

Because a wallpaper is an ordinary scene, the engine's runner, the picker
previews, the hidden-tab pause and the reduced-motion still frame apply
unchanged.

## Landscape wallpapers

The built-in wallpapers are code scenes in the same tile language, too
elaborate for the spec format: one `static/backdrops/scene-<key>.js` each,
registered in `scenes.js`, built on `tiles.js` (a tile
atlas filled on demand, every fill/ink/glyph combination painted once) and
`tile-land.js` (tile pines and mountain ranges). Air, sky and fog are painted
smooth; matter is tiles. Each sets `wallpaper: true` and `scheme`, so the
engine, the /theme picker and the full-strength CSS treat it like a spec
wallpaper. Static layers are painted once per resize; a frame redraws only
what moves (swaying crowns, switching windows, aurora rays, reflections).

## Validation

`validateWallpaper(spec)` returns `{ ok, errors }`. Errors are short strings
with a path (`layers[2].motions[0].type: unknown motion "zoom" (allowed: …)`),
at most 20, written so a model can fix its spec from them. `wallpaperScene`
throws on an invalid spec.

Hard limits (`LIMITS` in `wallpaper-spec.js`):

| Limit | Value |
| --- | --- |
| Serialized JSON | 32 768 bytes |
| Nesting (the spec itself is level 1) | 6 levels |
| `name` | 40 characters |
| `glyphs` alphabet | 64 glyphs |
| Sprites | 24, each at most 32 × 20 tiles, legend at most 32 entries |
| Layers | 12, each with at most 4 motions |
| Sprites placed (sum of every sprite layer's `count` max) | 160 |
| Particles alive at once (all particle layers) | 240 |
| Ambient glyphs | 600 |
| Tiles drawn in one frame (upper bound, `cellBound(spec)`) | 6 000 |

Also enforced: the spec is a plain object (no functions, cycles or class
instances); **unknown keys are rejected at every level**; colours are hex
only (`#rgb` or `#rrggbb`); glyphs are printable ASCII or one of `·•°◦○●★✦`,
and every glyph used must be in the spec's `glyphs`; numbers and `[min, max]`
ranges stay inside their bounds; sprite names are a letter then up to 23
letters, digits, `_` or `-`.

## Spec

Top level (required unless marked optional):

| Key | Type | Meaning |
| --- | --- | --- |
| `version` | `1` | Format version. |
| `name` | string ≤ 40, optional | Display name. |
| `scheme` | `"light"` or `"dark"` | Hint for the UI: which theme family reads well on top. |
| `seed` | integer 0..2147483647 | Every random choice derives from it. |
| `cell` | integer 8..32 | Tile size in CSS px at full size. Phones get down to 0.6×, picker cards about ⅓. |
| `fps` | 4..20, optional (12) | Frame rate. |
| `gap` | 0..0.3, optional (0) | Gap between tiles, as a fraction of a tile. |
| `glyphs` | string | The alphabet: every glyph the spec draws. |
| `background` | object | See below. |
| `ambient` | object, optional | A sparse field of animated characters. |
| `sprites` | object | Named character-art sprites. |
| `layers` | array | What is placed and how it moves, drawn in order (first = back). |

### Background

`{ "type": "solid" | "linear" | "radial", "stops": [hex, …], "at": [x, y], "grid": {…} }`

- `solid` takes exactly 1 stop; `linear` (top to bottom) and `radial` take 2 to 4, spaced evenly.
- `at` (radial only, default `[0.5, 0.4]`): the centre, as screen fractions.
- `grid` (optional): a faint dot at tile corners, strongest at the screen edges and gone further in.
  `{ "color": hex, "alpha": 0..1 (0.25), "every": 1..8 tiles (2), "edge": 0.02..0.5 (0.2, band width as a fraction of the short side) }`.

### Ambient

`{ "glyphs": ".,:", "colors": [hex × 1..4], "count": [min, max], "alpha": [lo, hi], "period": [min, max], "region": [x0, y0, x1, y1] }`

Glyphs sit on the tile grid, fade between `alpha` lo and hi over their period
and step to the next glyph of their alphabet each period.

### Sprites

```json
"fish": {
  "rows": [" qq ", ">qqqe", " qq "],
  "legend": {
    ">": [">", null, "#ffb3c4"],
    "q": ["s", "#f27a9a", "#7a1a38"],
    "e": ["o", "#f7a8bc", "#4a0c20"]
  }
}
```

Each character in `rows` is one tile; a space is empty; rows may be ragged.
A legend entry is `[glyph, tile, ink]`: the glyph drawn (or `" "` for a bare
tile), the tile's fill colour (or `null` for no tile) and the glyph colour
(`null` only for a bare tile). Sprites face **right**. One travelling left is
mirrored, and `( ) < > / \ [ ] { }` swap so they still point the right way.

### Layers

Sprite layer (`"type": "sprites"`, or omitted):

| Key | Default | Meaning |
| --- | --- | --- |
| `sprite` | required | A sprite name, or a list of up to 8. |
| `frameRate` | 0 | 0: each entity picks one sprite from the list. Above 0: entities cycle through the list as animation frames, this many per second. |
| `count` | `[1, 1]` | Entities, interpolated by viewport area: `min` on a phone or smaller, `max` from a 1440×900 window up. Whole numbers ≤ 60. |
| `depth` | 0 | 0 near … 1 far. Far layers are up to 45 % smaller, 70 % slower (parallax) and 60 % fainter. |
| `region` | `[0, 0, 1, 1]` | Where anchors are placed, as screen fractions. Anchors are spread evenly across it. |
| `alpha` | `[1, 1]` | Per-entity opacity range. |
| `snap` | false | Snap drawn positions to the tile grid. |
| `motions` | `[]` | Up to 4 motions, combined. |

Particle layer (`"type": "particles"`):

| Key | Default | Meaning |
| --- | --- | --- |
| `sprite` | required | A list plays over each particle's life: the first at birth, the last as it dies. |
| `mode` | `"stream"` | `"stream"`: a steady trickle. `"burst"`: every `period` seconds, `burst` particles at once from one point. |
| `region` | `[0, 0, 1, 1]` | Where particles (or bursts) start. Burst layers with the same region and period go off together. |
| `rate` | 2 | Stream spawns per second, scaled down to ¼ on small screens. |
| `period`, `burst` | 4, 12 | Burst timing and size. |
| `life` | `[2, 4]` | Seconds. Particles fade out over their life; stream particles also ease in. |
| `speed`, `angle` | `[0.5, 1.5]`, `[0, 360]` | Tiles per second; degrees, 0 = right, 90 = down. |
| `gravity` | 0 | Tiles/s², positive pulls down. |
| `drag` | 0 | 0..6. With drag a particle shoots out and coasts to a stop: a bloom. |
| `sway`, `depth`, `alpha` | 0, 0, `[1, 1]` | As for sprite layers. |

### Motions

Ranges (`[min, max]`) are drawn per entity from the seed. Speeds are in tiles
per second, amplitudes in tiles, periods in seconds.

| Type | Parameters (defaults) | Effect |
| --- | --- | --- |
| `drift` | `speed` [0.5, 1], `angle` [0, 0] | Straight travel, wrapping at the screen edges. Faces left when moving left. |
| `swim` | `speed` [1, 2], `amp` [0.5, 1.5], `period` [4, 8], `climb` [0, 0] | Back and forth across the region on a wavy path, flipping to face its way. `climb` adds a vertical back-and-forth for diagonal paths. |
| `rise` | `speed` [0.5, 1], `sway` [0.3, 1], `period` [3, 6] | Rises (a negative speed falls) with a side-to-side sway, wrapping top and bottom. |
| `bob` | `amp` [0.5, 1], `period` [3, 6], `axis` "y" | Sine sway around the anchor. |
| `orbit` | `radius` [1, 3], `period` [6, 12], `squash` 0.6 | Circles the anchor; `squash` flattens the circle into an ellipse. |
| `pulse` | `scale` [0.92, 1.08], `period` [2, 4] | Breathes between the two scales (here the pair bounds the oscillation; it is not a random range). |
| `twinkle` | `min` 0.2, `period` [1, 3], `flicker` 0 | Fades between `min` and full; `flicker` mixes in random steps. |

Travel motions (`drift`, `swim`, `rise`) move the anchor and wrap; the others
then play around it, whatever their order in the list.

## Determinism and cost

Every position is a pure function of the seed, the viewport and time: no
per-frame state and no `Math.random`, so a frame is reproducible and the
reduced-motion still frame is as settled as any other. The background is
painted once per resize and each sprite is pre-rendered once per tile size and
facing, so a frame is one background blit, one `drawImage` per visible sprite
or particle and one atlas stamp per ambient glyph. Spec wallpapers draw in under a
millisecond per frame at 1440×900 in headless Chromium.

## Display strength

Wallpapers paint an opaque, full-colour background of their own. `engine.js`
`sync()` stamps `data-wallpaper` on the scene whenever the active kind has a
`spec`, and `chat-design.css` shows `.chat-scene[data-wallpaper]` canvases at
`0.45 + Intensity` (0.57–1 over the 12–55 % range) with no centre mask and no
veil, so no CSS lists wallpaper keys.

## Scheme and themes

Each chat theme keeps its own backdrop, strength and palette
(`fuse.chat.views.v1` in `chat-appearance.js`). Choosing a wallpaper whose
`scheme` is `"light"` (the bright daytime landscapes) moves that theme from a dark palette to
Paper and remembers the old one; None, or a dark wallpaper, brings it back.
Picking a palette by hand while a light wallpaper is on keeps the hand pick.

## Registering a new wallpaper

`FuseChatAppearance.addWallpaper(key, spec)` is the hook for model-made
wallpapers: it hands the spec to `FuseBackdrop.register` (`chat-backdrop.js`),
which checks the key (`[a-z][a-z0-9-]{0,31}`, not a built-in), runs
`validateWallpaper` and sets `SCENES[key] = wallpaperScene(spec)` inside
try/catch; then it adds a catalog entry, repaints the Appearance cards and
fires `fuse-wallpapers` so an open `/theme` picker refreshes. It returns
`{ ok, errors }`; nothing is registered on failure
(`tests/chat_wallpaper_register.test.cjs`). Registrations live for the page
only; a theme whose saved backdrop names one draws nothing until it is
registered again.

## Example

```json
{
  "version": 1,
  "name": "Night Reef",
  "scheme": "dark",
  "seed": 31337,
  "cell": 16,
  "fps": 12,
  "glyphs": "%t:;.,oO<>sSx",
  "background": {
    "type": "linear",
    "stops": ["#0b2a3a", "#071a26", "#030b12"],
    "grid": { "color": "#4a8aa0", "alpha": 0.25, "every": 2, "edge": 0.15 }
  },
  "ambient": { "glyphs": ".,:", "colors": ["#3f7f90"], "count": [20, 60], "alpha": [0.05, 0.35], "period": [3, 8] },
  "sprites": {
    "shark": {
      "rows": ["     d      ", "dd dddddddo ", " ddbbbbbbbd>", "dd  ddd     "],
      "legend": { "d": [":", "#5d6f7c", "#2a3640"], "b": [".", "#c9d3da", "#8a98a2"], "o": ["o", "#5d6f7c", "#0a1014"], ">": [">", null, "#c9d3da"] }
    },
    "fish": { "rows": [">qqe"], "legend": { ">": [">", null, "#ffd27a"], "q": ["s", "#f2a33a", "#6a3a08"], "e": ["o", "#ffd27a", "#3a1a04"] } },
    "coral": { "rows": ["x x", "xxx", " x "], "legend": { "x": ["x", "#a83a5a", "#3a0a1a"] } },
    "bubble": { "rows": ["o"], "legend": { "o": ["o", null, "#bfefff"] } },
    "pop": { "rows": ["o"], "legend": { "o": ["O", null, "#e6faff"] } }
  },
  "layers": [
    { "sprite": "coral", "count": [3, 10], "region": [0, 0.9, 1, 1], "motions": [{ "type": "bob", "axis": "x", "amp": [0.1, 0.2], "period": [3, 5] }] },
    { "sprite": "shark", "count": [1, 2], "depth": 0.4, "region": [0, 0.15, 1, 0.6], "motions": [{ "type": "swim", "speed": [1.5, 2.2], "amp": [0.5, 1], "period": [6, 9] }] },
    { "sprite": "fish", "count": [4, 10], "region": [0, 0.4, 1, 0.9], "motions": [{ "type": "swim", "speed": [2, 3], "amp": [0.3, 0.8], "period": [3, 5] }, { "type": "twinkle", "min": 0.6, "period": [2, 4] }] },
    { "type": "particles", "sprite": ["bubble", "pop"], "mode": "stream", "region": [0, 0.7, 1, 1], "rate": 1.5, "life": [5, 9], "speed": [1, 2], "angle": [-95, -85], "sway": 0.6 }
  ]
}
```

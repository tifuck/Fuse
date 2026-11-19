/* Apply before paint; view rendering and navigation remain owned by chat.js. */
(() => {
  const themes = {
    fuse: { label: "Fuse", scheme: "dark", ink: "#000000", panel: "#0e0e0e", raise: "#181818", mist: "#ececec", mute: "#9a9a9a", line: "#262626", brass: "#a5d8ff", steel: "#a6c0d0", reason: "#aebcc5", fault: "#f29d8e", ok: "#aed397", onBrass: "#000000", code: "#070707" },
    paper: { label: "Paper", scheme: "light", ink: "#ffffff", panel: "#f3f1ec", raise: "#e7e3da", mist: "#1c1b18", mute: "#6a655c", line: "#d5cfc4", brass: "#b45c2e", steel: "#2f6a4a", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#f4f1ea" },
    midnight: { label: "Midnight", scheme: "dark", ink: "#0a1018", panel: "#121a28", raise: "#1b2638", mist: "#e8eef6", mute: "#8b97a8", line: "#243044", brass: "#e3bc81", steel: "#7ec8e8", reason: "#a8b4c8", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#0a1018", code: "#070c12" },
    void: { label: "Void", scheme: "dark", ink: "#000000", panel: "#0c0c0c", raise: "#161616", mist: "#f2f2f2", mute: "#8c8c8c", line: "#222222", brass: "#5ef2c0", steel: "#8ad4c4", reason: "#9aa4b0", fault: "#ff8b7e", ok: "#7ee0a8", onBrass: "#000000", code: "#000000" },
    ember: { label: "Ember", scheme: "dark", ink: "#16110e", panel: "#1f1915", raise: "#2a221c", mist: "#f3ebe3", mute: "#a89888", line: "#3a3028", brass: "#e08a5c", steel: "#d4a07a", reason: "#cbb6a6", fault: "#ef8b78", ok: "#c5c07a", onBrass: "#16110e", code: "#120e0b" },
    moss: { label: "Moss", scheme: "dark", ink: "#101410", panel: "#171c17", raise: "#222822", mist: "#e8eee6", mute: "#8f9a88", line: "#2c342c", brass: "#c6b06a", steel: "#8fbf8a", reason: "#b7c4b0", fault: "#e09a7a", ok: "#9dca86", onBrass: "#101410", code: "#0c100c" },
    rose: { label: "Rose", scheme: "dark", ink: "#161014", panel: "#1f171c", raise: "#2b2128", mist: "#f4e8ee", mute: "#a8909a", line: "#3a2c34", brass: "#e8a0b4", steel: "#c9a0c4", reason: "#d0b8c4", fault: "#ef8b9a", ok: "#c8c090", onBrass: "#161014", code: "#120c10" },
    ocean: { label: "Ocean", scheme: "dark", ink: "#07141a", panel: "#0d1c24", raise: "#152830", mist: "#dff3f4", mute: "#7fa0a8", line: "#1e3640", brass: "#6ad4c8", steel: "#7eb8d4", reason: "#a8c8d0", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#07141a", code: "#051016" },
    dusk: { label: "Dusk", scheme: "dark", ink: "#12101a", panel: "#1a1826", raise: "#262436", mist: "#ece8f6", mute: "#9a94b0", line: "#322e44", brass: "#c4b5e8", steel: "#8eb0e8", reason: "#b8b0d0", fault: "#f0a4c0", ok: "#a8d0c0", onBrass: "#12101a", code: "#0e0c16" },
    wine: { label: "Wine", scheme: "dark", ink: "#140a0e", panel: "#1c1014", raise: "#2a181c", mist: "#f4e8e6", mute: "#a88888", line: "#3a2428", brass: "#d4b06a", steel: "#c49090", reason: "#d0b8b0", fault: "#ef8b78", ok: "#c0c080", onBrass: "#140a0e", code: "#10080c" },
    slate: { label: "Slate", scheme: "dark", ink: "#12151a", panel: "#191e26", raise: "#242a34", mist: "#e8eef4", mute: "#8b97a8", line: "#2c3440", brass: "#7aa2ff", steel: "#8ec8e0", reason: "#a8b8d0", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#12151a", code: "#0e1218" },
    carbon: { label: "Carbon", scheme: "dark", ink: "#0a0a0a", panel: "#141414", raise: "#1e1e1e", mist: "#f0f0f0", mute: "#8a8a8a", line: "#2a2a2a", brass: "#ff8a4c", steel: "#d4b090", reason: "#b0a8a0", fault: "#ff8b7e", ok: "#c8d080", onBrass: "#0a0a0a", code: "#050505" },
    sand: { label: "Sand", scheme: "light", ink: "#f6f0e6", panel: "#ebe3d4", raise: "#dfd4c2", mist: "#2a241c", mute: "#6e6558", line: "#d4c8b4", brass: "#b85c38", steel: "#3d6b52", reason: "#5a564c", fault: "#c24b40", ok: "#3d7a48", onBrass: "#ffffff", code: "#f3ebe0" },
    frost: { label: "Frost", scheme: "light", ink: "#f3f6f8", panel: "#e6eef2", raise: "#d7e2e8", mist: "#1a2428", mute: "#5c6c74", line: "#c8d4dc", brass: "#2a7a8c", steel: "#3d6a80", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#eef4f6" },
    matcha: { label: "Matcha", scheme: "light", ink: "#f3f4ea", panel: "#e6ead8", raise: "#d8dec6", mist: "#1c2418", mute: "#5c6850", line: "#c8d0b4", brass: "#4a6b32", steel: "#3d6a52", reason: "#4e5a46", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#eef0e2" },
    ivory: { label: "Ivory", scheme: "light", ink: "#fffcf7", panel: "#f4efe6", raise: "#e8e0d4", mist: "#1c1a28", mute: "#5c5868", line: "#d8d0c4", brass: "#3d4a9a", steel: "#3d6a80", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#faf6f0" },
  };
  const BACKDROPS = {
    none: { label: "None", hint: "Solid" },
    fogpines: { label: "Fog Pines", hint: "Tile pines · valley mist" },
    auroralake: { label: "Aurora Lake", hint: "Northern lights · lake" },
    bamboo: { label: "Bamboo Grove", hint: "Tile culms · sun shafts" },
    waterfall: { label: "Waterfall Gorge", hint: "Tile cliffs · plunge pool" },
    sakura: { label: "Sakura Wind", hint: "Old cherry · moon bridge" },
    terraces: { label: "Rice Terraces", hint: "Flooded steps · sunrise" },
    meadow: { label: "Alpine Meadow", hint: "Golden hour · backlit grass" },
    desert: { label: "Desert Night", hint: "Star trails · moonlit dunes" },
    cliffs: { label: "Coastal Cliffs", hint: "Lighthouse · dusk surf" },
    deepsea: { label: "Deep Sea", hint: "Whale passing · god rays" },
    orbital: { label: "Orbital", hint: "Planet limb · station" },
    harbor: { label: "Harbor Night", hint: "Neon skyline · bay" },
    rain: { label: "Rain District", hint: "Neon alley · wet reflections" },
    rooftops: { label: "Rooftops", hint: "Clay roofs · sunset · a cat" },
    circuit: { label: "Circuit City", hint: "PCB city map · data traffic" },
    launch: { label: "Launch Site", hint: "Rocket at dawn · launch cycle" },
  };
  // Saved choices from retired backdrops map to their nearest replacement
  // instead of silently switching the wallpaper off.
  const LEGACY_BACKDROPS = {
    glyphs: "circuit", life: "circuit", ripple: "deepsea", spiral: "orbital", coral: "deepsea",
    riso: "sakura", warp: "orbital", signal: "circuit", mandala: "sakura", aurora: "auroralake",
    garden: "sakura", botanica: "bamboo", ivy: "bamboo", pond: "terraces", koi: "deepsea",
    orbit: "orbital", galaxy: "orbital", eclipse: "orbital", belt: "orbital", contact: "orbital",
    spark: "launch", clouddrift: "meadow", honulagoon: "deepsea", koipond: "deepsea",
    jellyfish: "deepsea", paperboats: "cliffs", fireflies: "fogpines", moonlit: "auroralake",
    hush: "circuit", tide: "deepsea", veil: "fogpines", ember: "launch", phosphor: "rain",
    dither: "sakura", scope: "orbital", topo: "terraces", loom: "sakura", wind: "meadow",
    maze: "bamboo", ants: "circuit", pulsar: "orbital", ink: "auroralake", fern: "bamboo", sumi: "deepsea",
  };
  // Reading fonts. Bricolage Grotesque, Red Hat Mono and Geist are bundled
  // (static/fonts); the rest load from Google Fonts only when chosen, and the
  // picker loads a tiny glyph subset of each so its cards render in their face.
  const SANS = '"Segoe UI", system-ui, sans-serif';
  const SERIF = 'Georgia, "Times New Roman", serif';
  const MONO = 'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace';
  const W3 = "wght@400;500;700";
  const FONTS = {
    bricolage: { label: "Bricolage Grotesque", kind: "sans", spec: "", bundled: true },
    geist: { label: "Geist", kind: "sans", spec: "", bundled: true },
    instrument: { label: "Instrument Sans", kind: "sans", spec: W3 },
    figtree: { label: "Figtree", kind: "sans", spec: W3 },
    schibsted: { label: "Schibsted Grotesk", kind: "sans", spec: W3 },
    spacegrotesk: { label: "Space Grotesk", kind: "sans", spec: W3 },
    hanken: { label: "Hanken Grotesk", kind: "sans", spec: W3 },
    onest: { label: "Onest", kind: "sans", spec: W3 },
    manrope: { label: "Manrope", kind: "sans", spec: W3 },
    atkinson: { label: "Atkinson Hyperlegible Next", kind: "sans", spec: W3 },
    publicsans: { label: "Public Sans", kind: "sans", spec: W3 },
    fraunces: { label: "Fraunces", kind: "serif", spec: W3 },
    newsreader: { label: "Newsreader", kind: "serif", spec: W3 },
    sourceserif: { label: "Source Serif 4", kind: "serif", spec: W3 },
    literata: { label: "Literata", kind: "serif", spec: W3 },
    garamond: { label: "EB Garamond", kind: "serif", spec: W3 },
    plexserif: { label: "IBM Plex Serif", kind: "serif", spec: W3 },
    redhat: { label: "Red Hat Mono", kind: "mono", spec: "", bundled: true },
    jetbrains: { label: "JetBrains Mono", kind: "mono", spec: W3 },
    geistmono: { label: "Geist Mono", kind: "mono", spec: W3 },
    plexmono: { label: "IBM Plex Mono", kind: "mono", spec: W3 },
    firacode: { label: "Fira Code", kind: "mono", spec: W3 },
    martian: { label: "Martian Mono", kind: "mono", spec: W3 },
    victor: { label: "Victor Mono", kind: "mono", spec: W3 },
    sourcecode: { label: "Source Code Pro", kind: "mono", spec: W3 },
    azeret: { label: "Azeret Mono", kind: "mono", spec: W3 },
    splinemono: { label: "Spline Sans Mono", kind: "mono", spec: W3 },
    spacemono: { label: "Space Mono", kind: "mono", spec: "wght@400;700" },
    dmmono: { label: "DM Mono", kind: "mono", spec: "wght@400;500" },
    sometype: { label: "Sometype Mono", kind: "mono", spec: W3 },
  };
  for (const font of Object.values(FONTS)) {
    font.stack = `"${font.label}", ${font.kind === "mono" ? MONO : font.kind === "serif" ? SERIF : SANS}`;
  }
  // Retired picks map to the closest face in the new catalog.
  const LEGACY_FONTS = { system: "geist", serif: "newsreader", sysmono: "jetbrains" };
  const FONT_KINDS = { sans: "Sans", serif: "Serif", mono: "Mono" };
  const FONT_SUBSET = "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTtUuVvWwXxYyZz0123456789 .,:;'\"!?-()[]{}<>/=+*&#@";
  const fontLinks = new Set();
  function addStylesheet(key, href) {
    if (fontLinks.has(key) || !document.head) return;
    fontLinks.add(key);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.append(link);
  }
  function familyParam(font) {
    return "family=" + encodeURIComponent(font.label).replace(/%20/g, "+") + (font.spec ? ":" + font.spec : "");
  }
  function ensureFont(id) {
    const font = FONTS[id];
    if (!font || font.bundled) return;
    addStylesheet("font:" + id, `https://fonts.googleapis.com/css2?${familyParam(font)}&display=swap`);
  }
  function ensurePreviewFonts() {
    const families = Object.values(FONTS).filter((font) => !font.bundled)
      .map((font) => "family=" + encodeURIComponent(font.label).replace(/%20/g, "+")).join("&");
    addStylesheet("font:preview", `https://fonts.googleapis.com/css2?${families}&text=${encodeURIComponent(FONT_SUBSET)}&display=swap`);
  }
  // The page draws with one theme: the server stamps html[data-chat-view] from
  // the fuse_chat_view cookie (settings pages always the first theme). The
  // cookie is the choice the Layout control shows. Every theme's metadata
  // (static/themes/*/theme.json) arrives as #fuse-chat-themes; the first
  // entry is the default.
  let THEME_META = [];
  try {
    THEME_META = JSON.parse(document.getElementById?.("fuse-chat-themes")?.textContent || "[]");
  } catch (_) {}
  if (!Array.isArray(THEME_META) || !THEME_META.length) {
    THEME_META = [{ id: "console", label: "Console", font: "redhat", wallpaper: "none", scheme: "dark" }];
  }
  const VIEWS = THEME_META.map((meta) => meta.id);
  const VIEW_META = Object.fromEntries(THEME_META.map((meta) => [meta.id, meta]));
  const VIEW_COOKIE = "fuse_chat_view";
  const stampedView = document.documentElement.dataset.chatView;
  const renderedView = VIEWS.includes(stampedView) ? stampedView : VIEWS[0];
  let chosenView = VIEWS[0];
  try {
    const match = new RegExp(`(?:^|;\\s*)${VIEW_COOKIE}=([a-z]+)`).exec(document.cookie || "");
    if (match && VIEWS.includes(match[1])) chosenView = match[1];
  } catch (_) {}
  const DEFAULT_FONT = FONTS[VIEW_META[renderedView].font] ? VIEW_META[renderedView].font : "redhat";
  const THEME_VARS = {
    ink: "--ink",
    panel: "--panel",
    raise: "--raise",
    mist: "--mist",
    mute: "--mute",
    line: "--line",
    brass: "--brass",
    steel: "--steel",
    reason: "--reason",
    fault: "--fault",
    ok: "--ok",
    onBrass: "--on-brass",
    code: "--chat-code-bg",
  };
  const STORE_FONTS = "fuse.chat.fonts.v1";
  const STORE_SIZE = "fuse.chat.textsize.v1";
  const STORE_THEME = "fuse.chat.theme.v1";
  const STORE_COMPOSER = "fuse.chat.composer.v1";
  const COMPOSER_DEFAULTS = { spell: true, palettes: true, history: true, dial: true };
  const STORE_CUSTOM = "fuse.chat.custom.v1";
  const STORE_BACKDROP = "fuse.chat.backdrop.v1";
  const STORE_STRENGTH = "fuse.chat.backdrop.strength.v1";
  // Each theme keeps its own palette, backdrop and strength (and the palette a
  // light wallpaper replaced): { [view]: { palette, backdrop, strength, restore } }.
  // The global keys above are read once, as every theme's first value.
  const STORE_VIEWS = "fuse.chat.views.v1";
  // A light wallpaper (or a theme's sun toggle) switches a dark palette to this.
  const LIGHT_PALETTE = "paper";
  const SIZE_MIN = 30;
  const SIZE_MAX = 130;
  const SIZE_DEFAULT = 100;
  const STRENGTH_MIN = 12;
  const STRENGTH_MAX = 55;
  const STRENGTH_DEFAULT = 28;
  const html = document.documentElement;
  let theme = "fuse";
  let textScale = SIZE_DEFAULT;
  let selectedFont = "";
  let custom = { ink: "#000000", brass: "#a5d8ff" };
  let backdrop = "none";
  let strength = STRENGTH_DEFAULT;
  let views = {};
  let fontFilter = "all";
  let fontHover = "";

  function clampHex(value, fallback) {
    const raw = String(value || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
      const h = raw.slice(1);
      return `#${h[0]}${h[0]}${h[1]}${h[1]}${h[2]}${h[2]}`.toLowerCase();
    }
    return fallback;
  }
  function hexToRgb(hex) {
    return {
      r: parseInt(hex.slice(1, 3), 16),
      g: parseInt(hex.slice(3, 5), 16),
      b: parseInt(hex.slice(5, 7), 16),
    };
  }
  function rgbToHex(r, g, b) {
    const n = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return `#${n(r)}${n(g)}${n(b)}`;
  }
  function mix(a, b, t) {
    const A = hexToRgb(a);
    const B = hexToRgb(b);
    return rgbToHex(A.r + (B.r - A.r) * t, A.g + (B.g - A.g) * t, A.b + (B.b - A.b) * t);
  }
  function luma(hex) {
    const { r, g, b } = hexToRgb(hex);
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  }
  function buildCustom(ink, brass) {
    const dark = luma(ink) < 0.48;
    const mist = dark ? mix(ink, "#f4f1ea", 0.92) : mix(ink, "#161410", 0.84);
    const mute = dark ? mix(mist, ink, 0.45) : mix(mist, ink, 0.38);
    const panel = dark ? mix(ink, mist, 0.07) : mix(ink, "#1c1b18", 0.05);
    const raise = dark ? mix(ink, mist, 0.13) : mix(ink, "#1c1b18", 0.1);
    const line = dark ? mix(ink, mist, 0.16) : mix(ink, "#1c1b18", 0.14);
    const steel = mix(brass, dark ? "#7ec8e8" : "#2f6a4a", 0.38);
    const reason = mix(mist, steel, 0.28);
    const fault = dark ? "#f29d8e" : "#c24b40";
    const ok = mix(brass, dark ? "#8fcdb0" : "#2f7a48", 0.4);
    return {
      label: "Custom",
      scheme: dark ? "dark" : "light",
      ink,
      panel,
      raise,
      mist,
      mute,
      line,
      brass,
      steel,
      reason,
      fault,
      ok,
      onBrass: luma(brass) < 0.55 ? "#ffffff" : "#121212",
      code: dark ? mix(ink, "#000000", 0.35) : mix(ink, "#ffffff", 0.18),
    };
  }

  const isPalette = (id) => id === "custom" || Object.prototype.hasOwnProperty.call(themes, id);
  const backdropKey = (id) => typeof id === "string" && /^[a-z][a-z0-9-]{0,31}$/.test(id) ? (LEGACY_BACKDROPS[id] || id) : "";
  const clampStrength = (value) => {
    const n = Number(value);
    return value != null && value !== "" && Number.isFinite(n) && n >= STRENGTH_MIN && n <= STRENGTH_MAX ? Math.round(n) : null;
  };
  function viewDefaults(id) {
    const meta = VIEW_META[id] || {};
    return {
      palette: meta.scheme === "light" ? LIGHT_PALETTE : "fuse",
      backdrop: backdropKey(meta.wallpaper) || "none",
      strength: STRENGTH_DEFAULT,
      restore: "",
    };
  }
  function cleanView(raw, base) {
    const entry = raw && typeof raw === "object" ? raw : {};
    return {
      palette: isPalette(entry.palette) ? entry.palette : base.palette,
      backdrop: backdropKey(entry.backdrop) || base.backdrop,
      strength: clampStrength(entry.strength) ?? base.strength,
      restore: isPalette(entry.restore) ? entry.restore : "",
    };
  }
  let legacy = {};
  try {
    const saved = localStorage.getItem(STORE_THEME);
    if (isPalette(saved)) legacy.palette = saved;
    const storedSize = Number(localStorage.getItem(STORE_SIZE));
    if (Number.isFinite(storedSize) && storedSize >= SIZE_MIN && storedSize <= SIZE_MAX) textScale = Math.round(storedSize / 5) * 5;
    const storedFonts = JSON.parse(localStorage.getItem(STORE_FONTS) || "{}");
    if (storedFonts && typeof storedFonts === "object") {
      const id = LEGACY_FONTS[storedFonts[renderedView]] || storedFonts[renderedView];
      if (FONTS[id]) selectedFont = id;
    }
    const storedCustom = JSON.parse(localStorage.getItem(STORE_CUSTOM) || "null");
    if (storedCustom && typeof storedCustom === "object") {
      custom = {
        ink: clampHex(storedCustom.ink, custom.ink),
        brass: clampHex(storedCustom.brass, custom.brass),
      };
    }
    let storedBackdrop = localStorage.getItem(STORE_BACKDROP);
    if (storedBackdrop && LEGACY_BACKDROPS[storedBackdrop]) {
      storedBackdrop = LEGACY_BACKDROPS[storedBackdrop];
      localStorage.setItem(STORE_BACKDROP, storedBackdrop);
    }
    if (storedBackdrop && Object.prototype.hasOwnProperty.call(BACKDROPS, storedBackdrop)) legacy.backdrop = storedBackdrop;
    const storedStrength = clampStrength(localStorage.getItem(STORE_STRENGTH));
    if (storedStrength != null) legacy.strength = storedStrength;
  } catch (_) {}
  // First run of per-theme looks: every theme starts from the old global
  // values (or its metadata defaults where there were none). After that a
  // theme added later starts from its own defaults.
  let storedViews = null;
  try { storedViews = JSON.parse(localStorage.getItem(STORE_VIEWS) || "null"); } catch (_) {}
  const migrating = !storedViews || typeof storedViews !== "object";
  for (const id of VIEWS) {
    const base = viewDefaults(id);
    views[id] = migrating ? cleanView(legacy, base) : cleanView(storedViews[id], base);
  }
  if (migrating) persistViews();
  const view = renderedView;
  theme = views[view].palette;
  backdrop = views[view].backdrop;
  strength = views[view].strength;

  // A saved key may name a wallpaper registered later (addWallpaper); until
  // then it draws nothing.
  function shownBackdrop() {
    return Object.prototype.hasOwnProperty.call(BACKDROPS, backdrop) ? backdrop : "none";
  }

  function fontFor() {
    return selectedFont || DEFAULT_FONT;
  }

  function specFor(id) {
    if (id === "custom") return buildCustom(custom.ink, custom.brass);
    return themes[id] || themes.fuse;
  }

  function clearThemeVars() {
    for (const prop of Object.values(THEME_VARS)) html.style.removeProperty(prop);
  }

  const SCENE_MARKUP = '<span data-chat-scene-wash></span><canvas data-chat-scene-fx hidden></canvas><span data-chat-scene-veil></span>';
  // Drawing lives in chat-backdrop.js (loaded after this file); until it
  // arrives, the scene simply shows its CSS layers.
  function syncFx(scene) {
    if (window.FuseBackdrop) window.FuseBackdrop.sync(scene, scene && !scene.hidden ? shownBackdrop() : "none");
  }

  // Only chat draws a backdrop; every other page keeps a solid theme ground.
  function ensureScene() {
    if (!document.body || !document.body.classList.contains("is-chat")) return null;
    let el = document.querySelector("[data-chat-scene]");
    if (!el) {
      el = document.createElement("div");
      el.className = "chat-scene";
      el.dataset.chatScene = "";
      el.setAttribute("aria-hidden", "true");
      el.innerHTML = SCENE_MARKUP;
      const stage = document.querySelector("body.is-chat .stage");
      if (stage) document.body.insertBefore(el, stage);
      else document.body.prepend(el);
    } else if (!el.querySelector("canvas[data-chat-scene-fx]") || !el.querySelector("[data-chat-scene-veil]")) {
      el.innerHTML = SCENE_MARKUP;
    }
    return el;
  }

  function apply() {
    const spec = specFor(theme);
    html.dataset.chatTheme = theme;
    if (renderedView === "console") html.dataset.chatConsole = "true";
    html.dataset.chatBackdrop = shownBackdrop();
    html.dataset.chatView = renderedView;
    ensureFont(fontFor());
    html.style.setProperty("--chat-text-scale", String(textScale / 100));
    // The reading font is the site font: it drives the --sans and --mono
    // tokens every rule reads, not just the transcript. Code keeps a fixed
    // pitch (--code): the chosen face when it is a mono, Red Hat Mono otherwise.
    const fontId = fontFor();
    const font = FONTS[fontId];
    html.dataset.chatFont = fontId;
    html.style.setProperty("--chat-view-font", font.stack);
    html.style.setProperty("--sans", font.stack);
    html.style.setProperty("--mono", font.stack);
    html.style.setProperty("--code", font.kind === "mono" ? font.stack : FONTS.redhat.stack);
    html.style.setProperty("--chat-backdrop-strength", String(strength / 100));
    if (theme === "custom") {
      for (const [key, prop] of Object.entries(THEME_VARS)) html.style.setProperty(prop, spec[key]);
    } else {
      clearThemeVars();
    }
    html.style.colorScheme = spec.scheme;
    html.dataset.chatScheme = spec.scheme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", spec.scheme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", spec.ink);
    const scene = ensureScene();
    if (scene) {
      scene.hidden = shownBackdrop() === "none";
      scene.dataset.kind = shownBackdrop();
      syncFx(scene);
    }
    if (typeof CustomEvent === "function" && document.dispatchEvent) {
      try { document.dispatchEvent(new CustomEvent("fuse-composer-metrics")); } catch (_) {}
      try { document.dispatchEvent(new CustomEvent("fuse-appearance")); } catch (_) {}
    }
  }
  apply();

  // Artifact pages get the theme as --fuse-* variables: ?theme=<scheme>,<hex>...
  // in app/artifacts.py THEME_NAMES order.
  const ARTIFACT_KEYS = ["ink", "panel", "raise", "mist", "mute", "line", "brass", "onBrass", "ok", "fault", "code"];
  function artifactTheme() {
    const spec = specFor(theme);
    return [spec.scheme, ...ARTIFACT_KEYS.map((key) => String(spec[key] || "").slice(1))].join(",");
  }

  function getComposerPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_COMPOSER) || "null");
      if (!raw || typeof raw !== "object") return { ...COMPOSER_DEFAULTS };
      return {
        spell: raw.spell !== false,
        palettes: raw.palettes !== false,
        history: raw.history !== false,
        dial: raw.dial !== false,
      };
    } catch (_) {
      return { ...COMPOSER_DEFAULTS };
    }
  }

  function setComposerPrefs(partial) {
    const next = { ...getComposerPrefs(), ...(partial || {}) };
    const value = {
      spell: next.spell !== false,
      palettes: next.palettes !== false,
      history: next.history !== false,
      dial: next.dial !== false,
    };
    try { localStorage.setItem(STORE_COMPOSER, JSON.stringify(value)); } catch (_) {}
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      const key = el.getAttribute("data-composer-pref");
      if (key && Object.prototype.hasOwnProperty.call(value, key)) el.checked = value[key];
    });
    if (typeof CustomEvent === "function" && document.dispatchEvent) {
      try { document.dispatchEvent(new CustomEvent("fuse-composer-pref", { detail: value })); } catch (_) {}
    }
    return value;
  }

  function persistFonts() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORE_FONTS) || "{}") || {};
      const fonts = {};
      for (const id of VIEWS) if (typeof stored[id] === "string") fonts[id] = stored[id];
      fonts[renderedView] = selectedFont;
      localStorage.setItem(STORE_FONTS, JSON.stringify(fonts));
    } catch (_) {}
  }

  // The server links the theme, so a switch saves the cookie and reloads.
  function setView(next) {
    if (!VIEWS.includes(next) || next === chosenView) return;
    if (typeof window.fuseUnsaved === "function" && window.fuseUnsaved()
      && !window.confirm("Switching the layout reloads the page. Unsaved work on it will be lost.")) return;
    const secure = location.protocol === "https:" ? "; secure" : "";
    document.cookie = `${VIEW_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax${secure}`;
    location.reload();
  }
  function persistViews() {
    try { localStorage.setItem(STORE_VIEWS, JSON.stringify(views)); } catch (_) {}
  }
  function persistTheme() {
    views[view].palette = theme;
    persistViews();
  }
  function persistCustom() {
    try { localStorage.setItem(STORE_CUSTOM, JSON.stringify(custom)); } catch (_) {}
  }
  function persistBackdrop() {
    views[view].backdrop = backdrop;
    views[view].strength = strength;
    persistViews();
  }

  function paintSwatches() {
    const themeHost = document.querySelector("[data-chat-theme-swatches]");
    if (themeHost && !themeHost.childElementCount) {
      const entries = [...Object.entries(themes), ["custom", { label: "Custom" }]];
      for (const [id, spec] of entries) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ap-theme";
        btn.dataset.chatTheme = id;
        btn.setAttribute("aria-pressed", "false");
        // A tiny window drawn in the theme's own colors: bar, prompt, text.
        btn.innerHTML = '<span class="ap-theme-art" aria-hidden="true"><b></b><i></i><i></i><em></em></span>' +
          `<span class="ap-theme-name">${spec.label}</span>`;
        themeHost.append(btn);
      }
    }
    const backHost = document.querySelector("[data-chat-backdrop-swatches]");
    if (backHost && !backHost.childElementCount) {
      for (const [id, spec] of Object.entries(BACKDROPS)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ap-backdrop";
        btn.dataset.chatBackdrop = id;
        btn.setAttribute("aria-pressed", "false");
        btn.innerHTML = `<i class="chat-backdrop-preview" data-kind="${id}" aria-hidden="true"></i><span>${spec.label}</span><small>${spec.hint}</small>`;
        backHost.append(btn);
      }
      window.FuseBackdrop?.previews(backHost);
    }
  }

  function paintFonts() {
    const grid = document.querySelector("[data-chat-font-grid]");
    if (!grid) return;
    if (!grid.childElementCount) {
      const card = (id, name, kind, stack) =>
        `<button type="button" class="ap-font" role="radio" aria-checked="false" data-font-id="${id}" data-font-kind="${kind}" style="--ap-face: ${stack.replace(/"/g, "&quot;")}">` +
        `<span class="ap-font-glyph" aria-hidden="true">Ag</span><span class="ap-font-name">${name}</span><small>${FONT_KINDS[kind] || kind}</small></button>`;
      grid.innerHTML = card("", "Default", "default", "inherit") +
        Object.entries(FONTS).map(([id, font]) => card(id, font.label, font.kind, font.stack)).join("");
    }
    const fallback = FONTS[DEFAULT_FONT];
    const selected = selectedFont || "";
    grid.querySelectorAll(".ap-font").forEach((btn) => {
      const id = btn.dataset.fontId;
      if (!id) {
        btn.style.setProperty("--ap-face", fallback.stack);
        btn.querySelector("small").textContent = fallback.label;
      }
      const on = id === selected;
      btn.setAttribute("aria-checked", String(on));
      btn.tabIndex = on ? 0 : -1;
      btn.hidden = fontFilter !== "all" && id !== "" && btn.dataset.fontKind !== fontFilter;
    });
    document.querySelectorAll("[data-chat-font-filter]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.chatFontFilter === fontFilter));
    });
    const sample = document.querySelector("[data-chat-font-sample]");
    if (sample) {
      const shown = fontHover !== "" && fontHover != null ? fontHover : fontFor();
      const font = FONTS[shown] || fallback;
      sample.style.fontFamily = font.stack;
      sample.dataset.fontLabel = font.label;
    }
  }

  function syncSwatches() {
    document.querySelectorAll("[data-chat-theme-swatches] [data-chat-theme]").forEach((btn) => {
      const id = btn.dataset.chatTheme;
      const spec = specFor(id);
      for (const key of ["ink", "panel", "raise", "mist", "mute", "line", "brass"]) {
        btn.style.setProperty(`--t-${key}`, spec[key]);
      }
      btn.setAttribute("aria-pressed", String(id === theme));
      btn.classList.toggle("is-on", id === theme);
    });
    document.querySelectorAll("[data-chat-backdrop-swatches] [data-chat-backdrop]").forEach((btn) => {
      const id = btn.dataset.chatBackdrop;
      btn.setAttribute("aria-pressed", String(id === shownBackdrop()));
      btn.classList.toggle("is-on", id === shownBackdrop());
    });
    document.querySelectorAll("[data-chat-view-choice]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.chatViewChoice === chosenView));
    });
    const customBox = document.querySelector("[data-chat-custom]");
    if (customBox) customBox.hidden = theme !== "custom";
    const ink = document.querySelector("[data-chat-custom-ink]");
    const brass = document.querySelector("[data-chat-custom-brass]");
    if (ink) ink.value = custom.ink;
    if (brass) brass.value = custom.brass;
    const strengthRange = document.querySelector("[data-chat-backdrop-strength]");
    const strengthOut = document.querySelector("[data-chat-backdrop-strength-value]");
    const strengthWrap = document.querySelector("[data-chat-backdrop-strength-wrap]");
    if (strengthRange) strengthRange.value = String(strength);
    if (strengthOut) strengthOut.textContent = `${strength}%`;
    if (strengthWrap) strengthWrap.hidden = shownBackdrop() === "none";
    document.querySelectorAll("[data-appearance-scope]").forEach((el) => {
      el.textContent = `Saved for this theme (${VIEW_META[view].label || view}).`;
    });
  }

  function syncUi() {
    paintSwatches();
    const range = document.querySelector("[data-chat-size]");
    if (range) range.value = String(textScale);
    const out = document.querySelector("[data-chat-size-value]");
    if (out) out.textContent = `${textScale}%`;
    paintFonts();
    const prefs = getComposerPrefs();
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      const key = el.getAttribute("data-composer-pref");
      if (key && Object.prototype.hasOwnProperty.call(prefs, key)) el.checked = prefs[key];
    });
    syncSwatches();
  }

  // A palette the user picks is theirs: a light wallpaper no longer owes it back.
  function setTheme(next) {
    theme = isPalette(next) ? next : "fuse";
    views[view].restore = "";
    persistTheme();
    apply();
    syncSwatches();
  }

  function setCustom(partial) {
    custom = {
      ink: clampHex(partial.ink, custom.ink),
      brass: clampHex(partial.brass, custom.brass),
    };
    theme = "custom";
    views[view].restore = "";
    persistCustom();
    persistTheme();
    apply();
    syncSwatches();
  }

  // A light wallpaper (its spec says scheme "light") moves a dark palette to
  // the light one and remembers it; leaving for None or a dark wallpaper
  // brings it back.
  function wallpaperScheme(id) {
    return (window.FuseBackdrop?.scheme?.(id)) || BACKDROPS[id]?.scheme || "";
  }
  function setBackdrop(next) {
    backdrop = Object.prototype.hasOwnProperty.call(BACKDROPS, next) ? next : "none";
    const entry = views[view];
    if (wallpaperScheme(backdrop) === "light") {
      if (specFor(theme).scheme !== "light") {
        entry.restore = theme;
        theme = LIGHT_PALETTE;
      }
    } else if (entry.restore) {
      theme = entry.restore;
      entry.restore = "";
    }
    entry.palette = theme;
    persistBackdrop();
    apply();
    syncSwatches();
  }

  function setStrength(next) {
    const value = Math.round(Number(next));
    if (!Number.isFinite(value)) return;
    strength = Math.min(STRENGTH_MAX, Math.max(STRENGTH_MIN, value));
    persistBackdrop();
    apply();
    const range = document.querySelector("[data-chat-backdrop-strength]");
    if (range) range.value = String(strength);
    const out = document.querySelector("[data-chat-backdrop-strength-value]");
    if (out) out.textContent = `${strength}%`;
  }

  // The theme's light/dark switch: dark keeps the palette to come back to.
  function setScheme(next) {
    if (next !== "light" && next !== "dark") return;
    if (specFor(theme).scheme === next) return;
    const entry = views[view];
    if (next === "light") {
      entry.restore = theme;
      theme = LIGHT_PALETTE;
    } else {
      theme = entry.restore && specFor(entry.restore).scheme === "dark" ? entry.restore : "fuse";
      entry.restore = "";
    }
    persistTheme();
    apply();
    syncSwatches();
  }

  // Registers a data-only wallpaper spec (docs/wallpapers.md) as a backdrop
  // and adds it to the catalog: the hook a wallpaper-making tool calls.
  // chat-backdrop.js validates it (validateWallpaper) and builds the scene.
  function addWallpaper(key, spec) {
    const engine = window.FuseBackdrop;
    if (!engine || typeof engine.register !== "function") return { ok: false, errors: ["the backdrop engine is not loaded"] };
    if (Object.prototype.hasOwnProperty.call(BACKDROPS, key) && !BACKDROPS[key].added) {
      return { ok: false, errors: [`key: "${key}" is a built-in backdrop`] };
    }
    let result;
    try { result = engine.register(key, spec); } catch (error) { result = { ok: false, errors: [String(error && error.message || error)] }; }
    if (!result || !result.ok) return { ok: false, errors: (result && result.errors) || ["invalid wallpaper"] };
    const name = String(spec.name || key).slice(0, 40);
    BACKDROPS[key] = { label: name, hint: "Wallpaper", scheme: spec.scheme === "light" ? "light" : "dark", added: true };
    const backHost = document.querySelector("[data-chat-backdrop-swatches]");
    if (backHost && backHost.childElementCount) {
      backHost.replaceChildren();
      paintSwatches();
    }
    apply();
    syncSwatches();
    if (typeof CustomEvent === "function" && document.dispatchEvent) {
      try { document.dispatchEvent(new CustomEvent("fuse-wallpapers", { detail: { key } })); } catch (_) {}
    }
    return { ok: true, errors: [] };
  }

  window.FuseChatAppearance = {
    syncUi,
    get artifactTheme() { return artifactTheme(); },
    get size() { return textScale; },
    get layout() { return chosenView; },
    setLayout: setView,
    setSize(next) {
      const value = Math.round(Number(next));
      if (!Number.isFinite(value)) return;
      textScale = Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round(value / 5) * 5));
      try { localStorage.setItem(STORE_SIZE, String(textScale)); } catch (_) {}
      apply();
      const range = document.querySelector("[data-chat-size]");
      if (range) range.value = String(textScale);
      const out = document.querySelector("[data-chat-size-value]");
      if (out) out.textContent = `${textScale}%`;
    },
    setFont(id) {
      selectedFont = id && FONTS[id] ? id : "";
      persistFonts();
      apply();
      syncUi();
    },
    setTheme,
    setBackdrop,
    setStrength,
    setScheme,
    addWallpaper,
    get themeId() { return view; },
    get palette() { return theme; },
    get scheme() { return specFor(theme).scheme; },
    get backdrop() { return shownBackdrop(); },
    // The data-only wallpapers (built-in presets and any added), for the /theme picker.
    get wallpapers() {
      return Object.entries(BACKDROPS)
        .filter(([id]) => window.FuseBackdrop?.isWallpaper?.(id))
        .map(([id, spec]) => ({ id, label: spec.label, hint: spec.hint, scheme: wallpaperScheme(id) || "dark" }));
    },
    getComposerPrefs,
    setComposerPrefs,
  };

  document.addEventListener("DOMContentLoaded", () => {
    // Bind wherever the controls are: chat's Appearance sheet or Settings ›
    // Appearance. Other pages only apply the saved look.
    const root = document.querySelector("[data-chat], [data-chat-theme-swatches]");
    const sizeRange = document.querySelector("[data-chat-size]");
    const fontGrid = document.querySelector("[data-chat-font-grid]");
    if (!root) {
      apply();
      return;
    }
    paintSwatches();
    document.querySelector("[data-chat-theme-swatches]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-theme]");
      if (!btn) return;
      setTheme(btn.dataset.chatTheme);
    });
    document.querySelectorAll("[data-chat-view-choice]").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.chatViewChoice));
    });
    document.querySelector("[data-chat-backdrop-swatches]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-backdrop]");
      if (!btn) return;
      setBackdrop(btn.dataset.chatBackdrop);
    });
    document.querySelector("[data-chat-custom-ink]")?.addEventListener("input", (event) => {
      setCustom({ ink: event.target.value, brass: custom.brass });
    });
    document.querySelector("[data-chat-custom-brass]")?.addEventListener("input", (event) => {
      setCustom({ ink: custom.ink, brass: event.target.value });
    });
    document.querySelector("[data-chat-backdrop-strength]")?.addEventListener("input", (event) => {
      setStrength(event.target.value);
    });
    sizeRange?.addEventListener("input", () => {
      window.FuseChatAppearance.setSize(sizeRange.value);
    });
    document.querySelectorAll("[data-size-step]").forEach((btn) => {
      btn.addEventListener("click", () => {
        window.FuseChatAppearance.setSize(textScale + Number(btn.getAttribute("data-size-step")) * 5);
      });
    });
    // Picking loads the full face; hovering or focusing a card previews it
    // in the sample line without committing.
    fontGrid?.addEventListener("click", (event) => {
      const btn = event.target.closest(".ap-font");
      if (!btn) return;
      fontHover = "";
      window.FuseChatAppearance.setFont(btn.dataset.fontId || null);
    });
    const preview = (event) => {
      const btn = event.target.closest?.(".ap-font");
      const next = btn ? btn.dataset.fontId || DEFAULT_FONT : "";
      if (next === fontHover) return;
      fontHover = next;
      if (next) ensureFont(next);
      paintFonts();
    };
    fontGrid?.addEventListener("pointerover", preview);
    fontGrid?.addEventListener("focusin", preview);
    fontGrid?.addEventListener("pointerleave", () => { fontHover = ""; paintFonts(); });
    fontGrid?.addEventListener("focusout", (event) => {
      if (!fontGrid.contains(event.relatedTarget)) { fontHover = ""; paintFonts(); }
    });
    fontGrid?.addEventListener("keydown", (event) => {
      const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (!(event.key in keys)) return;
      const cards = [...fontGrid.querySelectorAll(".ap-font:not([hidden])")];
      const at = cards.indexOf(document.activeElement);
      if (at < 0) return;
      event.preventDefault();
      const next = cards[(at + keys[event.key] + cards.length) % cards.length];
      next.focus();
      next.click();
    });
    document.querySelector("[data-chat-font-filters]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-font-filter]");
      if (!btn) return;
      fontFilter = btn.dataset.chatFontFilter;
      paintFonts();
    });
    // Card faces load only once the window is first opened.
    const menu = document.querySelector("[data-view-menu]");
    if (menu) {
      const loadOnOpen = new MutationObserver(() => {
        if (menu.hidden) return;
        ensurePreviewFonts();
        loadOnOpen.disconnect();
      });
      loadOnOpen.observe(menu, { attributes: true, attributeFilter: ["hidden"] });
    }
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      el.addEventListener("change", () => {
        const key = el.getAttribute("data-composer-pref");
        if (!key) return;
        setComposerPrefs({ [key]: el.checked });
      });
    });
    apply();
    syncUi();
  });
})();

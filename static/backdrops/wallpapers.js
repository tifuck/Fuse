// Wallpaper presets: plain spec data (see docs/wallpapers.md), each run
// through the wallpaper interpreter into an ordinary scene. Sprites face
// right; the interpreter mirrors them when they travel left.

import { wallpaperScene } from "./wallpaper.js";

export const SPARK = {
  version: 1,
  name: "Spark",
  scheme: "dark",
  seed: 7101,
  cell: 16,
  fps: 15,
  glyphs: "*+x.,:'·",
  background: {
    type: "radial",
    stops: ["#3b1e0f", "#21120a", "#130b07"],
    at: [0.8, 0.22],
    grid: { color: "#7a4528", alpha: 0.45, every: 2, edge: 0.16 },
  },
  ambient: { glyphs: ".,'·", colors: ["#6b3a20", "#8a4a26"], count: [20, 70], alpha: [0.1, 0.5], period: [3, 8] },
  sprites: {
    flash: { rows: [" c ", "cCc", " c "], legend: { C: ["*", "#ffd27a", "#7a2e05"], c: ["+", "#ff9a3a", "#5a1e04"] } },
    hot: { rows: ["o"], legend: { o: ["*", "#ffb347", "#6a2404"] } },
    warm: { rows: ["o"], legend: { o: ["+", "#ff7a22", "#4a1804"] } },
    cool: { rows: ["o"], legend: { o: ["x", "#c4461a", "#2e0e04"] } },
    ash: { rows: ["o"], legend: { o: [".", null, "#ff9a50"] } },
    ember: { rows: ["o"], legend: { o: [".", "#d4561c", "#ffc080"] } },
    mote: { rows: ["o"], legend: { o: ["·", null, "#ff8a40"] } },
  },
  layers: [
    { type: "particles", sprite: ["ember", "mote"], mode: "stream", region: [0, 0.5, 1, 1], rate: 2.4, life: [7, 12], speed: [0.4, 0.9], angle: [-112, -68], sway: 1.4, alpha: [0.55, 1], depth: 0.2 },
    { type: "particles", sprite: ["hot", "warm", "cool", "ash"], mode: "burst", region: [0.66, 0.2, 0.8, 0.34], period: 4.4, burst: 14, life: [1.4, 2.2], speed: [3, 6], drag: 2.4, gravity: 0.9, depth: 0.4 },
    { type: "particles", sprite: ["flash"], mode: "burst", region: [0.74, 0.1, 0.88, 0.24], period: 3.3, burst: 1, life: [0.5, 0.7], speed: [0, 0] },
    { type: "particles", sprite: ["hot", "warm", "warm", "cool", "ash"], mode: "burst", region: [0.74, 0.1, 0.88, 0.24], period: 3.3, burst: 30, life: [1.8, 2.9], speed: [2, 13], drag: 2, gravity: 1.1 },
  ],
};

const CLOUD = { "%": ["%", "#ffffff", "#8d9ca8"], ";": [";", "#ffffff", "#98a7b2"], ":": [":", "#f6f9fb", "#9aa9b4"], t: ["t", "#ffffff", "#84949f"], s: [":", "#dbe7ef", "#9db0bd"] };

export const CLOUDDRIFT = {
  version: 1,
  name: "Cloud Drift",
  scheme: "light",
  seed: 2207,
  cell: 16,
  fps: 12,
  glyphs: "%;:t^v.,'",
  background: {
    type: "linear",
    stops: ["#8fcaee", "#b5ddf5", "#dff1fb"],
    grid: { color: "#4f8fb8", alpha: 0.4, every: 2, edge: 0.18 },
  },
  ambient: { glyphs: ".,'", colors: ["#ffffff"], count: [10, 40], alpha: [0.15, 0.6], period: [3, 7], region: [0, 0, 1, 0.8] },
  sprites: {
    big: {
      rows: [
        "         %t:; t:;%t:",
        "        %t:;%t:;%t:;% :",
        "       %t:;%t:;%t:;%t:;%t",
        "    :;%t:;%t:;%t:;%t:;%t:;",
        "   :;%t:;%t:;%t:;%t:;%t:;%t",
        " t:;%t:;%t:;%t:;%t:;%t:;%t:",
        " :;%t:;%t:;%t:;%t:;%t:;%t:;%",
        " ssssssssssssssssssssssssss",
      ],
      legend: CLOUD,
    },
    mid: {
      rows: [
        "      %t:;%",
        "     %t:;%t:;%t",
        "  :;%t:;%t:;%t:;",
        "  ;%t:;%t:;%t:;%t",
        "  %t:;%t:;%t:;%t:;",
        "  ssssssssssssssss",
      ],
      legend: CLOUD,
    },
    small: {
      rows: [
        "    t:;%",
        "  %t:;%t:;",
        " %t:;%t:;%",
        " ssssssssss",
      ],
      legend: CLOUD,
    },
    birdUp: { rows: ["^"], legend: { "^": ["^", null, "#3f5a6b"] } },
    birdDown: { rows: ["v"], legend: { v: ["v", null, "#3f5a6b"] } },
  },
  layers: [
    { sprite: ["small", "mid"], count: [3, 7], depth: 0.6, region: [0, 0, 1, 0.6], alpha: [0.85, 1], motions: [{ type: "drift", speed: [0.18, 0.3], angle: [0, 0] }, { type: "bob", amp: [0.1, 0.3], period: [8, 14] }] },
    { sprite: ["mid", "big"], count: [2, 4], depth: 0.3, region: [0, 0.12, 1, 0.75], alpha: [0.95, 1], motions: [{ type: "drift", speed: [0.35, 0.5], angle: [0, 0] }, { type: "bob", amp: [0.15, 0.4], period: [9, 15] }] },
    { sprite: ["birdUp", "birdDown"], frameRate: 3, count: [3, 7], region: [0.05, 0.12, 0.95, 0.5], motions: [{ type: "drift", speed: [0.8, 1.4], angle: [-8, 4] }, { type: "bob", amp: [0.4, 1], period: [3, 5] }] },
    { sprite: ["big", "mid"], count: [1, 3], depth: 0, region: [0, 0.35, 1, 1], motions: [{ type: "drift", speed: [0.55, 0.8], angle: [0, 0] }, { type: "bob", amp: [0.2, 0.5], period: [10, 16] }] },
  ],
};

const SHELL = {
  R: [":", "#a8731e", "#f2ca62"],
  S: ["%", "#dca938", "#8a5a14"],
  T: ["t", "#ecc45c", "#8a5a14"],
  C: [":", "#cc962e", "#6e440c"],
  K: [":", "#5aa391", "#245a50"],
  E: ["o", "#5aa391", "#0c2a27"],
  M: [",", "#4a9282", "#173f38"],
  F: [";", "#1d5a58", "#7cc2ae"],
};
const REEF = { p: ["S", "#e8698c", "#7a1f3a"], q: ["s", "#f08aa6", "#8a2846"], r: ["x", "#d63f4e", "#5e0f1a"], o: ["o", "#f2a0b4", "#8a2846"] };

export const HONULAGOON = {
  version: 1,
  name: "Honu Lagoon",
  scheme: "dark",
  seed: 4242,
  cell: 16,
  fps: 12,
  glyphs: "%t:;.,oOsSx><",
  background: {
    type: "linear",
    stops: ["#2b9e99", "#177c80", "#0f5f69", "#0a4553"],
    grid: { color: "#8fe0d6", alpha: 0.3, every: 2, edge: 0.18 },
  },
  ambient: { glyphs: ".,:", colors: ["#7fd6cf", "#a6ece4"], count: [30, 90], alpha: [0.08, 0.4], period: [3, 9] },
  sprites: {
    turtleDown: {
      rows: [
        "      FFFFF",
        "      FKKKKFFFF",
        "       FKKKKKKKFFF",
        "  FFFF  FKKRRRRRKKFF",
        "   FKKFFRRSSSCSSSRRF",
        "    FKRSSCSSSCSSSCSSR",
        "     RSTSCSTSCSTSCSTSR",
        "    RCCCCCCCCCCCCCCCCCR  K",
        "    RSSCSTSCSTSCSTSCSSRKKKEK",
        "  KKRSSCSTSCSTSCSTSCSSRKKKKM",
        "    RSSCSTSCSTSCSTSCSSRKKKEK",
        "    RCCCCCCCCCCCCCCCCCR  K",
        "     RSTSCSTSCSTSCSTSR",
        "    FKRSSCSSSCSSSCSSR",
        "   FKKFFRRSSSCSSSRRF",
        "  FFFF  FKKRRRRRKKFF",
        "       FKKKKKKKFFF",
        "      FKKKKFFFF",
        "      FFFFF",
      ],
      legend: SHELL,
    },
    turtleUp: {
      rows: [
        "                   FFF",
        "                 FFKKF",
        "                FKKKKF",
        "   FF      RRRRRKKKKF",
        "   FKFFFRRSSSCSSSRRKF",
        "    FKRSSCSSSCSSSCSSR",
        "     RSTSCSTSCSTSCSTSR",
        "    RCCCCCCCCCCCCCCCCCR  K",
        "    RSSCSTSCSTSCSTSCSSRKKKEK",
        "  KKRSSCSTSCSTSCSTSCSSRKKKKM",
        "    RSSCSTSCSTSCSTSCSSRKKKEK",
        "    RCCCCCCCCCCCCCCCCCR  K",
        "     RSTSCSTSCSTSCSTSR",
        "    FKRSSCSSSCSSSCSSR",
        "   FKFFFRRSSSCSSSRRKF",
        "   FF      RRRRRKKKKF",
        "                FKKKKF",
        "                 FFKKF",
        "                   FFF",
      ],
      legend: SHELL,
    },
    fan: { rows: ["p q p", "pq qp", " ppp ", "  q  ", "  p  "], legend: REEF },
    branch: { rows: ["r   r", "rr rr", " rrr ", "  r  "], legend: REEF },
    anemone: { rows: ["o o o", "qopoq", " ppp "], legend: REEF },
    fish: { rows: [" qq ", ">qqqe", " qq "], legend: { ">": [">", null, "#ffb3c4"], q: ["s", "#f27a9a", "#7a1a38"], e: ["o", "#f7a8bc", "#4a0c20"] } },
    bub1: { rows: ["o"], legend: { o: [".", null, "#e4fffb"] } },
    bub2: { rows: ["o"], legend: { o: ["o", null, "#e4fffb"] } },
    bub3: { rows: ["o"], legend: { o: ["O", null, "#ffffff"] } },
  },
  layers: [
    { sprite: ["turtleDown", "turtleUp"], frameRate: 0.8, count: [0, 1], depth: 0.7, region: [0, 0.04, 1, 0.42], alpha: [0.7, 0.7], motions: [{ type: "swim", speed: [0.6, 0.8], climb: [0.15, 0.25], amp: [0.3, 0.5], period: [7, 9] }] },
    { type: "particles", sprite: ["bub1", "bub2", "bub3"], mode: "stream", region: [0, 0.55, 1, 1], rate: 1.8, life: [6, 10], speed: [1.2, 2.2], angle: [-96, -84], sway: 0.8, alpha: [0.7, 1] },
    { sprite: "fish", count: [3, 8], region: [0, 0.45, 1, 0.85], motions: [{ type: "swim", speed: [1.5, 2.6], amp: [0.4, 0.9], period: [3, 5] }] },
    { sprite: ["fan", "branch", "anemone"], count: [4, 12], region: [0, 0.9, 1, 1], motions: [{ type: "bob", axis: "x", amp: [0.1, 0.25], period: [3, 5] }] },
    { sprite: ["turtleDown", "turtleUp"], frameRate: 0.8, count: [1, 1], region: [0.02, 0.06, 0.98, 0.86], motions: [{ type: "swim", speed: [1.1, 1.3], climb: [0.45, 0.6], amp: [0.4, 0.6], period: [6, 8] }] },
  ],
};

const KOI = { W: [":", "#f7f2e6", "#c2b69c"], O: ["%", "#f0782a", "#9a3a08"], G: ["%", "#f2b33a", "#9a6408"], K: ["s", "#22241f", "#6e7166"], T: ["<", "#e9a77a", "#8a3c10"], f: ["'", "#cfe0d2", "#7a8f80"], E: ["o", "#f7f2e6", "#22241f"], e: ["o", "#f0782a", "#2a0e02"] };

export const KOIPOND = {
  version: 1,
  name: "Koi Pond",
  scheme: "dark",
  seed: 1188,
  cell: 16,
  fps: 12,
  glyphs: "%:;s<,o~-.'",
  background: {
    type: "radial",
    stops: ["#3e8457", "#2a6646", "#174433"],
    at: [0.5, 0.45],
    grid: { color: "#9fdcae", alpha: 0.25, every: 2, edge: 0.14 },
  },
  ambient: { glyphs: "~-.", colors: ["#8fd1a0", "#b7e6c1"], count: [30, 90], alpha: [0.08, 0.35], period: [3, 8] },
  sprites: {
    kohaku: { rows: ["           ff    ", "      WWOOOWWW   ", "TT  WWWOOOOWWOOO ", " TTWWWWWOOWWWOOOE", "TT  WWWOOWWWWOOO ", "      WWWOOOWW   ", "           ff    "], legend: KOI },
    showa: { rows: ["           ff    ", "      KWOOKWWO   ", "TT  WKKOOOWKKOOO ", " TTWWKWOOKKWWOOOe", "TT  WWKOOWKKWOOK ", "      WKOOOWKW   ", "           ff    "], legend: KOI },
    ogon: { rows: ["           ff    ", "      GGGOGGG    ", "TT  GGGGOGGGGGGG ", " TTGGGGGGGOGGGGGe", "TT  GGGOGGGGGGGG ", "      GGGGOGG    ", "           ff    "], legend: KOI },
    pad: { rows: ["  LLL    ", " LLlLL LL", "LLLLlLLLL", "LLlLLLlLL", " LLLLlLL ", "  LLLLL  "], legend: { L: [";", "#4f9c40", "#2a5e20"], l: [":", "#5fb04c", "#2a5e20"] } },
    bloom: { rows: ["  LLL    ", " LLLpL LL", "LLLpPpLLL", "LLLLpLLLL", " LLLLLLL ", "  LLLLL  "], legend: { L: [";", "#4f9c40", "#2a5e20"], p: ["%", "#f4a6c4", "#a84a72"], P: ["o", "#ffe07a", "#9a6a08"] } },
    petal: { rows: ["p"], legend: { p: [",", "#f7b3c8", "#c45f80"] } },
    petal2: { rows: ["pq"], legend: { p: ["'", "#f7b3c8", "#c45f80"], q: [",", "#fbd3df", "#c45f80"] } },
  },
  layers: [
    { sprite: ["kohaku", "showa", "ogon", "kohaku"], count: [3, 7], depth: 0.25, region: [0, 0.05, 1, 0.95], alpha: [0.85, 0.95], motions: [{ type: "swim", speed: [0.9, 1.6], amp: [0.8, 2], period: [5, 9], climb: [0, 0.3] }] },
    { sprite: ["pad", "pad", "bloom"], count: [3, 9], region: [0, 0, 1, 1], motions: [{ type: "orbit", radius: [0.2, 0.5], period: [14, 22], squash: 0.6 }] },
    { sprite: ["petal", "petal2"], count: [3, 9], region: [0, 0, 1, 1], motions: [{ type: "drift", speed: [0.35, 0.7], angle: [8, 28] }, { type: "bob", amp: [0.3, 0.8], period: [4, 7] }] },
  ],
};

const JELLY = { p: [":", "#e78ad0", "#a24a92"], P: ["o", "#f5b4e4", "#b5579f"], v: ["s", "#9b6be0", "#ecdfff"], t: [";", "#5d3f9e", "#f2b8ec"], ";": [";", null, "#e2b8ff"], ":": [":", null, "#f7b6e4"], ",": [",", null, "#d6b2ff"] };

export const JELLYFISH = {
  version: 1,
  name: "Jellyfish",
  scheme: "dark",
  seed: 5150,
  cell: 16,
  fps: 12,
  glyphs: ":;,osx.'·",
  background: {
    type: "radial",
    stops: ["#1b2668", "#0f1544", "#070a25"],
    at: [0.5, 0.25],
    grid: { color: "#6a78d8", alpha: 0.25, every: 2, edge: 0.15 },
  },
  ambient: { glyphs: ".·'", colors: ["#8a9cff", "#c3a8ff"], count: [30, 100], alpha: [0.05, 0.4], period: [3, 9] },
  sprites: {
    open: { rows: ["   ppp   ", " ppPpPpp ", "pPpppppPp", "ppPpppPpp", "vvvvvvvvv", " t  t  t ", " ;  :  ; ", "t  t t  t", ": ;   ; :", " ,  ,  , "], legend: JELLY },
    shut: { rows: ["   ppp   ", "  pPpPp  ", " pPpppPp ", " ppPpPpp ", "  vvvvv  ", "  t t t  ", "  ; : ;  ", " t  t  t ", " :  ;  : ", "  , , ,  "], legend: JELLY },
    mote: { rows: ["o"], legend: { o: ["·", null, "#9fb0ff"] } },
  },
  layers: [
    { sprite: ["open", "shut"], frameRate: 0.7, count: [3, 7], depth: 0.5, alpha: [0.7, 0.85], motions: [{ type: "rise", speed: [0.3, 0.6], sway: [0.4, 0.9], period: [6, 10] }, { type: "pulse", scale: [0.94, 1.04], period: [2.5, 3.5] }] },
    { type: "particles", sprite: ["mote"], mode: "stream", region: [0, 0, 1, 1], rate: 2, life: [6, 12], speed: [0.1, 0.4], angle: [-120, -60], sway: 1, alpha: [0.3, 0.7] },
    { sprite: ["open", "shut"], frameRate: 0.7, count: [2, 5], alpha: [0.72, 0.9], motions: [{ type: "rise", speed: [0.5, 0.9], sway: [0.6, 1.2], period: [7, 11] }, { type: "pulse", scale: [0.93, 1.05], period: [2.5, 3.5] }] },
  ],
};

const BOAT = { "^": ["^", "#ffffff", "#9aa6ad"], "/": ["/", "#ffffff", "#9aa6ad"], "\\": ["\\", "#ffffff", "#9aa6ad"], s: [";", "#ffffff", "#bcc6cc"], h: ["%", "#efeadf", "#b3ab98"], u: [":", "#d8d1c0", "#9f9682"], w: ["~", null, "#0d5862"] };

export const PAPERBOATS = {
  version: 1,
  name: "Paper Boats",
  scheme: "dark",
  seed: 3303,
  cell: 16,
  fps: 12,
  glyphs: "~-^/\\;%:.'",
  background: {
    type: "linear",
    stops: ["#2aa0a6", "#168089", "#0c5f6b"],
    grid: { color: "#9fe6e6", alpha: 0.25, every: 2, edge: 0.15 },
  },
  ambient: { glyphs: ".'", colors: ["#d6fbfb"], count: [15, 50], alpha: [0.1, 0.55], period: [2, 5] },
  sprites: {
    wave: { rows: ["~-~~ -~~~-  ~~-~"], legend: { "~": ["~", null, "#8fe0e2"], "-": ["-", null, "#6fcbd0"] } },
    swell: { rows: ["~~~ ~~-~~ ~~~"], legend: { "~": ["~", null, "#b9f0ee"], "-": ["-", null, "#8fe0e2"] } },
    crest: { rows: ["~~-~~~ ~~~-~~~~ -~~"], legend: { "~": ["~", "#2fb2b6", "#e0fbfa"], "-": ["-", "#249aa0", "#a9e7e6"] } },
    boat: { rows: ["      ^      ", "     /s\\     ", "    /sss\\    ", "   /sssss\\   ", "hhhhhhhhhhhhh", " hhhhhhhhhhh ", "  uuuuuuuuu  ", "   wwwwwww   "], legend: BOAT },
    dinghy: { rows: ["  ^  ", " /s\\ ", "hhhhh", " uuu ", " www "], legend: BOAT },
  },
  layers: [
    { sprite: ["wave", "swell"], count: [10, 34], depth: 0.5, alpha: [0.4, 0.75], motions: [{ type: "drift", speed: [0.2, 0.45], angle: [0, 0] }, { type: "bob", amp: [0.15, 0.3], period: [3, 5] }] },
    { sprite: "dinghy", count: [1, 4], depth: 0.35, region: [0, 0.1, 1, 0.6], motions: [{ type: "drift", speed: [0.25, 0.45], angle: [0, 0] }, { type: "bob", amp: [0.2, 0.35], period: [2.5, 3.5] }] },
    { sprite: ["crest", "swell", "crest"], count: [6, 18], alpha: [0.75, 1], motions: [{ type: "drift", speed: [0.4, 0.7], angle: [0, 0] }, { type: "bob", amp: [0.2, 0.4], period: [2.5, 4] }] },
    { sprite: "boat", count: [1, 4], region: [0, 0.35, 1, 0.95], motions: [{ type: "drift", speed: [0.45, 0.75], angle: [0, 0] }, { type: "bob", amp: [0.3, 0.5], period: [2.5, 3.5] }] },
  ],
};

const GRASS = { "|": ["|", "#06120b", "#1d4a2c"], "/": ["/", "#06120b", "#1d4a2c"], "\\": ["\\", "#06120b", "#1d4a2c"], v: ["v", "#06120b", "#1d4a2c"], g: [";", "#050e08", "#14331f"] };

export const FIREFLIES = {
  version: 1,
  name: "Fireflies",
  scheme: "dark",
  seed: 6611,
  cell: 16,
  fps: 12,
  glyphs: "o*+.|/\\v;'",
  background: {
    type: "radial",
    stops: ["#1d4027", "#102618", "#08150e"],
    at: [0.5, 1],
    grid: { color: "#5f9a6a", alpha: 0.22, every: 2, edge: 0.14 },
  },
  ambient: { glyphs: ".'", colors: ["#3f6e4a", "#5a8a5a"], count: [20, 60], alpha: [0.1, 0.45], period: [3, 8], region: [0, 0, 1, 0.8] },
  sprites: {
    fly: { rows: ["o"], legend: { o: ["o", "#e2fb66", "#4e6c08"] } },
    spark: { rows: ["o"], legend: { o: ["*", null, "#f0ff9a"] } },
    glow: { rows: [" . ", ".o.", " . "], legend: { o: ["+", "#f0ff7a", "#56720c"], ".": [".", "#4f6e1c", "#d8f25c"] } },
    halo: { rows: [" ... ", ".:o:.", " ... "], legend: { o: ["o", "#f4ff8a", "#4e6c08"], ":": ["+", "#b8d84a", "#f8ffb0"], ".": [".", null, "#d8f25c"] } },
    clump: {
      rows: [
        "   /    |  \\  ",
        " \\ |  / |\\ | /",
        " |\\|/ | ||/|/ ",
        "\\|||v/|\\||||v|",
        "gggggggggggggg",
        "gggggggggggggg",
        "gggggggggggggg",
        "gggggggggggggg",
        "gggggggggggggg",
        "gggggggggggggg",
      ],
      legend: GRASS,
    },
    tuft: {
      rows: [
        "  |   /   ",
        "\\ |\\ /| / ",
        "|v||/||v| ",
        "gggggggggg",
        "gggggggggg",
        "gggggggggg",
        "gggggggggg",
        "gggggggggg",
        "gggggggggg",
        "gggggggggg",
      ],
      legend: GRASS,
    },
  },
  layers: [
    { sprite: ["fly", "spark", "glow"], count: [8, 22], depth: 0.5, region: [0, 0.05, 1, 0.8], motions: [{ type: "drift", speed: [0.05, 0.2], angle: [-180, 180] }, { type: "orbit", radius: [1, 3], period: [10, 18] }, { type: "twinkle", min: 0, period: [2, 4], flicker: 0.25 }] },
    { sprite: ["clump", "tuft"], count: [6, 14], region: [0, 0.86, 1, 1], motions: [{ type: "bob", axis: "x", amp: [0.1, 0.3], period: [3, 5] }] },
    { sprite: ["halo", "glow", "fly"], count: [6, 16], region: [0, 0.1, 1, 0.9], motions: [{ type: "drift", speed: [0.1, 0.3], angle: [-180, 180] }, { type: "orbit", radius: [1.5, 4], period: [8, 14] }, { type: "bob", amp: [0.5, 1.2], period: [3, 6] }, { type: "twinkle", min: 0.05, period: [1.5, 3.5], flicker: 0.2 }] },
  ],
};

export const MOONLIT = {
  version: 1,
  name: "Moonlit",
  scheme: "dark",
  seed: 9091,
  cell: 16,
  fps: 12,
  glyphs: "%:~-.+*'·",
  background: {
    type: "linear",
    stops: ["#050a22", "#0c1840", "#1a2f68", "#0a1636"],
    grid: { color: "#7a8fd0", alpha: 0.22, every: 2, edge: 0.14 },
  },
  ambient: { glyphs: ".·'", colors: ["#c8d2ff", "#fff3c8"], count: [30, 110], alpha: [0.05, 0.55], period: [2, 6], region: [0, 0, 1, 0.62] },
  sprites: {
    moon: {
      rows: ["    mmmMM", "   mmMM", "  mmmM", " mmmMM", "mmmmM", "mmmmM", "mmmmM", "mmmmMM", "mmmmmM", " mmmmMM", "  mmmmmMMMM", "   mmmmmmM", "    mmmmm"],
      legend: { m: ["%", "#f7edc0", "#c4b16e"], M: [":", "#e6d79a", "#a8955a"] },
    },
    star: { rows: ["o"], legend: { o: ["+", null, "#f4f1dc"] } },
    star2: { rows: ["o"], legend: { o: ["*", null, "#fff6cf"] } },
    bright: { rows: [" . ", ".*.", " . "], legend: { "*": ["*", "#fff8d6", "#8a7a30"], ".": [".", null, "#fff8d6"] } },
    ripple: { rows: ["~-~~ -~~"], legend: { "~": ["~", null, "#6f8fd8"], "-": ["-", null, "#4f6fb8"] } },
    swell: { rows: ["~~-~~~ ~~"], legend: { "~": ["~", "#1c3474", "#8fa8e8"], "-": ["-", "#162c66", "#6f8fd8"] } },
    glint: { rows: ["-~~~-"], legend: { "~": ["~", "#e6d79a", "#7a6a30"], "-": ["-", null, "#f7edc0"] } },
    glint2: { rows: ["~~"], legend: { "~": ["~", "#f2e6b0", "#8a7a40"] } },
  },
  layers: [
    { sprite: ["star", "star2", "star"], count: [12, 40], region: [0, 0, 1, 0.62], motions: [{ type: "twinkle", min: 0, period: [2, 5], flicker: 0.3 }] },
    { sprite: "bright", count: [1, 4], region: [0, 0.02, 0.7, 0.5], motions: [{ type: "twinkle", min: 0.3, period: [3, 6] }, { type: "pulse", scale: [0.85, 1.1], period: [3, 5] }] },
    { sprite: "moon", count: [1, 1], region: [0.74, 0.06, 0.84, 0.28], motions: [{ type: "bob", amp: [0.2, 0.3], period: [14, 18] }] },
    { sprite: ["ripple", "swell"], count: [14, 44], region: [0, 0.7, 1, 1], alpha: [0.6, 1], motions: [{ type: "drift", speed: [0.15, 0.35], angle: [0, 0] }, { type: "bob", amp: [0.1, 0.2], period: [3, 5] }] },
    { sprite: ["glint", "glint2"], count: [5, 9], region: [0.74, 0.72, 0.84, 0.99], motions: [{ type: "bob", axis: "x", amp: [0.4, 0.9], period: [2, 3.5] }, { type: "twinkle", min: 0.25, period: [1.5, 3] }] },
  ],
};

export const WALLPAPERS = { spark: SPARK, clouddrift: CLOUDDRIFT, honulagoon: HONULAGOON, koipond: KOIPOND, jellyfish: JELLYFISH, paperboats: PAPERBOATS, fireflies: FIREFLIES, moonlit: MOONLIT };

export const spark = wallpaperScene(SPARK);
export const clouddrift = wallpaperScene(CLOUDDRIFT);
export const honulagoon = wallpaperScene(HONULAGOON);
export const koipond = wallpaperScene(KOIPOND);
export const jellyfish = wallpaperScene(JELLYFISH);
export const paperboats = wallpaperScene(PAPERBOATS);
export const fireflies = wallpaperScene(FIREFLIES);
export const moonlit = wallpaperScene(MOONLIT);

// Scene: Circuit City. A circuit board seen straight down at night, read
// as a city map: chips are blocks with rows of pins, traces are streets
// that turn at 45 and 90 degrees, vias are plazas, a capacitor stands like
// a rotunda. Packets of light run the traces like traffic (data words move
// down the bus as one), a glow lies under the busy chips, LEDs blink, and
// now and then rush hour floods the streets.

import { noise, hash, seeded, newSeed } from "./noise.js";
import { tiles, tile, put, layer, dotGrid, mixc, shade } from "./tiles.js";

const TAU = Math.PI * 2;
const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
const DG = ["-", "\\", "|", "/"];
const BG = "#020a07";
const UNDER = "#0c2c1e";
const TRACE = "#3c9068";
const PIN = "#b9c4bc";
const SILK = "#9fb8aa";
const CHIP = "#161e1b";
const TRAFFIC = [
  { fill: "#d8fff0", ink: "#1f6a4c", glow: "190,255,226" },
  { fill: "#9ff3ff", ink: "#135a66", glow: "140,236,255" },
  { fill: "#ffc46a", ink: "#6a4210", glow: "255,190,100" },
];
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const adiff = (a, b) => Math.min((a - b + 8) % 8, (b - a + 8) % 8);

function build(s) {
  const R = seeded(s.seed);
  const k = (s.k = s.k || 1);
  tiles(s, Math.max(4, 12 * k));
  const { w, h, cell } = s;
  const cols = Math.ceil(w / cell);
  const rows = Math.ceil(h / cell);
  const portrait = h > w * 1.15;
  // 0 free, 1 trace, 2 part, 3 keep-out.
  const occ = new Uint8Array(cols * rows);
  const inb = (x, y) => x >= 0 && y >= 0 && x < cols && y < rows;
  const at = (x, y) => (inb(x, y) ? occ[y * cols + x] : 9);
  const set = (x, y, v) => {
    if (inb(x, y)) occ[y * cols + x] = v;
  };
  const rectFree = (x0, y0, cw, ch, m) => {
    if (x0 - m < 0 || y0 - m < 0 || x0 + cw + m > cols || y0 + ch + m > rows) return false;
    for (let y = y0 - m; y < y0 + ch + m; y += 1) for (let x = x0 - m; x < x0 + cw + m; x += 1) if (occ[y * cols + x]) return false;
    return true;
  };
  const claim = (x0, y0, cw, ch, m, v = 2) => {
    for (let y = y0 - m; y < y0 + ch + m; y += 1) for (let x = x0 - m; x < x0 + cw + m; x += 1) if (inb(x, y) && !occ[y * cols + x]) occ[y * cols + x] = x >= x0 && x < x0 + cw && y >= y0 && y < y0 + ch ? v : 3;
  };
  const sideOf = (x) => Math.abs(((x + 0.5) * cell) / w - 0.5) * 2;
  // The centre sits under the reading column: calmer there.
  const calm = (x) => (portrait ? 0.82 : 1 - 0.42 * Math.exp(-((sideOf(x) / 0.42) ** 2)));
  const parts = []; // static tiles: {x, y, id}
  const starts = []; // trace seeds: {x, y, d}
  const chips = [];
  const ptile = (x, y, g, fill, ink) => parts.push({ x, y, id: tile(s, g, fill, ink) });
  const text = (x, y, str, fill, ink) => Array.from(str).forEach((ch, i) => ptile(x + i, y, ch, fill, ink));

  // A chip: body tiles, a pin-1 dot, a label, and rows of pins on the given sides.
  function chip(x0, y0, cw, ch, o = {}) {
    const sides = o.sides || "tb";
    claim(x0, y0, cw, ch, 1);
    const body = o.body || shade(CHIP, R.range(0.9, 1.25));
    const ink = shade(body, 2.2);
    // Roofs: plain, a metal plate, or hatched.
    const roof = o.roof !== undefined ? o.roof : cw >= 5 && ch >= 4 && R() < 0.45 ? 1 : R() < 0.35 ? 2 : 0;
    const inner = (x, y) => x > x0 && x < x0 + cw - 1 && y > y0 && y < y0 + ch - 1;
    let labelF = body;
    for (let y = y0; y < y0 + ch; y += 1) {
      for (let x = x0; x < x0 + cw; x += 1) {
        const edge = !inner(x, y);
        const f = y === y0 ? shade(body, 1.4) : x === x0 ? shade(body, 1.18) : y === y0 + ch - 1 || x === x0 + cw - 1 ? shade(body, 0.85) : body;
        if (!edge && roof === 1) {
          const lit = 1.15 - 0.3 * ((x - x0) / cw + (y - y0) / ch) / 2;
          labelF = shade("#3c474a", lit);
          ptile(x, y, "=", labelF, shade(labelF, 1.3));
        } else ptile(x, y, edge ? ":" : roof === 2 ? "/" : ".", f, edge ? shade(f, 2) : roof === 2 ? shade(f, 1.7) : ink);
      }
    }
    ptile(x0 + 1, y0 + 1, "o", roof === 1 ? labelF : shade(body, 1.1), "#9aa8a2");
    if (o.label && o.label.length <= cw - 2) {
      const ly = y0 + Math.floor(ch / 2);
      text(x0 + Math.floor((cw - o.label.length) / 2), ly, o.label, roof === 1 ? shade("#3c474a", 1) : body, roof === 1 ? "#e8f0ec" : o.labelInk || "#d8e4de");
    }
    const pinT = { v: tile(s, "|", null, PIN), hz: tile(s, "-", null, PIN) };
    const pins = (x, y, d, g) => {
      parts.push({ x, y, id: g });
      set(x, y, 2);
      starts.push({ x: x + DIRS[d][0], y: y + DIRS[d][1], d, chip: chips.length });
    };
    const skip = o.skip || 1;
    if (sides.includes("t")) for (let x = x0 + 1; x < x0 + cw - 1; x += skip) pins(x, y0 - 1, 6, pinT.v);
    if (sides.includes("b")) for (let x = x0 + 1; x < x0 + cw - 1; x += skip) pins(x, y0 + ch, 2, pinT.v);
    if (sides.includes("l")) for (let y = y0 + 1; y < y0 + ch - 1; y += skip) pins(x0 - 1, y, 4, pinT.hz);
    if (sides.includes("r")) for (let y = y0 + 1; y < y0 + ch - 1; y += skip) pins(x0 + cw, y, 0, pinT.hz);
    const c = { x0, y0, cw, ch, busy: o.busy || R.range(0.15, 0.6), tint: o.tint || (R() < 0.35 ? 2 : 0) };
    chips.push(c);
    return c;
  }

  // A disc of tiles (electrolytic capacitor or mounting hole).
  function disc(cx, cy, r, kind) {
    claim(cx - r, cy - r, 2 * r + 1, 2 * r + 1, 1);
    const neg = R() * TAU;
    for (let y = -r; y <= r; y += 1) {
      for (let x = -r; x <= r; x += 1) {
        const d = Math.hypot(x, y) / (r + 0.45);
        if (d > 1) continue;
        if (kind === "hole") {
          if (d < 0.55) continue;
          const f = shade("#8a7848", 1.1 - 0.3 * clamp01((x + y) / (2 * r) + 0.5));
          ptile(cx + x, cy + y, d > 0.85 ? "." : ":", f, shade(f, 0.6));
          continue;
        }
        const lit = 1.15 - 0.35 * clamp01((x + y) / (2 * r) + 0.5);
        const a = Math.atan2(y, x);
        const stripe = Math.abs(((a - neg + TAU * 1.5) % TAU) - Math.PI) < 0.55;
        if (d > 0.78) {
          const f = shade(stripe ? "#7d8792" : "#16243e", lit);
          ptile(cx + x, cy + y, stripe ? "-" : "o", f, shade(f, stripe ? 0.6 : 1.9));
        } else {
          const vent = x === 0 || y === 0 || Math.abs(x) === Math.abs(y);
          const f = shade("#9aa4a8", lit);
          ptile(cx + x, cy + y, vent ? (x === 0 && y === 0 ? "X" : x === 0 ? "|" : y === 0 ? "-" : x * y > 0 ? "\\" : "/") : ".", f, shade(f, vent ? 0.55 : 0.82));
        }
      }
    }
    chips.push({ x0: cx - r, y0: cy - r, cw: 2 * r + 1, ch: 2 * r + 1, busy: 0, cap: true });
  }

  // Layout in abstract axes: a runs from the CPU to the memory, b across.
  const A = portrait ? rows : cols;
  const B = portrait ? cols : rows;
  const XY = (a, b) => (portrait ? [b, a] : [a, b]);
  const box = (a0, b0, ca, cb) => (portrait ? [b0, a0, cb, ca] : [a0, b0, ca, cb]);
  const cpuN = Math.max(9, Math.min(15, Math.round(Math.min(cols, rows) * (portrait ? 0.3 : 0.17))));
  const N = Math.min(6, Math.floor((cpuN - 1) / 2));
  const ca0 = Math.round(A * (portrait ? 0.07 : 0.08));
  const cb0 = Math.round(B * (portrait ? 0.5 : 0.34) - cpuN / 2);
  const ramT = 3;
  const ramN = portrait ? 3 : 4;
  const ramLen = 2 * N + 1;
  const ramGap = 3;
  const ramA1 = Math.round(A * (portrait ? 0.94 : 0.95));
  const ramA0 = ramA1 - ramN * ramT - (ramN - 1) * ramGap;
  const D = portrait ? Math.round(R.range(-2, 2)) : Math.round(B * R.range(0.16, 0.24));
  const rb0 = cb0 + D;
  // Mounting holes in the corners, first, so everything keeps clear of them.
  const hr = Math.min(cols, rows) < 50 ? 1 : 2;
  for (const [hx, hy] of [[hr + 2, hr + 2], [cols - hr - 3, hr + 2], [hr + 2, rows - hr - 3], [cols - hr - 3, rows - hr - 3]]) disc(hx, hy, hr, "hole");
  // The CPU: downtown. A metal lid with a lit die window.
  const cpuBox = box(ca0, cb0, cpuN, cpuN);
  const cpu = chip(...cpuBox, { sides: "tblr", body: "#141a1a", busy: 1, tint: 1, roof: 0 });
  const [cx0, cy0] = cpuBox;
  s.die = [];
  const lidM = 2;
  for (let y = cy0 + lidM; y < cy0 + cpuN - lidM; y += 1) {
    for (let x = cx0 + lidM; x < cx0 + cpuN - lidM; x += 1) {
      const e = x === cx0 + lidM || y === cy0 + lidM || x === cx0 + cpuN - lidM - 1 || y === cy0 + cpuN - lidM - 1;
      const lit = 1.2 - 0.4 * ((x - cx0 + y - cy0) / (2 * cpuN));
      const f = shade("#4a5458", lit);
      ptile(x, y, e ? "=" : "%", f, shade(f, e ? 1.5 : 0.75));
    }
  }
  const dm = Math.max(1, Math.floor(cpuN / 2) - 2);
  const dc = cx0 + Math.floor(cpuN / 2);
  const dr = cy0 + Math.floor(cpuN / 2);
  const dieOn = tile(s, "#", "#1f5a50", "#8ff0d8");
  const dieOff = tile(s, ":", "#0f2a26", "#2f6a5e");
  for (let y = dr - dm + 1; y < dr + dm; y += 1) {
    for (let x = dc - dm + 1; x < dc + dm; x += 1) {
      const on = R() < 0.6;
      parts.push({ x, y, id: on ? dieOn : dieOff });
      if (R() < 0.35) s.die.push({ x, y, on, p: R.range(4, 18), ph: R(), duty: R.range(0.2, 0.5) });
    }
  }
  s.dieT = [dieOn, dieOff];
  s.cpu = cpu;
  // Memory bank: thin chips in a row, pins facing along the bus.
  s.ram = [];
  for (let j = 0; j < ramN; j += 1) {
    const a = ramA0 + j * (ramT + ramGap);
    const rb = box(a, rb0, ramT, ramLen);
    const c = chip(...rb, { sides: portrait ? "tb" : "lr", body: "#121816", label: "", busy: 0.6, roof: 0 });
    c.ram = true;
    s.ram.push(c);
    // Ram label runs along the chip, one glyph per tile.
    const lbl = "DDR";
    for (let i = 0; i < lbl.length; i += 1) {
      const [x, y] = XY(a + 1, rb0 + Math.floor(ramLen / 2) - 1 + i);
      parts.push({ x, y, id: tile(s, lbl[i], "#121816", "#c8d4ce") });
    }
  }
  // The bus: N lanes, two apart, jog together on one diagonal.
  s.traces = [];
  const lanes = [];
  const busA0 = ca0 + cpuN + 1;
  const busA1 = ramA0 - 2;
  const jogLen = Math.abs(D);
  const am = Math.round((busA0 + busA1 - jogLen) / 2 + N / 2);
  for (let i = 0; i < N; i += 1) {
    const b = cb0 + 1 + 2 * i;
    const turn = D >= 0 ? am - i : am - (N - 1 - i);
    const cells = [];
    let a = busA0;
    let bb = b;
    while (a <= busA1) {
      cells.push(XY(a, bb));
      if (a >= turn && bb !== b + D) bb += Math.sign(D);
      a += 1;
    }
    lanes.push(cells);
  }
  // Clear the pins the bus replaces and lay it down.
  for (const cells of lanes) {
    for (const [x, y] of cells) set(x, y, 1);
    s.traces.push({ cells, bus: true });
  }
  // Starts that collide with the bus are dropped later by the free check.
  // Landmarks: an electrolytic capacitor or two beside the CPU.
  const capR = Math.max(2, Math.round(Math.min(cols, rows) * 0.035));
  const tryDisc = (fx, fy, r) => {
    const cx = Math.round(fx);
    const cy = Math.round(fy);
    if (rectFree(cx - r, cy - r, 2 * r + 1, 2 * r + 1, 2)) {
      disc(cx, cy, r, "cap");
      return true;
    }
    return false;
  };
  for (let n = 0, placed = 0; n < 60 && placed < (portrait ? 1 : 2); n += 1) {
    const [x, y] = portrait ? [R.range(capR + 2, cols - capR - 3), R.range(rows * 0.3, rows * 0.55)] : [R.range(capR + 3, cols * 0.3), R.range(rows * 0.08, rows * 0.92)];
    if (tryDisc(x, y, placed ? capR - 1 : capR)) placed += 1;
  }
  for (let n = 0; n < 40; n += 1) if (tryDisc(portrait ? R.range(capR + 2, cols - capR - 3) : R.range(cols * 0.72, cols - capR - 3), portrait ? R.range(rows * 0.6, rows * 0.85) : R.range(rows * 0.05, rows * 0.4), capR)) break;
  // Crystal beside the CPU.
  for (let n = 0; n < 40; n += 1) {
    const x = Math.round(cx0 + R.range(-6, cpuN + 4));
    const y = Math.round(cy0 + R.range(-6, cpuN + 4));
    if (!rectFree(x, y, 5, 2, 2)) continue;
    claim(x, y, 5, 2, 1);
    for (let i = 0; i < 5; i += 1) for (let j = 0; j < 2; j += 1) {
      const f = shade("#a8b0b4", 1.12 - 0.12 * j - 0.03 * i);
      const g = i === 0 ? "(" : i === 4 ? ")" : "=";
      ptile(x + i, y + j, g, f, shade(f, 0.6));
    }
    text(x + 1, y, "XTL", "#b8c0c4", "#4a5256");
    break;
  }
  // City blocks: more chips, mostly toward the edges.
  const nChips = Math.round((cols * rows) / (portrait ? 420 : 650));
  const LABELS = ["U3", "U7", "U12", "MCU", "PMU", "IO", "USB", "NET", "DSP", "ADC", "U21", "RF", "EEP"];
  for (let n = 0, placed = 0; n < nChips * 30 && placed < nChips; n += 1) {
    const quad = R() < 0.35;
    const cw = quad ? Math.round(R.range(5, 8)) : Math.round(R.range(4, 10));
    const ch = quad ? cw : Math.round(R.range(3, 6));
    const x = Math.floor(R() * (cols - cw - 2)) + 1;
    const y = Math.floor(R() * (rows - ch - 2)) + 1;
    const side = sideOf(x + cw / 2);
    if (!portrait && side < 0.5 && R() > 0.25) continue;
    if (!rectFree(x, y, cw, ch, 3)) continue;
    chip(x, y, cw, ch, { sides: quad ? "tblr" : ch > cw ? "lr" : "tb", label: LABELS[Math.floor(R() * LABELS.length)], skip: R() < 0.3 ? 2 : 1 });
    placed += 1;
  }
  // Passives: little resistors and capacitors along the streets; three are LEDs.
  s.leds = [];
  const nPass = Math.round((cols * rows) / 140);
  const LEDC = ["#ff4a3d", "#59ff9a", "#ffb03d", "#5ac8ff"];
  for (let n = 0, placed = 0; n < nPass * 20 && placed < nPass; n += 1) {
    const vert = R() < 0.5;
    const len = R() < 0.7 ? 3 : 2;
    const [cw, ch] = vert ? [1, len] : [len, 1];
    const x = Math.floor(R() * (cols - cw - 2)) + 1;
    const y = Math.floor(R() * (rows - ch - 2)) + 1;
    if (!portrait && sideOf(x) < 0.4 && R() > 0.4) continue;
    if (!rectFree(x, y, cw, ch, 1)) continue;
    claim(x, y, cw, ch, 0);
    const led = s.leds.length < 4 && (portrait || sideOf(x) > 0.55) && len === 3 && R() < 0.2;
    const kind = led ? "led" : R() < 0.55 ? "r" : "c";
    const bodyF = kind === "r" ? "#1c201e" : kind === "c" ? "#8a6a3e" : "#2a2a26";
    const capF = "#b4bab4";
    for (let i = 0; i < len; i += 1) {
      const xx = x + (vert ? 0 : i);
      const yy = y + (vert ? i : 0);
      const end = i === 0 || i === len - 1;
      if (end) ptile(xx, yy, vert ? "-" : "|", capF, "#6a706a");
      else if (kind === "led") {
        const col = LEDC[s.leds.length % LEDC.length];
        s.leds.push({ x: xx, y: yy, col, on: tile(s, "*", col, shade(col, 0.45)), off: tile(s, "*", shade(col, 0.3), shade(col, 0.55)), mode: s.leds.length % 3, ph: R() * 10 });
      } else ptile(xx, yy, kind === "r" ? (vert ? "|" : "=") : "#", bodyF, kind === "r" ? "#8a9a92" : shade(bodyF, 0.6));
    }
    // Every passive sits on a trace or two.
    if (vert) {
      starts.push({ x, y: y - 1, d: 6 });
      starts.push({ x, y: y + len, d: 2 });
    } else {
      starts.push({ x: x - 1, y, d: 4 });
      starts.push({ x: x + len, y, d: 0 });
    }
    placed += 1;
  }
  // Streets: traces wander out from pins with 45 degree jogs.
  function walk(x, y, d0, len) {
    if (at(x, y) !== 0) return null;
    const cells = [[x, y]];
    set(x, y, 1);
    let d = d0;
    let run = 0;
    let diagLen = 0;
    let rot = 1;
    for (let i = 1; i < len; i += 1) {
      run += 1;
      let nd = d;
      if (d % 2 === 0) {
        if (run > 2 && R() < 0.13) {
          rot = R() < 0.5 ? 1 : 7;
          nd = (d + rot) % 8;
          diagLen = Math.round(R.range(1, 5));
        }
      } else if (run >= diagLen) {
        const on = (d + rot) % 8;
        nd = R() < 0.3 && adiff(on, d0) <= 2 ? on : (d + 8 - rot) % 8;
      }
      if (adiff(nd, d0) > 2) nd = d;
      let ok = false;
      for (const cand of nd === d ? [d] : [nd, d]) {
        const [dx, dy] = DIRS[cand];
        const nx = x + dx;
        const ny = y + dy;
        if (at(nx, ny) !== 0) continue;
        if (dx && dy && at(x + dx, y) === 1 && at(x, y + dy) === 1) continue;
        // Keep a lane's width from other streets running alongside.
        const [px, py] = [-dy, dx];
        if (cand % 2 === 0 && at(nx + px, ny + py) === 1 && at(nx - px, ny - py) === 1) continue;
        if (cand !== d) run = 0;
        d = cand;
        x = nx;
        y = ny;
        ok = true;
        break;
      }
      if (!ok) break;
      cells.push([x, y]);
      set(x, y, 1);
    }
    if (cells.length < 3) {
      for (const [cx, cy] of cells) set(cx, cy, 0);
      return null;
    }
    return cells;
  }
  for (const st of starts) {
    const c = st.chip === undefined ? null : chips[st.chip];
    if (R() > (c ? (c === cpu ? 0.9 : 0.6) : 0.85)) continue;
    const len = Math.round(R.range(4, c === cpu ? 34 : 24));
    const cells = walk(st.x, st.y, st.d, len);
    if (cells) s.traces.push({ cells });
  }
  // Arterials: long streets in from the board edge.
  const nArt = Math.round((cols + rows) / (portrait ? 8 : 10));
  for (let n = 0, made = 0; n < nArt * 10 && made < nArt; n += 1) {
    const e = Math.floor(R() * 4);
    const x = e === 0 ? 0 : e === 2 ? cols - 1 : Math.floor(R() * cols);
    const y = e === 1 ? 0 : e === 3 ? rows - 1 : Math.floor(R() * rows);
    const d = [0, 2, 4, 6][e];
    const cells = walk(x, y, d, Math.round(R.range(25, 90)));
    if (cells && cells.length > 12) {
      s.traces.push({ cells, art: true });
      made += 1;
    }
  }
  // Vias: plazas at the ends of streets, and stitched squares in open ground.
  const viaT = tile(s, "o", "#1d3426", "#d0b46a");
  s.vias = [];
  for (const tr of s.traces) {
    if (tr.bus) continue;
    if (R() < 0.55) {
      const [x, y] = tr.cells[tr.cells.length - 1];
      tr.via = true;
      s.vias.push({ x, y });
    }
  }
  for (let n = 0, made = 0; n < 400 && made < Math.round((cols * rows) / 1800); n += 1) {
    const vw = Math.round(R.range(2, 4));
    const vh = Math.round(R.range(2, 3));
    const x = Math.floor(R() * (cols - vw * 2));
    const y = Math.floor(R() * (rows - vh * 2));
    if (!rectFree(x, y, vw * 2 - 1, vh * 2 - 1, 1)) continue;
    claim(x, y, vw * 2 - 1, vh * 2 - 1, 0);
    for (let i = 0; i < vw; i += 1) for (let j = 0; j < vh; j += 1) s.vias.push({ x: x + i * 2, y: y + j * 2 });
    made += 1;
  }
  // Silkscreen: refdes beside blocks, and a title in a quiet corner.
  for (const c of chips) {
    if (c.cap || c.ram || c === cpu) continue;
    const y = c.y0 - 3;
    const lbl = "U" + Math.floor(R.range(1, 40));
    if (y >= 0 && Array.from(lbl).every((_, i) => at(c.x0 + i, y) === 0)) {
      Array.from(lbl).forEach((ch, i) => {
        set(c.x0 + i, y, 2);
        s.silk = s.silk || [];
        s.silk.push({ x: c.x0 + i, y, id: tile(s, ch, null, SILK) });
      });
    }
  }
  const title = "CIRCUIT CITY  REV B";
  for (let n = 0; n < 200; n += 1) {
    const x = Math.floor(R() * (cols - title.length - 2)) + 1;
    const y = Math.floor(R() * (rows - 2)) + 1;
    if (!portrait && sideOf(x + title.length / 2) < 0.55) continue;
    if (!rectFree(x, y, title.length, 1, 1)) continue;
    claim(x, y, title.length, 1, 0);
    s.silk = s.silk || [];
    Array.from(title).forEach((ch, i) => ch !== " " && s.silk.push({ x: x + i, y, id: tile(s, ch, null, SILK) }));
    break;
  }

  // Paint the board once.
  const [back, g] = layer(s);
  g.fillStyle = BG;
  g.fillRect(0, 0, w, h);
  // Solder-mask sheen: a slow, smooth unevenness.
  const lw = Math.ceil(w / (24 * k)) + 2;
  const lh = Math.ceil(h / (24 * k)) + 2;
  const lo = document.createElement("canvas");
  lo.width = lw;
  lo.height = lh;
  const lg = lo.getContext("2d");
  const img = lg.createImageData(lw, lh);
  const off = R() * 100;
  for (let y = 0; y < lh; y += 1) {
    for (let x = 0; x < lw; x += 1) {
      const v = 0.6 * noise(x * 0.07 + off, y * 0.07) + 0.4 * noise(x * 0.2, y * 0.2 + off);
      const o = (y * lw + x) * 4;
      img.data[o] = 2 + 6 * v;
      img.data[o + 1] = 10 + 18 * v;
      img.data[o + 2] = 7 + 12 * v;
      img.data[o + 3] = 255;
    }
  }
  lg.putImageData(img, 0, 0);
  g.imageSmoothingEnabled = true;
  g.drawImage(lo, 0, 0, lw * 24 * k, lh * 24 * k);
  // City light: the haze over downtown, baked faint.
  const haze = (x, y, r, col, a) => {
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, `rgba(${col},${a})`);
    gr.addColorStop(1, `rgba(${col},0)`);
    g.fillStyle = gr;
    g.fillRect(x - r, y - r, r * 2, r * 2);
  };
  haze((cx0 + cpuN / 2) * cell, (cy0 + cpuN / 2) * cell, cpuN * cell * 2.4, "40,140,110", 0.18);
  // Light over the busier districts: teal, and sodium-warm here and there.
  for (const c of chips) {
    if (c.cap || c === cpu) continue;
    const warm = c.tint === 2;
    haze((c.x0 + c.cw / 2) * cell, (c.y0 + c.ch / 2) * cell, Math.max(c.cw, c.ch) * cell * 2.2, warm ? "150,110,40" : "30,120,90", (warm ? 0.1 : 0.08) * calm(c.x0));
  }
  const vg = g.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.hypot(w, h) * 0.6);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(0,0,0,0.45)");
  g.fillStyle = vg;
  g.fillRect(0, 0, w, h);
  dotGrid(g, s, "#5fae8a", 0.22);
  // Inner copper layers, seen dimly through the mask.
  g.save();
  g.filter = `blur(${Math.max(1, 2.5 * k)}px)`;
  g.strokeStyle = "#0b2c1f";
  g.lineJoin = "round";
  g.lineWidth = cell * 0.55;
  for (let i = 0; i < Math.round((cols + rows) / 6); i += 1) {
    let x = R() * cols;
    let y = R() * rows;
    let d = Math.floor(R() * 4) * 2;
    g.globalAlpha = R.range(0.5, 1);
    g.beginPath();
    g.moveTo(x * cell, y * cell);
    for (let j = 0; j < 6; j += 1) {
      const run = R.range(4, 24);
      x += DIRS[d][0] * run;
      y += DIRS[d][1] * run;
      g.lineTo(x * cell, y * cell);
      d = (d + (d % 2 ? (R() < 0.5 ? 1 : 7) : R() < 0.5 ? 1 : 7)) % 8;
    }
    g.stroke();
  }
  // Shadows under the parts, cast down and to the right.
  g.filter = `blur(${Math.max(1, 3 * k)}px)`;
  g.globalAlpha = 0.75;
  g.fillStyle = "#000000";
  for (const c of chips) {
    if (c.cap) {
      g.beginPath();
      g.arc((c.x0 + c.cw / 2 + 0.5) * cell, (c.y0 + c.ch / 2 + 0.7) * cell, (c.cw / 2) * cell, 0, TAU);
      g.fill();
    } else g.fillRect((c.x0 + 0.4) * cell, (c.y0 + 0.6) * cell, c.cw * cell, c.ch * cell);
  }
  g.restore();
  // Street beds under the glyphs.
  g.lineJoin = "round";
  g.lineCap = "round";
  for (const tr of s.traces) {
    g.strokeStyle = tr.bus ? "#103a29" : UNDER;
    g.lineWidth = cell * (tr.bus ? 0.7 : tr.art ? 0.6 : 0.5);
    g.globalAlpha = calm(tr.cells[0][0]);
    g.beginPath();
    tr.cells.forEach(([x, y], i) => (i ? g.lineTo : g.moveTo).call(g, (x + 0.5) * cell, (y + 0.5) * cell));
    g.stroke();
  }
  g.globalAlpha = 1;
  // Glyphs on the streets; each cell remembers its glyph for the traffic.
  const traceT = new Map();
  const traceTile = (gi, lv) => {
    const key = gi * 10 + lv;
    if (!traceT.has(key)) traceT.set(key, tile(s, DG[gi], null, mixc(BG, TRACE, 0.55 + 0.15 * lv)));
    return traceT.get(key);
  };
  for (const tr of s.traces) {
    tr.g = tr.cells.map(([x, y], i) => {
      const [ax, ay] = i ? tr.cells[i - 1] : tr.cells[1];
      const dx = i ? x - ax : ax - x;
      const dy = i ? y - ay : ay - y;
      return dy === 0 ? 0 : dx === 0 ? 2 : dx === dy ? 1 : 3;
    });
    tr.cells.forEach(([x, y], i) => {
      const c = calm(x);
      const lv = c > 0.9 ? 3 : c > 0.75 ? 2 : c > 0.62 ? 1 : 0;
      put(g, s, traceTile(tr.g[i], tr.bus ? Math.min(3, lv + 1) : lv), x * cell, y * cell);
    });
  }
  g.globalCompositeOperation = "lighter";
  for (const v of s.vias) {
    const r = cell * 2.2;
    const gr = g.createRadialGradient((v.x + 0.5) * cell, (v.y + 0.5) * cell, 0, (v.x + 0.5) * cell, (v.y + 0.5) * cell, r);
    gr.addColorStop(0, `rgba(230,190,90,${0.07 * calm(v.x)})`);
    gr.addColorStop(1, "rgba(230,190,90,0)");
    g.fillStyle = gr;
    g.fillRect((v.x + 0.5) * cell - r, (v.y + 0.5) * cell - r, r * 2, r * 2);
  }
  g.globalCompositeOperation = "source-over";
  for (const v of s.vias) put(g, s, viaT, v.x * cell, v.y * cell, 0.6 + 0.4 * calm(v.x));
  for (const p of parts) put(g, s, p.id, p.x * cell, p.y * cell);
  for (const p of s.silk || []) put(g, s, p.id, p.x * cell, p.y * cell, 0.45);
  g.globalAlpha = 1;
  s.back = back;

  // Traffic: packets per street; on the bus, whole words move together.
  s.pulseIds = TRAFFIC.map((c) => ({ head: DG.map((gl) => tile(s, gl, c.fill, c.ink)), tail: DG.map((gl) => tile(s, gl, null, c.fill)) }));
  s.packets = [];
  const busV = R.range(6, 9);
  const busCycle = Math.round(R.range(14, 22));
  s.traces.forEach((tr, ti) => {
    const L = tr.cells.length;
    if (tr.bus) {
      s.packets.push({ tr, v: busV, cycle: busCycle, ph: 0, dir: 1, col: 1, th: -1, lane: ti, a: 1 });
      return;
    }
    if (L < 6) return;
    const n = Math.max(1, Math.round(L / 16));
    for (let i = 0; i < n; i += 1) {
      const warm = R() < 0.3;
      s.packets.push({ tr, v: R.range(3, 8), cycle: L + Math.round(R.range(4, 30)), ph: R() * 200, dir: R() < 0.5 ? 1 : -1, col: warm ? 2 : 0, th: R() * 0.95, a: 0.55 + 0.45 * calm(tr.cells[0][0]) });
    }
  });
  // A glow sprite for busy blocks and LEDs.
  const gs = document.createElement("canvas");
  gs.width = gs.height = 128;
  const gg = gs.getContext("2d");
  const gr = gg.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, "rgba(255,255,255,1)");
  gr.addColorStop(0.35, "rgba(255,255,255,0.4)");
  gr.addColorStop(1, "rgba(255,255,255,0)");
  gg.fillStyle = gr;
  gg.fillRect(0, 0, 128, 128);
  s.glowSprite = gs;
  s.tinted = new Map();
  s.chips = chips.filter((c) => !c.cap);
  s.rushOff = R() * 100;
}

// A glow sprite in one colour, cached.
function glow(s, col) {
  if (!s.tinted.has(col)) {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    g.drawImage(s.glowSprite, 0, 0);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = `rgb(${col})`;
    g.fillRect(0, 0, 128, 128);
    s.tinted.set(col, c);
  }
  return s.tinted.get(col);
}

export const circuit = {
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
    const { ctx, w, h, cell } = s;
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(s.back, 0, 0, w, h);
    // Rush hour every 50 s or so: more traffic, brighter blocks.
    const rp = (t + s.rushOff) % 52;
    const rush = Math.exp(-(((rp - 26) / 6) ** 2));
    const act = 0.6 + 0.4 * rush;
    // Glow under busy blocks.
    ctx.globalCompositeOperation = "lighter";
    s.chips.forEach((c, i) => {
      const busy = c.busy * (0.55 + 0.45 * noise(t * 0.25, i * 3.7)) * (0.7 + 0.5 * rush);
      if (busy < 0.08) return;
      const cx = (c.x0 + c.cw / 2) * cell;
      const cy = (c.y0 + c.ch / 2) * cell;
      const r = Math.max(c.cw, c.ch) * cell * 1.3;
      ctx.globalAlpha = Math.min(0.5, 0.3 * busy);
      ctx.drawImage(glow(s, c.tint === 1 ? "60,220,190" : c.tint === 2 ? "220,160,70" : "70,200,140"), cx - r, cy - r, r * 2, r * 2);
    });
    ctx.globalCompositeOperation = "source-over";
    // The die blinks like office windows.
    for (const d of s.die) {
      const flip = (t / d.p + d.ph) % 1 < d.duty;
      if (flip) put(ctx, s, s.dieT[d.on ? 1 : 0], d.x * cell, d.y * cell);
    }
    // Traffic.
    for (const p of s.packets) {
      const { tr } = p;
      const L = tr.cells.length;
      const pos = t * p.v + p.ph;
      const n = Math.floor(pos / p.cycle);
      const head = Math.floor(pos - n * p.cycle);
      let a = p.a;
      if (p.th < 0) {
        // A bus lane carries a bit of the current word.
        if (hash(n, p.lane, 7) > 0.35 + 0.3 * (1 - rush)) continue;
      } else {
        a *= clamp01((act - p.th) / 0.15);
        if (a <= 0.02) continue;
      }
      const ids = s.pulseIds[p.col];
      if (head >= 0 && head < L) {
        const [hx, hy] = tr.cells[p.dir > 0 ? head : L - 1 - head];
        const r = cell * 1.8;
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = 0.5 * a;
        ctx.drawImage(glow(s, TRAFFIC[p.col].glow), (hx + 0.5) * cell - r, (hy + 0.5) * cell - r, r * 2, r * 2);
        ctx.globalCompositeOperation = "source-over";
      }
      for (let j = 0; j < 5; j += 1) {
        const i = head - j;
        if (i < 0 || i >= L) continue;
        const ci = p.dir > 0 ? i : L - 1 - i;
        const [x, y] = tr.cells[ci];
        put(ctx, s, j === 0 || (p.th < 0 && j < 3) ? ids.head[tr.g[ci]] : ids.tail[tr.g[ci]], x * cell, y * cell, a * (j === 0 ? 1 : 0.85 - j * 0.16));
      }
    }
    // LEDs: a heartbeat, a busy flicker, a slow breath.
    for (const l of s.leds) {
      let v;
      if (l.mode === 0) {
        const q = (t + l.ph) % 1.6;
        v = q < 0.12 || (q > 0.3 && q < 0.42) ? 1 : 0;
      } else if (l.mode === 1) v = noise(t * 6 + l.ph, 1) * (0.5 + rush) > 0.45 ? 1 : 0;
      else v = 0.5 + 0.5 * Math.sin(t * 1.3 + l.ph);
      put(ctx, s, v > 0.5 ? l.on : l.off, l.x * cell, l.y * cell);
      if (v > 0.05) {
        const r = cell * 3.2;
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = 0.55 * v;
        const [cr, cg, cb] = [1, 3, 5].map((o) => parseInt(l.col.slice(o, o + 2), 16));
        ctx.drawImage(glow(s, `${cr},${cg},${cb}`), (l.x + 0.5) * cell - r, (l.y + 0.5) * cell - r, r * 2, r * 2);
        ctx.globalCompositeOperation = "source-over";
      }
    }
    // Now and then a soft light slides across the glossy board.
    const sp = ((t + s.rushOff * 1.7) % 83) / 9;
    if (sp < 1) {
      const x = -w * 0.4 + sp * w * 1.8;
      const gr = ctx.createLinearGradient(x - h * 0.6, 0, x + h * 0.2, h * 0.8);
      gr.addColorStop(0, "rgba(140,255,210,0)");
      gr.addColorStop(0.5, `rgba(140,255,210,${0.05 * Math.sin(Math.PI * sp)})`);
      gr.addColorStop(1, "rgba(140,255,210,0)");
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = 1;
      ctx.fillStyle = gr;
      ctx.fillRect(0, 0, w, h);
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
  },
};

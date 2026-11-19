// The running spinner: particles riding strange attractors. Each attractor is
// a vector field; a morph blends the current field into the next, so the
// swarm flows from one shape into the other. The next attractor, how long
// each holds, and the view's turn are random, so no two runs (or rows) trace
// the same sequence.
//
// Canvases carry data-chaos="<key>"; one animation frame loop draws every
// one on screen, at most FPS times a second. A key's simulation outlives its canvas, so a sidebar repaint
// (which rebuilds the rows) carries on mid-flow instead of starting over.

// Field coords = p * S + C, with the vertical axis swapped where Z is up. K
// sets each to about the same on-screen pace and H caps its integration step
// (both measured offline over many starts).
const FIELDS = [
  { up: true, S: 26.37 / 0.85, C: [0.26, 0.5, 25.09], K: 0.28, H: 0.003, f: ([x, y, z]) => [10 * (y - x), x * (28 - z) - y, x * y - 8 / 3 * z] }, // Lorenz
  { up: true, S: 1.38 / 1.05, C: [-0.15, 0.14, 0.72], K: 0.75, H: 0.01, f: ([x, y, z]) => [(z - 0.7) * x - 3.5 * y, 3.5 * x + (z - 0.7) * y, 0.6 + 0.95 * z - z * z * z / 3 - (x * x + y * y) * (1 + 0.25 * z) + 0.1 * z * x * x * x] }, // Aizawa
  { up: false, S: 3.82 / 0.85, C: [0, 0, 0], K: 5.2, H: 0.05, f: ([x, y, z]) => [Math.sin(y) - 0.208186 * x, Math.sin(z) - 0.208186 * y, Math.sin(x) - 0.208186 * z] }, // Thomas
  { up: false, S: 9.27 / 0.85, C: [-2.95, -2.95, -2.95], K: 0.24, H: 0.003, f: ([x, y, z]) => [-1.89 * x - 4 * y - 4 * z - y * y, -1.89 * y - 4 * z - 4 * x - z * z, -1.89 * z - 4 * x - 4 * y - x * x] }, // Halvorsen
  { up: true, S: 12.3 / 0.85, C: [1.21, -1.53, 12.31], K: 1.4, H: 0.01, f: ([x, y, z]) => [-y - z, x + 0.2 * y, 0.2 + z * (x - 5.7)] }, // Rössler
  { up: true, S: 27.04 / 0.85, C: [0.26, 0.34, 27.14], K: 0.17, H: 0.0008, f: ([x, y, z]) => [35 * (y - x), -7 * x - x * z + 28 * y, x * y - 3 * z] }, // Chen
  { up: false, S: 1.1 / 0.85, C: [0.75, -0.02, 0], K: 1.3, H: 0.01, f: ([x, y, z]) => [y + 2.07 * x * y + x * z, 1 - 1.79 * x * x + y * z, x - x * x - y * y] }, // Sprott
];

const TAU = Math.PI * 2;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const smoother = (x) => { x = clamp(x); return x * x * x * (x * (x * 6 - 15) + 10); };
const rand = (a, b) => a + Math.random() * (b - a);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const states = new Map();
let raf = 0;

function fieldVel(k, p) {
  const F = FIELDS[k];
  const X = F.up ? [p[0] * F.S + F.C[0], p[2] * F.S + F.C[1], -p[1] * F.S + F.C[2]] : [p[0] * F.S + F.C[0], p[1] * F.S + F.C[1], p[2] * F.S + F.C[2]];
  const v = F.f(X), m = F.K / F.S;
  return F.up ? [v[0] * m, -v[2] * m, v[1] * m] : [v[0] * m, v[1] * m, v[2] * m];
}

function spawn() {
  const u = Math.random() * TAU, w = rand(-1, 1), r = 0.4 * Math.cbrt(Math.random()), s = Math.sqrt(1 - w * w);
  return { p: [Math.cos(u) * s * r, w * r, Math.sin(u) * s * r], h: [] };
}

function pickNext(st) {
  let k;
  do k = Math.floor(Math.random() * FIELDS.length); while (k === st.a || k === st.prev);
  return k;
}

function step(st, pt, dt, e) {
  const sub = Math.max(3, Math.ceil(Math.max(FIELDS[st.a].K / FIELDS[st.a].H, e > 0 ? FIELDS[st.b].K / FIELDS[st.b].H : 0) * dt));
  const h = dt / sub, p = pt.p;
  for (let s = 0; s < sub; s++) {
    const va = fieldVel(st.a, p), vb = e > 0 ? fieldVel(st.b, p) : va;
    const r = Math.hypot(p[0], p[1], p[2]);
    for (let k = 0; k < 3; k++) {
      let v = va[k] + (vb[k] - va[k]) * e;
      // A soft wall: a particle a blend flings out is pulled back in.
      if (r > 1.1) v -= p[k] * (r - 1.1) * 8;
      p[k] += v * h;
    }
  }
  if (!p.every(Number.isFinite) || Math.hypot(p[0], p[1], p[2]) > 2.2) Object.assign(pt, spawn());
}

function newState(now, small) {
  const st = {
    small, a: Math.floor(Math.random() * FIELDS.length), prev: -1, b: 0,
    hold: rand(3.5, 6), morph: rand(3, 4.2), t0: now,
    yaw: Math.random() * TAU, spin: rand(0.14, 0.3) * (Math.random() < 0.5 ? -1 : 1),
    tilt: rand(0.2, 0.5), wob: Math.random() * TAU, seen: now,
    pts: Array.from({ length: small ? 60 : 320 }, spawn),
  };
  st.b = pickNext(st);
  st.t0 = now - Math.random() * st.hold;
  for (let w = 0; w < 240; w++) st.pts.forEach((pt) => step(st, pt, 1 / 60, 0));
  return st;
}

function advance(st, now, dt) {
  let el = now - st.t0;
  if (el > st.hold + st.morph) {
    st.prev = st.a;
    st.a = st.b;
    st.b = pickNext(st);
    st.hold = rand(3.5, 6);
    st.morph = rand(3, 4.2);
    st.t0 = now;
    el = 0;
  }
  const e = el < st.hold ? 0 : smoother((el - st.hold) / st.morph);
  st.yaw += st.spin * dt;
  const trail = st.small ? 6 : 14;
  for (const pt of st.pts) {
    step(st, pt, dt, e);
    pt.h.push(pt.p.slice());
    if (pt.h.length > trail) pt.h.shift();
  }
}

const rgb = (css) => (css.match(/[\d.]+/g) || []).slice(0, 3).map(Number);

// Colours are read once and again only when the appearance changes.
let inkEpoch = 0;
let lift = null;
const repaint = () => { inkEpoch += 1; lift = null; };
document.addEventListener("fuse-appearance", repaint);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", repaint);

// Segments are stroked in batches of one width and one shade; ALPHA_STEP
// is how finely their opacities are told apart.
const ALPHA_STEP = 0.04;

function draw(c, st, now) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const S = Math.round(c._side * dpr);
  if (!S) return;
  if (c.width !== S) { c.width = S; c.height = S; }
  const ctx = c.getContext("2d");
  if (c._inkEpoch !== inkEpoch) {
    lift ||= rgb(getComputedStyle(document.body).color);
    c._ink = rgb(getComputedStyle(c).color);
    c._lift = lift;
    c._inkEpoch = inkEpoch;
    c._colours = new Map();
  }
  const ink = c._ink, light = c._lift, colours = c._colours;
  const colour = (key, alpha, l) => {
    let css = colours.get(key);
    if (!css) {
      const m = ink.map((v, i) => Math.round(v + ((light[i] ?? v) - v) * l));
      colours.set(key, (css = `rgba(${m[0]},${m[1]},${m[2]},${clamp(alpha)})`));
    }
    return css;
  };
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = "source-over";
  ctx.clearRect(0, 0, S, S);
  ctx.translate(S / 2, S / 2);
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  const R = S / 2 * 0.86 * 0.82, lw = Math.max(dpr * 0.75, S * 0.0075);
  const tilt = st.tilt + 0.15 * Math.sin(now * 0.1 + st.wob);
  const cy = Math.cos(st.yaw), sy = Math.sin(st.yaw), cx = Math.cos(tilt), sx = Math.sin(tilt);
  const view = ([x, y, z]) => {
    const y1 = y * cx - z * sx, z1 = y * sx + z * cx;
    const x2 = x * cy + z1 * sy, z2 = -x * sy + z1 * cy;
    const k = 3.4 / (3.4 - z2);
    return [x2 * k * R, y1 * k * R, clamp((z2 + 1.1) / 2.2)];
  };
  // A segment's width and tint follow its place along the trail (f), its
  // opacity that and its depth: one batch per place and opacity step.
  const batches = new Map();
  for (const pt of st.pts) {
    const H = pt.h;
    if (H.length < 2) continue;
    let a = view(H[0]);
    for (let k = 1; k < H.length; k++) {
      const b = view(H[k]), f = k / H.length;
      const step = Math.round(f * f * (0.15 + 0.6 * b[2]) * (st.small ? 1.25 : 1) / ALPHA_STEP);
      const key = (H.length * 32 + k) * 64 + step;
      let batch = batches.get(key);
      if (!batch) batches.set(key, (batch = { f, step, xy: [] }));
      batch.xy.push(a[0], a[1], b[0], b[1]);
      a = b;
    }
  }
  batches.forEach(({ f, step, xy }, key) => {
    if (!step) return;
    ctx.strokeStyle = colour(key, step * ALPHA_STEP, 0.15 + 0.4 * f);
    ctx.lineWidth = lw * (0.4 + 0.9 * f);
    ctx.beginPath();
    for (let i = 0; i < xy.length; i += 4) { ctx.moveTo(xy[i], xy[i + 1]); ctx.lineTo(xy[i + 2], xy[i + 3]); }
    ctx.stroke();
  });
}

// Mounted canvases, found once after markup asks for one (chaosHtml,
// mountChaos), and the ones on screen, which the loop draws.
const FPS = 20;
const mounted = new Set();
const onScreen = new Set();
let scan = false;
let last = 0;
const sight = new IntersectionObserver((entries) => {
  for (const { target, isIntersecting } of entries) {
    target._side = target.clientWidth;
    if (isIntersecting && target._side) onScreen.add(target);
    else onScreen.delete(target);
  }
  wake();
});

function findCanvases() {
  scan = false;
  for (const c of document.querySelectorAll("canvas[data-chaos]")) {
    if (mounted.has(c)) continue;
    mounted.add(c);
    sight.observe(c);
  }
}

function frame(ms) {
  raf = 0;
  if (document.hidden) return;
  if (scan) findCanvases();
  for (const c of mounted) {
    if (c.isConnected) continue;
    mounted.delete(c);
    onScreen.delete(c);
    sight.unobserve(c);
  }
  const now = ms / 1000;
  const still = reduceMotion.matches;
  // A frame budget: draws at FPS, the swarm still moving in 60ths of a second.
  if (!still && last && now - last < 1 / FPS - 0.004) {
    if (onScreen.size) raf = requestAnimationFrame(frame);
    return;
  }
  const dt = last ? clamp(now - last, 0, 4 / 60) : 1 / 60;
  last = onScreen.size ? now : 0;
  const seen = new Set();
  for (const c of onScreen) {
    const key = c.dataset.chaos;
    let st = states.get(key);
    if (!st) states.set(key, (st = newState(now, c._side <= 40)));
    if (!seen.has(key)) {
      seen.add(key);
      if (!still) {
        const ticks = Math.max(1, Math.round(dt * 60));
        for (let i = 0; i < ticks; i++) advance(st, now, dt / ticks);
      }
      st.seen = now;
    }
    if (!still || !c.dataset.drawn) {
      if (still && st.pts[0].h.length < 2) for (let i = 0; i < 14; i++) advance(st, now, 1 / 60);
      draw(c, st, now);
      c.dataset.drawn = "1";
    }
  }
  for (const [key, st] of states) if (now - st.seen > 30) states.delete(key);
  if (onScreen.size && !still) raf = requestAnimationFrame(frame);
}

function wake() {
  if (!raf && !document.hidden) raf = requestAnimationFrame(frame);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { wake(); return; }
  cancelAnimationFrame(raf);
  raf = 0;
  last = 0;
});
reduceMotion.addEventListener?.("change", wake);

// Starts the loop; it stops by itself once no canvas is left on screen.
export function kickChaos() {
  scan = true;
  wake();
}

// A spinner canvas for markup; color (a hex) tints it, else CSS does.
export function chaosHtml(key, color = "") {
  kickChaos();
  const tint = /^#[0-9a-f]{3,8}$/i.test(color) ? ` style="color:${color}"` : "";
  return `<canvas class="dk-chaos" data-chaos="${String(key).replace(/[^\w:-]/g, "")}"${tint} aria-hidden="true"></canvas>`;
}

// Puts the spinner in a slot (the status line's), keeping a live one.
export function mountChaos(el, key) {
  if (!el) return;
  const have = el.querySelector(":scope > canvas[data-chaos]");
  if (!have || have.dataset.chaos !== key) {
    const c = document.createElement("canvas");
    c.className = "dk-chaos";
    c.dataset.chaos = key;
    c.setAttribute("aria-hidden", "true");
    el.replaceChildren(c);
    el.dataset.spinSet = "chaos";
  }
  kickChaos();
}

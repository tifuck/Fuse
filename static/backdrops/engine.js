// Engine: scene state, the frame runner, picker previews and sync.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { html, reduce } from "./env.js";
import { palette } from "./color.js";
import { SCENES, hasScene, isWallpaper, loadScene } from "./scenes.js";

function makeState(ctx, k) {
  return { ctx, pal: palette(), dpr: 0, w: 0, h: 0, k };
}

// —— Runner ——
const run = { kind: "", canvas: null, ctx: null, raf: 0, timer: 0, last: 0, acc: 0, t: 0, state: null, listening: false, shown: true };
// The timer wakes this much before a scene frame is due, so the display
// frame that follows it is the one that draws.
const EARLY = 0.004;

function stop() {
  if (run.raf) cancelAnimationFrame(run.raf);
  clearTimeout(run.timer);
  run.raf = 0;
  run.timer = 0;
  run.last = 0;
}

// One display frame per scene frame: a 12 fps scene sleeps through the rest.
// Nothing is scheduled while the tab or the canvas is out of sight.
function next() {
  if (run.raf || run.timer || document.hidden || !run.shown) return;
  const wake = () => {
    run.timer = 0;
    run.raf = requestAnimationFrame(frame);
  };
  const wait = run.last ? (1 / SCENES[run.kind].fps - run.acc - EARLY) * 1000 : 0;
  if (wait > 0) run.timer = setTimeout(wake, wait);
  else wake();
}

function measure() {
  const s = run.state;
  if (!s || !run.canvas) return false;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const w = run.canvas.clientWidth || window.innerWidth;
  const h = run.canvas.clientHeight || window.innerHeight;
  if (w < 2 || h < 2) return false;
  const bw = Math.round(w * dpr);
  const bh = Math.round(h * dpr);
  if (run.canvas.width !== bw || run.canvas.height !== bh || s.dpr !== dpr) {
    run.canvas.width = bw;
    run.canvas.height = bh;
    run.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    Object.assign(s, { w, h, dpr });
    SCENES[run.kind].resize(s);
  }
  return true;
}

function frame(now) {
  run.raf = 0;
  const engine = SCENES[run.kind];
  if (!engine || !run.state) return;
  const sec = now / 1000;
  const dt = run.last ? Math.min(0.25, sec - run.last) : 0;
  run.last = sec;
  run.acc += dt;
  const budget = 1 / engine.fps;
  if (run.acc >= budget - EARLY || !run.t) {
    const step = Math.max(run.acc, budget);
    run.acc = 0;
    run.t += step;
    if (measure()) engine.draw(run.state, run.t, step);
  }
  next();
}

function start() {
  stop();
  const engine = SCENES[run.kind];
  if (!engine || !run.canvas) return;
  run.state = makeState(run.ctx, 1);
  engine.setup?.(run.state);
  // Start mid-flight so the first frame already looks settled.
  run.t = 40 + Math.random() * 60;
  if (!measure()) return;
  // Reduced motion holds one frame, so it should be a finished one.
  engine.warm?.(run.state, run.t, reduce.matches);
  engine.draw(run.state, run.t, 1 / engine.fps);
  if (reduce.matches) return;
  next();
}

// A scene's setup, warm-up and first frame take up to a second, so they wait
// for the page: its load, the transcript of the chat it is opening (an idle
// moment comes sooner, while that is still on the wire), then an idle moment.
const settled = new Promise((resolve) => {
  if (document.readyState === "complete") resolve();
  else window.addEventListener("load", resolve, { once: true });
}).then(() => new Promise((resolve) => {
  const log = new URLSearchParams(location.search).get("t") && document.querySelector("[data-chat-log]");
  if (!log || log.childElementCount) return resolve();
  const done = () => {
    filled.disconnect();
    clearTimeout(timer);
    resolve();
  };
  const filled = new MutationObserver(done);
  const timer = setTimeout(done, 4000);
  filled.observe(log, { childList: true });
}));
const whenIdle = (fn) => settled.then(() => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 1500 }) : setTimeout(fn, 200)));

function listen() {
  if (run.listening) return;
  run.listening = true;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else if (SCENES[run.kind] && run.state && !reduce.matches) next();
  });
  // A canvas that is not rendered (a hidden ancestor, no size) stops the loop too.
  if (typeof IntersectionObserver === "function") {
    run.watch = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.target === run.canvas) run.shown = entry.isIntersecting;
      if (!run.shown) stop();
      else if (SCENES[run.kind] && run.state && !reduce.matches) next();
    });
  }
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    if (resizeFrame || !run.state) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      if (measure() && reduce.matches && SCENES[run.kind]) SCENES[run.kind].draw(run.state, run.t, 0.1);
    });
  });
  reduce.addEventListener?.("change", () => { if (SCENES[run.kind] && run.state) start(); });
}

// —— Custom backdrops ——
// A user's own document ("u-" + id, GET /backdrop/<id>): a sandboxed frame
// with an opaque origin, never focusable, never hit by the pointer.
const CUSTOM = /^u-[a-z0-9]{12}$/;
export const isCustom = (kind) => CUSTOM.test(kind || "");

function customFrame(kind) {
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("tabindex", "-1");
  frame.setAttribute("aria-hidden", "true");
  frame.title = "";
  frame.src = "/backdrop/" + kind.slice(2);
  return frame;
}

function syncFrame(scene, kind) {
  const frame = scene?.querySelector("iframe[data-chat-scene-frame]");
  if (!kind) return frame?.remove();
  if (frame?.dataset.kind === kind) return;
  frame?.remove();
  const next = customFrame(kind);
  next.dataset.chatSceneFrame = "";
  next.dataset.kind = kind;
  scene.querySelector("[data-chat-scene-veil]")?.before(next) || scene.append(next);
}

// A custom card runs its document only while the card is on screen.
let framed = null;
function framePreview(el, on) {
  const frame = el.querySelector("iframe");
  if (!on) return frame?.remove();
  if (!frame) el.append(customFrame(el.dataset.kind));
}

// —— Picker previews ——
// Each card shows a real, settled frame of its scene; the hovered card runs.
const previews = new Map();
let observer = null;
let seen = null;
const hover = { el: null, raf: 0, last: 0, acc: 0 };

function paintPreview(el) {
  const kind = el.dataset.kind;
  const engine = SCENES[kind];
  if (!engine) return;
  const w = el.clientWidth;
  const h = el.clientHeight;
  if (w < 8 || h < 4) return;
  let canvas = el.querySelector("canvas");
  if (!canvas) {
    canvas = document.createElement("canvas");
    el.append(canvas);
  }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const s = makeState(ctx, engine.previewK || 0.5);
  Object.assign(s, { w, h, dpr });
  engine.setup?.(s);
  engine.resize(s);
  const t = 50;
  engine.warm?.(s, t, true);
  engine.draw(s, t, 1 / engine.fps);
  previews.set(el, { s, t, engine });
}

function hoverFrame(now) {
  hover.raf = 0;
  const entry = hover.el && previews.get(hover.el);
  if (!entry) return;
  const sec = now / 1000;
  const dt = hover.last ? Math.min(0.25, sec - hover.last) : 0;
  hover.last = sec;
  hover.acc += dt;
  const budget = 1 / entry.engine.fps;
  if (hover.acc >= budget) {
    const step = Math.max(hover.acc, budget);
    hover.acc = 0;
    entry.t += step;
    entry.engine.draw(entry.s, entry.t, step);
  }
  hover.raf = requestAnimationFrame(hoverFrame);
}

function hoverPreview(el) {
  if (hover.el === el) return;
  if (hover.raf) cancelAnimationFrame(hover.raf);
  hover.raf = 0;
  hover.el = el;
  hover.last = 0;
  hover.acc = 0;
  if (el && previews.has(el) && !reduce.matches) hover.raf = requestAnimationFrame(hoverFrame);
}

export function scanPreviews(root = document) {
  const els = root.querySelectorAll?.(".chat-backdrop-preview[data-kind]") || [];
  if (!observer && typeof ResizeObserver === "function" && typeof IntersectionObserver === "function") {
    // A card's scene loads when the card first comes into view; from then on
    // the card is sized, painted and repainted by the resize observer.
    seen = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        seen.unobserve(entry.target);
        loadScene(entry.target.dataset.kind).then(() => observer.observe(entry.target), () => {});
      }
    });
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const el = entry.target;
        const box = entry.contentRect;
        const prev = previews.get(el);
        if (box.width > 8 && (!prev || Math.abs(prev.s.w - el.clientWidth) > 1 || Math.abs(prev.s.h - el.clientHeight) > 1)) paintPreview(el);
      }
    });
    document.addEventListener("pointerover", (event) => {
      const card = event.target.closest?.(".ap-backdrop");
      hoverPreview(card ? card.querySelector(".chat-backdrop-preview[data-kind]") : null);
    });
  }
  for (const el of els) {
    if (isCustom(el.dataset.kind) && !el.dataset.fxObserved) {
      el.dataset.fxObserved = "1";
      if (typeof IntersectionObserver !== "function") {
        framePreview(el, true);
        continue;
      }
      framed ||= new IntersectionObserver((entries) => {
        for (const entry of entries) framePreview(entry.target, entry.isIntersecting);
      });
      framed.observe(el);
      continue;
    }
    if (!hasScene(el.dataset.kind) || el.dataset.fxObserved) continue;
    el.dataset.fxObserved = "1";
    if (observer) seen.observe(el);
    else loadScene(el.dataset.kind).then(() => paintPreview(el), () => {});
  }
}

// Only a theme change alters the scene's colors; Intensity, text size and
// the choice itself leave them as they are.
let ink = "";
function themeInk() {
  const style = getComputedStyle(html);
  return ["--ink", "--mist", "--mute", "--brass", "--steel"].map((p) => style.getPropertyValue(p).trim()).join();
}

function repaintPreviews() {
  for (const el of previews.keys()) {
    if (!el.isConnected) previews.delete(el);
    else paintPreview(el);
  }
}

// A custom color picker fires on every pointer move; repainting the scene
// and every preview card per tick made dragging it crawl. Recolor once the
// colors stop changing.
const SETTLE_MS = 150;
let settle = 0;

// chat-appearance.js calls this on every appearance change.
export function sync(scene, kind) {
  const next = themeInk();
  if (ink && next !== ink) {
    clearTimeout(settle);
    settle = setTimeout(() => {
      repaintPreviews();
      if (run.state) start();
    }, SETTLE_MS);
  }
  ink = next;
  const canvas = scene && scene.querySelector("canvas[data-chat-scene-fx]");
  const animated = Boolean(canvas && !scene.hidden && hasScene(kind));
  const same = animated && run.state && run.kind === kind && run.canvas === canvas;
  // A custom backdrop is a frame, not a canvas scene: one of the two runs.
  const custom = Boolean(scene && !scene.hidden && isCustom(kind));
  syncFrame(scene, custom ? kind : "");
  // Wallpapers (data-only specs, the landscape scenes and custom documents)
  // paint their own opaque ground: the CSS shows them at full strength,
  // without the centre mask and the veil.
  const dress = () => scene?.toggleAttribute("data-wallpaper", custom || isWallpaper(kind));
  if (!animated) {
    dress();
    stop();
    run.kind = kind || "";
    run.state = null;
    if (canvas) canvas.hidden = true;
    return;
  }
  listen();
  if (same) return;
  stop();
  run.state = null;
  if (run.canvas !== canvas) {
    if (run.canvas) run.watch?.unobserve(run.canvas);
    run.watch?.observe(canvas);
    run.shown = true;
  }
  run.canvas = canvas;
  run.ctx = canvas.getContext("2d");
  run.kind = kind;
  // The scene's module loads on first use and its first frame is drawn once
  // the page is idle. Until then the layers stay as they were (the CSS wash
  // and veil, or the previous scene's last frame), so nothing flashes.
  loadScene(kind).then(() => whenIdle(() => {
    if (run.kind !== kind || run.canvas !== canvas || run.state || scene.hidden) return;
    dress();
    canvas.hidden = false;
    start();
  }), () => {});
}

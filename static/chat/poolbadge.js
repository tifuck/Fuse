// The plan usage gauge beside the model (app/sources/pool_badge.py): how much
// of the subscription plan the chat's pool account has used, and a card with
// every limit and every account of the pool. Each theme places
// [data-pool-badge] holding a [data-pool-toggle] button and styles the parts:
// the gauge (a ring with the percent in it), the window label, or the window
// cells. The card is the permissions menu's frame, so it looks like the
// theme's own menus: a popover above the composer, a sheet on phones.
import { S } from './state.js';
import { escapeHtml } from './core.js';
import { attachSheetSwipe } from './overlays.js';

const CELLS = 5;
const MANAGE = "/sources";
const WHY = { limit: "it reached its usage limit", error: "it stopped answering" };
const STATUS = { current: "in use", ready: "ready", full: "full", paused: "paused", off: "off" };
const CLOSE_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

let deps = { place: () => {}, overlays: null };
let menu = null;
const ink = new Map();

const level = (pct) => (pct >= 95 ? "hot" : pct >= 75 ? "warn" : "ok");
const clamp = (n) => Math.max(0, Math.min(100, Math.round(Number(n) || 0)));

function windowsOf(list) {
  return (Array.isArray(list) ? list : []).map((w) => ({ ...w, percent: clamp(w.percent) }));
}

function span(at) {
  const left = Number(at) - Date.now() / 1000;
  if (!(left > 0)) return "";
  const hours = Math.floor(left / 3600);
  const minutes = Math.max(1, Math.round((left % 3600) / 60));
  if (hours >= 48) return `${Math.round(hours / 24)}d`;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

// The chat's gauge, or null when it has none, belongs to another chat, or the
// picker moved to another model since the account answered.
function current() {
  const pool = S.pool;
  return pool && S.poolThread === S.currentId && S.poolModel === S.modelId ? pool : null;
}

// Wired once by chat.js: the composer's popover placement and the back stack.
export function bindPool(next) {
  deps = { ...deps, ...next };
  window.addEventListener("resize", () => { if (menu && !menu.hidden) placeMenu(); });
  document.fonts?.ready.then(() => { ink.clear(); renderPool(); });
}

export function acceptPool(pool) {
  const before = current();
  S.pool = pool && typeof pool === "object" && pool.slot ? pool : null;
  S.poolThread = S.currentId;
  S.poolModel = S.modelId;
  renderPool(Boolean(before && S.pool && before.slot !== S.pool.slot));
}

// Centres the percent's ink (not its line box) in the ring: the font's
// ascent and descent would otherwise push digits off centre.
function centre(text, size) {
  const style = getComputedStyle(text);
  const font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const key = font + "|" + text.textContent;
  let off = ink.get(key);
  if (!off) {
    const ctx = (centre.ctx ||= document.createElement("canvas").getContext("2d"));
    ctx.font = font;
    const m = ctx.measureText(text.textContent);
    off = [(m.actualBoundingBoxRight - m.actualBoundingBoxLeft) / 2, (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2];
    ink.set(key, off);
  }
  text.setAttribute("x", (size / 2 - off[0]).toFixed(2));
  text.setAttribute("y", (size / 2 + off[1]).toFixed(2));
}

function cells(pct) {
  const on = Math.round((pct / 100) * CELLS);
  return Array.from({ length: CELLS }, (_, i) => `<i${i < on ? ' class="is-on"' : ""}></i>`).join("");
}

export function renderPool(hopped = false) {
  const host = S.root?.querySelector("[data-pool-badge]");
  if (!host) return;
  const btn = host.querySelector("[data-pool-toggle]");
  wire(host, btn);
  const pool = current();
  host.hidden = !pool;
  if (!pool) {
    setOpen(false);
    return;
  }
  const windows = windowsOf(pool.windows);
  const tight = windows.reduce((a, w) => (!a || w.percent > a.percent ? w : a), null);
  host.dataset.level = tight ? level(tight.percent) : "none";
  const pct = tight ? String(tight.percent) : "–";
  const name = `${pool.pool}${pool.plan ? " " + pool.plan : ""} plan`;
  const label = tight ? `${name}: ${tight.percent}% of the ${tight.label.toLowerCase()} limit used` : `${name}: no usage reported yet`;
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.innerHTML =
    `<span class="chat-pool-gauge" aria-hidden="true"><svg viewBox="0 0 20 20">` +
    `<circle class="chat-pool-track" cx="10" cy="10" r="8.5"/>` +
    `<circle class="chat-pool-fill" cx="10" cy="10" r="8.5" pathLength="100" stroke-dasharray="${tight ? tight.percent : 0} 100" transform="rotate(-90 10 10)"/>` +
    `<text class="chat-pool-num${pct.length > 2 ? " is-long" : ""}">${pct}</text></svg></span>` +
    (tight ? `<span class="chat-pool-label" aria-hidden="true">${escapeHtml(tight.short || "")}</span>` : "") +
    `<span class="chat-pool-wins" aria-hidden="true">${windows.map((w) =>
      `<span class="chat-pool-win" data-level="${level(w.percent)}"${w === tight ? " data-tight" : ""}><em>${escapeHtml(w.short || "")}</em>` +
      `<span class="chat-pool-cells">${cells(w.percent)}</span><b>${w.percent}%</b></span>`).join("")}</span>`;
  const text = btn.querySelector("text");
  if (text.getClientRects().length) centre(text, 20);
  if (menu && !menu.hidden) fillMenu(pool);
  if (hopped && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    host.classList.remove("is-hop");
    void host.offsetWidth;
    host.classList.add("is-hop");
    host.addEventListener("animationend", () => host.classList.remove("is-hop"), { once: true });
  }
}

function wire(host, btn) {
  if (host.dataset.poolWired) return;
  host.dataset.poolWired = "1";
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setOpen(!menu || menu.hidden);
  });
  document.addEventListener("pointerdown", (event) => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !host.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !menu || menu.hidden) return;
    setOpen(false);
    btn.focus();
  });
}

function buildMenu() {
  const el = document.createElement("div");
  el.id = "chat-pool-menu";
  el.className = "chat-perms-menu chat-pool-menu";
  el.setAttribute("role", "dialog");
  el.tabIndex = -1;
  el.hidden = true;
  el.addEventListener("click", (event) => {
    event.stopPropagation();
    if (event.target.closest("[data-pool-close]")) setOpen(false);
  });
  attachSheetSwipe(el, ".chat-picker-head", () => el.classList.contains("is-sheet"), () => setOpen(false));
  deps.overlays?.backLayerFollowsHidden(el, "pool");
  return el;
}

function placeMenu() {
  const host = S.root.querySelector("[data-pool-badge]");
  const sheet = S.pickerSheetQuery.matches;
  menu.classList.toggle("is-sheet", sheet);
  // Phones portal the sheet to the chat root (composer ancestors would
  // contain it), like the model and permission sheets.
  const home = sheet ? S.root : host;
  if (menu.parentElement !== home) home.appendChild(menu);
  deps.place(menu, host.querySelector("[data-pool-toggle]"));
}

export function setOpen(open) {
  const host = S.root?.querySelector("[data-pool-badge]");
  const btn = host?.querySelector("[data-pool-toggle]");
  const pool = current();
  const next = Boolean(open && pool && btn);
  if (!next && (!menu || menu.hidden)) return;
  if (!menu) menu = buildMenu();
  btn?.setAttribute("aria-expanded", String(next));
  btn?.setAttribute("aria-controls", menu.id);
  if (!next) {
    menu.hidden = true;
    return;
  }
  fillMenu(pool);
  menu.hidden = false;
  placeMenu();
}

export function closePool() {
  setOpen(false);
}

function line(name, pct, meta, attrs = "") {
  const bar = pct == null ? '<span class="chat-pool-bar is-empty"></span>' : `<span class="chat-pool-bar"><i style="width:${pct}%"></i></span>`;
  return `<li class="chat-perms-row chat-pool-line"${pct == null ? "" : ` data-level="${level(pct)}"`}${attrs}>` +
    `<span class="chat-perms-path chat-pool-name">${name}</span>${bar}` +
    `<span class="chat-pool-pct">${pct == null ? "–" : pct + "%"}</span>` +
    `<span class="chat-perms-tag chat-pool-meta">${meta}</span></li>`;
}

function group(title, aside, rows, extra = "") {
  return `<section class="chat-perms-group"><p><span class="chat-perms-host">${title}</span>${extra}` +
    `<span class="chat-perms-count">${aside}</span></p><ul>${rows}</ul></section>`;
}

function fillMenu(pool) {
  const limit = (w) => line(escapeHtml(w.label), w.percent, w.reset_at ? escapeHtml(span(w.reset_at)) : "");
  const plan = escapeHtml(`${pool.pool}${pool.plan ? " " + pool.plan : ""}`);
  const windows = windowsOf(pool.windows);
  const limits = windowsOf(pool.limits);
  const accounts = Array.isArray(pool.accounts) ? pool.accounts : [];
  const acct = (a) => {
    const who = `<b>${escapeHtml(a.slot)}</b><span class="chat-pool-who">${[a.plan && escapeHtml(a.plan), a.own && "yours"].filter(Boolean).join(" · ")}</span>`;
    const meta = a.status === "limit" ? (a.until ? `back ${escapeHtml(span(a.until))}` : "at limit") : STATUS[a.status] || "";
    const pct = a.percent == null ? null : clamp(a.percent);
    return line(who, pct, meta, ` data-status="${escapeHtml(a.status)}"`);
  };
  const hop = pool.hop && pool.hop.from && pool.hop.from !== pool.slot
    ? `<p class="chat-perms-hint">Moved here from account ${escapeHtml(pool.hop.from)}${WHY[pool.hop.reason] ? `: ${WHY[pool.hop.reason]}` : ""}.</p>`
    : "";
  menu.setAttribute("aria-label", `${pool.pool} plan usage`);
  menu.innerHTML =
    `<div class="chat-picker-head"><p>Plan usage</p><button type="button" class="chat-menu-close" data-pool-close aria-label="Close">${CLOSE_SVG}</button></div>` +
    '<div class="chat-perms-list">' +
    group(plan, `account ${escapeHtml(pool.slot)} · ${pool.own ? "yours" : "shared"}`,
      windows.map(limit).join("") || '<li class="chat-perms-empty">No usage reported yet.</li>',
      '<span class="chat-perms-here">this chat</span>') +
    (limits.length ? group("Model limits", "", limits.map(limit).join("")) : "") +
    (accounts.length ? group("Accounts", `${Number(pool.ready) || 0} of ${Number(pool.size) || 0} ready`, accounts.map(acct).join("")) : "") +
    "</div>" +
    `<div class="chat-perms-foot chat-pool-foot">${hop}<a class="chat-pool-manage" href="${MANAGE}">Manage sources</a></div>`;
}

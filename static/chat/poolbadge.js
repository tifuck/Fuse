// The plan usage meter beside the model (app/sources/pool_badge.py): how much
// of the subscription plan the chat's pool account has used, and a card with
// every limit, every account of the pool, and the user's sharing settings for
// it (saved through the Sources page's endpoint). Each theme places
// [data-pool-badge] holding a [data-pool-toggle] button and styles the parts:
// the meter (two windows, each a letter, a bar and its percent) or the same
// windows as cells. The card is the permissions menu's frame, so it looks like the
// theme's own menus: a popover above the composer, a sheet on phones.
import { S } from './state.js';
import { escapeHtml } from './core.js';
import { csrfToken } from './net.js';
import { attachSheetSwipe, returnFocus } from './overlays.js';

const CELLS = 5;
const MANAGE = "/sources";
const WHY = { limit: "it reached its usage limit", error: "it stopped answering" };
const STATUS = { current: "in use", next: "next", ready: "ready", full: "full", paused: "paused", off: "off" };
const SHARE = [
  ["everyone", "Anyone", (p) => `Anyone using the ${p} pool may use your accounts.`],
  ["contributors", "Sharers", (p) => `Only people who share a ${p} account too may use yours.`],
  ["private", "Nobody", () => "Only you use your accounts, and you use only them."],
];
const USE = [
  ["pool", "Yours + pool", () => "Your accounts first, then accounts shared with you."],
  ["mine", "Only yours", () => "Only your accounts. Requests fail when they are spent."],
];
const BACK_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10 3.5L5.5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
// A sideways drag this far (and mostly sideways) on a phone sheet goes back.
const SWIPE_BACK = 64;
const CLOSE_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

let deps = { place: () => {}, overlays: null };
// Before a reply: the account the next request goes to, per chat and model
// (GET /chat/pool), kept a minute so switching back and forth costs nothing.
const PREVIEW_MS = 60_000;
const previews = new Map();
let previewing = "";
// An account picked for a chat not sent yet ({key, id}): its preview stays
// shown, and the send pins the chat there once it exists (claimPoolChoice).
let choice = null;
// An idle chat rereads its meter this often while the page is visible, and
// the read starts the pool's usage refresh on the server; replies update it
// anyway.
const REFRESH_MS = 60_000;
let shownAt = 0;
let menu = null;
let view = "usage";
let saving = null;
let notice = "";

const level = (pct) => (pct >= 95 ? "hot" : pct >= 75 ? "warn" : pct >= 50 ? "mid" : "ok");
const CURRENCY = { USD: "$", CNY: "¥", EUR: "€" };
// Money left on an account ({amount, currency}), or "" when none is reported.
function money(b) {
  const amount = Number(b?.amount);
  if (!b || !Number.isFinite(amount)) return "";
  const text = Math.abs(amount).toFixed(2);
  const sign = CURRENCY[b.currency];
  return (amount < 0 ? "-" : "") + (sign ? sign + text : `${text} ${b.currency}`);
}
const moneyLevel = (b) => (Number(b?.amount) > 0 ? "ok" : "hot");
// A window's one-letter tag: 5-hour H, Weekly W, Fable weekly F, Auto A.
const letter = (w) => (String(w.label || "").replace(/^[\d\s-]+/, "")[0] || "·").toUpperCase();
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

// The chat's meter, or null when it has none, belongs to another chat, or the
// picker moved to another model since the account answered.
function current() {
  const pool = S.pool;
  return pool && S.poolThread === S.currentId && S.poolModel === S.modelId ? pool : null;
}

function previewKey() {
  return `${S.currentId || ""}|${S.modelId || ""}`;
}

// A fresh preview for the open chat and model becomes its meter until a
// reply names the account that really served it.
function previewed() {
  const hit = previews.get(previewKey());
  if (!hit?.pool || (Date.now() - hit.at > PREVIEW_MS && choice?.key !== previewKey())) return null;
  shownAt = hit.at;
  S.pool = hit.pool;
  S.poolThread = S.currentId;
  S.poolModel = S.modelId;
  return hit.pool;
}

function requestPreview() {
  const key = previewKey();
  const hit = previews.get(key);
  if (!S.modelId || previewing === key || choice?.key === key || (hit && Date.now() - hit.at <= PREVIEW_MS)) return;
  previewing = key;
  askPool().then((pool) => {
    if (previewing === key) previewing = "";
    previews.set(key, { at: Date.now(), pool });
    if (pool && previewKey() === key && !current()) renderPool();
  });
}

// The account the chat's next request goes to (its pinned account while the
// pin lasts), with its numbers as the server has them now.
function askPool(refresh = false) {
  const query = new URLSearchParams({ model: S.modelId });
  if (S.currentId) query.set("thread", S.currentId);
  if (refresh) query.set("refresh", "1");
  return fetch(`/chat/pool?${query}`, { headers: { "X-CSRF-Token": csrfToken() } })
    .then((res) => (res.ok ? res.json() : null))
    .catch(() => null)
    .then((data) => (data?.pool?.name ? data.pool : null));
}

// Rereads a shown meter older than REFRESH_MS, unless the page is hidden or
// a reply is streaming. A failed read keeps what is shown.
export function refreshPool() {
  const pool = current();
  if (!pool || saving || document.hidden || S.streaming || S.sending || Date.now() - shownAt < REFRESH_MS) return null;
  const key = previewKey();
  if (choice?.key === key) return null;
  shownAt = Date.now();
  return askPool(true).then((fresh) => {
    if (!fresh || previewKey() !== key || current() !== pool) return;
    // The preview has no hop: the same account keeps its "moved here" note.
    if (!fresh.hop && fresh.id === pool.id) fresh.hop = pool.hop;
    previews.set(key, { at: Date.now(), pool: fresh });
    acceptPool(fresh);
  });
}

// Wired once by chat.js: the composer's popover placement and the back stack.
export function bindPool(next) {
  deps = { ...deps, ...next };
  window.addEventListener("resize", () => { if (menu && !menu.hidden) placeMenu(); });
  window.setInterval(refreshPool, REFRESH_MS / 2);
  document.addEventListener("visibilitychange", refreshPool);
}

// Keeps an open card on the composer's top edge as the composer grows.
export function placePool() {
  if (menu && !menu.hidden) placeMenu();
}

export function acceptPool(pool) {
  const before = current();
  shownAt = Date.now();
  S.pool = pool && typeof pool === "object" && pool.name ? pool : null;
  S.poolThread = S.currentId;
  S.poolModel = S.modelId;
  renderPool(Boolean(before && S.pool && before.id !== S.pool.id));
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
  const pool = current() || previewed();
  host.hidden = !pool;
  if (!pool) {
    setOpen(false);
    requestPreview();
    return;
  }
  // Up to three rows: the plan's windows (5-hour, weekly), then model or
  // product limits (Claude's Fable weekly, Cursor's Auto).
  const shown = windowsOf(pool.windows).concat(windowsOf(pool.limits)).slice(0, 3);
  const name = pool.prefs ? `${pool.pool}${pool.plan ? " " + pool.plan : ""} plan, ${pool.name}` : `${pool.pool}, ${pool.name}`;
  const cash = money(pool.balance);
  const label = shown.length ? `${name}: ${shown.map((w) => `${w.label} ${w.percent}%`).join(", ")} used${cash ? `, ${cash} left` : ""}`
    : cash ? `${name}: ${cash} left` : `${name}: no usage reported yet`;
  btn.setAttribute("aria-label", label);
  btn.title = label;
  const rows = shown.length ? shown : [{ percent: 0, empty: true }];
  // An account that reports only money left (a prepaid key) shows the amount.
  if (!shown.length && cash) {
    const lvl = moneyLevel(pool.balance);
    btn.innerHTML =
      `<span class="chat-pool-meter" aria-hidden="true"><span class="chat-pool-row is-balance" data-level="${lvl}"><b>${escapeHtml(cash)}</b></span></span>` +
      `<span class="chat-pool-wins" aria-hidden="true"><span class="chat-pool-win is-balance" data-level="${lvl}"><b>${escapeHtml(cash)}</b></span></span>`;
  } else btn.innerHTML =
    `<span class="chat-pool-meter" aria-hidden="true">${rows.map((w) =>
      `<span class="chat-pool-row" data-level="${level(w.percent)}"><em>${w.empty ? "" : letter(w)}</em>` +
      `<span class="chat-pool-track"><i style="width: ${w.percent}%"></i></span><b>${w.empty ? "–" : w.percent + "%"}</b></span>`).join("")}</span>` +
    `<span class="chat-pool-wins" aria-hidden="true">${shown.map((w) =>
      `<span class="chat-pool-win" data-level="${level(w.percent)}"><em>${letter(w)}</em>` +
      `<span class="chat-pool-cells">${cells(w.percent)}</span><b>${w.percent}%</b></span>`).join("")}</span>`;
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
    const hit = (name) => event.target.closest(`[${name}]`);
    if (hit("data-pool-close")) setOpen(false);
    else if (hit("data-pool-sharing")) show("sharing");
    else if (hit("data-pool-back")) back();
    else if (hit("data-pool-share")) save({ share: hit("data-pool-share").dataset.poolShare });
    else if (hit("data-pool-use")) save({ use: hit("data-pool-use").dataset.poolUse });
    else if (hit("data-pool-switch")) switchTo(Number(hit("data-pool-switch").dataset.poolSwitch));
    else if (hit("data-pool-stop")) {
      setOpen(false);
      deps.stop?.();
    }
    else if (hit("data-pool-first")) {
      const id = Number(hit("data-pool-first").dataset.poolFirst);
      save({ primary: current()?.prefs?.primary === id ? null : id });
    }
  });
  el.addEventListener("change", (event) => {
    const box = event.target.closest("[data-pool-acct]");
    if (!box) return;
    const prefs = current()?.prefs || {};
    const skip = new Set(prefs.skip || []);
    if (box.checked) skip.delete(Number(box.value));
    else skip.add(Number(box.value));
    save({ skip: [...skip] });
  });
  el.addEventListener("keydown", (event) => {
    const row = event.target.closest("[data-pool-switch]");
    if (!row || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    switchTo(Number(row.dataset.poolSwitch));
  });
  attachSheetSwipe(el, ".chat-picker-head", () => el.classList.contains("is-sheet"), () => setOpen(false));
  swipeBack(el);
  deps.overlays?.backLayerFollowsHidden(el, "pool");
  return el;
}

// On a phone sheet the sharing panel is one level in: a swipe to the right
// (like the system's back gesture) returns to the usage panel.
function swipeBack(el) {
  let start = null;
  el.addEventListener("pointerdown", (event) => {
    start = el.classList.contains("is-sheet") && view === "sharing" && event.pointerType !== "mouse"
      ? { x: event.clientX, y: event.clientY } : null;
  });
  el.addEventListener("pointercancel", () => { start = null; });
  el.addEventListener("pointerup", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = Math.abs(event.clientY - start.y);
    start = null;
    if (dx > SWIPE_BACK && dy < dx / 2) back();
  });
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
    // A tap on the sheet's close button must not land on what lies under it.
    if (menu.classList.contains("is-sheet")) deps.overlays?.armOverlayGhost(menu.querySelector("[data-pool-close]"));
    window.FuseBack?.drop("chat:pool-sharing");
    menu.hidden = true;
    view = "usage";
    notice = "";
    returnFocus(menu, btn);
    return;
  }
  if (menu.hidden) deps.closeOthers?.();
  fillMenu(pool);
  menu.hidden = false;
  placeMenu();
  menu.focus({ preventScroll: true });
}

export function closePool() {
  setOpen(false);
}

// Back from the sharing panel: its own entry on the phone's back stack, so the
// system back gesture steps out of it before it closes the sheet.
function back() {
  window.FuseBack?.drop("chat:pool-sharing");
  show("usage");
}

function show(next) {
  if (next === "sharing") window.FuseBack?.open("chat:pool-sharing", () => show("usage"));
  view = next;
  notice = "";
  const pool = current();
  if (!pool) return;
  fillMenu(pool);
  placeMenu();
  menu.querySelector(next === "sharing" ? "[data-pool-back]" : "[data-pool-sharing]")?.focus({ preventScroll: true });
}

// An email shows its name part (the whole address is its title); any other
// name, like the Sources page's "Claude plan 2", shows as it is.
function short(name) {
  const text = String(name || "");
  const at = text.indexOf("@");
  return at > 0 ? text.slice(0, at) : text;
}

function nameHtml(name) {
  return `<b title="${escapeHtml(name)}">${escapeHtml(short(name))}</b>`;
}

// pct null with cash: the account reports money left, not a share used.
function line(name, pct, meta, attrs = "", cash = "") {
  const bar = pct == null ? '<span class="chat-pool-bar is-empty"></span>' : `<span class="chat-pool-bar"><i style="width:${pct}%"></i></span>`;
  return `<li class="chat-perms-row chat-pool-line"${pct == null ? "" : ` data-level="${level(pct)}"`}${attrs}>` +
    `<span class="chat-perms-path chat-pool-name">${name}</span>${bar}` +
    `<span class="chat-pool-pct">${pct == null ? escapeHtml(cash) || "–" : pct + "%"}</span>` +
    `<span class="chat-perms-tag chat-pool-meta">${meta}</span></li>`;
}

function group(title, aside, rows, extra = "") {
  return `<section class="chat-perms-group"><p><span class="chat-perms-host">${title}</span>${extra}` +
    `<span class="chat-perms-count">${aside}</span></p><ul>${rows}</ul></section>`;
}

function head(title, backTo = "") {
  const back = backTo ? `<button type="button" class="chat-menu-close chat-pool-head-back" data-pool-back aria-label="Back to ${backTo}">${BACK_SVG}</button>` : "";
  return `<div class="chat-picker-head"><p>${back}${title}</p><button type="button" class="chat-menu-close" data-pool-close aria-label="Close">${CLOSE_SVG}</button></div>`;
}

function fillMenu(pool) {
  menu.dataset.view = view;
  if (view === "sharing") fillSharing(pool);
  else fillUsage(pool);
}

function fillUsage(pool) {
  const limit = (w) => line(escapeHtml(w.label), w.percent, w.reset_at ? escapeHtml(span(w.reset_at)) : "");
  const plan = escapeHtml(`${pool.pool}${pool.plan ? " " + pool.plan : ""}`);
  const windows = windowsOf(pool.windows);
  const limits = windowsOf(pool.limits);
  const accounts = Array.isArray(pool.accounts) ? pool.accounts : [];
  const acct = (a) => {
    const who = nameHtml(a.name) + (a.own ? '<span class="chat-pool-who">yours</span>' : "");
    const meta = a.status === "current" && pool.preview ? "next" :
      a.status === "limit" ? (a.until ? `back ${escapeHtml(span(a.until))}` : "at limit") : STATUS[a.status] || "";
    const pct = a.percent == null ? null : clamp(a.percent);
    // Another usable account: a click moves the chat there. While a switch
    // waits, a click on the account in use cancels it.
    const stay = a.status === "current" && pool.next;
    const pick = !saving && (a.status === "ready" || a.status === "full" || stay)
      ? ` data-pool-switch="${Number(a.id)}" role="button" tabindex="0" title="${stay ? "Stay on this account" : "Use this account for this chat (its prompt cache starts cold)"}"` : "";
    return line(who, pct, meta, ` data-status="${escapeHtml(a.status)}"${pick}`, money(a.balance));
  };
  const cash = money(pool.balance);
  const balance = cash ? line("Balance", null, "left", ` data-level="${moneyLevel(pool.balance)}"`, cash) : "";
  const hop = pool.hop && pool.hop.from
    ? `<li class="chat-perms-hint chat-pool-note">Moved here from ${escapeHtml(short(pool.hop.from))}${WHY[pool.hop.reason] ? `: ${WHY[pool.hop.reason]}` : ""}.</li>`
    : "";
  const pending = pool.next ? pendingNote(pool) : "";
  const prefs = pool.prefs || {};
  const shareName = (SHARE.find(([id]) => id === prefs.share) || SHARE[0])[1];
  // A key outside a pool (prefs null) has no sharing settings.
  const sharing = !pool.prefs ? "<span></span>" : prefs.can_override === false
    ? '<span class="chat-pool-share is-locked" title="The admin has turned sharing off for this pool">Sharing <b>off site-wide</b></span>'
    : `<button type="button" class="chat-pool-share" data-pool-sharing aria-haspopup="true">Sharing <b>${shareName}</b><span aria-hidden="true">›</span></button>`;
  menu.setAttribute("aria-label", `${pool.pool} ${pool.prefs ? "plan " : ""}usage`);
  menu.innerHTML = head(pool.prefs ? "Plan usage" : "Usage") +
    '<div class="chat-perms-list">' +
    group(plan, `<span title="${escapeHtml(pool.name)}">${escapeHtml(short(pool.name))}</span>`,
      (windows.map(limit).join("") + balance || '<li class="chat-perms-empty">No usage reported yet.</li>') + hop + pending) +
    (limits.length ? group("Model limits", "", limits.map(limit).join("")) : "") +
    (accounts.length ? group("Accounts", `${Number(pool.ready) || 0} of ${Number(pool.size) || 0} ready`, accounts.map(acct).join("")) : "") +
    (notice ? `<p class="chat-perms-hint chat-pool-notice" role="status">${escapeHtml(notice)}</p>` : "") +
    "</div>" +
    `<div class="chat-perms-foot chat-pool-foot">${sharing}<a class="chat-pool-manage" href="${MANAGE}">Manage sources</a></div>`;
}

// A pick made while a turn runs: the turn's request in flight finishes on the
// account it started on, the next one goes to the pick. Stop ends the turn
// now (asking about background subagents, which keep their own accounts).
function pendingNote(pool) {
  const from = escapeHtml(short(pool.name));
  const to = `<b title="${escapeHtml(pool.next.name)}">${escapeHtml(short(pool.next.name))}</b>`;
  const running = deps.running?.();
  const text = running
    ? `This turn is on ${from}; ${to} takes over from its next request.`
    : `The next reply uses ${to}.`;
  const stop = running && deps.stop
    ? ' <button type="button" class="chat-pool-share chat-pool-stop" data-pool-stop>Stop turn and <b>switch now</b></button>' : "";
  return `<li class="chat-perms-hint chat-pool-note chat-pool-pending" role="status">${text}${stop}</li>`;
}

// The Sources sharing dialog, condensed: who may use your accounts, which
// accounts your requests use, and the rotation with its first choice. Each
// click saves at once, like the permission modes.
function fillSharing(pool) {
  const prefs = pool.prefs || {};
  const accounts = Array.isArray(pool.accounts) ? pool.accounts : [];
  const mine = accounts.filter((a) => a.own).length;
  const label = escapeHtml(pool.pool);
  const segs = (kind, options, value, off = () => false) =>
    `<div class="chat-perms-modes" style="--segs: ${options.length}" role="radiogroup" aria-label="${kind === "share" ? "Who may use your accounts" : "Your requests use"}">` +
    options.map(([id, name]) => `<button type="button" role="radio" aria-checked="${id === value}" class="${id === value ? "is-on" : ""}" data-pool-${kind}="${id}"${off(id) || saving ? " disabled" : ""}>${name}</button>`).join("") +
    "</div>";
  const hint = (options, value) => escapeHtml(((options.find(([id]) => id === value) || options[0])[2])(pool.pool));
  const skip = new Set(prefs.skip || []);
  const pick = (a) => `<li class="chat-perms-row chat-pool-pick" data-status="${skip.has(a.id) ? "off" : "on"}">` +
    `<label class="chat-perms-path chat-pool-name"><input type="checkbox" value="${Number(a.id)}" data-pool-acct${skip.has(a.id) ? "" : " checked"}${saving ? " disabled" : ""}>` +
    nameHtml(a.name) + (a.own ? '<span class="chat-pool-who">yours</span>' : "") + "</label>" +
    `<button type="button" class="chat-perms-tag chat-pool-first${prefs.primary === a.id ? " is-on" : ""}" data-pool-first="${Number(a.id)}" aria-pressed="${prefs.primary === a.id}"${skip.has(a.id) || saving ? " disabled" : ""} title="New conversations start on this account while it has room">first</button></li>`;
  const note = notice ? `<p class="chat-perms-hint chat-pool-notice" role="status">${escapeHtml(notice)}</p>` :
    (mine ? "" : `<p class="chat-perms-hint">You have no ${label} account linked; these choices apply once you add one.</p>`);
  menu.setAttribute("aria-label", `${pool.pool} sharing`);
  menu.innerHTML = head("Sharing", "usage") +
    '<div class="chat-perms-list">' +
    `<section class="chat-perms-group chat-pool-setting"><p><span class="chat-perms-host">Who may use your accounts</span></p>` +
    segs("share", SHARE, prefs.saved_share || prefs.share) + `<p class="chat-perms-hint">${hint(SHARE, prefs.saved_share || prefs.share)}</p></section>` +
    `<section class="chat-perms-group chat-pool-setting"><p><span class="chat-perms-host">Your requests use</span></p>` +
    segs("use", USE, prefs.use, (id) => id === "pool" && (prefs.saved_share || prefs.share) === "private") +
    `<p class="chat-perms-hint">${hint(USE, prefs.use)}</p></section>` +
    (accounts.length > 1 ? group("Rotation", "first choice", accounts.map(pick).join("")) : "") +
    note + "</div>" +
    `<div class="chat-perms-foot chat-pool-foot"><button type="button" class="chat-pool-share" data-pool-back><span aria-hidden="true">‹</span> Usage</button><a class="chat-pool-manage" href="${MANAGE}">Manage sources</a></div>`;
}

function postSwitch(thread, id) {
  const body = new URLSearchParams({ model: S.modelId, account: String(id) });
  if (thread) body.set("thread", thread);
  return fetch("/chat/pool/switch", {
    method: "POST",
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken() },
    body,
  }).then((res) => (res.ok ? res.json() : null)).then((data) => data?.pool || null).catch(() => null);
}

// Pins the chat to another account; the server answers with its badge. A
// chat not sent yet only previews it until the send creates the chat.
async function switchTo(id) {
  const pool = current();
  if (!pool || saving || !(id > 0)) return;
  const key = previewKey();
  const thread = S.currentId;
  saving = { switch: id };
  notice = "";
  fillMenu(pool);
  const fresh = await postSwitch(thread, id);
  saving = null;
  if (previewKey() !== key || !current()) return;
  if (fresh?.name) {
    if (!thread) choice = { key, id };
    notice = fresh.next ? "" : `The next reply uses ${short(fresh.name)}, so it starts a new prompt cache.`;
    previews.set(key, { at: Date.now(), pool: fresh });
    acceptPool(fresh);
  } else {
    notice = "Could not move this chat to that account.";
    if (menu && !menu.hidden) fillMenu(current());
  }
}

// The send's new chat takes the account picked before the chat existed.
export async function claimPoolChoice() {
  const picked = choice;
  choice = null;
  if (!picked || !S.currentId || picked.key !== `|${S.modelId || ""}`) return;
  const fresh = await postSwitch(S.currentId, picked.id);
  if (fresh?.name) acceptPool(fresh);
}

async function save(change) {
  const pool = current();
  if (!pool || saving) return;
  const prefs = { ...(pool.prefs || {}) };
  const share = change.share || prefs.saved_share || prefs.share || "everyone";
  const use = share === "private" ? "mine" : change.use || prefs.use || "pool";
  const ids = (pool.accounts || []).map((a) => Number(a.id));
  const skip = new Set(change.skip || prefs.skip || []);
  if (ids.length && ids.every((id) => skip.has(id))) {
    notice = "Keep at least one account in your rotation.";
    fillMenu(pool);
    return;
  }
  const primary = "primary" in change ? change.primary : prefs.primary;
  const body = new URLSearchParams({ share, use });
  if (ids.length > 1) {
    body.set("accounts_listed", ids.join(","));
    ids.filter((id) => !skip.has(id)).forEach((id) => body.append("account", String(id)));
    body.set("primary", primary != null && !skip.has(primary) ? String(primary) : "");
  }
  saving = change;
  notice = "";
  fillMenu(pool);
  let reply = null;
  try {
    const res = await fetch(`/sources/pool/${encodeURIComponent(pool.provider)}/prefs`, {
      method: "POST",
      headers: { Accept: "application/json", "X-CSRF-Token": csrfToken() },
      body,
    });
    reply = res.ok ? await res.json() : null;
  } catch (_) {
    reply = null;
  }
  saving = null;
  const now = current();
  if (!now) return;
  if (reply && reply.prefs) {
    now.prefs = reply.prefs;
    const off = new Set(reply.prefs.skip || []);
    (now.accounts || []).forEach((a) => {
      a.primary = reply.prefs.primary === a.id;
      if (off.has(a.id) && a.status !== "current") a.status = "off";
      else if (!off.has(a.id) && a.status === "off") a.status = "ready";
    });
  }
  notice = !reply ? "Could not save. Try again, or use Manage sources." : reply.ok ? "" : reply.message || "";
  if (menu && !menu.hidden) fillMenu(now);
}

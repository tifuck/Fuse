// The pool account badge beside the model (app/sources/pool_badge.py): which
// account of a shared pool served this chat, named only by a slot letter, and
// how much of its usage windows is spent. Each theme places [data-pool-badge]
// (a [data-pool-toggle] button and a [data-pool-pop] card) and styles the
// parts; a theme shows the ring, the percent or the window bars as it likes.
import { S } from './state.js';
import { escapeHtml } from './core.js';

const RING = 2 * Math.PI * 6.5;
const CELLS = 5;
const WHY = { limit: "it reached its usage limit", error: "it stopped answering" };

const level = (pct) => (pct >= 95 ? "hot" : pct >= 75 ? "warn" : "ok");

function resetsIn(at) {
  const left = Number(at) - Date.now() / 1000;
  if (!(left > 0)) return "";
  const hours = Math.floor(left / 3600);
  const minutes = Math.max(1, Math.round((left % 3600) / 60));
  if (hours >= 48) return `resets in ${Math.round(hours / 24)}d`;
  return hours ? `resets in ${hours}h ${minutes}m` : `resets in ${minutes}m`;
}

// The chat's badge, or null when it has none, belongs to another chat, or the
// picker moved to another model since the account answered.
function current() {
  const pool = S.pool;
  return pool && S.poolThread === S.currentId && S.poolModel === S.modelId ? pool : null;
}

export function acceptPool(pool) {
  const before = current()?.slot || "";
  S.pool = pool && typeof pool === "object" && pool.slot ? pool : null;
  S.poolThread = S.currentId;
  S.poolModel = S.modelId;
  renderPool(Boolean(before && S.pool && before !== S.pool.slot));
}

function windowsOf(pool) {
  return (Array.isArray(pool.windows) ? pool.windows : [])
    .map((w) => ({ ...w, percent: Math.max(0, Math.min(100, Math.round(Number(w.percent) || 0))) }));
}

function setPop(host, open) {
  const btn = host.querySelector("[data-pool-toggle]");
  const pop = host.querySelector("[data-pool-pop]");
  const pool = current();
  if (!btn || !pop || pop.hidden === !(open && pool)) return;
  pop.hidden = !(open && pool);
  btn.setAttribute("aria-expanded", String(!pop.hidden));
  pop.style.translate = "";
  if (pop.hidden) return;
  fillPop(pop, pool);
  // Keep the card on screen: the badge sits at a different spot per theme.
  const box = pop.getBoundingClientRect();
  const pad = 8;
  const shift = box.left < pad ? pad - box.left : box.right > innerWidth - pad ? innerWidth - pad - box.right : 0;
  if (shift) pop.style.translate = `${Math.round(shift)}px 0`;
}

function wire(host) {
  if (host.dataset.poolWired) return;
  host.dataset.poolWired = "1";
  const btn = host.querySelector("[data-pool-toggle]");
  const pop = host.querySelector("[data-pool-pop]");
  btn?.addEventListener("click", (event) => {
    event.preventDefault();
    setPop(host, pop.hidden);
  });
  document.addEventListener("pointerdown", (event) => { if (!host.contains(event.target)) setPop(host, false); });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !pop || pop.hidden) return;
    setPop(host, false);
    btn?.focus();
  });
}

function cells(pct) {
  const on = Math.round((pct / 100) * CELLS);
  return Array.from({ length: CELLS }, (_, i) => `<i${i < on ? ' class="is-on"' : ""}></i>`).join("");
}

function fillPop(pop, pool) {
  const rows = windowsOf(pool).map((w) => {
    const reset = resetsIn(w.reset_at);
    return `<li data-level="${level(w.percent)}"><span>${escapeHtml(w.label)}</span>` +
      `<span class="chat-pool-meter"><i style="width:${w.percent}%"></i></span>` +
      `<span class="chat-pool-num">${w.percent}%</span>` +
      (reset ? `<small>${reset}</small>` : "") + "</li>";
  }).join("");
  const hop = pool.hop && pool.hop.from && pool.hop.from !== pool.slot
    ? `<p class="chat-pool-hop">Switched from ${escapeHtml(pool.hop.from)}${WHY[pool.hop.reason] ? `: ${WHY[pool.hop.reason]}` : ""}.</p>`
    : "";
  pop.innerHTML =
    `<div class="chat-context-pop-head"><p class="chat-context-pop-kicker">${escapeHtml(pool.pool)} pool</p>` +
    `<p class="chat-pool-ready">${Number(pool.ready) || 0} of ${Number(pool.size) || 0} ready</p></div>` +
    `<p class="chat-pool-acct">Account <b>${escapeHtml(pool.slot)}</b> · ${pool.own ? "yours" : "shared"}</p>` +
    (rows ? `<ul class="chat-pool-rows">${rows}</ul>` : '<p class="chat-pool-none">No usage reported yet.</p>') +
    hop + '<a class="chat-pool-link" href="/sources">Manage accounts</a>';
}

export function renderPool(hopped = false) {
  const host = S.root?.querySelector("[data-pool-badge]");
  if (!host) return;
  wire(host);
  const pool = current();
  host.hidden = !pool;
  if (!pool) {
    setPop(host, false);
    return;
  }
  const btn = host.querySelector("[data-pool-toggle]");
  const pop = host.querySelector("[data-pool-pop]");
  const windows = windowsOf(pool);
  const tight = windows.reduce((a, w) => (!a || w.percent > a.percent ? w : a), null);
  const peak = tight ? tight.percent : null;
  const slot = escapeHtml(pool.slot);
  host.dataset.level = peak == null ? "none" : level(peak);
  host.dataset.own = pool.own ? "true" : "false";
  const label = `${pool.pool} account ${pool.slot}${pool.own ? ", yours" : ", shared"}` +
    (tight ? `, ${peak}% of ${tight.label} used` : "");
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.innerHTML =
    `<span class="chat-pool-ring" aria-hidden="true"><svg viewBox="0 0 18 18"><circle class="chat-pool-face" cx="9" cy="9" r="6.5"/>` +
    `<circle class="chat-pool-track" cx="9" cy="9" r="6.5"/>` +
    `<circle class="chat-pool-fill" cx="9" cy="9" r="6.5" stroke-dasharray="${((peak || 0) / 100) * RING} ${RING}"/></svg><b>${slot}</b></span>` +
    `<span class="chat-pool-slot" aria-hidden="true">${slot}</span>` +
    (peak == null ? "" : `<span class="chat-pool-pct" aria-hidden="true">${peak}%</span>`) +
    `<span class="chat-pool-wins" aria-hidden="true">${windows.map((w) =>
      `<span class="chat-pool-win" data-level="${level(w.percent)}"${w === tight ? " data-tight" : ""}><em>${escapeHtml(w.short || "")}</em>` +
      `<span class="chat-pool-cells">${cells(w.percent)}</span><b>${w.percent}</b></span>`).join("")}</span>`;
  if (!pop.hidden) fillPop(pop, pool);
  if (hopped && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    host.classList.remove("is-hop");
    void host.offsetWidth;
    host.classList.add("is-hop");
    host.addEventListener("animationend", () => host.classList.remove("is-hop"), { once: true });
  }
}

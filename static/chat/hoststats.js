// Machine stats card: live CPU, RAM, disk and network of the session's
// connector machine, under the composer. One socket while the page is
// visible and the chat is bound to a machine; the connector samples only
// while that socket is open. Each theme draws the card ([data-host-stats]).
import { S } from './state.js';

// No sample for this long (connector offline, sampling stopped): hide.
const STALE_MS = 7000;

// White to 50%, amber at 75%, red from 95%; mixed in oklch so the ramp
// stays even. Below 50% the theme's own colour stands.
export function loadTone(pct) {
  if (!(pct > 50)) return "";
  if (pct >= 95) return "#ff453a";
  if (pct < 75) return `color-mix(in oklch, #f5b83d ${Math.round((pct - 50) * 4)}%, #fff)`;
  return `color-mix(in oklch, #ff453a ${Math.round((pct - 75) * 5)}%, #f5b83d)`;
}

// Bytes per second, short: 0B, 840B, 84K, 1.2M, 3.4G.
export function shortRate(bps) {
  const n = Math.max(0, Number(bps) || 0);
  const units = [["G", 1e9], ["M", 1e6], ["K", 1e3]];
  for (const [unit, size] of units) {
    if (n >= size * 0.995) {
      const v = n / size;
      return (v < 9.95 ? v.toFixed(1) : String(Math.round(v))) + unit;
    }
  }
  return Math.round(n) + "B";
}

function gib(bytes) {
  const v = (Number(bytes) || 0) / 2 ** 30;
  return (v < 9.95 ? v.toFixed(1) : String(Math.round(v))) + " GB";
}

const pct = (part, whole) => (whole > 0 ? (100 * part) / whole : 0);

// Recent samples as block characters, newest last: "▁▂▅█".
export function loadBlocks(samples) {
  return samples.map((p) => "▁▂▃▄▅▆▇█"[Math.min(7, Math.max(0, Math.round((p / 100) * 7)))]).join("");
}

const HISTORY = 24;

// Recent samples as polyline points in a w×h box, newest at the right edge.
export function sparkPoints(samples, top, w, h) {
  const step = w / (HISTORY - 1);
  const start = w - step * (samples.length - 1);
  return samples.map((v, i) => `${(start + i * step).toFixed(1)},${(h - 1 - (Math.min(v, top) / top) * (h - 2)).toFixed(1)}`).join(" ");
}

// The details card: [label, detail, percent or ""] per row.
export function detailRows(d) {
  const [memUsed, memTotal] = d.mem;
  const [rx, tx] = d.net;
  const share = (part, whole) => Math.round(pct(part, whole)) + "%";
  return [
    ["CPU", "", Math.round(d.cpu) + "%"],
    ["Memory", `${gib(memUsed)} of ${gib(memTotal)}`, share(memUsed, memTotal)],
    d.vol && ["Storage", `${gib(d.vol[0])} of ${gib(d.vol[1])}`, share(d.vol[0], d.vol[1])],
    d.disk && ["Disk", `read ${shortRate(d.disk[1])}/s, write ${shortRate(d.disk[2])}/s`, Math.round(d.disk[0]) + "%"],
    ["Network", `↓ ${shortRate(rx)}/s  ↑ ${shortRate(tx)}/s`, ""],
  ].filter(Boolean);
}

// A new sample glides in instead of jumping: fast, eased out, then still.
const GLIDE_MS = 260;
export function glide(from, to, t) {
  const k = Math.min(1, Math.max(0, t));
  return from + (to - from) * (1 - (1 - k) ** 3);
}

export function createHostStats() {
  const card = S.root?.querySelector("[data-host-stats]");
  const slot = (name) => card?.querySelector(`[data-hs-${name}]`);
  const el = card ? {
    alias: slot("alias"), cpu: slot("cpu"), ram: slot("ram"), disk: slot("disk"), rx: slot("rx"), tx: slot("tx"),
    hostSlice: slot("host-slice"), diskSlice: slot("disk-slice"), netSlice: slot("net-slice"),
    cpuHist: slot("cpu-hist"), toggle: slot("toggle"), pop: slot("pop"),
  } : null;
  const history = { cpu: [] };
  // A theme may keep the card when samples stop (data-host-stats-keep): it
  // greys out in place instead of leaving, so nothing around it moves.
  const keep = Boolean(card?.hasAttribute("data-host-stats-keep"));
  // A theme may give the card a second place for narrow screens: the
  // element with data-host-stats-narrow, whose value is the media query.
  const narrow = S.root?.querySelector("[data-host-stats-narrow]");
  const narrowQuery = card && narrow ? window.matchMedia?.(narrow.dataset.hostStatsNarrow) : null;
  const home = card ? [card.parentElement, card.nextSibling] : null;
  const refused = new Set();
  let hostId = 0;
  let alias = "";
  let ws = null;
  let retry = 0;
  let stale = 0;
  let fitKey = "";
  let shown = null;
  let from = null;
  let goal = null;
  let glideAt = 0;
  let frame = 0;
  let last = null;

  function hide() {
    window.clearTimeout(stale);
    window.cancelAnimationFrame(frame);
    frame = 0;
    shown = null;
    setPop(false);
    card?.classList.remove("is-stale");
    if (card && !card.hidden) card.hidden = true;
    fitKey = "";
  }

  // No sample for a while: a kept card greys out with dashes, others hide.
  function stall() {
    if (!keep || card.hidden) { hide(); return; }
    window.cancelAnimationFrame(frame);
    frame = 0;
    card.classList.add("is-stale");
    for (const node of [el.cpu, el.ram, el.disk, el.rx, el.tx]) set(node, "–", "");
    for (const name of ["cpu", "ram", "disk"]) {
      card.style.removeProperty(`--hs-${name}`);
      card.style.removeProperty(`--hs-${name}-tone`);
    }
    fillPop();
  }

  function close() {
    window.clearTimeout(retry);
    history.cpu.length = 0;
    const sock = ws;
    ws = null;
    sock?.close();
    hide();
  }

  function open() {
    if (!card || ws || !hostId || document.hidden || refused.has(hostId)) return;
    const id = hostId;
    const url = new URL(`/hosts/${id}/stats`, location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const sock = new WebSocket(url);
    ws = sock;
    sock.onmessage = (event) => {
      if (ws !== sock) return;
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      if (data?.type === "stats") paint(data);
    };
    sock.onclose = (event) => {
      if (ws !== sock) return;
      ws = null;
      // Not a connector, not this user's, signed out: no point asking again.
      if ([4401, 4403, 4404].includes(event.code)) { hide(); refused.add(id); return; }
      // A kept card greys out once its samples are overdue, not on every reconnect.
      if (!keep) hide();
      retry = window.setTimeout(open, event.code === 1012 ? 1000 : 5000);
    };
  }

  function set(node, text, tone) {
    if (!node) return;
    if (node.textContent !== text) node.textContent = text;
    if (tone !== undefined && node.style.color !== tone) node.style.color = tone;
  }

  // The theme's meters and rings read --hs-<name> (0-100) and --hs-<name>-tone.
  function meter(name, value) {
    card.style.setProperty(`--hs-${name}`, String(value));
    const tone = loadTone(value);
    if (tone) card.style.setProperty(`--hs-${name}-tone`, tone);
    else card.style.removeProperty(`--hs-${name}-tone`);
  }

  function draw(v) {
    set(el.cpu, Math.round(v.cpu) + "%", loadTone(Math.round(v.cpu)));
    set(el.ram, Math.round(v.ram) + "%", loadTone(Math.round(v.ram)));
    meter("cpu", v.cpu);
    meter("ram", v.ram);
    if (v.disk !== undefined) {
      set(el.disk, Math.round(v.disk) + "%", loadTone(Math.round(v.disk)));
      meter("disk", v.disk);
    }
    set(el.rx, shortRate(v.rx));
    set(el.tx, shortRate(v.tx));
  }

  function step(now) {
    const t = (now - glideAt) / GLIDE_MS;
    for (const key in goal) shown[key] = glide(from[key] ?? goal[key], goal[key], t);
    draw(shown);
    frame = t < 1 ? window.requestAnimationFrame(step) : 0;
  }

  // The first sample after showing, or with reduced motion, lands at once.
  function show(next) {
    if (!shown || document.hidden || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      window.cancelAnimationFrame(frame);
      frame = 0;
      shown = { ...next };
      draw(shown);
      return;
    }
    from = { ...shown };
    goal = next;
    shown = { ...next, ...shown };
    glideAt = performance.now();
    if (!frame) frame = window.requestAnimationFrame(step);
  }

  function remember(list, value) {
    list.push(value);
    if (list.length > HISTORY) list.shift();
  }

  function paint(d) {
    const [memUsed, memTotal] = d.mem;
    const [rx, tx] = d.net;
    const cpu = Math.round(d.cpu);
    const ram = Math.round(pct(memUsed, memTotal));
    last = d;
    card.classList.remove("is-stale");
    set(el.alias, alias || "machine");
    if (el.diskSlice) el.diskSlice.hidden = !d.disk;
    const next = { cpu: d.cpu, ram: pct(memUsed, memTotal), rx, tx };
    if (d.disk) next.disk = d.disk[0];
    show(next);
    remember(history.cpu, cpu);
    if (el.cpuHist) set(el.cpuHist, loadBlocks(history.cpu));
    // A card with its own details card leaves the browser tooltip out.
    const lines = el.pop ? "" : [
      alias || "Machine",
      `CPU ${cpu}%`,
      `Memory ${gib(memUsed)} of ${gib(memTotal)} (${ram}%)`,
      d.disk && `Disk ${Math.round(d.disk[0])}% busy, read ${shortRate(d.disk[1])}/s, write ${shortRate(d.disk[2])}/s`,
      d.vol && `Storage ${gib(d.vol[0])} of ${gib(d.vol[1])} used`,
      `Network down ${shortRate(rx)}/s, up ${shortRate(tx)}/s`,
    ].filter(Boolean).join("\n");
    if (card.title !== lines) card.title = lines;
    if (card.hidden) card.hidden = false;
    fit();
    fillPop();
    window.clearTimeout(stale);
    stale = window.setTimeout(stall, STALE_MS);
  }

  // The details card ([data-hs-pop]), opened by [data-hs-toggle].
  function setPop(open) {
    if (!el?.pop || el.pop.hidden === !open) return;
    el.pop.hidden = !open;
    el.toggle?.setAttribute("aria-expanded", String(open));
    if (open) fillPop();
    else if (!card.hidden) { fitKey = ""; fit(); }
  }

  function fillPop() {
    const pop = el.pop;
    if (!pop || pop.hidden || !last) return;
    if (!pop.firstChild) pop.innerHTML = '<p class="hs-pop-head"><b></b><span></span></p><dl class="hs-pop-rows"></dl>';
    const [name, state] = pop.firstChild.children;
    const live = !card.classList.contains("is-stale");
    set(name, alias || "Machine");
    set(state, live ? "Live" : "No recent data");
    pop.lastChild.replaceChildren(...detailRows(last).map(([label, detail, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const info = document.createElement("dd");
      const num = document.createElement("dd");
      term.textContent = label;
      if (label === "CPU") info.innerHTML = `<svg viewBox="0 0 100 16" preserveAspectRatio="none" aria-hidden="true"><polyline points="${sparkPoints(history.cpu, 100, 100, 16)}"></polyline></svg>`;
      else info.textContent = detail;
      num.textContent = value;
      num.style.color = live ? loadTone(parseFloat(value)) : "";
      row.append(term, info, num);
      return row;
    }));
  }

  // Whatever does not fit between the composer's own controls goes:
  // the machine name first, then the network, then the disk, then the whole card.
  function room() {
    const row = card.parentElement;
    const groups = [...row.querySelectorAll(":scope > [data-box-group]")];
    if (groups.length < 2) return row.clientWidth;
    const r = row.getBoundingClientRect();
    const mid = r.left + r.width / 2;
    const edges = (group) => [...group.children]
      .map((child) => child.getBoundingClientRect())
      .filter((b) => b.width && b.bottom > r.top && b.top < r.bottom);
    const left = Math.max(r.left, ...edges(groups[0]).map((b) => b.right));
    const right = Math.min(r.right, ...edges(groups[1]).map((b) => b.left));
    return 2 * Math.min(mid - left, right - mid) - 24;
  }

  function fit() {
    // The open details card would count toward the card's width.
    if (el.pop && !el.pop.hidden) return;
    const key = `${card.parentElement.clientWidth}|${[el.cpu, el.ram, el.disk, el.rx, el.tx, el.alias].map((n) => n?.textContent.length).join(",")}`;
    if (key === fitKey) return;
    fitKey = key;
    const steps = [el.hostSlice, el.netSlice, el.diskSlice].filter(Boolean);
    card.classList.remove("is-squeezed");
    steps.forEach((node) => node.classList.remove("is-dropped"));
    const space = room();
    for (const node of steps) {
      if (card.scrollWidth <= space) return;
      node.classList.add("is-dropped");
    }
    if (card.scrollWidth > space) card.classList.add("is-squeezed");
  }

  function place() {
    setPop(false);
    if (narrowQuery.matches) narrow.append(card);
    else home[0].insertBefore(card, home[1]);
    fitKey = "";
    if (!card.hidden) fit();
  }

  // Called whenever the chat's identity is synced (thread, project session).
  function sync(bound) {
    if (!card) return;
    const next = Number(bound?.host_id) || 0;
    alias = bound?.host_alias || "";
    if (el.alias && card.hidden === false) set(el.alias, alias || "machine");
    if (next === hostId) return;
    close();
    hostId = next;
    open();
  }

  if (narrowQuery) {
    place();
    narrowQuery.addEventListener("change", place);
  }
  if (card) {
    document.addEventListener("visibilitychange", () => (document.hidden ? close() : open()));
    window.addEventListener("resize", () => { if (!card.hidden) { fitKey = ""; fit(); } });
  }
  if (el?.toggle && el.pop) {
    el.toggle.addEventListener("click", () => setPop(el.pop.hidden));
    document.addEventListener("pointerdown", (event) => { if (!card.contains(event.target)) setPop(false); });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || el.pop.hidden) return;
      setPop(false);
      el.toggle.focus();
    });
  }

  return { sync };
}

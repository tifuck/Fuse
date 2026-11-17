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
    cpuHist: slot("cpu-hist"), netSpark: slot("net-spark"),
  } : null;
  const history = { cpu: [], net: [] };
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

  function hide() {
    window.clearTimeout(stale);
    window.cancelAnimationFrame(frame);
    frame = 0;
    shown = null;
    if (card && !card.hidden) card.hidden = true;
    fitKey = "";
  }

  function close() {
    window.clearTimeout(retry);
    history.cpu.length = history.net.length = 0;
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
      hide();
      // Not a connector, not this user's, signed out: no point asking again.
      if ([4401, 4403, 4404].includes(event.code)) { refused.add(id); return; }
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
    set(el.alias, alias || "machine");
    if (el.diskSlice) el.diskSlice.hidden = !d.disk;
    const next = { cpu: d.cpu, ram: pct(memUsed, memTotal), rx, tx };
    if (d.disk) next.disk = d.disk[0];
    show(next);
    remember(history.cpu, cpu);
    remember(history.net, rx + tx);
    if (el.cpuHist) set(el.cpuHist, loadBlocks(history.cpu));
    if (el.netSpark) {
      const top = Math.max(...history.net, 1);
      const step = 30 / (HISTORY - 1);
      const start = 30 - step * (history.net.length - 1);
      el.netSpark.setAttribute("points", history.net.map((v, i) => `${(start + i * step).toFixed(1)},${(9 - (v / top) * 8).toFixed(1)}`).join(" "));
    }
    const lines = [
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
    window.clearTimeout(stale);
    stale = window.setTimeout(hide, STALE_MS);
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

  if (card) {
    document.addEventListener("visibilitychange", () => (document.hidden ? close() : open()));
    window.addEventListener("resize", () => { if (!card.hidden) { fitKey = ""; fit(); } });
  }

  return { sync };
}

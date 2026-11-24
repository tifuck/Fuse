// Interactive terminal: real shells on the user's machines, as tabs in a
// place the theme gives it ([data-terminal]: a bottom dock, a pane tab, a
// full-height sheet on a phone). Sessions live on the server
// (/terminal/sessions) and outlive the page; a reload or a dropped socket
// reattaches them. xterm.js loads on first open. Terminal text never enters
// the page as HTML.
//
// Theme hooks inside [data-terminal]: [data-terminal-tabs], [data-terminal-new]
// with its [data-terminal-menu], [data-terminal-body], [data-terminal-hide],
// [data-terminal-search] and [data-terminal-find] (input, prev, next, close),
// [data-terminal-font="-1|1"], [data-terminal-line] with
// [data-terminal-line-form] / [data-terminal-line-input], [data-terminal-keys],
// [data-terminal-resize]. Anywhere: [data-terminal-toggle]. The runtime sets
// data-terminal-open on the chat root and sends "fuse-terminal" ({open});
// a theme asks for a change with "fuse-terminal-set" ({open}).
import { S } from './state.js';
import { cssColorHex, pasteNeedsConfirm, safeLink, terminalPalette } from './terminal/core.js';
import { createSession } from './terminal/session.js';
import { bindLineMode, createKeyBar } from './terminal/keybar.js';

const STORE = "fuse.chat.terminal.v1";
const FONT = { min: 9, max: 24, base: 13 };

let modules = null;
function loadXterm() {
  modules ||= Promise.all([
    import('../vendor/xterm/xterm.mjs'),
    import('../vendor/xterm/addon-fit.mjs'),
    import('../vendor/xterm/addon-webgl.mjs'),
    import('../vendor/xterm/addon-unicode11.mjs'),
    import('../vendor/xterm/addon-web-links.mjs'),
    import('../vendor/xterm/addon-search.mjs'),
    import('../vendor/xterm/addon-clipboard.mjs'),
  ]).then(([core, fit, webgl, unicode, links, search, clipboard]) => ({
    Terminal: core.Terminal,
    FitAddon: fit.FitAddon,
    WebglAddon: webgl.WebglAddon,
    Unicode11Addon: unicode.Unicode11Addon,
    WebLinksAddon: links.WebLinksAddon,
    SearchAddon: search.SearchAddon,
    ClipboardAddon: clipboard.ClipboardAddon,
  })).catch((error) => {
    modules = null;
    throw error;
  });
  return modules;
}

function readStore() {
  try { return JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch (_) { return {}; }
}
function writeStore(patch) {
  try { localStorage.setItem(STORE, JSON.stringify({ ...readStore(), ...patch })); } catch (_) {}
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

export function createTerminal(ctx) {
  const { net, sidebar, picker } = ctx;
  const root = S.root;
  const dock = root?.querySelector("[data-terminal]");
  const idle = { toggle() {}, open() {}, close() {}, snapshot: () => null, resume() {} };
  if (!dock) return idle;
  const q = (name) => dock.querySelector(`[data-terminal-${name}]`);
  const tabsEl = q("tabs");
  const body = q("body");
  const menu = q("menu");
  const newBtn = q("new");
  const find = q("find");
  const findInput = find?.querySelector("input");
  const lineBtn = q("line");
  const lineForm = q("line-form");
  const keysEl = q("keys");
  const resizer = q("resize");
  const mobile = window.matchMedia("(max-width: 800px)");
  // The page saves its own state; a reload reattaches every session.
  dock.setAttribute("data-reload-safe", "");

  const sessions = new Map();
  let xt = null;
  let listed = false;
  let opened = false;
  let activeId = "";
  let empty = null;
  let font = { family: "", size: Math.min(FONT.max, Math.max(FONT.min, Number(readStore().font) || FONT.base)) };
  let palette = null;

  const active = () => sessions.get(activeId) || null;

  // ---- open and close ----------------------------------------------------

  function setOpen(open, { fromBack = false } = {}) {
    if (opened === open) return;
    opened = open;
    if (open) root.dataset.terminalOpen = "";
    else delete root.dataset.terminalOpen;
    dock.hidden = !open;
    root.querySelectorAll("[data-terminal-toggle]").forEach((btn) => btn.setAttribute("aria-expanded", String(open)));
    if (window.FuseBack && mobile.matches) {
      if (open) FuseBack.open("chat:terminal", () => setOpen(false, { fromBack: true }));
      else if (!fromBack) FuseBack.drop("chat:terminal");
    }
    if (!open) {
      closeMenu();
      if (dock.contains(document.activeElement)) document.activeElement.blur();
    }
    document.dispatchEvent(new CustomEvent("fuse-terminal", { detail: { open } }));
  }

  async function open({ focus = true, want = "", fresh = true } = {}) {
    setOpen(true);
    try {
      await ready();
    } catch (error) {
      showEmpty(error.message || "Could not load the terminal.");
      return;
    }
    if (want && sessions.has(want)) activate(want, focus);
    else if (active()) activate(activeId, focus);
    else if (sessions.size) activate(sessions.keys().next().value, focus);
    else if (fresh && sidebar.sessionProject()) await start(projectParams(), focus);
    else showEmpty();
  }

  function toggle() {
    if (opened) setOpen(false);
    else open().catch((error) => picker.setHint(error.message || "Could not open the terminal."));
  }

  // xterm, the font and the sessions the server still holds (first open only).
  async function ready() {
    if (!xt) {
      const loaded = loadXterm();
      font.family = codeFont();
      await Promise.all([loaded, document.fonts?.load(`${font.size}px ${font.family}`).catch(() => {})]);
      xt = await loaded;
      palette = readPalette();
    }
    if (listed) return;
    const data = await net.api("/terminal/sessions");
    listed = true;
    for (const info of data.sessions || []) if (info.alive !== false && !sessions.has(info.id)) add(info, { host_id: info.host_id, shell: info.shell });
  }

  // ---- sessions ------------------------------------------------------------

  function projectParams() {
    const project = sidebar.sessionProject();
    const thread = Number(S.currentId);
    // The server takes host and folder from the chat's binding; a project
    // session with no chat yet opens in the machine's home folder.
    return Number.isInteger(thread) && thread > 0 && !S.pendingProject ? { thread_id: thread } : { host_id: Number(project?.host_id) };
  }

  // A first size so the shell starts close to its final one (fit corrects it).
  function guessSize() {
    const w = body.clientWidth || dock.clientWidth || 800;
    const h = body.clientHeight || 300;
    return { cols: Math.max(20, Math.floor(w / (font.size * 0.6))), rows: Math.max(5, Math.floor(h / (font.size * 1.2))) };
  }

  async function start(params, focus = true, replace = null) {
    closeMenu();
    let info;
    try {
      info = await net.api("/terminal/sessions", { method: "POST", body: JSON.stringify({ ...params, ...guessSize() }) });
    } catch (error) {
      const text = error.message || "Could not open a terminal.";
      if (sessions.size) picker.setHint(text);
      else showEmpty(text);
      return null;
    }
    const session = add(info, params, replace);
    activate(session.id, focus);
    return session;
  }

  function add(info, params, replace = null) {
    const pane = el("div", "term-pane");
    pane.dataset.termPane = info.id;
    pane.hidden = true;
    const screen = el("div", "term-screen");
    screen.dataset.termScreen = "";
    const note = el("div", "term-note");
    note.dataset.termNote = "";
    note.hidden = true;
    note.append(el("p", "term-note-text"), el("div", "term-note-actions"));
    pane.append(screen, note);
    if (replace) replace.pane.before(pane);
    else body.append(pane);
    const session = createSession(info, {
      xt, pane, palette, font,
      onChange: paint,
      transform: (data) => keyBar?.transform(data) ?? data,
      keyHandler,
      openLink,
      pasteCheck: pasteNeedsConfirm,
      confirmPaste,
    });
    session.params = params;
    if (replace) {
      const entries = [...sessions].map(([id, s]) => (id === replace.id ? [session.id, session] : [id, s]));
      replace.dispose();
      sessions.clear();
      entries.forEach(([id, s]) => sessions.set(id, s));
      if (activeId === replace.id) activeId = session.id;
    } else sessions.set(session.id, session);
    clearEmpty();
    renderTabs();
    return session;
  }

  function activate(id, focus = false) {
    const session = sessions.get(id);
    if (!session) return;
    activeId = id;
    for (const s of sessions.values()) s.pane.hidden = s !== session;
    renderTabs();
    session.connect();
    session.refit();
    if (focus && !session.pane.querySelector("[data-term-note]:not([hidden])")) session.focus();
  }

  function remove(session) {
    const ids = [...sessions.keys()];
    const at = ids.indexOf(session.id);
    sessions.delete(session.id);
    session.dispose();
    if (activeId === session.id) {
      activeId = "";
      const next = ids[at + 1] ?? ids[at - 1];
      if (next && sessions.has(next)) activate(next, true);
    }
    renderTabs();
    if (!sessions.size) showEmpty();
  }

  async function closeSession(session) {
    if (["live", "connecting"].includes(session.state)) {
      const ok = await window.fuseConfirm("Close this terminal? The shell and anything running in it end.", { confirmLabel: "Close", danger: true });
      if (!ok) return;
    }
    session.kill();
    net.api(`/terminal/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" }).catch(() => {});
    remove(session);
  }

  // ---- drawing (textContent only: titles and hosts are remote text) ------

  function label(session) {
    return session.title || session.info.alias || "terminal";
  }

  function paint(session) {
    const note = session.pane.querySelector("[data-term-note]");
    const show = ["exited", "lost"].includes(session.state) || Boolean(session.note);
    note.hidden = !show;
    note.dataset.state = session.state;
    note.firstChild.textContent = session.note || (session.state === "connecting" ? "Connecting…" : "");
    const actions = [];
    if (session.state === "exited") actions.push(["restart", "Restart"], ["dismiss", "Close"]);
    else if (session.state === "lost") actions.push(...(session.info.alive === false ? [] : [["reconnect", "Reconnect"]]), ["dismiss", "Close"]);
    note.lastChild.replaceChildren(...actions.map(([action, text]) => {
      const btn = el("button", "term-note-btn", text);
      btn.type = "button";
      btn.dataset.termAction = action;
      return btn;
    }));
    renderTabs();
  }

  function renderTabs() {
    if (!tabsEl) return;
    tabsEl.setAttribute("role", "tablist");
    tabsEl.replaceChildren(...[...sessions.values()].map((session) => {
      const info = session.info;
      const wrap = el("div", "term-tab");
      wrap.dataset.state = session.state;
      wrap.classList.toggle("is-active", session.id === activeId);
      wrap.classList.toggle("is-root", Boolean(info.root));
      const btn = el("button", "term-tab-main");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", String(session.id === activeId));
      btn.dataset.termTab = session.id;
      btn.title = [`${info.user || "?"}@${info.alias || "machine"}`, info.cwd, info.shell].filter(Boolean).join("  ");
      btn.append(el("span", "term-tab-dot"), el("span", "term-tab-name", label(session)));
      if (info.root) btn.append(el("span", "term-tab-root", "root"));
      const close = el("button", "term-tab-close", "×");
      close.type = "button";
      close.dataset.termTabClose = session.id;
      close.setAttribute("aria-label", `Close ${label(session)}`);
      close.title = "Close terminal";
      wrap.append(btn, close);
      return wrap;
    }));
    dock.classList.toggle("has-sessions", sessions.size > 0);
  }

  // ---- choosing a machine -------------------------------------------------

  async function choices() {
    const project = sidebar.sessionProject();
    const items = [];
    if (project) {
      items.push({ name: project.project_name || project.host_alias || "This project", detail: [project.host_alias, project.remote_path].filter(Boolean).join(":") || "the project folder", params: projectParams() });
    }
    const data = await net.api("/terminal/hosts");
    for (const host of data.hosts || []) {
      const shells = host.os === "windows" && Array.isArray(host.shells) && host.shells.length ? host.shells : [null];
      const ordered = [...shells].sort((a, b) => (b?.id === host.default_shell) - (a?.id === host.default_shell));
      for (const shell of ordered) {
        items.push({
          name: shell ? `${host.alias} · ${shell.label}` : String(host.alias || "machine"),
          detail: host.online ? (host.transport === "ssh" ? "SSH" : host.os === "windows" ? "Windows" : "connector") : "offline",
          disabled: !host.online,
          params: { host_id: host.id, ...(shell ? { shell: shell.id } : {}) },
        });
      }
    }
    return items;
  }

  function choiceList(items) {
    const list = el("div", "term-choices");
    list.setAttribute("role", "menu");
    if (!items.length) list.append(el("p", "term-choice-none", "No machines yet. Add one under Machines."));
    items.forEach((item) => {
      const btn = el("button", "term-choice");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.disabled = Boolean(item.disabled);
      btn.append(el("span", "term-choice-name", item.name), el("span", "term-choice-detail", item.detail));
      btn.addEventListener("click", () => start(item.params));
      list.append(btn);
    });
    return list;
  }

  async function openMenu() {
    if (!menu) return;
    menu.hidden = false;
    newBtn?.setAttribute("aria-expanded", "true");
    menu.replaceChildren(el("p", "term-choice-none", "Loading machines…"));
    try {
      const items = await choices();
      if (!menu.hidden) menu.replaceChildren(choiceList(items));
      menu.querySelector("button:not(:disabled)")?.focus();
    } catch (error) {
      menu.replaceChildren(el("p", "term-choice-none", error.message || "Could not list machines."));
    }
  }

  function closeMenu() {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    newBtn?.setAttribute("aria-expanded", "false");
  }

  function clearEmpty() {
    empty?.remove();
    empty = null;
  }

  // No session: the machine list in the body, or why there is none.
  async function showEmpty(error = "") {
    clearEmpty();
    empty = el("div", "term-empty");
    empty.dataset.termEmpty = "";
    body.append(empty);
    const mine = empty;
    if (error) {
      mine.append(el("p", "term-empty-error", error));
      return;
    }
    mine.append(el("p", "term-empty-title", "Open a terminal on"), el("p", "term-choice-none", "Loading machines…"));
    try {
      const items = await choices();
      if (empty === mine) mine.lastChild.replaceWith(choiceList(items));
    } catch (error2) {
      if (empty === mine) mine.lastChild.replaceWith(el("p", "term-empty-error", error2.message || "Could not list machines."));
    }
  }

  // ---- keys, clipboard, links ---------------------------------------------

  async function copySelection(session) {
    if (!session.term.hasSelection()) return;
    try { await navigator.clipboard.writeText(session.term.getSelection()); } catch (_) { picker.setHint("Copy was blocked: select and use the browser's Copy."); }
  }

  // xterm's own handling for everything but the terminal's shortcuts; false
  // keeps a key from the shell. The browser's paste follows Ctrl+Shift+V.
  function keyHandler(event, session) {
    const combo = event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey;
    if (!combo || !["KeyC", "KeyV", "KeyF"].includes(event.code)) return true;
    if (event.type === "keydown" && event.code !== "KeyV") {
      event.preventDefault();
      if (event.code === "KeyC") copySelection(session);
      else openFind();
    }
    return false;
  }

  function confirmPaste(text) {
    const lines = text.split(/\r\n|\r|\n/).length;
    return window.fuseConfirm(`Paste ${lines} lines into the terminal? Each line break runs what is before it.`, { confirmLabel: "Paste" });
  }

  async function pasteFromClipboard() {
    const session = active();
    if (!session) return;
    try {
      session.paste(await navigator.clipboard.readText());
    } catch (_) {
      picker.setHint("Paste was blocked: long-press the terminal and choose Paste.");
    }
  }

  async function openLink(uri) {
    const href = safeLink(uri);
    if (!href) return;
    if (await window.fuseConfirm(`Open this link?\n${href}`, { confirmLabel: "Open" })) window.open(href, "_blank", "noopener,noreferrer");
  }

  // ---- find ------------------------------------------------------------------

  const findOptions = () => {
    const accent = palette?.cursor || "#888888";
    return { incremental: false, decorations: { matchBackground: accent + "44", activeMatchBackground: accent + "aa", matchOverviewRuler: accent, activeMatchColorOverviewRuler: accent } };
  };

  function openFind() {
    if (!find || !findInput) return;
    find.hidden = false;
    findInput.focus();
    findInput.select();
  }

  function closeFind() {
    if (!find || find.hidden) return;
    find.hidden = true;
    active()?.search.clearDecorations();
    active()?.focus();
  }

  function findStep(back = false) {
    const session = active();
    const text = findInput.value;
    if (!session || !text) return;
    const hit = back ? session.search.findPrevious(text, findOptions()) : session.search.findNext(text, { ...findOptions(), incremental: true });
    find.classList.toggle("is-miss", !hit);
  }

  // ---- looks -----------------------------------------------------------------

  function codeFont() {
    return getComputedStyle(document.documentElement).getPropertyValue("--code").trim() || '"Red Hat Mono", ui-monospace, monospace';
  }

  // The theme's tokens on the dock: --term-bg, --term-fg, --term-accent,
  // --term-cursor, falling back to the chat palette.
  // A token may be color-mix(): a probe in the dock lets the browser resolve it.
  function readPalette() {
    const probe = el("span");
    probe.hidden = true;
    dock.append(probe);
    const token = (...names) => {
      for (const name of names) {
        probe.style.color = `var(${name}, transparent)`;
        const hex = cssColorHex(getComputedStyle(probe).color);
        if (hex) return hex;
      }
      return "";
    };
    const palette = terminalPalette({
      bg: token("--term-bg", "--ink"), fg: token("--term-fg", "--mist"), accent: token("--term-accent", "--brass"),
      cursor: token("--term-cursor"), red: token("--fault"), green: token("--ok"), mute: token("--mute"),
    }, document.documentElement.dataset.chatScheme === "light" ? "light" : "dark");
    probe.remove();
    return palette;
  }

  function restyle() {
    if (!xt) return;
    palette = readPalette();
    font = { ...font, family: codeFont() };
    for (const session of sessions.values()) {
      session.setPalette(palette);
      session.setFont(font);
    }
  }

  function zoom(step) {
    const size = Math.min(FONT.max, Math.max(FONT.min, font.size + step));
    if (size === font.size) return;
    font = { ...font, size };
    writeStore({ font: size });
    for (const session of sessions.values()) session.setFont(font);
  }

  // ---- the dock's height (Console, Desktop) ---------------------------------

  function setHeight(px, save) {
    const stage = dock.parentElement?.clientHeight || window.innerHeight;
    const h = Math.round(Math.min(Math.max(120, px), Math.max(160, stage - 180)));
    dock.style.setProperty("--terminal-h", h + "px");
    if (save) writeStore({ height: h });
  }

  if (resizer) {
    const saved = Number(readStore().height);
    if (saved > 0) setHeight(saved, false);
    resizer.addEventListener("pointerdown", (event) => {
      if (mobile.matches || event.button !== 0) return;
      event.preventDefault();
      resizer.setPointerCapture?.(event.pointerId);
      const bottom = dock.getBoundingClientRect().bottom;
      dock.classList.add("is-resizing");
      const move = (e) => setHeight(bottom - e.clientY, false);
      const up = () => {
        resizer.removeEventListener("pointermove", move);
        dock.classList.remove("is-resizing");
        setHeight(dock.getBoundingClientRect().height, true);
      };
      resizer.addEventListener("pointermove", move);
      resizer.addEventListener("pointerup", up, { once: true });
      resizer.addEventListener("pointercancel", up, { once: true });
    });
    resizer.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      setHeight(dock.getBoundingClientRect().height + (event.key === "ArrowUp" ? 24 : -24), true);
    });
  }

  // ---- wiring ------------------------------------------------------------------

  const keyBar = keysEl ? createKeyBar(keysEl, {
    session: active,
    paste: pasteFromClipboard,
    toggleKeyboard() {
      const session = active();
      if (!session) return;
      if (dock.contains(document.activeElement)) document.activeElement.blur();
      else session.focus();
    },
  }) : null;
  bindLineMode(lineForm, q("line-input"), { session: active, transform: (data) => keyBar?.transform(data) ?? data });

  function setLineMode(on) {
    if (!lineForm) return;
    lineForm.hidden = !on;
    dock.classList.toggle("is-line-mode", on);
    lineBtn?.setAttribute("aria-pressed", String(on));
    if (on) q("line-input")?.focus();
    else active()?.focus();
  }
  if (lineForm) lineForm.hidden = true;

  root.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest("[data-terminal-toggle]")) { toggle(); return; }
    if (!dock.contains(target)) return;
    if (target.closest("[data-terminal-hide]")) { setOpen(false); return; }
    if (target.closest("[data-terminal-new]")) { if (menu && !menu.hidden) closeMenu(); else openMenu(); return; }
    if (target.closest("[data-terminal-search]")) { if (find?.hidden) openFind(); else closeFind(); return; }
    if (target.closest("[data-terminal-line]")) { setLineMode(lineForm?.hidden ?? false); return; }
    const fontBtn = target.closest("[data-terminal-font]");
    if (fontBtn) { zoom(Number(fontBtn.dataset.terminalFont) || 0); return; }
    if (target.closest("[data-terminal-find-next]")) { findStep(false); return; }
    if (target.closest("[data-terminal-find-prev]")) { findStep(true); return; }
    if (target.closest("[data-terminal-find-close]")) { closeFind(); return; }
    const closeTab = target.closest("[data-term-tab-close]");
    if (closeTab) { const s = sessions.get(closeTab.dataset.termTabClose); if (s) closeSession(s); return; }
    const tab = target.closest("[data-term-tab]");
    if (tab) { activate(tab.dataset.termTab, true); return; }
    const action = target.closest("[data-term-action]");
    if (action) {
      const session = sessions.get(action.closest("[data-term-pane]")?.dataset.termPane);
      if (!session) return;
      const what = action.dataset.termAction;
      if (what === "restart") start(session.params, true, session);
      else if (what === "reconnect") session.reconnect();
      else if (what === "dismiss") remove(session);
    }
  });

  document.addEventListener("pointerdown", (event) => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !newBtn?.contains(event.target)) closeMenu();
  });

  findInput?.addEventListener("input", () => findStep(false));
  findInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); findStep(event.shiftKey); }
    else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeFind(); }
  });
  menu?.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closeMenu();
    newBtn?.focus();
  });

  // The desktop app passes Ctrl+R and friends through while a terminal has the focus.
  const focusNote = (focused) => window.dispatchEvent(new CustomEvent("fuse:terminal-focus", { detail: { focused } }));
  dock.addEventListener("focusin", () => focusNote(true));
  dock.addEventListener("focusout", (event) => { if (!dock.contains(event.relatedTarget)) focusNote(false); });

  document.addEventListener("fuse-appearance", restyle);
  document.addEventListener("fuse-terminal-set", (event) => {
    if (event.detail?.open) open({ focus: false }).catch(() => {});
    else setOpen(false);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", restyle);

  return {
    toggle,
    open: (opts) => open(opts).catch((error) => picker.setHint(error.message || "Could not open the terminal.")),
    close: () => setOpen(false),
    // What a reload brings back (boot.js): the dock and the tab on top.
    snapshot: () => (opened ? { open: true, active: activeId } : null),
    resume(saved) {
      if (saved?.open) open({ focus: false, fresh: false, want: String(saved.active || "") }).catch(() => {});
    },
  };
}

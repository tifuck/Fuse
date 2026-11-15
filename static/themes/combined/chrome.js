// Combined's chrome: the header status pill, the rail, the phone ⋯ menu and
// Escape for the theme's own popups. The pill says what the run is doing, for
// how long and how many agents it has; it toggles the rail. The rail opens
// only when the user asks (the pill, Todos): wide, it docks and the choice is
// kept in localStorage; narrow, it is a dropdown under the header that Esc or
// a click elsewhere closes. Runtime events only light the pill's dot.
const WIDE = "(min-width: 1040px)";
const KEY = "fuse.chat.combined.rail.v1";

export function create(deps) {
  let railOpen = null; // null until the first sync reads the saved choice
  let bound = false;
  let seen = null; // the rail contents the user has seen, per thread
  let seenThread = null;
  let news = false;
  let menuUsed = false;
  let opener = "[data-theme-pill]"; // what Escape refocuses when it shuts the dropdown
  const q = (sel) => deps.root.querySelector(sel);
  const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };
  const wide = () => window.matchMedia(WIDE).matches;
  const shown = (el) => Boolean(el && el.getClientRects().length);
  const saved = () => { try { return localStorage.getItem(KEY) === "open"; } catch (_) { return false; } };

  function busy() {
    const run = Boolean(deps.streaming || deps.runIsActive(deps.activeRun));
    const agents = deps.agentsRows.some((row) => deps.agentLive(row.status));
    return { run, agents, jobs: deps.jobsRows.length > 0 };
  }

  function setRail(open, focus) {
    railOpen = open;
    if (wide()) { try { localStorage.setItem(KEY, open ? "open" : "closed"); } catch (_) { /* private mode */ } }
    syncChrome();
    if (open && !wide() && focus) q("[data-theme-rail]")?.querySelector("button, [href], [tabindex='0']")?.focus({ preventScroll: true });
  }

  // The phone ⋯ menu holds the header's sheet toggles; wide, its box is display: contents.
  function menuItems() {
    return [...(q("[data-theme-menu]")?.querySelectorAll("button") || [])].filter(shown);
  }

  function setMenu(open) {
    const menu = q("[data-theme-menu]");
    const more = q("[data-theme-more]");
    if (!menu || !more) return;
    menu.classList.toggle("is-open", open);
    more.setAttribute("aria-expanded", String(open));
    if (open) menu.setAttribute("role", "menu"); else menu.removeAttribute("role");
    menu.querySelectorAll("button").forEach((btn) => {
      if (open) btn.setAttribute("role", "menuitem"); else btn.removeAttribute("role");
    });
    if (open) {
      if (railOpen && !wide()) setRail(false);
      menuItems()[0]?.focus({ preventScroll: true });
    }
  }

  // The topmost theme layer Escape closes, as a function; null leaves the key to the runtime.
  function escapeLayer(event) {
    if (document.querySelector("dialog[open]")) return null;
    const pop = document.querySelector("body > .chat-pop");
    if (pop) {
      // The row menu closes itself (and refocuses its ⋯) when the key starts inside it.
      if (pop.contains(event.target)) return null;
      return () => pop.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    }
    const account = document.querySelector("[data-account-menu]:not([hidden])");
    if (account) {
      const root = account.closest("[data-account]");
      return () => {
        root?.querySelector("[data-account-close]")?.click();
        root?.querySelector("[data-account-toggle]")?.focus({ preventScroll: true });
      };
    }
    if (q("[data-theme-menu].is-open")) return () => { setMenu(false); q("[data-theme-more]")?.focus({ preventScroll: true }); };
    const context = document.getElementById("chat-context-pop");
    if (context && !context.hidden) {
      return () => {
        const ring = document.querySelector("[data-chat-context]");
        ring?.click();
        ring?.focus({ preventScroll: true });
      };
    }
    if (railOpen && !wide()) return () => { setRail(false); (q(`${opener}:not([hidden])`) || q("[data-theme-pill]"))?.focus({ preventScroll: true }); };
    return null;
  }

  function bind() {
    if (bound) return;
    bound = true;
    q("[data-theme-pill]")?.addEventListener("click", (event) => {
      opener = "[data-theme-pill]";
      setRail(!railOpen, event.detail === 0);
    });
    // Todos opens the rail the list lives in (the runtime only folds the list).
    q("[data-todo-header]")?.addEventListener("click", () => {
      if (!railOpen) {
        opener = "[data-todo-header]";
        setRail(true);
      }
      requestAnimationFrame(() => q("[data-live-todos]")?.scrollIntoView({ block: "nearest" }));
    });
    q("[data-theme-agent-back]")?.addEventListener("click", () => q("[data-agent-main]")?.click());
    q("[data-theme-more]")?.addEventListener("click", () => setMenu(!q("[data-theme-menu]")?.classList.contains("is-open")));
    // An item shuts the menu once its own handler (which may stop the click) has run.
    q("[data-theme-menu]")?.addEventListener("click", (event) => {
      if (!event.target.closest("button") || !q("[data-theme-menu]").classList.contains("is-open")) return;
      menuUsed = true;
      setTimeout(() => setMenu(false));
    }, true);
    q("[data-theme-menu]")?.addEventListener("keydown", (event) => {
      if (!q("[data-theme-menu]").classList.contains("is-open")) return;
      const list = menuItems();
      const i = list.indexOf(document.activeElement);
      const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: list.length - 1 }[event.key];
      if (to !== undefined) {
        event.preventDefault();
        list[(to + list.length) % list.length]?.focus();
      } else if (event.key === "Tab") {
        setMenu(false);
      }
    });
    // A sheet opened from ⋯ hands focus back to ⋯ when it closes (its own toggle is hidden in the menu).
    document.addEventListener("focusout", (event) => {
      if (!menuUsed || event.relatedTarget) return;
      const from = event.target;
      setTimeout(() => {
        if (document.activeElement !== document.body || shown(from)) return;
        menuUsed = false;
        const more = q("[data-theme-more]");
        if (shown(more)) more.focus({ preventScroll: true });
      });
    });
    // Narrow: a click elsewhere closes the rail dropdown and the ⋯ menu.
    document.addEventListener("click", (event) => {
      const t = event.target;
      if (!t.closest?.("[data-theme-menu], [data-theme-more]") && q("[data-theme-menu].is-open")) setMenu(false);
      if (wide() || !railOpen || t.closest?.("[data-theme-rail], [data-theme-pill], [data-todo-header]")) return;
      railOpen = false;
      syncChrome();
    });
    // Collapsing the sidebar hides the focused button: focus moves to the header's toggle.
    document.addEventListener("click", (event) => {
      if (!event.target.closest?.("[data-history] .chat-history-collapse")) return;
      requestAnimationFrame(() => {
        const toggle = q("[data-chat-bar] [data-history-toggle]");
        const at = document.activeElement;
        if (shown(toggle) && (!at || at === document.body || !shown(at))) toggle.focus({ preventScroll: true });
      });
    });
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.isComposing) return;
      const close = escapeLayer(event);
      if (!close) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
    }, true);
    // A layer the runtime closes on Escape (the phone model sheet, a sheet opened
    // from ⋯) can drop the focus to <body>: give it back to what opened it.
    let lastOpener = null;
    document.addEventListener("pointerdown", (event) => { lastOpener = event.target.closest?.("button, [role=menuitem]") || lastOpener; }, true);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") lastOpener = document.activeElement;
      if (event.key !== "Escape") return;
      setTimeout(() => {
        if (document.activeElement && document.activeElement !== document.body) return;
        const back = shown(lastOpener) && lastOpener.isConnected ? lastOpener : lastOpener?.closest?.("[data-theme-menu]") && q("[data-theme-more]");
        back?.focus?.({ preventScroll: true });
      });
    }, true);
    window.matchMedia(WIDE).addEventListener?.("change", () => {
      railOpen = wide() && saved();
      setMenu(false);
      syncChrome();
    });
    // Rows, lists, the todo tray and the title change outside the run clock.
    const watch = new MutationObserver(() => syncChrome());
    const rail = q("[data-theme-rail]");
    if (rail) watch.observe(rail, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    const title = q("[data-chat-title]");
    if (title) watch.observe(title, { subtree: true, childList: true, characterData: true });
    watch.observe(deps.root, { attributes: true, attributeFilter: ["class"] });
  }

  function clock(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "";
    return deps.formatActivityClock(Math.max(0, Math.round(seconds)));
  }

  function syncPill(state) {
    const pill = q("[data-theme-pill]");
    if (!pill) return;
    const text = pill.querySelector("[data-theme-pill-text]");
    const meta = pill.querySelector("[data-theme-pill-meta]");
    const total = deps.agentsRows.length;
    const live = deps.agentsRows.filter((row) => deps.agentLive(row.status)).length;
    let label = deps.messages.length ? "Done" : "Ready";
    let tone = "idle";
    let elapsed = "";
    if (state.run) {
      label = "Working";
      tone = "run";
      const msg = [...deps.messages].reverse().find((row) => row.role === "assistant");
      elapsed = msg ? clock(deps.liveElapsedSeconds(msg, true)) : "";
    } else if (state.agents) {
      label = live === 1 ? "1 agent working" : `${live} agents working`;
      tone = "run";
    } else if (state.jobs) {
      label = "Running in background";
      tone = "run";
    }
    const parts = [elapsed, total ? `${total} agent${total === 1 ? "" : "s"}` : "", state.jobs ? `${deps.jobsRows.length} cmd` : ""].filter(Boolean);
    setText(text, label);
    setText(meta, parts.join(" · "));
    if (pill.dataset.tone !== tone) pill.dataset.tone = tone;
    const name = `Activity: ${[label, ...parts].join(", ")}${news ? ", new" : ""}`;
    if (pill.getAttribute("aria-label") !== name) pill.setAttribute("aria-label", name);
    pill.setAttribute("aria-expanded", railOpen ? "true" : "false");
    pill.classList.toggle("has-news", news);
  }

  // The dot on the pill: the rail's agents, commands or todos changed during
  // work while it was shut. Opening the rail or another thread clears it.
  function syncNews(state) {
    const sig = [deps.agentsRows.length, deps.agentsRows.filter((row) => deps.agentLive(row.status)).length,
      deps.jobsRows.length, q("[data-live-todos]")?.hidden ? "" : q("[data-todo-count]")?.textContent || ""].join("|");
    const thread = new URLSearchParams(location.search).get("t") || "";
    if (railOpen || thread !== seenThread) news = false;
    else if (sig !== seen && (state.run || state.agents || state.jobs)) news = true;
    seen = sig;
    seenThread = thread;
  }

  function syncContext() {
    const box = q("[data-theme-context]");
    if (!box) return;
    const meter = deps.liveMeter;
    const used = Number(meter && meter.used);
    const limit = Number(meter && meter.limit);
    if (!(limit > 0) || !Number.isFinite(used)) { if (!box.hidden) box.hidden = true; return; }
    const pct = Math.min(100, Math.round(used / limit * 100));
    const k = (n) => n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n);
    // Writes only on change: the rail's MutationObserver calls back here.
    if (box.hidden) box.hidden = false;
    setText(box.querySelector("[data-theme-context-pct]"), `${pct}%`);
    const bar = box.querySelector("[data-theme-context-bar]");
    if (bar.style.width !== `${pct}%`) bar.style.width = `${pct}%`;
    setText(box.querySelector("[data-theme-context-cap]"), `${k(used)} / ${k(limit)} tokens`);
  }

  function syncTitle() {
    const title = q("[data-chat-title]");
    const head = title?.closest(".cb-title");
    const text = title?.textContent.trim() || "";
    if (head && head.title !== text) head.title = text;
  }

  // The runtime sizes the todo list to five rows (from the in-progress one)
  // when it paints; painted inside a closed rail, that measured nothing.
  function fitTodos() {
    const list = q("[data-live-todos] [data-todo-list]");
    const rows = [...(list?.children || [])];
    if (!rows.length) return;
    const current = rows.findIndex((row) => row.classList.contains("is-in-progress"));
    const at = current >= 0 ? current : Math.max(0, rows.findIndex((row) => row.classList.contains("is-pending")));
    const start = Math.max(0, Math.min(at, rows.length - 5));
    list.style.maxHeight = `${rows.slice(start, start + 5).reduce((sum, row) => sum + row.offsetHeight, 0)}px`;
    list.scrollTop = rows[at].offsetTop;
  }

  function syncChrome() {
    if (!deps.root) return;
    bind();
    if (railOpen === null) railOpen = wide() && saved();
    const state = busy();
    const next = railOpen ? "open" : "closed";
    if (deps.root.dataset.themeRailState !== next) {
      deps.root.dataset.themeRailState = next;
      if (railOpen) fitTodos();
    }
    syncNews(state);
    syncPill(state);
    syncContext();
    syncTitle();
  }

  return { syncChrome };
}

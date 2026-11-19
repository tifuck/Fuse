// Combined's chrome: the status bar, the rail, the phone ⋯ menu and Escape
// for the theme's own popups. The status bar runs along the bottom edge under
// the composer, like an editor's: what the run is doing and for how long, its
// live agents, background commands and plan (each toggles the rail), then
// context and the machine. The rail opens only when the user asks (the bar,
// Todos): wide, it docks and the choice is kept in localStorage; narrow, it is
// a popover above the bar that Esc or a click elsewhere closes.
const WIDE = "(min-width: 1040px)";
const KEY = "fuse.chat.combined.rail.v1";
// Text that ticks on its own: row clocks, ages and spinner glyphs.
const TICKS = ".chat-agents-time, [data-age-stamp], .chat-console-spin";

export function create(deps) {
  let railOpen = null; // null until the first sync reads the saved choice
  let bound = false;
  let menuUsed = false;
  let opener = null; // what Escape refocuses when it shuts the popover
  let chromeFrame = 0;
  let watch = null;
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
    if (railOpen && !wide()) return () => { setRail(false); (shown(opener) ? opener : q("[data-theme-status-run]"))?.focus({ preventScroll: true }); };
    return null;
  }

  function statusBar() {
    const seg = (name, body, extra = " hidden") => `<button type="button" class="cb-sb" data-theme-status-${name} aria-controls="cb-rail" aria-expanded="false"${extra}>${body}</button>`;
    const bar = document.createElement("div");
    bar.className = "cb-status";
    bar.dataset.themeStatus = "";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Status");
    bar.innerHTML = seg("run", '<i class="cb-sb-dot" aria-hidden="true"></i><b data-theme-status-label></b> <span data-theme-status-clock></span>',
      ' title="Agents, background commands, todos and context"')
      + '<i class="cb-sb-sep" data-theme-status-sep aria-hidden="true" hidden></i>'
      + seg("agents", `${deps.STEP_ICONS.agent}<span><span data-theme-status-n></span> <span class="cb-sb-word"></span></span>`)
      + seg("jobs", `${deps.STEP_ICONS.shell}<span><span data-theme-status-n></span> <span class="cb-sb-word"></span></span>`)
      + seg("plan", 'Plan <span data-theme-status-n></span><span class="cb-sb-meter" aria-hidden="true"><i></i></span>')
      + '<span class="cb-sb-gap"></span>'
      + '<span class="cb-sb" data-theme-status-context hidden>Context <span data-theme-status-n></span></span>'
      + '<i class="cb-sb-sep" aria-hidden="true"></i>';
    // The machine stats card moves in whole: hoststats.js keeps painting it.
    const stats = q("[data-host-stats]");
    if (stats) bar.append(stats);
    bar.addEventListener("click", (event) => {
      const btn = event.target.closest("button.cb-sb");
      if (!btn) return;
      opener = btn;
      setRail(!railOpen, event.detail === 0);
    });
    return bar;
  }

  function bind() {
    if (bound) return;
    bound = true;
    q("[data-chat-form]")?.after(statusBar());
    // Todos opens the rail the list lives in (the runtime only folds the list).
    q("[data-todo-header]")?.addEventListener("click", (event) => {
      if (!railOpen) {
        opener = event.currentTarget;
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
      if (wide() || !railOpen || t.closest?.("[data-theme-rail], [data-theme-status] button, [data-todo-header]")) return;
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
      // The runtime may close its sheet a beat later: watch briefly for the focus landing on <body>.
      const until = Date.now() + 600;
      const check = () => {
        const at = document.activeElement;
        if (at && at !== document.body) { if (Date.now() < until) setTimeout(check, 50); return; }
        const back = shown(lastOpener) && lastOpener.isConnected ? lastOpener : lastOpener?.closest?.("[data-theme-menu]") && q("[data-theme-more]");
        back?.focus?.({ preventScroll: true });
      };
      setTimeout(check);
    }, true);
    window.matchMedia(WIDE).addEventListener?.("change", () => {
      railOpen = wide() && saved();
      setMenu(false);
      syncChrome();
    });
    // The runtime redraws the chat list in place; keep the keyboard on the same row and control.
    const rowKey = (item) => ["thread", "draft", "project"].map((k) => item.dataset[k] || "").join("|");
    const rowControls = (item) => [...item.querySelectorAll("button, a, [tabindex]")];
    let rowFocus = null;
    document.addEventListener("focusin", (event) => {
      const item = event.target.closest?.("[data-history-item]");
      rowFocus = item ? { key: rowKey(item), at: rowControls(item).indexOf(event.target) } : null;
    });
    document.addEventListener("pointerdown", (event) => { if (!event.target.closest?.("[data-history-item]")) rowFocus = null; }, true);
    const historyList = q("[data-history-list]");
    if (historyList) new MutationObserver((records) => {
      if (records.every(({ target }) => target.closest?.(TICKS))) return;
      if (!rowFocus || (document.activeElement && document.activeElement !== document.body)) return;
      const item = [...historyList.querySelectorAll("[data-history-item]")].find((el) => rowKey(el) === rowFocus.key);
      if (item) rowControls(item)[rowFocus.at]?.focus({ preventScroll: true });
    }).observe(historyList, { childList: true, subtree: true });
    // Rows, lists, the todo tray and the title change outside the run clock; a
    // clock or spinner ticking in a row changes nothing drawn here.
    watch = new MutationObserver((records) => {
      if (records.some(({ target }) => !target.closest?.(TICKS))) scheduleChrome();
    });
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

  // A count segment: the number, then its noun (which phones drop).
  function syncCount(seg, n, noun) {
    if (!seg) return;
    if (seg.hidden !== !n) seg.hidden = !n;
    setText(seg.querySelector("[data-theme-status-n]"), String(n));
    setText(seg.querySelector(".cb-sb-word"), n === 1 ? noun : `${noun}s`);
  }

  function syncStatus(state) {
    const bar = q("[data-theme-status]");
    if (!bar) return;
    const seg = (name) => bar.querySelector(`[data-theme-status-${name}]`);
    const live = deps.agentsRows.filter((row) => deps.agentLive(row.status)).length;
    const jobs = deps.jobsRows.length;
    const todos = q("[data-live-todos]");
    const plan = todos && !todos.hidden ? todos.querySelector("[data-todo-count]")?.textContent || "" : "";
    let label = deps.messages.length ? "Done" : "Ready";
    let elapsed = "";
    if (state.run) {
      label = "Working";
      const msg = [...deps.messages].reverse().find((row) => row.role === "assistant");
      elapsed = msg ? clock(deps.liveElapsedSeconds(msg, true)) : "";
    } else if (state.agents || state.jobs) {
      label = "Running in background";
    }
    const tone = state.run || state.agents || state.jobs ? "run" : "idle";
    if (bar.dataset.tone !== tone) bar.dataset.tone = tone;
    setText(seg("label"), label);
    setText(seg("clock"), elapsed);
    syncCount(seg("agents"), live, "agent");
    syncCount(seg("jobs"), jobs, "command");
    const planSeg = seg("plan");
    if (planSeg.hidden !== !plan) planSeg.hidden = !plan;
    setText(planSeg.querySelector("[data-theme-status-n]"), plan);
    const [done, total] = plan.split("/").map(Number);
    const width = `${total > 0 ? Math.round(done / total * 100) : 0}%`;
    const fill = planSeg.querySelector(".cb-sb-meter i");
    if (fill.style.width !== width) fill.style.width = width;
    const sep = seg("sep");
    if (sep.hidden !== !(live || jobs || plan)) sep.hidden = !(live || jobs || plan);
    const expanded = railOpen ? "true" : "false";
    bar.querySelectorAll("button.cb-sb").forEach((btn) => {
      if (btn.getAttribute("aria-expanded") !== expanded) btn.setAttribute("aria-expanded", expanded);
    });
  }

  function syncContext() {
    const box = q("[data-theme-context]");
    const seg = q("[data-theme-status-context]");
    const meter = deps.liveMeter;
    const used = Number(meter && meter.used);
    const limit = Number(meter && meter.limit);
    if (!(limit > 0) || !Number.isFinite(used)) {
      [box, seg].forEach((el) => { if (el && !el.hidden) el.hidden = true; });
      return;
    }
    const pct = Math.min(100, Math.round(used / limit * 100));
    if (seg) {
      if (seg.hidden) seg.hidden = false;
      setText(seg.querySelector("[data-theme-status-n]"), `${pct}%`);
    }
    if (!box) return;
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

  // Runtime ticks and DOM changes ask for a sync; at most one runs a frame.
  function scheduleChrome() {
    if (!chromeFrame) chromeFrame = requestAnimationFrame(() => { chromeFrame = 0; syncChrome(); });
  }

  // A clock tick changes only the run's own time; idle, it has nothing to redraw.
  function tickChrome() {
    if (busy().run) scheduleChrome();
  }

  function syncChrome() {
    if (!deps.root) return;
    if (chromeFrame) { cancelAnimationFrame(chromeFrame); chromeFrame = 0; }
    bind();
    if (railOpen === null) railOpen = wide() && saved();
    const state = busy();
    const next = railOpen ? "open" : "closed";
    if (deps.root.dataset.themeRailState !== next) {
      deps.root.dataset.themeRailState = next;
      if (railOpen) fitTodos();
    }
    syncStatus(state);
    syncContext();
    syncTitle();
    // What this sync wrote is drawn already: it must not ask for another.
    watch?.takeRecords();
  }

  return { scheduleChrome, syncChrome, tickChrome };
}

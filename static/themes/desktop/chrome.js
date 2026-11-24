// Desktop's chrome: the top bar (the breadcrumb's title menu, "← Main" in a
// subagent, the ⋯ menu, the Activity button), the sidebar's Backdrop and
// light/dark switches, the composer's chips row, the artifact workspace's own
// controls, and Activity: the agents table, background commands, the plan and
// a command's output, drawn here from the runtime's live state. Activity
// opens only when the user asks (the choice is kept in localStorage); the
// button says what is running instead. Above a phone it docks beside the
// chat and pushes it aside, beside an artifact it slides over it, and on
// phones it is a bottom sheet that drags
// and whose pages swipe sideways. Hovering the button docks it for a look
// (dk-insp-peek) until the pointer leaves; a click on it or in it pins it.
const DOCK = "(min-width: 1100px)";
const WIDE = "(min-width: 801px)";
// Hover peek: the pointer rests on the Activity button this long before the
// panel slides in, and it stays this long after the pointer leaves.
const PEEK_OPEN_MS = 60;
const PEEK_CLOSE_MS = 250;
const PHONE = "(max-width: 640px)";
const STORE = "fuse.chat.desktop.activity.v1";
const PAGES = ["activity", "plan", "output"];
const SORTS = new Set(["td", "ta", "cd", "ca"]);
const LIVE_JOB = new Set(["running", "queued", "cancelling"]);
const TERM = '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M3 4.5l3.2 3.2L3 10.9M8 11.5h5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const EYE = '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8s-2.3 4.2-6.2 4.2S1.8 8 1.8 8z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
// Toggles whose sheet may close while they sit in a closed menu or a folded bar.
const SHEET_TOGGLES = "[data-skills-toggle], [data-view-toggle], [data-memory-toggle], [data-artifact-toggle]";
// Text that ticks on its own: row clocks, ages and spinner glyphs.
const TICKS = ".chat-agents-time, [data-age-stamp], .chat-console-spin";

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "{}");
    return {
      open: saved.open === true,
      fold: saved.fold && typeof saved.fold === "object" ? saved.fold : {},
      sort: SORTS.has(saved.sort) ? saved.sort : "",
    };
  } catch (_) {
    return { open: false, fold: {}, sort: "" };
  }
}

export function create(deps) {
  const store = load();
  let bound = false;
  let lastState = "";
  let pickedJob = "";
  let page = "";
  let titleOpen = false;
  let pickerReturn = null;
  let opener = null;
  let threadKey = null;
  let baseSig = null;
  let wasArtifact = false;
  let openBeforeArtifact = null;
  let justDragged = false;
  let peek = false;
  let peekOpenTimer = 0;
  let peekCloseTimer = 0;
  const inertSaved = new Map();
  const seen = { output: "" };
  let chromeFrame = 0;
  let watch = null;
  const q = (sel) => deps.root.querySelector(sel);
  const setText = (el, text) => { if (el) FuseChatDOM.setText(el, text); };
  const setAttr = (el, name, value) => { if (el && el.getAttribute(name) !== value) el.setAttribute(name, value); };
  const show = (el, on) => { if (el && el.hidden === on) el.hidden = !on; };
  const esc = (value) => deps.escapeHtml(String(value ?? ""));
  const visible = (el) => Boolean(el && el.getClientRects().length);
  const matches = (query) => window.matchMedia(query).matches;
  const isOpen = () => deps.root.dataset.themeRailState === "open";
  const reduced = () => matches("(prefers-reduced-motion: reduce)");

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(store)); } catch (_) {}
  }

  // dock: a column beside the chat that pushes it aside; over: a panel over
  // it (beside an artifact, or a narrow window); sheet: a phone's bottom sheet.
  function mode() {
    if (matches(PHONE)) return "sheet";
    return matches(WIDE) && !deps.root.classList.contains("is-artifact") ? "dock" : "over";
  }

  // Hovering the Activity button docks it for a look without pinning it;
  // a click on the button or anywhere in the panel pins it.
  function setPeek(on) {
    clearTimeout(peekOpenTimer);
    clearTimeout(peekCloseTimer);
    peekOpenTimer = 0;
    const next = Boolean(on) && !store.open && mode() === "dock";
    if (next === peek) return;
    peek = next;
    syncChrome();
  }

  // The pointer back on the button or the panel, a popup, or a field being
  // typed in keeps it open. The push moves the button, so ask :hover, not
  // the enter and leave events.
  function schedulePeekClose() {
    clearTimeout(peekCloseTimer);
    peekCloseTimer = setTimeout(() => {
      if (!peek) return;
      const active = document.activeElement;
      const held = q("[data-theme-inspector]:hover, [data-theme-rail]:hover") || document.querySelector("body > .chat-pop, dialog[open]") ||
        (q("[data-theme-rail]")?.contains(active) && active.matches("input, select, textarea"));
      if (held) schedulePeekClose();
      else setPeek(false);
    }, PEEK_CLOSE_MS);
  }

  function setOpen(open, from) {
    if (from) opener = from;
    peek = false;
    store.open = Boolean(open);
    openBeforeArtifact = null;
    if (!open) {
      q("[data-theme-rail]")?.classList.remove("is-full");
      page = "";
    }
    save();
    syncChrome();
  }

  // Opens Activity on a page: "output" (a command's output) or the overview.
  function openPage(name, from) {
    page = name === "output" ? "output" : "";
    if (!isOpen()) setOpen(true, from);
    else syncChrome();
    if (mode() === "sheet") goTab(PAGES.indexOf(name) >= 0 ? name : "activity", isOpen());
  }

  function act(name) {
    window.FuseChatActions?.run?.(name);
  }

  // —— ⋯ menu ——

  const menuItems = () => [...(q("[data-theme-menu]")?.querySelectorAll(".dk-mi") || [])].filter(visible);

  function toggleMenu(open, refocus) {
    const menu = q("[data-theme-menu]");
    const btn = q("[data-theme-more]");
    if (!menu || !btn) return;
    const next = open ?? menu.hidden;
    if (next) syncMenuChecks();
    menu.hidden = !next;
    btn.setAttribute("aria-expanded", String(next));
    if (next) menuItems()[0]?.focus();
    else if (refocus) btn.focus();
  }

  function syncMenuChecks() {
    deps.root.querySelectorAll("[data-theme-menu] [role=menuitemcheckbox]").forEach((item) => {
      const on = item.matches("[data-theme-scheme]") ? window.FuseChatAppearance?.scheme === "light" : item.getAttribute("aria-pressed") === "true";
      item.setAttribute("aria-checked", String(on));
    });
  }

  function menuKey(event) {
    const items = menuItems();
    const i = items.indexOf(document.activeElement);
    const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[event.key];
    if (to !== undefined) {
      event.preventDefault();
      items[(to + items.length) % items.length]?.focus();
    } else if (event.key === "Tab") {
      event.preventDefault();
      toggleMenu(false, true);
    }
  }

  // —— Title menu: the open chat's row menu (rename, pin, fork, archive) ——

  function titleMenu(btn) {
    const row = q('[data-history-list] [data-history-item] [data-row-open][aria-current="true"]')?.closest("[data-history-item]");
    if (!row) return false;
    const box = btn.getBoundingClientRect();
    titleOpen = true;
    btn.setAttribute("aria-expanded", "true");
    row.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left, clientY: box.bottom + 4 }));
    if (!document.querySelector("body > .chat-pop")) titleClosed();
    return true;
  }

  function titleClosed() {
    if (!titleOpen) return;
    titleOpen = false;
    const btn = q("[data-theme-title-menu]");
    btn?.setAttribute("aria-expanded", "false");
    const active = document.activeElement;
    if (!active || active === document.body || active.matches("[data-row-more]")) btn?.focus({ preventScroll: true });
  }

  // —— Activity: focus and modality ——

  // Over the chat (a panel or a phone's sheet) it is a modal dialog.
  function setLayer(rail, dialog, modal) {
    if (dialog) {
      rail.setAttribute("role", "dialog");
      rail.setAttribute("aria-labelledby", "dk-inspector-title");
    } else ["role", "aria-labelledby"].forEach((name) => rail.removeAttribute(name));
    if (modal === rail.hasAttribute("aria-modal")) return;
    if (modal) {
      rail.setAttribute("aria-modal", "true");
      [...deps.root.children, document.querySelector("[data-fuse-dial]")].forEach((el) => {
        if (!el || el === rail || inertSaved.has(el)) return;
        inertSaved.set(el, el.inert);
        el.inert = true;
      });
    } else {
      rail.removeAttribute("aria-modal");
      inertSaved.forEach((was, el) => { el.inert = was; });
      inertSaved.clear();
    }
  }

  function syncLayer(state, how) {
    const rail = q("[data-theme-rail]");
    if (!rail) return;
    const open = state === "open";
    setLayer(rail, open && how !== "dock", open && how !== "dock");
    if (state === lastState) return;
    const was = lastState;
    lastState = state;
    if (!was) return;
    if (open && how !== "dock") q("[data-theme-inspector-close]")?.focus({ preventScroll: true });
    else if (!open) {
      const active = document.activeElement;
      if (!active || active === document.body || rail.contains(active)) {
        const back = [opener, q("[data-theme-inspector]"), q("[data-theme-more]")].find(visible);
        back?.focus({ preventScroll: true });
      }
    }
  }

  // —— Phone: tabs over pages that swipe (native scroll snapping) ——

  function goTab(name, smooth) {
    const pages = q("[data-theme-pages]");
    const i = PAGES.indexOf(name);
    if (!pages || i < 0) return;
    pages.scrollTo({ left: i * pages.clientWidth, behavior: smooth && !reduced() ? "smooth" : "auto" });
    syncTabs(i);
  }

  // A phone's sheet opens on the page with the most to show: running work,
  // else the plan, else Activity.
  function firstTab() {
    const { agentsLive, jobsLive, plan } = syncGroups();
    return agentsLive + jobsLive > 0 || !plan ? "activity" : "plan";
  }

  function syncTabs(index) {
    const pages = q("[data-theme-pages]");
    if (!pages) return;
    const width = pages.clientWidth || 1;
    const at = index ?? Math.round(pages.scrollLeft / width);
    pages.parentElement?.style.setProperty("--dk-ink", String(Math.max(0, Math.min(PAGES.length - 1, pages.scrollLeft / width))));
    deps.root.querySelectorAll("[data-theme-tab]").forEach((tab) => {
      const on = PAGES.indexOf(tab.dataset.themeTab) === at;
      setAttr(tab, "aria-selected", String(on));
      setAttr(tab, "tabindex", on ? "0" : "-1");
    });
  }

  function tabKey(event) {
    const tabs = [...deps.root.querySelectorAll("[data-theme-tab]")];
    const i = tabs.indexOf(event.target);
    if (i < 0) return;
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length];
    next.focus();
    goTab(next.dataset.themeTab, true);
  }

  // —— Dragging: the phone sheet by its grip, bar and tabs (down to close,
  //    up to fill the screen); a phone's artifact by its head (down to close) ——

  function bindDrag(el, handles, finish) {
    let drag = null;
    el.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !event.isPrimary || !finish.when()) return;
      const handle = event.target.closest?.(handles);
      if (!handle || !el.contains(handle)) return;
      drag = { id: event.pointerId, y: event.clientY, x: event.clientX, t: event.timeStamp, dy: 0, v: 0, moved: false, h: el.offsetHeight };
    });
    el.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dy = event.clientY - drag.y;
      if (!drag.moved) {
        // A sideways move is a swipe between pages, not a drag.
        if (Math.abs(dy) < 8 || Math.abs(event.clientX - drag.x) > Math.abs(dy)) {
          if (Math.abs(event.clientX - drag.x) > 8) drag = null;
          return;
        }
        drag.moved = true;
        try { el.setPointerCapture(event.pointerId); } catch (_) {}
        el.classList.add("is-dragging");
      }
      const dt = Math.max(1, event.timeStamp - drag.t);
      drag.v = (dy - drag.dy) / dt;
      drag.dy = dy;
      drag.t = event.timeStamp;
      el.style.setProperty("--dk-drag", `${Math.round(finish.follow(dy))}px`);
    });
    const end = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const done = drag;
      drag = null;
      if (!done.moved) return;
      el.classList.remove("is-dragging");
      el.style.removeProperty("--dk-drag");
      finish.end(done.dy, done.v, done.h);
      // The release's own click (on a tab or a button under the finger) is not a tap.
      justDragged = true;
      setTimeout(() => { justDragged = false; });
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  function bindSheet() {
    const rail = q("[data-theme-rail]");
    if (!rail) return;
    bindDrag(rail, "[data-theme-grab], [data-theme-drag], [data-theme-tabs]", {
      when: () => mode() === "sheet" && isOpen(),
      // Down follows the finger; up gives a little, as if pulled against a spring.
      follow: (dy) => (dy > 0 ? dy : -Math.min(48, Math.sqrt(-dy) * 4)),
      end: (dy, v, h) => {
        const full = rail.classList.contains("is-full");
        if (dy < -40 || v < -0.5) rail.classList.add("is-full");
        else if (full && (dy > h * 0.5 || (dy > 60 && v > 0.9))) setOpen(false);
        else if (full && (dy > 60 || v > 0.5)) rail.classList.remove("is-full");
        else if (!full && (dy > h * 0.25 || v > 0.5)) setOpen(false);
      },
    });
    const art = q("[data-chat-artifact]");
    if (art) bindDrag(art, "[data-theme-art-head]", {
      when: () => matches(PHONE),
      follow: (dy) => Math.max(0, dy),
      end: (dy, v) => { if (dy > 120 || v > 0.6) q("[data-artifact-close]")?.click(); },
    });
    const pages = q("[data-theme-pages]");
    if (pages) {
      let frame = 0;
      pages.addEventListener("scroll", () => {
        if (frame || mode() !== "sheet") return;
        frame = requestAnimationFrame(() => { frame = 0; syncTabs(); });
      }, { passive: true });
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    // The side panels slide only once the page has settled (shell.css).
    requestAnimationFrame(() => requestAnimationFrame(() => deps.root.classList.add("dk-slide")));
    // Read back, the phone's machine stats strip steps aside (shell.css).
    // Read a frame later, after a following reader is pinned to the end.
    const thread = q("[data-chat-thread]");
    if (thread) {
      let frame = 0;
      const reading = () => {
        if (frame) return;
        frame = requestAnimationFrame(() => {
          frame = 0;
          deps.root.classList.toggle("dk-reading", thread.scrollHeight - thread.scrollTop - thread.clientHeight > 4);
        });
      };
      thread.addEventListener("scroll", reading, { passive: true });
      new ResizeObserver(reading).observe(q("[data-chat-log]") || thread);
    }
    // The runtime's own handler stops the Artifacts click, so note the opener on the way down.
    deps.root.addEventListener("click", (event) => {
      const t = event.target.closest?.("[data-artifact-toggle]");
      if (t && visible(t)) opener = t;
      // A drag's release is not a tap.
      if (justDragged && event.target.closest?.("[data-theme-rail], [data-chat-artifact]")) {
        event.preventDefault();
        event.stopPropagation();
      }
    }, true);
    deps.root.addEventListener("click", (event) => {
      const t = event.target.closest?.("button, a");
      if (!t || !deps.root.contains(t)) {
        if (event.target.closest?.("[data-theme-backdrop]")) closeOverlays();
        return;
      }
      const fromMenu = Boolean(t.closest("[data-theme-menu]"));
      if (t.matches("[data-theme-inspector], [data-theme-art-live], [data-theme-art-live-bar]")) {
        const open = peek || !isOpen();
        setOpen(open, t);
        if (open && mode() === "sheet") goTab(firstTab());
      } else if (t.matches("[data-theme-inspector-close]")) {
        setOpen(false);
      } else if (t.matches("[data-theme-insp-back]")) {
        page = "";
        syncChrome();
        q("[data-theme-inspector-close]")?.focus({ preventScroll: true });
      } else if (t.matches("[data-theme-output]")) {
        openPage("output", fromMenu ? q("[data-theme-more]") : t);
      } else if (t.matches("[data-show-job]")) {
        pickedJob = t.dataset.showJob;
        seen.output = "";
        openPage("output", t);
      } else if (t.matches("[data-theme-pick-job]")) {
        pickedJob = t.dataset.themePickJob;
        seen.output = "";
        syncChrome();
      } else if (t.matches("[data-theme-out-stop]")) {
        const id = t.dataset.job;
        if (id) q(`[data-kill-job="${CSS.escape(id)}"]`)?.click();
      } else if (t.matches("[data-theme-out-copy]")) {
        copyOutput();
      } else if (t.matches("[data-theme-tab]")) {
        goTab(t.dataset.themeTab, true);
      } else if (t.matches("[data-theme-fold]")) {
        const name = t.dataset.themeFold;
        store.fold[name] = !store.fold[name];
        if (!store.fold[name]) delete store.fold[name];
        save();
        syncChrome();
      } else if (t.matches("[data-theme-agents-show]")) {
        const fold = q("[data-agents-fold]");
        const all = fold?.getAttribute("aria-expanded") === "true";
        if (fold && all !== (t.dataset.themeAgentsShow === "all")) fold.click();
      } else if (t.matches("[data-theme-sort]")) {
        const key = t.dataset.themeSort;
        store.sort = store.sort === `${key}d` ? `${key}a` : store.sort === `${key}a` ? "" : `${key}d`;
        save();
        syncChrome();
      } else if (t.matches("[data-theme-art-chat]")) {
        deps.root.classList.toggle("dk-art-chat");
        syncChrome();
        if (deps.root.classList.contains("dk-art-chat")) setTimeout(() => q("[data-chat-input]")?.focus({ preventScroll: true }), reduced() ? 0 : 280);
      } else if (t.matches("[data-theme-art-back]")) {
        q("[data-artifact-close]")?.click();
      } else if (t.matches("[data-theme-art-more]")) {
        // The runtime closes its menu on any click outside its toggle: open it after this one.
        event.stopPropagation();
        setTimeout(() => q("[data-artifact-menu-toggle]")?.click());
      } else if (t.matches("[data-theme-agent-back]")) {
        const main = q("[data-agent-main]");
        if (main) main.click();
        else document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      } else if (t.matches("[data-theme-title-menu]")) {
        // The runtime's document click would close the menu this click opens.
        if (!titleOpen && titleMenu(t)) event.stopPropagation();
      } else if (t.matches("[data-theme-new]")) {
        q("[data-chat-new]")?.click();
      } else if (t.matches("[data-theme-project]")) {
        act("project");
      } else if (t.matches("[data-theme-proxy]")) {
        opener = q("[data-theme-more]");
        q(`.dk-actions > ${t.dataset.themeProxy}`)?.click();
      } else if (t.matches("[data-theme-attach]")) {
        q("[data-chat-attach]")?.click();
      } else if (t.matches("[data-theme-customize]")) {
        pickerReturn = fromMenu ? q("[data-theme-more]") : t;
        deps.openWallpaperPicker?.();
      } else if (t.matches("[data-theme-scheme]")) {
        const appearance = window.FuseChatAppearance;
        appearance?.setScheme?.(appearance.scheme === "light" ? "dark" : "light");
        syncScheme();
      } else if (t.matches("[data-theme-more]")) {
        toggleMenu();
      } else if (t.matches("[data-theme-turn-act]")) {
        turnAction(t);
      } else if (t.matches("[data-history-toggle]") && !matches("(max-width: 800px)")) {
        // Collapsing or expanding hides the toggle just used: focus moves to the other one.
        setTimeout(() => {
          if (document.activeElement && document.activeElement !== document.body) return;
          deps.root.querySelectorAll("[data-history-toggle]").forEach((btn) => { if (visible(btn) && btn !== t) btn.focus({ preventScroll: true }); });
        });
      } else if (t.matches("[data-theme-expand]")) {
        const turn = t.closest(".chat-turn");
        const open = !turn?.classList.contains("is-expanded");
        turn?.classList.toggle("is-expanded", open);
        t.textContent = open ? "Show less" : "Show more";
        t.setAttribute("aria-expanded", String(open));
      }
      // A menu item did its work: the menu closes; toggles hand focus back to ⋯.
      if (fromMenu) toggleMenu(false, t.matches("[role=menuitemcheckbox]"));
    });
    // Real travel only, as the sidebar's peek: a button appearing under a
    // resting pointer fires pointerover but no movement. The top bar is
    // redrawn, so listen on the root.
    const PEEK_ZONE = "[data-theme-inspector], [data-theme-rail]";
    deps.root.addEventListener("pointermove", (event) => {
      if (event.pointerType !== "mouse" || event.buttons || !(event.movementX || event.movementY)) return;
      if (!event.target.closest?.("[data-theme-inspector]")) return;
      if (store.open || peek || peekOpenTimer || mode() !== "dock") return;
      peekOpenTimer = setTimeout(() => setPeek(true), PEEK_OPEN_MS);
    });
    deps.root.addEventListener("pointerout", (event) => {
      const from = event.target.closest?.(PEEK_ZONE);
      if (!from || from.contains(event.relatedTarget)) return;
      if (!peek) setPeek(false);
      else schedulePeekClose();
    });
    // Using the peeked panel (an agent, a fold, a command) pins it; its ✕ closes it.
    deps.root.addEventListener("click", (event) => {
      if (peek && event.target.closest?.("[data-theme-rail]") && !event.target.closest("[data-theme-inspector-close]")) setOpen(true);
    }, true);
    document.addEventListener("pointerdown", (event) => {
      if (peek && !event.target.closest?.("[data-theme-rail], [data-theme-inspector], .chat-pop, dialog")) setPeek(false);
    }, true);
    q("[data-theme-menu]")?.addEventListener("keydown", menuKey);
    q("[data-theme-menu]")?.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !event.relatedTarget.closest?.(".dk-menu-wrap")) toggleMenu(false);
    });
    q("[data-theme-tabs]")?.addEventListener("keydown", tabKey);
    document.addEventListener("click", (event) => {
      const menu = q("[data-theme-menu]");
      if (menu && !menu.hidden && !event.target.closest?.(".dk-menu-wrap")) toggleMenu(false);
      // Over the chat, a click outside Activity closes it.
      if (mode() !== "dock" && isOpen() && !justDragged
        && !event.target.closest?.("[data-theme-rail], [data-theme-inspector], [data-theme-art-live], [data-theme-art-live-bar], [data-theme-output], [data-show-job], .chat-pop, dialog")) {
        setOpen(false);
      }
    });
    // Escape closes the topmost of the theme's own popups, and only that one:
    // the runtime would also close the drawer or arm Stop on the same key.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      // Escape in a terminal is the shell's.
      if (event.target.closest?.("[data-terminal]")) return;
      const menu = q("[data-theme-menu]");
      const account = q("[data-account-menu]");
      const ctx = q("[data-context-pop]");
      if (menu && !menu.hidden) toggleMenu(false, true);
      else if (account && !account.hidden) {
        q("[data-account-close]")?.click();
        q("[data-account-toggle]")?.focus();
      } else if (ctx && !ctx.hidden) {
        q("[data-chat-context]")?.click();
        q("[data-chat-context]")?.focus();
      } else if (deps.root.classList.contains("dk-art-chat")) {
        deps.root.classList.remove("dk-art-chat");
        syncChrome();
        q("[data-theme-art-chat]")?.focus({ preventScroll: true });
      } else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (peek) { event.preventDefault(); setPeek(false); return; }
      // Over the chat it closes; docked, it closes when the focus is in it.
      const inside = q("[data-theme-rail]")?.contains(document.activeElement);
      if ((inside || mode() !== "dock") && isOpen() && !document.querySelector("body > .chat-pop, dialog[open]")) {
        event.preventDefault();
        if (page && mode() !== "sheet") { page = ""; syncChrome(); } else setOpen(false);
      }
    });
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
        const back = lastOpener?.isConnected && visible(lastOpener) ? lastOpener : lastOpener?.closest?.("[data-theme-menu]") && q("[data-theme-more]");
        back?.focus?.({ preventScroll: true });
      };
      setTimeout(check);
    }, true);
    [DOCK, PHONE, "(max-width: 800px)"].forEach((query) => window.matchMedia(query).addEventListener?.("change", () => {
      q("[data-theme-pages]")?.scrollTo({ left: 0 });
      if (mode() !== "dock") peek = false;
      syncChrome();
    }));
    document.addEventListener("fuse-appearance", syncScheme);
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
    // Rows, lists and the todo tray change outside the run clock; a clock or
    // spinner ticking in a row changes nothing drawn here.
    watch = new MutationObserver((records) => {
      if (records.some(({ target }) => !target.closest?.(TICKS))) scheduleChrome();
    });
    const rail = q("[data-theme-rail]");
    if (rail) watch.observe(rail, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    watch.observe(deps.root, { attributes: true, attributeFilter: ["class"] });
    // The title menu is the runtime's row pop, appended to <body>.
    new MutationObserver(() => { if (titleOpen && !document.querySelector("body > .chat-pop")) titleClosed(); })
      .observe(document.body, { childList: true });
    // A sheet whose toggle is out of sight (in ⋯, or folded off a narrow bar)
    // hands focus back to ⋯ when it closes; the runtime's first "false" at
    // load closes nothing.
    new MutationObserver((records) => {
      records.forEach(({ target, oldValue }) => {
        if (oldValue !== "true" || !target.matches(SHEET_TOGGLES) || target.getAttribute("aria-expanded") !== "false" || visible(target)) return;
        setTimeout(() => {
          const active = document.activeElement;
          if (!active || active === document.body) q("[data-theme-more]")?.focus({ preventScroll: true });
        });
      });
    }).observe(deps.root, { subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ["aria-expanded"] });
    const list = q("[data-history-list]");
    if (list) new MutationObserver((records) => { if (records.some(({ target }) => !target.closest?.(TICKS))) syncPins(); }).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-pinned", "aria-current"] });
    const picker = q("[data-wallpaper-picker]");
    if (picker) new MutationObserver(() => {
      if (!picker.hidden || !pickerReturn) return;
      const back = pickerReturn;
      pickerReturn = null;
      const active = document.activeElement;
      if (!active || active === document.body || active.matches("[data-chat-input]")) back.focus({ preventScroll: true });
    }).observe(picker, { attributes: true, attributeFilter: ["hidden"] });
    bindSheet();
    syncScheme();
  }

  function closeOverlays() {
    if (deps.root.classList.contains("dk-art-chat")) {
      deps.root.classList.remove("dk-art-chat");
      syncChrome();
    }
    if (mode() !== "dock" && isOpen()) setOpen(false);
  }

  // The footer sun keeps one name and says its state with aria-pressed; the
  // ⋯ item is a checkbox.
  function syncScheme() {
    const light = window.FuseChatAppearance?.scheme === "light";
    deps.root.querySelectorAll("[data-theme-scheme]").forEach((btn) => {
      btn.setAttribute(btn.getAttribute("role") === "menuitemcheckbox" ? "aria-checked" : "aria-pressed", String(light));
    });
  }

  // Reply actions under a finished turn: copy, fork, pin, read aloud.
  let speaking = null;
  function stopSpeaking() {
    speaking?.setAttribute("aria-pressed", "false");
    speaking = null;
  }

  async function turnAction(btn) {
    const kind = btn.dataset.themeTurnAct;
    const turn = btn.closest(".chat-turn");
    // The whole reply's markdown, else every rendered block of it.
    const text = (btn.closest("article")?._message?.content || "").trim()
      || [...(turn?.querySelectorAll("[data-flow] > [data-content-block]") || [])].map((el) => el.innerText.trim()).join("\n\n");
    if (kind === "copy") {
      try {
        await navigator.clipboard.writeText(text);
        window.FuseChatActions?.hint?.("Copied to clipboard.");
      } catch (_) {
        window.FuseChatActions?.hint?.("Clipboard copy failed.");
      }
    } else if (kind === "fork" || kind === "pin") {
      act(kind);
    } else if (kind === "speak" && "speechSynthesis" in window) {
      const again = speaking === btn;
      speechSynthesis.cancel();
      stopSpeaking();
      if (again || !text) return;
      const say = new SpeechSynthesisUtterance(text);
      say.onend = say.onerror = () => { if (speaking === btn) stopSpeaking(); };
      speaking = btn;
      btn.setAttribute("aria-pressed", "true");
      speechSynthesis.speak(say);
    }
  }

  // Pin follows the open chat's row in the sidebar.
  function syncPins() {
    const row = q('[data-history-list] [data-row-open][aria-current="true"]')?.closest("[data-history-item]");
    const pinned = Boolean(row?.hasAttribute("data-pinned"));
    const label = pinned ? "Unpin chat" : "Pin chat";
    deps.root.querySelectorAll('[data-theme-turn-act="pin"]').forEach((btn) => {
      if (btn.getAttribute("aria-pressed") === String(pinned) && btn.title === label) return;
      btn.setAttribute("aria-pressed", String(pinned));
      btn.setAttribute("aria-label", label);
      btn.title = label;
    });
  }

  // —— Activity: its lists, the plan and a command's output ——

  const jobId = (job) => String(job.command_id || job.id);
  const jobLive = (job) => LIVE_JOB.has(String(job.state || "running"));

  async function copyOutput() {
    const job = (deps.jobsRows || []).find((row) => jobId(row) === pickedJob);
    const text = job ? String(job.output || job.preview || "") : "";
    try {
      await navigator.clipboard.writeText(text);
      window.FuseChatActions?.hint?.(text ? "Copied the output." : "No output yet.");
    } catch (_) {
      window.FuseChatActions?.hint?.("Clipboard copy failed.");
    }
  }

  function syncOutput() {
    const rows = deps.jobsRows || [];
    const job = rows.find((row) => jobId(row) === pickedJob) || rows.find(jobLive) || rows[0];
    if (job) pickedJob = jobId(job);
    const out = q("[data-theme-out]");
    show(out, Boolean(job));
    show(q("[data-theme-out-empty]"), !job);
    // The phone's Output page picks among the live commands.
    const picks = q("[data-theme-out-picks]");
    const live = rows.filter(jobLive);
    if (picks) {
      const html = live.length > 1 ? live.map((row) => `<button type="button" class="dk-pick${jobId(row) === pickedJob ? " is-on" : ""}" data-theme-pick-job="${esc(jobId(row))}" aria-pressed="${jobId(row) === pickedJob}">${esc(deps.toolText(row.description || deps.displayCommand(row) || row.id))}</button>`).join("") : "";
      if (picks._html !== html) { picks._html = html; picks.innerHTML = html; }
    }
    if (!job) return false;
    const state = String(job.state || "running");
    const monitor = job.kind === "monitor" || job.name === "monitor";
    const command = deps.displayCommand(job) || "";
    const secs = Math.round((Number(job.started_ms) > 0 ? Date.now() - Number(job.started_ms) : Number(job.elapsed_ms) || 0) / 1000);
    const events = Number(job.event_count);
    const status = state === "running" ? `Running for ${deps.formatActivityClock(secs)}` : state === "queued" ? "Queued" : state === "cancelling" ? "Stopping" : deps.jobStateLabel(state);
    const meta = [status, job.host || "", monitor && events > 0 ? `${events} event${events === 1 ? "" : "s"}` : ""].filter(Boolean);
    setText(q("[data-theme-out-title]"), job.description || command || job.id);
    const metaEl = q("[data-theme-out-meta]");
    const metaHtml = meta.map((part, i) => `<span${i === 0 && jobLive(job) ? ' class="is-live"' : ""}>${esc(part)}</span>`).join("");
    if (metaEl && metaEl._html !== metaHtml) { metaEl._html = metaHtml; metaEl.innerHTML = metaHtml; }
    const icon = q("[data-theme-out-icon]");
    if (icon && icon.dataset.kind !== (monitor ? "monitor" : "command")) {
      icon.dataset.kind = monitor ? "monitor" : "command";
      icon.innerHTML = monitor ? EYE : TERM;
    }
    const stop = q("[data-theme-out-stop]");
    if (stop) {
      show(stop, jobLive(job) && state !== "cancelling");
      stop.dataset.job = jobId(job);
    }
    setText(q("[data-theme-out-name]"), command ? `$ ${command}` : "");
    const output = String(job.output || job.preview || "");
    const key = `${jobId(job)}:${output.length}`;
    // Drawn only where it shows: the output page (every page, on a phone's sheet).
    const shown = store.open && (page === "output" || mode() === "sheet");
    if (shown && key !== seen.output) {
      seen.output = key;
      const pre = q("[data-theme-out-text]");
      if (pre) {
        const stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
        pre.innerHTML = deps.renderAnsi(output.split("\n").slice(-400).join("\n")) || '<span class="dk-out-wait">No output yet.</span>';
        if (stick || !pre._seen) pre.scrollTop = pre.scrollHeight;
        pre._seen = true;
      }
    }
    deps.root.querySelectorAll("[data-jobs-list] [data-show-job]").forEach((btn) => {
      const on = page === "output" && btn.dataset.showJob === pickedJob;
      if (btn.classList.contains("is-picked") !== on) btn.classList.toggle("is-picked", on);
    });
    return true;
  }

  // Main's own clock and context sit in its row; the runtime draws the rest.
  function syncMainRow() {
    const busy = Boolean(deps.streaming || deps.runIsActive?.(deps.activeRun));
    const msgs = deps.messages || [];
    let last = null;
    for (let i = msgs.length - 1; i >= 0 && !last; i -= 1) if (msgs[i]?.role === "assistant") last = msgs[i];
    const secs = busy && last && deps.liveElapsedSeconds ? deps.liveElapsedSeconds(last, true) : null;
    setText(q("[data-theme-main-time]"), secs != null && Number.isFinite(secs) ? deps.formatActivityClock(Math.round(secs)) : "—");
    const ctx = q("[data-theme-main-ctx]");
    const pct = Number(/\((\d+)%\)/.exec(q("[data-chat-context]")?.title || "")?.[1]);
    const html = Number.isFinite(pct) ? `<b>${pct}%</b><i class="dk-meter${pct >= 70 ? " is-high" : ""}"><i style="width:${Math.min(100, pct)}%"></i></i>` : "<b>—</b>";
    if (ctx && ctx._html !== html) { ctx._html = html; ctx.innerHTML = html; }
    return busy;
  }

  function syncGroups() {
    const agentsRows = deps.agentsRows || [];
    const jobsRows = deps.jobsRows || [];
    const agentsLive = agentsRows.filter((row) => deps.agentLive(row.status)).length;
    const jobsLive = jobsRows.filter((row) => ["running", "cancelling"].includes(String(row.state || "running"))).length;
    const agentsList = q("[data-agents-list]");
    const jobsList = q("[data-jobs-list]");
    const agentsShown = Boolean(agentsList && !agentsList.hidden);
    const jobsShown = Boolean(jobsList && !jobsList.hidden);
    show(q('[data-theme-grp="agents"]'), agentsShown);
    show(q('[data-theme-grp="commands"]'), jobsShown);
    setText(q("[data-theme-agents-count]"), agentsRows.length ? `${agentsLive} running of ${agentsRows.length}` : "");
    const shownJobs = jobsList ? jobsList.querySelectorAll("[data-job-row]").length : 0;
    const shownLive = jobsList ? jobsList.querySelectorAll("[data-job-row].is-running").length : 0;
    setText(q("[data-theme-jobs-count]"), shownJobs ? (shownLive ? `${shownLive} running` : `${shownJobs} finished`) : "");
    const all = q("[data-agents-fold]")?.getAttribute("aria-expanded") === "true";
    deps.root.querySelectorAll("[data-theme-agents-show]").forEach((btn) => setAttr(btn, "aria-pressed", String((btn.dataset.themeAgentsShow === "all") === all)));
    // Sorting reorders rows in CSS (order keys on each row), so it survives every redraw.
    if (agentsList && (agentsList.dataset.sort || "") !== store.sort) {
      if (store.sort) agentsList.dataset.sort = store.sort;
      else delete agentsList.dataset.sort;
    }
    deps.root.querySelectorAll("[data-theme-sort]").forEach((btn) => {
      const key = btn.dataset.themeSort;
      const on = store.sort.startsWith(key) ? (store.sort.endsWith("a") ? "ascending" : "descending") : "";
      if ((btn.getAttribute("aria-sort") || "") !== on) {
        if (on) btn.setAttribute("aria-sort", on);
        else btn.removeAttribute("aria-sort");
      }
    });
    deps.root.querySelectorAll("[data-theme-fold]").forEach((btn) => {
      const folded = Boolean(store.fold[btn.dataset.themeFold]);
      setAttr(btn, "aria-expanded", String(!folded));
      btn.closest(".dk-grp")?.classList.toggle("is-folded", folded);
    });
    // The plan of the transcript on screen: main's, or the open subagent's.
    const todos = q(deps.root.classList.contains("is-agent-view") ? "[data-agent-live-todos]" : "[data-live-todos]");
    const plan = Boolean(todos && !todos.hidden);
    const rows = todos ? todos.querySelectorAll(".chat-todo-row") : [];
    const done = todos ? todos.querySelectorAll(".chat-todo-row:is(.is-completed, .is-cancelled)").length : 0;
    show(q('[data-theme-grp="plan"]'), plan);
    show(q("[data-theme-plan-empty]"), !plan);
    setText(q("[data-theme-plan-count]"), rows.length ? `${done}/${rows.length}` : "");
    const bar = q("[data-theme-plan-bar]");
    const width = rows.length ? `${Math.round((done / rows.length) * 100)}%` : "0%";
    if (bar && bar.style.width !== width) bar.style.width = width;
    return { agentsLive, jobsLive, tasks: agentsShown || jobsShown, plan: plan && rows.length ? { done, total: rows.length } : null };
  }

  function syncTitle() {
    const btn = q("[data-theme-title-menu]");
    const text = (q("[data-chat-title]")?.textContent || "").trim();
    const title = text ? `${text} · rename, pin, fork or archive` : "Rename, pin, fork or archive";
    if (btn && btn.title !== title) btn.title = title;
  }

  // Runtime ticks and DOM changes ask for a sync; at most one runs a frame.
  function scheduleChrome() {
    if (!chromeFrame) chromeFrame = requestAnimationFrame(() => { chromeFrame = 0; syncChrome(); });
  }

  // A clock tick changes only main's time while a run is on and a live
  // command's age; idle, it has nothing to redraw.
  function tickChrome() {
    if (deps.streaming || deps.runIsActive?.(deps.activeRun) || (deps.jobsRows || []).some(jobLive)) scheduleChrome();
  }

  function syncChrome() {
    if (!deps.root) return;
    if (chromeFrame) { cancelAnimationFrame(chromeFrame); chromeFrame = 0; }
    bind();
    const artifact = deps.root.classList.contains("is-artifact");
    // An artifact takes the window: an open Activity steps aside and comes back after.
    if (artifact !== wasArtifact) {
      if (artifact && store.open && mode() !== "sheet") { openBeforeArtifact = true; store.open = false; }
      else if (!artifact) {
        if (openBeforeArtifact) store.open = true;
        openBeforeArtifact = null;
        deps.root.classList.remove("dk-art-chat");
      }
      wasArtifact = artifact;
    }
    // The artifact fills the window when asked to ("Full window") or when the
    // window has no room for it beside the chat; then Chat slides in over it.
    const full = artifact && (deps.root.classList.contains("is-artifact-wide") || !matches(DOCK));
    if (deps.root.classList.contains("dk-art-full") !== full) deps.root.classList.toggle("dk-art-full", full);
    if (!full && deps.root.classList.contains("dk-art-chat")) deps.root.classList.remove("dk-art-chat");
    const how = mode();
    if (deps.root.dataset.themeInsp !== how) deps.root.dataset.themeInsp = how;
    const counts = syncGroups();
    const busy = syncMainRow();
    const output = syncOutput();
    if (!output && page === "output") page = "";
    // A phone's sheet shows every page side by side; wider, one at a time.
    const rail = q("[data-theme-rail]");
    const shown = how === "sheet" ? "" : page;
    if (rail && (rail.dataset.page || "") !== shown) {
      if (shown) rail.dataset.page = shown;
      else delete rail.dataset.page;
    }
    show(q("[data-theme-insp-back]"), page === "output" && how !== "sheet");
    const running = counts.agentsLive + counts.jobsLive;
    const label = running ? `${running} running` : counts.plan ? `Plan ${counts.plan.done}/${counts.plan.total}` : "";
    setText(q("[data-theme-live-label]"), label);
    setText(q("[data-theme-live-count]"), running ? `${running} running` : "");
    const toggle = q("[data-theme-inspector]");
    toggle?.classList.toggle("is-live", running > 0);
    toggle?.classList.toggle("has-label", Boolean(label));
    setAttr(toggle, "aria-label", label ? `Activity: ${label}` : "Activity");
    const artLive = q("[data-theme-art-live]");
    show(artLive, running > 0);
    artLive?.classList.toggle("is-live", running > 0);
    artLive?.classList.toggle("has-label", running > 0);
    setText(q("[data-theme-art-live-label]"), running ? `${running} running` : "");
    setText(q("[data-theme-art-bar-label]"), running ? `${running} running` : "Activity");
    q("[data-theme-art-live-bar]")?.classList.toggle("is-live", running > 0);
    setAttr(q("[data-theme-art-chat]"), "aria-pressed", String(deps.root.classList.contains("dk-art-chat")));
    setText(q("[data-theme-tab-n=\"activity\"]"), running ? String(running) : "");
    setText(q("[data-theme-tab-n=\"plan\"]"), counts.plan ? `${counts.plan.done}/${counts.plan.total}` : "");
    show(q("[data-theme-insp-empty]"), !counts.tasks);
    setText(q("[data-theme-insp-empty]"), busy ? "Working in the chat. Subagents and background commands show up here." : "Nothing running.");
    syncTitle();
    syncPins();
    show(q("[data-theme-agent-back]"), deps.root.classList.contains("is-agent-view"));
    if (peek && (store.open || how !== "dock")) peek = false;
    const next = store.open || peek ? "open" : "closed";
    deps.root.classList.toggle("dk-insp-peek", peek);
    if (deps.root.dataset.themeRailState !== next) deps.root.dataset.themeRailState = next;
    syncLayer(next, how);
    if (how === "sheet" && next === "open") syncTabs();
    deps.root.classList.toggle("dk-has-output", output);
    const open = next === "open";
    setAttr(toggle, "aria-pressed", String(store.open));
    // New work while it is shut only puts a dot on the button.
    const todos = q("[data-live-todos]");
    const sig = [counts.tasks && (deps.agentsRows || []).length, counts.tasks && (deps.jobsRows || []).map((row) => `${jobId(row)}:${row.state}`).join(","),
      counts.plan && todos.textContent.length].join("|");
    const id = new URLSearchParams(location.search).get("t") || "";
    if (id !== threadKey) {
      threadKey = id;
      baseSig = sig;
    }
    if (open) baseSig = sig;
    toggle?.classList.toggle("has-notes", sig !== baseSig);
    // What this sync wrote is drawn already: it must not ask for another.
    watch?.takeRecords();
  }

  return { scheduleChrome, syncChrome, tickChrome };
}

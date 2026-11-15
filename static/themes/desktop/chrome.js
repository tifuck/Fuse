// Desktop's chrome: the top bar (the breadcrumb's title menu, "← Main" in a
// subagent, the ⋯ menu), the sidebar's Customize and light/dark switches,
// the composer's chips row, and the inspector: a right column of cards
// (Preview is the artifact pane; Tasks, Plan, Terminal and Diff are drawn
// here from the runtime's live state). The inspector opens only when the
// user asks; the choice is kept in localStorage, and new work puts a dot on
// its button instead. Under 1040px it is an overlay, on phones a modal sheet.
const WIDE = "(min-width: 1040px)";
const PHONE = "(max-width: 800px)";
const STORE = "fuse.chat.desktop.inspector.v1";
const EDIT_TOOLS = new Set(["search_replace", "write_file", "edit_file", "apply_patch"]);
// Toggles whose sheet may close while they sit in a closed menu or a folded bar.
const SHEET_TOGGLES = "[data-skills-toggle], [data-view-toggle], [data-memory-toggle], [data-artifact-toggle]";

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "{}");
    return { open: saved.open === true, cards: saved.cards && typeof saved.cards === "object" ? saved.cards : {} };
  } catch (_) {
    return { open: false, cards: {} };
  }
}

export function create(deps) {
  const store = load();
  let bound = false;
  let lastState = "";
  let pickedJob = "";
  let titleOpen = false;
  let pickerReturn = null;
  let opener = null;
  let threadKey = null;
  let baseSig = null;
  const inertSaved = new Map();
  const seen = { diff: "", terminal: "" };
  const q = (sel) => deps.root.querySelector(sel);
  const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };
  const show = (el, on) => { if (el && el.hidden === on) el.hidden = !on; };
  const esc = (value) => deps.escapeHtml(String(value ?? ""));
  const visible = (el) => Boolean(el && el.getClientRects().length);
  const thread = () => new URLSearchParams(location.search).get("t") || "";

  function save() {
    const ids = Object.keys(store.cards);
    // ponytail: keeps the 50 most recently touched threads' dismissed cards.
    ids.slice(0, Math.max(0, ids.length - 50)).forEach((id) => delete store.cards[id]);
    try { localStorage.setItem(STORE, JSON.stringify(store)); } catch (_) {}
  }

  const closedCards = () => new Set(store.cards[thread()] || []);
  function setCard(name, dismissed) {
    const id = thread();
    const set = closedCards();
    if (dismissed) set.add(name); else set.delete(name);
    delete store.cards[id];
    if (set.size) store.cards[id] = [...set];
    save();
  }

  function setOpen(open, from) {
    if (from) opener = from;
    store.open = Boolean(open);
    if (store.open) delete store.cards[thread()];
    save();
    syncChrome();
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

  // —— Inspector focus and modality ——

  // Narrow, the open inspector is a dialog over the chat; on phones a modal one.
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

  function syncLayer(state) {
    const rail = q("[data-theme-rail]");
    if (!rail) return;
    const narrow = !window.matchMedia(WIDE).matches;
    const open = state === "open";
    setLayer(rail, open && narrow, open && window.matchMedia(PHONE).matches);
    if (state === lastState) return;
    const was = lastState;
    lastState = state;
    if (!was) return;
    if (open && narrow) q("[data-theme-inspector-close]")?.focus({ preventScroll: true });
    else if (!open) {
      const active = document.activeElement;
      if (!active || active === document.body || rail.contains(active)) {
        const back = [opener, q("[data-theme-inspector]"), q("[data-theme-more]")].find(visible);
        back?.focus({ preventScroll: true });
      }
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    // The runtime's own handler stops the Artifacts click, so note the opener on the way down.
    deps.root.addEventListener("click", (event) => {
      const t = event.target.closest?.("[data-artifact-toggle]");
      if (t && visible(t)) opener = t;
    }, true);
    deps.root.addEventListener("click", (event) => {
      const t = event.target.closest?.("button, a");
      if (!t || !deps.root.contains(t)) return;
      const fromMenu = Boolean(t.closest("[data-theme-menu]"));
      if (t.matches("[data-theme-inspector]")) {
        const open = deps.root.dataset.themeRailState === "open" || deps.root.classList.contains("is-artifact");
        if (open && deps.root.classList.contains("is-artifact")) q("[data-artifact-close]")?.click();
        setOpen(!open, t);
      } else if (t.matches("[data-theme-inspector-close]")) {
        if (deps.root.classList.contains("is-artifact")) q("[data-artifact-close]")?.click();
        setOpen(false);
      } else if (t.matches("[data-theme-terminal]")) {
        setCard("terminal", false);
        setOpen(true, fromMenu ? q("[data-theme-more]") : t);
        q('[data-theme-card="terminal"]:not([hidden])')?.scrollIntoView({ block: "nearest" });
      } else if (t.matches("[data-todo-header]")) {
        if (t.getAttribute("aria-expanded") === "true") {
          setCard("plan", false);
          setOpen(true, t);
          const plan = q('[data-theme-card="plan"]:not([hidden]) [data-live-todos]');
          plan?.scrollIntoView({ block: "nearest" });
          if (window.matchMedia(WIDE).matches) plan?.focus({ preventScroll: true });
        } else if (!window.matchMedia(WIDE).matches) setOpen(false);
      } else if (t.matches("[data-theme-card-close]")) {
        setCard(t.dataset.themeCardClose, true);
        syncChrome();
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
      } else if (t.matches("[data-show-job]")) {
        pickedJob = t.dataset.showJob;
        setCard("terminal", false);
        syncChrome();
      } else if (t.matches("[data-theme-turn-act]")) {
        turnAction(t);
      } else if (t.matches("[data-history-toggle]") && !window.matchMedia(PHONE).matches) {
        // Collapsing or expanding hides the toggle just used: focus moves to the other one.
        setTimeout(() => {
          if (document.activeElement && document.activeElement !== document.body) return;
          deps.root.querySelectorAll("[data-history-toggle]").forEach((btn) => { if (visible(btn) && btn !== t) btn.focus({ preventScroll: true }); });
        });
      } else if (t.matches("[data-theme-diff-fold]")) {
        t.hidden = true;
        for (let row = t.nextElementSibling; row?.hidden; row = row.nextElementSibling) row.hidden = false;
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
    q("[data-theme-menu]")?.addEventListener("keydown", menuKey);
    q("[data-theme-menu]")?.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !event.relatedTarget.closest?.(".dk-menu-wrap")) toggleMenu(false);
    });
    document.addEventListener("click", (event) => {
      const menu = q("[data-theme-menu]");
      if (menu && !menu.hidden && !event.target.closest?.(".dk-menu-wrap")) toggleMenu(false);
      // Narrow: the inspector overlays the chat; a click outside closes it.
      if (!window.matchMedia(WIDE).matches && deps.root.dataset.themeRailState === "open"
        && !event.target.closest?.("[data-theme-rail], [data-theme-inspector], [data-theme-terminal], [data-todo-header], [data-show-job], .chat-pop, dialog")) {
        setOpen(false);
      }
    });
    // Escape closes the topmost of the theme's own popups, and only that one:
    // the runtime would also close the drawer or arm Stop on the same key.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
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
      } else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Narrow it is an overlay; docked, it closes when the focus is in it.
      const inside = q("[data-theme-rail]")?.contains(document.activeElement);
      if ((inside || !window.matchMedia(WIDE).matches) && deps.root.dataset.themeRailState === "open" && !deps.root.classList.contains("is-artifact")
        && !document.querySelector("body > .chat-pop, dialog[open]")) setOpen(false);
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
    window.matchMedia(WIDE).addEventListener?.("change", () => syncChrome());
    window.matchMedia(PHONE).addEventListener?.("change", () => syncChrome());
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
    if (historyList) new MutationObserver(() => {
      if (!rowFocus || (document.activeElement && document.activeElement !== document.body)) return;
      const item = [...historyList.querySelectorAll("[data-history-item]")].find((el) => rowKey(el) === rowFocus.key);
      if (item) rowControls(item)[rowFocus.at]?.focus({ preventScroll: true });
    }).observe(historyList, { childList: true, subtree: true });
    // Rows, lists and the todo tray change outside the run clock.
    const watch = new MutationObserver(() => syncChrome());
    const rail = q("[data-theme-rail]");
    if (rail) watch.observe(rail, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    watch.observe(deps.root, { attributes: true, attributeFilter: ["class"] });
    // The title menu is the runtime's row pop, appended to <body>.
    new MutationObserver(() => { if (titleOpen && !document.querySelector("body > .chat-pop")) titleClosed(); })
      .observe(document.body, { childList: true });
    // A sheet whose toggle is out of sight (in ⋯, or folded off a narrow bar)
    // hands focus back to ⋯ when it closes.
    new MutationObserver((records) => {
      records.forEach(({ target }) => {
        if (!target.matches(SHEET_TOGGLES) || target.getAttribute("aria-expanded") !== "false" || visible(target)) return;
        setTimeout(() => {
          const active = document.activeElement;
          if (!active || active === document.body) q("[data-theme-more]")?.focus({ preventScroll: true });
        });
      });
    }).observe(deps.root, { subtree: true, attributes: true, attributeFilter: ["aria-expanded"] });
    const list = q("[data-history-list]");
    if (list) new MutationObserver(syncPins).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-pinned", "aria-current"] });
    const picker = q("[data-wallpaper-picker]");
    if (picker) new MutationObserver(() => {
      if (!picker.hidden || !pickerReturn) return;
      const back = pickerReturn;
      pickerReturn = null;
      const active = document.activeElement;
      if (!active || active === document.body || active.matches("[data-chat-input]")) back.focus({ preventScroll: true });
    }).observe(picker, { attributes: true, attributeFilter: ["hidden"] });
    syncScheme();
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
    const label = pinned ? "Unpin" : "Pin";
    deps.root.querySelectorAll('[data-theme-turn-act="pin"]').forEach((btn) => {
      if (btn.getAttribute("aria-pressed") === String(pinned) && btn.title === label) return;
      btn.setAttribute("aria-pressed", String(pinned));
      btn.setAttribute("aria-label", label);
      btn.title = label;
    });
  }

  // —— Inspector cards ——

  function editDiff(trace) {
    const found = [trace.preview, trace.output].find((value) => typeof value === "string" && deps.looksLikeDiff(value));
    if (found) return found;
    if (typeof trace.old_string === "string" || typeof trace.new_string === "string") {
      const lines = (text, mark) => String(text || "").split("\n").filter((line, i, all) => line || i < all.length - 1).map((line) => mark + line);
      return [...lines(trace.old_string, "-"), ...lines(trace.new_string, "+")].join("\n");
    }
    if (trace.name === "write_file" && typeof trace.content === "string") return trace.content.split("\n").map((line) => "+" + line).join("\n");
    return "";
  }

  function latestEdit() {
    const messages = deps.messages || [];
    for (let m = messages.length - 1; m >= 0; m -= 1) {
      const tools = messages[m].tools || [];
      for (let t = tools.length - 1; t >= 0; t -= 1) {
        const trace = tools[t];
        if (!EDIT_TOOLS.has(trace.name) || trace.status === "error" || trace.error) continue;
        const diff = editDiff(trace);
        if (diff) return { trace, diff };
      }
    }
    return null;
  }

  // Long runs of unchanged rows fold behind a button that shows them.
  function foldUnchanged(html) {
    const box = document.createElement("div");
    box.innerHTML = html;
    const rows = [...box.querySelectorAll(".chat-console-nline")];
    let run = [];
    const flush = () => {
      if (run.length > 6) {
        const hidden = run.slice(2, -2);
        const note = document.createElement("button");
        note.type = "button";
        note.className = "dk-diff-fold";
        note.textContent = `${hidden.length} unmodified lines`;
        note.dataset.themeDiffFold = "";
        hidden[0].before(note);
        hidden.forEach((row) => { row.hidden = true; });
      }
      run = [];
    };
    rows.forEach((row) => { if (row.classList.contains("is-ctx")) run.push(row); else flush(); });
    flush();
    return box.innerHTML;
  }

  function syncDiff(closed) {
    const card = q('[data-theme-card="diff"]');
    if (!card) return false;
    const edit = latestEdit();
    const key = edit ? `${edit.trace.id}:${edit.diff.length}` : "";
    if (key !== seen.diff) {
      seen.diff = key;
      if (edit) {
        const counts = deps.consoleDiffCounts(edit.diff);
        const path = String(edit.trace.path || edit.trace.file_path || "");
        setText(card.querySelector("[data-theme-diff-file]"), path.split("/").slice(-2).join("/"));
        card.querySelector("[data-theme-diff-stat]").innerHTML = `<ins>+${counts.added}</ins> <del>-${counts.removed}</del>`;
        card.querySelector("[data-theme-diff]").innerHTML = foldUnchanged(deps.consoleDiffHtml(edit.diff, { limit: 400 }));
      }
    }
    const on = Boolean(edit) && !closed.has("diff");
    show(card, on);
    return on;
  }

  function syncTerminal(closed) {
    const card = q('[data-theme-card="terminal"]');
    if (!card) return false;
    const rows = deps.jobsRows || [];
    const job = rows.find((row) => (row.command_id || row.id) === pickedJob)
      || rows.find((row) => ["running", "queued"].includes(String(row.state || "running"))) || rows[0];
    const output = job ? String(job.output || job.preview || "") : "";
    const key = job ? `${job.command_id || job.id}:${output.length}` : "";
    if (key !== seen.terminal) {
      seen.terminal = key;
      if (job) {
        setText(card.querySelector("[data-theme-term-name]"), `$ ${deps.displayCommand(job) || job.description || job.id}`);
        const out = card.querySelector("[data-theme-term-out]");
        out.innerHTML = deps.renderAnsi(output.split("\n").slice(-200).join("\n")) || esc("(no output yet)");
        out.scrollTop = out.scrollHeight;
      }
    }
    deps.root.querySelectorAll("[data-jobs-list] [data-show-job]").forEach((btn) => {
      const on = Boolean(job) && btn.dataset.showJob === (job.command_id || job.id);
      if (btn.classList.contains("is-picked") !== on) btn.classList.toggle("is-picked", on);
    });
    show(card, Boolean(job) && !closed.has("terminal"));
    return Boolean(job);
  }

  function syncTasks(closed) {
    const strip = q("[data-work-strip]");
    const live = Boolean(strip && !strip.hidden);
    const agents = (deps.agentsRows || []).length;
    const jobs = (deps.jobsRows || []).length;
    setText(q("[data-theme-tasks-count]"), agents + jobs ? String(agents + jobs) : "");
    show(q('[data-theme-card="tasks"]'), live && !closed.has("tasks"));
    return live;
  }

  function syncPlan(closed) {
    const todos = q("[data-live-todos]");
    // In a subagent the composer shows its own list; the card keeps to main's.
    const live = Boolean(todos && !todos.hidden) && !deps.root.classList.contains("is-agent-view");
    const items = todos ? todos.querySelectorAll("li, [data-todo-id], .chat-todo-row").length : 0;
    setText(q("[data-theme-plan-count]"), items ? String(items) : "");
    show(q('[data-theme-card="plan"]'), live && !closed.has("plan"));
    return live;
  }

  function syncChips() {
    const project = deps.sessionProject?.();
    const host = q("[data-theme-host-chip]");
    const proj = q("[data-theme-project-chip]");
    show(host, Boolean(project && project.host_alias));
    show(proj, Boolean(project && project.project_name));
    setText(q("[data-theme-host-name]"), project?.host_alias || "");
    setText(q("[data-theme-project-name]"), project?.project_name || "");
  }

  function syncTitle() {
    const btn = q("[data-theme-title-menu]");
    const text = (q("[data-chat-title]")?.textContent || "").trim();
    const title = text ? `${text} · rename, pin, fork or archive` : "Rename, pin, fork or archive";
    if (btn && btn.title !== title) btn.title = title;
  }

  function syncChrome() {
    if (!deps.root) return;
    bind();
    const closed = closedCards();
    const tasks = syncTasks(closed);
    const plan = syncPlan(closed);
    const terminal = syncTerminal(closed);
    const diff = syncDiff(closed);
    syncChips();
    syncTitle();
    syncPins();
    show(q("[data-theme-agent-back]"), deps.root.classList.contains("is-agent-view"));
    const artifact = deps.root.classList.contains("is-artifact");
    const cards = deps.root.querySelectorAll("[data-theme-rail] [data-theme-card]:not([data-theme-card=preview]):not([hidden])").length;
    show(q("[data-theme-insp-empty]"), !cards && !artifact);
    const running = Boolean(deps.streaming || deps.runIsActive(deps.activeRun));
    const next = store.open ? "open" : "closed";
    if (deps.root.dataset.themeRailState !== next) deps.root.dataset.themeRailState = next;
    syncLayer(artifact ? "open" : next);
    deps.root.classList.toggle("dk-has-term", terminal);
    deps.root.classList.toggle("dk-running", running);
    const toggle = q("[data-theme-inspector]");
    const open = next === "open" || artifact;
    if (toggle && toggle.getAttribute("aria-pressed") !== String(open)) toggle.setAttribute("aria-pressed", String(open));
    // New work while it is shut (or on a dismissed card) only puts a dot on the button.
    const todos = q("[data-live-todos]");
    const sig = [tasks && (deps.agentsRows || []).length, tasks && (deps.jobsRows || []).map((row) => `${row.command_id || row.id}:${row.state}`).join(","),
      plan && todos.textContent.length, diff && seen.diff].join("|");
    if (thread() !== threadKey) {
      threadKey = thread();
      baseSig = sig;
    }
    if (open && closed.size === 0) baseSig = sig;
    toggle?.classList.toggle("has-notes", sig !== baseSig);
  }

  return { syncChrome };
}

// Desktop's chrome: the top bar (back/forward, the Chats | Code switch, the
// breadcrumb's title menu, the ⋯ menu), the sidebar's Code, Customize and
// light/dark switches, the composer's chips row, and the inspector: a right
// column of cards (Preview is the artifact pane; Diff, Terminal, Tasks and
// Plan are drawn here from the runtime's live state). The inspector opens by
// itself on wide screens while agents, commands or todos are live, and the
// panel button pins it open or shut; under 1040px it is an overlay, on phones
// a full-screen sheet.
const WIDE = "(min-width: 1040px)";
const EDIT_TOOLS = new Set(["search_replace", "write_file", "edit_file", "apply_patch"]);

export function create(deps) {
  let userOpen = null; // null follows the work; true/false is the user's choice
  let bound = false;
  let lastAgentView = false;
  let pickedJob = "";
  const closed = new Set(); // cards the user dismissed with ×, until new content
  const seen = { diff: "", terminal: "" };
  const q = (sel) => deps.root.querySelector(sel);
  const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };
  const show = (el, on) => { if (el && el.hidden === on) el.hidden = !on; };
  const esc = (value) => deps.escapeHtml(String(value ?? ""));

  function act(name) {
    window.FuseChatActions?.run?.(name);
  }

  function toggleMenu(open) {
    const menu = q("[data-theme-menu]");
    const btn = q("[data-theme-more]");
    if (!menu || !btn) return;
    const next = open ?? menu.hidden;
    menu.hidden = !next;
    btn.setAttribute("aria-expanded", String(next));
    if (next) menu.querySelector("button, a")?.focus();
  }

  // The breadcrumb title opens the chat's row menu (rename, pin, fork, archive).
  function titleMenu(btn) {
    const row = q('[data-history-list] [data-history-item] [data-row-open][aria-current="true"]')?.closest("[data-history-item]");
    if (!row) return;
    const box = btn.getBoundingClientRect();
    row.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left, clientY: box.bottom + 4 }));
  }

  function bind() {
    if (bound) return;
    bound = true;
    deps.root.addEventListener("click", (event) => {
      const t = event.target.closest?.("button, a");
      if (!t || !deps.root.contains(t)) return;
      if (t.matches("[data-theme-inspector]")) {
        userOpen = deps.root.dataset.themeRailState !== "open";
        syncChrome();
      } else if (t.matches("[data-theme-inspector-close]")) {
        userOpen = false;
        syncChrome();
      } else if (t.matches("[data-theme-terminal]")) {
        closed.delete("terminal");
        userOpen = true;
        syncChrome();
        q('[data-theme-card="terminal"]:not([hidden])')?.scrollIntoView({ block: "nearest" });
      } else if (t.matches("[data-theme-card-close]")) {
        closed.add(t.dataset.themeCardClose);
        syncChrome();
      } else if (t.matches("[data-theme-nav]")) {
        if (t.dataset.themeNav === "back") history.back(); else history.forward();
      } else if (t.matches("[data-theme-seg]")) {
        q(`[data-history-tab="${t.dataset.themeSeg}"]`)?.click();
        syncChrome();
      } else if (t.matches("[data-theme-title-menu]")) {
        titleMenu(t);
      } else if (t.matches("[data-theme-new]")) {
        q("[data-chat-new]")?.click();
      } else if (t.matches("[data-theme-project], [data-theme-code]")) {
        act("project");
      } else if (t.matches("[data-theme-attach]")) {
        q("[data-chat-attach]")?.click();
      } else if (t.matches("[data-theme-customize]")) {
        toggleMenu(false);
        deps.openWallpaperPicker?.();
      } else if (t.matches("[data-theme-scheme]")) {
        toggleMenu(false);
        const appearance = window.FuseChatAppearance;
        appearance?.setScheme?.(appearance.scheme === "light" ? "dark" : "light");
        syncScheme();
      } else if (t.matches("[data-theme-more]")) {
        toggleMenu();
      } else if (t.matches("[data-show-job]")) {
        pickedJob = t.dataset.showJob;
        closed.delete("terminal");
        syncChrome();
      } else if (t.matches("[data-theme-turn-act]")) {
        turnAction(t);
      } else if (t.matches("[data-theme-expand]")) {
        const turn = t.closest(".chat-turn");
        const open = !turn?.classList.contains("is-expanded");
        turn?.classList.toggle("is-expanded", open);
        t.textContent = open ? "Show less" : "Show more";
        t.setAttribute("aria-expanded", String(open));
      }
    });
    document.addEventListener("click", (event) => {
      const menu = q("[data-theme-menu]");
      if (menu && !menu.hidden && !event.target.closest?.(".dk-menu-wrap")) toggleMenu(false);
      // Narrow: the inspector overlays the chat; a click outside closes it.
      if (!window.matchMedia(WIDE).matches && deps.root.dataset.themeRailState === "open"
        && !event.target.closest?.("[data-theme-rail], [data-theme-inspector], [data-theme-terminal], [data-show-job], .chat-pop, dialog")) {
        userOpen = false;
        syncChrome();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const menu = q("[data-theme-menu]");
      if (menu && !menu.hidden) {
        toggleMenu(false);
        q("[data-theme-more]")?.focus();
      } else if (!window.matchMedia(WIDE).matches && deps.root.dataset.themeRailState === "open" && !deps.root.classList.contains("is-artifact")) {
        userOpen = false;
        syncChrome();
      }
    });
    window.matchMedia(WIDE).addEventListener?.("change", () => { userOpen = null; syncChrome(); });
    document.addEventListener("fuse-appearance", syncScheme);
    // Rows, lists and the todo tray change outside the run clock.
    const watch = new MutationObserver(() => syncChrome());
    const rail = q("[data-theme-rail]");
    if (rail) watch.observe(rail, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    watch.observe(deps.root, { attributes: true, attributeFilter: ["class"] });
    syncScheme();
  }

  function syncScheme() {
    const light = window.FuseChatAppearance?.scheme === "light";
    deps.root.querySelectorAll("[data-theme-scheme]").forEach((btn) => {
      const label = light ? "Dark appearance" : "Light appearance";
      if (btn.classList.contains("dk-ib")) {
        btn.setAttribute("aria-label", label);
        btn.title = label;
      } else setText(btn, label);
      btn.setAttribute("aria-pressed", String(light));
    });
  }

  // Reply actions under a finished turn: copy, fork, pin, read aloud.
  async function turnAction(btn) {
    const kind = btn.dataset.themeTurnAct;
    const turn = btn.closest(".chat-turn");
    const body = turn?.querySelector(".chat-md, .chat-assistant-body, .chat-reply") || turn;
    const text = (body?.innerText || "").trim();
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
      if (speechSynthesis.speaking) speechSynthesis.cancel();
      else speechSynthesis.speak(new SpeechSynthesisUtterance(text));
    }
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

  // Long runs of unchanged rows fold to "N unmodified lines".
  function foldUnchanged(html) {
    const box = document.createElement("div");
    box.innerHTML = html;
    const rows = [...box.querySelectorAll(".chat-console-nline")];
    let run = [];
    const flush = () => {
      if (run.length > 6) {
        const hidden = run.slice(2, -2);
        const note = document.createElement("div");
        note.className = "dk-diff-fold";
        note.textContent = `${hidden.length} unmodified lines`;
        hidden[0].before(note);
        hidden.forEach((row) => row.remove());
      }
      run = [];
    };
    rows.forEach((row) => { if (row.classList.contains("is-ctx")) run.push(row); else flush(); });
    flush();
    return box.innerHTML;
  }

  function syncDiff() {
    const card = q('[data-theme-card="diff"]');
    if (!card) return false;
    const edit = latestEdit();
    const key = edit ? `${edit.trace.id}:${edit.diff.length}` : "";
    if (key !== seen.diff) {
      seen.diff = key;
      closed.delete("diff");
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

  function syncTerminal() {
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
    const on = Boolean(job) && !closed.has("terminal");
    show(card, on);
    return on;
  }

  function syncTasks() {
    const strip = q("[data-work-strip]");
    const live = !strip?.hidden;
    const agents = (deps.agentsRows || []).length;
    const jobs = (deps.jobsRows || []).length;
    const card = q('[data-theme-card="tasks"]');
    setText(q("[data-theme-tasks-count]"), agents + jobs ? String(agents + jobs) : "");
    show(q("[data-theme-tasks-empty]"), !live);
    show(card, !closed.has("tasks"));
    return live;
  }

  function syncPlan() {
    const todos = q("[data-live-todos]");
    const live = Boolean(todos && !todos.hidden);
    const items = todos ? todos.querySelectorAll("li, [data-todo-id], .chat-todo-row").length : 0;
    setText(q("[data-theme-plan-count]"), items ? String(items) : "");
    show(q("[data-theme-plan-empty]"), !live);
    show(q('[data-theme-card="plan"]'), !closed.has("plan"));
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
    const tab = q('[data-history-tab="projects"]')?.getAttribute("aria-selected") === "true" ? "projects" : "chats";
    deps.root.querySelectorAll("[data-theme-seg]").forEach((btn) => {
      const on = btn.dataset.themeSeg === tab;
      if (btn.getAttribute("aria-pressed") !== String(on)) btn.setAttribute("aria-pressed", String(on));
    });
  }

  function syncChrome() {
    if (!deps.root) return;
    bind();
    const agentView = deps.root.classList.contains("is-agent-view");
    if (agentView !== lastAgentView) userOpen = null;
    lastAgentView = agentView;
    const diff = syncDiff();
    const terminal = syncTerminal();
    const tasks = syncTasks();
    const plan = syncPlan();
    syncChips();
    const wide = window.matchMedia(WIDE).matches;
    const running = Boolean(deps.streaming || deps.runIsActive(deps.activeRun));
    // Inside a subagent the agent tree is the way back to main: wide, the
    // inspector opens; narrow, the Tasks card sits inline under the top bar.
    let next = "closed";
    if (userOpen !== null) next = userOpen ? "open" : "closed";
    else if (agentView && tasks) next = wide ? "open" : "inline";
    else if (wide && (tasks || plan)) next = "open";
    if (deps.root.dataset.themeRailState !== next) deps.root.dataset.themeRailState = next;
    const inline = next === "inline" ? q("[data-theme-rail]")?.offsetHeight || 0 : 0;
    const inlineH = inline ? `${inline}px` : "";
    if (deps.root.style.getPropertyValue("--dk-inline-h") !== inlineH) {
      if (inlineH) deps.root.style.setProperty("--dk-inline-h", inlineH);
      else deps.root.style.removeProperty("--dk-inline-h");
    }
    deps.root.classList.toggle("dk-has-diff", diff);
    deps.root.classList.toggle("dk-has-term", terminal);
    deps.root.classList.toggle("dk-running", running);
    const toggle = q("[data-theme-inspector]");
    const open = next === "open" || deps.root.classList.contains("is-artifact");
    if (toggle && toggle.getAttribute("aria-pressed") !== String(open)) toggle.setAttribute("aria-pressed", String(open));
  }

  return { syncChrome };
}

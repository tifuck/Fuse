// The pane beside the transcript (a sheet on a phone): a call's output, the
// files the chat changed, and its agents and background commands.
// The output view is the open call's own .chat-console-full, fixed over the
// pane by CSS (styles/tail.css), so the runtime keeps patching a live call as
// in every theme; this module only decides which call is open and which view
// shows. Dependencies are live getters supplied by index.js.
const CHANGE_TOOLS = new Set(["search_replace", "write_file"]);

export function create(deps) {
  const root = document.querySelector("[data-chat]");
  const pane = root?.querySelector("[data-tail-pane]");
  if (!pane) return {};
  const esc = (value) => deps.escapeHtml(value);
  const log = root.querySelector("[data-chat-log]");
  const toggle = root.querySelector("[data-tail-pane-toggle]");
  const badge = root.querySelector("[data-tail-pane-badge]");
  const changesEl = pane.querySelector("[data-tail-changes]");
  const countEl = (name) => pane.querySelector(`[data-tail-count="${name}"]`);
  const shown = () => root.dataset.tailPane || "";
  let last = "changes";
  // Opened by a call: the pane closes when that call does.
  let byCall = false;

  // A call inside another call's output opens in place, not in the pane.
  const paneCalls = () => [...root.querySelectorAll("li.chat-step.is-console-call.is-console-expanded")]
    .filter((li) => !li.parentElement.closest(".chat-console-full"));

  function closeCall(li) {
    li.classList.remove("is-console-expanded");
    li.querySelector(":scope > .chat-step-main .chat-console-hang")?.classList.remove("is-expanded");
    li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", "false"));
    const owner = li.closest("article")?._message;
    if (!owner) return;
    (owner.stepOpen ||= {})[li.dataset.step] = false;
    deps.saveDisclosures(owner);
  }

  // Every file the chat's finished edits and writes touched, newest first,
  // with the lines they added and removed and the call to open for it.
  function changedFiles() {
    const files = new Map();
    for (const msg of deps.messages || []) {
      if (!msg || msg.role !== "assistant") continue;
      (msg.tools || []).forEach((trace, index) => {
        if (!trace || !CHANGE_TOOLS.has(trace.name) || !trace.path) return;
        const state = deps.toolPresentation(trace, false);
        if (state.failed || state.interrupted || state.running) return;
        const counts = deps.consoleEditCounts(trace);
        const file = files.get(trace.path) || { path: String(trace.path), added: 0, removed: 0, edits: 0 };
        file.added += counts.added;
        file.removed += counts.removed;
        file.edits += 1;
        file.step = deps.toolKey(trace, index);
        file.label = deps.compactPath(trace.path, trace, 64);
        files.delete(trace.path);
        files.set(trace.path, file);
      });
    }
    return [...files.values()].reverse();
  }

  function changeRowHtml(file) {
    const cut = file.label.lastIndexOf("/") + 1;
    const stat = file.added || file.removed
      ? `<span class="tail-plus">+${file.added}</span>${file.removed ? ` <span class="tail-minus">−${file.removed}</span>` : ""}` : "written";
    return `<button type="button" class="tail-change" data-tail-change="${esc(file.step)}" title="${esc(file.path)}">` +
      `<span class="tail-change-path"><i>${esc(file.label.slice(0, cut))}</i>${esc(file.label.slice(cut))}</span>` +
      (file.edits > 1 ? `<span class="tail-change-edits">${file.edits} edits</span>` : "") +
      `<span class="tail-change-leader" aria-hidden="true"></span><span class="tail-change-stat">${stat}</span></button>`;
  }

  function renderChanges() {
    const files = changedFiles();
    const count = countEl("changes");
    if (count) count.textContent = files.length ? String(files.length) : "";
    const html = files.length ? files.map(changeRowHtml).join("") : '<p class="tail-pane-empty">No files changed in this chat yet.</p>';
    if (changesEl && changesEl._html !== html) changesEl.innerHTML = changesEl._html = html;
  }

  // The tab counts every agent and background command; the top bar's badge
  // only what is still running, so it reads while the pane is closed.
  function syncAgents() {
    const all = root.querySelectorAll('[data-agents-list] [data-agent-row]:not([data-agent-row="main"]), [data-jobs-list] [data-job-row]').length;
    const running = root.querySelectorAll("[data-agents-list] :is([data-agent-stop], [data-agent-force]), [data-jobs-list] .chat-jobs-row.is-running").length;
    const count = countEl("agents");
    if (count && count.textContent !== (all ? String(all) : "")) count.textContent = all ? String(all) : "";
    if (!badge) return;
    if (badge.hidden !== !running) badge.hidden = !running;
    if (badge.textContent !== String(running || "")) badge.textContent = String(running || "");
  }

  function show(view, fromCall = false) {
    byCall = shown() ? byCall && fromCall : fromCall;
    root.dataset.tailPane = last = view;
    pane.querySelectorAll("[data-tail-tab]").forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.tailTab === view)));
    toggle?.setAttribute("aria-expanded", "true");
    renderChanges();
    syncAgents();
  }

  function hide() {
    if (!shown()) return;
    delete root.dataset.tailPane;
    byCall = false;
    toggle?.setAttribute("aria-expanded", "false");
    paneCalls().forEach(closeCall);
  }

  // Opens the call behind a changed file: its burst, then the call itself.
  function showStep(step) {
    const li = log?.querySelector(`li.chat-step[data-step="${CSS.escape(step)}"]`);
    if (!li) return;
    const burst = li.closest("[data-activity]");
    if (burst && !burst.classList.contains("is-open")) burst.querySelector("button[data-activity-summary]")?.click();
    if (!li.classList.contains("is-console-expanded")) li.querySelector(":scope > .chat-step-main > .chat-step-head [data-step-toggle]")?.click();
    li.scrollIntoView({ block: "center" });
  }

  // A phone's top bar folds Appearance, Context, Skills and Alerts into one menu.
  const more = root.querySelector("[data-tail-more]");
  const setMore = (open) => {
    if (!more || (more.getAttribute("aria-expanded") === "true") === open) return;
    more.setAttribute("aria-expanded", String(open));
  };

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    setMore(Boolean(target.closest("[data-tail-more]")) && more.getAttribute("aria-expanded") !== "true");
    if (target.closest("[data-tail-pane-toggle]")) { if (shown()) hide(); else show(last); return; }
    if (target.closest("[data-tail-pane-close]")) { hide(); return; }
    const tab = target.closest("[data-tail-tab]");
    if (tab) { show(tab.dataset.tailTab); return; }
    const change = target.closest("[data-tail-change]");
    if (change) { showStep(change.dataset.tailChange); return; }
    const li = target.closest("li.chat-step.is-console-call");
    if (!li || !root.contains(li) || li.parentElement.closest(".chat-console-full")) return;
    if (li.classList.contains("is-console-expanded")) {
      paneCalls().forEach((other) => { if (other !== li) closeCall(other); });
      if (shown() !== "output") show("output", !shown());
    } else if (shown() === "output" && byCall && !paneCalls().length) hide();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (more?.getAttribute("aria-expanded") === "true") { setMore(false); more.focus(); return; }
    if (!shown()) return;
    // A menu or dialog on screen takes Escape first (their shells stay in the page, unsized, while closed).
    const layers = document.querySelectorAll('[role="menu"]:not([hidden]), [role="listbox"]:not([hidden]), [role="dialog"]:not([hidden]), dialog[open]');
    if ([...layers].some((layer) => layer.getBoundingClientRect().width > 0)) return;
    hide();
  });

  // What the page shows changes under the pane (a reply streams, another chat
  // opens, a reload restores an open call): one pass a frame keeps it true.
  let queued = false;
  function sync() {
    queued = false;
    const open = paneCalls().length;
    if (!shown() && open) show("output", true);
    else if (shown() === "output" && byCall && !open) hide();
    if (shown()) renderChanges();
    syncAgents();
  }
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(sync); } };
  if (log) new MutationObserver(later).observe(log, { childList: true, subtree: true });
  const strip = root.querySelector("[data-work-strip]");
  if (strip) new MutationObserver(later).observe(strip, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "hidden"] });

  // Reading a subagent's transcript: its list is how to get back to main.
  let inAgent = root.classList.contains("is-agent-view");
  new MutationObserver(() => {
    const now = root.classList.contains("is-agent-view");
    if (now && !inAgent) show("agents");
    inAgent = now;
  }).observe(root, { attributes: true, attributeFilter: ["class"] });

  // Where the chat works, for the status bar. The runtime updates the project
  // chip whenever the chat's project changes; that is the cue to read it.
  const chip = root.querySelector("[data-project-chip]");
  const where = root.querySelector("[data-tail-where]");
  function syncWhere() {
    const bound = chip.hidden ? null : deps.sessionProject();
    const host = String(bound?.host_alias || "");
    const path = String(bound?.remote_path || "");
    where.querySelector("[data-tail-host]").textContent = host;
    where.querySelector("[data-tail-path]").textContent = path;
    where.hidden = !host && !path;
  }
  if (chip && where) {
    new MutationObserver(syncWhere).observe(chip, { attributes: true, attributeFilter: ["title", "hidden"] });
    syncWhere();
  }

  later();
  return {};
}

// Combined's chrome: the header status pill and the rail. The pill says what
// the run is doing, for how long and how many agents it has; it toggles the
// rail. The rail opens by itself on wide screens while something runs or
// todos are live, and is a dropdown under the header on narrow ones.
const WIDE = "(min-width: 1040px)";

export function create(deps) {
  let userRail = null; // null follows the run; true/false is the user's choice
  let bound = false;
  let lastBusy = false;
  const q = (sel) => deps.root.querySelector(sel);
  const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };

  function busy() {
    const run = Boolean(deps.streaming || deps.runIsActive(deps.activeRun));
    const agents = deps.agentsRows.some((row) => deps.agentLive(row.status));
    return { run, agents, jobs: deps.jobsRows.length > 0, todos: !q("[data-live-todos]")?.hidden };
  }

  function bind() {
    if (bound) return;
    bound = true;
    q("[data-theme-pill]")?.addEventListener("click", () => {
      userRail = deps.root.dataset.themeRailState !== "open";
      syncChrome();
    });
    // Narrow: the rail is a dropdown; a click elsewhere or Esc closes it.
    document.addEventListener("click", (event) => {
      if (window.matchMedia(WIDE).matches || deps.root.dataset.themeRailState !== "open") return;
      if (event.target.closest?.("[data-theme-rail], [data-theme-pill]")) return;
      userRail = false;
      syncChrome();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || window.matchMedia(WIDE).matches || deps.root.dataset.themeRailState !== "open") return;
      userRail = false;
      syncChrome();
    });
    window.matchMedia(WIDE).addEventListener?.("change", () => { userRail = null; syncChrome(); });
    // Rows, lists and the todo tray change outside the run clock.
    const watch = new MutationObserver(() => syncChrome());
    const rail = q("[data-theme-rail]");
    if (rail) watch.observe(rail, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
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
    pill.setAttribute("aria-pressed", deps.root.dataset.themeRailState === "open" ? "true" : "false");
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

  function syncChrome() {
    if (!deps.root) return;
    bind();
    const state = busy();
    const any = state.run || state.agents || state.jobs;
    // A run that starts again hands the rail back to the run.
    if (any && !lastBusy && userRail === false && window.matchMedia(WIDE).matches) userRail = null;
    lastBusy = any;
    const wide = window.matchMedia(WIDE).matches;
    const open = userRail !== null ? userRail : wide && (state.agents || state.jobs || state.todos || (state.run && !q("[data-work-strip]")?.hidden));
    const next = open ? "open" : "closed";
    if (deps.root.dataset.themeRailState !== next) deps.root.dataset.themeRailState = next;
    syncPill(state);
    syncContext();
  }

  return { syncChrome };
}

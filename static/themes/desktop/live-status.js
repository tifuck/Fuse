// live-status presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function formatActivityClock(secondsF) {
    const total = Math.max(1, Math.round(secondsF));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${m}m ${s}s`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

function formatActivityElapsed(secondsF, live) {
    if (secondsF == null || (live && secondsF < 1)) return "";
    return ` for ${formatActivityClock(secondsF)}`;
  }

// The thought's newest heading ("**Checking the FK delete rules**" or a
// markdown heading line), which says what the model is working out.
function thoughtHeading(text) {
    const lines = String(text || "").split("\n");
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const m = /^\s*(?:#{1,6}\s+(.+?)\s*#*|\*\*(.+?)\*\*:?)\s*$/.exec(lines[i]);
      const heading = m ? deps.ccFlat(m[1] || m[2]).replace(/[\s.:…]+$/, "") : "";
      if (heading) return heading;
    }
    return "";
  }

function ccThinkingState(msg, phase) {
    if (phase !== "thinking") return "";
    // The newest thought: its run of reasoning segments.
    const segs = Array.isArray(msg.segments) ? msg.segments : [];
    let end = segs.length - 1;
    while (end >= 0 && segs[end]?.type !== "reasoning") end -= 1;
    let text = "";
    for (let k = end; k >= 0 && segs[k]?.type === "reasoning"; k -= 1) text = `${segs[k].text || ""}\n${text}`;
    return thoughtHeading(segs.length ? text : msg.reasoning) || "thinking";
  }

// Between calls (the model reading a result) the line keeps the newest
// action, in the past tense: "Ran the test suite twice", "Read app/main.py".
const DONE_WORDS = { Reading: "Read", Viewing: "Viewed", Searching: "Searched", Finding: "Found", Listing: "Listed", Running: "Ran", Editing: "Edited", Writing: "Wrote" };
function lastActionText(msg) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    const last = traces[traces.length - 1];
    if (!last) return "";
    const description = last.name === "run_terminal_command" ? deps.ccFlat(last.description) : "";
    if (description) return deps.ccRunningForm(description) ? deps.ccInflect(description).done : description;
    return actionText(last).replace(/^\w+/, (word) => DONE_WORDS[word] || word);
  }

// A call by what it acts on: "Reading app/main.py", "Editing static/chat.js".
function actionText(trace) {
    const live = deps.ccLiveText(trace);
    if (live) return live;
    const path = (trace.name === "search_replace" || trace.name === "write_file") ? deps.ccPathHint(trace) : "";
    return path ? `${trace.name === "write_file" ? "Writing" : "Editing"} ${path}` : "";
  }

function ccSpinnerState(status, msg) {
    let state = "";
    if (status.phase === "queued") state = "queued";
    else if (status.phase === "stopping") state = "stopping";
    else if (status.current) state = deps.executionState(status.current) === "cancelling" ? "cancelling"
      : deps.isSearchTool(status.current.name) ? "searching the web" : actionText(status.current) || CC_SPINNER_STATES[status.current.name] || "running tool";
    else if (status.phase === "calling") state = !status.tool ? "calling tool"
      : deps.isSearchTool(status.tool) ? "searching the web" : CC_SPINNER_STATES[status.tool] || `calling ${status.tool}`;
    else if (status.phase === "waiting" || status.phase === "starting") state = lastActionText(msg);
    else if (status.phase === "writing") state = "writing the reply";
    return state;
  }

function formatConsoleLiveStatus(msg, groups, opts) {
    const status = deps.currentActivity(msg, groups, true);
    const elapsed = opts && opts.seconds != null ? opts.seconds : deps.liveElapsedSeconds(msg, true);
    const seconds = Math.max(0, Math.floor(elapsed || 0));
    // A running command is the line itself and nothing follows it: what it is
    // for ("Running the unit tests"), else the command in parentheses
    // ("Running command (npm test)"). Opening the line shows the command. It
    // joins the summary line above once it ends.
    const command = status.phase !== "thinking" && status.current?.name === "run_terminal_command" && !deps.toolBackground(status.current) ? status.current : null;
    const arg = command ? deps.ccFlat(deps.displayCommand(command)) : "";
    if (arg) {
      const description = deps.ccFlat(command.description);
      const doing = description ? deps.ccRunningForm(description) ?? description : "Running command";
      return { verb: doing.charAt(0).toUpperCase() + doing.slice(1) + (description ? "…" : ""), meta: null, arg: description ? "" : arg, command };
    }
    // Everything else names what is happening; only the clock follows it.
    const state = ccThinkingState(msg, status.phase) || ccSpinnerState(status, msg);
    const word = state ? state.charAt(0).toUpperCase() + state.slice(1) : "Working";
    const settled = (status.phase === "waiting" || status.phase === "starting") && state;
    const verb = settled || /(…|\.\.\.)$/.test(word) ? word : `${word}…`;
    const meta = seconds >= 1 ? { clock: deps.ccDuration(seconds * 1000) } : null;
    return { verb, meta, arg: "" };
  }

function syncActivityElapsed(article, msg, live) {
    const elapsedEls = article.querySelectorAll("[data-activity-elapsed]");
    if (!elapsedEls.length) return;
    const text = formatActivityElapsed(deps.liveElapsedSeconds(msg, live), live);
    elapsedEls.forEach((el) => {
      if (el.closest("[data-live-status]")) {
        if (el.textContent !== text) el.textContent = text;
        return;
      }
      const next = el.closest("[data-activity]")?.classList.contains("is-waiting") ? "" : text;
      if (el.textContent !== next) el.textContent = next;
    });
  }

function activitySpinSet() {
    return deps.currentRunSpin();
  }

function syncActivitySpin(article, msg, live) {
    const els = article.querySelectorAll("[data-activity-spin]");
    if (!els.length) return;
    if (!live) {
      els.forEach((el) => {
        el.replaceChildren();
        delete el.dataset.spinSet;
      });
      return;
    }
    const el = article.querySelector("[data-live-status] [data-activity-spin]") || [...els].find((node) => node.closest(".chat-activity.is-live, .chat-activity.is-waiting"));
    if (!el) return;
    deps.applySpinEl(el, activitySpinSet(msg), "live", true, msg._spinOrigin);
  }

const CC_SPINNER_STATES = {
    run_terminal_command: "running command", get_command_output: "waiting for command", kill_command: "stopping command",
    read_file: "reading", grep: "searching", glob: "searching", list_dir: "listing", search_replace: "editing",
    write_file: "writing file", read_url: "fetching", spawn_subagent: "running agent", get_subagent_output: "waiting for agent",
    kill_subagent: "stopping agent", list_subagents: "listing agents", monitor: "watching",
    create_artifact: "building artifact", update_artifact: "building artifact", edit_artifact: "editing artifact",
    read_artifact: "reading artifact", search_models: "finding models",
  };

  return { formatActivityClock, formatActivityElapsed, thoughtHeading, ccThinkingState, lastActionText, ccSpinnerState, formatConsoleLiveStatus, syncActivityElapsed, activitySpinSet, syncActivitySpin, CC_SPINNER_STATES };
}

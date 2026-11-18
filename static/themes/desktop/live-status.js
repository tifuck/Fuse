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

// A thought is one word, whatever it says: the line never streams it.
function ccThinkingState(msg, phase) {
    return phase === "thinking" ? "thinking" : "";
  }

// A call by what it acts on: "Reading app/main.py", "Editing static/chat.js".
function actionText(trace) {
    const live = deps.ccLiveText(trace);
    if (live) return live;
    const path = (trace.name === "search_replace" || trace.name === "write_file") ? deps.ccPathHint(trace) : "";
    return path ? `${trace.name === "write_file" ? "Writing" : "Editing"} ${path}` : "";
  }

function ccSpinnerState(status) {
    let state = "";
    if (status.phase === "queued") state = "queued";
    else if (status.phase === "stopping") state = "stopping";
    else if (status.current) state = deps.executionState(status.current) === "cancelling" ? "cancelling"
      : deps.isSearchTool(status.current.name) ? "searching the web" : actionText(status.current) || CC_SPINNER_STATES[status.current.name] || "running tool";
    else if (status.phase === "calling") state = !status.tool ? "calling tool"
      : deps.isSearchTool(status.tool) ? "searching the web" : CC_SPINNER_STATES[status.tool] || `calling ${status.tool}`;
    // Between calls (one ended, the next request is out, no token yet) the
    // model reads what came back: one word for the whole gap, the one a
    // thought keeps when it starts, so none of its steps flashes by.
    else if (status.phase === "working" || status.phase === "waiting" || status.phase === "starting") state = "thinking";
    else if (status.phase === "writing") state = "writing the reply";
    return state;
  }

function formatConsoleLiveStatus(msg, groups, opts) {
    const status = deps.currentActivity(msg, groups, true);
    const elapsed = opts && opts.seconds != null ? opts.seconds : deps.liveElapsedSeconds(msg, true);
    const seconds = Math.max(0, Math.floor(elapsed || 0));
    const line = heldLine(msg, liveLine(msg, status), status.phase === "stopping");
    // The line names what is happening; the turn's clock ends it, whatever is
    // running. A call's own time is in the live pane (live-dock.js).
    const meta = seconds >= 1 ? { clock: deps.ccDuration(seconds * 1000) } : null;
    return { ...line, meta };
  }

// The line also carries what it names, for the live pane (live-dock.js): the
// call (key: its id) and the phase.
function liveLine(msg, status) {
    const key = status.current ? String(status.current.id ?? "") : "";
    // A running command: what it is for ("Running the unit tests"), else the
    // command in parentheses ("Running command (npm test)"). The live pane
    // shows the command. It joins the summary line above once it ends.
    const command = status.phase !== "thinking" && status.current?.name === "run_terminal_command" && !deps.toolBackground(status.current) ? status.current : null;
    const arg = command ? deps.ccFlat(deps.displayCommand(command)) : "";
    if (arg) {
      const description = deps.ccFlat(command.description);
      const doing = description ? deps.ccRunningForm(description) ?? description : "Running command";
      return { verb: doing.charAt(0).toUpperCase() + doing.slice(1) + (description ? "…" : ""), arg: description ? "" : arg, key, phase: status.phase };
    }
    const state = ccThinkingState(msg, status.phase) || ccSpinnerState(status);
    const word = state ? state.charAt(0).toUpperCase() + state.slice(1) : "Working";
    return { verb: /(…|\.\.\.)$/.test(word) ? word : `${word}…`, arg: "", key, phase: status.phase };
  }

// A line stays up at least HOLD_MS, so a state that lasts a blink (a call
// ending, the next request going out) is skipped, never flashed. The newest
// line lands on a later sync (the live clock syncs every 250 ms). A stop is
// the user's own action and shows at once.
const HOLD_MS = 700;
function heldLine(msg, line, now) {
    const shown = msg._liveLine;
    const at = Date.now();
    if (shown && shown.verb === line.verb && shown.arg === line.arg) {
      shown.line = line;
      return line;
    }
    if (shown && !now && at - shown.at < HOLD_MS) return shown.line;
    msg._liveLine = { verb: line.verb, arg: line.arg, line, at };
    return line;
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
    read_artifact: "reading artifact", search_models: "finding models", run_code: "running code",
  };

  return { formatActivityClock, formatActivityElapsed, ccThinkingState, ccSpinnerState, formatConsoleLiveStatus, syncActivityElapsed, activitySpinSet, syncActivitySpin, CC_SPINNER_STATES };
}

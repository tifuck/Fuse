// activity-groups presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
// A finished reply's line: what it took, then when it ended, then its
// actions: "done 3m 53s 4m ago". usage: the stored-reply stand-in for
// the work time.
function ccTurnLineHtml(msg, latest, usage = "") {
    // One run of text, so it reads and copies as one line: "done 3m 53s 4m ago".
    const ago = agoHtml(msg);
    const lead = ago ? " " : "";
    // A stopped or failed turn never finished: its outcome leads
    // ("interrupted 26s"), the one tinted word.
    const outcome = msg.stopped ? "interrupted" : msg.error ? "failed" : "";
    if (msg.elapsedMs == null) return `<span class="dk-turn-text">${ago}${usage ? `<span class="dk-took">${lead}${usage}</span>` : ""}</span>${turnActionsHtml(msg, latest)}`;
    const pendingAgents = latest ? (deps.agentsRows || []).filter((row) => deps.agentLive(row.status)).length : 0;
    const took = deps.escapeHtml(deps.ccDuration(Math.max(0, Number(msg.elapsedMs) || 0)));
    const doing = pendingAgents ? `waiting for ${pendingAgents} background ${pendingAgents === 1 ? "agent" : "agents"}` : outcome ? `<em class="dk-outcome is-${outcome}">${outcome}</em> <b>${took}</b>` : `<b>${took}</b>`;
    const still = latest && !pendingAgents ? deps.ccStillRunningText() : "";
    // tok/s is live only (the status line while the model streams).
    return `<span class="dk-turn-text"><span class="dk-lead">${doing}</span>${lead}${ago}${still ? ` · ${deps.escapeHtml(still)} still running` : ""}</span>${turnActionsHtml(msg, latest)}`;
  }

// A finished reply's actions: copy, fork, pin, read aloud. Desktop's
// chrome.js runs the buttons.
// Fork copies the whole chat, so it sits only under the last reply; a
// subagent's replies (noActions) can be copied and read, not forked or pinned.
const TURN_ACTIONS = [
  ["copy", "Copy reply", '<rect x="5.2" y="5.2" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10.8 5V3.8a1.2 1.2 0 0 0-1.2-1.2H4a1.2 1.2 0 0 0-1.2 1.2v5.6A1.2 1.2 0 0 0 4 10.6h1.2" fill="none" stroke="currentColor" stroke-width="1.3"/>'],
  ["fork", "Fork this chat", '<circle cx="4.5" cy="3.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="11.5" cy="3.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="8" cy="12.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4.5 5v1.5a2 2 0 0 0 2 2h3a2 2 0 0 0 2-2V5M8 8.5V11" fill="none" stroke="currentColor" stroke-width="1.3"/>'],
  ["pin", "Pin this chat", '<path d="M6 2.5h4l-.6 4 2.3 2.3H4.3L6.6 6.5zM8 8.8V13.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"/>'],
  ["speak", "Read aloud", '<path d="M2.8 6.2h2.2l3-2.6v8.8l-3-2.6H2.8z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M10.5 5.6a3.2 3.2 0 0 1 0 4.8M12.3 3.9a5.6 5.6 0 0 1 0 8.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>'],
];
function agoText(at) {
    const when = new Date(typeof at === "number" ? at : String(at || ""));
    const s = (Date.now() - when.getTime()) / 1000;
    if (!Number.isFinite(s)) return "";
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
function turnActionsHtml(msg, latest) {
    const shown = TURN_ACTIONS.filter(([kind]) => kind === "copy" || (kind === "speak" ? "speechSynthesis" in window
      : !msg.noActions && (kind !== "fork" || latest)));
    const buttons = shown.map(([kind, label, icon]) =>
      `<button type="button" class="dk-act-btn" data-theme-turn-act="${kind}" aria-label="${label}" title="${label}"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">${icon}</svg></button>`).join("");
    return `<span class="dk-acts">${buttons}</span>`;
  }
// When the reply ended ("just now", "5m ago"); the full time in its title.
// A settled reply is not repainted, so its time moves on by itself.
let agoTimer = 0;
function syncAgo() {
    const els = document.querySelectorAll("time.dk-ago");
    if (!els.length) {
      clearInterval(agoTimer);
      agoTimer = 0;
    }
    els.forEach((el) => {
      const text = agoText(el.dateTime);
      if (text) FuseChatDOM.setText(el, text);
    });
  }
function agoHtml(msg) {
    const ago = agoText(msg.endedAt);
    if (!ago) return "";
    agoTimer ||= setInterval(syncAgo, 30_000);
    const when = new Date(msg.endedAt);
    return `<time class="dk-ago" datetime="${deps.escapeHtml(when.toISOString())}" title="${deps.escapeHtml(when.toLocaleString())}">${deps.escapeHtml(ago)}</time>`;
  }

function ccShellHint(trace) {
    const command = deps.displayCommand(trace);
    const nl = command.indexOf("\n");
    const first = (nl === -1 ? command : command.slice(0, nl)).trim();
    if (first.startsWith("#") && !first.startsWith("#!") &&
        !(nl !== -1 && command.slice(nl + 1).split("\n").some((line) => line.trim() && !line.trim().startsWith("#")))) {
      const note = first.replace(/^#+\s*/, "");
      if (note && !/[\x00-\x1f\x7f-\x9f]/.test(note)) return deps.ccClip(note, 300);
    }
    const lines = command.split("\n").map(deps.ccFlat).filter(Boolean);
    return lines.length ? deps.ccClip("$ " + lines.join("\n"), 300) : "";
  }

function ccPathHint(trace) {
    const path = String((trace && trace.path) || "").trim();
    return path ? deps.ccClip(deps.ccFlat(deps.shortToolPath(path, trace)), 300) : "";
  }

function ccActivityText(trace, cap = 50) {
    const name = trace.name;
    if (name === "run_terminal_command") {
      const command = deps.displayCommand(trace);
      return command ? deps.ccFlat(`Running ${deps.ccClip(deps.ccFlat(command), cap)}`) : "Running command";
    }
    if (name === "read_file") return trace.path ? `${deps.ccImageRead(trace) ? "Viewing" : "Reading"} ${ccPathHint(trace)}` : "Reading file";
    if (name === "grep") return trace.pattern ? deps.ccFlat(`Searching for ${deps.ccClip(trace.pattern, cap)}`) : "Searching";
    if (name === "glob") return trace.pattern ? deps.ccFlat(`Finding ${deps.ccClip(trace.pattern, cap)}`) : "Finding files";
    if (name === "list_dir") return trace.path ? `Listing ${ccPathHint(trace)}` : "Listing directory";
    return "";
  }

function ccRunning(trace) {
    return deps.toolPresentation(trace, true).running && !deps.toolBackground(trace);
  }

function ccSince(trace) {
    if (!trace._since) {
      const started = Number(trace.started_ms);
      trace._since = Number.isFinite(started) && started > 0
        ? started
        : Date.now() - Math.max(0, Number(trace.elapsed_ms) || 0);
    }
    return trace._since;
  }

function ccHeadline(items, msg) {
    const last = items[items.length - 1];
    if (!last || last.type !== "tool") return "";
    const activity = msg?.id === deps.activeRun?.message_id ? deps.activeRun?.activity : msg?._activity;
    const busy = items.some((item) => item.type === "tool" && ccRunning(item.entry.trace));
    if (activity?.phase ? activity.phase !== "tools" : !busy) return "";
    return ccLiveText(last.entry.trace);
  }

function ccLiveText(trace, cap) {
    if (!CC_LIVE_TOOLS.has(trace && trace.name)) return "";
    const description = deps.ccFlat(trace.description);
    const text = description ? deps.ccRunningForm(description) ?? description : ccActivityText(trace, cap);
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

function ccElapsedText(since) {
    const ms = Date.now() - since;
    return ms >= 2000 ? ` · ${deps.ccDuration(ms)}` : "";
  }

function ccShellSuffix(since, lines) {
    const seconds = Math.floor((Date.now() - since) / 1000);
    if (seconds < 2) return "";
    const took = deps.ccDuration(seconds * 1000);
    return lines > 0 ? ` (${took} · ${lines} ${lines === 1 ? "line" : "lines"})` : ` (${took})`;
  }

function ccThinkClock(base, since) {
    return deps.ccDuration(Math.max(1000, base + (since ? Date.now() - since : 0)));
  }

function syncConsoleClocks(root) {
    root.querySelectorAll("[data-cc-clock]").forEach((el) => {
      const since = Number(el.dataset.since) || 0;
      const kind = el.dataset.ccClock;
      const text = kind === "work" ? deps.ccDuration(Math.max(1000, (Number(el.dataset.base) || 0) + (since ? Date.now() - since : 0)))
        : kind === "suffix" ? ccShellSuffix(since, Number(el.dataset.lines) || 0)
        : ccElapsedText(since);
      FuseChatDOM.setText(el, text);
    });
  }

// The live clock asks workClocksLive, then syncWorkClocks, on each tick:
// one search of the page serves both.
let workClocks = null;
function liveWorkClocks() {
    if (!workClocks) {
      workClocks = document.querySelectorAll('[data-cc-clock="work"]');
      queueMicrotask(() => { workClocks = null; });
    }
    return workClocks;
  }

function workClocksLive() {
    return liveWorkClocks().length > 0;
  }

function syncWorkClocks() {
    liveWorkClocks().forEach((el) => {
      const base = Number(el.dataset.base) || 0;
      const since = Number(el.dataset.since) || 0;
      const text = deps.ccDuration(Math.max(1000, base + (since ? Date.now() - since : 0)));
      FuseChatDOM.setText(el, text);
    });
  }

// A count on a summary line rolls from its old value to the new one when it
// changes ("ran 2" -> "ran 3"), the old one fading up and out. The line is rebuilt on every render, so each
// count remembers when it last changed and a rebuilt line resumes the roll
// where it was (negative delay) instead of starting it over.
const ROLL_MS = 420;
const rolledCounts = new Map();
function rollingCountHtml(id, n) {
    const now = Date.now();
    const seen = rolledCounts.get(id);
    if (!seen || seen.n !== n) rolledCounts.set(id, { n, from: seen ? seen.n : null, at: now });
    const roll = rolledCounts.get(id);
    const age = now - roll.at;
    if (roll.from === null || age >= ROLL_MS) return `<b>${n}</b>`;
    return `<b class="dk-roll" data-from="${roll.from}" style="--dk-roll-at:-${age}ms"><span>${n}</span></b>`;
  }

function ccGroupHtml(group, live, openMap) {
    const key = "cgroup-" + group.items[0].key;
    const open = group.items.some((item) => deps.stepOpenAt(openMap, "cgroup-" + item.key));
    let search = 0;
    let readOps = 0;
    let list = 0;
    let bash = 0;
    let todos = 0;
    let gitOpBash = 0;
    let plus = 0;
    let minus = 0;
    let failedCalls = 0;
    let stoppedCalls = 0;
    let stillRunning = 0;
    let stillCommands = false;
    const readPaths = new Set();
    const imagePaths = new Set();
    const edited = new Set();
    const wrote = new Set();
    const commits = [];
    const pushes = [];
    const branches = [];
    const prs = [];
    const others = new Map();
    // The line counts calls only; thoughts sit among the rows, unnamed. A
    // script counts as the calls it made; one that made none, as a script.
    // Reads past the server's row cap (tool_runner._CHILD_READ_ROWS) draw no
    // row; the script's own call counts still name them.
    const counted = group.items.flatMap((item) => item.calls?.length
      ? item.calls.map((entry) => ({ type: "tool", entry, cls: deps.ccToolClass(entry.trace) })) : [item]);
    group.items.forEach((item) => {
      if (!item.calls?.length) return;
      const ran = deps.codeRunParts(item.entry.trace).calls.find(([name]) => name === "read_file")?.[1] || 0;
      readOps += Math.max(0, ran - item.calls.filter((entry) => entry.trace.name === "read_file").length);
      // A script that failed or was cut short counts as such, unless one of
      // its calls already says so (a stopped script stops its running call).
      const own = deps.toolPresentation(item.entry.trace, live);
      if (item.calls.some((entry) => { const p = deps.toolPresentation(entry.trace, live); return p.failed || p.interrupted; })) return;
      if (own.failed) failedCalls += 1;
      else if (own.interrupted) stoppedCalls += 1;
    });
    counted.forEach((item) => {
      if (item.type !== "tool") return;
      const trace = item.entry.trace;
      const presentation = deps.toolPresentation(trace, live);
      // A background command still running is not "ran" yet: it is counted
      // apart, "(1 still running)", and joins the count when it ends.
      if (presentation.running) {
        stillRunning += 1;
        if (trace.name !== "monitor") stillCommands = true;
        return;
      }
      const cls = item.cls;
      const bad = presentation.failed || (trace.name === "run_terminal_command" && presentation.state === "warning" &&
        deps.ccExitMeaning(deps.displayCommand(trace), deps.shellExitCode(trace)).isError);
      if (bad) failedCalls += 1;
      else if (presentation.interrupted) stoppedCalls += 1;
      if (!cls) {
        const kind = deps.isSearchTool(trace.name) ? "web" : OTHER_KIND[trace.name] || "tool";
        others.set(kind, (others.get(kind) || 0) + 1);
        return;
      }
      if (cls.kind === "silent") return;
      if (cls.kind === "todo") {
        todos += 1;
        return;
      }
      if (cls.kind === "edit" || cls.kind === "write") {
        (cls.kind === "edit" ? edited : wrote).add(String(trace.path || trace.id));
        const counts = deps.consoleEditCounts(trace);
        plus += counts.added;
        minus += counts.removed;
        return;
      }
      if (cls.kind === "bash") {
        bash += 1;
        const ops = !presentation.running && typeof trace.output === "string" ? deps.ccGitOps(deps.displayCommand(trace), trace.output) : null;
        if (ops) {
          gitOpBash += 1;
          if (ops.commit) commits.push(ops.commit);
          if (ops.push) pushes.push(ops.push);
          if (ops.branch) branches.push(ops.branch);
          if (ops.pr) prs.push(ops.pr);
        }
      } else if (cls.kind === "list") {
        list += 1;
      } else if (cls.kind === "search") {
        search += 1;
      } else if (cls.image) {
        imagePaths.add(String(trace.path));
      } else if (!cls.shell && trace.path) {
        readPaths.add(String(trace.path));
      } else {
        readOps += 1;
      }
    });
    // Path reads count distinct files (two reads of one path are one file);
    // shell reads (cat, head, …) have no path to dedupe and count their calls.
    // Both sides always count: a path read plus a shell read is two files.
    const reads = readPaths.size + readOps;
    const images = imagePaths.size;
    const shells = Math.max(0, bash - gitOpBash);
    if (!search && !reads && !images && !list && !shells && !gitOpBash && !todos && !edited.size && !wrote.size && !others.size && !stillRunning) return "";

    const parts = [];
    const add = (verb, rest) => {
      const word = parts.length ? verb : verb.charAt(0).toUpperCase() + verb.slice(1);
      parts.push(rest ? `${deps.escapeHtml(word)} ${rest}` : deps.escapeHtml(word));
    };
    const count = (part, n) => rollingCountHtml(`${key}|${part}`, n);
    const bold = (text) => `<b>${deps.escapeHtml(text)}</b>`;
    for (const [kind, verb] of [["committed", "committed"], ["amended", "amended commit"], ["cherry-picked", "cherry-picked"]]) {
      const shas = commits.filter((commit) => commit.kind === kind).map((commit) => commit.sha);
      if (shas.length) add(verb, bold(shas.join(", ")));
    }
    if (pushes.length) add("pushed to", bold([...new Set(pushes)].join(", ")));
    branches.forEach((branch) => add(branch.action === "merged" ? "merged" : "rebased onto", bold(branch.ref)));
    prs.forEach((pr) => add(CC_PR_VERBS[pr.action] || pr.action, bold(pr.url && pr.url.includes("/-/merge_requests/") ? `MR !${pr.number}` : `PR #${pr.number}`)));
    if (search) add("searched for", `${count("search", search)} ${search === 1 ? "pattern" : "patterns"}`);
    if (reads) add("read", `${count("read", reads)} ${reads === 1 ? "file" : "files"}`);
    if (images) add("viewed", `${count("image", images)} ${images === 1 ? "image" : "images"}`);
    if (list) add("listed", `${count("list", list)} ${list === 1 ? "directory" : "directories"}`);
    if (shells) add("ran", `${count("bash", shells)} shell ${shells === 1 ? "command" : "commands"}`);
    if (edited.size) add("edited", `${count("edit", edited.size)} ${edited.size === 1 ? "file" : "files"}`);
    if (wrote.size) add("wrote", `${count("write", wrote.size)} ${wrote.size === 1 ? "file" : "files"}`);
    if (todos) add("updated", todos === 1 ? "todos" : `todos ${count("todo", todos)} times`);
    for (const [kind, n] of others) {
      const [past, , one, many] = OTHER_WORDS[kind];
      add(past, `${count(kind, n)} ${n === 1 ? one : many}`);
    }

    // Desktop: the line never names the newest call; it counts them.
    const stat = (plus ? ` <span class="dk-plus">+${plus}</span>` : "") + (minus ? ` <span class="dk-minus">−${minus}</span>` : "");
    const noun = stillCommands ? (stillRunning === 1 ? "command" : "commands") : (stillRunning === 1 ? "monitor" : "monitors");
    // The folded line counts its steps; the sentence is its tooltip.
    const sentence = !parts.length && stillRunning ? `${stillRunning} ${noun} still running` : parts.join(", ");
    const steps = counted.filter((item) => item.type === "tool").length;
    const label = parts.length ? `${deps.ROW_CHEV}<span class="dk-steps">${count("steps", steps)} ${steps === 1 ? "step" : "steps"}</span>` : sentence;
    // The diff counts end the line even when its text is cut short.
    const diffstat = stat && parts.length ? `<span class="dk-diffstat">${stat.trim()}</span>` : "";
    // Ledger: the line truncates, its tallies stay pinned at the end.
    // A failure or a stop is never folded away: it is tallied on the line.
    const tallies = [
      stillRunning && parts.length ? `<span class="dk-tally is-running">${stillRunning} still running</span>` : "",
      failedCalls ? `<span class="dk-tally is-failed">${failedCalls} failed</span>` : "",
      stoppedCalls ? `<span class="dk-tally is-stopped">${stoppedCalls} stopped</span>` : "",
    ].join("");
    const title = (sentence + stat + (stillRunning && parts.length ? ` (${stillRunning} still running)` : "") + (failedCalls ? ` (${failedCalls} failed)` : "") + (stoppedCalls ? ` (${stoppedCalls} stopped)` : "")).replace(/<[^>]*>/g, "");
    // One call: the line stays its opened list, header hidden, so the call
    // reads as its own row; the same element folds to the summary when a
    // second call joins.
    const single = counted.filter((item) => item.type === "tool").length === 1 && !stillRunning;
    const rows = group.items.map((item) => item.type === "think"
      ? thoughtRowHtml(item.key, item.text, item.ms, item.segmentIndex, deps.stepOpenAt(openMap, item.key))
      : item.calls ? scriptRowHtml(item, live, openMap)
      : deps.toolStepHtml(item.entry.trace, item.key, live, deps.stepOpenAt(openMap, item.key), openMap, { oneLine: true })).join("");
    return (
      `<li class="chat-step is-console-group is-done${single ? " is-console-expanded is-dk-single" : open ? " is-console-expanded" : ""}" data-step="${deps.escapeHtml(key)}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">` +
      `<div class="chat-step-head">` +
      `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}" title="${title}"><span class="chat-step-label">${label}</span>${diffstat}${tallies ? `<span class="dk-tallies">${tallies}</span>` : ""}</button>` + `</div>` +
      // A failed or interrupted member says so on its own row once the line
      // is opened; the folded line is only its summary.
      `<div class="chat-console-bundle"><ol class="chat-timeline">${rows}</ol></div>` +
      `</div></li>`
    );
  }

// Desktop: all the work between two stretches of reply text is one grey
// summary line ("Ran 2 shell commands, read 1 file ›") that counts the calls,
// the newest included; it opens to one line per call, thoughts among them.
// Only reply text ends a line. Subagent calls stay out: the agents pane
// shows them.
function consoleActivityEntriesHtml(entries, msg, live) {
    const stepOpen = msg.stepOpen || {};
    const times = deps.consoleFreezeThoughtTimes(msg, live);
    const segs = deps.assistantSegments(msg);
    let lastReasonIndex = -1;
    segs.forEach((seg, index) => { if (seg && seg.type === "reasoning") lastReasonIndex = index; });
    const phase = deps.runPhase(msg);
    const streamingLast = Boolean(live && lastReasonIndex >= 0 && !segs.some((seg, index) => index > lastReasonIndex) && phase !== "calling" && phase !== "tools");
    const items = [];
    let i = 0;
    while (i < entries.length) {
      const entry = entries[i];
      if (entry.type === "reasoning") {
        const run = [entry];
        let j = i + 1;
        while (j < entries.length && entries[j].type === "reasoning") run.push(entries[j++]);
        i = j;
        // Thinking hidden: the status line says when the model is thinking.
        if (!deps.showThinking) continue;
        const last = run[run.length - 1];
        // A thought still streaming is only the status line's one word; it
        // joins the transcript once it ends.
        if (streamingLast && last.segmentIndex === lastReasonIndex) continue;
        const key = `think-${last.segmentIndex}`;
        const text = run.map((item) => String(item.text || "").trim()).filter(Boolean).join("\n\n");
        if (text) items.push({ type: "think", key, text, ms: times[key] || 1000, segmentIndex: last.segmentIndex });
        continue;
      }
      i += 1;
      if (entry.type !== "tool" || AGENT_TOOLS.has(entry.trace.name)) continue;
      // A script is one call of the line; the calls it made (chat_output.py
      // orders them after it) open under its row. A running one is the status
      // line until it ends, like any call.
      if (entry.trace.name === "run_code") {
        const calls = [];
        while (i < entries.length && entries[i].type === "tool" && entries[i].trace.parent === entry.trace.id) calls.push(entries[i++]);
        if (!deps.toolPresentation(entry.trace, live).running) items.push({ type: "tool", entry, cls: null, key: deps.toolKey(entry.trace, entry.index), calls });
        continue;
      }
      // The call running now is the status line (live-status.js), not a row:
      // it joins the summary when it ends. A background one stays counted.
      // A Wait blocks on its command, so it is the call running now too.
      if (deps.toolPresentation(entry.trace, live).running && (!deps.toolBackground(entry.trace) || entry.trace.name === "get_command_output")) continue;
      const cls = deps.ccToolClass(entry.trace);
      // A silent call (search_tools) stays hidden unless it was cut short: an
      // interrupted one says what the run was doing.
      if (cls?.kind === "silent" && !deps.toolPresentation(entry.trace, live).interrupted) continue;
      items.push({ type: "tool", entry, cls, key: deps.toolKey(entry.trace, entry.index) });
    }
    // No call yet: a thought is its own line, since the summary counts calls.
    if (!items.some((item) => item.type === "tool")) {
      return items.map((item) => thoughtRowHtml(item.key, item.text, item.ms, item.segmentIndex, deps.stepOpenAt(stepOpen, item.key))).join("");
    }
    return ccGroupHtml({ items }, live, stepOpen);
  }

// A script's row reads like any call: its description, then how many calls
// it made (or why it ended). It opens to those calls, three or more reads in
// a row as one row, then its code and what it returned.
function scriptRowHtml(item, live, openMap) {
    const { entry: { trace }, calls, key } = item;
    const presentation = deps.toolPresentation(trace, live);
    const open = deps.stepOpenAt(openMap, key);
    const parts = deps.codeRunParts(trace);
    const total = parts.calls.length ? parts.calls.reduce((n, [, k]) => n + k, 0) : calls.length;
    const finished = calls.filter((c) => ["done", "warning"].includes(deps.toolPresentation(c.trace, live).state)).length;
    const result = presentation.failed ? String(trace.error || "failed").split("\n")[0]
      : presentation.interrupted ? `Interrupted · ${finished} of ${total} ${total === 1 ? "call" : "calls"} finished` : `${total} ${total === 1 ? "call" : "calls"}`;
    // No description: the code's first line says what it is, as in Console.
    const name = trace.description || parts.code.split("\n").map((l) => l.trim()).find(Boolean) || "Script";
    const rows = [];
    for (let at = 0; at < calls.length;) {
      let end = at;
      const reading = (c) => c && c.trace.name === "read_file" && deps.toolPresentation(c.trace, live).state === "done";
      while (reading(calls[end])) end += 1;
      if (end - at >= 3) {
        rows.push(readsRowHtml(calls.slice(at, end), live, openMap));
        at = end;
        continue;
      }
      const c = calls[at++];
      const k = deps.toolKey(c.trace, c.index);
      rows.push(deps.toolStepHtml(c.trace, k, live, deps.stepOpenAt(openMap, k), openMap, { oneLine: true }));
    }
    if (parts.code) rows.push(codeRowHtml(trace, parts, `${key}:code`, deps.stepOpenAt(openMap, `${key}:code`), deps.escapeHtml(result)));
    const line = `${deps.rowIconHtml(trace)}<span class="chat-console-call is-described"><span class="chat-console-val">${deps.escapeHtml(name)}</span></span>` +
      `<span class="chat-console-result-text">${deps.escapeHtml(result)}</span>`;
    return (
      `<li class="chat-step is-console-call is-dk-script is-${presentation.state}${open ? " is-console-expanded" : ""}" data-tool="run_code" data-state="${presentation.state}" data-step="${deps.escapeHtml(key)}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">${deps.toolHeadHtml({ ...presentation, line: line + deps.ROW_CHEV, title: trace.description || "", expandable: true, open })}` +
      `<div class="chat-step-reveal is-open"><div class="chat-step-reveal-inner"><div class="chat-console-hang is-body-only${open ? " is-expanded" : ""}">` +
      `<span class="chat-console-branch" aria-hidden="true">└</span><div class="chat-console-hang-body"><ol class="chat-timeline dk-script-calls">${rows.join("")}</ol></div></div></div></div></div></li>`
    );
  }

// A row whose body is one box under it, opened like a call row.
function boxRowHtml(key, line, box, open, cls = "") {
    return (
      `<li class="chat-step is-console-call is-done${cls}${open ? " is-console-expanded" : ""}" data-step="${deps.escapeHtml(key)}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">${deps.toolHeadHtml({ line: line + deps.ROW_CHEV, title: "", expandable: true, open })}` +
      `<div class="chat-step-reveal is-open"><div class="chat-step-reveal-inner"><div class="chat-console-hang is-body-only${open ? " is-expanded" : ""}">` +
      `<span class="chat-console-branch" aria-hidden="true">└</span><div class="chat-console-hang-body">${box}</div></div></div></div></div></li>`
    );
  }

function readsRowHtml(reads, live, openMap) {
    const key = deps.toolKey(reads[0].trace, reads[0].index) + ":reads";
    const paths = reads.map((c) => deps.shortToolPath(String(c.trace.path || ""), c.trace));
    const folder = (path) => path.slice(0, path.lastIndexOf("/") + 1);
    const where = paths.every((path) => folder(path) === folder(paths[0])) ? folder(paths[0]).replace(/\/$/, "") : "";
    const line = `${deps.rowIconHtml(reads[0].trace)}<span class="chat-console-call"><span class="chat-tool-action">Read</span><span class="chat-console-paren">(</span>` +
      `<span class="chat-console-val"><span class="dk-base">${reads.length} files</span>${where ? ` <span class="dk-dir">in ${deps.escapeHtml(where)}</span>` : ""}</span><span class="chat-console-paren">)</span></span>`;
    // It opens to the reads themselves, each opening to what it read.
    const rows = reads.map((c) => {
      const k = deps.toolKey(c.trace, c.index);
      return deps.toolStepHtml(c.trace, k, live, deps.stepOpenAt(openMap, k), openMap, { oneLine: true });
    }).join("");
    return boxRowHtml(key, line, `<ol class="chat-timeline dk-script-calls">${rows}</ol>`, deps.stepOpenAt(openMap, key));
  }

function codeRowHtml(trace, parts, key, open, meta) {
    // A failed run's text is its error, the calls that finished before it and
    // its console output: shown, not dropped.
    const failed = deps.toolPresentation(trace, false).failed;
    const ret = parts.result.trim()
      ? `<div class="dk-code-part"><div class="dk-code-label">${failed ? "Error" : "Returned"}</div><pre class="chat-console-out is-stdout">${deps.escapeHtml(parts.result)}</pre></div>` : "";
    const line = `${deps.rowIconHtml(trace)}<span class="chat-console-call is-described"><span class="chat-tool-action">Code</span>${ret ? `<span class="chat-console-val">${failed ? "and its error" : "and what it returned"}</span>` : ""}</span>${meta ? `<span class="chat-console-result-text">${meta}</span>` : ""}`;
    const lines = parts.code.split("\n").length;
    const body = `${ret}<div class="dk-code-part"><div class="dk-code-label">Code <span>JavaScript · ${lines} ${lines === 1 ? "line" : "lines"}</span></div>` +
      `<pre class="chat-console-out is-code"><code>${deps.highlightCode("javascript", parts.code)}</code></pre></div>`;
    return boxRowHtml(key, line, deps.callBoxHtml({ body }), open, " is-dk-coderow");
  }

// A settled thought: one line ("Thought for 4s" and its first words) that
// opens to the whole text, like a call row.
function thoughtRowHtml(key, text, ms, segmentIndex, open) {
    // The row names the thought by its first line, markdown marks dropped.
    const first = String(text || "").split("\n").map((part) => part.replace(/\*\*|__|`|^#+\s*/g, "").trim()).find(Boolean) || "";
    const line = `${deps.rowIconHtml(null)}<span class="chat-console-call"><span class="chat-tool-action">Thought</span>` +
      `<span class="dk-think-first">${deps.escapeHtml(deps.ccClip(first, 120))}</span></span><span class="chat-console-took">${deps.escapeHtml(ccThinkClock(ms, 0))}</span>${deps.ROW_CHEV}`;
    return (
      `<li class="chat-step is-console-call is-dk-thought is-done${open ? " is-console-expanded" : ""}" data-step="${deps.escapeHtml(key)}" data-thought="${segmentIndex}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">${deps.toolHeadHtml({ line, title: "", expandable: true, open })}` +
      `<div class="chat-step-reveal is-open"><div class="chat-step-reveal-inner"><div class="chat-console-hang is-body-only${open ? " is-expanded" : ""}">` +
      `<span class="chat-console-branch" aria-hidden="true">└</span><div class="chat-console-hang-body">` +
      `<div class="chat-console-thought chat-think-markdown">${deps.renderMarkdown(text, false)}</div></div></div></div></div></div></li>`
    );
  }

const AGENT_TOOLS = new Set(["spawn_subagent", "get_subagent_output", "kill_subagent", "list_subagents"]);

// Calls the shell classes do not cover, counted on the summary line.
const OTHER_KIND = {
    read_url: "fetch", monitor: "monitor", get_command_output: "wait", kill_command: "stop", load_skill: "skill",
    propose_skill: "draft", create_artifact: "artifact", update_artifact: "artifact", edit_artifact: "artifact",
    read_artifact: "artifact-read", remember: "remember", recall: "recall", forget: "forget", scratch: "scratch",
    read_transcript: "transcript", search_models: "models", run_code: "script", read_result: "kept",
  };
const OTHER_WORDS = {
    fetch: ["fetched", "fetching", "page", "pages"], web: ["ran", "running", "web search", "web searches"],
    monitor: ["started", "starting", "monitor", "monitors"], wait: ["waited on", "waiting on", "command", "commands"],
    stop: ["stopped", "stopping", "command", "commands"], skill: ["loaded", "loading", "skill", "skills"],
    draft: ["drafted", "drafting", "skill", "skills"], artifact: ["built", "building", "artifact", "artifacts"],
    "artifact-read": ["read", "reading", "artifact", "artifacts"], remember: ["saved", "saving", "memory", "memories"],
    recall: ["recalled", "recalling", "memory", "memories"], forget: ["forgot", "forgetting", "memory", "memories"],
    scratch: ["used", "using", "scratch note", "scratch notes"], transcript: ["read", "reading", "transcript", "transcripts"],
    models: ["searched", "searching", "model list", "model lists"], script: ["ran", "running", "script", "scripts"],
    kept: ["read", "reading", "kept result", "kept results"],
    tool: ["used", "using", "tool", "tools"],
  };

const CC_PR_VERBS = { created: "created", edited: "edited", merged: "merged", commented: "commented on", closed: "closed",
    reopened: "reopened", ready: "marked ready", draft: "marked draft", "auto-merge-enabled": "enabled auto-merge on",
    "auto-merge-disabled": "disabled auto-merge on" };

const CC_LIVE_TOOLS = new Set(["run_terminal_command", "read_file", "grep", "glob", "list_dir"]);

  return { ccTurnLineHtml, ccShellHint, ccPathHint, ccActivityText, ccRunning, ccSince, ccHeadline, ccLiveText, ccElapsedText, ccShellSuffix, ccThinkClock, syncConsoleClocks, workClocksLive, syncWorkClocks, ccGroupHtml, consoleActivityEntriesHtml, CC_PR_VERBS, CC_LIVE_TOOLS };
}

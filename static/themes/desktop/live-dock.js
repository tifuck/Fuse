// live-dock presentation. Dependencies are live getters supplied by index.js.
// The dock's clock and token count tick several times a second, so it is not
// a live region. Screen readers hear the phase when it changes and the end.
const SPOKEN_PHASE = { thinking: "Thinking", writing: "Writing", stopping: "Stopping", queued: "Queued", searching: "Searching" };
let spokenPhase = "";
let spokenMsg = null;
function speakPhase(phase, msg) {
  const text = SPOKEN_PHASE[phase] || "Working";
  spokenMsg = msg;
  if (text === spokenPhase) return;
  spokenPhase = text;
  globalThis.fuseAnnounce?.(text);
}
function speakEnd() {
  if (!spokenPhase) return;
  const text = spokenMsg?.error ? "Response failed" : spokenPhase === "Stopping" || spokenMsg?.stopped ? "Stopped" : "Response finished";
  spokenPhase = "";
  spokenMsg = null;
  globalThis.fuseAnnounce?.(text);
}

export function create(deps) {
function setLiveLabelText(el, text, meta, arg = "") {
    if (!el) return;
    let verb = el.querySelector(":scope > .chat-status-verb");
    let tail = el.querySelector(":scope > .chat-status-meta");
    if (!verb || !tail) {
      verb = document.createElement("span");
      verb.className = "chat-status-verb";
      tail = document.createElement("span");
      tail.className = "chat-status-meta";
      el.replaceChildren(verb, tail);
    }
    // An undescribed running command, in parentheses; one line.
    let cmd = el.querySelector(":scope > .dk-live-arg");
    if (!cmd) {
      cmd = document.createElement("span");
      cmd.className = "dk-live-arg";
      cmd.append(document.createElement("span"));
      el.insertBefore(cmd, tail);
    }
    if (cmd.firstChild.textContent !== arg) cmd.firstChild.textContent = arg;
    if (cmd.hidden !== !arg) cmd.hidden = !arg;
    // The chevron after the text: shown while the line opens the live pane.
    if (!el.querySelector(":scope > .dk-live-chev")) {
      const chev = document.createElement("span");
      chev.className = "dk-live-chev";
      chev.setAttribute("aria-hidden", "true");
      chev.innerHTML = '<svg viewBox="0 0 16 16"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      el.insertBefore(chev, tail);
    }
    // The clock, at the line's far end.
    const clock = meta?.clock || "";
    if (tail.textContent !== clock) tail.textContent = clock;
    if (tail.title !== "Elapsed") tail.title = "Elapsed";
    const hideTail = !clock;
    // Toggling hidden on every tick reflows the in-flow dock and can flip the
    // transcript extent by a rounding pixel; only touch it on a real change.
    if (tail.hidden !== hideTail) tail.hidden = hideTail;
    if (verb.textContent === text) return;
    swapVerb(el, verb, text);
  }

// The status changing fades: the status element keeps the new text (its
// shimmer, ellipsis and textContent untouched); the old text leaves in an
// aria-hidden copy laid over it, clipped to the line.
const SWAP_MS = 180;
const SWAP_EASE = "cubic-bezier(.2, .8, .2, 1)";
function swapVerb(el, verb, text) {
    const old = verb.textContent;
    const live = verb._swap;
    if (live) {
      live.anims.forEach((a) => a.cancel());
      live.ghost.remove();
    }
    verb._swap = null;
    const w0 = verb.getBoundingClientRect().width;
    const box = { left: verb.offsetLeft, top: verb.offsetTop, width: verb.offsetWidth, height: verb.offsetHeight };
    verb.textContent = text;
    if (!old || !text || !w0 || deps.reduceMotion.matches) return;
    const run = { anims: [], ghost: document.createElement("span") };
    verb._swap = run;
    run.ghost.className = "dk-verb-ghost";
    run.ghost.setAttribute("aria-hidden", "true");
    Object.assign(run.ghost.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
    const out = document.createElement("span");
    out.textContent = old;
    run.ghost.append(out);
    el.append(run.ghost);
    run.anims.push(out.animate([{ opacity: 1, filter: "blur(0)" }, { opacity: 0, filter: "blur(4px)" }], { duration: SWAP_MS * 0.8, easing: "ease-out", fill: "forwards" }));
    const landed = verb.animate([{ opacity: 0, filter: "blur(4px)", transform: "scale(.98)" }, { opacity: 1, filter: "blur(0)", transform: "none" }], { duration: SWAP_MS, easing: SWAP_EASE });
    run.anims.push(landed);
    landed.finished.then(() => {
      if (verb._swap !== run) return;
      run.ghost.remove();
      verb._swap = null;
    }, () => {});
  }

// The live pane. Opened, the status line heads a card over the reply's
// newest calls, oldest at the top: a call that starts is the next entry, so
// moving on opens, closes and moves nothing, and the card keeps one height.
// Open is a remembered choice, not a call's: it holds for every later call,
// the thinking between them, the next reply and a reload.
const PANE_KEY = "fuse.desktop.livePane";
const PANE_CALLS = 8;
const PANE_LINES = 40;
const PANE_MS = 240;
const PANE_EASE = "cubic-bezier(0.2, 0, 0, 1)";
const PANE_SKIP = new Set(["spawn_subagent", "get_subagent_output", "kill_subagent", "list_subagents"]);
const PANE_DOTS = '<span class="dk-fe-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
let paneOpen = false;
try { paneOpen = localStorage.getItem(PANE_KEY) === "1"; } catch (_) {}

// The calls the summary rows count (no subagent calls, no todo or tool-search
// bookkeeping), newest last.
function paneTraces(msg) {
    const traces = Array.isArray(msg?.tools) ? msg.tools : [];
    return traces.filter((trace) => trace && !PANE_SKIP.has(trace.name) && !["silent", "todo"].includes(deps.ccToolClass(trace)?.kind)).slice(-PANE_CALLS);
  }

// How a call stands, at the end of its line: running (dots, then its own
// time), how it ended, what an edit changed, or how long it took.
function paneState(trace) {
    const presentation = deps.toolPresentation(trace, true);
    if (presentation.running) {
      if (deps.toolBackground(trace)) return { text: "in the background" };
      const ms = Date.now() - deps.ccSince(trace);
      return { text: ms >= 2000 ? deps.ccDuration(ms) : "", running: true };
    }
    if (presentation.interrupted) return { text: "Stopped" };
    const exit = deps.shellExitCode(trace);
    if (presentation.failed || (exit && deps.ccExitMeaning(deps.displayCommand(trace), exit).isError)) return { text: exit ? `Exit ${exit}` : "Failed", bad: true };
    if (trace.name === "search_replace" || trace.name === "write_file") {
      const { added, removed } = deps.consoleEditCounts(trace);
      if (added || removed) return { html: `<span class="dk-plus">+${added}</span> <span class="dk-minus">−${removed}</span>` };
    }
    const ms = Number(trace.execution_duration_ms ?? trace.call_duration_ms ?? trace.duration_ms ?? trace.latency_ms);
    return { text: ms > 0 ? deps.humanMs(ms) : "" };
  }

// What a call shows under its line: a command's newest output, an edit's
// change, a search's first matches or results. Anything else is its line.
function paneBody(trace) {
    const name = trace.name;
    if (name === "search_replace" || name === "write_file") {
      const diff = deps.consoleEditDiff(trace);
      return diff ? { html: deps.consoleDiffHtml(diff, { limit: 12, numbers: false }) } : null;
    }
    if (deps.isSearchTool(name)) {
      const results = Array.isArray(trace.results) ? trace.results.slice(0, 4) : [];
      return { lines: results.map((r) => `<b>${deps.escapeHtml(deps.pageHost(r.url) || "")}</b>  ${deps.escapeHtml(r.title || r.url || "")}`) };
    }
    const out = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
    if (!out) return null;
    const rows = out.split("\n");
    if (["run_terminal_command", "get_command_output", "monitor"].includes(name)) return { lines: rows.slice(-PANE_LINES).map(deps.renderAnsi) };
    if (["grep", "glob", "list_dir"].includes(name)) return { lines: rows.slice(0, 6).map(deps.escapeHtml) };
    return null;
  }

function paneLine(html) {
    const line = document.createElement("div");
    line.className = "dk-fe-ln";
    line.innerHTML = html || " ";
    return line;
  }

function paneEntry() {
    const el = document.createElement("div");
    el.className = "dk-fe";
    el.innerHTML = '<div class="dk-fe-head"><span class="dk-fe-ico"></span><span class="dk-fe-in"></span><span class="dk-fe-state"></span></div><div class="dk-fe-out"></div>';
    [el._icoEl, el._inEl, el._state] = el.firstChild.children;
    el._out = el.lastChild;
    return el;
  }

// Brings one entry up to date in place; true when anything on it changed.
function syncPaneEntry(el, trace) {
    let changed = false;
    const ico = deps.rowIconHtml(trace);
    if (el._ico !== ico) {
      el._ico = ico;
      el._icoEl.innerHTML = ico;
    }
    const input = deps.callInput(trace);
    const text = input.input ? `${input.prompt ? '<span class="dk-fe-pr">$ </span>' : ""}${deps.escapeHtml(deps.ccFlat(input.input))}` : deps.escapeHtml(deps.toolRowLabel(trace));
    if (el._text !== text) {
      el._text = text;
      el._inEl.innerHTML = text;
      changed = true;
    }
    const state = paneState(trace);
    const stateHtml = state.html || (state.running && !state.text ? PANE_DOTS : deps.escapeHtml(state.text));
    if (el._stateHtml !== stateHtml) {
      el._stateHtml = stateHtml;
      el._state.innerHTML = stateHtml;
      el._state.classList.toggle("is-bad", Boolean(state.bad));
      el.classList.toggle("is-done", !state.running);
    }
    const body = paneBody(trace);
    if (body?.html) {
      if (el._html === body.html) return changed;
      el._html = body.html;
      el._lines = null;
      el._out.innerHTML = body.html;
      return true;
    }
    if (el._html) {
      el._html = null;
      el._out.replaceChildren();
    }
    const lines = body?.lines || [];
    const old = el._lines || [];
    let same = 0;
    while (same < old.length && same < lines.length && old[same] === lines[same]) same += 1;
    if (same === old.length && same === lines.length) return changed;
    // Output grows at its end: the lines already drawn stay (a selection in
    // them survives); only a grown last line and the new ones are drawn.
    if (same >= old.length - 1 && lines.length >= old.length) {
      if (same < old.length) el._out.lastChild.innerHTML = lines[same] || " ";
      for (let i = old.length; i < lines.length; i += 1) el._out.append(paneLine(lines[i]));
    } else el._out.replaceChildren(...lines.map(paneLine));
    el._lines = lines;
    return true;
  }

// The card: the status line and, under it, the pane, which folds open and shut.
function paneParts(dock) {
    if (dock._pane) return dock._pane;
    const head = dock.querySelector("[data-dock-status]");
    const card = document.createElement("div");
    card.className = "dk-live-card";
    const fold = document.createElement("div");
    fold.className = "dk-pane";
    fold.id = "dk-live-pane";
    fold.inert = true;
    fold.innerHTML = '<div class="dk-pane-in"><div class="dk-feed" role="group" aria-label="Calls"><div class="dk-feed-lines"></div></div></div>';
    head.before(card);
    card.append(head, fold);
    head.setAttribute("aria-controls", fold.id);
    const pane = dock._pane = { head, fold, feed: fold.querySelector(".dk-feed"), lines: fold.querySelector(".dk-feed-lines"), entries: new Map(), msgId: null };
    const toggle = (event) => {
      if (!dock.classList.contains("is-detailable") || !event.target.closest("[data-dock-status]")) return;
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      paneOpen = !paneOpen;
      try { localStorage.setItem(PANE_KEY, paneOpen ? "1" : "0"); } catch (_) {}
      showPane(dock, pane);
    };
    dock.addEventListener("click", toggle);
    dock.addEventListener("keydown", toggle);
    return pane;
  }

function showPane(dock, pane) {
    const open = paneOpen && pane.entries.size > 0;
    if (pane.head.hasAttribute("role")) pane.head.setAttribute("aria-expanded", String(open));
    if (dock.classList.contains("is-pane-open") === open) return;
    dock.classList.toggle("is-pane-open", open);
    pane.fold.inert = !open;
    // A followed transcript keeps its end in view while the pane folds.
    followLiveDockSlide(PANE_MS);
  }

function syncLivePane(dock, msg) {
    const pane = paneParts(dock);
    if (pane.msgId !== msg.id) {
      pane.msgId = msg.id;
      pane.entries.clear();
      pane.lines.replaceChildren();
    }
    // Changes slide: what was on screen moves to where it now sits.
    const moving = dock.classList.contains("is-pane-open") && !deps.reduceMotion.matches;
    const before = moving ? pane.lines.getBoundingClientRect().top : 0;
    const keep = new Set();
    const added = [];
    let changed = false;
    paneTraces(msg).forEach((trace) => {
      const key = String(trace.id || deps.executionIdentity(trace));
      keep.add(key);
      let el = pane.entries.get(key);
      if (!el) {
        el = paneEntry();
        pane.entries.set(key, el);
        pane.lines.append(el);
        added.push(el);
      }
      if (syncPaneEntry(el, trace)) changed = true;
    });
    for (const [key, el] of pane.entries) {
      if (keep.has(key)) continue;
      el.remove();
      pane.entries.delete(key);
      changed = true;
    }
    // Read from the top until it fills, then from the newest line up.
    pane.feed.classList.toggle("is-full", pane.lines.offsetHeight > pane.feed.clientHeight);
    if (moving && changed) {
      pane.lines.getAnimations().forEach((a) => a.cancel());
      const dy = before - pane.lines.getBoundingClientRect().top;
      if (Math.abs(dy) > 0.5) pane.lines.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: PANE_MS, easing: PANE_EASE });
      added.forEach((el) => el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: PANE_MS, easing: "ease-out" }));
    }
    const detailable = pane.entries.size > 0;
    dock.classList.toggle("is-detailable", detailable);
    if (detailable && pane.head.getAttribute("role") !== "button") {
      pane.head.setAttribute("role", "button");
      pane.head.tabIndex = 0;
    } else if (!detailable && pane.head.hasAttribute("role")) {
      pane.head.removeAttribute("role");
      pane.head.removeAttribute("tabindex");
      pane.head.removeAttribute("aria-expanded");
    }
    showPane(dock, pane);
  }

function setLivePhase(dock, phase) {
    if (dock && dock.dataset.phase !== phase) dock.dataset.phase = phase;
  }

function currentActivity(msg, groups, live) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    const grouped = groups || deps.groupTraces(traces);
    const owner = msg?.id === deps.activeRun?.message_id ? deps.activeRun
      : typeof deps.agentRun !== "undefined" && msg?.id === deps.agentRun?.message_id ? deps.agentRun : null;
    const stopping = Boolean(live && owner && deps.runIsStopping(owner));
    let current = live && !stopping ? deps.lastCurrentTrace(grouped) : null;
    const activity = msg?.id === deps.activeRun?.message_id ? deps.activeRun?.activity : msg?._activity;
    if (activity?.phase === "model" || activity?.phase === "calling") current = null;
    else if (live && !stopping && activity?.phase === "tools" && activity.tool_id) {
      current = traces.find(trace => trace.id === activity.tool_id && trace.name !== "todo_write" && deps.toolIsCurrent(trace)) || current;
    }
    const segments = deps.assistantSegments(msg);
    const last = [...segments].reverse().find(part => part && (part.type === "tool" || String(part.text || "").trim()));
    // Ordered text after a tool is newer activity, even if that tool's child
    // is still alive or its terminal checkpoint has not arrived yet.
    if (current && !activity?.phase && Array.isArray(msg?.segments) && ["reasoning", "content"].includes(last?.type)) {
      const toolIndex = segments.findLastIndex(part => part.type === "tool" && part.id === current.id);
      if (toolIndex >= 0 && segments.lastIndexOf(last) > toolIndex) current = null;
    }
    let phase = "idle";
    let label = "";
    if (stopping) {
      phase = "stopping";
      label = "Stopping";
    } else if (live && owner?.status === "queued") {
      phase = "queued";
      label = "Queued";
    } else if (current?.status === "queued") {
      phase = "queued";
      label = (deps.isShellExecTool(current.name) ? "queued bash" : current.name === "spawn_subagent" ? "queued task" : "queued");
    } else if (current) {
      if (deps.isSearchTool(current.name)) {
        phase = "searching";
        label = ("searching");
      } else if (current.name === "read_url") {
        phase = "reading";
        label = ("fetching");
      } else if (["create_artifact", "update_artifact", "edit_artifact", "read_artifact"].includes(current.name)) {
        phase = "building";
        label = ("artifact");
      } else if (current.name === "list_subagents") {
        phase = "working";
        label = ("agents");
      } else if (current.name === "search_models") {
        phase = "searching";
        label = ("models");
      } else if (current.name === "get_command_output") {
        phase = "working";
        label = ("awaiting bash");
      } else if (current.name === "kill_command") {
        phase = "working";
        label = ("killing");
      } else if (current.name === "kill_subagent") {
        phase = "working";
        label = ("killing task");
      } else if (current.name === "spawn_subagent") {
        phase = "spawning";
        label = ("task");
      } else if (current.name === "get_subagent_output") {
        phase = "waiting";
        label = ("awaiting task");
      } else if (current.name === "run_terminal_command") {
        phase = "working";
        label = ("bash");
      } else if (current.name === "monitor") {
        phase = "working";
        label = ("watch");
      } else {
        phase = "working";
        label = (deps.consoleVerb(current.name, current).toLowerCase());
      }
    } else if (live && activity?.phase === "tools") {
      phase = "working";
      label = ("working");
    } else if (live && activity?.phase === "calling") {
      // The model is writing a tool call; the stream names it before its
      // arguments finish, long before the call starts running.
      phase = "calling";
      label = activity.tool ? deps.consoleVerb(activity.tool, { name: activity.tool }).toLowerCase() : "calling";
    } else if (live) {
      if (last && last.type === "reasoning") {
        phase = "thinking";
        label = ("thinking");
      } else if (last && last.type === "content") {
        phase = "writing";
        label = ("writing");
      } else if (activity?.phase === "model" && activity.thinking) {
        // The provider marked a think that may stream no text.
        phase = "thinking";
        label = ("thinking");
      } else if (last && (last.type === "tool" || last.type === "delivery")) {
        // Delivered results, like a finished call, hand the turn back to the model.
        phase = activity?.phase === "model" ? "waiting" : "starting";
        label = activity?.phase === "model" ? (("waiting")) : (("working"));
      } else if (msg && msg.reasoning && !msg.content) {
        phase = "thinking";
        label = ("thinking");
      } else if (msg && msg.content) {
        phase = "writing";
        label = ("writing");
      } else {
        phase = "waiting";
        label = ("waiting");
      }
    } else if (msg && msg.stopped) {
      phase = "stopped";
      label = "Stopped";
    } else if (msg && msg.error) {
      phase = "error";
      label = "Failed";
    } else {
      const items = grouped.flatMap((g) => g.items);
      const total = items.length;
      const counts = deps.toolCounts(items, live || Boolean(msg?.tools_live));
      phase = "done";
      if (!total) label = msg && msg.reasoning ? "Thought" : "Worked";
      else if (counts.done === total) label = `Completed ${total} ${total === 1 ? "action" : "actions"}`;
      else label = `${total} ${total === 1 ? "action" : "actions"} · ${deps.countsLabel(counts)}`;
    }
    const tool = phase === "calling" ? String(activity.tool || "") : "";
    return { phase, label, current, tool, live: Boolean(live && !stopping), busy: Boolean(live) };
  }

function activityKind(msg, groups, live) {
    if (!live) return "";
    const status = currentActivity(msg, groups, live);
    if (status.phase === "searching") return "searching";
    if (status.phase === "reading") return "reading";
    if (status.phase === "building") return "building";
    if (status.phase === "writing") return "writing";
    if (status.phase === "thinking") return "thinking";
    if (status.phase === "calling") return deps.isSearchTool(status.tool) ? "searching" : "working";
    if (status.phase === "starting" || status.phase === "stopping") return "waiting";
    if (status.current) return "working";
    return "waiting";
  }

// Desktop's status line appears and goes in place, with no fade to wait for.
function liveDockMotionMs() {
    return 0;
  }

function followLiveDockSlide(ms = liveDockMotionMs()) {
    if (!deps.thread || !deps.stickToBottom) return;
    if (deps.liveDockFollowFrame) cancelAnimationFrame(deps.liveDockFollowFrame);
    const until = performance.now() + ms;
    const step = (now) => {
      if (deps.stickToBottom && deps.thread) deps.scrollThread();
      if (now < until) deps.liveDockFollowFrame = requestAnimationFrame(step);
      else deps.liveDockFollowFrame = 0;
    };
    deps.liveDockFollowFrame = requestAnimationFrame(step);
  }

function hideLiveDock() {
    if (!deps.liveDock) return;
    deps.liveDock.hidden = true;
    deps.liveDock.setAttribute("aria-hidden", "true");
    deps.syncLatest();
  }

// A neutral braille spinner; sidebar marks use the same frames.
function consoleSpinGlyph() {
    return deps.reduceMotion.matches ? CONSOLE_SPIN_STILL : CONSOLE_SPIN_FRAMES[deps.consoleSpinFrame % CONSOLE_SPIN_FRAMES.length];
  }

function consoleSpinMarks() {
    return [[deps.latestPill, deps.latestSpin]]
      .filter(([dock, spin]) => dock && spin && !dock.hidden && !spin.hidden)
      .map(([, spin]) => spin.querySelector(":scope > .chat-console-spin"))
      .concat([...deps.root.querySelectorAll("[data-run-spin]:not([hidden]) > .chat-console-spin")])
      .filter(Boolean);
  }

function stopConsoleSpin() {
    if (deps.consoleSpinTimer) {
      window.clearInterval(deps.consoleSpinTimer);
      deps.consoleSpinTimer = 0;
    }
  }

function setConsoleSpin(el) {
    if (!el) return;
    let mark = el.querySelector(":scope > .chat-console-spin");
    if (el.dataset.spinSet !== "console-glyph" || !mark) {
      el.dataset.spinSet = "console-glyph";
      mark = document.createElement("span");
      mark.className = "chat-console-spin";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = consoleSpinGlyph("runSpin" in el.dataset);
      el.replaceChildren(mark);
    }
    if (deps.consoleSpinTimer || deps.reduceMotion.matches) return;
    deps.consoleSpinTimer = window.setInterval(() => {
      const marks = consoleSpinMarks();
      if (!marks.length) {
        stopConsoleSpin();
        return;
      }
      deps.consoleSpinFrame = (deps.consoleSpinFrame + 1) % CONSOLE_SPIN_FRAMES.length;
      marks.forEach((spin) => { spin.textContent = consoleSpinGlyph("runSpin" in spin.parentElement.dataset); });
    }, 110);
  }

function syncLiveDock() {
    deps.scheduleTodoSync();
    if (!deps.liveDock) return;
    // A hidden log is a transcript being replaced (a thread switch): the line
    // takes the opened thread's state at once. The slide in and the delayed
    // hide are for a run starting or ending in view; on a switch they left the
    // last thread's line under the new transcript for a beat, then dropped it.
    const instant = deps.log.hidden;
    const live = Boolean(deps.streaming || (deps.runIsActive(deps.activeRun) && deps.runIsStopping(deps.activeRun)));
    const msg = live ? deps.activeAssistant() : null;
    const on = Boolean(live && msg && msg.role === "assistant");
    if (on) {
      if (deps.liveDockHideTimer) {
        clearTimeout(deps.liveDockHideTimer);
        deps.liveDockHideTimer = 0;
      }
      deps.liveDock.hidden = false;
      deps.liveDock.setAttribute("aria-hidden", "false");
      const groups = deps.groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = currentActivity(msg, groups, true);
      setLivePhase(deps.liveDock, status.phase);
      speakPhase(status.phase, msg);
      {
        const line = deps.formatConsoleLiveStatus(msg, groups);
        setLiveLabelText(deps.liveDockLabel, line.verb, line.meta, line.arg);
        syncLivePane(deps.liveDock, msg);
      }
      if (instant) {
        if (deps.liveDockShowFrame) cancelAnimationFrame(deps.liveDockShowFrame);
        deps.liveDockShowFrame = 0;
        deps.root.classList.add("is-live-dock");
      } else if (!deps.root.classList.contains("is-live-dock") && !deps.liveDockShowFrame) {
        deps.liveDockShowFrame = requestAnimationFrame(() => {
          deps.liveDockShowFrame = 0;
          if (!deps.runIsActive(deps.activeRun)) return;
          deps.root.classList.add("is-live-dock");
          followLiveDockSlide();
        });
      }
      deps.syncLatest();
      return;
    }
    if (!deps.root.classList.contains("is-live-dock") && deps.liveDock.hidden) return;
    if (instant) {
      clearTimeout(deps.liveDockHideTimer);
      deps.liveDockHideTimer = 0;
      spokenPhase = "";
      spokenMsg = null;
      deps.root.classList.remove("is-live-dock");
      hideLiveDock();
      return;
    }
    if (deps.liveDockHideTimer) return;
    speakEnd();
    deps.root.classList.remove("is-live-dock");
    followLiveDockSlide();
    deps.liveDock.setAttribute("aria-hidden", "true");
    deps.syncLatest();
    const wait = liveDockMotionMs();
    if (!wait) {
      hideLiveDock();
      return;
    }
    deps.liveDockHideTimer = setTimeout(() => {
      deps.liveDockHideTimer = 0;
      if (!deps.runIsActive(deps.activeRun) || !deps.activeAssistant()) hideLiveDock();
    }, wait);
  }

function activityLabel(msg, groups, live) {
    return currentActivity(msg, groups, live).label;
  }

const CONSOLE_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const CONSOLE_SPIN_STILL = "⠿";

  return { setLiveLabelText, setLivePhase, currentActivity, activityKind, liveDockMotionMs, followLiveDockSlide, hideLiveDock, consoleSpinGlyph, consoleSpinMarks, stopConsoleSpin, setConsoleSpin, syncLiveDock, activityLabel, CONSOLE_SPIN_FRAMES, CONSOLE_SPIN_STILL };
}

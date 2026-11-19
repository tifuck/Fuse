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
    // The clock, at the line's far end.
    const clock = meta?.clock || "";
    FuseChatDOM.setText(tail, clock);
    if (tail.title !== "Elapsed") tail.title = "Elapsed";
    const hideTail = !clock;
    // Toggling hidden on every tick reflows the in-flow dock and can flip the
    // transcript extent by a rounding pixel; only touch it on a real change.
    if (tail.hidden !== hideTail) tail.hidden = hideTail;
    if (verb.textContent === text && cmd.firstChild.textContent === arg) return;
    swapLabel(el, verb, cmd, text, arg);
  }

// The status changing rolls over: the old words rise and blur out of the line
// in an aria-hidden copy clipped to it, the new ones rise into place. The
// status element keeps the new text (its shimmer, ellipsis and textContent
// untouched). The command in parentheses moves with its status.
const SWAP_OUT_MS = 140;
const SWAP_IN_MS = 220;
const SWAP_EASE = "cubic-bezier(.2, .8, .2, 1)";
function swapLabel(el, verb, cmd, text, arg) {
    const old = verb.textContent;
    const live = verb._swap;
    if (live) {
      live.anims.forEach((a) => a.cancel());
      live.ghost.remove();
    }
    verb._swap = null;
    const right = cmd.hidden ? verb.offsetLeft + verb.offsetWidth : cmd.offsetLeft + cmd.offsetWidth;
    const box = { left: verb.offsetLeft, width: right - verb.offsetLeft };
    const ghost = document.createElement("span");
    ghost.className = "dk-verb-ghost";
    ghost.setAttribute("aria-hidden", "true");
    const was = document.createElement("span");
    was.className = "dk-ghost-verb";
    was.textContent = old;
    ghost.append(was);
    if (!cmd.hidden) ghost.append(cmd.cloneNode(true));
    FuseChatDOM.setText(verb, text);
    if (cmd.firstChild.textContent !== arg) cmd.firstChild.textContent = arg;
    if (cmd.hidden !== !arg) cmd.hidden = !arg;
    if (!old || !text || !box.width || deps.reduceMotion.matches) return;
    const run = { anims: [], ghost };
    verb._swap = run;
    Object.assign(ghost.style, { left: `${box.left}px`, width: `${box.width}px` });
    el.append(ghost);
    const gone = { opacity: 0, transform: "translateY(-0.45em)", filter: "blur(2px)" };
    const coming = { opacity: 0, transform: "translateY(0.45em)", filter: "blur(2px)" };
    const shown = { opacity: 1, transform: "none", filter: "blur(0)" };
    run.anims.push(ghost.animate([shown, gone], { duration: SWAP_OUT_MS, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards" }));
    const landed = [verb, ...(cmd.hidden ? [] : [cmd])].map((node) =>
      node.animate([coming, shown], { duration: SWAP_IN_MS, delay: 40, easing: SWAP_EASE, fill: "backwards" }));
    run.anims.push(...landed);
    landed[0].finished.then(() => {
      if (verb._swap !== run) return;
      run.ghost.remove();
      verb._swap = null;
    }, () => {});
  }

// The live pane. Opened, the status line heads a card with what that line
// names and nothing else: the call running now (its input, how it stands, its
// newest output or change) or the thought being written. When the line moves
// on, the card swaps to the new one, its height easing between the two; a line
// with nothing to show (the reply being written) leaves just the line.
// Open is a remembered choice, not a call's: it holds for every later call,
// the thinking between them, the next reply and a reload.
const PANE_KEY = "fuse.desktop.livePane";
const PANE_LINES = 40;
const PANE_MS = 240;
const PANE_EASE = "cubic-bezier(0.2, 0, 0, 1)";
const PANE_IDLE = { thinking: "Thinking", writing: "The reply is coming in above" };
const PANE_DOTS = '<span class="dk-fe-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
let paneOpen = false;
try { paneOpen = localStorage.getItem(PANE_KEY) === "1"; } catch (_) {}

// The call the line names (key: its id). A call a run_code's code made
// shows as part of that run_code, so the pane holds one script while its
// calls come and go.
function paneTrace(msg, key) {
    const traces = Array.isArray(msg?.tools) ? msg.tools : [];
    const trace = key ? traces.find((t) => String(t?.id ?? "") === key) : null;
    const parent = trace?.parent ? traces.find((t) => String(t?.id ?? "") === String(trace.parent)) : null;
    return parent?.name === "run_code" ? parent : trace || null;
  }

// The calls a run_code's code has made, in the order they started.
function paneCalls(msg, trace) {
    if (trace?.name !== "run_code") return [];
    const id = String(trace.id ?? "");
    return (Array.isArray(msg?.tools) ? msg.tools : []).filter((t) => String(t?.parent ?? "") === id);
  }

// The thought being written: the newest run of reasoning, as plain text.
function paneThought(msg) {
    const segs = Array.isArray(msg?.segments) ? msg.segments : [];
    let end = segs.length - 1;
    while (end >= 0 && segs[end]?.type !== "reasoning") end -= 1;
    const parts = [];
    for (let k = end; k >= 0 && segs[k]?.type === "reasoning"; k -= 1) parts.unshift(String(segs[k].text || "").trim());
    const text = segs.length ? parts.filter(Boolean).join("\n\n") : String(msg?.reasoning || "");
    return text.replace(/\*\*|^#{1,6}\s+/gm, "").replace(/\n{3,}/g, "\n\n").trim();
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

// What a call shows under its line: a command's newest output (tail: read
// from the bottom), an edit's change, a search's first matches or results.
function paneBody(trace) {
    const name = trace.name;
    if (name === "search_replace" || name === "write_file") {
      const diff = deps.consoleEditDiff(trace);
      return diff ? { html: deps.consoleDiffHtml(diff, { limit: 12, numbers: false }) } : null;
    }
    if (name === "run_code") {
      // Before its first call, what it will run.
      const code = deps.codeRunParts(trace).code.replace(/\s+$/, "");
      return code ? { lines: code.split("\n").slice(0, PANE_LINES).map(deps.escapeHtml) } : null;
    }
    if (name === "read_file" && trace.image) {
      const info = deps.toolImageInfo(trace);
      return info ? { html: `${deps.toolImageHtml(trace)}<div class="dk-fe-ln">${deps.escapeHtml(info)}</div>` } : null;
    }
    if (deps.isSearchTool(name)) {
      const results = Array.isArray(trace.results) ? trace.results.slice(0, 4) : [];
      return { lines: results.map((r) => `<b>${deps.escapeHtml(deps.pageHost(r.url) || "")}</b>  ${deps.escapeHtml(r.title || r.url || "")}`) };
    }
    const out = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
    if (!out) return null;
    const rows = out.split("\n");
    if (["run_terminal_command", "get_command_output", "monitor"].includes(name)) return { lines: rows.slice(-PANE_LINES).map(deps.renderAnsi), tail: true };
    if (["grep", "glob", "list_dir", "read_file"].includes(name)) return { lines: rows.slice(0, 6).map(deps.escapeHtml) };
    return null;
  }

// What an empty item says: what the call is doing until it has output.
const IMAGE_PATH = /\.(png|jpe?g|webp|gif|bmp|tiff?|ico|apng)$/i;
function paneEmpty(trace) {
    if (!deps.toolPresentation(trace, true).running) return "No output";
    if (trace.name === "read_file") return IMAGE_PATH.test(String(trace.path || "")) ? "Loading the image" : "Reading the file";
    if (deps.isSearchTool(trace.name) || ["grep", "glob", "list_dir"].includes(trace.name)) return "Searching";
    return "Waiting for output";
  }

function paneLine(html) {
    const line = document.createElement("div");
    line.className = "dk-fe-ln";
    line.innerHTML = html || " ";
    return line;
  }

// One item: a call (its line over its output) or a thought (its text). Until
// it has any, it says so (empty), so an open pane never shrinks to the line.
function paneItem(thought, empty = "Waiting for output") {
    const el = document.createElement("div");
    el.className = thought ? "dk-now dk-think" : "dk-now dk-fe";
    el.innerHTML = (thought ? "" : '<div class="dk-fe-head"><span class="dk-fe-ico"></span><span class="dk-fe-in"></span><span class="dk-fe-state"></span></div>') +
      `<div class="dk-fe-out${thought ? " is-tail" : ""}"><div class="dk-fe-lines"></div></div>`;
    if (!thought) [el._icoEl, el._inEl, el._state] = el.firstChild.children;
    el._box = el.lastChild;
    el._out = el._box.firstChild;
    el._out.dataset.empty = empty;
    // Faded at its cut edge once the output overflows the box: measured as
    // it changes size, after layout, rather than on every sync.
    el._fit = new ResizeObserver(() => el._box.classList.toggle("is-full", el._out.offsetHeight > el._box.clientHeight));
    el._fit.observe(el._out);
    return el;
  }

// A thought grows at its end: the text already drawn stays (a selection in it
// survives) and only the new words are added.
function syncPaneThought(el, text, grow) {
    const shown = el._text || "";
    if (text === shown) return;
    grow?.();
    if (shown && text.startsWith(shown) && el._out.lastChild?.nodeType === 3) el._out.lastChild.appendData(text.slice(shown.length));
    else el._out.textContent = text;
    el._text = text;
  }

// A call's line: what it was given, else what it is.
function paneInputHtml(trace) {
    const input = deps.callInput(trace);
    return input.input ? `${input.prompt ? '<span class="dk-fe-pr">$ </span>' : ""}${deps.escapeHtml(deps.ccFlat(input.input))}` : deps.escapeHtml(deps.toolRowLabel(trace));
  }

function paneStateHtml(state) {
    return state.html || (state.running && !state.text ? PANE_DOTS : deps.escapeHtml(state.text));
  }

// A script's calls, one row each like the call line, three or more finished
// reads in a row folded into one as in the transcript. A running command
// adds its newest output line under its row.
function paneScriptRows(calls) {
    const rows = [];
    const read = (t) => t?.name === "read_file" && !t.image && !paneState(t).running && !paneState(t).bad;
    for (let at = 0; at < calls.length;) {
      let end = at;
      while (read(calls[end])) end += 1;
      if (end - at >= 3) {
        const paths = calls.slice(at, end).map((t) => deps.shortToolPath(String(t.path || ""), t));
        const folder = (path) => path.slice(0, path.lastIndexOf("/") + 1);
        const where = paths.every((path) => folder(path) === folder(paths[0])) ? folder(paths[0]).replace(/\/$/, "") : "";
        rows.push({ id: `${calls[at].id}:reads`, done: true,
          html: `<span class="dk-fe-ico">${deps.rowIconHtml(calls[at])}</span><span class="dk-fe-in">Read ${end - at} files${where ? ` in ${deps.escapeHtml(where)}` : ""}</span><span class="dk-fe-state"></span>` });
        at = end;
        continue;
      }
      const trace = calls[at++];
      const state = paneState(trace);
      let html = `<span class="dk-fe-ico">${deps.rowIconHtml(trace)}</span><span class="dk-fe-in">${paneInputHtml(trace)}</span>` +
        `<span class="dk-fe-state${state.bad ? " is-bad" : ""}">${paneStateHtml(state)}</span>`;
      const body = state.running ? paneBody(trace) : null;
      const last = body?.tail ? body.lines.findLast((line) => line.replace(/<[^>]*>/g, "").trim()) : "";
      if (last) html += `<span class="dk-step-out">${last}</span>`;
      rows.push({ id: String(trace.id ?? ""), html, done: !state.running });
    }
    return rows;
  }

// Brings a script's rows up to date in place: a row that changed is redrawn,
// new rows are added at the end (the box slides up once it is full).
function syncPaneRows(el, rows, grow) {
    if (!el._rows) {
      grow?.();
      el._rows = new Map();
      el._html = null;
      el._lines = null;
      el._out.replaceChildren();
    }
    const seen = new Set();
    let prev = null;
    for (const row of rows) {
      seen.add(row.id);
      let node = el._rows.get(row.id);
      if (!node) {
        grow?.();
        node = document.createElement("div");
        node.className = "dk-step";
        el._rows.set(row.id, node);
      }
      if (node._html !== row.html) {
        node._html = row.html;
        node.innerHTML = row.html;
      }
      node.classList.toggle("is-done", row.done);
      const at = prev ? prev.nextSibling : el._out.firstChild;
      if (at !== node) el._out.insertBefore(node, at);
      prev = node;
    }
    for (const [id, node] of el._rows) {
      if (seen.has(id)) continue;
      node.remove();
      el._rows.delete(id);
    }
  }

// Brings a call's item up to date in place. A run_code that has made calls
// lists them under its line.
function syncPaneCall(el, trace, grow, calls = []) {
    const ico = deps.rowIconHtml(trace);
    if (el._ico !== ico) {
      el._ico = ico;
      el._icoEl.innerHTML = ico;
    }
    const text = paneInputHtml(trace);
    if (el._text !== text) {
      el._text = text;
      el._inEl.innerHTML = text;
    }
    const state = paneState(trace);
    const stateHtml = paneStateHtml(state);
    if (el._stateHtml !== stateHtml) {
      el._stateHtml = stateHtml;
      el._state.innerHTML = stateHtml;
      el._state.classList.toggle("is-bad", Boolean(state.bad));
      el.classList.toggle("is-done", !state.running);
    }
    const empty = paneEmpty(trace);
    if (el._out.dataset.empty !== empty) el._out.dataset.empty = empty;
    if (calls.length) {
      el._box.classList.add("is-tail");
      syncPaneRows(el, paneScriptRows(calls), grow);
      return;
    }
    const body = paneBody(trace);
    el._box.classList.toggle("is-tail", Boolean(body?.tail));
    if (body?.html) {
      if (el._html === body.html) return;
      grow?.();
      el._html = body.html;
      el._lines = null;
      el._out.innerHTML = body.html;
      return;
    }
    const lines = body?.lines || [];
    const old = el._lines || [];
    let same = 0;
    while (same < old.length && same < lines.length && old[same] === lines[same]) same += 1;
    if (!el._html && same === old.length && same === lines.length) return;
    grow?.();
    if (el._html) {
      el._html = null;
      el._out.replaceChildren();
    }
    // Output grows at its end: the lines already drawn stay (a selection in
    // them survives); only a grown last line and the new ones are drawn.
    if (same >= old.length - 1 && lines.length >= old.length) {
      if (same < old.length) el._out.lastChild.innerHTML = lines[same] || " ";
      for (let i = old.length; i < lines.length; i += 1) el._out.append(paneLine(lines[i]));
    } else el._out.replaceChildren(...lines.map(paneLine));
    el._lines = lines;
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
    fold.innerHTML = '<div class="dk-pane-in" role="group" aria-label="Running now"></div>';
    head.before(card);
    card.append(head, fold);
    head.setAttribute("aria-controls", fold.id);
    head.setAttribute("role", "button");
    head.tabIndex = 0;
    dock.classList.add("is-detailable");
    const pane = dock._pane = { head, fold, inner: fold.firstChild, item: null, key: "" };
    const toggle = (event) => {
      if (!event.target.closest("[data-dock-status]")) return;
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
    if (pane.head.getAttribute("aria-expanded") !== String(paneOpen)) pane.head.setAttribute("aria-expanded", String(paneOpen));
    if (dock.classList.contains("is-pane-open") === paneOpen) return;
    dock.classList.toggle("is-pane-open", paneOpen);
    pane.fold.inert = !paneOpen;
    // A followed transcript keeps its end in view while the pane folds.
    followLiveDockSlide(PANE_MS);
  }

// line: the status line as shown (live-status.js), with the call it names
// (key) and its phase.
function syncLivePane(dock, msg, line) {
    const pane = paneParts(dock);
    const text = line.phase === "thinking" ? paneThought(msg) : "";
    const trace = text || line.phase === "thinking" ? null : paneTrace(msg, line.key);
    const key = text ? `think:${msg.id}` : trace ? `call:${trace.id}` : `idle:${line.phase}`;
    const moving = paneOpen && dock.classList.contains("is-pane-open") && !deps.reduceMotion.matches;
    const fill = (grow) => {
      if (text) syncPaneThought(pane.item, text, grow);
      else if (trace) syncPaneCall(pane.item, trace, grow, paneCalls(msg, trace));
    };
    if (pane.key !== key) {
      // Another line: its item replaces the last, the height easing between.
      const h0 = moving ? pane.inner.offsetHeight : 0;
      pane.inner.getAnimations().forEach((a) => a.cancel());
      pane.item?._fit.disconnect();
      pane.item?.remove();
      pane.key = key;
      pane.item = paneItem(!trace, trace || text ? undefined : PANE_IDLE[line.phase]);
      pane.inner.append(pane.item);
      fill();
      if (moving) {
        const h1 = pane.inner.offsetHeight;
        if (h0 !== h1) pane.inner.animate([{ height: `${h0}px` }, { height: `${h1}px` }], { duration: PANE_MS, easing: PANE_EASE });
        pane.item?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: PANE_MS, easing: "ease-out" });
        followLiveDockSlide(PANE_MS);
      }
    } else if (pane.item) {
      // The same line: new output slides in from below once the box is full.
      // Measured only when there is new output to slide.
      const out = pane.item._out;
      let before = null;
      fill(() => { if (moving && before === null) before = out.getBoundingClientRect().top; });
      if (before !== null) {
        const dy = before - out.getBoundingClientRect().top;
        if (Math.abs(dy) > 0.5) {
          out.getAnimations().forEach((a) => a.cancel());
          out.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: PANE_MS, easing: PANE_EASE });
        }
      }
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

// The glyphs that tick: the "Jump to latest" pill's and every shown run
// spinner's, each kept as setConsoleSpin fills its slot.
function spinTicks(mark) {
    const slot = mark.parentElement;
    if (!mark.isConnected || !slot || slot.hidden) return false;
    if (slot === deps.latestSpin) return Boolean(deps.latestPill && !deps.latestPill.hidden);
    return "runSpin" in slot.dataset;
  }

function consoleSpinMarks() {
    const marks = [];
    for (const mark of spinMarks) {
      if (!mark.isConnected) spinMarks.delete(mark);
      else if (spinTicks(mark)) marks.push(mark);
    }
    return marks;
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
    if (el.matches(UNSHOWN_SPIN)) return;
    if (!spinMarks.has(mark)) {
      spinMarks.forEach((old) => { if (!old.isConnected) spinMarks.delete(old); });
      spinMarks.add(mark);
    }
    if (deps.consoleSpinTimer || deps.reduceMotion.matches || !spinTicks(mark)) return;
    deps.consoleSpinTimer = window.setInterval(() => {
      const marks = consoleSpinMarks();
      if (!marks.length) {
        stopConsoleSpin();
        return;
      }
      if (document.hidden) return;
      deps.consoleSpinFrame = (deps.consoleSpinFrame + 1) % CONSOLE_SPIN_FRAMES.length;
      marks.forEach((spin) => {
        FuseChatDOM.setText(spin, consoleSpinGlyph("runSpin" in spin.parentElement.dataset));
      });
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
      if (deps.liveDock.getAttribute("aria-hidden") !== "false") deps.liveDock.setAttribute("aria-hidden", "false");
      const groups = deps.groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = currentActivity(msg, groups, true);
      setLivePhase(deps.liveDock, status.phase);
      speakPhase(status.phase, msg);
      {
        const line = deps.formatConsoleLiveStatus(msg, groups);
        setLiveLabelText(deps.liveDockLabel, line.verb, line.meta, line.arg);
        syncLivePane(deps.liveDock, msg, line);
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

// Slots whose glyph this view never shows (styles/shell.css): a session row's,
// where the Chaos canvas spins instead, and a tab's, drawn as a dot.
const UNSHOWN_SPIN = ".dk-live, .dk-tab .chat-run-spin";
const spinMarks = new Set();

  return { setLiveLabelText, setLivePhase, paneTrace, paneThought, currentActivity, activityKind, liveDockMotionMs, followLiveDockSlide, hideLiveDock, consoleSpinGlyph, consoleSpinMarks, stopConsoleSpin, setConsoleSpin, syncLiveDock, activityLabel, CONSOLE_SPIN_FRAMES, CONSOLE_SPIN_STILL };
}

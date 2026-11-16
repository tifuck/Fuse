// live-dock presentation. Dependencies are live getters supplied by index.js.
import { mountChaos } from './chaos.js';

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
    // Clock, tokens, speed, after the status. Speed comes last, so it coming
    // and going never moves the others.
    if (!tail.querySelector(":scope > .dk-stat")) {
      tail.innerHTML = `<span class="dk-stat is-clock" title="Elapsed"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 5v3l2 1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><span></span></span>`
        + `<span class="dk-stat is-tokens" title="Tokens this round"><span></span></span>`
        + `<span class="dk-stat is-speed"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9 1 3 9h4l-1 6 6-8H8z" fill="currentColor"/></svg><span></span></span>`;
    }
    const stat = (name, text, title) => {
      const el = tail.querySelector(`:scope > .is-${name}`);
      if (el.lastChild.textContent !== text) el.lastChild.textContent = text;
      if (title !== undefined && el.title !== title) el.title = title;
      if (el.hidden !== !text) el.hidden = !text;
    };
    stat("clock", meta?.clock || "");
    stat("tokens", meta?.tokens || "");
    stat("speed", meta?.speed?.text || "", meta?.speed?.title || "");
    const hideTail = !meta;
    // Toggling hidden on every tick reflows the in-flow dock and can flip the
    // transcript extent by a rounding pixel; only touch it on a real change.
    if (tail.hidden !== hideTail) tail.hidden = hideTail;
    if ((verb.dataset.text ?? verb.textContent) === text) return;
    swapVerb(el, verb, text);
  }

// The status changing animates in one of six ways, picked at random once a
// turn (el.dataset.turn), fast. The status element keeps the new text (its
// shimmer, ellipsis and textContent untouched); the old text leaves in an
// aria-hidden copy laid over it, clipped to the line. The stats glide with
// the status's width.
const SWAP_KINDS = ["roll", "blur", "cascade", "wipe", "flip", "decode"];
const SWAP_MS = 180;
const SWAP_EASE = "cubic-bezier(.2, .8, .2, 1)";
function swapVerb(el, verb, text) {
    const old = verb.dataset.text ?? verb.textContent;
    const live = verb._swap;
    if (live) {
      live.anims.forEach((a) => a.cancel());
      live.ghost?.remove();
      cancelAnimationFrame(live.frame);
    }
    verb._swap = null;
    verb.style.webkitMaskImage = verb.style.maskImage = verb.style.webkitMaskSize = verb.style.maskSize = "";
    const w0 = verb.getBoundingClientRect().width;
    const box = { left: verb.offsetLeft, top: verb.offsetTop, width: verb.offsetWidth, height: verb.offsetHeight };
    verb.textContent = text;
    delete verb.dataset.text;
    if (!old || !text || !w0 || deps.reduceMotion.matches) return;
    if (el.dataset.swapTurn !== (el.dataset.turn || "")) {
      el.dataset.swapTurn = el.dataset.turn || "";
      el.dataset.swap = SWAP_KINDS[Math.floor(Math.random() * SWAP_KINDS.length)];
    }
    const kind = el.dataset.swap || "roll";
    const d = SWAP_MS;
    const run = { anims: [], ghost: null, frame: 0 };
    verb._swap = run;
    const go = (node, frames, opts) => {
      const a = node.animate(frames, { duration: d, easing: SWAP_EASE, ...opts });
      run.anims.push(a);
      return a;
    };
    const w1 = verb.getBoundingClientRect().width;
    if (Math.abs(w1 - w0) > 0.5) go(verb, [{ width: `${w0}px` }, { width: `${w1}px` }]);
    let out = null;
    if (kind !== "decode") {
      run.ghost = document.createElement("span");
      run.ghost.className = "dk-verb-ghost";
      run.ghost.setAttribute("aria-hidden", "true");
      Object.assign(run.ghost.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
      out = document.createElement("span");
      out.textContent = old;
      run.ghost.append(out);
      el.append(run.ghost);
    }
    let landed;
    if (kind === "roll") {
      go(out, [{ transform: "none", opacity: 1 }, { transform: "translateY(-100%)", opacity: 0 }], { fill: "forwards" });
      landed = go(verb, [{ transform: "translateY(100%)", clipPath: "inset(-100% 0 100% 0)", opacity: 0 }, { transform: "none", clipPath: "inset(0)", opacity: 1 }]);
    } else if (kind === "blur") {
      go(out, [{ opacity: 1, filter: "blur(0)" }, { opacity: 0, filter: "blur(4px)" }], { duration: d * 0.8, easing: "ease-out", fill: "forwards" });
      landed = go(verb, [{ opacity: 0, filter: "blur(4px)", transform: "scale(.98)" }, { opacity: 1, filter: "blur(0)", transform: "none" }]);
    } else if (kind === "flip") {
      go(out, [{ transformOrigin: "50% 0", transform: "perspective(300px) rotateX(0)", opacity: 1 }, { transformOrigin: "50% 0", transform: "perspective(300px) rotateX(80deg)", opacity: 0 }], { duration: d * 0.6, easing: "ease-in", fill: "forwards" });
      landed = go(verb, [
        { transformOrigin: "50% 100%", transform: "perspective(300px) rotateX(-80deg)", opacity: 0, offset: 0 },
        { transformOrigin: "50% 100%", transform: "perspective(300px) rotateX(-80deg)", opacity: 0, offset: 0.35 },
        { transformOrigin: "50% 100%", transform: "perspective(300px) rotateX(0)", opacity: 1 },
      ], { duration: d * 1.3 });
    } else if (kind === "wipe") {
      go(out, [{ opacity: 1 }, { opacity: 0 }], { duration: d * 0.6, easing: "ease-out", fill: "forwards" });
      verb.style.webkitMaskImage = verb.style.maskImage = "linear-gradient(90deg, #000 40%, transparent 60%)";
      verb.style.webkitMaskSize = verb.style.maskSize = "250% 100%";
      landed = go(verb, [{ webkitMaskPosition: "100% 0", maskPosition: "100% 0" }, { webkitMaskPosition: "0 0", maskPosition: "0 0" }], { duration: d * 1.2 });
    } else if (kind === "cascade") {
      go(out, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-30%)" }], { duration: d * 0.5, easing: "ease-out", fill: "forwards" });
      // Letters are their own boxes, which a text-clipped shimmer cannot
      // paint through: they rise in a plain colour, then the text rejoins.
      const chars = [...text];
      const step = Math.min(10, (d * 0.8) / chars.length);
      verb.replaceChildren(...chars.map((c) => Object.assign(document.createElement("span"), { className: "dk-verb-ch", textContent: c })));
      const rises = [...verb.children].map((ch, i) => go(ch, [{ opacity: 0, transform: "translateY(.55em)", filter: "blur(2px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }], { delay: i * step, fill: "backwards" }));
      landed = rises[rises.length - 1];
    } else {
      // decode: random letters settle into the text, left to right.
      verb.dataset.text = text;
      const glyphs = "abcdefghijklmnopqrstuvwxyz";
      const target = [...text];
      const t0 = performance.now();
      const total = d * 1.4;
      const tick = (now) => {
        if (verb._swap !== run) return;
        const p = Math.min(1, (now - t0) / total);
        const fixed = Math.floor(p * target.length);
        verb.textContent = p < 1 ? target.map((c, i) => (i < fixed || /[\s.…]/.test(c) ? c : glyphs[(Math.random() * 26) | 0])).join("") : text;
        if (p < 1) run.frame = requestAnimationFrame(tick);
        else {
          delete verb.dataset.text;
          verb._swap = null;
        }
      };
      run.frame = requestAnimationFrame(tick);
      return;
    }
    landed?.finished.then(() => {
      if (verb._swap !== run) return;
      run.anims.forEach((a) => a.cancel());
      run.ghost?.remove();
      verb.style.webkitMaskImage = verb.style.maskImage = verb.style.webkitMaskSize = verb.style.maskSize = "";
      if (kind === "cascade") verb.textContent = text;
      verb._swap = null;
    }, () => {});
  }

// The running command's line opens to what it runs, where, its deadline and
// its newest output. It closes by itself when another call takes the line.
const detail = { open: false, id: "", trace: null };
function liveDetailHtml(trace) {
    const esc = deps.escapeHtml;
    const where = [trace.host, trace.cwd].filter(Boolean).join(" · ");
    const deadline = deps.ccTimeoutNote(trace).replace(/^\(timeout: (.*)\)$/, "times out after $1");
    const out = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
    const tail = out ? out.split("\n").slice(-8).join("\n") : "";
    return `<div class="dk-live-cmd">$ ${esc(deps.displayCommand(trace))}</div>` +
      (where || deadline ? `<div class="dk-live-where">${esc([where, deadline].filter(Boolean).join(" · "))}</div>` : "") +
      `<pre class="chat-console-out dk-live-out">${tail ? deps.renderAnsi(tail) : '<span class="dk-live-wait">Waiting for output</span>'}</pre>`;
  }
function renderLiveDetail(dock) {
    let panel = dock.querySelector(":scope > [data-live-detail]");
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "dk-live-detail";
      panel.dataset.liveDetail = "";
      dock.append(panel);
    }
    const html = detail.open && detail.trace ? liveDetailHtml(detail.trace) : "";
    if (panel._html !== html) {
      panel._html = html;
      panel.innerHTML = html;
    }
    if (panel.hidden !== !html) panel.hidden = !html;
    dock.classList.toggle("is-detail-open", Boolean(html));
  }
function syncLiveDetail(dock, trace) {
    if (!dock) return;
    const head = dock.querySelector("[data-dock-status]");
    if (!dock._detailBound) {
      dock._detailBound = true;
      const toggle = (event) => {
        if (!detail.trace || !event.target.closest("[data-dock-status]")) return;
        if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        detail.open = !detail.open;
        head.setAttribute("aria-expanded", String(detail.open));
        renderLiveDetail(dock);
      };
      dock.addEventListener("click", toggle);
      dock.addEventListener("keydown", toggle);
    }
    const id = trace ? String(trace.id || deps.executionIdentity(trace)) : "";
    if (id !== detail.id) Object.assign(detail, { open: false, id });
    detail.trace = trace;
    dock.classList.toggle("is-detailable", Boolean(trace));
    if (trace) {
      if (head.getAttribute("role") !== "button") {
        head.setAttribute("role", "button");
        head.tabIndex = 0;
      }
      head.setAttribute("aria-expanded", String(detail.open));
    } else if (head.hasAttribute("role")) {
      head.removeAttribute("role");
      head.removeAttribute("tabindex");
      head.removeAttribute("aria-expanded");
    }
    renderLiveDetail(dock);
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

function followLiveDockSlide() {
    if (!deps.thread || !deps.stickToBottom) return;
    if (deps.liveDockFollowFrame) cancelAnimationFrame(deps.liveDockFollowFrame);
    const until = performance.now() + liveDockMotionMs();
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
    if (deps.liveDockSpin) {
      deps.liveDockSpin.replaceChildren();
      delete deps.liveDockSpin.dataset.spinSet;
    }
    deps.syncLatest();
  }

// A neutral braille spinner; sidebar marks use the same frames.
function consoleSpinGlyph() {
    return deps.reduceMotion.matches ? CONSOLE_SPIN_STILL : CONSOLE_SPIN_FRAMES[deps.consoleSpinFrame % CONSOLE_SPIN_FRAMES.length];
  }

function consoleSpinMarks() {
    return [[deps.liveDock, deps.liveDockSpin], [deps.agentLiveDock, deps.agentLiveSpin], [deps.latestPill, deps.latestSpin]]
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
    // The status lines lead with the Chaos spinner (chaos.js), not a glyph.
    if (el === deps.liveDockSpin || el === deps.agentLiveSpin) {
      mountChaos(el, el === deps.liveDockSpin ? "dock" : "agent-dock");
      return;
    }
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
        if (deps.liveDockLabel) deps.liveDockLabel.dataset.turn = String(msg.id || "");
        setLiveLabelText(deps.liveDockLabel, line.verb, line.meta, line.arg);
        syncLiveDetail(deps.liveDock, line.command || null);
      }
      if ((deps.liveDockSpin)) setConsoleSpin(deps.liveDockSpin);
      else if (deps.liveDockSpin) deps.applySpinEl(deps.liveDockSpin, deps.activitySpinSet(msg), "dock", true, msg._spinOrigin);
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

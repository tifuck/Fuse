// call-progress presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function ccCallClockHtml(trace, presentation) {
    const name = trace && trace.name;
    if (!name || deps.isSearchTool(name) || name === "spawn_subagent") return "";
    // A background launch returns at once; its clock runs only while its
    // process does. (A Wait on it is flagged background too and keeps its own.)
    // Its finish (backgroundResult) shows how long the command ran.
    if (name === "run_terminal_command" && deps.toolBackground(trace) && !presentation.running && !trace.backgroundResult) return "";
    // A monitor's call returns at once and then keeps watching, so it ticks
    // while it does. Every running clock anchors on the server's started_ms.
    if (presentation.running || (name === "monitor" && deps.executionPending(trace))) {
      const since = deps.ccSince(trace);
      return `<span class="chat-console-took" data-cc-clock="elapsed" data-since="${since}">${deps.ccElapsedText(since)}</span>`;
    }
    // A settled Await's line already says how long ("Agent "…" finished ·
    // 41s", or "still running"); a header clock beside it would be a second,
    // different number.
    if (name === "get_subagent_output") return "";
    // A Wait's elapsed_ms and duration_ms belong to the command it waited on;
    // its own time is the call's.
    const ms = Number(name === "run_terminal_command" && trace.backgroundResult
      ? trace.execution_duration_ms ?? trace.latency_ms ?? trace.duration_ms
      : name === "run_terminal_command"
      ? trace.execution_duration_ms ?? trace.call_duration_ms ?? trace.duration_ms
      : name === "get_command_output" ? trace.call_duration_ms : trace.call_duration_ms ?? trace.duration_ms);
    return Number.isFinite(ms) && ms >= 2000 ? `<span class="chat-console-took"> ${deps.escapeHtml(deps.ccDuration(ms))}</span>` : "";
  }

function ccCallName(item) {
    return CC_CALL_NAMES[item && item.name] || String((item && item.name) || "tool");
  }

function ccCallSummary(item) {
    if (!item) return "";
    const clip = (value) => deps.ccClip(deps.ccFlat(value), 50);
    switch (item.name) {
      case "run_terminal_command": return clip(item.description || item.command);
      case "read_file": case "write_file": case "search_replace": case "list_dir": return item.path ? deps.compactPath(item.path, item, 50) : "";
      case "grep": case "glob": return clip(item.pattern);
      case "read_url": return clip(item.url);
      case "web_search": return clip(item.query);
      case "spawn_subagent": case "monitor": return clip(item.description);
      default: return clip(item.description || item.command || item.path || item.pattern || item.url || item.query || "");
    }
  }

function ccCallKind(item) {
    const name = item && item.name;
    if (name === "read_file") return "read";
    if (name === "grep" || name === "glob") return "search";
    if (name === "run_terminal_command") {
      const kind = deps.ccShellKind(item.command || "");
      if (kind.isSearch) return "search";
      if (kind.isRead) return "read";
    }
    return "";
  }

function ccCountsText(search, read, active) {
    const parts = [];
    const add = (verb, rest) => parts.push(`${parts.length ? verb : verb.charAt(0).toUpperCase() + verb.slice(1)} ${rest}`);
    if (search) add(active ? "searching for" : "searched for", `${search} ${search === 1 ? "pattern" : "patterns"}`);
    if (read) add(active ? "reading" : "read", `${read} ${read === 1 ? "file" : "files"}`);
    return parts.join(", ") + (active ? "…" : "");
  }

function ccAgentLastCall(recent) {
    const items = Array.isArray(recent) ? recent : [];
    let search = 0;
    let read = 0;
    for (let i = items.length - 1; i >= 0; i -= 1) {
      const kind = ccCallKind(items[i]);
      if (kind === "search") search += 1;
      else if (kind === "read") read += 1;
      else break;
    }
    if (search + read >= 2) return ccCountsText(search, read, true);
    const done = [...items].reverse().find((item) => item && item.status && item.status !== "running");
    if (!done) return "";
    const summary = ccCallSummary(done);
    return summary ? `${ccCallName(done)}: ${summary}` : ccCallName(done);
  }

function ccCallLine(item) {
    const name = ccCallName(item);
    const arg = item.name === "run_terminal_command" ? deps.ccClip(deps.ccFlat(item.command), 80) : ccCallSummary(item);
    return arg ? `${name}(${arg})` : name;
  }

function ccAgentProgressHtml(trace) {
    const items = Array.isArray(trace.agent_recent) ? trace.agent_recent : [];
    if (!items.length) return "";
    const entries = [];
    items.forEach((item) => {
      const kind = ccCallKind(item);
      const last = entries[entries.length - 1];
      if (kind && last && last.counts) {
        last.counts[kind] += 1;
        last.calls += 1;
        return;
      }
      if (kind) entries.push({ counts: { search: kind === "search" ? 1 : 0, read: kind === "read" ? 1 : 0 }, calls: 1 });
      else entries.push({ item, calls: 1 });
    });
    const shown = entries.slice(-3);
    const shownCalls = shown.reduce((sum, entry) => sum + entry.calls, 0);
    const total = Math.max(Number(trace.agent_tool_uses) || 0, items.length);
    const hidden = Math.max(0, total - shownCalls);
    const rows = shown.map((entry, index) => {
      const text = entry.counts
        ? ccCountsText(entry.counts.search, entry.counts.read, index === shown.length - 1)
        : ccCallLine(entry.item);
      return `<div class="chat-console-agent-step">${deps.escapeHtml(text)}</div>`;
    }).join("");
    return `<div class="chat-console-progress is-agent">${rows}` +
      (hidden > 0 ? `<div class="chat-console-more">… +${hidden} ${hidden === 1 ? "tool use" : "tool uses"}</div>` : "") + `</div>`;
  }

function ccAgentType(trace) {
    const type = String((trace && trace.subagent_type) || "").trim();
    if (!type || type === "general-purpose") return "Agent";
    return type.charAt(0).toUpperCase() + type.slice(1);
  }

function ccAgentDoneText(trace) {
    const uses = Number(trace.agent_tool_uses);
    const tokens = Number(trace.agent_tokens);
    const work = Number(trace.agent_work_ms);
    const parts = [];
    if (Number.isFinite(uses) && uses >= 0 && trace.agent_tool_uses != null) parts.push(uses === 1 ? "1 tool use" : `${uses} tool uses`);
    if (Number.isFinite(tokens) && tokens > 0) parts.push(`${deps.ccTokens(tokens)} tokens`);
    if (Number.isFinite(work) && work >= 0 && trace.agent_work_ms != null) parts.push(deps.ccDuration(Math.max(1000, work)));
    const speed = deps.ccGenSpeed(trace.agent_gen_out, trace.agent_gen_ms, trace.agent_gen_est > 0);
    if (speed) parts.push(speed.text);
    return parts.length ? `Done (${parts.join(", ")})` : "Done";
  }

function consoleOnlyFold(trace, presentation, verbose) {
    const { running, failed, interrupted } = presentation;
    const name = trace && trace.name;
    if (interrupted && !running) return ccInterruptedFold(trace, name === "run_terminal_command");
    // Wait (Fuse's get_command_output) blocks on a background command; Claude
    // Code has no such call, so it reads as what it does.
    if (name === "get_command_output" && running) return { status: "Waiting for output…", preview: "", full: "" };
    if (running && !deps.toolBackground(trace) && deps.ccLiveText(trace)) {
      // The header says what it is doing. The command goes under it only
      // when a description took its place, with no clock of its own.
      const hint = name === "run_terminal_command" && deps.ccFlat(trace.description) ? deps.ccShellHint(trace) : "";
      if (!hint) return { status: "", preview: "", full: "" };
      // The command's own output, not a count of it: the last few lines, the
      // newest last, with the deadline at the end of the last one. A long last
      // line is cut short so the deadline stays on it.
      const out = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
      const tail = out ? out.split("\n").slice(-4).map((line) => deps.ccClip(deps.ccFlat(line), 200)) : [];
      const note = deps.ccTimeoutNote(trace);
      const rows = [...hint.split("\n"), ...tail].slice(-1);
      const html = rows.map((line, i) => `<div class="chat-console-dim">${deps.escapeHtml(line)}${i === rows.length - 1 && note ? ` <span class="chat-console-timeout">${deps.escapeHtml(note)}</span>` : ""}</div>`).join("");
      return { status: "", preview: `<div class="chat-console-progress">${html}</div>`, full: "" };
    }
    if (name === "spawn_subagent") {
      const background = deps.toolBackground(trace);
      if (running) {
        if (background) return { status: "Backgrounded agent", preview: "", full: "" };
        return { status: ccAgentLastCall(trace.agent_recent) || "Initializing…", preview: "", full: ccAgentProgressHtml(trace) };
      }
      if (failed) {
        const fold = deps.ccErrorFold(String(trace.error || trace.summary || "Subagent failed").split("\n"));
        return { ...fold, full: ccAgentProgressHtml(trace) + fold.full };
      }
      const report = deps.consoleDetailHtml(trace, false, false);
      return { status: ccAgentDoneText(trace), preview: "", full: deps.consoleSameText(report, "") ? "" : report };
    }
    if (name === "run_terminal_command") {
      if (deps.toolBackground(trace)) {
        if (running && !["interrupted", "cancelled"].includes(trace.status)) return { status: "Running in the background", preview: "", full: "", keep: true };
        if (["interrupted", "cancelled"].includes(trace.status)) return { ...ccInterruptedFold(trace, true), keep: true };
        // Ended: say how, not what the launch said, then its newest output
        // line. (Interrupted is above.)
        const out = deps.consoleDetailHtml(trace, false, false);
        const exit = deps.shellExitCode(trace);
        const code = exit !== null ? ` (exit ${exit})` : "";
        const status = failed ? (deps.executionState(trace) === "timed_out" ? "Timed out in the background" : `Failed in the background${code}`)
          : `Completed in the background${code}`;
        const body = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
        const line = body ? deps.consoleLastLine(body.split("\n")) : null;
        return {
          status: line ? `${status} — ${line.text}` : status,
          statusHtml: line ? `${deps.escapeHtml(status)}<span class="chat-console-dim"> — </span>${line.html}` : undefined,
          preview: "", full: deps.consoleSameText(out, "") || (line && !line.more) ? "" : out, keep: true,
        };
      }
      const raw = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output)
        : [trace.preview, trace.summary].find((value) => { const text = String(value || "").trim(); return text && text !== "still running" && text !== "background task started"; }) || "";
      const lines = String(raw).replace(/\s+$/, "").split("\n");
      const empty = !String(raw).trim();
      const exit = deps.shellExitCode(trace);
      const meaning = deps.ccExitMeaning(deps.displayCommand(trace), exit);
      if (failed || meaning.isError) {
        if (exit !== null && exit !== 0) return deps.ccErrorFold([`Exit code ${exit}`, ...(empty ? [] : lines)], true);
        const failure = ccFailure(trace);
        return deps.ccErrorFold([deps.ccFlat(failure.error), ...(failure.output ? failure.output.split("\n") : [])], true);
      }
      // No file diff here: a working-tree snapshot around the call cannot tell
      // its edits from another agent's in the same repository.
      if (empty) return { status: meaning.message || "(No output)", preview: "", full: "" };
      // Folded, one line: the newest output line; the open prints it all.
      const line = deps.consoleLastLine(lines);
      return { status: line.text, statusHtml: line.html, preview: "", full: line.more ? deps.consoleDetailHtml(trace, false, false) : "" };
    }
    if (name === "read_file" && !running && !failed && !interrupted) {
      const body = deps.consoleFileBody(trace);
      if (body) { const note = deps.consoleTruncationNote(trace); return { status: deps.consoleResultLine(trace, presentation), preview: "", full: `<div class="chat-console-out is-file">${deps.fileBodyHtml(trace, body, 10)}</div>${note}`, keep: Boolean(note) }; }
    }
    if (name === "run_terminal_command" && trace.backgroundResult && !running) {
      const body = typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
      if (body) return { status: deps.consoleResultLine(trace, presentation), preview: "", full: deps.consoleDetailHtml(trace, false, false), keep: true };
    }
    if (failed && name !== "run_terminal_command" && name !== "get_command_output") {
      return deps.ccErrorFold(ccFailure(trace).error.split("\n"));
    }
    return null;
  }

// An interrupted command's kept output, then why it stopped: the output came
// first, so the note under it reads as where the run ended.
function ccInterruptedFold(trace, shell) {
    const text = ccInterruptedText(trace);
    const kept = shell && typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
    if (!kept) return { status: text, preview: "", full: "", keep: false };
    // Folded, one line: why it stopped; the open prints the kept output.
    return { status: text, preview: "", full: deps.consoleDetailHtml(trace, false, false), keep: true };
  }

// What a failed call prints as its error, and what as its output. A call that
// failed before anything ran (no command execution behind it) keeps the
// model's result text as its output, and that text is the error: printed
// under the error it read as the same failure twice. When one contains the
// other the fuller one is the error, since it is what the model was told. A
// command's own output stays its output unless it only repeats the error.
function ccFailure(trace) {
    const error = String((trace && (trace.error || trace.summary)) || "").trim();
    // Traces saved before calls kept their output carry only the preview.
    const stored = trace && (typeof trace.output === "string" ? trace.output
      : ["still running", "background task started"].includes(String(trace.preview || "").trim().toLowerCase()) ? "" : String(trace.preview || ""));
    const output = stored ? deps.cleanToolOutput(trace, stored).replace(/\s+$/, "").replace(/^\s*\n/, "") : "";
    const key = (text) => deps.ccFlat(text).toLowerCase().replace(/^(tool )?error: /, "");
    const ran = Boolean(deps.executionState(trace));
    if (!output) return { error: error || "failed", output: "" };
    if (!error) return ran ? { error: "failed", output } : { error: output, output: "" };
    if (key(output) === key(error) || (!ran && key(error).includes(key(output)))) return { error, output: "" };
    if (!ran && key(output).includes(key(error))) return { error: output, output: "" };
    return { error, output };
  }

function ccInterruptedText(trace) {
    const head = "Stopped";
    const error = String((trace && trace.error) || "").toLowerCase();
    const state = trace ? deps.executionState(trace) : "";
    if (/connector restarted/.test(error)) {
      return `${head}: ${trace.host ? `the connector on ${trace.host} restarted` : "the connector restarted"}`;
    }
    if (/connection lost/.test(error)) {
      return `${head}: ${trace.host ? `${trace.host} lost its connection` : "the connection was lost"}`;
    }
    if (/\b(agent|connector|host) is offline\b|went offline|disconnected/.test(error)) {
      return `${head}: ${trace.host ? `${trace.host} went offline` : "the machine went offline"}`;
    }
    if (state === "cancelled" || /cancel/.test(error) || (trace && trace.status === "cancelled")) {
      const who = /stopped by the user/.test(error) ? `${head} by user` : head;
      return trace && trace.termination_confirmed === false && trace.name === "run_terminal_command"
        ? `${who}: the process may still be running`
        : who;
    }
    if (state === "lost") return `${head}: the result was lost`;
    return `${head}: the run ended before it finished`;
  }

function ccCleanBackgroundEnd(trace) {
    if (!trace || trace.name !== "run_terminal_command" || !trace.backgroundResult || trace.status !== "done") return false;
    const state = deps.executionState(trace);
    if (["cancelled", "interrupted", "lost", "timed_out"].includes(state)) return false;
    const exit = deps.shellExitCode(trace);
    return exit === null || exit === 0;
  }

// A notice outside a burst reads like a call row: verb, subject, leader,
// result. toggle: the row opens what hangs under it.
function tailNoteHtml(verb, subjectHtml, resultHtml, toggle, extra = "") {
    const call = `<span class="tail-note-verb">${deps.escapeHtml(verb)}</span><span class="tail-note-subject">${subjectHtml}</span>`;
    return `<div class="chat-step-head">` +
      (toggle
        ? `<button type="button" class="chat-step-toggle chat-console-notice-text tail-note-call" data-step-toggle aria-expanded="false">${call}</button>`
        : `<span class="chat-console-notice-text tail-note-call">${call}</span>`) +
      `<span class="tail-note-leader" aria-hidden="true"></span>` +
      (resultHtml ? `<span class="tail-note-result">${resultHtml}</span>` : "") + extra + `</div>`;
  }

function consoleMonitorEventHtml(trace, key) {
    const body = String(trace.summary || trace.preview || "").replace(/\r\n?/g, "\n").replace(/^\s*\n|\s+$/g, "");
    const lines = body ? body.split("\n") : [];
    const description = deps.ccFlat(trace.description);
    const label = description || deps.ccFlat(trace.url) || deps.ccFlat(deps.displayCommand(trace)) || "monitor";
    const row = (headHtml, hang) => (
      `<li class="chat-step is-console-notice is-plain${hang ? " is-event" : ""}" data-execution="${deps.escapeHtml(deps.executionIdentity(trace))}"${hang ? ` data-step="${deps.escapeHtml(key)}"` : ""}>` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">${headHtml}${hang}</div></li>`
    );
    const stdout = (rows) => `<pre class="chat-console-out is-stdout">${rows.map((line) => `<div class="chat-console-stdout">${deps.renderAnsi(line) || " "}</div>`).join("")}</pre>`;
    const hang = (inner) => `<div class="chat-console-hang is-expanded"><div class="chat-console-hang-body">${inner}</div></div>`;
    const reveal = `<div class="chat-step-reveal"><div class="chat-step-reveal-inner">${hang(stdout(lines))}</div></div>`;
    if (!description) {
      const firstAt = lines.findIndex((line) => line.trim());
      const first = firstAt >= 0 ? lines[firstAt].trim() : "event";
      const rest = firstAt >= 0 ? lines.length - firstAt - 1 : 0;
      const head = (toggle) => tailNoteHtml("monitor", deps.escapeHtml(deps.ccFlat(first)), deps.escapeHtml(label), toggle);
      if (!rest) return row(head(false), "");
      return row(
        head(true),
        `<div class="chat-console-hang is-event-more" data-step-toggle><div class="chat-console-hang-body"><span class="chat-console-dim">… +${rest} ${rest === 1 ? "line" : "lines"}</span></div></div>` + reveal
      );
    }
    const head = (toggle) => tailNoteHtml("monitor", deps.escapeHtml(description), "event", toggle);
    if (!lines.length) return row(head(false), "");
    // The newest lines of the event, under the count of the older ones.
    const show = lines.length === 4 ? 4 : 3;
    const hidden = Math.max(0, lines.length - show);
    const preview = hang((hidden ? `<div class="chat-console-dim is-event-more" data-step-toggle>… +${hidden} ${hidden === 1 ? "line" : "lines"}</div>` : "") + stdout(lines.slice(hidden)));
    if (!hidden) return row(head(false), preview);
    return row(head(true), `<div class="is-event-preview">${preview}</div>` + reveal);
  }

// A delivered subagent report as one row: agent, its task, how it ended. The
// first lines of the report sit under it; the rest opens in place, and Open
// opens the agent's pane.
function consoleAgentNoticeHtml(trace, key) {
    const label = deps.ccFlat(trace.description) || "subagent";
    const stopped = trace.status === "interrupted";
    const failed = trace.status === "error";
    const tone = stopped ? "gold" : failed ? "red" : "green";
    const body = failed
      ? deps.ccClip(deps.providerErrorLine(deps.ccFlat(trace.error)).replace(/^Subagent failed:\s*/i, "") || "Unknown error", 160)
      : String(trace.summary || "").replace(/\r\n?/g, "\n").replace(/^\s*\n|\s+$/g, "");
    const lines = body ? body.split("\n") : [];
    // The preview is the report's first lines of text; the whole report,
    // blank lines included, opens in place.
    const filled = lines.filter((line) => line.trim());
    const show = filled.length === 3 ? 3 : 2;
    const hidden = Math.max(0, filled.length - show);
    const text = (rows) => `<div class="chat-console-report">${rows.map((line) => `<div>${deps.escapeHtml(line) || " "}</div>`).join("")}</div>`;
    const open = trace.child_id
      ? `<button type="button" class="chat-step-open" data-open-agent="${deps.escapeHtml(trace.child_id)}" aria-label="Open agent">Open<span class="chat-open-target">&nbsp;agent ↗</span></button>`
      : "";
    const preview = lines.length
      ? `<div class="chat-console-hang is-expanded${hidden ? " is-event-preview" : ""}"><div class="chat-console-hang-body">${text(filled.slice(0, show))}` +
        (hidden ? `<div class="chat-console-dim is-event-more" data-step-toggle>… +${hidden} ${hidden === 1 ? "line" : "lines"}</div>` : "") + `</div></div>`
      : "";
    const reveal = hidden
      ? `<div class="chat-step-reveal"><div class="chat-step-reveal-inner"><div class="chat-console-hang is-expanded"><div class="chat-console-hang-body">${text(lines)}</div></div></div></div>`
      : "";
    return (
      `<li class="chat-step is-console-notice is-agent-report is-${tone}${hidden ? " is-event" : ""}" data-agent-report="${deps.escapeHtml(trace.child_id || "")}"${hidden ? ` data-step="${deps.escapeHtml(key)}"` : ""}>` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">${tailNoteHtml("agent", deps.escapeHtml(label), stopped ? "stopped" : failed ? "failed" : "finished", Boolean(hidden), open)}${preview}${reveal}</div></li>`
    );
  }

// Result rows for background work handed to the main agent: a wake turn's
// message (msg.autoSteps) or a delivery segment inside a reply. Every
// delivery renders through here, so the same result always looks the same.
function consoleAutoStepsHtml(msg) {
    const traces = msg.autoSteps || [];
    const out = [];
    for (let i = 0; i < traces.length; i += 1) {
      const trace = traces[i];
      if (trace.repeat) continue;
      if (trace.agentReport) {
        out.push(consoleAgentNoticeHtml(trace, "wake-" + (msg.id || "") + "-" + i));
        continue;
      }
      if (trace.name === "monitor" && trace.backgroundResult && trace.status === "running") {
        out.push(consoleMonitorEventHtml(trace, "wake-" + (msg.id || "") + "-" + i));
        continue;
      }
      if (trace.backgroundResult && trace.status !== "running") {
        let j = i;
        while (ccCleanBackgroundEnd(traces[i]) && j + 1 < traces.length && ccCleanBackgroundEnd(traces[j + 1])) j += 1;
        if (j > i) {
          out.push(
            `<li class="chat-step is-console-notice is-green">` +
            `<span class="chat-step-node" aria-hidden="true"></span>` +
            `<div class="chat-step-main">${tailNoteHtml("run", `${j - i + 1} background commands`, "completed", false)}</div></li>`
          );
          i = j;
          continue;
        }
        // A finished background command prints as the call it is; a monitor's end stays a notice.
        out.push(trace.name === "monitor" ? deps.consoleWakeNoticeHtml(trace) : deps.toolStepHtml(trace, "wake-" + (msg.id || "") + "-" + i, false, false, null));
        continue;
      }
      out.push(deps.toolStepHtml(trace, "wake-" + (msg.id || "") + "-" + i, false, false, null));
    }
    return out.join("");
  }

// Subagents started together: a header line with one tick an agent, then one
// aligned row each (its type as the verb, its task, how it ended).
function ccAgentsGroupHtml(agents, live) {
    const rows = agents.map(({ entry }) => {
      const trace = entry.trace;
      const presentation = deps.toolPresentation(trace, live);
      return { trace, presentation, background: deps.toolBackground(trace), type: ccAgentType(trace) };
    });
    const unresolved = rows.some((row) => row.presentation.running);
    const errored = rows.some((row) => row.presentation.failed);
    const allBackground = rows.every((row) => row.background);
    const n = rows.length;
    const failures = rows.filter((row) => row.presentation.failed).length;
    const ticks = rows.map(({ presentation }) =>
      `<i${presentation.running ? ' class="is-live"' : presentation.failed ? ' class="is-bad"' : presentation.interrupted ? ' class="is-stop"' : ""}></i>`).join("");
    const head = `<span class="tail-ticks" aria-hidden="true">${ticks}</span>` +
      `<span class="tail-agents-count"><b>${n}</b> ${allBackground ? "background agents" : "agents"} ${unresolved ? "running" : allBackground ? "launched" : "finished"}</span>` +
      (failures ? `<span class="tail-agents-stat tail-fail">${failures} failed</span>` : "");
    const items = rows.map((row) => {
      const { trace, presentation, background } = row;
      const description = deps.ccFlat(deps.agentSubject(trace) || trace.description || "");
      const resolvedBackground = background && !presentation.running;
      const uses = Number(trace.agent_tool_uses);
      const tokens = Number(trace.agent_tokens);
      // A running child shows its clock: its counts are still moving. A
      // settled one says how it ended: a background child's report comes
      // with its wake, so its row keeps just the state and how long it took.
      const base = Number(trace.agent_work_ms);
      const hasWork = Number.isFinite(base) && base >= 0;
      const liveClock = presentation.running && hasWork;
      // A rebuilt trace (persisted base, no snap) must never freeze at
      // data-since=0: render it ticking from this paint. The snap stays off
      // the trace so the next poll anchors the pair at server work_ms.
      const since = trace._workSnap || Date.now();
      const took = hasWork ? ` ${deps.ccDuration(Math.max(1000, base))}` : "";
      const speed = presentation.running ? null : deps.ccGenSpeed(trace.agent_gen_out, trace.agent_gen_ms, trace.agent_gen_est > 0);
      const rate = speed ? `, <span class="chat-console-speed" title="${deps.escapeHtml(speed.title)}">${deps.escapeHtml(speed.text)}</span>` : "";
      const meta = resolvedBackground || liveClock ? ""
        : `${Number.isFinite(uses) ? uses : 0} ${uses === 1 ? "tool use" : "tool uses"}${Number.isFinite(tokens) && tokens > 0 ? `, ${deps.ccTokens(tokens)} tokens` : ""}${rate}`;
      const result = liveClock
        ? `<span data-cc-clock="work" data-base="${Math.round(base)}" data-since="${since}">${deps.ccDuration(Math.max(1000, base))}</span>`
        : presentation.running ? "running"
        : presentation.failed ? `failed${resolvedBackground ? "" : took}`
        : presentation.interrupted ? `stopped${resolvedBackground ? "" : took}`
        : `finished${took}`;
      // What a running child is doing goes under its row.
      const status = presentation.running ? (background ? "Running in the background" : ccAgentLastCall(trace.agent_recent) || "Initializing…") : "";
      return (
        `<li class="chat-console-agent${presentation.running ? " is-running" : ""}${presentation.failed ? " is-error" : ""}${presentation.interrupted && !presentation.running ? " is-stopped" : ""}" data-open-agent="${deps.escapeHtml(trace.child_id || "")}">` +
        `<div class="tail-agent-row"><span class="tail-note-call"><span class="tail-note-verb">${deps.escapeHtml(row.type.toLowerCase())}</span>` +
        `<span class="tail-note-subject">${deps.escapeHtml(description || row.type)}</span>${meta ? `<span class="tail-note-meta">${meta}</span>` : ""}</span>` +
        `<span class="tail-note-leader" aria-hidden="true"></span><span class="tail-note-result">${result}</span></div>` +
        (status ? `<div class="tail-agent-doing">${deps.escapeHtml(status)}</div>` : "") + `</li>`
      );
    }).join("");
    const state = unresolved ? `is-running${errored ? " is-errored" : ""}` : errored ? "is-error" : "is-done";
    return (
      `<li class="chat-step is-console-group is-console-agents ${state}" data-step="agents-${deps.escapeHtml(deps.executionIdentity(rows[0].trace))}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main"><div class="chat-step-head"><span class="chat-step-label tail-agents-head">${head}</span></div>` +
      `<ul class="tail-agents">${items}</ul></div></li>`
    );
  }

function ccTurnVerb(msg) {
    const key = String((msg && msg.id) || "");
    if (!key) return "Worked";
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) hash = (Math.imul(hash, 31) + key.charCodeAt(i)) | 0;
    return CC_TURN_VERBS[(hash >>> 0) % CC_TURN_VERBS.length];
  }

function ccDoneAt(stamp) {
    const when = new Date(stamp || "");
    if (!stamp || Number.isNaN(when.getTime())) return "";
    const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const days = window.fuseTime.dayNumber(Date.now()) - window.fuseTime.dayNumber(when);
    if (days === 0) return time;
    if (days > 0 && days < 7) return `${when.toLocaleDateString([], { weekday: "long" })} ${time}`;
    return `${when.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })}, ${time}`;
  }

function ccStillRunningText() {
    try { return ccStillRunningCount(); } catch { return ""; }
  }

function ccStillRunningCount() {
    const running = (deps.jobsRows || []).filter((job) => ["running", "queued", "cancelling"].includes(String(job.state || job.status || "")));
    const monitors = running.filter((job) => job.kind === "monitor").length;
    const shells = running.length - monitors;
    const agents = (deps.agentsRows || []).filter((row) => deps.agentLive(row.status)).length;
    const parts = [];
    if (shells) parts.push(shells === 1 ? "1 shell" : `${shells} shells`);
    if (monitors) parts.push(monitors === 1 ? "1 monitor" : `${monitors} monitors`);
    if (agents) parts.push(agents === 1 ? "1 local agent" : `${agents} local agents`);
    if (parts.length > 1) {
      const total = shells + monitors + agents;
      return `${total} background tasks`;
    }
    return parts[0] || "";
  }

const CC_CALL_NAMES = {
    read_file: "Read", grep: "Search", glob: "Search", list_dir: "List", run_terminal_command: "Bash",
    search_replace: "Update", write_file: "Write", read_url: "Fetch", web_search: "Web Search",
    spawn_subagent: "Agent", monitor: "Monitor", get_command_output: "Wait", kill_command: "Stop Task",
    read_transcript: "Transcript",
  };

const CC_TURN_VERBS = ["Baked", "Brewed", "Churned", "Cogitated", "Cooked", "Crunched", "Sautéed", "Worked"];

  return { ccCallClockHtml, ccCallName, ccCallSummary, ccCallKind, ccCountsText, ccAgentLastCall, ccCallLine, ccAgentProgressHtml, ccAgentType, ccAgentDoneText, consoleOnlyFold, ccInterruptedFold, ccFailure, ccInterruptedText, ccCleanBackgroundEnd, consoleMonitorEventHtml, consoleAgentNoticeHtml, consoleAutoStepsHtml, ccAgentsGroupHtml, ccTurnVerb, ccDoneAt, ccStillRunningText, ccStillRunningCount, CC_CALL_NAMES, CC_TURN_VERBS };
}

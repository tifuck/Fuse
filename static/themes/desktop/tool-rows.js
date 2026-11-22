// tool-rows presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function toolImage(trace) {
    const image = trace?.name === "read_file" && trace.image;
    if (!image || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(image.mime)) return null;
    const safe = { mime: image.mime };
    if (typeof image.preview_url === "string" && /^\/chat\/runs\/[A-Za-z0-9_-]{1,80}\/images\/ts_\d+_\d+$/.test(image.preview_url)) {
      safe.preview_url = image.preview_url;
    } else if (typeof image.data === "string" && image.data.length && image.data.length % 4 === 0 &&
        !/[^A-Za-z0-9+/=]/.test(image.data) && /^[^=]*={0,2}$/.test(image.data)) {
      safe.data = image.data;
    } else return null;
    for (const key of ["width", "height", "original_width", "original_height", "frame", "frames"]) {
      if (Number.isSafeInteger(image[key]) && image[key] >= (key === "frame" ? 0 : 1)) safe[key] = image[key];
    }
    if (typeof image.format === "string") safe.format = image.format.slice(0, 32);
    return safe;
  }

// One line for what the image is: "PNG · 640 × 480 · resized from … · frame 2 of 3".
function toolImageInfo(trace) {
    const image = toolImage(trace);
    if (!image) return "";
    const info = [image.format || image.mime];
    if (image.width && image.height) info.push(`${image.width} × ${image.height}`);
    if (image.original_width && image.original_height && (image.original_width !== image.width || image.original_height !== image.height)) {
      info.push(`resized from ${image.original_width} × ${image.original_height}`);
    }
    if (image.frames > 1 && image.frame != null) info.push(`frame ${image.frame + 1} of ${image.frames}`);
    return info.join(" · ");
  }

function toolImageHtml(trace) {
    const image = toolImage(trace);
    if (!image) return '<span class="chat-tool-truncated">Image preview unavailable.</span>';
    return `<figure class="chat-tool-image">${deps.imagePreviewButton({ data: image.preview_url || `data:${image.mime};base64,${image.data}`, name: trace.path || "Image preview" })}</figure>`;
  }

function toolDetailHtml(trace, failed, running) {
    const sections = [];
    const hasImage = trace.name === "read_file" && trace.image && !failed && !running;
    if (hasImage) sections.push(toolImageHtml(trace));
    const error = failed ? trace.error || trace.summary || trace.preview || "The tool failed without an error message."
      : ["interrupted", "cancelled"].includes(trace.status) || ["cancelled", "interrupted", "lost"].includes(deps.executionState(trace)) ? trace.error || "" : "";
    if (error) sections.push(deps.failNoteHtml(error));
    const inputs = [trace.command, trace.path, trace.pattern, trace.url, deps.searchQueries(trace).join("\n"), trace.description, trace.title];
    const seen = new Set([error, ...inputs].filter(Boolean).map(deps.normErr));
    const preview = trace.summary && String(trace.summary).startsWith(String(trace.preview || "")) ? "" : trace.preview;
    const authoritativeOutput = typeof trace.output === "string";
    let output = authoritativeOutput ? deps.cleanToolOutput(trace, trace.output) : [preview, trace.summary].filter((value) => {
      const key = deps.normErr(value);
      if (!key || seen.has(key) || key === "still running" || key === "background task started") return false;
      seen.add(key);
      return true;
    }).join("\n\n");
    if (error && deps.normErr(output) === deps.normErr(error)) output = "";
    const outputLabel = { read_file: "Contents", list_dir: "Entries", grep: "Matches", glob: "Matches", search_replace: "Change result", write_file: "Write result", spawn_subagent: "Report", get_subagent_output: "Report", monitor: "Events" }[trace.name] || "Output";
    const hasResults = Boolean(trace.results?.length) && (deps.isSearchTool(trace.name) || trace.name === "read_url");
    const diff = ["search_replace", "write_file"].includes(trace.name) && !failed
      ? [trace.preview, trace.output].find((value) => deps.looksLikeDiff(value)) : "";
    const fileBody = trace.name === "write_file" && !failed && !diff
      ? (deps.looksLikeDiff(trace.preview) ? "" : String(trace.preview || "").trim()) : "";
    let shownOutput = !hasResults;
    if (hasResults) {
      const snippet = trace.name === "read_url" ? String(trace.results[0].snippet || "").trim() : "";
      sections.push(`<section class="chat-tool-section is-sources" data-tool-section="Sources"><span class="chat-tool-section-label visually-hidden">Sources</span>${deps.webResultsHtml(trace.results)}` +
        (snippet ? `<pre class="chat-tool-out">${deps.escapeHtml(deps.toolText(snippet, 1600))}</pre>` : "") + "</section>");
    } else if (diff) {
      shownOutput = false;
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="Changes"><div class="chat-tool-section-head"><span class="chat-tool-section-label">Changes</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy changes">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.consoleDiffHtml(diff, { numbers: trace.name !== "search_replace" })}</div></section>`);
    } else if (fileBody) {
      shownOutput = false;
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="Contents"><div class="chat-tool-section-head"><span class="chat-tool-section-label">Contents</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy contents">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.fileBodyHtml(trace, fileBody)}</div></section>`);
    } else if (!failed && output && deps.structuredToolHtml(trace, output) !== null) {
      shownOutput = false;
      const body = deps.structuredToolHtml(trace, output);
      if (body) sections.push(`<section class="chat-tool-section is-result is-structured" data-tool-section="${outputLabel}"><span class="chat-tool-section-label visually-hidden">${outputLabel}</span>${body}</section>`);
    } else if (trace.name === "read_file" && output && !failed) {
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="${outputLabel}"><div class="chat-tool-section-head"><span class="chat-tool-section-label">${outputLabel}</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy ${outputLabel.toLowerCase()}">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.fileBodyHtml(trace, output)}</div></section>`);
    } else if (output || (trace.command && !failed)) {
      sections.push(detailSectionHtml(outputLabel, output || (running ? "Waiting for output" : "No output"), `${output ? "is-result" : "is-result is-empty"}${deps.isCodeOutputTool(trace.name) ? " is-code" : ""}`, authoritativeOutput ? 24000 : 4000, trace.command || ""));
    }
    if (authoritativeOutput && shownOutput) {
      const note = deps.consoleTruncationNote(trace);
      if (note) sections.push(note);
    } else if (trace.output_truncated && output.length <= 4000) sections.push('<span class="chat-tool-truncated">Output truncated · preview only</span>');
    if (!hasImage && !error && !output && !hasResults && !trace.command && !["todo_write", "create_artifact", "update_artifact", "edit_artifact", "read_artifact"].includes(trace.name)) {
      sections.push(detailSectionHtml("Output", running ? "Waiting for output"
        : trace.child_id && ["spawn_subagent", "get_subagent_output"].includes(trace.name) ? "No inline output. Open agent for its transcript." : "No output", "is-empty"));
    }
    const exit = deps.shellExitCode(trace);
    const work = deps.agentTraceWorkMs(trace);
    const duration = work !== undefined ? work
      : Number.isFinite(trace.execution_duration_ms) ? trace.execution_duration_ms : Number.isFinite(trace.duration_ms) ? trace.duration_ms : trace.latency_ms;
    const state = deps.executionState(trace);
    const queries = deps.searchQueries(trace);
    const meta = [
      trace.host ? `Host ${trace.host}` : "",
      trace.cwd && trace.cwd !== trace.project_root ? `Directory ${trace.cwd}` : trace.cwd && !trace.host ? `Directory ${trace.cwd}` : "",
      trace.path && ["read_file", "write_file", "search_replace", "list_dir", "grep", "glob"].includes(trace.name) ? trace.path : "",
      Number.isFinite(trace.offset) ? `lines ${trace.offset}–${trace.offset + (Number.isFinite(trace.limit) ? trace.limit - 1 : 0)}` : "",
      trace.include ? `include ${trace.include}` : "",
      queries.length > 1 ? queries.join(" · ") : "",
      running && trace.progressLabel ? trace.progressLabel : "",
      deps.toolBackground(trace) ? "background" : "",
      state && !["completed", "running"].includes(state) ? state.replace(/_/g, " ") : "",
      trace.wake_state === "dropped" ? "Report not delivered to the main agent" : trace.wake_state === "held" ? "Main agent stopped; report goes with your next message" : trace.wake_state === "retrying" ? "Report pending delivery" : "",
      trace.status === "done" && deps.executionPending(trace) ? "Tool returned; execution continues" : "",
      trace.termination_confirmed === true ? "Termination confirmed" : trace.termination_confirmed === false ? "Termination not confirmed"
        : deps.isShellExecTool(trace.name) && state && !deps.executionPending(trace) ? "Termination unknown" : "",
      trace.version != null ? `v${trace.version}` : "", trace.visibility || "",
      exit !== null ? `exit ${exit}` : "",
      state === "timed_out" && Number.isFinite(trace.timeout_ms) ? `timeout ${deps.humanMs(trace.timeout_ms)}` : "",
      trace.name === "monitor" && Number.isFinite(trace.event_count) ? `${trace.event_count} events` : "",
      trace.name === "todo_write" || duration == null ? "" : work != null ? `Agent worked ${deps.formatTurnDuration(work)}` : deps.humanMs(duration),
      // The id matters when a run did not end cleanly (to find or stop it).
      deps.executionIdentity(trace) && ((state && !["completed", "running"].includes(state)) || trace.termination_confirmed === false) ? `ID ${deps.executionIdentity(trace)}` : "",
    ].filter(Boolean);
    if (meta.length) sections.push(`<div class="chat-tool-context"${deps.executionIdentity(trace) ? ` title="ID ${deps.escapeHtml(deps.executionIdentity(trace))}"` : ""}>${meta.map((value) => `<span>${deps.escapeHtml(value)}</span>`).join("")}</div>`);
    return `<div class="chat-tool-details">${sections.join("")}</div>`;
  }

function detailSectionHtml(label, value, kind = "", cap = 4000, command = "") {
    const text = String(value || "");
    const characters = Array.from(text);
    const clipped = characters.slice(0, cap).join("");
    const ansi = kind.includes("is-result") || kind === "is-command" || kind === "is-error";
    const cmd = command ? `<span class="chat-tool-cmd">$ ${deps.escapeHtml(command)}</span>\n` : "";
    const body = deps.looksLikeDiff(clipped)
      ? `<pre class="chat-tool-out is-diff">${cmd}<code>${deps.highlightCode("diff", clipped)}</code></pre>`
      : `<pre class="chat-tool-out">${cmd}${cmd ? `<span class="chat-tool-text">${ansi ? deps.renderAnsi(clipped) : deps.escapeHtml(clipped)}</span>` : ansi ? deps.renderAnsi(clipped) : deps.escapeHtml(clipped)}</pre>`;
    return `<section class="chat-tool-section ${kind}" data-tool-section="${deps.escapeHtml(label)}"><div class="chat-tool-section-head"><span class="chat-tool-section-label">${deps.escapeHtml(label)}</span>` +
      (command ? `<button type="button" class="chat-tool-copy" data-copy-tool data-copy-value="${deps.escapeHtml(command)}" aria-label="Copy command">Copy command</button>` : "") +
      `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy ${deps.escapeHtml(label.toLowerCase())}">Copy</button></div>` +
      body +
      (characters.length > cap ? `<span class="chat-tool-truncated">Output truncated · showing ${deps.ccTokens(cap)} of ${deps.ccTokens(characters.length)} chars</span>` : "") + "</section>";
  }

// The result after a row's object, worded so it does not repeat the verb
// and reads as a fact for the reader, not a receipt for the model:
// "84 lines", "3 lines across 2 files", "+2 −1", "exit 5", "still running".
function resultHtml(status) {
    const text = String(status || "");
    const edit = text.match(/^(?:Added (\d[\d,]*) lines?)?(?:, )?(?:[Rr]emoved (\d[\d,]*) lines?)?$/);
    if (edit && (edit[1] || edit[2])) {
      return [edit[1] && `<span class="dk-plus">+${edit[1]}</span>`, edit[2] && `<span class="dk-minus">−${edit[2]}</span>`].filter(Boolean).join(" ");
    }
    return deps.consoleStatusHtml(text.replace(/^(Wrote \d[\d,]* lines?) to .+$/, "$1").replace(/^(Read|Found|Listed|Received|Did|Wrote) (?=\d)/, "").replace(/^Ran (?=in the background)/, "")
      .replace(/^Created .+ \(([^()]+)\)$/, "$1").replace(/^\((No output)\)$/, "$1").replace(/^Exit code (?=\d)/, "exit ")
      .replace(/^\d+ search(?:es)? in /, "").replace(/^(?:Returned while the command was still running|Still running)$/, "still running")
      .replace(/^(?:Error: )?artifact is at v\d+, not v\d+: it changed since you read it.*$/, "it changed after it was read")
      .replace(/^(?:Error: )?Unknown tool\b.*$/i, "")
      .replace(/^[a-z]+(?:_[a-z]+)+$/, (state) => state.replace(/_/g, " ")));
  }

// Desktop's opened summary line: each row starts with a glyph for its kind of
// call, and ends in a chevron when it opens.
const ROW_ICON_PATHS = {
    read: '<path d="M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8 11.9 12.2 8 12.2 1.8 8 1.8 8z"/><circle cx="8" cy="8" r="1.8"/>',
    edit: '<path d="M10.5 2.5 12.5 4.5 6.5 10.5H4.5V8.5z"/><path d="M3 13.5h10"/>',
    write: '<path d="M8 2.5v7M5 6.5l3 3 3-3"/><path d="M3 11v2.5h10V11"/>',
    search: '<path d="M2.5 4h7M2.5 7h4M2.5 10h3"/><circle cx="10.5" cy="9.5" r="2.5"/><path d="m12.3 11.3 1.7 1.7"/>',
    list: '<path d="M3.5 2.5v9M3.5 5.5h3M3.5 11.5h3"/><rect x="7.5" y="4" width="6" height="3" rx=".8"/><rect x="7.5" y="10" width="6" height="3" rx=".8"/>',
    shell: '<path d="m3 4.5 3.5 3.5L3 11.5M8 12h5"/>',
    wait: '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.3"/>',
    fetch: '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c2 2 2 9 0 11M8 2.5c-2 2-2 9 0 11"/>',
    web: '<path d="M2.5 5h11M2.5 8h7M2.5 11h5"/><path d="m12 9 .8 1.7 1.7.8-1.7.8L12 14l-.8-1.7-1.7-.8 1.7-.8z" fill="currentColor" stroke="none"/>',
    code: '<path d="M5.5 4.5 2.5 8l3 3.5M10.5 4.5l3 3.5-3 3.5"/>',
    other: '<circle cx="8" cy="8" r="2"/>',
  };
const ROW_ICON_KIND = {
    read_file: "read", read_transcript: "read", read_artifact: "read", search_replace: "edit", edit_artifact: "edit",
    write_file: "write", create_artifact: "write", update_artifact: "write", grep: "search", glob: "search", list_dir: "list",
    run_terminal_command: "shell", kill_command: "shell", get_command_output: "wait", monitor: "wait", read_url: "fetch", run_code: "code",
  };
// Kinds the row draws with the step glyphs the theme already has.
const ROW_STEP_ICON = {
    todo_write: "todo", scratch: "scratch", remember: "memory", recall: "memory", forget: "memory", load_skill: "skill", propose_skill: "skill", search_models: "model",
  };
function rowIconHtml(trace) {
    const step = trace && ROW_STEP_ICON[trace.name];
    if (step) return `<span class="dk-row-ico" aria-hidden="true">${deps.STEP_ICONS[step]}</span>`;
    const kind = trace ? ROW_ICON_KIND[trace.name] || (deps.isSearchTool(trace.name) ? "web" : "other") : "other";
    return `<span class="dk-row-ico" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${ROW_ICON_PATHS[kind]}</svg></span>`;
  }
const ROW_CHEV = '<span class="dk-row-chev" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';
const ROW_CHEV_EMPTY = '<span class="dk-row-chev is-empty" aria-hidden="true"></span>';

// A failure's first line; one ending in a colon ("regex parse error:") takes
// the next line with it.
function errorLine(text) {
  const lines = String(text || "").split("\n").map((line) => line.trim()).filter(Boolean);
  return lines[0]?.endsWith(":") && lines[1] ? `${lines[0]} ${lines[1]}` : lines[0] || "";
}

// A cut-short call's row says it in one word; its box says why.
// "Interrupted · stopped; the process may still be running" ->
// "Stopped before it finished. The process may still be running."
function stopWord(text) {
    return /^Interrupted · stopped/.test(text) ? "Stopped" : "Interrupted";
  }
function stopNote(text) {
    const rest = String(text).replace(/^Interrupted · /, "");
    if (/^stopped/.test(rest)) return `Stopped before it finished.${/may still be running/.test(rest) ? " The process may still be running." : ""}`;
    return `${rest.charAt(0).toUpperCase()}${rest.slice(1)}.`;
  }

// An opened call: one block, the same for every tool. Its first line is the
// exact input (the command, the path, the pattern) with where it ran and a
// copy button, then only the output, and a note when the call needs
// explaining (why it stopped). How it ended and how long it took stay on the
// row above. body is HTML.
// The prompt sits outside the input, so a multi-line command's later lines
// line up under its first and a long one scrolls under a fixed "$".
function callBoxHtml({ input = "", prompt = false, label = "input", body = "", note = "", bad = false, stop = false, host = "", cwd = "" }) {
    // The folder keeps its end when it is cut: the marks keep a leading "/" in
    // place inside the right-to-left box that cuts it from the start.
    const where = host || cwd ? `<span class="dk-call-where">${host ? `<span class="dk-call-host" title="${deps.escapeHtml(host)}">${deps.escapeHtml(host)}</span>` : ""}` +
      `${cwd ? `<span class="dk-call-cwd" title="${deps.escapeHtml(cwd)}">&lrm;${deps.escapeHtml(cwd)}&lrm;</span>` : ""}</span>` : "";
    const head = input ? `<div class="dk-call-head">${prompt ? '<span class="dk-call-pr" aria-hidden="true">$ </span>' : ""}<span class="dk-call-in">${deps.escapeHtml(input)}</span>${where}` +
      `<button type="button" class="dk-call-copy" data-copy-tool data-copy-value="${deps.escapeHtml(input)}" aria-label="Copy ${label}">Copy</button></div>` : "";
    const noteHtml = note ? `<div class="dk-call-note${bad ? " is-bad" : stop ? " is-stop" : ""}">${deps.escapeHtml(note)}</div>` : "";
    return `<div class="dk-call-box">${head}${body}${noteHtml}</div>`;
  }

function callInput(trace) {
    const name = trace.name;
    if (name === "run_terminal_command") return { input: deps.displayCommand(trace), prompt: true, label: "command" };
    if (name === "grep" || name === "glob") return { input: trace.pattern ? `"${trace.pattern}"${trace.path ? ` in ${trace.path}` : ""}` : "", label: "pattern" };
    if (["read_file", "write_file", "search_replace", "list_dir"].includes(name)) return { input: String(trace.path || ""), label: "path" };
    if (name === "read_url") return { input: String(trace.url || ""), label: "address" };
    if (deps.isSearchTool(name)) return { input: deps.searchQueries(trace).join("\n"), label: "search" };
    return {};
  }

function toolRowLabel(trace) {
    const subject = deps.toolSubject(trace);
    return `${deps.toolAction(trace)}${subject ? ` · ${subject}` : ""}`;
  }

function toolStepHtml(trace, index, live, open, openMap, opts = {}) {
    const presentation = deps.toolPresentation(trace, live);
    const { running, interrupted, failed } = presentation;
    const artifact = ["create_artifact", "update_artifact", "edit_artifact", "read_artifact"].includes(trace.name);
    const read = trace.name === "read_url";
    const search = deps.isSearchTool(trace.name);
    const artifactVerb = { create_artifact: "Create artifact", update_artifact: "Update artifact", edit_artifact: "Edit artifact", read_artifact: "Read artifact" }[trace.name];
    const label = artifact ? `${artifactVerb} · ${trace.title || "artifact"}`
      : read ? `Page read · ${deps.pageHost(trace.url) || "page"}`
      : search ? `Search · ${deps.searchCountFor(trace) === 1 && deps.searchQueries(trace)[0] ? deps.shortToolText(deps.searchQueries(trace)[0], 72) : `${deps.searchCountFor(trace)} ${deps.searchCountFor(trace) === 1 ? "query" : "queries"}`}`
      : toolRowLabel(trace);
    const kind = artifact ? "artifact" : read ? "read" : search ? "search" : TOOL_KIND[trace.name] || "workspace";
    const openKind = artifact && trace.identifier && presentation.state === "done" ? "artifact"
      : ["spawn_subagent", "get_subagent_output"].includes(trace.name) && trace.child_id ? "agent" : "";
    const openId = openKind === "artifact" ? trace.identifier : openKind === "agent" ? trace.child_id : "";
    const revealed = open;
    let inner;
    const icon = (failed ? deps.STEP_ICONS.error : interrupted ? deps.STEP_ICONS.stop : artifact ? deps.STEP_ICONS.artifact : deps.toolIcon(trace.name, false, false));
    {
      const verbose = Boolean(opts.verbose);
      const fold = deps.consoleFold(trace, presentation, verbose, opts.oneLine);
      // An opened summary line lists one-line rows: each opens its own output.
      const preview = verbose || opts.oneLine ? "" : fold.preview;
      inner = verbose || opts.oneLine ? fold.full || fold.preview : fold.full;
      // A cut-short one-line row: one word on the line, the reason as the box's
      // note (not under its output as well).
      const stopText = opts.oneLine && interrupted && !running ? deps.ccInterruptedText(trace) : "";
      if (stopText) inner = inner.replace(/<div class="chat-console-result-text is-stop-note[^"]*">[^<]*<\/div>/g, "");
      const described = deps.shellDescription(trace, presentation, verbose);
      if (described && !opts.oneLine) inner = `<div class="dk-row-cmd">$ ${deps.escapeHtml(deps.displayCommand(trace))}</div>${inner}`;
      // An opened one-line row is one box (callBoxHtml); a call with an input
      // (a command, a path, a pattern) opens even with no output, to its input.
      const input = opts.oneLine ? callInput(trace) : {};
      const boxed = Boolean(opts.oneLine && (inner || described || input.input || stopText));
      const expandable = !verbose && openKind !== "agent" && (Boolean(inner) || boxed);
      const hangOpen = verbose ? Boolean(inner) : revealed && expandable;
      // A running one-line row already says what it is doing; its result
      // only adds where (the background) or that it is being stopped.
      // A non-zero exit Console prints as an error ("Error: Exit code 1")
      // gets the red dot too; exit 1 from grep and friends stays finished.
      const dotState = (presentation.state === "warning") && trace.name === "run_terminal_command" &&
        deps.ccExitMeaning(deps.displayCommand(trace), deps.shellExitCode(trace)).isError ? "error" : presentation.state;
      // A failure keeps its reason on the line too.
      const exit = deps.shellExitCode(trace);
      const status = opts.oneLine && running
        ? (deps.toolBackground(trace) ? "in the background" : presentation.statusLabel === "Cancelling" ? "Cancelling…" : "")
        : stopText ? (deps.rowSaysStopped(trace, presentation) ? "" : stopWord(stopText))
        : fold.status || (opts.oneLine && (failed || dotState === "error")
          ? (exit !== null && exit !== 0 ? `Exit code ${exit}` : errorLine(trace.error || trace.summary) || "failed")
          : "");
      // A pending call names its deadline at the end of the line under it
      // ("Waiting for output… (timeout: 2m)"), and that line exists even when
      // the call has nothing else to say yet. A fold that already printed the
      // deadline (the command line under a described shell) is left as it is.
      const timeout = (running) && !verbose && !/\btimeout: [^)]*/.test(`${status} ${preview}`) ? deps.ccTimeoutNote(trace) : "";
      const said = resultHtml(status);
      const result = said || timeout ? `<span class="chat-console-result-text">${said}${said && timeout ? " " : ""}${timeout ? `<span class="chat-console-timeout">${deps.escapeHtml(timeout)}</span>` : ""}</span>` : "";
      // Ledger: a one-line row carries its result on the line itself (verb,
      // subject, result); its hang is only the body that opens under it.
      const lineResult = opts.oneLine ? result : "";
      if (boxed) {
        // Where: the machine, plus a command's directory.
        inner = callBoxHtml({
          ...input, body: inner, note: stopText ? stopNote(stopText) : "", bad: failed || dotState === "error", stop: Boolean(stopText),
          host: trace.name === "read_url" || deps.isSearchTool(trace.name) ? "" : trace.host || "", cwd: trace.name === "run_terminal_command" ? trace.cwd || "" : "",
        });
      }
      const line = deps.consoleCallHtml(trace, presentation, verbose) + lineResult;
      const head = deps.toolHeadHtml({
        ...presentation, line: opts.oneLine ? rowIconHtml(trace) + line + (expandable ? ROW_CHEV : ROW_CHEV_EMPTY) : line, title: trace.command || trace.path || trace.url || label, expandable, open: revealed, openKind, openId,
        contextHost: "",
      });
      const showHang = Boolean((lineResult ? "" : result) || preview || inner);
      // A fold with only its hidden body prints one line: the branch glyph
      // and the hang's margins wait for the open.
      const bodyOnly = (lineResult || !status) && !preview;
      return (
        `<li class="chat-step is-console-call is-${kind} is-${dotState}${deps.toolBackground(trace) ? " is-background" : ""}${hangOpen || verbose ? " is-console-expanded" : ""}${verbose ? " is-console-verbose" : ""}" style="--console-hang: ${deps.consoleVerb(trace.name, trace).length + 1}ch" data-execution="${deps.escapeHtml(deps.executionIdentity(trace))}" data-tool="${deps.escapeHtml(trace.name)}" data-state="${presentation.state}" data-step="${deps.escapeHtml(index)}">` +
        `<span class="chat-step-node" aria-hidden="true">${icon}</span>` +
        `<div class="chat-step-main">${head}` +
        (showHang
          ? `<div class="chat-step-reveal is-open">` +
            `<div class="chat-step-reveal-inner">` +
            `<div class="chat-console-hang${running ? " is-running" : ""}${failed ? " is-error" : ""}${fold.keep ? " is-kept" : ""}${bodyOnly ? " is-body-only" : ""}${hangOpen ? " is-expanded" : ""}">` +
            `<span class="chat-console-branch" aria-hidden="true">└</span>` +
            `<div class="chat-console-hang-body">` +
            (lineResult ? "" : result) +
            (preview ? `<div class="chat-console-preview">${preview}</div>` : "") +
            (inner ? inner : "") +
            `</div></div></div></div>`
          : "") +
        `</div></li>`
      );
    }

  }

const TOOL_KIND = {
    read_file: "file",
    list_dir: "list",
    grep: "grep",
    glob: "glob",
    write_file: "write",
    search_replace: "edit",
    run_terminal_command: "shell",
    get_command_output: "shell",
    monitor: "watch",
    search_tools: "tools",
    load_skill: "skill",
    spawn_subagent: "agent",
    get_subagent_output: "agent",
    kill_subagent: "agent",
    list_subagents: "agent",
    search_models: "search",
    read_transcript: "read",
  };

  return { toolImage, toolImageInfo, toolImageHtml, toolDetailHtml, detailSectionHtml, callBoxHtml, callInput, rowIconHtml, ROW_CHEV, toolRowLabel, toolStepHtml, TOOL_KIND };
}

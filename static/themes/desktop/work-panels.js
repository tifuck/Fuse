// work-panels presentation. Dependencies are live getters supplied by index.js.
const CHECK = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 8.4l3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const CROSS = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
const TERM = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 4.5l3.2 3.2L3 10.9M8 11.5h5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const EYE = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8s-2.3 4.2-6.2 4.2S1.8 8 1.8 8z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
const KINDS = { explore: "Explore", plan: "Plan", "general-purpose": "General" };
// What a subagent has done, in words: "6 files, 3 searches, 1 edit".
const NOUNS = {
  read_file: "file", list_dir: "folder", grep: "search", glob: "search", search_replace: "edit", write_file: "edit",
  run_terminal_command: "command", monitor: "watch", web_search: "web search", read_url: "page", spawn_subagent: "subagent",
  load_skill: "skill", create_artifact: "artifact", update_artifact: "artifact", edit_artifact: "artifact",
};
const plural = (n, word) => (n === 1 ? word : /(ch|sh)$/.test(word) ? `${word}es` : `${word}s`);
function workDone(row) {
  const tally = new Map();
  Object.entries(row.tool_counts && typeof row.tool_counts === "object" ? row.tool_counts : {}).forEach(([name, n]) => {
    const noun = NOUNS[name];
    if (noun && Number(n) > 0) tally.set(noun, (tally.get(noun) || 0) + Number(n));
  });
  return [...tally].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([noun, n]) => `${n} ${plural(n, noun)}`).join(", ");
}
const firstLine = (text) => String(text || "").trim().split(/\n/)[0].slice(0, 160);
// Sort keys: a row sorts with the top of its family (the subagent its main
// agent started), live families first; equal keys keep the runtime's order,
// so children stay under their parent. Section labels sit between.
const SECTION = 2e7;

export function create(deps) {
  // agentMainRowHtml opens every render of the list; the rows that follow
  // need its counts to label the Running and Finished sections.
  let render = { live: 0, finished: 0, labelled: false, ref: 0 };

  const ratioOf = (row) => (row && row.context && Number(row.context.limit) > 0
    ? Math.min(100, Math.round((Number(row.context.used) || 0) / Number(row.context.limit) * 100)) : null);

  // The family head: up the parents while the runtime would hang the row
  // under them (a live row under a finished parent hangs off main instead).
  function familyHead(row) {
    const rows = deps.agentsRows || [];
    let head = row;
    for (let hops = 0; hops < 8; hops += 1) {
      const up = rows.find((r) => String(r.id) === String(head.parent_run_id ?? "") && r !== head);
      if (!up || (!deps.agentLive(up.status) && deps.agentLive(head.status))) break;
      head = up;
    }
    return head;
  }

  function sortKeys(row) {
    const head = familyHead(row);
    const live = deps.agentLive(head.status);
    const base = live ? 0 : SECTION;
    // Live rows sort by spawn time, which never moves: their work clocks tick
    // and pause unevenly, so near-equal ones swapped places on every poll.
    // Same-second spawns tie and keep spawn order. The offset is a day ahead
    // of now so server clock skew never pushes an age below zero.
    const born = Date.parse(head.created_at || "");
    const age = live && Number.isFinite(born) ? render.ref - Math.floor(born / 1000) : deps.agentDurationSeconds(head);
    const secs = Math.min(SECTION / 2 - 1, Math.max(0, Math.round(age || 0)));
    const ctx = ratioOf(head) ?? 0;
    return `--o-td:${base + SECTION / 2 - secs};--o-ta:${base + secs};--o-cd:${base + 100 - ctx};--o-ca:${base + ctx}`;
  }

  const sectionHtml = (kind, label, n) =>
    `<div class="dk-ar-sec is-${kind}" role="presentation">${label}<span>${n}</span></div>`;

  // One table row per subagent: status, the task and what it is doing (or
  // how it ended), context used and its clock, and at most one action. The
  // whole row but the action opens that agent's transcript.
  function agentRowHtml(row, opts = {}) {
    const live = deps.agentLive(row.status);
    const ratio = ratioOf(row);
    const dur = deps.agentDurationSeconds(row);
    const err = String(row.error || "").trim();
    const wake = row.wake_state === "dropped" ? "report not delivered" : row.wake_state === "held" ? "report waits for your next message" : row.wake_state === "retrying" ? "report pending" : "";
    const alert = err || wake;
    const lastCall = !alert && live ? deps.ccAgentLastCall(row.recent) : "";
    const status = deps.agentStatusText(row);
    const done = workDone(row);
    // Live: what it is doing now; finished: how it ended.
    const doing = live
      ? (row.cancel_requested ? "Stopping" : lastCall || (done ? `Working, ${done}` : "Working"))
      : row.status === "completed" ? firstLine(row.summary) || done || "Done"
        : row.status === "failed" ? "Failed" : done ? `Stopped, ${done}` : "Stopped";
    const commands = deps.jobsFor(row.id).length;
    const kind = KINDS[row.subagent_type] || (row.subagent_type ? row.subagent_type[0].toUpperCase() + row.subagent_type.slice(1) : "General");
    const depth = opts.tree ? opts.tree.ancestors.length : 0;
    const title = [
      row.description || "Subagent",
      status,
      kind,
      row.model || "",
      ratio != null ? `${ratio}% context` : "",
      commands ? `${commands} command${commands === 1 ? "" : "s"}` : "",
      alert,
    ].filter(Boolean).join(" · ");
    const id = deps.escapeHtml(row.id);
    const who = deps.escapeHtml(row.description || "subagent");
    const stamp = Date.parse(row.cancel_requested_at || "");
    const wedged = row.cancel_requested && Number.isFinite(stamp) && Date.now() - stamp >= 15000;
    const action = live && wedged
      ? `<button type="button" class="chat-agents-act is-urgent" data-agent-force="${id}" aria-label="Force stop ${who}" title="Wedged in stopping: force stop">${CROSS}</button>`
      : live && !row.cancel_requested
        ? `<button type="button" class="chat-agents-act" data-agent-stop="${id}" aria-label="Stop ${who}" title="Stop this subagent">${CROSS}</button>`
        : deps.agentResumeAvailable(row)
          ? `<button type="button" class="chat-agents-act is-resume" data-agent-resume="${id}" aria-label="Resume ${who}" title="Resume from where it stopped">↻</button>`
          : '<span class="chat-agents-act" aria-hidden="true"></span>';
    const state = live ? "running" : row.status === "completed" ? "done" : row.status === "failed" ? "failed" : "stopped";
    const mark = state === "done" ? CHECK : state === "failed" ? CROSS : "";
    const sub = alert
      ? `<span class="chat-agents-sum">${deps.escapeHtml(alert.slice(0, 180))}</span>`
      : `<span class="chat-agents-doing">${deps.escapeHtml(doing)}</span>`;
    let label = "";
    if (!live && !render.labelled) {
      render.labelled = true;
      label = sectionHtml("finished", "Finished", render.finished);
    }
    return label +
      `<div class="chat-agents-row dk-ar is-${deps.escapeHtml(row.status || "unknown")} is-${state}${depth ? " is-child" : ""}${row.wake_state === "dropped" ? " is-undelivered" : ""}${deps.agentIsStalled(row) ? " is-stalled" : ""}${live && row.cancel_requested ? " is-stopping" : ""}" role="listitem" data-agent-row="${id}" style="${sortKeys(row)}"${opts.selected ? ' aria-current="true"' : ""}>` +
      `<button type="button" class="chat-agents-pick" data-agent-id="${id}" title="${deps.escapeHtml(title)}">` +
      `<span class="dk-ar-st"><span class="chat-agents-dot" aria-hidden="true">${mark}</span></span>` +
      `<span class="dk-ar-main"><span class="chat-agents-name">${depth ? '<span class="dk-sub-of" aria-hidden="true">↳</span>' : ""}${deps.escapeHtml(row.description || "Subagent")}</span>` +
      `<span class="chat-agents-meta"><span class="dk-kind">${deps.escapeHtml(kind)}</span>${sub}</span></span>` +
      `<span class="dk-ar-ctx">${ratio != null ? `<b>${ratio}%</b><i class="dk-meter${ratio >= 70 ? " is-high" : ""}"><i style="width:${ratio}%"></i></i>` : "<b>—</b>"}</span>` +
      `<span class="chat-agents-time">${dur != null ? deps.formatActivityClock(dur) : "—"}</span>` +
      "</button>" +
      action +
      "</div>";
  }

  // The root of the table: the main agent, its state and clock (the chrome
  // fills its context and clock), then the Running label. Its fold control
  // is the panel's All / Running switch.
  function agentMainRowHtml({ state, live, selected, total, expanded }) {
    render = { live, finished: Math.max(0, total - live), labelled: false, ref: Math.floor(Date.now() / 1000) + 86400 };
    const fold = `${total} agent${total === 1 ? "" : "s"}`;
    const doing = live ? `Waiting on ${live} subagent${live === 1 ? "" : "s"}` : state === "working" ? "Working" : "Idle";
    return (
      `<div class="chat-agents-row dk-ar is-main is-${deps.escapeHtml(state)}${state === "working" || live ? " is-running" : ""}" role="listitem" data-agent-row="main"${selected ? ' aria-current="true"' : ""}>` +
      '<button type="button" class="chat-agents-pick" data-agent-main title="Main agent">' +
      '<span class="dk-ar-st"><span class="chat-agents-dot" aria-hidden="true"></span></span>' +
      '<span class="dk-ar-main"><span class="chat-agents-name">Main</span>' +
      `<span class="chat-agents-meta"><span class="chat-agents-state">${deps.escapeHtml(doing)}</span></span></span>` +
      '<span class="dk-ar-ctx" data-theme-main-ctx><b>—</b></span>' +
      '<span class="chat-agents-time" data-theme-main-time></span>' +
      "</button>" +
      `<button type="button" class="chat-agents-fold" data-agents-fold aria-expanded="${expanded ? "true" : "false"}" title="${expanded ? "Hide" : "Show"} finished subagents"><span>${fold}</span></button>` +
      "</div>" +
      (total ? sectionHtml("running", "Running", live) : "")
    );
  }

function workHeadHtml(title, note) {
    return `<header class="chat-work-head"><span>${deps.escapeHtml(title)}</span>${note ? `<small>${deps.escapeHtml(note)}</small>` : ""}</header>`;
  }

function compactionMarkHtml(text, summary) {
    const body = String(text || "").trim();
    const title = body.startsWith("Context pruned") ? "Context pruned" : "Context compacted";
    const handoff = String(summary || "").trim();
    if (title === "Context compacted" && handoff) {
      return (
        `<details class="chat-compact-banner" data-compaction open>` +
        `<summary><strong>Compaction</strong><span>Handoff sent to the model</span></summary>` +
        `<div class="chat-compact-handoff chat-md">${deps.renderMarkdown(handoff, false, [])}</div>` +
        `</details>`
      );
    }
    const detail = body.startsWith(title) ? body.slice(title.length).replace(/^[.\s]+/, "") : body;
    return `<p class="chat-compact-mark" data-compaction><strong>${deps.escapeHtml(title)}</strong>${detail ? `<span>${deps.escapeHtml(detail)}</span>` : ""}</p>`;
  }

  // A background command: what it is for (else the command), then where it
  // runs and the command itself (a monitor: what it watches and its event
  // count), its clock and a stop button. The row opens its output.
  function jobRowHtml(job, nowMs) {
    const secs = Math.round(deps.jobElapsedMs(job, nowMs) / 1000);
    const state = String(job.state || "running");
    const cancelling = state === "cancelling";
    const live = ["running", "queued", "cancelling"].includes(state);
    const label = cancelling ? "Stopping" : state === "queued" ? "Queued" : live ? "" : state.replace(/_/g, " ");
    const monitor = job.kind === "monitor" || job.name === "monitor";
    const command = deps.toolText(deps.displayCommand(job) || "");
    const title = deps.toolText(job.description || command || job.id);
    const events = Number(job.event_count);
    const what = monitor
      ? `Watching${Number.isFinite(events) && events > 0 ? `, ${events} event${events === 1 ? "" : "s"}` : ""}`
      : job.description && command ? `<code>${deps.escapeHtml(command)}</code>` : "";
    const meta = [job.host ? `<span class="dk-host">${deps.escapeHtml(job.host)}</span>` : "", label ? `<span>${deps.escapeHtml(label)}</span>` : "", what].filter(Boolean).join("");
    const id = deps.escapeHtml(job.command_id || job.id);
    return (
      `<div class="chat-jobs-row dk-jr is-${deps.escapeHtml(state)}${live ? " is-running" : ""}${monitor ? " is-monitor" : ""}" role="listitem" data-job-row="${id}">` +
      `<button type="button" class="chat-jobs-output-link" data-show-job="${id}" title="${deps.escapeHtml(command || title)}${job.host ? " · " + deps.escapeHtml(job.host) : ""}">` +
      `<span class="dk-jr-ic" aria-hidden="true">${monitor ? EYE : TERM}<i class="chat-agents-dot"></i></span>` +
      `<span class="dk-jr-main"><span class="chat-jobs-cmd">${deps.escapeHtml(title)}</span>` +
      (meta ? `<span class="chat-jobs-meta">${meta}</span>` : "") + "</span>" +
      `<span class="chat-agents-time" data-job-time="${deps.escapeHtml(job.id)}">${state === "queued" ? "" : deps.formatActivityClock(secs)}</span>` +
      "</button>" +
      `<button type="button" class="chat-jobs-kill" data-kill-job="${id}" aria-label="${cancelling ? "Stopping" : "Stop"} ${deps.escapeHtml(title)}" title="Stop this background command"${cancelling ? " disabled" : ""}>${CROSS}</button>` +
      "</div>"
    );
  }

// A background command's output, opened from its row. The runtime mounts it
// right after that row in the list (data-job-detail), never inside it: the row
// is one fixed-height line.
function jobDetailHtml(job) {
    const trace = { ...job, name: job.name === "monitor" || job.kind === "monitor" ? "monitor" : "run_terminal_command", status: "running" };
    return `<div class="chat-jobs-detail chat-step-reveal" role="listitem" data-job-detail="${deps.escapeHtml(job.command_id || job.id)}">` +
      `<div class="chat-step-reveal-inner">${deps.toolDetailHtml(trace, false, true)}</div></div>`;
  }

  return { agentRowHtml, agentMainRowHtml, workHeadHtml, compactionMarkHtml, jobRowHtml, jobDetailHtml };
}

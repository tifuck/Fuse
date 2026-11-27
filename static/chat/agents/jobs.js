// Background jobs list and refresh.
import { S } from '../state.js';

let jobsRendered = "";

let jobsThread = "";

let jobsRequest = null;

let jobsRefreshAgain = false;

function jobElapsedMs(job, nowMs) {
  const started = Number(job && job.started_ms);
  if (Number.isFinite(started) && started > 0) return Math.max(0, nowMs - started);
  return Math.max(0, Number(job && job.elapsed_ms) || 0);
}

export function createJobs(ctx) {
  const { live, net, sync, traces, transcript, ui } = ctx;
    function jobDetail(job, open) {
    const tpl = document.createElement("template");
    tpl.innerHTML = ui.jobDetailHtml(job).trim();
    const details = tpl.content.firstElementChild;
    details.classList.toggle("is-open", open);
    return details;
  }

  // A job's detail is its row's next sibling in the list (the row is one
  // fixed-height line, so nothing may open inside it).
  const jobRow = (id) => S.jobsListEl?.querySelector(`[data-job-row="${CSS.escape(id)}"]`);
  const jobDetailEl = (id) => S.jobsListEl?.querySelector(`[data-job-detail="${CSS.escape(id)}"]`);

    async function onJobsClick(event) {
    const output = event.target.closest("[data-show-job]");
    if (output) {
      const id = output.dataset.showJob;
      // The strip lists the commands of the transcript on screen: a
      // subagent's live in its transcript, the main agent's in the thread.
      const inAgent = Boolean(S.agentsOpen && S.agentViewId);
      const row = (inAgent ? S.agentBodyEl : S.log)?.querySelector(`[data-execution="${CSS.escape(id)}"]`);
      if (row && inAgent) {
        transcript.revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: S.reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, S.reduceMotion.matches ? 0 : 230);
      } else if (row) {
        transcript.pauseThreadFollow();
        transcript.revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: S.reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, S.reduceMotion.matches ? 0 : 230);
      } else {
        const job = S.jobsRows.find(job => String(job.command_id || job.id) === id);
        const row = jobRow(id);
        if (job && row) {
          let details = jobDetailEl(id);
          if (!details) row.after(details = jobDetail(job, false));
          transcript.setToolReveal(details, !details.classList.contains("is-open"));
        }
      }
      return;
    }
    const button = event.target.closest("[data-kill-job]");
    if (!button || !S.currentId) return;
    const tid = S.currentId;
    button.disabled = true;
    try {
      await net.api("/chat/threads/" + encodeURIComponent(tid) + "/jobs/" + encodeURIComponent(button.dataset.killJob) + "/kill", { method: "POST", body: "{}" });
      if (tid === S.currentId) await refreshJobs();
    } catch (error) {
      button.disabled = false;
      sync.showFailure("Could not stop background command", error);
    }
  }

    // Each transcript's pinned strip contains only its owner's commands.
  function jobsFor(agentId) {
    const owner = String(agentId || "");
    return S.jobsRows.filter((job) => String(job.agent_run_id || "") === owner);
  }

    function renderJobsList() {
    if (!S.jobsListEl) return;
    const owner = S.agentsOpen ? S.agentViewId : "";
    const rows = jobsFor(owner);
    const sig = owner + "\u001e" + JSON.stringify(rows.map((job) => [job.id, job.command, job.description, job.host, job.state, job.event_count, job.kind]));
    if (sig !== jobsRendered) {
      jobsRendered = sig;
      // A row change (a monitor's event count) must not close an open detail;
      // it reopens built from the current job.
      const open = new Set([...S.jobsListEl.querySelectorAll("[data-job-detail].is-open")].map((el) => el.dataset.jobDetail));
      S.jobsListEl.hidden = !rows.length;
      S.jobsListEl.innerHTML = rows.map((job) => ui.jobRowHtml(job, Date.now())).join("");
      rows.forEach((job) => {
        const id = String(job.command_id || job.id);
        if (open.has(id)) jobRow(id)?.after(jobDetail(job, true));
      });
      live.syncWorkStrip();
    }
    tickJobTimes();
  }

    function tickJobTimes() {
    if (!S.jobsRows.length) return;
    const nowMs = Date.now();
    S.jobsRows.forEach((job) => {
      S.root.querySelectorAll('[data-job-time="' + CSS.escape(String(job.id)) + '"]').forEach((el) => {
        el.textContent = ui.formatActivityClock(Math.round(jobElapsedMs(job, nowMs) / 1000));
      });
    });
  }

    async function refreshJobs() {
    if (!S.currentId) {
      S.jobsRows = [];
      renderJobsList();
      return;
    }
    const tid = S.currentId;
    const gen = S.generation;
    if (jobsRequest?.tid === tid && jobsRequest.gen === gen) { jobsRefreshAgain = true; return; }
    if (jobsThread !== tid) {
      jobsThread = tid;
      S.completedJobs.clear();
      S.jobsRows = [];
      renderJobsList();
    }
    const ticket = jobsRequest = { tid, gen };
    // Finished jobs come once; a running job's output comes from where this
    // view's copy ends (output_from) and is joined to it here.
    const priors = new Map(S.jobsRows.map((job) => [String(job.id), job]));
    const known = [...priors.values()].filter((job) => job.output_chars_exact && typeof job.output === "string").map((job) => job.id + ":" + job.output_chars);
    const done = [...S.completedJobs.keys()].slice(-200);
    const query = (known.length ? "&known=" + encodeURIComponent(known.join(",")) : "") + (done.length ? "&done=" + encodeURIComponent(done.join(",")) : "");
    try {
      const data = await net.api("/chat/threads/" + encodeURIComponent(tid) + "/jobs" + query.replace("&", "?"));
      if (tid !== S.currentId || gen !== S.generation) return;
      S.jobsRows = (Array.isArray(data.jobs) ? data.jobs : []).map((job) => {
        if (job.output_from == null) return job;
        const { output_from: _, ...rest } = job;
        const text = String(priors.get(String(job.id))?.output || "") + job.output;
        return { ...rest, output: text.slice(Math.max(0, text.length - Number(job.output_retained_chars))) };
      });
      S.jobsThreadId = tid;
      renderJobsList();
      live.syncAgentsUi();
      (Array.isArray(data.recent) ? data.recent : []).forEach((job) => S.completedJobs.set(String(job.id), job));
      traces.reconcileBgTraces([...S.jobsRows, ...S.completedJobs.values()]);
      traces.settleOrphanTraces(["run_terminal_command", "monitor"], [...S.jobsRows, ...S.completedJobs.values()]);
    } catch {} finally {
      if (jobsRequest === ticket) {
        jobsRequest = null;
        if (jobsRefreshAgain) { jobsRefreshAgain = false; refreshJobs(); }
      }
    }
  }

  return {
    jobsFor,
    onJobsClick,
    refreshJobs,
    renderJobsList,
    tickJobTimes,
    jobElapsedMs,
  };
}

export { jobElapsedMs };

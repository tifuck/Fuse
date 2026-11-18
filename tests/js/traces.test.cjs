// Tool-trace semantics and mutation (static/chat/traces/**), plus the trace
// consumers in sync/status and agents/view: execution state, wake envelopes,
// result folding, background reconciliation and agent snapshots.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../../static/chat/state.js');
const sem = require('../../static/chat/traces/semantics.js');
const { createMerge } = require('../../static/chat/traces/merge.js');
const { createStatus } = require('../../static/chat/sync/status.js');
const { createView } = require('../../static/chat/agents/view.js');

const noop = () => {};
// Any collaborator method a test does not name is a no-op.
const stub = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : k === 'then' ? undefined : noop) });
const tick = () => new Promise((resolve) => setImmediate(resolve));

function reset(extra = {}) {
  Object.assign(S, {
    activeRun: null, agentRun: null, agentsOpen: false, messages: [], log: { children: [] },
    completedJobs: new Map(), jobsRows: [], agentPendingEvents: [], agentViewId: '', stoppingRunId: '',
  }, extra);
}
function merge(agents = stub()) {
  return createMerge({ agents, transcript: stub(), ui: stub({ workClocksLive: () => false }) });
}
function view(extra = {}) {
  return createView({ jobs: stub(), live: stub(), net: stub(), overlays: stub(), picker: stub(), sync: stub(), traces: { ...sem, ...merge() }, transcript: stub(), ui: stub(), ...extra });
}

test('execution identity, state and pending', () => {
  assert.equal(sem.executionIdentity(null), '');
  assert.equal(sem.executionIdentity({ job_id: 'j', child_id: 'c', command_id: 'cmd' }), 'cmd');
  assert.equal(sem.executionIdentity({ job_id: 'j', child_id: 'c' }), 'c');
  assert.equal(sem.executionIdentity({ job_id: 'j' }), 'j');
  assert.equal(sem.executionIdentity({ command_id: 0, child_id: 'c' }), 'c', '0 is not an id');
  // A kill receipt never carries execution state.
  assert.equal(sem.executionState({ name: 'kill_command', state: 'cancelling', execution_status: 'cancelling' }), '');
  assert.equal(sem.executionPending({ name: 'kill_command', state: 'cancelling' }), false);
  assert.equal(sem.executionState({ state: 'running', execution_status: 'completed' }), 'running', 'state wins');
  assert.equal(sem.executionState({ execution_status: 'queued' }), 'queued');
  assert.equal(sem.executionPending({ execution_status: 'cancelling' }), true);
  assert.equal(sem.executionPending({ state: 'completed' }), false);
});

test('background and current calls', () => {
  assert.equal(sem.toolBackground({ name: 'spawn_subagent', summary: 'Background task started' }), true);
  assert.equal(sem.toolBackground({ name: 'spawn_subagent', summary: 'Background task started.' }), false);
  assert.equal(sem.toolBackground({ name: 'run_terminal_command', background: 1 }), true);
  assert.equal(sem.toolBackground({ name: 'grep' }), false);
  assert.equal(sem.toolIsCurrent(null), false);
  assert.equal(sem.toolIsCurrent({ name: 'grep', status: 'running', state: 'completed' }), false);
  assert.equal(sem.toolIsCurrent({ name: 'grep', status: 'done' }), false);
  assert.equal(sem.toolIsCurrent({ name: 'run_terminal_command', status: 'running', background: true, child_id: 'job' }), false, 'a background child is not current');
  assert.equal(sem.toolIsCurrent({ name: 'get_command_output', status: 'running', background: true, child_id: 'job' }), true, 'a Wait stays current');
  assert.equal(sem.toolIsCurrent({ name: 'grep', status: 'queued' }), true);
});

test('shell exit codes are outcomes, not tool failures, and keep the output', () => {
  reset();
  const m = merge();
  assert.equal(sem.shellExitCode({ name: 'grep', exit_code: 1 }), null);
  assert.equal(sem.shellExitCode({ name: 'run_terminal_command', exit_code: NaN }), null);
  assert.equal(sem.shellExitCode({ name: 'kill_command', exit_code: -9 }), -9);
  for (const error of ['', 'exit 1', 'Exit -9', 'Command exited with code 137', 'command exited with code -15']) {
    assert.equal(sem.shellExitOnly({ name: 'run_terminal_command', exit_code: -9, error }), true, error);
  }
  assert.equal(sem.shellExitOnly({ name: 'monitor', exit_code: 2, error: 'script exploded' }), false);
  assert.equal(sem.shellExitOnly({ name: 'run_terminal_command', error: 'exit 1' }), false, 'no numeric code');
  const killed = { name: 'run_terminal_command', status: 'error', exit_code: -9, error: 'Command exited with code -9', output: 'died 😬' };
  assert.equal(sem.toolFailed(killed), false);
  assert.equal(killed.output, 'died 😬');
  assert.equal(m.toolPresentation(killed, false).warning, true);
  assert.equal(m.toolPresentation(killed, false).statusLabel, 'Exit -9');
  assert.equal(sem.toolFailed({ name: 'grep', status: 'error', state: 'completed' }), false, 'a completed execution is not a failure');
  assert.equal(sem.toolFailed({ name: 'grep', status: 'failed', error: 'no such file' }), true);
});

test('presentation names queued, cancelling, lost, timed-out and stopped executions', () => {
  reset();
  const m = merge();
  const shell = (extra) => ({ name: 'run_terminal_command', status: 'running', ...extra });
  const label = (trace, live) => m.toolPresentation(trace, live).statusLabel;
  assert.equal(label(shell({ execution_status: 'queued' }), true), 'Queued');
  assert.equal(label(shell({ execution_status: 'cancelling' }), true), 'Cancelling');
  assert.equal(label(shell({ state: 'lost', status: 'error' }), false), 'Lost');
  assert.equal(label(shell({ state: 'timed_out', status: 'error' }), false), 'Timed out');
  const uncertain = m.toolPresentation(shell({ state: 'cancelled', status: 'interrupted', termination_confirmed: false }), false);
  assert.equal(uncertain.statusLabel, 'Cancelled');
  assert.equal(uncertain.state, 'interrupted');
  assert.equal(label(shell({ state: 'interrupted', status: 'interrupted', termination_confirmed: true }), false), 'Stopped');
  assert.equal(label({ name: 'run_terminal_command', status: 'done', state: 'running' }, false), 'Background');
  assert.equal(label({ name: 'grep', status: 'running', background: true }, true), 'Background');
  assert.equal(m.toolInterrupted({ name: 'grep', status: 'running', state: 'completed' }, false), false);
  assert.equal(m.toolInterrupted({ name: 'run_terminal_command', status: 'running', child_id: 'job' }, false), false, 'a child outlives its turn');
  assert.equal(m.toolInterrupted({ name: 'grep', status: 'queued' }, false), true, 'a plain call left running is interrupted');
  assert.equal(m.toolRunning({ name: 'monitor', status: 'progress', child_id: 'm' }, false), true);
});

test('foreground calls run only while live; background executions outlive the turn', () => {
  reset();
  const m = merge();
  const fg = { name: 'run_terminal_command', status: 'running' };
  assert.equal(m.toolRunning(fg, true), true);
  assert.equal(m.toolRunning(fg, false), false);
  const bg = { name: 'run_terminal_command', background: true, status: 'running', child_id: 'job-1' };
  assert.equal(m.toolRunning(bg, false), true);
  sem.applyCommandResult(bg, { state: 'completed', exit_code: null, duration_ms: 10, output: 'done' });
  assert.equal(m.toolRunning(bg, false), false);
  assert.equal(bg.exit_code, undefined);
  const receipt = { name: 'run_terminal_command', background: true, backgroundResult: true, status: 'done', child_id: 'job-2' };
  sem.applyCommandResult(receipt, { state: 'cancelled', exit_code: null, duration_ms: null, error: 'cancelled', output: '' });
  assert.equal(receipt.status, 'interrupted');
  assert.equal(m.toolRunning(receipt, false), false);
  assert.equal(m.toolRunning({ name: 'spawn_subagent', status: 'done', execution_status: 'running' }, false), true);
});

test('a Wait is an invocation: done once returned, never spinning for its child', () => {
  reset();
  const m = merge();
  for (const name of ['get_command_output', 'get_subagent_output']) {
    for (const execution_status of ['running', 'queued', 'cancelling', 'completed', 'failed']) {
      const p = m.toolPresentation({ name, child_id: 'child', status: 'done', execution_status }, true);
      assert.equal(p.running, false);
      assert.equal(p.statusLabel, 'Done');
    }
    for (const execution_status of [undefined, 'running', 'cancelling']) {
      const trace = { name, child_id: 'child', status: 'running', execution_status };
      assert.equal(m.toolPresentation(trace, true).running, true);
      assert.equal(m.toolPresentation(trace, false).running, false);
      assert.equal(m.toolPresentation(trace, false).interrupted, true);
    }
  }
});

test('summary counts name each state and never drop a category', () => {
  reset();
  const m = merge();
  const items = [
    { name: 'grep', status: 'done' }, { name: 'grep', status: 'error', error: 'boom' }, { name: 'grep', status: 'interrupted' },
    { name: 'run_terminal_command', status: 'done', exit_code: 2 }, { name: 'grep', status: 'running' }, { name: 'grep', status: 'queued' },
    { name: 'run_terminal_command', status: 'running', background: true, child_id: 'job-1' },
  ].map((trace) => ({ trace }));
  const counts = m.toolCounts(items, true);
  assert.deepEqual([counts.running, counts.failed, counts.stopped, counts.warning, counts.done], [3, 1, 1, 1, 1]);
  assert.deepEqual({ ...counts.phases }, { running: 1, queued: 1, background: 1 });
  assert.equal(sem.countsLabel(counts), '1 running · 1 queued · 1 background · 1 done · 1 failed · 1 stopped · 1 nonzero exit');
  assert.equal(sem.countsLabel({ running: 0, failed: 0, stopped: 0, warning: 0, done: 0, phases: {} }), '');
  assert.equal(sem.countsLabel({ running: 2, failed: 1, stopped: 0, warning: 0, done: 0 }), '2 running · 1 failed');
  assert.equal(sem.countsLabel({ running: 0, failed: 0, stopped: 0, warning: 2, done: 0, phases: {} }), '2 nonzero exits');
  const stale = m.toolCounts([{ trace: { name: 'grep', status: 'running' } }], false);
  assert.deepEqual([stale.stopped, stale.running], [1, 0], 'history: a call left running is stopped');
});

test('automated row labels read envelope ids, not the handoff boilerplate', () => {
  const footer = 'A <monitor_event> means the watch fired. A <monitor_result state="timed_out"> means it ended. A <command_result> is a job.';
  for (const empty of [footer, '', null]) assert.equal(sem.autoEventLabel(empty), 'Automated · background event');
  assert.equal(sem.autoEventLabel('[Fuse] AUTOMATED EVENT\n<command_result id="ab12" state="completed">\nexit 0\n</command_result>\n' + footer), 'Automated · background command result');
  assert.equal(sem.autoEventLabel('<task id="c1" state="completed">report</task>'), 'Automated · background subagent report');
  assert.equal(sem.autoEventLabel('<monitor_event id="m1" seq="1">line</monitor_event>'), 'Automated · monitor event');
  assert.equal(sem.autoEventLabel('<monitor_result id="m9" state="timed_out"></monitor_result>'), 'Automated · monitor event');
  assert.equal(sem.autoEventLabel('SCHEDULED RUN\n<command_result id="a"></command_result>'), 'Automated · scheduled run');
  assert.equal(sem.autoEventLabel('<monitor_event id="m1"></monitor_event>\n<command_result id="ab12" state="completed"></command_result>'), 'Automated · background results');
  assert.equal(sem.autoEventLabel('<monitor_result id="m9"></monitor_result>\n<task id="c1" state="completed"></task>'), 'Automated · background results');
});

test('command envelopes parse monitors and signals and keep output off the metadata', () => {
  const text = [
    '<command_result id="job-9" state="interrupted">', 'host: gpu-box command: kill -9 sleep', 'exit_code: -9',
    'duration_ms: 1500', 'error: killed', 'description: Stop the sleeper', '', 'died at 😬', 'line two', '</command_result>',
    '<monitor_event id="mon-1" seq="3" count="4">', 'url: https://example.test/feed', 'source: ws', 'events: no', '',
    'first event line', '</monitor_event>',
    '<monitor_result id="mon-1" state="completed">', 'host: box source: log watch', 'duration_ms: nope', '</monitor_result>',
  ].join('\n');
  const [cmd, event, done] = sem.parseCommandResults(text);
  assert.deepEqual([cmd.exit_code, cmd.output, cmd.host, cmd.command, cmd.duration_ms, cmd.description, cmd.error, cmd.monitor],
    [-9, 'died at 😬\nline two', 'gpu-box', 'kill -9 sleep', 1500, 'Stop the sleeper', 'killed', false]);
  assert.equal(event.tag, 'monitor_event');
  assert.equal(event.state, 'running', 'an event without a state is live');
  assert.equal(event.event_count, 4, 'a non-numeric events: line falls back to count');
  assert.equal(event.url, 'https://example.test/feed');
  assert.equal(event.command, '', 'a url: line is not a command');
  assert.equal(event.output, 'first event line');
  assert.equal(done.command, 'log watch', 'a source: line is the monitor target');
  assert.equal(done.duration_ms, null, 'non-numeric duration is absent, not NaN');
  assert.equal(done.exit_code, null);
  const [bare] = sem.parseCommandResults('<monitor_event id="mon-2" count="5">\nurl: wss://h/ws\n\nline\n</monitor_event>');
  assert.deepEqual([bare.duration_ms, bare.timeout_ms, bare.event_count], [null, null, 5], 'missing lines are absent, not 0');
  const trace = { name: 'monitor', child_id: 'mon-2', latency_ms: 4200 };
  sem.applyCommandResult(trace, bare);
  assert.equal(trace.latency_ms, 4200, 'an event without a duration keeps the real latency');
  assert.equal(sem.parseCommandResults('no envelopes here').length, 0);
  assert.equal(sem.parseCommandResults('<command_result id="x">no close').length, 0);
});

test('a wake step keeps a monitor event live and a killed command interrupted', () => {
  const [killed] = sem.parseCommandResults('<command_result id="job-9" state="interrupted">\nexit_code: -9\n\ndied mid-line\n</command_result>');
  const step = sem.wakeStepTrace(killed);
  assert.deepEqual([step.name, step.status, step.exit_code, step.summary, step.backgroundResult],
    ['run_terminal_command', 'interrupted', undefined, 'died mid-line', true]);
  const [event] = sem.parseCommandResults('<monitor_event id="mon-1" seq="2">\nurl: wss://h/ws\n\nline one\n</monitor_event>');
  const live = sem.wakeStepTrace(event);
  assert.deepEqual([live.name, live.status, live.state, live.error, live.summary], ['monitor', 'running', 'running', '', 'line one']);
  const [bad] = sem.parseCommandResults('<monitor_result id="mon-1" state="timed_out">\nerror: gave up\n</monitor_result>');
  const failed = sem.wakeStepTrace(bad);
  assert.deepEqual([failed.status, failed.error], ['error', 'gave up']);
});

test('auto handoffs fold every delivered result in order and settle matching calls', () => {
  const command = { name: 'run_terminal_command', child_id: 'job-1', command: 'npm test', description: 'Run tests', status: 'running' };
  const report = '<task id="c1" state="completed">\n<summary>Audit</summary>\n<task_result>\nQuotes </task> safely.\n</task_result>\n</task>\ntask_id: c1 (pass task_id to continue this subagent)';
  const rows = [
    { role: 'user', auto: true, content: 'hello' },
    { role: 'assistant', content: '', tools: [command, { name: 'grep', child_id: 'job-1' }] },
    { role: 'user', content: '<command_result id="job-1" state="completed">\nexit_code: 0\n</command_result>' },
    { role: 'user', auto: true, content: '<command_result id="unknown" state="completed">\nexit_code: 1\nerror: exit 1\n</command_result>' },
  ];
  sem.foldAutoCommandResults(rows);
  assert.equal(rows[0].autoSteps, null);
  assert.equal(rows[2].autoSteps, undefined, 'a plain user row is not a handoff');
  assert.equal(command.status, 'running', 'an unmatched envelope never touches the command');
  assert.equal(rows[3].autoSteps[0].status, 'done', 'exit 1 still completed');
  assert.equal(rows[3].autoSteps[0].description, '');

  rows.push({ role: 'user', auto: true, content: `${report}\n<command_result id="job-1" state="completed">\nhost: box command: nohup x\nexit_code: 0\n</command_result>\n<task id="c2" state="cancelled">\nbare report\n</task>` });
  sem.foldAutoCommandResults(rows);
  const steps = rows[4].autoSteps;
  assert.deepEqual(steps.map((s) => [s.name, s.status]), [['spawn_subagent', 'done'], ['run_terminal_command', 'done'], ['spawn_subagent', 'interrupted']]);
  assert.equal(steps[0].summary, 'Quotes </task> safely.', 'a quoted close tag does not cut the report');
  assert.equal(steps[0].description, 'Audit');
  assert.deepEqual([steps[1].child_id, steps[1].exit_code, steps[1].backgroundResult], ['job-1', 0, true]);
  assert.equal(steps[2].summary, 'bare report');
  assert.equal(command.status, 'done', 'the result settles its call');
});

test('applying a result keeps output, caps on code points and never revives a settled call', () => {
  const kept = { name: 'run_terminal_command', status: 'running', output: 'already kept' };
  sem.applyCommandResult(kept, { state: 'completed', exit_code: 0, duration_ms: 9, output: 'shorter preview' });
  assert.deepEqual([kept.status, kept.exit_code, kept.output, kept.summary, kept.latency_ms], ['done', 0, 'already kept', undefined, 9]);
  const live = { name: 'run_terminal_command', status: 'running' };
  sem.applyCommandResult(live, { state: 'interrupted', exit_code: -9, duration_ms: 40, error: 'killed', output: 'partial 😀 output' });
  assert.deepEqual([live.status, live.exit_code, live.summary, live.output_truncated], ['interrupted', undefined, 'partial 😀 output', false]);
  const capped = { name: 'run_terminal_command', status: 'running' };
  sem.applyCommandResult(capped, { state: 'completed', exit_code: null, duration_ms: null, output: '😀'.repeat(4001) });
  assert.equal(capped.summary.length, 4000);
  assert.equal(capped.output_truncated, true);
  assert.ok(!capped.summary.includes('\uFFFD') && capped.summary.endsWith('😀'), 'the cap never splits an emoji');
  const settled = { name: 'run_terminal_command', status: 'done' };
  sem.applyCommandResult(settled, { state: 'running', exit_code: null, duration_ms: null, output: '' });
  assert.deepEqual([settled.status, settled.state, settled.summary], ['done', 'running', 'running']);
  const kill = { name: 'kill_command', status: 'done', state: 'cancelling' };
  sem.applyCommandResult(kill, { state: 'completed', exit_code: 0, duration_ms: 1, output: 'gone' });
  assert.deepEqual(kill, { name: 'kill_command', status: 'done', state: 'cancelling' });
  for (const status of ['running', 'done', 'interrupted']) {
    const wait = { name: 'get_command_output', status, child_id: 'job', summary: 'observed' };
    const before = { ...wait };
    sem.applyCommandResult(wait, { state: 'failed', error: 'later failure', output: 'later output' });
    assert.deepEqual(wait, before, 'a Wait keeps what it observed');
  }
});

test('agent reconciliation: dead children have no report, delivered reports stay whole, Waits stay put', () => {
  const trace = { name: 'spawn_subagent', child_id: 'child', status: 'running' };
  reset({ messages: [{ tools: [trace] }] });
  const m = merge();
  const apply = (row) => { m.reconcileAgentTraces([row]); return trace.summary; };
  for (const status of ['failed', 'cancelled', 'lost', 'timed_out']) {
    assert.match(apply({ id: 'child', status, error: status === 'failed' ? 'boom' : '' }), /No completed report is available\./);
  }
  assert.match(apply({ id: 'child', status: 'interrupted', message: { notice: 'host gone' } }), /interrupted: host gone\. No completed report/);
  assert.doesNotMatch(apply({ id: 'child', status: 'interrupted', error: 'worked through step 2' }), /^worked through step 2/, 'partial output is not the report');
  assert.equal(apply({ id: 'child', status: 'completed', summary: 'list preview', message: { content: 'full report' } }), 'full report');
  trace.summary = 'kept';
  m.reconcileAgentTraces([{ id: 'child', status: 'running', summary: 'still going' }]);
  assert.equal(trace.summary, 'kept', 'a live child does not gain a report');

  const report = 'Final report. '.repeat(100);
  const tools = [
    { name: 'spawn_subagent', child_id: 'c2', status: 'done', state: 'running', execution_status: 'running', summary: report },
    { name: 'get_subagent_output', child_id: 'c2', status: 'running' },
    { name: 'get_subagent_output', child_id: 'c2', status: 'done', execution_status: 'running' },
  ];
  reset({ messages: [{ tools }] });
  m.reconcileAgentTraces([{ id: 'c2', status: 'completed', summary: report.slice(0, 200) }]);
  assert.equal(tools[0].execution_status, 'completed');
  assert.equal(m.toolRunning(tools[0], false), false, 'state and execution_status agree');
  assert.equal(tools[0].summary, report, 'a list preview does not truncate the delivered report');
  assert.equal(tools[1].status, 'running', 'only the tool result completes an in-flight wait');
  assert.equal(tools[2].status, 'done');
});

test('background polling settles nested-agent calls and never rewrites Waits', () => {
  const trace = { name: 'run_terminal_command', child_id: 'job', status: 'done', execution_status: 'running', background: true };
  let paints = 0;
  reset({ agentRun: { messages: [{ role: 'assistant', tools: [trace] }] }, agentsOpen: true });
  const m = merge(stub({ scheduleAgentView: () => { paints += 1; } }));
  m.reconcileBgTraces([{ id: 'job', state: 'completed', output: 'Final output', output_chars: 12, exit_code: 0 }]);
  assert.equal(m.toolPresentation(trace, false).statusLabel, 'Done');
  assert.equal(trace.output, 'Final output');
  assert.equal(paints, 1);
  reset();
  for (const state of ['completed', 'failed', 'cancelled', 'lost']) {
    for (const status of ['running', 'done', 'error', 'interrupted']) {
      const wait = { name: 'get_command_output', child_id: 'job', status, state: 'running', output: 'observed' };
      const before = { ...wait };
      S.messages = [{ role: 'assistant', tools: [wait] }];
      m.reconcileBgTraces([{ id: 'job', state, output: 'later output', output_chars: 12 }], false);
      assert.deepEqual(wait, before);
    }
  }
});

test('a late cancel acknowledgement cannot install a run in an idle thread', () => {
  reset();
  const status = createStatus({ agents: stub(), apply: stub(), composer: stub(), net: stub(), picker: stub(), sidebar: stub(), traces: { ...sem, ...merge() }, transcript: stub(), turns: stub(), ui: stub() });
  status.applyCancelResult({ run: { id: 'old-run', status: 'running', cancel_requested: true } }, 'old-run');
  assert.equal(S.activeRun, null);
  S.activeRun = { id: 'new-run', status: 'running' };
  status.applyCancelResult({ run: { id: 'old-run', status: 'cancelled' } }, 'old-run');
  assert.equal(S.activeRun.id, 'new-run');
});

test('agent snapshots keep activity, replay events in order and never regress the cursor', () => {
  reset({ agentPendingEvents: [{ id: 12, type: 'agent.cancel_requested', payload: { run_id: 'child', status: 'running', cancel_requested: true } }] });
  const v = view();
  assert.equal(v.acceptAgentSnapshot({ id: 'child', event_cursor: 10, status: 'running', message: {}, activity: { phase: 'model' } }), true);
  assert.equal(S.agentRun.message._activity.phase, 'model');
  assert.equal(S.agentRun.cancel_requested, true);
  assert.equal(S.agentRun.event_cursor, 12);
  assert.equal(v.acceptAgentSnapshot({ id: 'child', event_cursor: 11, status: 'running', message: {} }), false, 'older snapshot refused');
  S.agentPendingEvents = [{ id: 14, type: 'agent.resumed', payload: { run_id: 'child' } }];
  v.acceptAgentSnapshot({ id: 'child', event_cursor: 13, status: 'cancelled', cancel_requested: true, message: {} });
  assert.deepEqual([S.agentRun.status, S.agentRun.cancel_requested], ['queued', false]);

  const applied = [];
  reset({ agentPendingEvents: [
    { id: 22, type: 'agent.progress', payload: { run_id: 'child', n: 22 } },
    { id: 21, type: 'agent.progress', payload: { run_id: 'child', n: 21 } },
  ] });
  view({ sync: stub({ applyProgressPayload: (_msg, payload) => applied.push(payload.n) }) }).acceptAgentSnapshot({
    id: 'child', event_cursor: 18, status: 'running', message: {}, replay: [
      { id: 19, type: 'agent.progress', payload: { run_id: 'child', n: 19 } },
      { id: 20, type: 'agent.progress', payload: { run_id: 'other', n: 0 } },
      { id: 21, type: 'agent.progress', payload: { run_id: 'child', n: 21 } },
    ] });
  assert.deepEqual(applied, [19, 21, 22], 'replay tail first, then queued socket events, each once');
  assert.equal(S.agentRun.event_cursor, 22);
});

test('an agent list read overtaken by a lifecycle event is dropped and replaced', async () => {
  reset({ currentId: 'thread', generation: 1, agentsRequest: null, agentsRefreshAgain: false,
    agentLifecycleEpoch: 0, agentLifecycle: new Map(), agentsRows: [], agentsFailCount: 0 });
  const pending = [];
  const v = view({ net: stub({ api: () => new Promise((resolve) => pending.push(resolve)) }) });
  const first = v.refreshAgents();
  S.agentLifecycleEpoch += 1;
  await v.refreshAgents();
  pending.shift()({ agents: [{ id: 'child', status: 'running' }] });
  await first;
  assert.equal(S.agentsRows.length, 0, 'stale running state is not published');
  assert.equal(pending.length, 1, 'the lifecycle refresh is not dropped');
  pending.shift()({ agents: [{ id: 'child', status: 'completed' }] });
  await tick();
  assert.equal(S.agentsRows[0].status, 'completed');
});

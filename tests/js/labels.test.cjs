// Label and number formatting from the runtime modules (static/chat/core.js,
// sidebar/threads.js, agents/live.js): durations, token counts, usage, titles.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const core = require('../../static/chat/core.js');
const { isUntitledThread } = require('../../static/chat/sidebar/threads.js');
const { createLive } = require('../../static/chat/agents/live.js');

test('turn durations', () => {
  const cases = [[0, '0s'], [400, '0.4s'], [10_000, '10s'], [3_600_000, '1h'], [3_661_000, '1h 1m 1s']];
  for (const [ms, text] of cases) assert.equal(core.formatTurnDuration(ms), text, String(ms));
});

test('sub-second durations stay in ms and hand off above a second', () => {
  const cases = [[0, '0ms'], [450, '450ms'], [999, '999ms'], [999.6, '1.0s'], [1000, '1.0s'], [9500, '9.5s'],
    [10_000, '10s'], [25_000, '25s'], [65_000, '1m 5s'], [-5, ''], [NaN, ''], ['x', ''],
    [9940, '9.9s'], [9960, '10s'], [59_600, '1m']];
  for (const [ms, text] of cases) assert.equal(core.humanMs(ms), text, String(ms));
});

test('byte sizes take the larger unit once they round up to it, in every theme', async () => {
  for (const theme of ['console', 'tail', 'desktop']) {
    const { ccBytes } = (await import(`../../static/themes/${theme}/call-semantics.js`)).create({});
    const cases = [[1023, '1023 bytes'], [1536, '1.5KB'], [1_048_575, '1MB'], [5 * 1024 ** 2, '5MB'], [1024 ** 3 - 1, '1GB']];
    for (const [n, text] of cases) assert.equal(ccBytes(n), text, `${theme} ${n}`);
  }
});

test('a Desktop todo row says what the call changed since the list before it', async () => {
  const { todoChangeText } = (await import('../../static/themes/desktop/tool-formatting.js')).create({});
  const list = (...statuses) => statuses.map((status, index) => ({ id: String(index + 1), content: `todo ${index + 1}`, status }));
  assert.equal(todoChangeText([], list('pending', 'pending', 'pending')), 'Added 3 todos');
  assert.equal(todoChangeText(list('pending', 'pending'), list('completed', 'completed')), 'Marked 2 todos finished');
  assert.equal(todoChangeText(list('in_progress', 'pending'), list('completed', 'in_progress', 'pending', 'pending')), 'Added 2 todos, marked 1 finished, started 1');
  assert.equal(todoChangeText(list('pending', 'pending'), list('pending')), 'Removed 1 todo');
  assert.equal(todoChangeText(list('pending'), list('cancelled')), 'Cancelled 1 todo');
  // Nothing to compare with (a subagent's list), or nothing changed.
  assert.equal(todoChangeText(null, list('pending')), 'Updated todos');
  assert.equal(todoChangeText(list('pending'), list('pending')), 'Updated todos');
});

test('think tags split as the server does: openers only at the start, closers outside code', () => {
  const split = (text) => { const r = core.splitThink(text); return [r.reasoning, r.content, r.inThink]; };
  const F = '```';
  assert.deepEqual(split('<think>plan</think>Answer'), ['plan', 'Answer', false]);
  assert.deepEqual(split('<think>plan so f'), ['plan so f', '', true], 'a streaming thought stays open');
  assert.deepEqual(split('<think>a</think> <thinking>b</thinking>C'), ['ab', ' C', false]);
  assert.deepEqual(split('Use <think> tags.\nMore'), ['', 'Use <think> tags.\nMore', false], 'an opener after visible text is prose');
  assert.deepEqual(split('Write `</think>` here'), ['', 'Write `</think>` here', false], 'quoted in an inline span');
  assert.deepEqual(split(`${F}\n<think>x</think>\n${F}\nok`), ['', `${F}\n<think>x</think>\n${F}\nok`, false], 'quoted in a fence');
  assert.deepEqual(split('<think>tag `</think>` ends</think>Done'), ['tag `</think>` ends', 'Done', false]);
  assert.deepEqual(split('opened by the prompt</think>Answer'), ['', 'opened by the promptAnswer', false], 'a bare closer is dropped');
  assert.deepEqual(split('<think>a `b </think>'), ['a `b </think>', '', true], 'a span that may still close holds the closer as text');
  assert.deepEqual(split('<think>a `b </think>\nAnswer'), ['a `b ', '\nAnswer', false], 'until its line settles');
  assert.deepEqual(split('<think>a</thi'), ['a</thi', '', true], 'a partial tag is text');
});

test('reasoning deltas take the first key set, never several', () => {
  const reasoning = (delta) => core.deltaParts({ choices: [{ delta }] }).reasoning;
  assert.equal(reasoning({ reasoning_content: 'same', reasoning: 'same' }), 'same');
  assert.equal(reasoning({ reasoning_content: '', thinking: 't' }), 't');
  assert.equal(core.deltaParts({ choices: [{ delta: {} }], reasoning_text: 'top' }).reasoning, 'top');
});

test('token counts abbreviate at thousand and million', () => {
  const cases = [[0, '0'], [999, '999'], [1000, '1k'], [1500, '1.5k'], [9999, '10k'], [10_500, '11k'],
    [999_999, '1M'], [1_000_000, '1M'], [1_500_000, '1.5M'], [12_000_000, '12M'], [-7, '0'], [NaN, '0']];
  for (const [n, text] of cases) assert.equal(core.formatTokens(n), text, String(n));
});

test('usage splits input and output tokens', () => {
  assert.equal(core.usageInOut(null), null);
  assert.equal(core.usageInOut('nope'), null);
  assert.deepEqual({ ...core.usageInOut({ prompt_tokens: 10, output_tokens: 5 }) }, { inn: 10, out: 5 });
  assert.deepEqual({ ...core.usageInOut({ input_tokens: 7, completion_tokens: 2 }) }, { inn: 7, out: 2 });
  assert.deepEqual({ ...core.usageInOut({ prompt_tokens: 3, input_tokens: 9 }) }, { inn: 3, out: 0 }, 'prompt_tokens wins');
  assert.equal(core.usageInOut({ prompt_tokens: 0, output_tokens: 0 }), null, 'no usage recorded');
  assert.deepEqual({ ...core.usageInOut({ prompt_tokens: -5, output_tokens: '3' }) }, { inn: 0, out: 3 }, 'never negative');
  assert.equal(core.usageInOut({ prompt_tokens: 'x' }), null);
});

test('turn meta shows output tokens and elapsed time', () => {
  const bits = core.formatTurnMeta({ usage: { prompt_tokens: 100, output_tokens: 500 }, elapsedMs: 1500 });
  assert.deepEqual(bits.map((b) => ({ ...b })), [
    { text: '500 tok', title: '500 output tokens this reply' },
    { text: '1.5s', title: '1.5s elapsed' },
  ]);
  assert.equal(core.formatTurnMeta({ usage: { prompt_tokens: 100 } }).length, 0, 'output tokens only');
});

test('untitled threads hide their title', () => {
  for (const thread of [null, {}, { title: '   ' }, { title: 'New Chat' }, { kind: 'project', project_name: 'Fuse', title: ' fuse ' }]) {
    assert.equal(isUntitledThread(thread), true, JSON.stringify(thread));
  }
  assert.equal(isUntitledThread({ title: 'Fix the router' }), false);
  assert.equal(isUntitledThread({ kind: 'project', project_name: 'Fuse', title: 'Fuse Dev' }), false);
  assert.equal(isUntitledThread({ project_name: 'Fuse', title: 'Fuse' }), false, 'only project threads borrow the project name');
});

test('agent rows say stopping only while the stopped agent is still live', () => {
  const live = createLive({ ui: { formatActivityClock: (secs) => `${Math.round(secs)}s` } });
  const ago = (secs) => new Date(Date.now() - secs * 1000).toISOString();
  assert.equal(live.agentStatusText({ status: 'running', cancel_requested: true, cancel_requested_at: ago(1) }), 'stopping');
  assert.equal(live.agentStatusText({ status: 'running', cancel_requested: true, cancel_requested_at: ago(40) }), 'wedged — force stop');
  assert.equal(live.agentStatusText({ status: 'cancelled', cancel_requested: true, cancel_requested_at: ago(40) }), 'cancelled');
  assert.equal(live.agentStatusText({ status: 'completed', cancel_requested: true, cancel_requested_at: ago(40) }), 'completed');
});

test('desktop status line: a thought is one word, whatever it says', async () => {
  const status = (await import('../../static/themes/desktop/live-status.js')).create({});
  const thinking = { segments: [{ type: 'reasoning', text: 'x\n**Checking the FK delete rules**\ny' }] };
  assert.equal(status.ccThinkingState(thinking, 'thinking'), 'thinking', 'no heading from the thought');
  assert.equal(status.ccThinkingState({ segments: [{ type: 'reasoning', text: 'plain' }] }, 'thinking'), 'thinking');
  assert.equal(status.ccThinkingState(thinking, 'writing'), '', 'only while thinking');
});

test('desktop status line: one word between calls, each line held long enough to read, the turn clock throughout', async (t) => {
  const lexicon = (await import('../../static/themes/desktop/verb-lexicon.js')).create({});
  const words = (await import('../../static/themes/desktop/call-semantics.js')).create(lexicon);
  let now = 1_000_000;
  t.mock.method(Date, 'now', () => now);
  let activity = { phase: 'thinking' };
  const status = (await import('../../static/themes/desktop/live-status.js')).create({
    ccFlat: words.ccFlat, ccRunningForm: words.ccRunningForm, ccLiveText: () => '', ccPathHint: () => '',
    currentActivity: () => activity, liveElapsedSeconds: () => 5, ccDuration: (ms) => `${ms / 1000}s`,
    isSearchTool: () => false, toolBackground: () => false, executionState: () => '', displayCommand: (trace) => trace.command,
  });
  const msg = { segments: [{ type: 'reasoning', text: 'plain' }], tools: [] };
  const line = () => status.formatConsoleLiveStatus(msg);
  assert.equal(line().verb, 'Thinking…');
  assert.equal(line().phase, 'thinking');
  assert.deepEqual(line().meta, { clock: '5s' }, 'the turn clock');
  activity = { phase: 'working', current: { id: 'run-1', name: 'run_terminal_command', description: 'Run the test suite twice', command: 'make test', started_ms: now } };
  now += 300;
  assert.equal(line().verb, 'Thinking…', 'a line shown 300ms ago stays up');
  now += 500;
  assert.equal(line().verb, 'Running the test suite twice…');
  assert.equal(line().key, 'run-1', 'the line names its call, for the live pane');
  assert.deepEqual(line().meta, { clock: '5s' }, 'a command keeps the turn clock; its own time is in the live pane');
  // The call ends, the request goes out, the thought starts: no past tense, one word.
  for (const phase of ['working', 'waiting', 'starting', 'thinking']) {
    activity = { phase };
    now += 800;
    assert.equal(line().verb, 'Thinking…', phase);
  }
  activity = { phase: 'queued' };
  assert.equal(line().verb, 'Queued…', 'a line up long enough gives way at once');
  activity = { phase: 'thinking' };
  now += 100;
  assert.equal(line().verb, 'Queued…', 'held');
  activity = { phase: 'stopping' };
  assert.equal(line().verb, 'Stopping…', 'a stop shows at once');
});

test('desktop live pane: what the line names, a run_code with the calls its code makes', async () => {
  const dock = (await import('../../static/themes/desktop/live-dock.js')).create({});
  const msg = { tools: [{ id: 'a', name: 'read_file' }, { id: 'b', name: 'run_terminal_command' }, { id: 'c', name: 'run_code' },
    { id: 'c1', name: 'grep', parent: 'c' }, { id: 'c2', name: 'read_file', parent: 'c' }] };
  assert.equal(dock.paneTrace(msg, 'b').id, 'b', 'the call the line names, not the newest');
  assert.equal(dock.paneTrace(msg, ''), null, 'a line that names no call shows none');
  assert.equal(dock.paneTrace(msg, 'c').id, 'c', 'code shows as itself');
  assert.equal(dock.paneTrace(msg, 'c2').id, 'c', 'a call its code made shows as part of the code');
  assert.deepEqual(dock.paneCalls(msg, msg.tools[2]).map((t) => t.id), ['c1', 'c2'], 'its calls, in the order they started');
  assert.deepEqual(dock.paneCalls(msg, msg.tools[1]), [], 'any other call has none');
  const thought = { segments: [{ type: 'reasoning', text: 'old' }, { type: 'tool', id: 'a' },
    { type: 'reasoning', text: '**Checking the loop**\nIt exits' }, { type: 'reasoning', text: 'then more' }] };
  assert.equal(dock.paneThought(thought), 'Checking the loop\nIt exits\n\nthen more', 'the newest thought only, without its markdown marks');
});

test('desktop rows read as sentences: a described command in the past tense once it finished, a file by name then folder', async () => {
  const lexicon = (await import('../../static/themes/desktop/verb-lexicon.js')).create({});
  const words = (await import('../../static/themes/desktop/call-semantics.js')).create(lexicon);
  const rows = (await import('../../static/themes/desktop/tool-formatting.js')).create({
    ...words, escapeHtml: String, ccCallClockHtml: () => '', ccLiveText: () => '', ccSedEdit: () => null,
    ccExitMeaning: (_command, exit) => ({ isError: exit > 0 }), shellExitCode: (trace) => trace.exit_code ?? null,
    toolBackground: () => false, sessionProject: () => null, messages: [],
    consoleVerb: (name) => ({ run_terminal_command: 'Run', read_file: 'Read', grep: 'Search', list_dir: 'List' })[name],
  });
  const text = (trace, presentation = { state: 'done' }) => rows.consoleCallHtml(trace, presentation).replace(/<[^>]*>|[()]/g, '');
  const run = { name: 'run_terminal_command', command: 'make test', description: 'Run the test suite twice' };
  assert.equal(text(run), 'Ranthe test suite twice', 'the inflected verb is its own word, the rest the subject');
  assert.equal(text({ ...run, exit_code: 2 }, { state: 'warning' }), 'Failed to runthe test suite twice', 'a failed one says so: it did not do what it says');
  assert.equal(text(run, { state: 'interrupted', interrupted: true }), 'Stopped runningthe test suite twice', 'so does one cut short');
  assert.equal(text({ name: 'read_file', path: 'app/main.py' }, { state: 'error', failed: true }), 'Readmain.py in app');
  assert.equal(text({ ...run, description: 'Unit tests' }), 'RanUnit tests', 'no verb to inflect: the verb stays its own word');
  assert.equal(text({ name: 'read_file', path: 'app/main.py' }), 'Readmain.py in app');
  assert.equal(text({ name: 'read_file', path: 'README.md' }), 'ReadREADME.md');
  assert.equal(text({ name: 'grep', pattern: 'def main', path: 'app' }), 'Searched"def main" in app');
  const root = { project_root: '/srv/demo', path: '/srv/demo' };
  assert.equal(text({ name: 'grep', pattern: 'x', ...root }), 'Searched"x"', 'the project folder itself is not "in ."');
  assert.equal(text({ name: 'list_dir', ...root }), 'Listeddemo', 'it reads by its name');
});

test('desktop agents table: what a subagent did, in words; how a finished one ended', async () => {
  const panels = (await import('../../static/themes/desktop/work-panels.js')).create({
    agentLive: (status) => status === 'running', agentDurationSeconds: () => 30, ccAgentLastCall: () => '',
    agentStatusText: (row) => row.status, jobsFor: () => [], escapeHtml: String, agentIsStalled: () => false,
    agentResumeAvailable: () => false, formatActivityClock: (secs) => `${secs}s`, agentsRows: [],
  });
  const doing = (row) => /class="chat-agents-doing">([^<]*)</.exec(panels.agentRowHtml({ id: 'a', description: 'Audit', ...row }))?.[1];
  assert.equal(doing({ status: 'completed', tool_counts: { read_file: 6, grep: 2, glob: 1, search_replace: 1, todo_write: 4 } }), '6 files, 3 searches, 1 edit');
  assert.equal(doing({ status: 'completed', summary: 'Fixed the reconnect.\nDetails follow.', tool_counts: { read_file: 1 } }), 'Fixed the reconnect.');
  assert.equal(doing({ status: 'cancelled', tool_counts: { read_file: 1 } }), 'Stopped, 1 file');
  assert.equal(doing({ status: 'running', tool_counts: { web_search: 2 } }), 'Working, 2 web searches');
  assert.equal(doing({ status: 'running' }), 'Working');
});

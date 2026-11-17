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
    [10_000, '10s'], [25_000, '25s'], [65_000, '1m 5s'], [-5, ''], [NaN, ''], ['x', '']];
  for (const [ms, text] of cases) assert.equal(core.humanMs(ms), text, String(ms));
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

test('desktop status line: the thought heading, then the newest action in the past tense', async () => {
  const lexicon = (await import('../../static/themes/desktop/verb-lexicon.js')).create({});
  const words = (await import('../../static/themes/desktop/call-semantics.js')).create(lexicon);
  const status = (await import('../../static/themes/desktop/live-status.js')).create({
    ccFlat: words.ccFlat, ccInflect: words.ccInflect, ccRunningForm: words.ccRunningForm,
    ccLiveText: (trace) => (trace.name === 'read_file' ? `Reading ${trace.path}` : ''), ccPathHint: (trace) => trace.path,
  });
  assert.equal(status.thoughtHeading('**Reading the schema**\nfirst\n\n**Checking the FK delete rules**\nThe dump…'), 'Checking the FK delete rules');
  assert.equal(status.thoughtHeading('### Planning the backup:\nsteps'), 'Planning the backup');
  assert.equal(status.thoughtHeading('no heading, **bold** inline only'), '');
  const thinking = { segments: [{ type: 'reasoning', text: 'x\n**Checking the FK delete rules**\ny' }] };
  assert.equal(status.ccThinkingState(thinking, 'thinking'), 'Checking the FK delete rules');
  assert.equal(status.ccThinkingState({ segments: [{ type: 'reasoning', text: 'plain' }] }, 'thinking'), 'thinking');
  const ran = { tools: [{ name: 'run_terminal_command', description: 'Run the test suite twice', command: 'make test' }] };
  assert.equal(status.lastActionText(ran), 'Ran the test suite twice');
  assert.equal(status.lastActionText({ tools: [{ name: 'read_file', path: 'app/main.py' }] }), 'Read app/main.py');
  assert.equal(status.lastActionText({ tools: [{ name: 'search_replace', path: 'static/chat.js' }] }), 'Edited static/chat.js');
  assert.equal(status.lastActionText({ tools: [] }), '');
});

test('desktop rows read as sentences: a described command in the past tense, a file by name then folder', async () => {
  const lexicon = (await import('../../static/themes/desktop/verb-lexicon.js')).create({});
  const words = (await import('../../static/themes/desktop/call-semantics.js')).create(lexicon);
  const rows = (await import('../../static/themes/desktop/tool-formatting.js')).create({
    ...words, escapeHtml: String, ccCallClockHtml: () => '', ccLiveText: () => '', ccSedEdit: () => null,
    ccExitMeaning: (_command, exit) => ({ isError: exit > 0 }), shellExitCode: (trace) => trace.exit_code ?? null,
    toolBackground: () => false, sessionProject: () => null, messages: [],
    consoleVerb: (name) => ({ run_terminal_command: 'Run', read_file: 'Read', grep: 'Search' })[name],
  });
  const text = (trace, presentation = { state: 'done' }) => rows.consoleCallHtml(trace, presentation).replace(/<[^>]*>|[()]/g, '');
  const run = { name: 'run_terminal_command', command: 'make test', description: 'Run the test suite twice' };
  assert.equal(text(run), 'Ranthe test suite twice', 'the inflected verb is its own word, the rest the subject');
  assert.equal(text({ ...run, exit_code: 2 }, { state: 'warning' }), 'Ranthe test suite twice', 'a failed one reads in the past tense too');
  assert.equal(text(run, { state: 'interrupted', interrupted: true }), 'Ranthe test suite twice', 'so does one cut short');
  assert.equal(text({ name: 'read_file', path: 'app/main.py' }, { state: 'error', failed: true }), 'Readmain.py in app');
  assert.equal(text({ ...run, description: 'Unit tests' }), 'RanUnit tests', 'no verb to inflect: the verb stays its own word');
  assert.equal(text({ name: 'read_file', path: 'app/main.py' }), 'Readmain.py in app');
  assert.equal(text({ name: 'read_file', path: 'README.md' }), 'ReadREADME.md');
  assert.equal(text({ name: 'grep', pattern: 'def main', path: 'app' }), 'Searched"def main" in app');
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

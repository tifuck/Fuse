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

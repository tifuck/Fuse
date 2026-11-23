// Web search setup (static/chat/websetup-core.js): when the prompt shows,
// the machine rows and which machines start ticked.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const core = () => import('../../static/chat/websetup-core.js');
const DAY = 24 * 60 * 60 * 1000;

test('the prompt shows only while web search is off, and not within a day of a dismissal', async () => {
  const { shouldPrompt, SNOOZE_MS } = await core();
  const now = 1_000_000_000;
  assert.equal(SNOOZE_MS, DAY);
  assert.equal(shouldPrompt(false, null, now), true);
  assert.equal(shouldPrompt(false, now + SNOOZE_MS, now), false);
  assert.equal(shouldPrompt(false, now - 1, now), true, 'a day after the dismissal it asks again');
  assert.equal(shouldPrompt(false, 'garbage', now), true);
  // On (picked machines, or the server allowed to go out) or unknown: never.
  assert.equal(shouldPrompt(true, null, now), false);
  assert.equal(shouldPrompt(undefined, null, now), false);
});

const overview = {
  min_version: '1.11.0',
  groups: [
    { network: '203.0.113.5', machines: [
      { id: 1, alias: 'home-a', ip: '203.0.113.5', network: '203.0.113.5', online: true, supported: true, selected: false },
      { id: 2, alias: 'home-b', ip: '203.0.113.5', network: '203.0.113.5', online: true, supported: true, selected: false },
    ] },
    { network: '198.51.100.7', machines: [
      { id: 3, alias: 'vps', ip: '198.51.100.7', network: '198.51.100.7', online: false, supported: true, selected: false },
      { id: 4, alias: 'vps2', ip: '198.51.100.7', network: '198.51.100.7', online: true, supported: true, selected: false },
    ] },
    { network: '', machines: [
      { id: 5, alias: 'old', ip: '', network: '', online: true, supported: false, selected: false },
      { id: 6, alias: 'new', ip: '', network: '', online: true, supported: true, selected: false },
      { id: 7, alias: 'other', ip: '', network: '', online: true, supported: true, selected: false },
    ] },
  ],
};

test('rows name the machines behind the same public IP; an unknown IP shares with none', async () => {
  const { setupRows } = await core();
  const rows = setupRows(overview);
  assert.deepEqual(rows.map((r) => [r.id, r.shares]), [[1, ['home-b']], [2, ['home-a']], [3, ['vps2']], [4, ['vps']], [5, []], [6, []], [7, []]]);
  assert.deepEqual(rows.map((r) => r.status), ['online', 'online', 'offline', 'online', 'needs connector 1.11.0', 'online', 'online']);
  assert.deepEqual(setupRows({}), []);
});

test('one online machine per network starts ticked; offline and outdated ones never', async () => {
  const { setupRows, suggested } = await core();
  // vps is offline, so vps2 stands for that network; 6 and 7 are not known to share one.
  assert.deepEqual(suggested(setupRows(overview)), [1, 4, 6, 7]);
});

test('a choice the user already made is kept as it is', async () => {
  const { setupRows, suggested } = await core();
  const mine = structuredClone(overview);
  mine.groups[0].machines[1].selected = true;
  mine.groups[1].machines[0].selected = true;
  assert.deepEqual(suggested(setupRows(mine)), [2, 3]);
});

test('the public IPs are read first only when a usable machine has none yet', async () => {
  const { setupRows, needsIp } = await core();
  assert.equal(needsIp(setupRows(overview)), true);
  assert.equal(needsIp(setupRows({ groups: [overview.groups[0]] })), false);
});

test('the save button says how many machines it turns on', async () => {
  const { saveLabel } = await core();
  assert.equal(saveLabel(0), 'Pick a machine');
  assert.equal(saveLabel(1), 'Use 1 machine');
  assert.equal(saveLabel(3), 'Use 3 machines');
});

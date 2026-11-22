// Protect secrets window (static/chat/secrets-core.js): the row model, the
// primary button's label, the header checkbox and the decision that is sent.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const core = () => import('../../static/chat/secrets-core.js');

test('a row is named by its key, or by its kind when the line had no NAME=', async () => {
  const { secretRows } = await core();
  const rows = secretRows([
    { id: 4, key: 'API_KEY', value: 'abc', kind: 'OpenAI key', host: 'qa-box', file: '.env', line: 3 },
    { id: 5, key: '', value: 'line one\nline two', kind: 'npm token', host: '', file: 'apps/api/.npmrc', line: 0 },
  ]);
  assert.deepEqual(rows[0], { id: 4, name: 'API_KEY', keyed: true, kind: 'OpenAI key', value: 'abc', host: 'qa-box', place: '.env:3' });
  assert.deepEqual(rows[1], { id: 5, name: 'npm token', keyed: false, kind: 'npm token', value: 'line one\nline two', host: '', place: 'apps/api/.npmrc' });
});

test('rows without a usable id are left out: the server only takes integer ids', async () => {
  const { secretRows } = await core();
  assert.deepEqual(secretRows([null, { id: '7', key: 'A', value: 'x' }, { key: 'B', value: 'y' }, { id: 2, key: 'C', value: 'z' }]).map((row) => row.id), [2]);
  assert.deepEqual(secretRows(undefined), []);
});

test('the primary button counts the ticked rows', async () => {
  const { protectLabel } = await core();
  assert.equal(protectLabel(7), 'Protect 7 selected');
  assert.equal(protectLabel(1), 'Protect 1 selected');
  assert.equal(protectLabel(0), 'Protect none');
});

test('the header checkbox is all, none or mixed', async () => {
  const { allState } = await core();
  assert.equal(allState(3, 3), 'all');
  assert.equal(allState(0, 3), 'none');
  assert.equal(allState(2, 3), 'mixed');
});

test('turning detection off protects nothing', async () => {
  const { decision } = await core();
  assert.deepEqual(decision([1, 2]), { protect: [1, 2], never: false });
  assert.deepEqual(decision([]), { protect: [], never: false });
  assert.deepEqual(decision([1, 2], true), { protect: [], never: true });
});

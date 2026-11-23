// The plan meter's idle reread (static/chat/poolbadge.js): a shown meter is
// read again once it is a minute old, never from a hidden page, and the same
// account keeps the note of the account it moved from.
const { test } = require('node:test');
const assert = require('node:assert/strict');

global.document = { hidden: false, querySelector: () => null };
const { S } = require('../../static/chat/state.js');
const { acceptPool, refreshPool } = require('../../static/chat/poolbadge.js');

test('an idle meter rereads its account once a minute while the page is visible', async () => {
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  const asked = [];
  global.fetch = async (url) => {
    asked.push(url);
    return { ok: true, json: async () => ({ pool: { name: 'Claude plan 2', id: 2, hop: null, windows: [{ label: '5-hour', percent: 50 }] } }) };
  };
  try {
    Object.assign(S, { currentId: 't', modelId: 'claude/opus', root: null, streaming: false, sending: false });
    acceptPool({ name: 'Claude plan 2', id: 2, hop: { from: 'Claude plan 1', reason: 'limit' }, windows: [{ label: '5-hour', percent: 42 }] });
    assert.equal(refreshPool(), null);
    now += 61_000;
    document.hidden = true;
    assert.equal(refreshPool(), null);
    document.hidden = false;
    await refreshPool();
    assert.deepEqual(asked, ['/chat/pool?model=claude%2Fopus&thread=t&refresh=1']);
    assert.equal(S.pool.windows[0].percent, 50);
    assert.deepEqual(S.pool.hop, { from: 'Claude plan 1', reason: 'limit' });
    assert.equal(refreshPool(), null);
  } finally {
    Date.now = realNow;
    delete global.fetch;
  }
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../static/chat/state.js');

test('a failed draft save is not retried once newer text was sent', async () => {
  const timers = [];
  global.window = {
    setTimeout: (fn) => timers.push(fn),
    clearTimeout: () => {},
    addEventListener: () => {},
  };
  const { createDrafts } = require('../static/chat/composer/drafts.js');
  const sent = [];
  const replies = [];
  const net = {
    api: (_url, opts) => {
      sent.push(JSON.parse(opts.body).text);
      return new Promise((resolve, reject) => replies.push({ resolve, reject }));
    },
  };
  S.drafts = new Map();
  createDrafts({ net, input: {}, sidebar: {}, sync: {} });
  const fire = () => timers.splice(0).forEach((fn) => fn());

  S.drafts.save('t1', 'old');
  fire();
  S.drafts.save('t1', 'newer');
  fire();
  assert.deepEqual(sent, ['old', 'newer']);
  replies[1].resolve({});
  replies[0].reject(new Error('offline'));
  await new Promise((resolve) => setImmediate(resolve));
  fire();
  assert.deepEqual(sent, ['old', 'newer']);

  S.drafts.save('t1', 'latest');
  fire();
  replies[2].reject(new Error('offline'));
  await new Promise((resolve) => setImmediate(resolve));
  fire();
  assert.deepEqual(sent, ['old', 'newer', 'latest', 'latest']);
  delete global.window;
});

test('a sync snapshot that predates an in-flight draft save does not overwrite it', async () => {
  const timers = [];
  global.window = {
    setTimeout: (fn) => timers.push(fn),
    clearTimeout: () => {},
    addEventListener: () => {},
  };
  const { createDrafts } = require('../static/chat/composer/drafts.js');
  const replies = [];
  const net = { api: () => new Promise((resolve, reject) => replies.push({ resolve, reject })) };
  class Drafts extends Map {
    remote(key, text) { return text ? super.set(key, text) : super.delete(key); }
  }
  const noop = () => {};
  Object.assign(S, { drafts: new Drafts(), currentId: 't1', input: { value: 'typed' }, stash: new Map() });
  const drafts = createDrafts({ net, input: { syncComposer: noop, resizeInput: noop, syncEmptyComposer: noop }, sidebar: { renderHistory: noop }, sync: {} });

  S.drafts.set('t1', 'typed');
  S.drafts.save('t1', 'typed');
  timers.splice(0).forEach((fn) => fn());
  drafts.acceptDrafts({ t1: 'older' });
  drafts.applyRemoteDraft('t1', 'older');
  assert.equal(S.drafts.get('t1'), 'typed');
  assert.equal(S.input.value, 'typed');

  replies[0].resolve({});
  await new Promise((resolve) => setImmediate(resolve));
  drafts.acceptDrafts({ t1: 'from another device' });
  assert.equal(S.drafts.get('t1'), 'from another device');
  assert.equal(S.input.value, 'from another device');
  Object.assign(S, { drafts: undefined, currentId: null, input: undefined, stash: undefined });
  delete global.window;
});

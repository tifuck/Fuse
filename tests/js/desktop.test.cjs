// Fuse Desktop shell logic (fuse-desktop/*.js): context menu, permission
// gate, site URL check, the post-update notice and GPU rendering state.
const test = require('node:test');
const assert = require('node:assert/strict');
const { contextMenuTemplate } = require('../../fuse-desktop/context-menu.js');
const { allowPermission } = require('../../fuse-desktop/permissions.js');
const { checkSiteUrl, isFuseHealth, serverOrigin } = require('../../fuse-desktop/site-url.js');
const { MAX_INSTALL_TRIES, launchNotice } = require('../../fuse-desktop/update-notice.js');
const { GPU_CRASH_LIMIT, GPU_CRASH_WINDOW_MS, countGpuCrash, gpuForcedOff, launchRendering, parseRendering } = require('../../fuse-desktop/rendering.js');

function fakeContents() {
  const calls = [];
  const wc = new Proxy({ session: { addWordToSpellCheckerDictionary: (w) => calls.push(['addWord', w]) } }, {
    get: (target, name) => (name in target ? target[name] : (...args) => calls.push([name, ...args])),
  });
  return { wc, calls };
}
const labels = (items) => items.map((item) => (item.type === 'separator' ? '-' : item.label));
const page = { x: 10, y: 20, linkURL: '', selectionText: '', isEditable: false, editFlags: {}, misspelledWord: '', dictionarySuggestions: [], mediaType: 'none' };

test('context menu: selection copies, nothing selected shows nothing', () => {
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, selectionText: 'some text' }, wc, {});
  assert.deepEqual(labels(items), ['Copy']);
  items[0].click();
  assert.deepEqual(calls, [['copy']]);
  assert.deepEqual(contextMenuTemplate({ ...page, selectionText: '  ' }, wc, {}), []);
});

test('context menu: text fields get the edit menu, enabled from their edit flags', () => {
  const { wc, calls } = fakeContents();
  const editFlags = { canUndo: false, canRedo: false, canCut: true, canCopy: true, canPaste: true, canSelectAll: true };
  const items = contextMenuTemplate({ ...page, isEditable: true, selectionText: 'x', editFlags }, wc, {});
  assert.deepEqual(labels(items), ['Undo', 'Redo', '-', 'Cut', 'Copy', 'Paste', '-', 'Select all']);
  assert.deepEqual(items.filter((item) => item.label).map((item) => item.enabled), [false, false, true, true, true, true]);
  items.find((item) => item.label === 'Paste').click();
  assert.deepEqual(calls, [['paste']]);
});

test('context menu: misspellings list suggestions first and can be learned', () => {
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, isEditable: true, misspelledWord: 'teh', dictionarySuggestions: ['the', 'ten'] }, wc, {});
  assert.deepEqual(labels(items).slice(0, 4), ['the', 'ten', 'Add to dictionary', '-']);
  items[0].click();
  items[2].click();
  assert.deepEqual(calls, [['replaceMisspelling', 'the'], ['addWord', 'teh']]);
});

test('context menu: only web links open or copy; images copy from the click point', () => {
  const opened = [];
  const copied = [];
  const io = { openExternal: (url) => opened.push(url), copyText: (text) => copied.push(text) };
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, linkURL: 'https://example.com/a' }, wc, io);
  assert.deepEqual(labels(items), ['Open in browser', 'Copy link']);
  items[0].click();
  items[1].click();
  assert.deepEqual([opened, copied], [['https://example.com/a'], ['https://example.com/a']]);
  assert.deepEqual(contextMenuTemplate({ ...page, linkURL: 'javascript:alert(1)' }, wc, io), []);
  const image = contextMenuTemplate({ ...page, mediaType: 'image', hasImageContents: true }, wc, io);
  assert.deepEqual(labels(image), ['Copy image']);
  image[0].click();
  assert.deepEqual(calls, [['copyImageAt', 10, 20]]);
});

test('permissions: the site\'s own top frame gets notifications, mic and clipboard write; nothing else', () => {
  const APP = 'https://fuse.example.com';
  const top = (extra) => ({ requestingUrl: `${APP}/chat`, isMainFrame: true, ...extra });
  assert.equal(allowPermission('notifications', top(), APP), true);
  assert.equal(allowPermission('media', top({ mediaTypes: ['audio'] }), APP), true);
  assert.equal(allowPermission('clipboard-sanitized-write', top(), APP), true);
  assert.equal(allowPermission('notifications', { requestingOrigin: APP, isMainFrame: true }, APP), true, 'check-handler form');
  assert.equal(allowPermission('media', top({ mediaTypes: ['audio', 'video'] }), APP), false, 'no camera');
  assert.equal(allowPermission('geolocation', top(), APP), false);
  assert.equal(allowPermission('clipboard-read', top(), APP), false);
  assert.equal(allowPermission('media', top({ isMainFrame: false, mediaTypes: ['audio'] }), APP), false, 'an artifact subframe is not the site');
  assert.equal(allowPermission('notifications', { requestingUrl: 'https://evil.example/', isMainFrame: true }, APP), false);
  assert.equal(allowPermission('notifications', { requestingOrigin: 'null' }, APP), false);
  assert.equal(allowPermission('notifications', undefined, APP), false);
  assert.equal(allowPermission('openExternal', { externalURL: 'mailto:a@b.c' }, APP), true);
  for (const url of ['ms-msdt:/id', 'file:///C:/x.exe', 'search-ms:query=x', 'smb://host/share', 'not a url']) {
    assert.equal(allowPermission('openExternal', { externalURL: url }, APP), false, url);
  }
});

test('site URL must be a full http(s) URL from the allowed list', () => {
  assert.equal(checkSiteUrl('https://fuse.example.com').origin, 'https://fuse.example.com');
  for (const value of ['fuse.example.com', '', undefined]) {
    assert.throws(() => checkSiteUrl(value), /is not a URL; use https:\/\/your-host/);
  }
  assert.throws(() => checkSiteUrl('file:///etc/passwd'), /must use https:\/\/ or http:\/\//);
  assert.throws(() => checkSiteUrl('http://fuse.example.com', ['https:']), /must use https:\/\//);
});

test('connect page: a typed server becomes an origin, https by default', () => {
  assert.equal(serverOrigin(' fuse.example.com '), 'https://fuse.example.com');
  assert.equal(serverOrigin('https://fuse.example.com/chat?x=1'), 'https://fuse.example.com');
  assert.equal(serverOrigin('http://box:8088'), 'http://box:8088');
  assert.throws(() => serverOrigin(''), /is not a URL/);
  assert.throws(() => serverOrigin('file:///etc/passwd'), /must use https:\/\/ or http:\/\//);
});

test('connect page: only a Fuse /v1/health answer counts as a Fuse server', () => {
  assert.equal(isFuseHealth({ ok: false, database: { ok: false }, background: { running: false } }), true);
  for (const body of [null, 'ok', { ok: true }, { status: 'ok', database: 'up' }]) assert.equal(isFuseHealth(body), false);
});

test('update notice: quiet on install, names versions after an update', () => {
  assert.deepEqual(launchNotice({}, '1.0.7', false), { state: { version: '1.0.7' }, toast: null });
  const legacy = launchNotice({}, '1.0.7', true);
  assert.deepEqual([legacy.toast.title, legacy.toast.msg, legacy.state], ['Fuse updated', 'Now on 1.0.7', { version: '1.0.7' }]);
  const updated = launchNotice({ version: '1.0.7', pending: { to: '1.0.8', tries: 1, installing: true } }, '1.0.8', true);
  assert.deepEqual([updated.toast.kind, updated.toast.msg, updated.state], ['ok', '1.0.7 → 1.0.8', { version: '1.0.8' }]);
  const waiting = { version: '1.0.7', pending: { to: '1.0.8', tries: 0 } };
  assert.deepEqual(launchNotice(waiting, '1.0.7', true), { state: waiting, toast: null }, 'a downloaded update is kept');
});

test('update notice: a failed install warns, counts the try, and the last one links the download', () => {
  const { state, toast } = launchNotice({ version: '1.0.7', pending: { to: '1.0.8', tries: 0, installing: true } }, '1.0.7', true);
  assert.deepEqual([toast.kind, toast.title], ['warn', "Fuse 1.0.8 didn't install"]);
  assert.deepEqual(state, { version: '1.0.7', pending: { to: '1.0.8', tries: 1 } });
  assert.equal(launchNotice(state, '1.0.7', true).toast, null, 'warned once per attempt');
  const last = launchNotice({ version: '1.0.7', pending: { to: '1.0.8', tries: MAX_INSTALL_TRIES - 1, installing: true } }, '1.0.7', true);
  assert.deepEqual([last.toast.kind, last.toast.href, last.state.pending.tries], ['err', '/download', MAX_INSTALL_TRIES]);
});

test('rendering: GPU on unless saved off; a missing or corrupt file is GPU on', () => {
  assert.deepEqual(parseRendering('{"gpu":false,"fellBack":true}'), { gpu: false, fellBack: true });
  assert.deepEqual(parseRendering('{"gpu":false}'), { gpu: false, fellBack: false });
  for (const text of ['', 'garbage', '{"gpu":', 'null', '{}']) assert.deepEqual(parseRendering(text), { gpu: true, fellBack: false }, text);
});

test('rendering: FUSE_DISABLE_GPU=1 or --disable-gpu turns the GPU off for the launch', () => {
  const none = () => false;
  assert.equal(gpuForcedOff({}, none), false);
  assert.equal(gpuForcedOff({ FUSE_DISABLE_GPU: '1' }, none), true);
  assert.equal(gpuForcedOff({ FUSE_DISABLE_GPU: '0' }, none), false);
  assert.equal(gpuForcedOff({}, (name) => name === 'disable-gpu'), true);
});

test('rendering: GPU crashes within the window fall back; clean exits and other processes do not count', () => {
  const gpu = { type: 'GPU', reason: 'crashed' };
  let crashes = [];
  for (const details of [{ type: 'GPU', reason: 'clean-exit' }, { type: 'Utility', reason: 'crashed' }]) {
    const counted = countGpuCrash(crashes, details, 1000);
    assert.deepEqual(counted, { crashes, fallBack: false }, details.type + ' ' + details.reason);
  }
  for (let i = 1; i < GPU_CRASH_LIMIT; i++) {
    const counted = countGpuCrash(crashes, i === 1 ? gpu : { type: 'GPU', reason: 'killed' }, i * 1000);
    assert.equal(counted.fallBack, false);
    crashes = counted.crashes;
  }
  assert.equal(countGpuCrash(crashes, gpu, GPU_CRASH_LIMIT * 1000).fallBack, true);
  const later = countGpuCrash(crashes, gpu, 1000 + GPU_CRASH_WINDOW_MS);
  assert.deepEqual([later.fallBack, later.crashes.length], [false, GPU_CRASH_LIMIT - 1], 'the oldest crash aged out');
});

test('rendering: the fallback notice shows once', () => {
  const first = launchRendering({ gpu: false, fellBack: true });
  assert.deepEqual(first, { state: { gpu: false, fellBack: false }, notice: true });
  assert.equal(launchRendering(first.state).notice, false);
  assert.equal(launchRendering({ gpu: true, fellBack: false }).notice, false);
  assert.equal(launchRendering({ gpu: false, fellBack: false }).notice, false, 'turned off by hand');
});

test('a script row splits what the model got from its source and counts its calls', async () => {
  for (const view of ['console', 'desktop']) {
    const { codeRunParts } = (await import(`../../static/themes/${view}/tool-formatting.js`)).create({});
    const now = codeRunParts({ output: '17\n[Fuse: 41 calls: glob×1, read_file×40]', code: 'return 17;' });
    assert.deepEqual(now, { result: '17', code: 'return 17;', calls: [['glob', 1], ['read_file', 40]] }, view);
    const before = codeRunParts({ output: 'ok\n[Fuse: 1 calls: grep×1]\n[Fuse: 1 calls were still running when the code ended and were stopped; await every call.]\n\nawait grep("x");\n\nreturn "ok";' });
    assert.deepEqual(before, { result: 'ok', code: 'await grep("x");\n\nreturn "ok";', calls: [['grep', 1]] }, `${view}: a row saved with its source in the output`);
    const failed = codeRunParts({ status: 'error', output: 'Error: line 1: SyntaxError\nNo tool calls finished before the error.\n\nreturn (;' });
    assert.deepEqual(failed, { result: 'Error: line 1: SyntaxError\nNo tool calls finished before the error.', code: 'return (;', calls: [] }, view);
  }
});

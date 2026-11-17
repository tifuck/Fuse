// Composer logic: draft saves (composer/drafts.js), @ / path / slash
// completion (composer-complete.js), spell tokenizing (composer-spell.js) and
// the prompt library's fields and composition (composer/library-core.js).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { S } = require('../../static/chat/state.js');
const { createDrafts } = require('../../static/chat/composer/drafts.js');

const noop = () => {};
const tick = () => new Promise((resolve) => setImmediate(resolve));
// Draft saves are debounced on window timers; tests fire them by hand.
const timers = [];
global.window = { setTimeout: (fn) => timers.push(fn), clearTimeout: noop, addEventListener: noop };
const fire = () => timers.splice(0).forEach((fn) => fn());

function script(names, extra = {}) {
  const world = { console, ...extra };
  world.window = world;
  world.globalThis = world;
  for (const name of names) vm.runInNewContext(readFileSync(resolve(__dirname, '../../static', name), 'utf8'), world);
  return world;
}
const complete = script(['composer-complete.js']).FuseComposerComplete;
const spell = script(['composer-lexicon.js', 'composer-spell.js'], {
  setTimeout, clearTimeout, Uint8Array, TextDecoder, localStorage: { getItem: () => null, setItem: noop },
}).FuseComposerSpell;
const library = () => import('../../static/chat/composer/library-core.js');

test('a failed draft save is retried only while it is still the newest text', async () => {
  const sent = [];
  const replies = [];
  const net = { api: (_url, opts) => { sent.push(JSON.parse(opts.body).text); return new Promise((resolve, reject) => replies.push({ resolve, reject })); } };
  S.drafts = new Map();
  createDrafts({ net, input: {}, sidebar: {}, sync: {} });
  S.drafts.save('t1', 'old');
  fire();
  S.drafts.save('t1', 'newer');
  fire();
  replies[1].resolve({});
  replies[0].reject(new Error('offline'));
  await tick();
  fire();
  assert.deepEqual(sent, ['old', 'newer'], 'an older failure is not retried over newer text');
  S.drafts.save('t1', 'latest');
  fire();
  replies[2].reject(new Error('offline'));
  await tick();
  fire();
  assert.deepEqual(sent, ['old', 'newer', 'latest', 'latest']);
});

test('a sync snapshot that predates an in-flight draft save does not overwrite it', async () => {
  const replies = [];
  const net = { api: () => new Promise((resolve, reject) => replies.push({ resolve, reject })) };
  class Drafts extends Map { remote(key, text) { return text ? super.set(key, text) : super.delete(key); } }
  Object.assign(S, { drafts: new Drafts(), currentId: 't1', input: { value: 'typed' }, stash: new Map() });
  const drafts = createDrafts({ net, input: { syncComposer: noop, resizeInput: noop, syncEmptyComposer: noop }, sidebar: { renderHistory: noop }, sync: {} });
  S.drafts.set('t1', 'typed');
  S.drafts.save('t1', 'typed');
  fire();
  drafts.acceptDrafts({ t1: 'older' });
  drafts.applyRemoteDraft('t1', 'older');
  assert.equal(S.drafts.get('t1'), 'typed');
  assert.equal(S.input.value, 'typed');
  replies[0].resolve({});
  await tick();
  drafts.acceptDrafts({ t1: 'from another device' });
  assert.equal(S.drafts.get('t1'), 'from another device');
  assert.equal(S.input.value, 'from another device');
});

test('completion triggers: path only for known paths, @ for mentions, / for slash', () => {
  assert.equal(complete.trigger('and/', 4, { traces: [], listing: null, attachments: [] }), null, 'no path without a known path');
  assert.equal(complete.trigger('./src', 5, { traces: [], listing: null }).kind, 'path');
  assert.equal(complete.trigger('/hel', 4, {}).kind, 'slash');
  const ctx = { listing: { path: '/proj', entries: [{ name: 'src', kind: 'dir' }] } };
  const t = complete.trigger('src/', 4, ctx);
  assert.equal(t.kind, 'path');
  assert.ok(complete.items(t, ctx).some((r) => r.insert.endsWith('/src')));
  assert.equal(complete.joinPath('/root/fuse/', 'src'), '/root/fuse/src');
});

test('@ completes skills by bare slug, even with an empty query', () => {
  const ctx = { projects: [{ project_name: 'fuse', remote_path: '/root/fuse', host_alias: 'local' }],
    skills: [{ slug: 'ponytail', description: 'style' }], traces: [], attachments: [], threads: [] };
  const t = complete.trigger('@', 1, ctx);
  assert.equal(t.kind, 'at');
  assert.ok(complete.items(t, ctx).some((r) => r.kind === 'skill' && r.insert === 'ponytail'));
  const rows = complete.items(complete.trigger('please run @pony', 16, ctx), ctx);
  assert.equal(rows[0].insert, 'ponytail', 'a slug, not /slug');
});

test('prompt history skips slash commands and duplicates', () => {
  const rows = Array.from(complete.history({ prompts: ['/undo last', 'Build the dock', '/help', 'Build the dock'] }), String);
  assert.deepEqual(rows, ['Build the dock']);
});

test('spell tokens: technical terms are skipped, prose words are checked', () => {
  const words = ["doesn't", 'js', 'ui', 'is', "we're", 'defaced'];
  const skipped = ['OAuth', 'iOS', 'PostgreSQL', 'useEffect', 'API', 'HTTP', 'LLM', 'PR', 'C++', '.gitignore', 'k8s', 'i18n',
    'a11y', 'NaN', 'app/web/chat.py', '`fooobar`', 'PR #12', 'JIRA-123', 'v1.0.2', '#c5df88', 'https://fuse.example.com/chat',
    'glm-5.3-flash', 'gpt-4o', 'claude-3-5', 'gpt-4o-mini', "O'Brien", 'a1b2c3d', '你好', '!!!'];
  const kind = (input) => (spell.tokenize(input).find((t) => t.text === input) || spell.tokenize(input)[0]).kind;
  for (const input of words) assert.equal(kind(input), 'word', input);
  for (const input of skipped) assert.equal(kind(input), 'skip', input);
});

test('spell check: hyphens split, slash commands and fences skip, the current word waits', () => {
  const english = new Set(["doesn't", 'well', 'known', 'state', 'of', 'the', 'art', 'keep', 'last', 'hour', 'is', 'fox']);
  // Arrays from the script's realm are copied into this one for deepEqual.
  const issues = (text, caret) => Array.from(spell.check(text, { ready: true, english, caret }), (i) => String(i.word));
  const wordsOf = (text) => Array.from(spell.tokenize(text).filter((t) => t.kind === 'word'), (t) => String(t.text));
  assert.deepEqual(wordsOf('state-of-the-art'), ['state', 'of', 'the', 'art']);
  assert.deepEqual(issues('well-knonw '), ['knonw']);
  const tokens = spell.tokenize('/compact keep the last hour');
  assert.deepEqual([String(tokens[0].text), tokens[0].kind], ['/compact', 'skip']);
  assert.deepEqual(issues('/compact keep the last hour'), []);
  assert.ok(spell.tokenize('```js\nconsle.log\n```').every((t) => t.kind === 'skip'));
  assert.deepEqual(issues('```js\nconsle.log\n```'), []);
  assert.deepEqual(issues('js '), []);
  assert.deepEqual(issues('teh', 3), [], 'the word under the caret is not flagged yet');
  assert.deepEqual(issues('teh fox', 7), ['teh']);
});

test('spell predicates', () => {
  assert.equal(spell.isSlugWithDigit('gpt-4o'), true);
  assert.equal(spell.isSlugWithDigit('well-known'), false);
  assert.equal(spell.isInteriorCapital('OAuth'), true);
  assert.equal(spell.isAcronym('API'), true);
  assert.equal(spell.isAcronym('NaN'), false);
  assert.equal(spell.isCamel('useEffect'), true);
  assert.equal(spell.isAlnumAbbrev('k8s'), true);
});

test('prompt fields: required blanks stay literal, defaults fill, edits shift later fields', async () => {
  const { fillFields, shiftFields, hasBlank, firstBlank } = await library();
  const { text, fields } = fillFields('A: {{Name}}\nB: {{Where|here}}.');
  assert.equal(text, 'A: {{Name}}\nB: here.');
  assert.deepEqual(fields.map((f) => [text.slice(f.start, f.end), f.required]), [['{{Name}}', true], ['here', false]]);
  const typed = text.replace('{{Name}}', 'Ann');
  const next = shiftFields(fields, text, typed);
  assert.equal(typed.slice(next[1].start, next[1].end), 'here');
  assert.equal(next[0].required, false, 'a typed blank is filled');
  const before = 'one AAAA two BBBB end';
  const two = [{ start: 4, end: 8, required: false }, { start: 13, end: 17, required: false }];
  assert.deepEqual(shiftFields(two, before, 'one AABBB end'), [], 'an edit across a boundary drops the fields it cuts');
  const after = 'one AAAABBBB end';
  assert.deepEqual(shiftFields(two, before, after).map((f) => after.slice(f.start, f.end)), ['AAAA', 'BBBB']);
  assert.equal(hasBlank('x {{Name}} y'), true);
  assert.equal(firstBlank('x {{Name}} y'), 'Name');
  assert.equal(hasBlank('x here y'), false);
});

test('composePrompt appends the mode rules and only modifiers valid for it; the shipped catalog composes cleanly', async () => {
  const { composePrompt, fillFields, hasBlank } = await library();
  const catalog = {
    footers: { audit: { text: 'RULES' }, act: { text: 'ACT' } },
    modifiers: [{ id: 'sub', modes: ['audit'], text: 'SUB' }, { id: 'terse', modes: ['audit', 'act'], text: 'TERSE' }],
  };
  assert.equal(composePrompt(catalog, { body: 'BODY' }, 'audit', ['sub', 'terse']), 'BODY\n\nRULES\n\nSUB\n\nTERSE');
  assert.equal(composePrompt(catalog, { body: 'BODY' }, 'act', ['sub', 'terse']), 'BODY\n\nACT\n\nTERSE');
  const { CATALOG } = await import('../../static/chat/composer/prompt-catalog.js');
  for (const cat of CATALOG.categories) {
    for (const p of cat.prompts) {
      for (const mode of p.modes) {
        const { text } = fillFields(composePrompt(CATALOG, p, mode, CATALOG.modifiers.map((m) => m.id)));
        assert.equal(/\{\{[^{}]*\|/.test(text), false, `${p.id} left a default unresolved`);
        assert.equal(hasBlank(text), p.needs_input, `${p.id} blank flag`);
      }
    }
  }
});

test('machine stats: load stays white to 50%, turns amber, red from 95%; rates read short', async () => {
  const { loadTone, shortRate } = await import('../../static/chat/hoststats.js');
  assert.equal(loadTone(0), '');
  assert.equal(loadTone(50), '');
  assert.equal(loadTone(75), 'color-mix(in oklch, #ff453a 0%, #f5b83d)');
  assert.match(loadTone(60), /#f5b83d 40%, #fff/);
  assert.equal(loadTone(95), '#ff453a');
  assert.equal(loadTone(100), '#ff453a');
  assert.equal(loadTone(NaN), '');
  assert.deepEqual([0, 840, 999, 84_000, 1_234_567, 12_500_000, 3.4e9, -5].map(shortRate),
    ['0B', '840B', '1.0K', '84K', '1.2M', '13M', '3.4G', '0B']);
});

test('model picker: a long name keeps its first two words and a version right after them', async () => {
  const { shortModelName } = await import('../../static/chat/picker/list.js');
  assert.equal(shortModelName('Opus 5.5'), 'Opus 5.5');
  assert.equal(shortModelName('Claude Haiku'), 'Claude Haiku');
  assert.equal(shortModelName('DeepSeek V4.1 Flash 0528'), 'DeepSeek V4.1');
  assert.equal(shortModelName('Claude Opus 4.5 Thinking'), 'Claude Opus 4.5');
  assert.equal(shortModelName('Ornith 1.5 35B A3B Abliterated.i1'), 'Ornith 1.5');
  assert.equal(shortModelName('  GPT-5.1   Codex Max '), 'GPT-5.1 Codex');
  assert.equal(shortModelName('Kimi-K2.5-Instruct-Turbo-0905'), 'Kimi-K2.5');
  assert.equal(shortModelName('Qwen3-Coder-480B-A35B-Instruct'), 'Qwen3-Coder-480B');
  assert.equal(shortModelName('Supercalifragilistic'), 'Supercalifragilistic');
  assert.equal(shortModelName(''), '');
});

test('machine stats: a new sample glides in eased out and lands exactly', async () => {
  const { glide } = await import('../../static/chat/hoststats.js');
  assert.equal(glide(10, 90, 0), 10);
  assert.equal(glide(10, 90, 0.5), 80);
  assert.equal(glide(10, 90, 1), 90);
  assert.equal(glide(10, 90, 3), 90);
  assert.equal(glide(90, 10, -1), 90);
});

test('machine stats: recent samples draw right-aligned in their box, clamped to the top', async () => {
  const { sparkPoints } = await import('../../static/chat/hoststats.js');
  assert.equal(sparkPoints([0, 100], 100, 30, 10), '28.7,9.0 30.0,1.0');
  assert.equal(sparkPoints([150], 100, 30, 10), '30.0,1.0');
  assert.equal(sparkPoints([], 100, 30, 10), '');
});

test('machine stats: the details card lists memory, storage, disk and network, skipping what the sample lacks', async () => {
  const { detailRows } = await import('../../static/chat/hoststats.js');
  const gb = 2 ** 30;
  assert.deepEqual(detailRows({ cpu: 11.4, mem: [2.6 * gb, 16 * gb], net: [1.2e6, 12e3], disk: [0.4, 0, 84e3], vol: [41 * gb, 120 * gb] }), [
    ['CPU', '', '11%'],
    ['Memory', '2.6 GB of 16 GB', '16%'],
    ['Storage', '41 GB of 120 GB', '34%'],
    ['Disk', 'read 0B/s, write 84K/s', '0%'],
    ['Network', '↓ 1.2M/s  ↑ 12K/s', ''],
  ]);
  assert.deepEqual(detailRows({ cpu: 3, mem: [1, 0], net: [0, 0] }).map((row) => row[0]), ['CPU', 'Memory', 'Network']);
});

test('machine stats: recent load reads as block characters, clamped to the scale', async () => {
  const { loadBlocks } = await import('../../static/chat/hoststats.js');
  assert.equal(loadBlocks([0, 50, 100, 140, -3]), '▁▅██▁');
  assert.equal(loadBlocks([]), '');
});

test('code blocks: a message splits into prose and code and joins back; carets map both ways', async () => {
  const { splitBlocks, joinBlocks, locate, offsetOf, fenceTrigger } = await import('../../static/chat/composer/blocks-core.js');
  const msg = 'Look:\n```bash\nls -la\npwd\n```\nthen\n\nmore';
  const parts = splitBlocks(msg);
  assert.deepEqual(parts.map((p) => [p.kind, p.text, p.lang]), [['text', 'Look:', undefined], ['code', 'ls -la\npwd', 'bash'], ['text', 'then\n\nmore', undefined]]);
  assert.equal(joinBlocks(parts), msg);
  for (const p of parts) assert.equal(msg.slice(p.start, p.end), p.text);
  assert.deepEqual(locate(parts, msg.indexOf('pwd')), { index: 1, offset: 7 });
  assert.deepEqual(locate(parts, msg.indexOf('bash')), { index: 1, offset: 0 }, 'the fence line snaps into the code');
  assert.equal(offsetOf(parts, 2, 4), msg.indexOf('then') + 4);
  assert.deepEqual(splitBlocks('no fences here').length, 1);
  // Empty prose around a block adds no lines; an empty block keeps one.
  const bare = [{ kind: 'text', text: '' }, { kind: 'code', text: '', lang: '' }, { kind: 'text', text: '' }];
  assert.equal(joinBlocks(bare), '```\n\n```');
  assert.deepEqual(splitBlocks('```\n\n```').map((p) => [p.kind, p.text]), [['text', ''], ['code', ''], ['text', '']]);
  assert.deepEqual(joinBlocks(bare, { index: 1, offset: 0 }), { value: '```\n\n```', caret: 4 });
  assert.deepEqual(joinBlocks([{ kind: 'text', text: 'a' }, { kind: 'code', text: 'x', lang: 'py' }, { kind: 'text', text: 'b' }], { index: 2, offset: 1 }),
    { value: 'a\n```py\nx\n```\nb', caret: 15 });
  assert.equal(splitBlocks('```js\nunclosed').at(-2).text, 'unclosed');
  assert.deepEqual(fenceTrigger('hi\n```', 6), { insert: '\n\n```', caret: 7 });
  assert.equal(fenceTrigger('a ```', 5), null, 'only a fence alone on its line');
  assert.equal(fenceTrigger('```x', 3), null, 'only with the caret at the end of the line');
});

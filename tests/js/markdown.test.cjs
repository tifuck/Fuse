// Chat markdown parsing (static/chat/markdown.js): text to block and inline
// tokens, no markup. Themes draw the tokens.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const md = () => import('../../static/chat/markdown.js');
const T = '```';
const kinds = (tokens) => tokens.map((t) => t.type);
// Inline nodes as a compact string: text as is, others as <t:...>.
const flat = (nodes) => nodes.map((n) => (n.t === 'text' ? n.v : n.t === 'code' ? `<code:${n.v}>` : n.t === 'br' ? '<br>'
  : n.t === 'link' ? `<link:${n.href}|${flat(n.c)}>` : n.t === 'autolink' ? `<auto:${n.href}>` : n.t === 'image' ? `<img:${n.href}|${n.alt}>`
    : `<${n.t}:${flat(n.c)}>`)).join('');

test('backticks inside a line never open a code block', async () => {
  const { parseBlocks, parseInline } = await md();
  const blocks = parseBlocks(`The box turns ${T} into code.\n\n- typing ${T} alone opens it\n- next`);
  assert.deepEqual(kinds(blocks), ['paragraph', 'list'], 'the rest of the message is not swallowed into code');
  assert.equal(flat(parseInline(blocks[0].text)), `The box turns ${T} into code.`);
  assert.equal(flat(parseInline(`Run ${T}npm test${T} now.`)), 'Run <code:npm test> now.', 'a one-line span loses nothing');
  assert.deepEqual(kinds(parseBlocks(`${T}npm test${T} at line start`)), ['paragraph']);
  // A sentence that starts with backticks is not a fence either.
  assert.deepEqual(kinds(parseBlocks(`${T} alone on a line opens a code box.\n\nMore text.`)), ['paragraph', 'paragraph']);
});

test('fences: languages, tildes, longer fences, quotes, list items, unclosed streams', async () => {
  const { parseBlocks } = await md();
  const [code] = parseBlocks(`${T}py title="a.py"\nx = 1\n${T}`);
  assert.deepEqual([code.type, code.lang, code.text, code.closed], ['code', 'py', 'x = 1', true]);
  assert.equal(parseBlocks('~~~\ntilde\n~~~')[0].text, 'tilde');
  assert.equal(parseBlocks(`\`\`\`\`md\n${T}js\nx\n${T}\n\`\`\`\``)[0].text, `${T}js\nx\n${T}`, 'a longer fence holds a shorter one');
  const [quote, after] = parseBlocks(`> ${T}\n> quoted\n> ${T}\n\nafter`);
  assert.deepEqual([quote.type, kinds(quote.children), quote.children[0].text, after.type], ['quote', ['code'], 'quoted', 'paragraph']);
  const [list] = parseBlocks(`1. Install:\n   ${T}bash\n   npm i\n   ${T}\n2. Run`);
  assert.deepEqual(list.items.map((item) => kinds(item.children)), [['paragraph', 'code'], ['paragraph']]);
  assert.equal(list.items[0].children[1].text, 'npm i');
  const open = parseBlocks(`Text\n${T}js\nconst a = 1;`).at(-1);
  assert.deepEqual([open.type, open.text, open.closed], ['code', 'const a = 1;', false], 'a streamed block shows before its close');
  const slip = parseBlocks(`Run this:${T}bash\nls\n${T}`);
  assert.deepEqual(kinds(slip), ['paragraph', 'code'], '"text```lang" with code below is a model slip, not prose');
});

test('inline: code spans, escapes, emphasis, links, autolinks, breaks', async () => {
  const { parseInline } = await md();
  const f = (s) => flat(parseInline(s));
  assert.equal(f(`Write \`\` ${T} \`\` to fence, \`\`a \` b\`\` too`), `Write <code:${T}> to fence, <code:a \` b> too`);
  assert.equal(f('`unclosed and *em*'), '`unclosed and <em:em>');
  assert.equal(f('\\*not em\\* and \\`not code\\`'), '*not em* and `not code`');
  assert.equal(f('my_var_name and _em_ and **b _i_ b**'), 'my_var_name and <em:em> and <strong:b <em:i> b>');
  assert.equal(f('***both*** ~~gone~~ ~kept~'), '<em:<strong:both>> <del:gone> ~kept~');
  assert.equal(f('[docs](https://w.org/Foo_(bar)) end'), '<link:https://w.org/Foo_(bar)|docs> end');
  assert.equal(f('see https://x.io/a_b. or (https://x.io/c)'), 'see <auto:https://x.io/a_b>. or (<auto:https://x.io/c>)');
  assert.equal(f('![alt](https://x.io/a.png)'), '<img:https://x.io/a.png|alt>');
  assert.equal(f('one  \ntwo\\\nthree\nfour'), 'one<br>two<br>three<br>four');
  assert.equal(f('[*a*](https://x.io)'), '<link:https://x.io|<em:a>>');
});

test('lists keep their numbers, nest by indentation and carry task boxes', async () => {
  const { parseBlocks } = await md();
  const [ol] = parseBlocks('3. three\n4. four');
  assert.deepEqual([ol.ordered, ol.start, ol.items.length], [true, 3, 2]);
  const [nested] = parseBlocks('1. One\n  - sub a\n  - sub b\n2. Two');
  assert.deepEqual(kinds(nested.items[0].children), ['paragraph', 'list'], 'two spaces nest under a numbered item');
  const [tasks] = parseBlocks('- [x] done\n- [ ] open\n- plain');
  assert.deepEqual(tasks.items.map((item) => item.task), [true, false, null]);
  assert.equal(tasks.items[0].children[0].text, 'done');
  assert.deepEqual(kinds(parseBlocks('Text\n- list right after text')), ['paragraph', 'list']);
  assert.deepEqual(kinds(parseBlocks('Text\n2. not a list')), ['paragraph']);
  const [loose] = parseBlocks('- one\n\n  more of one\n\n- two');
  assert.deepEqual(loose.items.map((item) => kinds(item.children)), [['paragraph', 'paragraph'], ['paragraph']]);
});

test('tables, headings, rules and alerts', async () => {
  const { parseBlocks } = await md();
  const [table] = parseBlocks('| a | b | c |\n|:--|:-:|--:|\n| `x | y` | a \\| b | `p \\| q` |');
  assert.deepEqual([table.align, table.rows[0]], [['left', 'center', 'right'], ['`x | y`', 'a | b', '`p | q`']]);
  assert.deepEqual(kinds(parseBlocks('| a |\n| b |')), ['paragraph'], 'no separator row, no table');
  assert.deepEqual(parseBlocks('Title\n=====\n\nSub\n---').map((t) => [t.type, t.level, t.text]), [['heading', 1, 'Title'], ['heading', 2, 'Sub']]);
  assert.deepEqual(kinds(parseBlocks('***\n\n- - -')), ['hr', 'hr']);
  assert.deepEqual(kinds(parseBlocks('#NoSpace')), ['paragraph']);
  const [alert] = parseBlocks('> [!WARNING]\n> Careful:\n> - one');
  assert.deepEqual([alert.alert, kinds(alert.children)], ['warning', ['paragraph', 'list']]);
});

test('a stream can freeze every block before the boundary: split parses match the whole', async () => {
  const { parseBlocks, stableBoundary } = await md();
  const strip = (tokens) => JSON.stringify(tokens, (key, value) => (key === 'offset' ? undefined : value));
  const text = [`Intro with ${T} inline.`, '1. Install:\n\n   ```bash\n   npm i\n   ```\n\n2. Run', '> [!NOTE]\n> quoted `code`',
    '| a | b |\n|---|---|\n| 1 | 2 |', `${T}js\nconst x = 1;\n\nconst y = 2;\n${T}`, 'Title\n---', '- [x] done\n- [ ] open', 'Last paragraph.'].join('\n\n');
  for (let n = 1; n <= text.length; n += 1) {
    const prefix = text.slice(0, n);
    const at = stableBoundary(prefix);
    if (!at) continue;
    assert.equal(strip([...parseBlocks(prefix.slice(0, at)), ...parseBlocks(prefix.slice(at))]), strip(parseBlocks(prefix)), `prefix of ${n}`);
  }
});

test('the stable boundary is an offset into the text as given, CRLF included', async () => {
  const { parseBlocks, stableBoundary } = await md();
  const text = `Intro\r\n\r\n${T}js\r\nx = 1\r\n${T}\r\n\r\nSome prose`;
  const tail = text.slice(stableBoundary(text));
  assert.equal(tail, 'Some prose');
  assert.deepEqual(kinds(parseBlocks(tail)), ['paragraph'], 'prose after a fence is not drawn as code');
});

test('composer marks keep every character and style what the sent message renders', async () => {
  const { marksHtml } = await import('../../static/chat/composer/marks.js');
  const text = (html) => html.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const spans = (html) => [...html.matchAll(/<span class="cm-(\w+)">([^<]*)/g)].map((m) => `${m[1]}:${m[2]}`);
  const { html, styled } = marksHtml('`test` test `two` and **bold** _em_ ~~gone~~ [docs](https://x.io)');
  assert.equal(styled, true);
  assert.deepEqual(spans(html).filter((s) => !s.startsWith('mark:')),
    ['code:test', 'code:two', 'strong:bold', 'em:em', 'del:gone', 'link:docs']);
  assert.equal(marksHtml('a ` b, \\*not\\*, snake_case_name').styled, false, 'nothing styled where the message has no markup');
  assert.equal(spans(marksHtml('**a\n\nb**').html).length, 0, 'markup never crosses a blank line');
  // The mirror must hold exactly the field's text, whatever it is.
  const pieces = ['`', '``', '*', '**', '_', '~~', '[', ']', '(', ')', '\\', '\n', '\n\n', ' ', 'ab', 'https://x.io/a_b', '<', '&'];
  let seed = 1;
  const pick = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return pieces[seed % pieces.length]; };
  for (let n = 0; n < 3000; n += 1) {
    const input = Array.from({ length: 1 + (n % 24) }, pick).join('');
    const issues = input.length > 3 ? [{ start: 1, end: 3 }] : [];
    assert.equal(text(marksHtml(input, issues).html), input, JSON.stringify(input));
  }
});

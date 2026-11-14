const { test } = require('node:test');
const assert = require('node:assert/strict');

const load = () => import('../static/chat/composer/library-core.js');

test('fillFields keeps required blanks literal and materializes defaults', async () => {
  const { fillFields } = await load();
  const { text, fields } = fillFields('A: {{Name}}\nB: {{Where|here}}.');
  assert.equal(text, 'A: {{Name}}\nB: here.');
  assert.deepEqual(fields.map((f) => [text.slice(f.start, f.end), f.required]), [['{{Name}}', true], ['here', false]]);
});

test('shiftFields grows the field being typed in and moves later ones', async () => {
  const { fillFields, shiftFields } = await load();
  const { text, fields } = fillFields('A: {{Name}}\nB: {{Where|here}}.');
  const typed = text.replace('{{Name}}', 'Ann');
  const next = shiftFields(fields, text, typed);
  assert.equal(next.length, 2);
  assert.equal(typed.slice(next[1].start, next[1].end), 'here');
  assert.equal(next[0].required, false);
});

test('an edit across a field boundary drops that field only', async () => {
  const { shiftFields } = await load();
  const before = 'one AAAA two BBBB end';
  const fields = [{ start: 4, end: 8, required: false }, { start: 13, end: 17, required: false }];
  // Deleting "AA two B" cuts across both fields: both are gone.
  assert.deepEqual(shiftFields(fields, before, 'one AABBB end'), []);
  // Deleting " two " between them keeps both and moves the second.
  const after = 'one AAAABBBB end';
  const next = shiftFields(fields, before, after);
  assert.deepEqual(next.map((f) => after.slice(f.start, f.end)), ['AAAA', 'BBBB']);
});

test('composePrompt appends the mode rules and only modifiers valid for it', async () => {
  const { composePrompt } = await load();
  const catalog = {
    footers: { audit: { text: 'RULES' }, act: { text: 'ACT' } },
    modifiers: [{ id: 'sub', modes: ['audit'], text: 'SUB' }, { id: 'terse', modes: ['audit', 'act'], text: 'TERSE' }],
  };
  assert.equal(composePrompt(catalog, { body: 'BODY' }, 'audit', ['sub', 'terse']), 'BODY\n\nRULES\n\nSUB\n\nTERSE');
  assert.equal(composePrompt(catalog, { body: 'BODY' }, 'act', ['sub', 'terse']), 'BODY\n\nACT\n\nTERSE');
});

test('hasBlank and firstBlank see only unfilled required blanks', async () => {
  const { hasBlank, firstBlank } = await load();
  assert.equal(hasBlank('x {{Name}} y'), true);
  assert.equal(firstBlank('x {{Name}} y'), 'Name');
  assert.equal(hasBlank('x here y'), false);
});

test('the shipped catalog composes and fills every prompt without leftovers', async () => {
  const { composePrompt, fillFields, hasBlank } = await load();
  const { CATALOG } = await import('../static/chat/composer/prompt-catalog.js');
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

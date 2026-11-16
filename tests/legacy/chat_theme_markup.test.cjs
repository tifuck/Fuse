// Every theme draws all of /chat (templates/themes/<t>/ partials plus the
// markup its JS returns), and the runtime finds elements only through data-*
// hooks. So every theme must carry the same runtime hooks and return the same
// JS surface; theme-local hooks are named data-theme-*.
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, readdirSync, existsSync } = require('node:fs');
const { resolve } = require('node:path');

const ROOT = resolve(__dirname, '../..');
const THEMES = readdirSync(resolve(ROOT, 'static/themes'), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
const HOOK = /\bdata-(?!theme-)[a-z][a-z0-9-]*/g;

function template(rel, seen = new Set()) {
  if (seen.has(rel)) return '';
  seen.add(rel);
  const text = readFileSync(resolve(ROOT, 'templates', rel), 'utf8').replace(/\{#[\s\S]*?#\}/g, '');
  return text.replace(/\{%-?\s*include\s+"([^"]+)"[^%]*%\}/g, (_, inner) => template(inner, seen));
}

function themeJs(theme) {
  const dir = resolve(ROOT, 'static/themes', theme);
  return readdirSync(dir).filter((n) => n.endsWith('.js')).map((n) => readFileSync(resolve(dir, n), 'utf8')).join('\n');
}

function runtimeHooks() {
  const files = [resolve(ROOT, 'static/chat.js')];
  const walk = (dir) => readdirSync(dir, { withFileTypes: true }).forEach((d) => {
    const p = resolve(dir, d.name);
    if (d.isDirectory()) walk(p); else if (d.name.endsWith('.js')) files.push(p);
  });
  walk(resolve(ROOT, 'static/chat'));
  const hooks = new Set();
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    for (const m of text.matchAll(HOOK)) hooks.add(m[0]);
    // dataset.fooBar reads data-foo-bar
    for (const m of text.matchAll(/dataset\.([a-zA-Z]+)/g)) hooks.add('data-' + m[1].replace(/[A-Z]/g, (c) => '-' + c.toLowerCase()));
  }
  return hooks;
}

function surface(theme) {
  const index = readFileSync(resolve(ROOT, 'static/themes', theme, 'index.js'), 'utf8');
  const block = index.slice(index.lastIndexOf('  return {'));
  return [...block.matchAll(/^\s{4}(\w+):/gm)].map((m) => m[1]).sort();
}

test('every theme ships the whole chat shell', () => {
  for (const theme of THEMES) {
    for (const part of ['_chat.html', '_sidebar.html', '_header.html', '_work.html', '_composer.html', '_sheets.html', '_dial.html']) {
      assert.ok(existsSync(resolve(ROOT, 'templates/themes', theme, part)), `${theme}/${part}`);
    }
  }
  assert.match(readFileSync(resolve(ROOT, 'templates/chat.html'), 'utf8'), /\{% include "themes\/" ~ chat_view ~ "\/_chat\.html" %\}/);
});

test('every theme carries the same runtime hooks', () => {
  const runtime = runtimeHooks();
  const hooks = (theme) => {
    const markup = template(`themes/${theme}/_chat.html`) + template(`themes/${theme}/_dial.html`) + themeJs(theme);
    return new Set([...markup.matchAll(HOOK)].map((m) => m[0]).filter((h) => runtime.has(h)));
  };
  const [first, ...rest] = THEMES;
  const want = hooks(first);
  assert.ok(want.has('data-chat-input') && want.has('data-history-item') && want.has('data-job-row'), 'sanity: hooks are found');
  for (const theme of rest) {
    const got = hooks(theme);
    assert.deepEqual([...want].filter((h) => !got.has(h)), [], `${theme} is missing hooks ${first} has`);
    assert.deepEqual([...got].filter((h) => !want.has(h)), [], `${theme} has hooks ${first} lacks`);
  }
});

test('every theme returns the same JS surface', () => {
  const [first, ...rest] = THEMES;
  const want = surface(first);
  assert.ok(want.includes('historyListHtml') && want.includes('jobDetailHtml'));
  for (const theme of rest) assert.deepEqual(surface(theme), want, theme);
});

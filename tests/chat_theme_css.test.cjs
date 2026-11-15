// /chat is drawn only by the active theme's stylesheets. The shared sheets keep
// their chat rules for the pages that still use them (settings, /projects,
// artifacts), gated off the chat page by :where(html:not(.is-chat-root)); each
// theme owns its copy under themes/<t>/styles/base/, gated to that theme.
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, readdirSync } = require('node:fs');
const { resolve } = require('node:path');

const STATIC = resolve(__dirname, '../static');
const read = (rel) => readFileSync(resolve(STATIC, rel), 'utf8');

function selectors(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  const walk = (src) => {
    let depth = 0, start = 0, prelude = '';
    for (let i = 0; i < src.length; i += 1) {
      const c = src[i];
      if (c === '"' || c === "'") { i = src.indexOf(c, i + 1); continue; }
      if (c === ';' && depth === 0) start = i + 1;
      else if (c === '{') {
        if (depth === 0) { prelude = src.slice(start, i).trim(); start = i + 1; }
        depth += 1;
      } else if (c === '}') {
        depth -= 1;
        if (depth === 0) {
          const body = src.slice(start, i);
          if (/^@(media|supports|container|layer)\b/.test(prelude)) walk(body);
          else if (!prelude.startsWith('@')) out.push(prelude);
          start = i + 1;
        }
      }
    }
  };
  walk(text);
  return out;
}

const parts = (sel) => {
  const out = []; let depth = 0, buf = '';
  for (const c of sel) {
    if (c === '(' || c === '[') depth += 1;
    if (c === ')' || c === ']') depth -= 1;
    if (c === ',' && depth === 0) { out.push(buf.trim()); buf = ''; } else buf += c;
  }
  out.push(buf.trim());
  return out;
};

const SHARED_GATE = /^(?::where\(html:not\(\.is-chat-root\)\)\s|(?:html|:root):where\(:not\(\.is-chat-root\)\))/;
const sharedExempt = (file, sel) => (file === 'chat-design.css' && /^html\[data-chat-(theme|scheme)(="[\w-]+")?\]$/.test(sel))
  || (file === 'chat-design.css' && sel.includes('.chat-scene'))
  || sel.includes('is-settings');

const appSheets = [...read('app.css').matchAll(/@import url\("([^"]+)"\);/g)].map((m) => m[1]);

test('shared chat stylesheets never style the chat page', () => {
  const chatSheets = ['chat-design.css', 'projects-ui.css', ...appSheets.filter((p) => /\/chat-[^/]+\.css$/.test(p))];
  for (const rel of chatSheets) {
    const file = rel.split('/').pop();
    for (const sel of selectors(read(rel))) {
      for (const one of parts(sel)) {
        if (sharedExempt(file, one)) continue;
        assert.match(one, SHARED_GATE, `${rel}: ungated "${one}" would style /chat for every theme`);
      }
    }
  }
});

test('no other shared stylesheet reaches chat markup', () => {
  for (const rel of appSheets.filter((p) => !/\/chat-[^/]+\.css$/.test(p))) {
    for (const sel of selectors(read(rel))) {
      for (const one of parts(sel)) {
        if (!/\.chat|chat-/.test(one) || sharedExempt('', one) || one.includes('data-chat-console="true"] body.is-settings')) continue;
        assert.match(one, SHARED_GATE, `${rel}: "${one}" styles chat markup outside a theme`);
      }
    }
  }
});

test('each theme copy is gated to its own chat page', () => {
  const themes = readdirSync(resolve(STATIC, 'themes'), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
  assert.ok(themes.includes('console') && themes.includes('combined') && themes.includes('desktop'));
  for (const theme of themes) {
    const dir = resolve(STATIC, `themes/${theme}/styles/base`);
    const files = readdirSync(dir).filter((n) => n.endsWith('.css'));
    assert.ok(files.includes('chat-history.css') && files.includes('chat-design.css'), theme);
    const gate = new RegExp(`^(?::where\\(html\\.is-chat-root\\[data-chat-view="${theme}"\\]\\)\\s|(?:html|:root):where\\(\\.is-chat-root\\[data-chat-view="${theme}"\\]\\))`);
    for (const name of files) {
      for (const sel of selectors(readFileSync(resolve(dir, name), 'utf8'))) {
        if (/^(from|to|\d+%)/.test(sel)) continue;
        for (const one of parts(sel)) assert.match(one, gate, `${theme}/styles/base/${name}: "${one}"`);
      }
    }
    const imports = [...read(`themes/${theme}/styles/base.css`).matchAll(/@import url\("base\/([^"]+)"\);/g)].map((m) => m[1]);
    assert.ok(imports.includes('chat-history.css') && imports.at(-1) === 'projects-ui.css', `${theme} base.css keeps app.css order, then projects-ui.css`);
    assert.match(read(`themes/${theme}/theme.css`), /^[^@]*(?:\/\*[\s\S]*?\*\/\s*)*@import url\("styles\/base\.css"\);/, `${theme}: base copies load before the theme's own rules`);
  }
});

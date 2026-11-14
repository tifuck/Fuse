const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { readdirSync } = require('node:fs');
const themeStyles = readdirSync(resolve(__dirname, '../static/themes/console/styles')).filter(name => name.endsWith('.css')).map(name => readFileSync(resolve(__dirname, '../static/themes/console/styles', name), 'utf8')).join('\n');

const source = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');
// What base.html renders as #fuse-chat-themes: every theme.json, in Layout order.
const themeMeta = readdirSync(resolve(__dirname, '../static/themes'), { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => JSON.parse(readFileSync(resolve(__dirname, '../static/themes', d.name, 'theme.json'), 'utf8')))
  .sort((a, b) => a.order - b.order);

function boot(saved = {}, blocked = false, { cookie = '', view, backdrop } = {}) {
  const storage = new Map(Object.entries(saved));
  const meta = {};
  const html = { dataset: view ? { chatView: view } : {}, style: { setProperty() {}, removeProperty() {} }, removeAttribute(name) { delete this.dataset[name.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())]; } };
  const root = { dataset: { view: 'console' } };
  let ready;
  const world = {
    document: {
      cookie,
      documentElement: html,
      getElementById: (id) => (id === 'fuse-chat-themes' ? { textContent: JSON.stringify(themeMeta) } : null),
      querySelector(selector) {
        if (selector === '[data-chat]') return root;
        if (selector === 'meta[name="color-scheme"]' || selector === 'meta[name="theme-color"]') {
          return { setAttribute: (name, value) => { meta[selector] = value; } };
        }
        return null;
      },
      querySelectorAll: (selector) => {
        return [];
      },
      addEventListener: (name, fn) => { ready = fn; },
    },
    localStorage: {
      getItem(key) { if (blocked) throw new Error('Unavailable'); return storage.get(key); },
      setItem(key, value) { if (blocked) throw new Error('Unavailable'); storage.set(key, value); },
    },
    MutationObserver: class { constructor(fn) {} observe() {} },
  };
  if (backdrop) world.FuseBackdrop = backdrop;
  world.window = world;
  vm.runInNewContext(source, world);
  return {
    html, root, meta, storage, ready, appearance: world.FuseChatAppearance,
    views: () => JSON.parse(storage.get('fuse.chat.views.v1')),
    change(theme) { world.FuseChatAppearance.setTheme(theme); },
  };
}

test('restores palette and default layout before DOM ready despite legacy preferences', () => {
  for (const legacy of ['cli', 'compact', 'console', 'mainframe', 'prism', 'unknown', '']) {
    const ui = boot({ 'fuse.chat.theme.v1': 'paper', 'fuse.chat.view.v2': legacy });
    assert.equal(ui.html.dataset.chatTheme, 'paper');
    assert.equal(ui.html.dataset.chatConsole, 'true');
    assert.equal(ui.html.dataset.chatView, 'console');
    assert.equal(ui.meta['meta[name="color-scheme"]'], 'light');
    assert.equal(ui.meta['meta[name="theme-color"]'], '#ffffff');
    ui.ready();
    assert.equal(ui.html.dataset.chatView, 'console');
    assert.equal(ui.appearance.view, undefined);
  }
});

test('composer prefs default on and persist in this browser', () => {
  const ui = boot();
  ui.ready();
  const prefs = ui.appearance.getComposerPrefs();
  assert.equal(prefs.spell, true);
  assert.equal(prefs.palettes, true);
  assert.equal(prefs.history, true);
  ui.appearance.setComposerPrefs({ spell: false });
  assert.equal(JSON.parse(ui.storage.get('fuse.chat.composer.v1')).spell, false);
  assert.equal(ui.appearance.getComposerPrefs().palettes, true);
});

test('theme choice persists with its meta colors', () => {
  const ui = boot();
  ui.ready();
  ui.change('midnight');
  // Palettes are saved per theme now (fuse.chat.views.v1), not in the global key.
  assert.equal(ui.views().console.palette, 'midnight');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#0a1018');
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#0a1018');
});

test('void and paper palettes apply to the default layout', () => {
  const ui = boot({ 'fuse.chat.theme.v1': 'void', 'fuse.chat.view.v2': 'cli' });
  assert.equal(ui.html.dataset.chatTheme, 'void');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'dark');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#000000');
  ui.ready();
  ui.change('paper');
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'light');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#ffffff');
});

test('invalid or unavailable storage uses safe defaults', () => {
  const invalid = boot({ 'fuse.chat.theme.v1': 'unknown' });
  assert.equal(invalid.html.dataset.chatTheme, 'fuse');
  const ui = boot({}, true);
  ui.ready();
  assert.equal(ui.html.dataset.chatTheme, 'fuse');
  assert.doesNotThrow(() => ui.change('paper'));
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  ui.change('invalid');
  assert.equal(ui.html.dataset.chatTheme, 'fuse');
});

test('backdrop kind persists independently of theme', () => {
  const ui = boot({ 'fuse.chat.backdrop.v1': 'signal', 'fuse.chat.backdrop.strength.v1': '40' });
  assert.equal(ui.html.dataset.chatBackdrop, 'signal');
  ui.ready();
  ui.appearance.setBackdrop('orbit');
  assert.equal(ui.html.dataset.chatBackdrop, 'orbit');
  assert.equal(ui.views().console.backdrop, 'orbit');
  assert.equal(ui.views().console.strength, 40);
  ui.change('void');
  assert.equal(ui.html.dataset.chatBackdrop, 'orbit');
});

test('only the saved console font is restored; legacy view fonts cannot change it', () => {
  const ui = boot({ 'fuse.chat.view.v2': 'prism', 'fuse.chat.fonts.v1': JSON.stringify({ cli: 'geist', compact: 'fraunces', prism: 'serif', mainframe: 'newsreader', console: 'jetbrains' }) });
  assert.equal(ui.html.dataset.chatFont, 'jetbrains');
  ui.appearance.setFont('fraunces');
  assert.equal(ui.html.dataset.chatFont, 'fraunces');
  assert.deepEqual(JSON.parse(ui.storage.get('fuse.chat.fonts.v1')), { console: 'fraunces' });
  ui.appearance.setFont(null);
  assert.equal(ui.html.dataset.chatFont, 'redhat');
  assert.equal(boot({ 'fuse.chat.fonts.v1': JSON.stringify({ cli: 'geist', prism: 'serif' }) }).html.dataset.chatFont, 'redhat');
});

test('the server-stamped view draws; the cookie is the Layout choice', () => {
  const ui = boot({}, false, { cookie: 'x=1; fuse_chat_view=combined', view: 'combined' });
  assert.equal(ui.html.dataset.chatView, 'combined');
  assert.equal(ui.html.dataset.chatConsole, undefined);
  assert.equal(ui.html.dataset.chatFont, 'bricolage');
  assert.equal(ui.appearance.layout, 'combined');
  // Settings pages always draw console, whatever the chat layout is.
  const settings = boot({}, false, { cookie: 'fuse_chat_view=combined' });
  assert.equal(settings.html.dataset.chatView, 'console');
  assert.equal(settings.html.dataset.chatConsole, 'true');
  assert.equal(settings.appearance.layout, 'combined');
  for (const cookie of ['', 'fuse_chat_view=prism']) assert.equal(boot({}, false, { cookie }).appearance.layout, 'console');
});

test('each view keeps its own font', () => {
  const saved = { 'fuse.chat.fonts.v1': JSON.stringify({ console: 'jetbrains', combined: 'geist', cli: 'serif' }) };
  assert.equal(boot(saved).html.dataset.chatFont, 'jetbrains');
  const ui = boot(saved, false, { view: 'combined' });
  assert.equal(ui.html.dataset.chatFont, 'geist');
  ui.appearance.setFont('fraunces');
  assert.deepEqual(JSON.parse(ui.storage.get('fuse.chat.fonts.v1')), { console: 'jetbrains', combined: 'fraunces' });
});

test('console and combined are independent theme folders', () => {
  const read = (dir) => readdirSync(dir, { recursive: true }).filter(name => /\.(css|js|html)$/.test(name)).map(name => [name, readFileSync(resolve(dir, name), 'utf8')]);
  // A theme is its static folder plus its template folder.
  const consoleFiles = [...read(resolve(__dirname, '../static/themes/console')), ...read(resolve(__dirname, '../templates/themes/console'))];
  const combinedFiles = [...read(resolve(__dirname, '../static/themes/combined')), ...read(resolve(__dirname, '../templates/themes/combined'))];
  for (const [name, text] of consoleFiles) assert.doesNotMatch(text, /combined/, `console/${name}`);
  for (const [name, text] of combinedFiles) {
    assert.doesNotMatch(text, /data-chat-console|data-view="console"|\.\.\/console\/|themes\/console\//, `combined/${name}`);
  }
  assert.ok(combinedFiles.some(([name]) => name === 'index.js'));
  assert.ok(consoleFiles.some(([name]) => name === '_chat.html') && combinedFiles.some(([name]) => name === '_chat.html'));
});

test('retired backdrops migrate to their replacement', () => {
  for (const [old, next] of [['tide', 'ripple'], ['pulsar', 'signal'], ['topo', 'ripple'], ['ink', 'aurora'], ['wind', 'aurora'], ['fern', 'meadow'], ['sumi', 'koi']]) {
    const ui = boot({ 'fuse.chat.backdrop.v1': old });
    assert.equal(ui.html.dataset.chatBackdrop, next, old);
    assert.equal(ui.storage.get('fuse.chat.backdrop.v1'), next, old);
  }
});

test('only chat draws the backdrop; other pages keep a solid theme ground', () => {
  const css = readFileSync(resolve(__dirname, '../static/chat-design.css'), 'utf8');
  const base = readFileSync(resolve(__dirname, '../templates/base.html'), 'utf8');
  const js = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');
  assert.doesNotMatch(css, /data-chat-backdrop="none"\]\) body\.is-settings/);
  assert.doesNotMatch(base, /chat-backdrop\.js/);
  assert.match(base, /\{% if is_chat %\}\s*<div class="chat-scene"/);
  assert.match(js, /classList\.contains\("is-chat"\)\) return null/);
});

test('backdrop wallpaper is a scene layer, not an opaque plate', () => {
  // The scene layer is shared Appearance; each theme makes its own surfaces see-through.
  const css = readFileSync(resolve(__dirname, '../static/chat-design.css'), 'utf8');
  const js = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');
  assert.match(css, /\.chat-scene\[data-kind\] \[data-chat-scene-wash\]/);
  assert.doesNotMatch(css, /chat-scene-bubbles/);
  for (const theme of ['console', 'combined']) {
    const own = readFileSync(resolve(__dirname, `../static/themes/${theme}/styles/base/chat-design.css`), 'utf8');
    const html = `html:where\\(\\.is-chat-root\\[data-chat-view="${theme}"\\]\\)\\[data-chat-backdrop\\]:not\\(\\[data-chat-backdrop="none"\\]\\) body\\.is-chat`;
    assert.match(own, new RegExp(html + ' :is\\(\\.stage, \\.stage-inner, main, \\.chat, \\.chat-stage, \\.chat-thread\\)[^{]*\\{\\s*background:\\s*transparent'), theme);
    assert.match(own, new RegExp(html + ' \\.chat \\.chat-dock \\{\\s*background:\\s*transparent'), theme);
    assert.doesNotMatch(own, /chat-stage, \.chat-thread\) \{\s*background:\s*color-mix\(in srgb, var\(--ink\) 82%/);
  }
  assert.match(js, /canvas data-chat-scene-fx/);
  assert.match(js, /signal: \{ label: "Signal"/);
  for (const kind of ['orbit', 'galaxy', 'eclipse', 'belt', 'contact', 'koi']) assert.match(js, new RegExp(`\\n    ${kind}: \\{ label`));
  for (const gone of ['pulsar', 'fern', 'ink', 'sumi']) assert.doesNotMatch(js, new RegExp(`\\n    ${gone}: \\{ label`));
  const registry = readFileSync(resolve(__dirname, '../static/backdrops/scenes.js'), 'utf8');
  const sceneKeys = registry.match(/export const SCENES = \{([^}]+)\}/)[1].split(',').map(key => key.trim());
  for (const kind of ['glyphs', 'life', 'signal', 'orbit', 'pond', 'koi', 'galaxy', 'eclipse', 'belt', 'contact']) assert.ok(sceneKeys.includes(kind), kind);
  for (const gone of ['pulsar', 'fern', 'ink', 'sumi']) assert.ok(!sceneKeys.includes(gone), gone);
});

test('tool rows collapse to one line; thinking unwraps in place', () => {
  const css = [
    readFileSync(resolve(__dirname, '../static/app.css'), 'utf8').replace(/@import url\("([^"]+)"\);/g, (_, path) => readFileSync(resolve(__dirname, '../static', path), 'utf8')),
    readFileSync(resolve(__dirname, '../static/chat-design.css'), 'utf8'),
    themeStyles,
  ].join('\n');
  assert.match(css, /\.chat-step-label:not\(\.chat-think-preview\)[\s\S]*?text-overflow:\s*ellipsis/);
  assert.match(css, /\.chat-step-label:not\(\.chat-think-preview\)[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /\.chat-step\.is-workspace \.chat-step-label \{[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /\.chat-query-label[\s\S]*?text-overflow:\s*ellipsis/);
  assert.match(css, /\.chat-think-preview[\s\S]*?text-overflow:\s*ellipsis[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /:not\(\.is-live-think\) \.chat-step-toggle\[aria-expanded="true"\] \.chat-think-preview[\s\S]*?white-space:\s*normal/);
  assert.match(css, /\.is-live-think \.chat-think-preview[\s\S]*?white-space:\s*pre-wrap/);
  assert.match(css, /\.chat-step:not\(\.is-think\) \.chat-step-label/);
  assert.match(css, /\.chat-step:not\(\.is-think\) :is\(\.chat-step-toggle, \.chat-step-static\)/);
  assert.doesNotMatch(css, /\.chat-step\.is-think \.chat-step-toggle\[aria-expanded="true"\] \.chat-think-preview \{ display: none/);
  assert.match(css, /html\[data-chat-theme="void"\]/);
  assert.match(css, /html\[data-chat-theme="paper"\] \{[\s\S]*?--ink:\s*#ffffff/);
  assert.doesNotMatch(css, /Terminal is an appearance invariant/);
  const js = require('./chat_source.cjs').source;
  assert.match(js, /function syncThinkingMode\(/);
  assert.match(js, /windowEl\.inert = mode === "collapsed"/);
  assert.match(js, /mode === "collapsed" \? -1 : 0/);
});

test('each theme keeps its own palette, backdrop and strength, starting from the old global values', () => {
  const legacy = { 'fuse.chat.theme.v1': 'midnight', 'fuse.chat.backdrop.v1': 'garden', 'fuse.chat.backdrop.strength.v1': '40' };
  const ui = boot(legacy, false, { view: 'combined' });
  // Migration: every theme starts from the global values, once.
  for (const meta of themeMeta) assert.deepEqual(ui.views()[meta.id], { palette: 'midnight', backdrop: 'garden', strength: 40, restore: '' }, meta.id);
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  ui.ready();
  ui.change('ember');
  ui.appearance.setBackdrop('rain');
  ui.appearance.setStrength(20);
  const saved = Object.fromEntries(ui.storage);
  // Console is untouched by Combined's edits, and the global keys no longer decide.
  const consoleUi = boot(saved);
  assert.equal(consoleUi.html.dataset.chatTheme, 'midnight');
  assert.equal(consoleUi.html.dataset.chatBackdrop, 'garden');
  const combinedUi = boot(saved, false, { view: 'combined' });
  assert.equal(combinedUi.html.dataset.chatTheme, 'ember');
  assert.equal(combinedUi.html.dataset.chatBackdrop, 'rain');
  assert.equal(combinedUi.views().combined.strength, 20);
  // A later edit of a global key does not move a migrated theme.
  const again = boot({ ...saved, 'fuse.chat.theme.v1': 'paper' });
  assert.equal(again.html.dataset.chatTheme, 'midnight');
});

test('with no saved look each theme takes its metadata defaults', () => {
  for (const meta of themeMeta) {
    const ui = boot({}, false, { view: meta.id });
    assert.equal(ui.html.dataset.chatScheme, meta.scheme, meta.id);
    assert.equal(ui.html.dataset.chatBackdrop, meta.wallpaper, meta.id);
    assert.equal(ui.html.dataset.chatFont, meta.font, meta.id);
    assert.equal(ui.appearance.themeId, meta.id);
  }
  // Console and Combined keep today's look exactly.
  for (const view of ['console', 'combined']) {
    const ui = boot({}, false, { view });
    assert.equal(ui.html.dataset.chatTheme, 'fuse');
    assert.equal(ui.html.dataset.chatBackdrop, 'none');
  }
  // A theme added after migration starts from its own defaults, not the old globals.
  const views = { console: { palette: 'ember', backdrop: 'rain', strength: 30 } };
  const fresh = boot({ 'fuse.chat.views.v1': JSON.stringify(views), 'fuse.chat.theme.v1': 'midnight' }, false, { view: 'combined' });
  assert.equal(fresh.html.dataset.chatTheme, 'fuse');
  assert.equal(fresh.views().console.palette, 'ember');
  // Garbage in the store falls back per field.
  const junk = boot({ 'fuse.chat.views.v1': JSON.stringify({ console: { palette: 'nope', backdrop: '<b>', strength: 999, restore: 1 } }) });
  assert.equal(junk.html.dataset.chatTheme, 'fuse');
  assert.equal(junk.html.dataset.chatBackdrop, 'none');
  junk.appearance.setStrength(30);
  assert.deepEqual(junk.views().console, { palette: 'fuse', backdrop: 'none', strength: 30, restore: '' });
});

test('a light wallpaper switches a dark palette to light; None brings it back', () => {
  const schemes = { clouddrift: 'light', honulagoon: 'dark' };
  const backdrop = { sync() {}, previews() {}, isWallpaper: (k) => k in schemes, scheme: (k) => schemes[k] || '' };
  const ui = boot({ 'fuse.chat.theme.v1': 'midnight' }, false, { backdrop });
  ui.ready();
  ui.appearance.setBackdrop('clouddrift');
  assert.equal(ui.html.dataset.chatScheme, 'light');
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  assert.equal(ui.views().console.restore, 'midnight');
  ui.appearance.setBackdrop('none');
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  assert.equal(ui.views().console.restore, '');
  // Dark wallpapers leave the palette alone; leaving light for one restores it.
  ui.appearance.setBackdrop('honulagoon');
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  ui.appearance.setBackdrop('clouddrift');
  ui.appearance.setBackdrop('honulagoon');
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  // A palette the user picks over a light wallpaper is kept.
  ui.appearance.setBackdrop('clouddrift');
  ui.change('frost');
  ui.appearance.setBackdrop('none');
  assert.equal(ui.html.dataset.chatTheme, 'frost');
  assert.deepEqual(JSON.parse(JSON.stringify(ui.appearance.wallpapers.map((w) => [w.id, w.scheme]))), [['clouddrift', 'light'], ['honulagoon', 'dark']]);
});

test('the sun toggle flips the scheme per theme and comes back to the dark palette', () => {
  const ui = boot({ 'fuse.chat.theme.v1': 'ember' }, false, { view: 'combined' });
  ui.ready();
  ui.appearance.setScheme('light');
  assert.equal(ui.html.dataset.chatScheme, 'light');
  assert.equal(ui.appearance.scheme, 'light');
  ui.appearance.setScheme('dark');
  assert.equal(ui.html.dataset.chatTheme, 'ember');
  assert.equal(boot(Object.fromEntries(ui.storage)).html.dataset.chatTheme, 'ember');
});

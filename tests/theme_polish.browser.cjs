// The theme polish contract (docs/themes/*.md), under whichever theme
// FUSE_CHAT_VIEW picks:
//   1. the inspector (Desktop) or rail (Combined) never opens by itself, and
//      the user's own choice survives a reload;
//   2. the composer's spell mirror lies exactly under the textarea and paints
//      no text, and a few lines of input need no inner scrollbar;
//   3. transcript text over every /theme wallpaper, dark and light, reaches
//      WCAG AA against the pixels actually behind it;
//   4. the top bar's controls never overlap, leave the viewport or squeeze
//      the title away, from 1920 to 360 wide.
// 1 and 3 are the new themes' promises; Console keeps its own and skips them.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const VIEW = process.env.FUSE_CHAT_VIEW || 'console';
const PANEL = { desktop: { toggle: '[data-theme-inspector]', attr: 'aria-pressed' }, combined: { toggle: '[data-theme-pill]', attr: 'aria-expanded' } }[VIEW];
const LONG_TITLE = 'Migrate the API client to fetch with retries, backoff and a typed error surface for every endpoint';
const PROSE = [
  '## Plan', '', 'The client now retries with **backoff**; see [the guide](https://example.com/guide) and `retry.ts`.', '',
  '- First, wrap `fetch` in one place.', '- Then type every error.', '',
  '| Endpoint | Retries |', '|---|---|', '| /users | 3 |', '| /orders | 5 |', '',
  '> Keep the old client until the new one ships.',
].join('\n');

async function openPage(browser, width, height = 900, storage) {
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce', hasTouch: width <= 480, storageState: storage });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => Boolean(window.fuseToolFixture));
  // Phones open on the chat list; leave it the way a reader would.
  if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
  return { page, context, errors };
}

const busyThread = () => fuseToolFixture.mountThread([
  { role: 'user', content: 'Go.' },
  { role: 'assistant', model: 'qa/model', elapsedMs: 30000, content: 'Working.', tools: [
    { id: 'td', name: 'todo_write', status: 'done', todos: [{ content: 'Write the client', status: 'in_progress' }, { content: 'Ship it', status: 'pending' }] },
    { id: 'ag', name: 'spawn_subagent', status: 'done', child_id: 'child-1', description: 'Audit the retries' },
    { id: 'ed', name: 'search_replace', status: 'done', path: '/srv/qa/app.py', preview: '-a = 1\n+a = 2\n', output: 'updated' },
  ], segments: [{ type: 'content', text: 'Working.' }, { type: 'tool', id: 'td' }, { type: 'tool', id: 'ag' }, { type: 'tool', id: 'ed' }] },
]);

async function panelNeverOpensItself(browser) {
  if (!PANEL) return console.log(`skip panel policy (${VIEW} has no inspector or rail)`);
  let { page, context, errors } = await openPage(browser, 1440);
  const state = async () => (await page.locator(PANEL.toggle).getAttribute(PANEL.attr)) === 'true';
  await page.evaluate(busyThread);
  await page.evaluate(() => fuseToolFixture.todo.agents([{ id: 'child-1', status: 'running', description: 'Audit the retries', updated_at: new Date().toISOString() }]));
  await page.evaluate(() => fuseToolFixture.repaint());
  await page.waitForTimeout(800);
  assert.equal(await state(), false, 'agents, todos and an edit do not open the panel');
  await page.locator(PANEL.toggle).click();
  assert.equal(await state(), true, 'the toggle opens it');
  const kept = await context.storageState();
  await context.close();
  ({ page, context, errors } = await openPage(browser, 1440, 900, kept));
  assert.equal(await state(), true, "a reload keeps the user's open panel");
  await page.locator(PANEL.toggle).click();
  assert.equal(await state(), false, 'the toggle closes it');
  await page.evaluate(busyThread);
  await page.evaluate(() => fuseToolFixture.todo.agents([{ id: 'child-2', status: 'running', description: 'Another', updated_at: new Date().toISOString() }]));
  await page.waitForTimeout(800);
  assert.equal(await state(), false, 'new work after a close does not reopen it');
  assert.deepEqual(errors, []);
  await context.close();
  console.log(`PASS the ${VIEW} panel opens only when asked`);
}

async function composerMirrorAligned(browser) {
  for (const width of [1440, 390]) {
    const { page, context, errors } = await openPage(browser, width);
    const input = page.locator('[data-chat-input]');
    await input.click();
    await input.fill('');
    for (const [i, line] of ['Short line.', 'A much longer line that has to wrap inside the composer at every width this test runs, so the mirror must wrap at exactly the same words as the textarea does.', 'third', 'fourth'].entries()) {
      if (i) await page.keyboard.press('Shift+Enter');
      await page.keyboard.type(line);
    }
    await page.waitForTimeout(300);
    const m = await page.evaluate((newTheme) => {
      const ta = document.querySelector('[data-chat-input]');
      const mirror = document.querySelector('[data-chat-compose-mirror]');
      const a = ta.getBoundingClientRect(); const b = mirror.getBoundingClientRect();
      const cs = (el) => getComputedStyle(el);
      // The runtime copies these onto the mirror (composer-spell.js METRIC_PROPS); the new themes also pin text-rendering.
      const props = ['font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing', 'padding-left', 'padding-top', 'padding-right', 'box-sizing', 'white-space', 'word-spacing', ...(newTheme ? ['text-rendering'] : [])];
      return {
        dx: Math.abs(a.left - b.left), dy: Math.abs(a.top - b.top), dw: Math.abs(a.width - b.width),
        diff: props.filter((p) => cs(ta).getPropertyValue(p) !== cs(mirror).getPropertyValue(p) && !(p === 'white-space' && /pre-wrap|break-spaces/.test(cs(mirror).whiteSpace))),
        color: cs(mirror).color, fill: cs(mirror).webkitTextFillColor,
        scroll: ta.scrollHeight - ta.clientHeight, overflowY: cs(ta).overflowY,
      };
    }, Boolean(PANEL));
    assert.ok(m.dx <= 1 && m.dy <= 1 && m.dw <= 1, `${width}px: the mirror sits on the textarea (dx ${m.dx}, dy ${m.dy}, dw ${m.dw})`);
    assert.deepEqual(m.diff, [], `${width}px: the mirror's text metrics match the textarea`);
    assert.match(m.color, /rgba\(\d+, \d+, \d+, 0\)|transparent/, `${width}px: the mirror paints no text (${m.color})`);
    assert.ok(m.scroll <= 1, `${width}px: four lines need no inner scrollbar (${m.scroll}px hidden, overflow-y ${m.overflowY})`);
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log(`PASS the ${VIEW} composer mirror lines up with its textarea`);
}

// The worst (10th percentile) contrast of each text element against the
// pixels under it, measured with every glyph made transparent.
const CONTRAST = async ({ png, rects }) => {
  const img = new Image();
  img.src = `data:image/png;base64,${png}`;
  await img.decode();
  const canvas = Object.assign(document.createElement('canvas'), { width: img.width, height: img.height });
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  return rects.map((r) => {
    const x = Math.max(0, Math.round(r.x)); const y = Math.max(0, Math.round(r.y));
    const w = Math.min(canvas.width - x, Math.round(r.w)); const h = Math.min(canvas.height - y, Math.round(r.h));
    if (w < 2 || h < 2) return null;
    const data = ctx.getImageData(x, y, w, h).data;
    const all = [];
    for (let i = 0; i < data.length; i += 16) all.push(ratio(r.fg, [data[i], data[i + 1], data[i + 2]]));
    all.sort((p, q) => p - q);
    return { ...r, ratio: all[Math.floor(all.length * 0.1)] };
  }).filter(Boolean);
};

async function legibleOverWallpapers(browser) {
  if (!PANEL) return console.log(`skip wallpaper contrast (${VIEW} keeps its own look)`);
  const { page, context, errors } = await openPage(browser, 1440);
  const wallpapers = await page.evaluate(() => FuseChatAppearance.wallpapers.map((w) => w.id));
  assert.ok(wallpapers.length >= 8, `the /theme wallpapers are listed (${wallpapers.length})`);
  const bad = [];
  for (const wallpaper of wallpapers) {
    for (const scheme of ['dark', 'light']) {
      await page.evaluate(([w, s]) => { FuseChatAppearance.setBackdrop(w); FuseChatAppearance.setScheme(s); }, [wallpaper, scheme]);
      await page.evaluate((text) => fuseToolFixture.mountThread([
        { role: 'user', content: 'Explain the plan.' },
        { role: 'assistant', model: 'qa/model', elapsedMs: 4000, content: text, segments: [{ type: 'content', text }] },
      ]), PROSE);
      await page.waitForFunction(() => [...document.querySelectorAll('canvas')].some((c) => c.width > 0 && c.getBoundingClientRect().width > 300));
      await page.waitForTimeout(400);
      const rects = await page.evaluate(() => {
        // rgb(…) is 0-255; color(srgb …) is 0-1.
        const rgb = (c) => (c.match(/[\d.]+/g) || []).slice(0, 3).map((v) => (c.startsWith('color(') ? Number(v) * 255 : Number(v)));
        const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        const out = [];
        for (const el of document.querySelectorAll('[data-chat-log] :is(p, li, a, code, th, td, blockquote, h2, [data-meta], [data-meta] *, time)')) {
          const b = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          if (!b.width || !b.height || b.bottom < 0 || b.top > innerHeight || s.visibility === 'hidden' || !ownText(el)) continue;
          const px = parseFloat(s.fontSize);
          const large = px >= 24 || (px >= 18.66 && Number(s.fontWeight) >= 700);
          out.push({ x: b.x, y: b.y, w: b.width, h: b.height, fg: rgb(s.color), need: large ? 3 : 4.5, what: `${el.tagName.toLowerCase()} "${el.textContent.trim().slice(0, 24)}"` });
        }
        return out;
      });
      assert.ok(rects.length >= 8, `${wallpaper}/${scheme}: transcript text is on screen (${rects.length})`);
      await page.addStyleTag({ content: '* { color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; caret-color: transparent !important; } svg { visibility: hidden !important; }' });
      const png = (await page.screenshot()).toString('base64');
      await page.evaluate(() => document.head.lastElementChild.remove());
      for (const r of await page.evaluate(CONTRAST, { png, rects })) {
        if (r.ratio < r.need) bad.push(`${wallpaper}/${scheme}: ${r.what} ${r.ratio.toFixed(2)}:1 < ${r.need}:1`);
      }
    }
  }
  assert.deepEqual(bad, [], `text fails AA over a wallpaper:\n  ${bad.join('\n  ')}`);
  assert.deepEqual(errors, []);
  await context.close();
  console.log(`PASS ${VIEW} transcript text reaches AA over ${wallpapers.length} wallpapers, dark and light`);
}

async function topBarNeverOverlaps(browser) {
  const problems = [];
  for (const width of [1920, 1440, 1280, 1180, 1040, 1024, 900, 800, 768, 600, 480, 390, 360]) {
    const { page, context, errors } = await openPage(browser, width, width <= 480 ? 844 : 900);
    await page.evaluate(busyThread);
    await page.evaluate((title) => { const t = document.querySelector('[data-chat-bar] [data-chat-title]'); if (t) { t.textContent = title; t.classList.remove('is-untitled'); } }, LONG_TITLE);
    const layouts = [['as loaded', async () => {}]];
    if (width > 800) layouts.push(['sidebar toggled', async () => { await page.locator('[data-chat-bar] [data-history-toggle], [data-history] [data-history-toggle]').filter({ visible: true }).first().click(); }]);
    if (PANEL && width >= 1040) layouts.push(['panel open', async () => { if ((await page.locator(PANEL.toggle).getAttribute(PANEL.attr)) !== 'true') await page.locator(PANEL.toggle).click(); }]);
    for (const [label, apply] of layouts) {
      await apply();
      await page.waitForTimeout(300);
      const found = await page.evaluate(() => {
        const bar = document.querySelector('[data-chat-bar]');
        const shown = (el) => { const b = el.getBoundingClientRect(); const s = getComputedStyle(el); return b.width > 1 && b.height > 1 && s.visibility !== 'hidden' && !el.closest('[hidden], [inert], [role=menu]') && Number(s.opacity) > 0.05; };
        const items = [...bar.querySelectorAll('button, a[href], [role=button], input, select')].filter(shown)
          .filter((el, _, all) => !all.some((o) => o !== el && o.contains(el)));
        const out = [];
        const rect = (el) => el.getBoundingClientRect();
        for (const el of items) {
          const b = rect(el);
          if (b.left < -1 || b.right > innerWidth + 1) out.push(`${el.getAttribute('aria-label') || el.textContent.trim().slice(0, 20)} leaves the viewport (${Math.round(b.left)}..${Math.round(b.right)})`);
        }
        for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
          const a = rect(items[i]); const b = rect(items[j]);
          const x = Math.min(a.right, b.right) - Math.max(a.left, b.left); const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (x > 1 && y > 1) out.push(`${items[i].getAttribute('aria-label') || items[i].textContent.trim().slice(0, 20)} overlaps ${items[j].getAttribute('aria-label') || items[j].textContent.trim().slice(0, 20)} by ${Math.round(x)}x${Math.round(y)}`);
        }
        const title = bar.querySelector('[data-chat-title]');
        if (title && shown(title)) {
          const t = rect(title);
          if (t.width < 48) out.push(`the title is squeezed to ${Math.round(t.width)}px`);
          for (const el of items) {
            if (el.contains(title)) continue;
            const b = rect(el);
            const x = Math.min(t.right, b.right) - Math.max(t.left, b.left); const y = Math.min(t.bottom, b.bottom) - Math.max(t.top, b.top);
            if (x > 1 && y > 1) out.push(`the title runs under ${el.getAttribute('aria-label') || el.textContent.trim().slice(0, 20)}`);
          }
        }
        return out;
      });
      problems.push(...found.map((p) => `${width}px ${label}: ${p}`));
    }
    assert.deepEqual(errors, [], `${width}px page errors`);
    await context.close();
  }
  assert.deepEqual(problems, [], `top bar problems:\n  ${problems.join('\n  ')}`);
  console.log(`PASS the ${VIEW} top bar never overlaps from 1920 to 360`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    await panelNeverOpensItself(browser);
    await composerMirrorAligned(browser);
    await legibleOverWallpapers(browser);
    await topBarNeverOverlaps(browser);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });

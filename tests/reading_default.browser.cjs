const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const legacy of [null, 'cli', 'compact', 'mainframe', 'prism', 'console', 'invalid']) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.addInitScript(value => {
        try {
          if (value !== null) localStorage.setItem('fuse.chat.view.v2', value);
          localStorage.setItem('fuse.chat.fonts.v1', JSON.stringify({ console: 'redhat', cli: 'fraunces' }));
          localStorage.setItem('fuse.chat.theme.v1', 'paper');
        } catch (_) { /* sandboxed frames have no storage */ }
      }, legacy);
      await page.goto(origin);
      // No layout preference: the fixture's login may set one (FUSE_CHAT_VIEW); drop it.
      await page.context().clearCookies({ name: 'fuse_chat_view' });
      await page.goto(origin + '/chat');
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
      assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), 'console');
      assert.equal(await page.locator('html').getAttribute('data-chat-font'), 'redhat');
      assert.equal(await page.locator('html').getAttribute('data-chat-theme'), 'paper');
      await page.getByRole('button', { name: 'Appearance', exact: true }).click();
      const menu = page.getByRole('dialog', { name: 'Appearance', exact: true });
      assert.equal(await menu.locator('[data-view]').count(), 0);
      assert.doesNotMatch(await menu.innerText(), /Reading mode|Terminal|Compact|Mainframe/);
      assert.equal(await menu.locator('[data-chat-view-choice="console"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('link[rel=stylesheet][href*="themes/combined/"]').count(), 0);
      const thinking = menu.locator('[data-thinking-toggle]');
      assert.equal(await thinking.count(), 1);
      await thinking.uncheck();
      assert.equal(await thinking.isChecked(), false);
      await thinking.check();
      assert.equal(await thinking.isChecked(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Layout › Combined: a cookie and a reload; the page then draws only with the combined theme.
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.context().clearCookies({ name: 'fuse_chat_view' });
    await page.goto(origin + '/chat');
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    const pick = async (view) => {
      await page.getByRole('button', { name: 'Appearance', exact: true }).click();
      await Promise.all([
        page.waitForNavigation(),
        page.getByRole('dialog', { name: 'Appearance', exact: true }).locator(`[data-chat-view-choice="${view}"]`).click(),
      ]);
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    };
    await pick('combined');
    assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), 'combined');
    assert.equal(await page.locator('html').getAttribute('data-chat-view'), 'combined');
    assert.equal(await page.locator('html').getAttribute('data-chat-console'), null);
    assert.equal(await page.locator('link[rel=stylesheet][href*="themes/console/"]').count(), 0);
    assert.equal(await page.locator('link[rel=stylesheet][href*="themes/combined/"]').count(), 2);
    assert.equal(await page.locator('html').getAttribute('data-chat-font'), 'bricolage');
    // The whole page flips, not just the transcript: Combined's own shell (its status pill).
    assert.equal(await page.locator('[data-chat-bar] [data-theme-pill]').count(), 1);
    await pick('console');
    assert.equal(await page.locator('[data-theme-pill]').count(), 0);
    assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), 'console');
    assert.equal(await page.locator('html').getAttribute('data-chat-console'), 'true');
    assert.equal(await page.locator('link[rel=stylesheet][href*="themes/combined/"]').count(), 0);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

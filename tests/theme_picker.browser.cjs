// The /theme wallpaper picker and per-theme looks, under whichever theme
// FUSE_CHAT_VIEW picks (the runtime owns the keys and data; each theme draws
// the strip). Then: each theme keeps its own background and palette across a
// Layout switch, with no page errors, and addWallpaper registers a spec that
// the picker lists.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const VIEW = process.env.FUSE_CHAT_VIEW || 'console';
const PRESETS = ['spark', 'clouddrift', 'honulagoon', 'koipond', 'jellyfish', 'paperboats', 'fireflies', 'moonlit'];
const EXAMPLE = JSON.parse(readFileSync(resolve(__dirname, '../docs/wallpapers.md'), 'utf8').split('## Example')[1].match(/```json\n([\s\S]+?)\n```/)[1]);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture) && Boolean(window.FuseBackdrop));
    assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), VIEW);
    const html = (name) => page.locator('html').getAttribute(name);
    const picker = page.locator('[data-wallpaper-picker]');
    const active = () => page.locator('[data-wallpaper-picker] [data-wallpaper-card][aria-selected="true"]').getAttribute('data-wallpaper-card');
    const current = () => page.locator('[data-wallpaper-picker] [data-wallpaper-card][aria-current="true"]').getAttribute('data-wallpaper-card');

    // /theme is a slash command in every theme.
    const input = page.locator('[data-chat-input]');
    await input.fill('/the');
    await page.locator('[data-chat-slash-list] #chat-slash-theme').waitFor();
    await input.fill('/theme');
    await input.press('Enter');
    await picker.waitFor({ state: 'visible' });
    assert.equal(await input.inputValue(), '', 'running /theme clears the box');
    const ids = await page.locator('[data-wallpaper-picker] [data-wallpaper-card]').evaluateAll((els) => els.map((el) => el.dataset.wallpaperCard));
    assert.deepEqual(ids, ['none', ...PRESETS], 'None, then every preset');
    assert.equal(await current(), 'none', 'the current one carries the check');
    assert.equal(await active(), 'none', 'the cursor starts on the current one');
    assert.match(await picker.innerText(), /Wallpapers[\s\S]*Honu Lagoon[\s\S]*Cloud Drift|Wallpapers[\s\S]*Cloud Drift[\s\S]*Honu Lagoon/);
    // Real preview frames, through FuseBackdrop's preview mechanism.
    await page.waitForFunction(() => document.querySelectorAll('[data-wallpaper-picker] [data-wallpaper-card] canvas').length === 8);

    // Arrows move, wrapping; Enter applies and closes.
    await page.keyboard.press('ArrowLeft');
    assert.equal(await active(), 'moonlit', 'Left from the first wraps to the last');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    assert.equal(await active(), 'honulagoon');
    assert.equal(await html('data-chat-backdrop'), 'none', 'moving does not apply');
    await page.keyboard.press('Enter');
    await picker.waitFor({ state: 'hidden' });
    assert.equal(await html('data-chat-backdrop'), 'honulagoon');
    assert.equal(await input.inputValue(), '', 'Enter in the picker sends nothing');
    assert.equal(await page.locator('[data-chat-scene]').getAttribute('data-wallpaper'), '', 'wallpapers get the vivid scene layer');
    const fx = await page.evaluate(() => {
      const el = document.querySelector('[data-chat-scene-fx]');
      const style = getComputedStyle(el);
      return { opacity: Number(style.opacity), mask: style.maskImage || style.webkitMaskImage, hidden: el.hidden };
    });
    assert.equal(fx.hidden, false);
    assert.ok(fx.opacity >= 0.55, `the wallpaper shows strong: ${fx.opacity}`);
    assert.equal(fx.mask, 'none', 'no centre mask');

    // Reopen: the check moved. A light wallpaper turns the palette light; None brings it back.
    await input.fill('/theme');
    await input.press('Enter');
    await picker.waitFor({ state: 'visible' });
    assert.equal(await current(), 'honulagoon');
    const palette = await html('data-chat-theme');
    await page.locator('[data-wallpaper-picker] [data-wallpaper-card="clouddrift"]').click();
    assert.equal(await html('data-chat-backdrop'), 'clouddrift', 'a click applies');
    assert.equal(await html('data-chat-scheme'), 'light', 'a light wallpaper switches to a light palette');
    assert.equal(await current(), 'clouddrift');
    await page.locator('[data-wallpaper-picker] [data-wallpaper-card="none"]').click();
    assert.equal(await html('data-chat-backdrop'), 'none');
    assert.equal(await html('data-chat-theme'), palette, 'None restores the palette');
    // Esc closes without applying.
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('Escape');
    await picker.waitFor({ state: 'hidden' });
    assert.equal(await html('data-chat-backdrop'), 'none');

    // addWallpaper: a valid spec is listed; an invalid one is refused.
    const added = await page.evaluate((spec) => FuseChatAppearance.addWallpaper('night-reef', spec), EXAMPLE);
    assert.deepEqual(added, { ok: true, errors: [] });
    const refused = await page.evaluate((spec) => FuseChatAppearance.addWallpaper('bad', { ...spec, layers: 'x' }), EXAMPLE);
    assert.equal(refused.ok, false);
    await input.fill('/theme');
    await input.press('Enter');
    await picker.waitFor({ state: 'visible' });
    const listed = await page.locator('[data-wallpaper-picker] [data-wallpaper-card]').evaluateAll((els) => els.map((el) => el.dataset.wallpaperCard));
    assert.deepEqual(listed, ['none', ...PRESETS, 'night-reef']);
    await page.keyboard.press('Escape');

    // Desktop also opens it from Sidebar › Customize.
    if (VIEW === 'desktop') {
      if (await page.locator('[data-chat].history-collapsed').count()) await page.locator('[data-chat-bar] [data-history-toggle]').click();
      await page.locator('[data-history] [data-theme-customize]').click();
      await picker.waitFor({ state: 'visible' });
      await page.keyboard.press('Escape');
      await picker.waitFor({ state: 'hidden' });
    }

    // Each theme keeps its own background and palette across Layout switches.
    await page.evaluate(() => { FuseChatAppearance.setBackdrop('jellyfish'); FuseChatAppearance.setTheme('ember'); });
    const others = (await page.locator('[data-chat-view-choice]').evaluateAll((els) => els.map((el) => el.dataset.chatViewChoice))).filter((view) => view !== VIEW);
    assert.ok(others.length >= 1, 'Layout lists the other themes');
    // A theme may keep Appearance in its overflow menu (Desktop's ⋯, Combined's phone ⋯).
    const appearance = async () => {
      if (!await page.locator('[data-view-toggle]').isVisible()) await page.locator('[data-theme-more]').click();
      await page.locator('[data-view-toggle]').click();
    };
    const layout = async (view) => {
      await appearance();
      await Promise.all([
        page.waitForNavigation(),
        page.getByRole('dialog', { name: 'Appearance', exact: true }).locator(`[data-chat-view-choice="${view}"]`).click(),
      ]);
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
      assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), view);
    };
    const look = async () => ({ backdrop: await html('data-chat-backdrop'), theme: await html('data-chat-theme') });
    for (const view of others) {
      await layout(view);
      const there = await look();
      assert.notDeepEqual(there, { backdrop: 'jellyfish', theme: 'ember' }, `${view} keeps its own look`);
      await page.evaluate((v) => FuseChatAppearance.setBackdrop(v === 'desktop' ? 'moonlit' : 'rain'), view);
      // The Appearance sheet says which theme the edit is saved for.
      await appearance();
      assert.match(await page.getByRole('dialog', { name: 'Appearance', exact: true }).innerText(), new RegExp(`Saved for this theme \\(${view[0].toUpperCase()}${view.slice(1)}\\)`));
      await page.keyboard.press('Escape');
    }
    await layout(VIEW);
    assert.deepEqual(await look(), { backdrop: 'jellyfish', theme: 'ember' }, 'the first theme kept its look');
    await layout(others[0]);
    assert.equal(await html('data-chat-backdrop'), others[0] === 'desktop' ? 'moonlit' : 'rain');
    await layout(VIEW);
    assert.deepEqual(errors, []);
    console.log(`PASS theme picker and per-theme looks (${VIEW})`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

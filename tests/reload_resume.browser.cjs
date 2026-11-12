// A reload comes back to what was on screen (static/chat/boot.js), against
// the isolated server only: the chat, the reading position, the draft and its
// caret; on a phone, the chat or the list, whichever was showing. A plain
// navigation starts fresh. (Opened blocks: console_groups.browser.cjs.)
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const VIEW = () => {
  const thread = document.querySelector('[data-chat-thread]');
  const edge = thread.getBoundingClientRect().top;
  const anchor = [...document.querySelector('[data-chat-log]').children].find(el => !el.hidden && el.getBoundingClientRect().bottom > edge);
  const input = document.querySelector('[data-chat-input]');
  return {
    url: location.pathname + location.search,
    anchor: anchor ? anchor.dataset.messageId : '',
    offset: anchor ? Math.round(anchor.getBoundingClientRect().top - edge) : 0,
    gap: Math.round(thread.scrollHeight - thread.scrollTop - thread.clientHeight),
    list: document.querySelector('.chat').classList.contains('is-history'),
    input: input.value,
    caret: [input.selectionStart, input.selectionEnd],
    focused: document.activeElement === input,
  };
};

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const open = async (options) => {
    const context = await browser.newContext(options);
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()).catch(() => {}));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    return page;
  };
  const live = page => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
  // The reading position is held for two seconds while the layout settles.
  const reloaded = async (page) => {
    await page.reload();
    await live(page);
    await page.waitForTimeout(2300);
    return page.evaluate(VIEW);
  };
  try {
    // Short enough that a few replies overflow it.
    const page = await open({ viewport: { width: 900, height: 400 } });
    await page.goto(origin);
    await live(page);
    for (const text of ['first question', 'second question', 'third question', 'fourth question']) {
      await page.locator('[data-chat-input]').fill(text);
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !fuseToolFixture.runInfo(), null, { timeout: 30000 });
    }
    const url = new URL(page.url());
    assert.ok(url.searchParams.get('t'), 'the chat is in the address');

    // Read partway up and leave a draft with the caret inside it.
    await page.locator('[data-chat-thread]').hover();
    await page.mouse.wheel(0, -180);
    await page.waitForTimeout(300);
    await page.locator('[data-chat-input]').fill('half a thought here');
    await page.evaluate(() => document.querySelector('[data-chat-input]').setSelectionRange(4, 9));
    await page.waitForTimeout(300);
    const before = await page.evaluate(VIEW);
    assert.ok(before.gap > 100 && before.anchor && before.focused, `the view to restore is set up: ${JSON.stringify(before)}`);
    const after = await reloaded(page);
    assert.ok(Math.abs(after.offset - before.offset) <= 2, `the reading position is kept: ${before.offset} -> ${after.offset}`);
    assert.deepEqual({ ...after, offset: 0, gap: 0 }, { ...before, offset: 0, gap: 0 });
    console.log('PASS a reload keeps the chat, reading position, draft and caret');

    // The reader can leave that spot at once: the hold yields to them.
    await page.reload();
    await live(page);
    await page.locator('[data-chat-thread]').hover();
    await page.mouse.wheel(0, 5000);
    await page.waitForTimeout(2300);
    assert.ok((await page.evaluate(VIEW)).gap <= 2, 'scrolling right after a reload is not fought');
    console.log('PASS the restored position yields to the reader');

    // Following the end is restored as following.
    assert.ok((await reloaded(page)).gap <= 2, 'a followed chat comes back at its end');
    console.log('PASS a followed chat stays at its end');

    // Arriving by navigation is not a reload: the chat opens at its end.
    await page.locator('[data-chat-thread]').hover();
    await page.mouse.wheel(0, -180);
    await page.waitForTimeout(300);
    assert.ok((await page.evaluate(VIEW)).gap > 100);
    await page.goto(origin + '/static/icon.svg');
    await page.goto(url.href);
    await live(page);
    await page.waitForTimeout(500);
    assert.ok((await page.evaluate(VIEW)).gap <= 2, 'a navigation starts at the end of the chat');
    console.log('PASS a plain navigation starts fresh');
    await page.locator('[data-chat-input]').fill('');
    await page.waitForTimeout(900);

    // Settings pages scroll inside their own box, which browsers do not restore.
    await page.goto(origin + '/settings');
    const scrolled = await page.evaluate(() => {
      const box = document.querySelector('.settings-scroll');
      box.scrollTop = 120;
      return box.scrollTop;
    });
    assert.equal(scrolled, 120, 'the page is tall enough to scroll');
    await page.reload();
    await page.waitForTimeout(2300);
    assert.equal(await page.evaluate(() => document.querySelector('.settings-scroll').scrollTop), 120);
    console.log('PASS a settings page keeps its scroll');

    const phone = await open({ viewport: { width: 390, height: 700 }, isMobile: true, hasTouch: true });
    await phone.goto(origin);
    await live(phone);
    assert.equal((await phone.evaluate(VIEW)).list, true, 'a phone opens on the chat list');
    await phone.evaluate(() => FuseChatActions.run('new'));
    await phone.locator('[data-chat-input]').fill('phone draft');
    await phone.waitForTimeout(300);
    let view = await reloaded(phone);
    assert.equal(view.list, false, 'a new chat is still on screen, not the list');
    assert.equal(view.input, 'phone draft');
    console.log('PASS phone: a reload keeps a new chat and its draft');
    await phone.locator('[data-chat-input]').fill('');
    await phone.waitForTimeout(900);

    await phone.goto(url.href);
    await live(phone);
    await phone.locator('.chat-bar [data-history-toggle]').tap();
    await phone.waitForFunction(() => document.querySelector('.chat').classList.contains('is-history'));
    view = await reloaded(phone);
    assert.equal(view.list, true, 'the list drawn over a chat comes back');
    assert.equal(view.url, url.pathname + url.search);
    await phone.goBack();
    await phone.waitForFunction(() => !document.querySelector('.chat').classList.contains('is-history'));
    assert.equal((await phone.evaluate(VIEW)).url, url.pathname + url.search, 'back from that list is the chat under it');
    console.log('PASS phone: a reload keeps the list over a chat, and back still leads to the chat');

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

// Chat toast lifecycle against the isolated server only: a toast for an
// action in flight (Stopping…, Retrying…) lives exactly as long as the action,
// a result toast keeps its full time instead of being wiped by a save, and
// every notice is the site's one toast stack (fuseUI.toast): under the header,
// clear of the composer, timed, paused while hovered, usable over a dialog.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()).catch(() => {}));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });

    // A chat notice is a toast keyed "chat:…"; its text is title + message.
    const CHAT = '.fuse-toast[data-toast-key^="chat:"] .fuse-toast-body';
    const toasts = () => page.evaluate(sel => [...document.querySelectorAll(sel)].map(el => el.textContent), CHAT);
    const runGone = () => page.waitForFunction(() => !fuseToolFixture.runInfo());
    const streaming = () => page.waitForFunction(() => document.body.classList.contains('is-streaming'));
    const idle = () => page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !fuseToolFixture.runInfo(), null, { timeout: 30000 });
    const toastGoneWithin = async (text, ms, what) => {
      await page.waitForFunction(([sel, text]) => ![...document.querySelectorAll(sel)].some(el => el.textContent === text), [CHAT, text], { timeout: ms })
        .catch(() => { throw new Error(`${what} toast outlived its action by more than ${ms}ms`); });
    };
    const toastShows = (text, timeout = 2000) => page.waitForFunction(([sel, text]) => [...document.querySelectorAll(sel)].some(el => el.textContent === text), [CHAT, text], { timeout });
    const send = async text => {
      await page.locator('[data-chat-input]').fill(text);
      await page.locator('[data-chat-send]').click();
    };
    // The first Enter accepts the command from the palette; a non-local one
    // then needs a second Enter to run.
    const slash = async text => {
      const box = page.locator('[data-chat-input]');
      await box.fill(text);
      await box.press('Enter');
      if (await box.inputValue()) await box.press('Enter');
    };

    // Stop: the toast leaves as soon as the run is stopped, not on a timer.
    await send('Queue fixture tool-step toast stop');
    await streaming();
    await page.waitForTimeout(400);
    await page.locator('[data-chat-send]').click();
    await toastShows('Stopping…');
    await runGone();
    await toastGoneWithin('Stopping…', 1000, 'Stopping…');
    console.log('PASS Stopping… leaves when the run stops');

    // A slow stop keeps saying Stopping… for as long as it takes.
    await send('Queue fixture tool-step toast slow stop');
    await streaming();
    await page.route('**/cancel', async route => { await new Promise(r => setTimeout(r, 3200)); await route.continue(); }, { times: 1 });
    await page.waitForTimeout(400);
    await page.locator('[data-chat-send]').click();
    await page.waitForTimeout(2800);
    assert.ok(runActive(await page.evaluate(() => fuseToolFixture.runInfo())), 'the run is still stopping');
    assert.ok((await toasts()).includes('Stopping…'), 'Stopping… stays while the stop is in flight');
    await runGone();
    await toastGoneWithin('Stopping…', 1000, 'Stopping…');
    console.log('PASS Stopping… stays through a slow stop');

    // Retry says so until the replacement turn is accepted.
    await send('Plain message for toast retry');
    await streaming();
    await idle();
    await slash('/retry');
    await toastShows('Retrying…');
    await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
    await toastGoneWithin('Retrying…', 1000, 'Retrying…');
    assert.ok(await page.evaluate(() => document.body.classList.contains('is-streaming')), 'the retried reply is still generating');
    await idle();
    console.log('PASS Retrying… leaves once the retry is running');

    // A result toast keeps its time; the save it triggers must not wipe it.
    await slash('/undo');
    await page.waitForTimeout(1200);
    assert.ok((await toasts()).includes('Undid last turn.'));
    console.log('PASS result toast survives the save it triggers');

    // A plain hint is timed by its length: gone within its reading time.
    await page.evaluate(() => FuseChatActions.hint('Copied link.'));
    await toastShows('Copied link.');
    await toastGoneWithin('Copied link.', 4000, 'Copied link.');
    console.log('PASS a plain hint times out');

    // A cache miss stays until dismissed; a passing hint sits beside it.
    await send('Cache fixture miss');
    await streaming();
    await idle();
    const miss = 'Cache miss · 51k of 52k input tokens uncached';
    await toastShows(miss);
    await page.waitForTimeout(9000);
    assert.ok((await toasts()).includes(miss), 'the miss outlasts the longest timed toast');
    await page.evaluate(() => FuseChatActions.hint('Copied link.'));
    await toastShows('Copied link.');
    assert.ok((await toasts()).includes(miss), 'a passing hint does not replace the miss');
    await page.locator('.fuse-toast[data-toast-key^="chat:cache:"] .fuse-toast-x').click();
    await toastGoneWithin(miss, 1000, 'Dismissed cache miss');
    await page.waitForTimeout(3000);
    assert.ok(!(await toasts()).includes(miss), 'a dismissed miss does not come back');
    console.log('PASS cache miss toast is sticky, lives beside a passing hint, and dismisses');

    // A miss caused by a different reasoning effort names both efforts.
    await send('Cache fixture effort');
    await streaming();
    await idle();
    const effortMiss = 'Cache miss · 51k of 52k input tokens uncached (reasoning effort changed: Default → Extra high)';
    await toastShows(effortMiss);
    await page.locator('.fuse-toast[data-toast-key^="chat:cache:"] .fuse-toast-x').click();
    await toastGoneWithin(effortMiss, 1000, 'Dismissed effort miss');
    await page.waitForTimeout(3000);
    assert.ok(!(await toasts()).includes(effortMiss), 'a dismissed effort miss does not come back');
    console.log('PASS an effort cache miss names the old and new effort');

    // Placement: under the chat bar, clear of the composer, tone on the toast.
    await page.evaluate(() => fuseUI.toast({ key: 'chat:probe', kind: 'err', title: 'Could not copy', msg: 'Clipboard blocked.' }));
    const placed = await page.evaluate(() => {
      const t = document.querySelector('.fuse-toast[data-toast-key="chat:probe"]');
      const r = t.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, bar: document.querySelector('.chat-bar').getBoundingClientRect().bottom,
        box: document.querySelector('.chat-box').getBoundingClientRect().top, err: t.classList.contains('is-err') };
    });
    assert.ok(placed.err && placed.top >= placed.bar && placed.bottom < placed.box, `toast sits under the bar and above the composer: ${JSON.stringify(placed)}`);
    await page.locator('.fuse-toast[data-toast-key="chat:probe"] .fuse-toast-x').click();
    console.log('PASS toasts sit under the chat bar, clear of the composer');

    // Hovering stops the clock; leaving starts it again.
    await page.evaluate(() => fuseUI.toast({ key: 'probe-ttl', msg: 'Short-lived', ttl: 600 }));
    await page.hover('.fuse-toast[data-toast-key="probe-ttl"]');
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('.fuse-toast[data-toast-key="probe-ttl"]').count(), 1, 'a hovered toast stays');
    await page.mouse.move(5, 500);
    await page.locator('.fuse-toast[data-toast-key="probe-ttl"]').waitFor({ state: 'detached', timeout: 3000 });
    console.log('PASS hover pauses a toast');

    // Over a modal dialog the stack moves into it, so a toast stays usable.
    await page.evaluate(() => { fuseConfirm('Probe?'); fuseUI.toast({ key: 'probe-modal', kind: 'err', msg: 'Shown over a dialog' }); });
    await page.waitForFunction(() => document.querySelector('.fuse-toasts')?.closest('dialog[open]'));
    const overModal = await page.evaluate(() => {
      const t = document.querySelector('.fuse-toast[data-toast-key="probe-modal"]');
      const r = t.getBoundingClientRect();
      return t.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    });
    assert.ok(overModal, 'a toast over an open dialog is on top and not inert');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('.fuse-toasts')?.parentElement === document.body);
    console.log('PASS toasts move into an open dialog and back');

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

function runActive(run) {
  return Boolean(run && ['queued', 'running'].includes(run.status));
}

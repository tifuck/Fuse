// Build watcher (static/app.js buildWatch) against the isolated server only:
// a page that learns the server runs a newer UI shows a Reload toast and
// reloads itself when nobody is mid-task, never over work only the page holds,
// never into a server that does not answer, and at most once per build.
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

    // What /build answers: null is the real server, 'down' no answer at all.
    let server = null;
    await page.route(origin + '/build', route => (
      server === null ? route.fallback() : server === 'down' ? route.abort() : route.fulfill({ status: 200, contentType: 'text/plain', body: server })
    ).catch(() => {}));

    const TOAST = '.fuse-toast[data-toast-key="fuse-build"]';
    const META = 'meta[name="fuse-build"]';
    const live = () => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    const visibility = hidden => page.evaluate(hidden => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event('visibilitychange'));
    }, hidden).catch(() => {});  // a reload tears the evaluation down
    const hide = () => visibility(true);
    const show = () => visibility(false);
    const socketSays = build => page.evaluate(build => window.fuseBuild(build), build);
    const mark = () => page.evaluate(() => { window.__sameView = true; });
    const kept = async (what, ms = 700) => {
      await page.waitForTimeout(ms);
      assert.equal(await page.evaluate(() => window.__sameView === true), true, what);
    };
    const reloads = async (trigger, what) => {
      const loaded = page.waitForEvent('load', { timeout: 5000 }).catch(() => { throw new Error(what); });
      await trigger();
      await loaded;
      await live();
    };

    await page.goto(origin);
    await live();
    const real = (await (await page.request.get(origin + '/build')).text()).trim();
    assert.match(real, /^[0-9a-f]{12}$/);
    assert.equal(await page.getAttribute(META, 'content'), real, 'the page carries the build it was rendered from');
    await page.waitForTimeout(300);
    assert.equal(await page.locator(TOAST).count(), 0);
    console.log('PASS a current page is left alone');

    server = 'b1';
    await mark();
    await socketSays('b1');
    await page.locator(TOAST).waitFor();
    await kept('a visible page in use is not reloaded');
    console.log('PASS a stale page in use gets the Reload toast, not a reload');

    // Work only this page holds. Each hold is set before the last is lifted,
    // so the hidden page is never free to reload in between.
    const holds = [
      ['a typed field', async () => {
        await page.evaluate(() => {
          const form = document.createElement('form');
          form.id = 'probe-form';
          form.innerHTML = '<input type="text" id="probe-field">';
          form.addEventListener('submit', event => event.preventDefault());
          document.body.append(form);
        });
        await page.type('#probe-field', 'unsaved');
      }, () => page.evaluate(() => document.getElementById('probe-form').requestSubmit())],
      ['an open dialog', () => page.evaluate(() => { fuseConfirm('Probe?'); }), () => page.keyboard.press('Escape')],
      ['content shown once', () => page.evaluate(() => {
        const once = document.createElement('p');
        once.id = 'probe-once';
        once.setAttribute('data-no-reload', '');
        document.body.append(once);
      }), () => page.evaluate(() => document.getElementById('probe-once').remove())],
      ['a focused artifact frame', async () => {
        await page.evaluate(() => {
          const frame = document.createElement('iframe');
          frame.id = 'probe-frame';
          document.body.append(frame);
          frame.focus();
        });
        assert.equal(await page.evaluate(() => document.activeElement.id), 'probe-frame');
      }, () => page.evaluate(() => document.getElementById('probe-frame').remove())],
      ['an attached file', async () => {
        await page.setInputFiles('[data-chat-file]', { name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('only in this page') });
        await page.waitForFunction(() => window.fuseUnsaved());
      }, null],
    ];
    let lift = null;
    for (const [name, hold, release] of holds) {
      await hold();
      if (lift) await lift();
      await hide();
      await kept(`${name} holds the reload`);
      console.log(`PASS ${name} holds the reload`);
      lift = release;
    }
    await show();
    await kept('bringing a held page back does not reload it');
    await page.reload();  // drops the attachment
    await live();

    // The composer's draft is synced, so it does not hold the page.
    server = 'b2';
    await page.locator('[data-chat-input]').fill('half a thought');
    await page.waitForFunction(() => !window.fuseUnsaved());
    await socketSays('b2');
    await reloads(hide, 'a hidden stale page reloads');
    assert.equal(await page.getAttribute(META, 'content'), real);
    await page.waitForFunction(() => document.querySelector('[data-chat-input]').value === 'half a thought');
    console.log('PASS a hidden stale page reloads and keeps the composer draft');

    // The server still answers b2: the page that came back must not loop.
    await mark();
    await hide();
    await show();
    await page.locator(TOAST).waitFor();
    await hide();
    await kept('one automatic reload per build');
    console.log('PASS a build that survives the reload is not reloaded for again');

    await page.reload();
    await live();
    await page.waitForTimeout(500);
    server = 'b3';
    await hide();
    await reloads(show, 'a stale page reloads when it is brought back');
    console.log('PASS a page brought back stale reloads before it is used');

    server = null;
    await socketSays('b4');
    await page.locator(TOAST).waitFor();
    await socketSays(real);
    await page.locator(TOAST).waitFor({ state: 'detached' });
    console.log('PASS a rollback to the page\'s build withdraws the toast');

    // Stale, but the last word from the server is old and it no longer answers.
    server = 'down';
    await mark();
    await socketSays('b5');
    await page.waitForTimeout(5200);
    await hide();
    await kept('no reload into a server that does not answer', 1000);
    console.log('PASS a page does not reload into an unreachable server');
    server = 'b5';
    await page.waitForTimeout(5200);
    await reloads(hide, 'the reload happens once the server answers again');
    console.log('PASS it reloads once the server answers again');

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

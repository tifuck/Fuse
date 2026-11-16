// Build watcher (static/app.js buildWatch) against the isolated server only:
// a page that learns the server runs a newer UI reloads itself, unprompted,
// once nobody is using it (hidden, just brought back, or idle), never over
// work only the page holds, never into a server that does not answer, and at
// most once per build.
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
    await context.route(origin + '/build', route => (
      server === null ? route.fallback() : server === 'down' ? route.abort() : route.fulfill({ status: 200, contentType: 'text/plain', body: server })
    ).catch(() => {}));

    const TOAST = '.fuse-toast';
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
    await kept('a visible page in use is not reloaded');
    assert.equal(await page.locator(TOAST).count(), 0, 'and is not asked to reload');
    console.log('PASS a stale page in use is neither reloaded nor prompted');

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

    // It says so once someone is back, and the account menu keeps the time.
    assert.equal(await page.locator(TOAST).count(), 0, 'nothing is shown to an empty room');
    await page.mouse.move(200, 200);
    await page.mouse.move(220, 220);
    await page.locator(TOAST).filter({ hasText: 'Fuse updated' }).waitFor();
    assert.equal(await page.locator('[data-build-note]').textContent(), 'Auto-updated just now');
    console.log('PASS the reloaded page says it updated, on the first input');

    // The server still answers b2: the page that came back must not loop.
    await mark();
    await hide();
    await show();
    await kept('one automatic reload per build');
    await hide();
    await kept('one automatic reload per build');
    console.log('PASS a build that survives the reload is not reloaded for again');

    await page.reload();
    await live();
    await page.mouse.move(240, 240);
    await page.waitForTimeout(500);
    assert.equal(await page.locator(TOAST).count(), 0, 'a manual reload is not announced');
    assert.equal(await page.locator('[data-build-note]').textContent(), 'Auto-updated just now', 'the menu still says when');
    server = 'b3';
    await hide();
    await reloads(show, 'a stale page reloads when it is brought back');
    console.log('PASS a page brought back stale reloads before it is used');

    server = null;
    await mark();
    await socketSays('b4');
    await socketSays(real);
    await hide();
    await kept('a page whose build is current again is not reloaded');
    await show();
    await page.waitForTimeout(500);  // the check that coming back starts
    console.log('PASS a rollback to the page\'s build calls the reload off');

    // Stale, but the last word from the server is old and it no longer answers.
    server = 'down';
    await mark();
    await socketSays('b5');
    await page.waitForTimeout(5200);
    await hide();
    await kept('no reload into a server that does not answer', 1000);
    console.log('PASS a page does not reload into an unreachable server');
    // The hidden page asks again on its own, within a tick of the watcher.
    const back = page.waitForEvent('load', { timeout: 20000 }).catch(() => { throw new Error('the reload happens once the server answers again'); });
    server = 'b5';
    await back;
    await live();
    console.log('PASS it reloads once the server answers again');

    // Visible and left alone, on a page with no chat socket: it learns of the
    // build from its own timer and reloads once IDLE_MS passes without input,
    // with no prompt. Its own page, so only this part runs on a fake clock.
    // (A chat page here would hear the fixture's real build on its socket.)
    const idle = await context.newPage();
    idle.on('pageerror', error => errors.push(error.message));
    await idle.clock.install();
    server = null;
    await idle.goto(origin + '/keys');
    assert.equal(await idle.getAttribute(META, 'content'), real);
    server = 'b6';
    await idle.evaluate(() => { window.__sameView = true; });
    await idle.clock.fastForward('01:30');
    await idle.waitForTimeout(700);
    assert.equal(await idle.evaluate(() => window.__sameView === true), true, 'not before the idle time is up');
    const again = idle.waitForEvent('load', { timeout: 5000 }).catch(() => { throw new Error('an idle stale page reloads'); });
    await idle.clock.fastForward('01:00');
    await again;
    assert.equal(await idle.locator(TOAST).count(), 0);
    console.log('PASS a visible page left idle learns of the build and reloads on its own');

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

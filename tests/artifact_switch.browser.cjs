// The artifact pane follows the chat the reader opens, with real chats made
// by real turns (the scripted provider in chat_browser_fixture.py): a chat
// with artifacts shows its own latest one, a chat without closes the pane,
// coming back to a chat reopens the pane it was left with, and a deleted
// artifact is not one a chat still "has".
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    // The chat list is how a reader moves between chats: open it.
    await page.locator('[data-history-toggle]:visible').first().click();
    await page.waitForFunction(() => !document.querySelector('.chat').classList.contains('history-collapsed'));
    const input = page.locator('#chat-input');
    const pane = page.locator('[data-chat-artifact]');
    const turn = async (text, done) => {
      const before = await page.locator('[data-chat-log] [data-flow]').count();
      await input.fill(text);
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(([n, done]) => {
        const all = [...document.querySelectorAll('[data-chat-log] [data-flow]')];
        return all.length > n && all.at(-1).textContent.includes(done);
      }, [before, done], { timeout: 30000 });
      await page.waitForFunction(() => !document.querySelector('[data-chat-send].is-stop'), null, { timeout: 30000 });
      return new URL(page.url()).searchParams.get('t');
    };
    const newChat = async () => {
      await page.locator('[data-chat-new]:visible').first().click();
      await page.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
    };
    const openChat = async (id) => {
      await page.locator(`[data-history-list]:visible [data-thread="${id}"] button.open`).click();
      await page.waitForFunction((id) => new URL(location.href).searchParams.get('t') === id && document.querySelector('[data-chat-log]')._threadId === id, id);
    };
    const artifactOf = () => page.evaluate(() => fuseToolFixture.artifactState().current?.identifier || '');
    // The pane is open on `id`: its frame loads that artifact's page.
    const shows = (id) => page.waitForFunction((id) => !document.querySelector('[data-chat-artifact]').hidden
      && (document.querySelector('[data-artifact-frame]').getAttribute('src') || '').includes(`/a/${id}/content`), id, { timeout: 10000 });
    const closed = async (why) => {
      await page.waitForFunction(() => document.querySelector('[data-chat-artifact]').hidden, null, { timeout: 5000 }).catch(() => {});
      assert.equal(await pane.isHidden(), true, why);
      assert.equal(await page.locator('[data-artifact-frame]').getAttribute('src'), null, `${why}: no page keeps running behind it`);
    };

    // Chat A makes an artifact: the first one of a chat opens the pane.
    const a = await turn('Artifact fixture line: make a Q3 revenue board', 'Ask if you want churn added');
    await pane.waitFor({ state: 'visible' });
    const artA = await artifactOf();
    await shows(artA);

    // A new chat has no artifact: the pane closes. Chat B never gets one.
    await newChat();
    await closed('a new chat opens without the last chat\'s artifact');
    const b = await turn('Plain question', 'Live output across both devices');
    assert.equal(await pane.isHidden(), true, 'a chat without artifacts keeps the pane closed');

    // Chat C makes its own.
    await newChat();
    const c = await turn('Artifact fixture line: make another board', 'Ask if you want churn added');
    await pane.waitFor({ state: 'visible' });
    const artC = await artifactOf();
    assert.notEqual(artC, artA);
    await shows(artC);

    // An open pane follows the chat: A's artifact in A, C's in C.
    await openChat(a);
    await shows(artA);
    assert.equal(await artifactOf(), artA, 'chat A shows its own artifact, not the one from the chat just left');
    await openChat(c);
    await shows(artC);

    // A chat without artifacts closes it; coming back opens it again.
    await openChat(b);
    await closed('a chat without artifacts closes the pane');
    assert.equal(await page.locator('[data-artifact-toggle] span').innerText(), 'Artifact');
    await openChat(c);
    await shows(artC);
    await openChat(b);
    await closed('... every time');
    await openChat(a);
    await shows(artA);

    // A pane the reader closed stays closed when they come back to that chat.
    await page.locator('[data-artifact-close]').click();
    await openChat(b);
    await openChat(a);
    await closed('the reader closed it in this chat');
    assert.equal(await artifactOf(), artA, 'the toggle still opens this chat\'s artifact');

    // An artifact opened on purpose in a chat that has none does not follow
    // the reader into other chats.
    await openChat(b);
    await page.evaluate((id) => fuseToolFixture.artifacts.showArtifact(id, null, { explicit: true }), artA);
    await shows(artA);
    await openChat(c);
    await shows(artC);

    // A deleted artifact is not one its chat still has.
    const status = await page.evaluate((id) => fetch(`/a/${id}/delete`, { method: 'POST', credentials: 'same-origin', body: '{}', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content } }).then((res) => res.status), artC);
    assert.equal(status, 200);
    await openChat(a);
    await shows(artA);
    await openChat(c);
    await closed('a chat whose only artifact was deleted has none to show');

    assert.deepEqual(errors, []);
    console.log('PASS: artifact pane follows the chat — own latest artifact, closes without one, reopens on return, stays closed when closed, explicit opens stay behind, deleted artifacts do not count');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

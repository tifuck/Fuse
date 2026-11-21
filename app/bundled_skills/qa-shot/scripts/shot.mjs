#!/usr/bin/env node
// qa-shot: screenshots of a running web app for an agent to look at. Opens
// pages in headless Chromium, runs your steps with a real mouse and keyboard,
// and writes every shot into one labelled PNG. Open the path it prints with
// read_file. Every request that is not a GET is aborted unless you pass
// --allow-writes, so a run changes nothing and any number can run at once.
//
//   node shot.mjs --base http://localhost:3000                        the home page
//   node shot.mjs --base URL --page /,/pricing --size 1440x900,390x844   two pages, desktop and phone
//   node shot.mjs --base URL --find "Sign in"                         the selector of whatever shows that text
//   node shot.mjs --base URL --click 'button.menu' --shot '.menu-list'   open a menu, shoot it
//   node shot.mjs --base URL --eval 'document.title'                  measure, no image
//
// Where. A comma list gives one labelled cell per combination, all in one image:
//   --base URL          the app (required unless a --setup gives one)
//   --page PATH[,PATH]  the pages to open, default /
//   --size WxH[,WxH]    viewport, default 1440x900. WxAUTO is as tall as the
//                       page needs, so nothing scrolls
//   --static PREFIX=DIR[,PREFIX=DIR]
//                       serve URLs under PREFIX from DIR instead of the server
//                       (a build's JS and CSS before it is deployed); several
//                       are compared side by side
//   --touch             a touch screen (no hover, coarse pointer), not a mouse;
//                       with --size 390x844 a phone
//   --zoom N            N device pixels per CSS pixel (1 to 4), to read small things
//
// Signing in:
//   --cookies FILE      a JSON array of cookies ({name, value, domain|url, ...})
//                       set before the first page. With --login, the session is
//                       written back here after a sign-in and reused next time
//   --login PATH        when the first page lands on PATH, fill its form with
//                       $SHOT_USER and $SHOT_PASS and submit it
//
// Steps, run in the order given, once per combination, with a real mouse and keys:
//   --find TEXT         print a selector for each visible element whose text,
//                       label or name holds TEXT, and for what it sits in
//   --click CSS         click the first visible match; fails if something
//                       else lies on top of it
//   --hover CSS         move the pointer onto it
//   --type TEXT         type into whatever has focus (click it first)
//   --key KEY           Enter, Escape, Tab, ArrowDown, a, Control+k ...
//   --wait CSS | MS     until a match is visible (5 s at most), or that many ms
//   --eval JS           run page script and print what it returns (a promise is
//                       awaited)
//   --shot [CSS]        one cell: the first visible match, or the viewport. An
//                       element taller than the area it scrolls in is shot
//                       whole; a box that scrolls its own content is shot as shown
//   --pad N             N px of what is around the element as well (default 0):
//                       after a --shot for that shot, before the first for all
//   With no --shot, the viewport is shot after the last step, unless the run
//   only reads the page (--find, --eval): then no image is made.
//
// Output:
//   --out FILE.png      where the image goes. Default: /tmp/qa-shot/, which this
//                       tool owns and clears of PNGs older than an hour
//   --motion            let the page animate (reduced motion is emulated by
//                       default so shots are stable)
//   --allow-writes      let POST, PUT, PATCH and DELETE through (steps that
//                       submit a form). They change the app's data
//   --setup FILE.mjs    app-specific hooks: options, sign-in, page rewrites
//                       (see the hooks at main() below)
//
// read_file scales down an image side over 2048 px, so a tall shot is cut into
// parts and a big sheet into pages (-1.png, -2.png). Exit 1 on failure, 2 on a
// bad option; the page's script errors are listed. Needs Node 22 or newer and
// Chromium: Playwright's headless shell (npx playwright install
// chromium-headless-shell), $CHROME, or a chromium/google-chrome on PATH.
// No npm packages: it drives Chromium over the DevTools protocol.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { basename, delimiter, dirname, extname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SELF = fileURLToPath(import.meta.url);
const MAX = 2048;   // read_file shows at most this many pixels a side
const TALL = 2000;  // so a cell is at most this tall, which leaves room for its caption
const LIMIT = 12000;  // the tallest viewport a shot may grow to
const VALUES = new Set(['page', 'size', 'zoom', 'pad', 'out', 'base', 'static', 'cookies', 'login', 'setup']);
const FLAGS = new Set(['touch', 'motion', 'allow-writes']);
const STEPS = new Set(['find', 'click', 'hover', 'type', 'key', 'wait', 'eval']);
const KEYS = { Enter: [13, '\r'], Escape: [27], Tab: [9], Backspace: [8], Delete: [46], Space: [32, ' '], ArrowLeft: [37], ArrowUp: [38],
  ArrowRight: [39], ArrowDown: [40], Home: [36], End: [35], PageUp: [33], PageDown: [34] };
const MODS = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };
const TYPES = { '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp',
  '.woff2': 'font/woff2', '.html': 'text/html; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.wasm': 'application/wasm' };

const list = (text) => String(text).split(',').map((part) => part.trim()).filter(Boolean);
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
// "Control+k" as the fields of a DevTools key event; null for a key this does not know.
function keyOf(combo) {
  const mods = combo.length > 1 ? combo.split('+') : [combo];
  const name = mods.pop();
  if (mods.some((mod) => !MODS[mod]) || !(KEYS[name] || name.length === 1)) return null;
  const modifiers = mods.reduce((sum, mod) => sum + MODS[mod], 0);
  const [windowsVirtualKeyCode, text] = KEYS[name] || [name.toUpperCase().charCodeAt(0), name];
  return { key: name === 'Space' ? ' ' : name, code: KEYS[name] ? name : /[a-z]/i.test(name) ? `Key${name.toUpperCase()}` : '',
    windowsVirtualKeyCode, modifiers, ...(text && !(modifiers & 7) ? { text } : {}) };
}

// Playwright's headless shell first (fastest, made for this), then $CHROME,
// then a full Chromium or Chrome on PATH. [path, isHeadlessShell]
function chromePath() {
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), process.platform === 'darwin' ? 'Library/Caches' : '.cache', 'ms-playwright');
  try {
    const dir = readdirSync(cache).filter((name) => name.startsWith('chromium_headless_shell-')).sort().pop();
    if (dir) {
      const sub = readdirSync(join(cache, dir)).find((name) => name.startsWith('chrome-headless-shell'));
      const exe = sub && join(cache, dir, sub, process.platform === 'win32' ? 'chrome-headless-shell.exe' : 'chrome-headless-shell');
      if (exe && existsSync(exe)) return [exe, true];
    }
  } catch { /* no Playwright cache */ }
  if (process.env.CHROME) return [process.env.CHROME, false];
  for (const dir of (process.env.PATH || '').split(delimiter)) {
    for (const name of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome']) {
      if (dir && existsSync(join(dir, name))) return [join(dir, name), false];
    }
  }
  throw new Error(`no Chromium found: run "npx playwright install chromium-headless-shell", or set $CHROME`);
}

// The first match a user can see: a hidden twin (a closed menu, another
// layout's copy) is never what a step means.
const PICK = `((css) => [...document.querySelectorAll(css)].find((el) => { const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && el.checkVisibility({ visibilityProperty: true }); }) || null)`;

// --find, run in the page: a selector, the size and the text of each visible
// element whose own text, label, or hook, class or id name holds the words. The
// selector is the element's hook, id or classes, under its nearest named
// parent when that is what makes it the first visible match.
function findInPage(want) {
  const text = want.toLowerCase();
  const seen = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && el.checkVisibility({ visibilityProperty: true }); };
  const first = (css) => [...document.querySelectorAll(css)].find(seen);
  const hooks = (el) => el.getAttributeNames().filter((name) => name.startsWith('data-'));
  const own = (el) => {
    const [hook] = hooks(el);
    if (hook) {
      const value = el.getAttribute(hook);
      return first(`[${hook}]`) !== el && /^[\w .:/-]{1,30}$/.test(value) ? `[${hook}="${value}"]` : `[${hook}]`;
    }
    if (el.id) return `#${CSS.escape(el.id)}`;
    return el.classList.length ? `.${[...el.classList].slice(0, 2).map((name) => CSS.escape(name)).join('.')}` : el.tagName.toLowerCase();
  };
  const selector = (el) => {
    let css = own(el);
    for (let up = el.parentElement; up && up !== document.body && first(css) !== el; up = up.parentElement) {
      if (up.id || hooks(up).length) css = `${own(up)} ${own(el)}`;
    }
    return css;
  };
  const found = new Map();
  for (const el of document.querySelectorAll('body *')) {
    if (found.size === 15) break;
    if (!seen(el)) continue;
    const said = [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent.toLowerCase().includes(text))
      || ['aria-label', 'title', 'placeholder', 'alt'].some((attr) => (el.getAttribute(attr) || '').toLowerCase().includes(text));
    const named = [el.id, ...el.classList, ...hooks(el)].some((name) => name.toLowerCase().includes(text.replace(/\s+/g, '-')));
    if (!said && !named) continue;
    // Text sits in a span; what a step wants is the control around it.
    const target = (said && el.closest('button, a, input, select, textarea, summary, label, [role]')) || el;
    const css = selector(target);
    if (found.has(css)) continue;
    // A row or a menu is found through one of its parts: name what the part sits in.
    const around = [];
    for (let up = target.parentElement; up && up !== document.body && around.length < 3; up = up.parentElement) {
      const box = up.getBoundingClientRect();
      if (up.id || hooks(up).length || up.classList.length) around.push(`${selector(up)} ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
    const r = target.getBoundingClientRect();
    const words = (target.innerText || target.getAttribute('aria-label') || target.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ');
    found.set(css, `${css}${first(css) === target ? '' : '  (not its first visible match)'}  ${target.tagName.toLowerCase()} ${Math.round(r.width)}x${Math.round(r.height)}  ${JSON.stringify(words.slice(0, 50))}${around.length ? `  in ${around.join(' < ')}` : ''}`);
  }
  return [...found.values()].join('\n');
}

// How far the element is taller than the room its nearest scrolling box (or the window) gives it.
const tallerBy = (css, pad) => `(() => { const el = ${PICK}(${JSON.stringify(css)}); if (!el) return 0;
  let room = innerHeight;
  for (let box = el.parentElement; box; box = box.parentElement) {
    if (/auto|scroll/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight) room = Math.min(room, box.clientHeight);
  }
  return Math.ceil(el.getBoundingClientRect().height + ${2 * pad} - room); })()`;
// How far the page's main scrolling box (the largest one that still scrolls, or the window) scrolls.
const SCROLLS = `(() => { let main = null;
  for (const el of [document.scrollingElement, ...document.querySelectorAll('body *')]) {
    if (el.scrollHeight - el.clientHeight > 1 && (el === document.scrollingElement || /auto|scroll/.test(getComputedStyle(el).overflowY))
      && (!main || el.clientWidth * el.clientHeight > main.clientWidth * main.clientHeight)) main = el;
  }
  return main ? main.scrollHeight - main.clientHeight : 0; })()`;

// Hooks a --setup module (its default export) may define, all optional:
//   help         the file whose leading // comment --help prints instead
//   base, outDir defaults for --base and the image directory
//   values, flags  more option names, read into api.opts (values take one argument)
//   steps        { name: async (api, arg) => {} }: more steps; a name ending in
//                ":" takes an argument (--name VALUE), others take none
//   configure(api)   after options are read: check them (api.bad), and set
//                api.variants ([{ name, ... }], shot side by side, e.g. themes),
//                api.statics, api.listOnly
//   start(api)       once Chromium is ready, before any page (init scripts)
//   signIn(api)      instead of --cookies/--login
//   targets(api)     after sign-in: the pages ([{ path, name }]), or null to stop
//   prepare(api, combo)  before each combination's page loads
//   ready(api, combo)    after it loaded; return false when the page is the
//                same in every variant, so it is shot once
//   allowWrite(url, method)   true lets one write through the guard
//   responseKind(request)     a name for a response to rewrite, or ''
//   rewriteBody(kind, text)   the rewritten body
//   responsePatterns          Fetch patterns for responses to rewrite
//   report(api)      after the image is written: lines for the end of the output
export async function main(argv, setup = {}) {
  const help = setup.help || SELF;
  const bad = (message) => {
    console.error(`shot: ${message}\n      node ${help} --help lists every option`);
    process.exit(2);
  };
  if (!argv.length || argv.includes('--help') || argv.includes('-h')) {
    const lines = readFileSync(help, 'utf8').split('\n');
    console.log(lines.slice(1, lines.findIndex((line, i) => i && !line.startsWith('//'))).map((line) => line.replace(/^\/\/ ?/, '')).join('\n'));
    process.exit(0);
  }
  const extraValues = new Set(setup.values || []);
  const extraFlags = new Set(setup.flags || []);
  const extraSteps = setup.steps || {};
  const stepTakes = (key) => Object.prototype.hasOwnProperty.call(extraSteps, `${key}:`);
  const opts = { size: '1440x900', base: setup.base || '', pad: '0', zoom: '1', steps: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith('--')) bad(`"${argv[i]}" is not an option (quote a value that holds spaces)`);
    const key = argv[i].slice(2);
    const next = argv[i + 1];
    if (Object.prototype.hasOwnProperty.call(extraSteps, key)) opts.steps.push(['custom', key]);
    else if (key === 'shot') {
      opts.steps.push(['shot', next !== undefined && !next.startsWith('--') ? argv[++i] : '']);
    } else if (FLAGS.has(key) || extraFlags.has(key)) opts[key] = true;
    else if (VALUES.has(key) || STEPS.has(key) || extraValues.has(key) || stepTakes(key)) {
      if (next === undefined || (next.startsWith('--') && key !== 'eval' && key !== 'type')) bad(`--${key} needs a value`);
      i += 1;
      const shot = key === 'pad' && opts.steps.findLast(([kind]) => kind === 'shot');
      if (STEPS.has(key)) opts.steps.push([key, next]);
      else if (stepTakes(key)) opts.steps.push(['custom', `${key}:`, next]);
      else if (shot) shot[2] = next;
      else opts[key] = opts[key] !== undefined && setup.repeat?.includes(key) ? `${opts[key]},${next}` : next;
    } else bad(`unknown option --${key}`);
  }
  if (!opts.base) bad('--base URL is required');
  try { new URL(opts.base); } catch { bad(`--base ${opts.base}: use a URL such as http://localhost:3000`); }
  opts.base = opts.base.replace(/\/$/, '');
  const sizes = list(opts.size).map((text) => {
    const found = /^(\d+)x(\d+|auto)$/i.exec(text) || bad(`--size ${text}: use WxH such as 1440x900, or 1440xauto`);
    return { text: text.toLowerCase(), width: Number(found[1]), height: Number(found[2]) || 900, auto: !Number(found[2]) };
  });
  const pages = list(opts.page || '/');
  pages.forEach((path) => path.startsWith('/') || bad(`--page ${path}: use a path such as /pricing`));
  const zoom = Number(opts.zoom);
  const padOf = (text) => (Number(text) >= 0 ? Number(text) : bad(`--pad ${text}: use a number of pixels`));
  const pad = padOf(opts.pad);
  opts.steps.forEach((step) => { if (step[0] === 'shot' && step[2] !== undefined) step[2] = padOf(step[2]); });
  if (!(zoom >= 1 && zoom <= 4)) bad(`--zoom ${opts.zoom}: use 1 to 4`);
  if (opts.out && !/\.png$/i.test(opts.out)) bad('--out must end in .png');
  opts.steps.forEach(([kind, arg]) => kind === 'key' && !keyOf(arg) && bad(`--key ${arg}: use ${Object.keys(KEYS).join(', ')}, one character, or ${Object.keys(MODS).join('+, ')}+ before one`));

  let ws;
  let sessionId;
  let nextId = 0;
  let guarding = false;
  let staticMap = null;
  const waiting = new Map();
  const listeners = new Set();
  const pageErrors = [];
  const blocked = [];
  const send = (method, params = {}) => new Promise((done, fail) => {
    const id = ++nextId;
    waiting.set(id, { done, fail, method });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const once = (method, ms = 20000) => new Promise((done, fail) => {
    const timer = setTimeout(() => { listeners.delete(fn); fail(new Error(`timed out waiting for ${method}`)); }, ms);
    const fn = (msg) => { if (msg.method === method) { clearTimeout(timer); listeners.delete(fn); done(msg.params); } };
    listeners.add(fn);
  });
  async function run(expression) {
    const out = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (out.exceptionDetails) throw new Error(`page script failed: ${out.exceptionDetails.exception?.description || out.exceptionDetails.text}`);
    return out.result.value;
  }
  async function go(url) {
    const loaded = once('Page.loadEventFired');
    await send('Page.navigate', { url });
    await loaded;
  }
  // A step may have started a page load, during which a script fails: try again.
  async function until(expression, what, ms = 15000) {
    const end = Date.now() + ms;
    let why = '';
    while (Date.now() < end) {
      try { if (await run(expression)) return; why = ''; } catch (error) { why = error.message; }
      await sleep(100);
    }
    throw new Error(`timed out waiting for ${what}${why ? ` (${why})` : ''}`);
  }
  const api = { opts, bad, send, run, go, once, until, sleep, list, PICK, pages, sizes, variants: [{ name: '' }], statics: null, listOnly: false };

  // --static PREFIX=DIR: each alternative is one more combination.
  api.statics = opts.static ? list(opts.static).map((pair) => {
    const at = pair.indexOf('=');
    if (at < 1) bad(`--static ${pair}: use PREFIX=DIR such as /assets/=./dist`);
    const prefix = pair.slice(0, at);
    const dir = resolve(pair.slice(at + 1));
    if (!prefix.startsWith('/') || !existsSync(dir)) bad(`--static ${pair}: PREFIX starts with / and DIR must exist`);
    return { name: basename(dir), label: dir, prefix, root: dir };
  }) : [{ name: '', label: '', prefix: '', root: '' }];
  await setup.configure?.(api);
  const { variants, statics } = api;

  // With no --shot, a run that only reads the page (--eval, --find) makes no image; any other shoots the viewport last.
  if (!api.listOnly && !opts.steps.some(([kind]) => kind === 'shot') && (opts.out || !opts.steps.some(([kind]) => kind === 'eval' || kind === 'find'))) opts.steps.push(['shot', '']);
  const shots = opts.steps.filter(([kind]) => kind === 'shot').length;

  // Images go to a directory this tool owns, so nobody has to name or delete them.
  const own = setup.outDir || join(tmpdir(), 'qa-shot');
  let outFile = opts.out ? resolve(opts.out) : '';
  if (shots && !outFile) {
    mkdirSync(own, { recursive: true, mode: 0o700 });
    for (const name of readdirSync(own)) {
      const file = join(own, name);
      try { if (name.endsWith('.png') && Date.now() - statSync(file).mtimeMs > 3600e3) rmSync(file, { force: true }); } catch { /* another run took it */ }
    }
    outFile = join(own, `${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}-${process.pid}.png`);
  } else if (outFile) mkdirSync(dirname(outFile), { recursive: true });

  const [chromeExe, shell] = (() => { try { return chromePath(); } catch (error) { console.error(`shot failed: ${error.message}`); process.exit(1); } })();
  const profile = mkdtempSync(join(tmpdir(), 'qa-shot-'));
  // Headless Chromium reports no pointer at all, which stylesheets read as
  // neither mouse nor touch. Say which one: a mouse (hover, fine pointer) unless
  // --touch asks for a finger (no hover, coarse pointer).
  const pointer = opts.touch ? 'primaryHoverType=1,availableHoverTypes=1,primaryPointerType=2,availablePointerTypes=2'
    : 'primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4';
  const chrome = spawn(chromeExe, [...(shell ? [] : ['--headless=new']), '--no-sandbox', '--ignore-certificate-errors', '--hide-scrollbars',
    '--remote-debugging-port=0', `--blink-settings=${pointer}`, `--user-data-dir=${profile}`, `--window-size=${sizes[0].width},${sizes[0].height}`,
    'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const gone = new Promise((done) => chrome.on('exit', done));
  // Chromium is slow to leave on a plain signal and still writes to its profile
  // while it shuts down: ask it to close, wait, then insist.
  const cleanup = async () => {
    if (ws?.readyState === 1) ws.send(JSON.stringify({ id: ++nextId, method: 'Browser.close' }));
    await Promise.race([gone, new Promise((done) => setTimeout(done, 3000).unref())]);
    chrome.kill('SIGKILL');
    await gone;
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  };

  async function rewrite({ requestId, request, responseStatusCode, responseHeaders = [] }) {
    const kind = setup.responseKind?.(request) || '';
    const type = responseHeaders.find((header) => header.name.toLowerCase() === 'content-type')?.value || '';
    if (!kind || responseStatusCode !== 200 || /event-stream/.test(type)) return send('Fetch.continueRequest', { requestId });
    const got = await send('Fetch.getResponseBody', { requestId });
    const text = got.base64Encoded ? Buffer.from(got.body, 'base64').toString('utf8') : got.body;
    return send('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: responseHeaders.filter((header) => !/^content-(length|encoding)$/i.test(header.name)),
      body: Buffer.from(setup.rewriteBody(kind, text, request)).toString('base64') });
  }
  // Every request stops here once the guard is on: a write is aborted (unless
  // allowed), a --static file comes from its directory, anything else goes through.
  function paused(params) {
    const { requestId, request } = params;
    if (params.responseStatusCode !== undefined || params.responseErrorReason) return rewrite(params);
    const url = new URL(request.url);
    if (guarding && !['GET', 'HEAD', 'OPTIONS'].includes(request.method) && !opts['allow-writes'] && !setup.allowWrite?.(url, request.method)) {
      blocked.push(`${request.method} ${url.pathname}`);
      return send('Fetch.failRequest', { requestId, errorReason: 'Aborted' });
    }
    if (staticMap?.root && url.origin === new URL(opts.base).origin && url.pathname.startsWith(staticMap.prefix)) {
      const file = resolve(staticMap.root, decodeURIComponent(url.pathname.slice(staticMap.prefix.length)));
      const inside = file.startsWith(staticMap.root + '/');
      let body = null;
      try { if (inside) body = readFileSync(file); } catch { body = null; }
      return send('Fetch.fulfillRequest', {
        requestId, responseCode: body ? 200 : 404,
        responseHeaders: [{ name: 'Content-Type', value: TYPES[extname(file)] || 'application/octet-stream' }, { name: 'Cache-Control', value: 'no-store' }],
        body: (body || Buffer.from('not in --static')).toString('base64'),
      });
    }
    // A body that will be rewritten is asked for uncompressed.
    if (setup.responseKind?.(request)) {
      const headers = Object.entries(request.headers).filter(([name]) => name.toLowerCase() !== 'accept-encoding').map(([name, value]) => ({ name, value }));
      return send('Fetch.continueRequest', { requestId, headers });
    }
    return send('Fetch.continueRequest', { requestId });
  }

  let viewport = [0, 0];
  let cell = '';  // the combination being shot, named in a failure
  async function metrics(width, height, scale = zoom) {
    viewport = [width, height];
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: Boolean(opts.touch) });
  }
  // Why nothing matched, and the visible hooks, classes and ids named most like it.
  async function miss(css, flag) {
    const found = await run(`(() => {
      const words = (text) => text.toLowerCase().split(/[^a-z0-9]+/).filter((word) => word && word !== 'data');
      const want = words(${JSON.stringify(css)});
      const seen = new Map();
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const names = [...el.getAttributeNames().filter((name) => name.startsWith('data-')).map((name) => '[' + name + ']'),
          ...[...el.classList].map((name) => '.' + name), ...(el.id ? ['#' + el.id] : [])];
        for (const name of names) {
          if (seen.has(name)) continue;
          const have = words(name);
          seen.set(name, want.reduce((sum, word) => sum + Math.max(0, ...have.map((got) => (got === word ? 2
            : Math.min(got.length, word.length) > 3 && (got.includes(word) || word.includes(got)) ? 1 : 0))), 0));
        }
      }
      return { hidden: document.querySelectorAll(${JSON.stringify(css)}).length,
        near: [...seen].filter(([, score]) => score > 0).sort((a, b) => b[1] - a[1] || a[0].length - b[0].length).slice(0, 8).map(([name]) => name) };
    })()`);
    return `nothing visible matches ${flag} ${css}${found.hidden ? ` (${found.hidden} hidden)` : ''}${found.near.length ? `. Visible here: ${found.near.join(' ')}` : ''}`;
  }
  // The element's box in the viewport, after scrolling it into view.
  async function boxOf(css, flag) {
    const box = await run(`(() => { const el = ${PICK}(${JSON.stringify(css)}); if (!el) return null;
      el.scrollIntoView({ block: 'center', inline: 'nearest' }); const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()`);
    if (!box) throw new Error(await miss(css, flag));
    return box;
  }
  // Make the viewport taller while the page says something is still missing and
  // growing helps. A step that changed nothing (a box of fixed height) is taken back.
  async function grow(missing) {
    let last = Infinity;
    for (let i = 0; i < 4; i += 1) {
      const over = Number(await run(missing)) || 0;  // -0 and NaN do not survive the trip from the page
      const [width, height] = viewport;
      if (over <= 0 || (over < last && height >= LIMIT)) return;
      if (over >= last) return metrics(width, height - last);
      last = Math.min(over, LIMIT - height);
      await metrics(width, height + last);
      await sleep(200);
    }
  }
  // One --shot: the element or the viewport as one cell, or several when it is
  // taller than read_file shows unscaled.
  async function shoot(css, room, auto, caption, about, cells) {
    const before = viewport;
    let area;
    let note = '';
    if (css) {
      await boxOf(css, '--shot');
      await grow(tallerBy(css, room));
      const box = await boxOf(css, '--shot');
      const x = Math.max(0, Math.floor(box[0] - room));
      const y = Math.max(0, Math.floor(box[1] - room));
      area = { x, y, width: Math.min(viewport[0] - x, Math.ceil(box[2] + 2 * room)), height: Math.min(viewport[1] - y, Math.ceil(box[3] + 2 * room)) };
      if (box[0] < 0 || box[1] < 0 || box[0] + box[2] > viewport[0] + 1 || box[1] + box[3] > viewport[1] + 1) note = ' (cut by the viewport)';
    } else {
      if (auto) await grow(SCROLLS);
      area = { x: 0, y: 0, width: viewport[0], height: viewport[1] };
    }
    const step = Math.floor(TALL / zoom);
    const parts = Math.ceil(area.height / step);
    for (let i = 0; i < parts; i += 1) {
      const clip = { ...area, y: area.y + i * step, height: Math.min(step, area.height - i * step), scale: 1 };
      const data = (await send('Page.captureScreenshot', { format: 'png', clip })).data;
      const png = Buffer.from(data, 'base64');
      const part = parts > 1 ? ` (part ${i + 1}/${parts})` : '';
      cells.push({ data, width: png.readUInt32BE(16), height: png.readUInt32BE(20), caption: `${caption}${part}${note}`, about: `${about}${part}${note}` });
    }
    if (viewport[1] !== before[1]) {
      await metrics(...before);
      await sleep(200);
    }
  }
  // Several cells become labelled sheets, drawn by this same browser: rows as
  // wide and pages as tall as read_file shows unscaled.
  async function sheets(cells) {
    if (cells.length === 1) return [{ png: Buffer.from(cells[0].data, 'base64'), width: cells[0].width, height: cells[0].height, what: cells[0].about }];
    const GAP = 12;
    const LABEL = 20;
    const rows = [];
    for (const one of cells) {
      const row = rows.at(-1);
      if (row && row.width + GAP + one.width <= MAX - 2 * GAP) {
        row.cells.push(one);
        row.width += GAP + one.width;
        row.height = Math.max(row.height, one.height + LABEL);
      } else rows.push({ cells: [one], width: one.width, height: one.height + LABEL });
    }
    const found = [];
    for (const row of rows) {
      const page = found.at(-1);
      if (page && page.height + GAP + row.height <= MAX - 2 * GAP) {
        page.rows.push(row);
        page.height += GAP + row.height;
        page.width = Math.max(page.width, row.width);
      } else found.push({ rows: [row], width: row.width, height: row.height });
    }
    const safe = (text) => text.replace(/[&<>"]/g, (ch) => `&#${ch.charCodeAt(0)};`);
    const out = [];
    staticMap = null;
    for (const page of found) {
      const width = page.width + 2 * GAP;
      const height = page.height + 2 * GAP;
      const html = page.rows.map((row) => `<div style="display:flex;gap:${GAP}px;align-items:flex-start;margin-bottom:${GAP}px">${row.cells.map((one) =>
        `<div><div style="height:${LABEL}px;max-width:${one.width}px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${safe(one.caption)}</div>`
        + `<img width="${one.width}" height="${one.height}" style="display:block" src="data:image/png;base64,${one.data}"></div>`).join('')}</div>`).join('');
      await metrics(width, height, 1);
      await go('about:blank');
      await run(`document.body.style.cssText = 'margin:0;padding:${GAP}px;background:#3a3f4b;color:#fff;font:13px/${LABEL}px ui-monospace,monospace';
        document.body.innerHTML = ${JSON.stringify(html)}; Promise.all([...document.images].map((img) => img.decode())).then(() => true)`);
      const shot = await send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width, height, scale: 1 } });
      out.push({ png: Buffer.from(shot.data, 'base64'), width, height, what: page.rows.flatMap((row) => row.cells.map((one) => one.caption)).join(' | ') });
    }
    return out;
  }

  // --cookies / --login: a generic sign-in, with the session kept in the cookie file.
  async function signIn() {
    const file = opts.cookies ? resolve(opts.cookies) : '';
    if (file && existsSync(file)) {
      const cookies = JSON.parse(readFileSync(file, 'utf8')).map((c) => (c.domain || c.url ? c : { ...c, url: opts.base }));
      await send('Network.setCookies', { cookies });
    }
    if (!opts.login) return;
    await go(opts.base + pages[0]);
    if (new URL(await run('location.href')).pathname.replace(/\/$/, '') !== opts.login.replace(/\/$/, '')) return;
    const user = process.env.SHOT_USER;
    const pass = process.env.SHOT_PASS;
    if (!user || !pass) throw new Error(`${opts.login} asks for a sign-in: set SHOT_USER and SHOT_PASS`);
    const signedIn = once('Page.loadEventFired');
    const filled = await run(`(() => { const pw = document.querySelector('input[type=password]'); const form = pw?.form; if (!form) return false;
      const id = form.querySelector('input[type=email], input[name=email], input[name=username], input[name=user], input[name=login], input[autocomplete=username], input[type=text]');
      if (id) id.value = ${JSON.stringify(user)};
      pw.value = ${JSON.stringify(pass)};
      form.requestSubmit ? form.requestSubmit() : form.submit(); return true; })()`);
    if (!filled) throw new Error(`${opts.login} has no form with a password field`);
    await signedIn;
    const at = new URL(await run('location.href')).pathname;
    if (at.replace(/\/$/, '') === opts.login.replace(/\/$/, '')) {
      const why = await run(`document.querySelector('[role=alert]')?.textContent.trim() || ''`);
      throw new Error(`sign-in stayed on ${at}${why ? `: ${why}` : ''}`);
    }
    if (file) {
      const { cookies } = await send('Network.getCookies', { urls: [opts.base] });
      writeFileSync(`${file}.${process.pid}`, JSON.stringify(cookies), { mode: 0o600 });
      renameSync(`${file}.${process.pid}`, file);
    }
  }

  try {
    const wsUrl = await new Promise((done, fail) => {
      let seen = '';
      chrome.stderr.on('data', (chunk) => {
        seen += chunk;
        const found = /DevTools listening on (ws:\/\/\S+)/.exec(seen);
        if (found) done(found[1]);
      });
      chrome.on('exit', () => fail(new Error('Chromium exited before it was ready')));
      setTimeout(() => fail(new Error('Chromium did not start in 15s')), 15000).unref();
    });
    ws = new WebSocket(wsUrl);
    await new Promise((done, fail) => { ws.onopen = done; ws.onerror = () => fail(new Error('DevTools socket failed')); });
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && waiting.has(msg.id)) {
        const { done, fail, method } = waiting.get(msg.id);
        waiting.delete(msg.id);
        if (msg.error) fail(new Error(`${method}: ${msg.error.message}`));
        else done(msg.result);
        return;
      }
      if (msg.method === 'Fetch.requestPaused') paused(msg.params).catch(() => {});
      if (msg.method === 'Runtime.exceptionThrown') pageErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      // An aborted write logs a failed load: that is the guard, not a page fault.
      if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error' && !/ERR_ABORTED|ERR_FAILED/.test(msg.params.entry.text)) {
        pageErrors.push(`${msg.params.entry.text} ${msg.params.entry.url || ''}`.trim());
      }
      listeners.forEach((fn) => fn(msg));
    };
    const { targetInfos } = await send('Target.getTargets');
    sessionId = (await send('Target.attachToTarget', { targetId: targetInfos.find((target) => target.type === 'page').targetId, flatten: true })).sessionId;
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable');
    await send('Fetch.enable', { patterns: [{ urlPattern: '*' }, ...(setup.responsePatterns?.(api) || [])] });
    // Two builds in one run: the second must not be handed the first one's files.
    if (statics.length > 1) await send('Network.setCacheDisabled', { cacheDisabled: true });
    await metrics(sizes[0].width, sizes[0].height);
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: opts.motion ? 'no-preference' : 'reduce' }] });
    await setup.start?.(api);
    await (setup.signIn ? setup.signIn(api) : signIn());
    guarding = true;

    const targets = setup.targets ? await setup.targets(api) : pages.map((path) => ({ path, name: path }));
    if (targets) {
      const cells = [];
      for (const target of targets) {
        let each = true;
        for (const variant of variants) for (const stat of statics) for (const size of sizes) {
          if (!each && variant !== variants[0]) continue;
          const label = () => [targets.length > 1 && target.name, variants.length > 1 && each && variant.name, statics.length > 1 && stat.name,
            sizes.length > 1 && size.text].filter(Boolean).join(' · ');
          cell = label();
          staticMap = stat;
          const combo = { target, variant, static: stat, size, first: variant === variants[0] && stat === statics[0] && size === sizes[0] };
          await setup.prepare?.(api, combo);
          await metrics(size.width, size.height);
          await go(opts.base + target.path);
          const at = await run('location.pathname');
          if (at.replace(/\/$/, '') !== new URL(opts.base + target.path).pathname.replace(/\/$/, '')) throw new Error(`${target.path} sent the browser to ${at}`);
          const status = await run(`performance.getEntriesByType('navigation')[0]?.responseStatus || 200`);
          if (status >= 400) throw new Error(`${target.path} answered ${status}`);
          each = (await setup.ready?.(api, combo)) !== false;
          await run('document.fonts.ready.then(() => true)');
          await sleep(300);

          cell = label();
          const name = cell;
          const about = [target.name, each && variant.name, size.text, opts.touch && 'touch', stat.label && `static from ${stat.label}`].filter(Boolean).join('  ');
          let shot = 0;
          // Every step runs in the order given, so a script can read what a hover
          // changed and rows can be opened after the page settles.
          for (const [kind, arg, room = pad] of opts.steps) {
            if (kind === 'custom') {
              await extraSteps[arg](api, room);
            } else if (kind === 'eval') {
              const value = await run(arg);
              if (value !== undefined) console.log(`${name ? `[${name}] ` : ''}${typeof value === 'string' ? value : JSON.stringify(value)}`);
            } else if (kind === 'find') {
              const found = await run(`(${findInPage})(${JSON.stringify(arg)})`);
              console.log(`${name ? `[${name}]\n` : ''}${found || `nothing visible holds "${arg}" in its text, label or name`}`);
            } else if (kind === 'shot') {
              shot += 1;
              const which = shots > 1 ? `#${shot} ${arg || 'viewport'}` : arg;
              await shoot(arg, room, size.auto, [name, shots > 1 || !name ? which : ''].filter(Boolean).join(' · ') || about, [about, which].filter(Boolean).join('  '), cells);
            } else if (kind === 'type') {
              await send('Input.insertText', { text: arg });
            } else if (kind === 'key') {
              const key = keyOf(arg);
              await send('Input.dispatchKeyEvent', { type: key.text ? 'keyDown' : 'rawKeyDown', ...key });
              await send('Input.dispatchKeyEvent', { type: 'keyUp', ...key });
            } else if (kind === 'wait') {
              if (/^\d+$/.test(arg)) await sleep(Number(arg));
              else await until(`Boolean(${PICK}(${JSON.stringify(arg)}))`, `--wait ${arg} to be visible`, 5000);
            } else {
              const box = await boxOf(arg, `--${kind}`);
              // A hover lands near the left edge, where a row's label is; a click in the middle.
              const point = { x: box[0] + (kind === 'hover' ? Math.min(box[2] / 2, 60) : box[2] / 2), y: box[1] + box[3] / 2 };
              // The pointer goes where a user's would: onto whatever lies on top.
              const cover = await run(`(() => { const el = ${PICK}(${JSON.stringify(arg)}); const hit = document.elementFromPoint(${point.x}, ${point.y});
                return !el || !hit || el.contains(hit) || hit.contains(el) ? '' : hit.tagName.toLowerCase() + [...hit.classList].map((name) => '.' + name).join(''); })()`);
              if (cover) throw new Error(`--${kind} ${arg}: it is covered by ${cover}, which the pointer would land on instead`);
              await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
              if (kind === 'click') {
                await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
                await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
              }
            }
            await sleep(250);
          }
        }
      }
      cell = '';
      const made = await sheets(cells);
      made.forEach((sheet, i) => {
        const file = made.length > 1 ? outFile.replace(/\.png$/i, `-${i + 1}.png`) : outFile;
        writeFileSync(file, sheet.png);
        const big = sheet.width > MAX || sheet.height > MAX ? `  (over ${MAX} px: read_file will scale it down; use a narrower --size or less --zoom)` : '';
        console.log(`${file}  ${sheet.width}x${sheet.height}  ${sheet.what}${big}`);
      });
    }
    (setup.report?.(api) || []).forEach((line) => console.log(line));
    if (blocked.length) console.log(`writes aborted (${blocked.length}): ${[...new Set(blocked)].slice(0, 6).join(', ')}`);
    // A listing prints only its lines (one per line, for scripts) unless the page failed.
    if (!api.listOnly || pageErrors.length) console.log(pageErrors.length ? `page errors (${pageErrors.length}):\n  ${pageErrors.slice(0, 8).join('\n  ')}` : 'page errors: none');
  } catch (error) {
    console.error(`shot failed: ${cell ? `[${cell}] ` : ''}${error.message}`);
    process.exitCode = 1;
  } finally {
    await cleanup();
    ws?.close();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === SELF) {
  const argv = process.argv.slice(2);
  const at = argv.indexOf('--setup');
  let setup = {};
  if (at >= 0 && argv[at + 1]) {
    const file = argv[at + 1];
    setup = (await import(pathToFileURL(isAbsolute(file) ? file : resolve(file)).href)).default || {};
  }
  await main(argv, setup);
}

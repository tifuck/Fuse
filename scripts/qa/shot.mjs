#!/usr/bin/env node
// The QA browser. Signs in to the dev instance as the QA login (an admin whose
// chats are made up: scripts/dev_qa_account.py), opens a page, runs your steps
// and writes what you shot as one PNG. Open the path it prints with read_file.
// Every request that is not a GET is aborted, so a run changes nothing and any
// number can run at once.
//
//   QA=/root/fuse-dev/scripts/qa/shot.mjs
//   node $QA --find ornith                                        the selector of whatever shows that text
//   node $QA --view all --shot '[data-chat-form]'                 the composer in all three views
//   node $QA --thread rows --open-rows --shot '.chat-step.is-console-call'   one call row of a QA chat
//   node $QA --tree dev,/root/worktrees/NAME --shot '[data-chat-form]'   dev next to a worktree
//   node $QA --page /usage,/admin/users --size 1440xauto          whole pages that are not the chat
//   node $QA --click '[data-chat-input]' --type / --shot          type "/" and shoot what opens
//   node $QA --thread rows --eval 'document.querySelectorAll(".chat-step").length'   measure, no image
//
// Where. A comma list gives one labelled cell per combination, all in one image:
//   --list              print the QA chats and stop
//   --thread TEXT       open the QA chat whose title contains TEXT (any case)
//   --page PATH[,PATH]  open another page: /usage, /settings, /admin/users ...
//                       Default: /chat with no chat open
//   --view V[,V] | all  combined | console | desktop (default). Chat and the
//                       settings pages; sign-in and artifact pages are drawn
//                       one way and shot once
//   --tree DIR[,DIR]    serve /static from this checkout: a worktree's JS and
//                       CSS before they merge. "dev" is the dev tree itself.
//                       Templates and Python always come from the dev instance
//   --size WxH[,WxH]    viewport, default 1440x900. WxAUTO is as tall as the
//                       page needs, so nothing scrolls
//   --touch             a touch screen (no hover, coarse pointer, overlay
//                       scrollbars that take no width), not a mouse;
//                       with --size 390x844 a phone. A phone shows the chat
//                       list over /chat until --thread opens a chat
//   --zoom N            N device pixels per CSS pixel (1 to 4), to read small things
//   --fake NAME[,NAME]  a live state, made by rewriting what the page reads from
//                       the server. Nothing runs:
//                         running  the last reply is still being worked on: status
//                                  line, clocks, a call still running (use with
//                                  --thread "live command", or "live code" for
//                                  a run_code whose calls are still running)
//                         ask      a pending permission prompt
//                         asks     three at once (a command, a subagent's file write,
//                                  a file read outside the project): the 1 of 3 card
//                         error    the last reply failed at the provider
//                         agents   the agents pane (Desktop inspector, Combined
//                                  rail) lists made-up subagents: running, done,
//                                  stopped, failed (use with --thread subagents
//                                  or --thread "live agents")
//                         stats    every chat is a project on the made-up machine
//                                  qa-box, whose stats socket answers in the page
//                                  with a sample a second. --eval "__qaStats.mode
//                                  = 'busy'" (or 'hot') raises the load;
//                                  __qaStats.stop() ends the samples (the card
//                                  goes stale 7 s later)
//                         dictation  the composer has a mic (dev has no speech
//                                  server, so its pages have none) that hears a
//                                  steady made-up voice: --click '[data-chat-mic]'
//                                  opens the live overlay and words land in the
//                                  box. The mic's markup is --tree's template
//   --trace JSON        with --fake running: the reply's running calls become
//                       these (an object, or an array of them), running unless
//                       they say otherwise. A call with "parent": true is made
//                       by the first one, a run_code. E.g. '{"name":"read_file",
//                       "path":"/srv/demo/app.py"}', or '[{"name":"run_code"}]'
//                       for a run_code before its first call
//
// Steps, run in the order given, once per combination, with a real mouse and keys:
//   --find TEXT         print a selector for each visible element whose text,
//                       label or name holds TEXT, and for what it sits in
//                       (its row, its menu): how to learn what to --shot
//   --click CSS         click the first visible match; fails if something
//                       else lies on top of it
//   --hover CSS         move the pointer onto it
//   --type TEXT         type into whatever has focus (click it first)
//   --key KEY           Enter, Escape, Tab, ArrowDown, a, Control+k ...
//   --wait CSS | MS     until a match is visible (5 s at most), or that many ms
//   --open-groups       open every folded work line of a chat
//   --open-rows         open those and every call row, in a group or on its own
//   --eval JS           run page script and print what it returns (a promise is
//                       awaited)
//   --shot [CSS]        one cell: the first visible match, or the viewport. An
//                       element taller than the area it scrolls in (a long
//                       transcript) is shot whole; a box that scrolls its own
//                       content (a menu with a fixed height) is shot as shown
//   --pad N             N px of what is around the element as well (default 0):
//                       after a --shot for that shot, before the first for all
//   With no --shot, the viewport is shot after the last step, unless the run
//   only reads the page (--find, --eval): then no image is made.
//
// Output:
//   --out FILE.png      where the image goes. Default: /tmp/fuse-qa/, which this
//                       tool owns and clears of PNGs older than an hour
//   --toasts            keep toast notices in the shot (hidden by default)
//   --motion            let the page animate (reduced motion is emulated by
//                       default so shots are stable); for checking motion
//   --live              let writes to /chat/* through, so steps can send a
//                       message: --click '[data-chat-input]' --type hi --click
//                       '[data-chat-send]' --wait 20000. A real run on the GPU
//                       server: keep prompts tiny. It can use fetch and web
//                       search, not shell or file tools, and leaves a chat
//                       behind: reseed with scripts/dev_qa_account.py
//   --base URL          the dev instance, default https://127.0.0.1:8088
//
// read_file scales down an image side over 2048 px, so a tall shot is cut into
// parts and a big sheet into pages (-1.png, -2.png). Exit 1 on failure, 2 on a
// bad option; the page's script errors are listed. No dependencies: Node's own
// WebSocket and Playwright's cached headless Chromium over the DevTools
// protocol. Tracked on dev, never released to main; the login stays in data/.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SELF = fileURLToPath(import.meta.url);
const ROOT = join(dirname(SELF), '..', '..');
const OWN = '/tmp/fuse-qa';
const MAX = 2048;   // read_file shows at most this many pixels a side
const TALL = 2000;  // so a cell is at most this tall, which leaves room for its caption
const LIMIT = 12000;  // the tallest viewport a shot may grow to
const THEMES = join(ROOT, 'static', 'themes');
const VIEWS = readdirSync(THEMES).filter((id) => existsSync(join(THEMES, id, 'theme.json')));
const FAKES = ['running', 'ask', 'asks', 'error', 'agents', 'stats', 'dictation'];
const VALUES = new Set(['thread', 'page', 'view', 'tree', 'size', 'zoom', 'pad', 'fake', 'out', 'base', 'trace']);
const FLAGS = new Set(['list', 'toasts', 'touch', 'live', 'motion']);
const STEPS = new Set(['find', 'click', 'hover', 'type', 'key', 'wait', 'eval']);
const KEYS = { Enter: [13, '\r'], Escape: [27], Tab: [9], Backspace: [8], Delete: [46], Space: [32, ' '], ArrowLeft: [37], ArrowUp: [38],
  ArrowRight: [39], ArrowDown: [40], Home: [36], End: [35], PageUp: [33], PageDown: [34] };
const MODS = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };

const bad = (message) => {
  console.error(`shot: ${message}\n      node ${SELF} --help lists every option`);
  process.exit(2);
};
const list = (text) => String(text).split(',').map((part) => part.trim()).filter(Boolean);
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

const opts = { view: 'desktop', size: '1440x900', base: 'https://127.0.0.1:8088', pad: '0', zoom: '1', fake: '', steps: [] };
const argv = process.argv.slice(2);
if (!argv.length || argv.includes('--help') || argv.includes('-h')) {
  const lines = readFileSync(SELF, 'utf8').split('\n');
  console.log(lines.slice(1, lines.findIndex((line, i) => i && !line.startsWith('//'))).map((line) => line.replace(/^\/\/ ?/, '')).join('\n'));
  process.exit(0);
}
for (let i = 0; i < argv.length; i += 1) {
  if (!argv[i].startsWith('--')) bad(`"${argv[i]}" is not an option (quote a value that holds spaces)`);
  const key = argv[i].slice(2);
  const next = argv[i + 1];
  if (key === 'open-groups' || key === 'open-rows') opts.steps.push(['open', key.slice(5)]);
  else if (key === 'shot') {
    opts.steps.push(['shot', next !== undefined && !next.startsWith('--') ? argv[++i] : '']);
  } else if (FLAGS.has(key)) opts[key] = true;
  else if (VALUES.has(key) || STEPS.has(key)) {
    if (next === undefined || (next.startsWith('--') && key !== 'eval' && key !== 'type')) bad(`--${key} needs a value`);
    i += 1;
    const shot = key === 'pad' && opts.steps.findLast(([kind]) => kind === 'shot');
    if (STEPS.has(key)) opts.steps.push([key, next]);
    else if (shot) shot[2] = next;
    else opts[key] = key === 'fake' ? `${opts.fake},${next}` : next;
  } else bad(`unknown option --${key}`);
}
opts.fake = new Set(list(opts.fake));
opts.fake.forEach((name) => FAKES.includes(name) || bad(`--fake ${name}: use ${FAKES.join(', ')}`));
if (opts.fake.has('asks')) opts.fake.add('ask');
if (opts.trace !== undefined) {
  if (!opts.fake.has('running')) bad('--trace needs --fake running');
  try { opts.trace = [JSON.parse(opts.trace)].flat(); } catch (error) { bad(`--trace: ${error.message}`); }
}
const views = opts.view === 'all' ? VIEWS : list(opts.view);
views.forEach((view) => VIEWS.includes(view) || bad(`--view ${view}: use ${VIEWS.join(', ')} or all`));
const sizes = list(opts.size).map((text) => {
  const found = /^(\d+)x(\d+|auto)$/i.exec(text) || bad(`--size ${text}: use WxH such as 1440x900, or 1440xauto`);
  return { text: text.toLowerCase(), width: Number(found[1]), height: Number(found[2]) || 900, auto: !Number(found[2]) };
});
const trees = list(opts.tree || 'dev').map((dir) => {
  if (dir === 'dev') return { name: 'dev', root: '' };
  if (!existsSync(resolve(dir, 'static'))) bad(`--tree ${dir} has no static/ directory`);
  return { name: basename(resolve(dir)), root: resolve(dir, 'static') };
});
if (opts.thread && opts.page) bad('--thread opens a chat and --page another page: use one of them');
const pages = list(opts.page || '/chat');
pages.forEach((path) => path.startsWith('/') || bad(`--page ${path}: use a path such as /usage`));
const zoom = Number(opts.zoom);
const padOf = (text) => (Number(text) >= 0 ? Number(text) : bad(`--pad ${text}: use a number of pixels`));
const pad = padOf(opts.pad);
opts.steps.forEach((step) => { if (step[2] !== undefined) step[2] = padOf(step[2]); });
if (!(zoom >= 1 && zoom <= 4)) bad(`--zoom ${opts.zoom}: use 1 to 4`);
if (opts.out && !/\.png$/i.test(opts.out)) bad('--out must end in .png');
opts.steps.forEach(([kind, arg]) => kind === 'key' && !keyOf(arg) && bad(`--key ${arg}: use ${Object.keys(KEYS).join(', ')}, one character, or ${Object.keys(MODS).join('+, ')}+ before one`));
// With no --shot, a run that only reads the page (--eval, --find) makes no image; any other shoots the viewport last.
if (!opts.list && !opts.steps.some(([kind]) => kind === 'shot') && (opts.out || !opts.steps.some(([kind]) => kind === 'eval' || kind === 'find'))) opts.steps.push(['shot', '']);
const shots = opts.steps.filter(([kind]) => kind === 'shot').length;

// Images go to a directory this tool owns, so nobody has to name or delete them.
let outFile = opts.out ? resolve(opts.out) : '';
if (shots && !outFile) {
  mkdirSync(OWN, { recursive: true, mode: 0o700 });
  for (const name of readdirSync(OWN)) {
    const file = join(OWN, name);
    try { if (name.endsWith('.png') && Date.now() - statSync(file).mtimeMs > 3600e3) rmSync(file, { force: true }); } catch { /* another run took it */ }
  }
  outFile = join(OWN, `${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}-${process.pid}.png`);
} else if (outFile) mkdirSync(dirname(outFile), { recursive: true });

const creds = JSON.parse(readFileSync(join(ROOT, 'data', 'dev-qa-login.json'), 'utf8'));
const SESSION = join(ROOT, 'data', 'dev-qa-session.json');
let staticRoot = '';
const TYPES = { '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp',
  '.woff2': 'font/woff2', '.html': 'text/html; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.wasm': 'application/wasm' };

function chromePath() {
  const cache = join(homedir(), '.cache', 'ms-playwright');
  const dir = readdirSync(cache).filter((name) => name.startsWith('chromium_headless_shell-')).sort().pop();
  if (!dir) throw new Error(`no chromium_headless_shell in ${cache}`);
  return join(cache, dir, 'chrome-headless-shell-linux64', 'chrome-headless-shell');
}

const profile = mkdtempSync(join(tmpdir(), 'fuse-qa-shot-'));
// Headless Chromium reports no pointer at all, which the stylesheets read as
// neither mouse nor touch. Say which one: a mouse (hover, fine pointer) unless
// --touch asks for a finger (no hover, coarse pointer).
const pointer = opts.touch ? 'primaryHoverType=1,availableHoverTypes=1,primaryPointerType=2,availablePointerTypes=2'
  : 'primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4';
const chrome = spawn(chromePath(), ['--no-sandbox', '--ignore-certificate-errors', '--hide-scrollbars', '--remote-debugging-port=0', `--blink-settings=${pointer}`,
  `--user-data-dir=${profile}`, `--window-size=${sizes[0].width},${sizes[0].height}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

let ws;
let sessionId;
let nextId = 0;
let guarding = false;
const waiting = new Map();
const listeners = new Set();
const pageErrors = [];
const blocked = [];
const faked = [];
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

// --fake: states no fixture can hold, made by rewriting what the page reads
// (the chat, its run, its permission asks). The server is never told.
const startedAt = Date.now();
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d+Z$/, 'Z');
const ASK = {
  id: 'qa-perm-1', kind: 'tool_permission', status: 'pending', perm_kind: 'shell', tool: 'run_terminal_command', host: 'qa-box', path: '',
  cwd: '/srv/demo', command: 'rm -rf build && make release VERSION=2.4.0', secret: false, requester: 'Main agent', decision: '',
  grant_scope: '', grant_kind: '', grant_value: '', description: 'Deletes the build directory, then builds and tags the release.',
};
const MORE_ASKS = [
  { ...ASK, id: 'qa-perm-2', perm_kind: 'file', tool: 'write_file', path: '/srv/demo/notes/findings.md', cwd: '', command: '', requester: 'researcher',
    grant_kind: 'file', description: '', reasons: ['outside allowed folders'] },
  { ...ASK, id: 'qa-perm-3', perm_kind: 'file', tool: 'read_file', path: '/etc/nginx/sites-enabled/fuse.conf', cwd: '', command: '',
    grant_kind: 'file', description: '', reasons: ['outside allowed folders'] },
];
// The agents pane lists child runs; fixtures have none. These match the
// child_ids in "QA · Subagents" and "QA · Live agents".
const agentRow = (id, status, description, extra = {}) => ({
  id, status, description, subagent_type: 'general-purpose', background: true, depth: 1, model: 'qa/fixture-small',
  parent_run_id: 'qa-root', root_run_id: 'qa-root', created_at: iso(startedAt - 300000), updated_at: iso(startedAt - 5000),
  cancel_requested: false, cancel_requested_at: '', resume_count: 0, has_transcript: true, resumable: false, summary: '', error: '',
  children: 0, tool_counts: {}, recent: [], current: '', quiet_s: 5, wake_state: '', context: null, gen: {},
  work_ms: 60000, work_ms_before: null, work_live: false, ...extra,
});
const AGENTS = [
  agentRow('qa-live-child', 'running', 'Audit the Linux connector', { subagent_type: 'explore', current: 'tool', work_ms: 42000, work_live: true,
    tool_counts: { grep: 3, read_file: 4 }, context: { used: 21000, limit: 128000, ratio: 0.164 },
    recent: [{ name: 'grep', status: 'done', pattern: 'reattach' }, { name: 'read_file', status: 'running', path: '/srv/demo/agent/linux/session.go' }] }),
  agentRow('qa-child-win', 'completed', 'Audit the Windows connector', { work_ms: 212000, tool_counts: { read_file: 6, search_replace: 1 },
    summary: 'Same bug as Linux. Fixed in agent/windows/session.go.' }),
  agentRow('qa-child-recheck', 'cancelled', 'Recheck the hub after both fixes', { work_ms: 21000, resumable: true }),
  agentRow('qa-child-mac', 'failed', 'Audit the macOS connector', { subagent_type: 'plan', work_ms: null, error: 'no model is available for this account' }),
];
const FIXES = {
  agents(data) {
    return { ...data, agents: AGENTS, live_count: AGENTS.filter((row) => row.status === 'running').length };
  },
  thread(data) {
    const last = [...(data.messages || [])].reverse().find((message) => message.role === 'assistant');
    if (opts.fake.has('running') && last) {
      if (opts.trace) {
        const running = new Set((last.tools || []).filter((trace) => trace.status === 'running').map((trace) => trace.id));
        last.tools = (last.tools || []).filter((trace) => !running.has(trace.id));
        last.segments = (last.segments || []).filter((part) => !(part.type === 'tool' && running.has(part.id)));
        const first = 'qa-trace-0';
        opts.trace.forEach((fields, i) => {
          const trace = { id: `qa-trace-${i}`, status: 'running', host: 'qa-box', ...fields };
          if (fields.parent === true) trace.parent = first;
          else last.segments.push({ type: 'tool', id: trace.id });
          last.tools.push(trace);
        });
      }
      (last.tools || []).forEach((trace) => { if (trace.status === 'running') trace.started_ms = startedAt - 42000; });
      data.run = { ...(data.run || {}), id: data.run?.id || 'qa-live', status: 'running', message_id: last.id, message: last,
        created_at: iso(startedAt - 75000), updated_at: iso(startedAt), cancel_requested: false };
    }
    // An ask only waits inside a run, and the page reads asks only for a thread that is running.
    if (opts.fake.has('ask')) Object.assign(data, { bypass_permissions: false, auto_permissions: false, running: true });
    if (opts.fake.has('error') && last) last.error = 'Upstream returned 502 Bad Gateway after 3 retries (provider: qa/fixture-model)';
    return data;
  },
  run(data) {
    if (!opts.fake.has('running')) return data;
    return { ...data, status: 'running', created_at: iso(startedAt - 47000), updated_at: iso(startedAt), cancel_requested: false,
      compaction: { ...(data.compaction || {}), meter: { used: 18400, limit: 128000, run_id: data.id } } };
  },
  perms(data) {
    if (!opts.fake.has('ask')) return data;
    const pending = [{ ...ASK, expires_at: iso(startedAt + 240000) }];
    if (opts.fake.has('asks')) pending.push(...MORE_ASKS.map((ask, i) => ({ ...ask, expires_at: iso(startedAt + 180000 - i * 60000) })));
    return { ...data, bypass: false, auto: false, pending };
  },
};
// --fake stats: runs in the page before its own scripts. The thread lists
// (/chat/threads, /chat/sync) bind every chat to qa-box, and the machine's
// stats socket is a stub that never reaches the server.
const STATS_FAKE = `(() => {
  const GB = 2 ** 30;
  const LOADS = { calm: [11, 2.6, 0, 1.2e6, 12e3], busy: [68, 12.7, 42, 18e6, 2.1e6], hot: [97, 15, 96, 84e6, 9.6e6] };
  const q = window.__qaStats = { mode: 'calm', stopped: false, stop() { q.stopped = true; } };
  let tick = 0;
  const sample = () => {
    const [cpu, mem, disk, rx, tx] = LOADS[q.mode] || LOADS.calm;
    tick += 1;
    return { type: 'stats', cpu: Math.min(100, cpu + (tick % 5) * 2), mem: [mem * GB, 15.6 * GB], disk: [disk, disk * 2.4e5, 84e3 + disk * 1.1e5],
      vol: [41 * GB, 120 * GB], net: [rx, tx] };
  };
  const RealSocket = window.WebSocket;
  window.WebSocket = function (url, protocols) {
    if (!/^\\/hosts\\/\\d+\\/stats$/.test(new URL(url, location.href).pathname)) return new RealSocket(url, protocols);
    const sock = { readyState: 1, onmessage: null, onclose: null, send() {}, close() { clearInterval(timer); sock.readyState = 3; } };
    const emit = () => { if (!q.stopped) sock.onmessage?.({ data: JSON.stringify(sample()) }); };
    const timer = setInterval(emit, 1000);
    setTimeout(emit, 30);
    return sock;
  };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3, prototype: RealSocket.prototype });
  const realFetch = window.fetch;
  window.fetch = async function (input, init) {
    const res = await realFetch.call(this, input, init);
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (!res.ok || !/^\\/chat\\/(threads|sync)$/.test(path)) return res;
    const data = await res.clone().json().catch(() => null);
    if (!data || !Array.isArray(data.threads)) return res;
    data.threads = data.threads.map((row) => ({ ...row, kind: 'project', host_id: 990, project_id: 991, host_alias: 'qa-box', project_name: 'demo' }));
    return new Response(JSON.stringify(data), { status: res.status, headers: { 'Content-Type': 'application/json' } });
  };
})();`;
// --fake dictation: runs in the page before its own scripts. Dev has no speech
// server, so its pages have no mic: the composer gets the one its view's
// template draws when there is one. The microphone is four steady tones in
// the voice band, and the speech server a stub that answers with more words
// the longer the audio is.
const dictationFake = (root, view) => {
  let mic = '';
  try { mic = /{% if chat_config\.speech %}([\s\S]*?){% endif %}/.exec(readFileSync(join(root, 'templates', 'themes', view, '_composer.html'), 'utf8'))?.[1] || ''; } catch { /* no such view */ }
  return `(() => {
  const WORDS = 'Check why the nightly backup on the demo box takes forty minutes now and tell me what changed since last week before you fix anything'.split(' ');
  // Parsed, and no module has run yet.
  document.addEventListener('readystatechange', () => {
    const send = document.querySelector('[data-chat-send]');
    const config = document.getElementById('chat-config');
    if (!send || !config || document.querySelector('[data-chat-mic]')) return;
    send.insertAdjacentHTML('beforebegin', ${JSON.stringify(mic)});
    config.textContent = JSON.stringify({ ...JSON.parse(config.textContent), speech: true });
  }, { once: true });
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => {
    const ctx = new AudioContext();
    const out = ctx.createMediaStreamDestination();
    [180, 420, 950, 2100].forEach((hz, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = hz;
      gain.gain.value = 0.12 / (i + 1);
      osc.connect(gain).connect(out);
      osc.start();
    });
    return out.stream;
  };
  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (path !== '/speech/transcribe') return realFetch.call(this, input, init);
    const seconds = (init?.body?.byteLength || 0) / 32000;
    return Promise.resolve(new Response(JSON.stringify({ text: WORDS.slice(0, Math.ceil(seconds * 2.5)).join(' ') }), { headers: { 'Content-Type': 'application/json' } }));
  };
})();`;
};
let micScript = '';
const fakeKind = (request) => {
  const path = new URL(request.url).pathname.replace(/\/$/, '');
  if (!opts.fake.size || request.method !== 'GET') return '';
  const has = (...names) => names.some((name) => opts.fake.has(name));
  if (has('agents') && /^\/chat\/threads\/[^/]+\/agents$/.test(path)) return 'agents';
  if (has('running', 'ask', 'error') && /^\/chat\/threads\/[^/]+$/.test(path)) return 'thread';
  if (has('ask') && /^\/chat\/threads\/[^/]+\/permissions$/.test(path)) return 'perms';
  return has('running') && /^\/chat\/runs\/[^/]+$/.test(path) ? 'run' : '';
};
// A JSON body, or each JSON line of a snapshot stream.
function mapJson(text, kind) {
  const fix = FIXES[kind];
  try { return JSON.stringify(fix(JSON.parse(text))); } catch { /* not one JSON value */ }
  return text.split('\n').map((line) => {
    try {
      const row = JSON.parse(line);
      if (row && Array.isArray(row.messages)) return JSON.stringify(fix(row));
      if (row && row.thread && typeof row.thread === 'object') return JSON.stringify({ ...row, thread: fix(row.thread) });
    } catch { /* keep the line */ }
    return line;
  }).join('\n');
}
async function rewrite({ requestId, request, responseStatusCode, responseHeaders = [] }) {
  const kind = fakeKind(request);
  const type = responseHeaders.find((header) => header.name.toLowerCase() === 'content-type')?.value || '';
  if (!kind || responseStatusCode !== 200 || /event-stream/.test(type)) return send('Fetch.continueRequest', { requestId });
  const got = await send('Fetch.getResponseBody', { requestId });
  const text = got.base64Encoded ? Buffer.from(got.body, 'base64').toString('utf8') : got.body;
  faked.push(`${kind} ${new URL(request.url).pathname}`);
  return send('Fetch.fulfillRequest', { requestId, responseCode: 200,
    responseHeaders: responseHeaders.filter((header) => !/^content-(length|encoding)$/i.test(header.name)),
    body: Buffer.from(mapJson(text, kind)).toString('base64') });
}

// Every request stops here once the guard is on: a write is aborted (--live
// lets the chat's own through, never an admin page's), a /static file comes
// from --tree, anything else goes through.
function paused(params) {
  const { requestId, request } = params;
  if (params.responseStatusCode !== undefined || params.responseErrorReason) return rewrite(params);
  const url = new URL(request.url);
  if (guarding && !['GET', 'HEAD', 'OPTIONS'].includes(request.method) && !(opts.live && url.pathname.startsWith('/chat'))) {
    blocked.push(`${request.method} ${url.pathname}`);
    return send('Fetch.failRequest', { requestId, errorReason: 'Aborted' });
  }
  if (staticRoot && url.origin === new URL(opts.base).origin && url.pathname.startsWith('/static/')) {
    const file = resolve(staticRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
    const inside = file.startsWith(staticRoot + '/');
    let body = null;
    try { if (inside) body = readFileSync(file); } catch { body = null; }
    return send('Fetch.fulfillRequest', {
      requestId, responseCode: body ? 200 : 404,
      responseHeaders: [{ name: 'Content-Type', value: TYPES[extname(file)] || 'application/octet-stream' }, { name: 'Cache-Control', value: 'no-store' }],
      body: (body || Buffer.from('not in --tree')).toString('base64'),
    });
  }
  // A body that will be rewritten is asked for uncompressed.
  if (fakeKind(request)) {
    const headers = Object.entries(request.headers).filter(([name]) => name.toLowerCase() !== 'accept-encoding').map(([name, value]) => ({ name, value }));
    return send('Fetch.continueRequest', { requestId, headers });
  }
  return send('Fetch.continueRequest', { requestId });
}

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
let viewport = [0, 0];
let cell = '';  // the combination being shot, named in a failure
async function metrics(width, height, scale = zoom) {
  viewport = [width, height];
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: Boolean(opts.touch) });
}

// The first match a user can see: a hidden twin (a closed menu, another
// layout's copy) is never what a step means.
const PICK = `((css) => [...document.querySelectorAll(css)].find((el) => { const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && el.checkVisibility({ visibilityProperty: true }); }) || null)`;
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

// One --shot: the element or the viewport as one cell, or several when it is
// taller than read_file shows unscaled.
async function shoot(css, pad, auto, caption, about, cells) {
  const before = viewport;
  let area;
  let note = '';
  if (css) {
    await boxOf(css, '--shot');
    await grow(tallerBy(css, pad));
    const box = await boxOf(css, '--shot');
    const x = Math.max(0, Math.floor(box[0] - pad));
    const y = Math.max(0, Math.floor(box[1] - pad));
    area = { x, y, width: Math.min(viewport[0] - x, Math.ceil(box[2] + 2 * pad)), height: Math.min(viewport[1] - y, Math.ceil(box[3] + 2 * pad)) };
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
  for (const cell of cells) {
    const row = rows.at(-1);
    if (row && row.width + GAP + cell.width <= MAX - 2 * GAP) {
      row.cells.push(cell);
      row.width += GAP + cell.width;
      row.height = Math.max(row.height, cell.height + LABEL);
    } else rows.push({ cells: [cell], width: cell.width, height: cell.height + LABEL });
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
  staticRoot = '';
  for (const page of found) {
    const width = page.width + 2 * GAP;
    const height = page.height + 2 * GAP;
    const html = page.rows.map((row) => `<div style="display:flex;gap:${GAP}px;align-items:flex-start;margin-bottom:${GAP}px">${row.cells.map((cell) =>
      `<div><div style="height:${LABEL}px;max-width:${cell.width}px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${safe(cell.caption)}</div>`
      + `<img width="${cell.width}" height="${cell.height}" style="display:block" src="data:image/png;base64,${cell.data}"></div>`).join('')}</div>`).join('');
    await metrics(width, height, 1);
    await go('about:blank');
    await run(`document.body.style.cssText = 'margin:0;padding:${GAP}px;background:#3a3f4b;color:#fff;font:13px/${LABEL}px ui-monospace,monospace';
      document.body.innerHTML = ${JSON.stringify(html)}; Promise.all([...document.images].map((img) => img.decode())).then(() => true)`);
    const shot = await send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width, height, scale: 1 } });
    out.push({ png: Buffer.from(shot.data, 'base64'), width, height, what: page.rows.flatMap((row) => row.cells.map((cell) => cell.caption)).join(' | ') });
  }
  return out;
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
  await send('Fetch.enable', { patterns: [{ urlPattern: '*' }, ...(opts.fake.size ? [{ urlPattern: '*/chat/threads/*', requestStage: 'Response' },
    { urlPattern: '*/chat/runs/*', requestStage: 'Response' }] : [])] });
  if (opts.fake.has('stats')) await send('Page.addScriptToEvaluateOnNewDocument', { source: STATS_FAKE });
  // Two trees in one run: the second must not be handed the first one's files.
  if (trees.length > 1) await send('Network.setCacheDisabled', { cacheDisabled: true });
  await metrics(sizes[0].width, sizes[0].height);
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: opts.motion ? 'no-preference' : 'reduce' }] });
  await send('Network.setCookie', { name: 'fuse_chat_view', value: views[0], url: opts.base });

  // Sign-in is rate limited (10 a minute per address, 30 per 15 minutes per
  // account), so the session cookies are kept and reused until they lapse.
  try { await send('Network.setCookies', { cookies: JSON.parse(readFileSync(SESSION, 'utf8')) }); } catch { /* none kept yet */ }
  await go(`${opts.base}/chat`);
  if (!(await run('location.pathname')).startsWith('/chat')) {
    await go(`${opts.base}/login`);
    const signedIn = once('Page.loadEventFired');
    await run(`(() => { const form = document.querySelector('form[action="/login"]');
      form.querySelector('input[name=email]').value = ${JSON.stringify(creds.email)};
      form.querySelector('input[name=password]').value = ${JSON.stringify(creds.password)};
      form.submit(); })()`);
    await signedIn;
    if (!(await run('location.pathname')).startsWith('/chat')) {
      const why = await run(`document.querySelector('[role=alert]')?.textContent.trim() || ''`);
      throw new Error(`sign-in did not reach /chat (at ${await run('location.pathname')})${why ? `: ${why}` : ''}`);
    }
    const { cookies } = await send('Network.getCookies', { urls: [opts.base] });
    writeFileSync(`${SESSION}.${process.pid}`, JSON.stringify(cookies.filter((c) => c.name !== 'fuse_chat_view')), { mode: 0o600 });
    renameSync(`${SESSION}.${process.pid}`, SESSION);
  }
  guarding = true;

  const threads = await run(`fetch('/chat/sync').then((r) => r.json()).then((d) => (d.threads || []).map((t) => [t.title, t.id]))`);
  if (opts.list) {
    console.log(threads.map(([title]) => title).join('\n') || 'no chats: run scripts/dev_qa_account.py');
  } else {
    let targets = pages.map((path) => ({ path, name: path }));
    if (opts.thread) {
      const want = opts.thread.toLowerCase();
      const exact = threads.filter(([title]) => title.toLowerCase() === want);
      const hits = exact.length ? exact : threads.filter(([title]) => title.toLowerCase().includes(want));
      if (hits.length !== 1) {
        throw new Error(`${hits.length ? 'several chats match' : 'no chat matches'} --thread ${JSON.stringify(opts.thread)}: ${(hits.length ? hits : threads).map(([title]) => title).join(', ') || 'this login has none'}`);
      }
      targets = [{ path: `/chat?t=${hits[0][1]}`, name: hits[0][0] }];
    }
    const cells = [];
    const toggles = (scope) => `(() => { const closed = [...document.querySelectorAll('${scope} > .chat-step-main > .chat-step-head > .chat-step-toggle[data-step-toggle][aria-expanded="false"]')]; closed.forEach((b) => b.click()); return closed.length; })()`;
    for (const target of targets) {
      let chat = true;
      for (const view of views) for (const tree of trees) for (const size of sizes) {
        if (!chat && view !== views[0]) continue;
        const label = () => [targets.length > 1 && target.name, views.length > 1 && chat && view, trees.length > 1 && tree.name, sizes.length > 1 && size.text].filter(Boolean).join(' · ');
        cell = label();
        staticRoot = tree.root;
        if (opts.fake.has('dictation')) {
          if (micScript) await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: micScript });
          faked.push('the mic and its speech server');
          micScript = (await send('Page.addScriptToEvaluateOnNewDocument', { source: dictationFake(tree.root ? dirname(tree.root) : ROOT, view) })).identifier;
        }
        await send('Network.setCookie', { name: 'fuse_chat_view', value: view, url: opts.base });
        await metrics(size.width, size.height);
        await go(opts.base + target.path);
        const at = await run('location.pathname');
        if (at.replace(/\/$/, '') !== new URL(opts.base + target.path).pathname.replace(/\/$/, '')) throw new Error(`${target.path} sent the browser to ${at}`);
        const status = await run(`performance.getEntriesByType('navigation')[0]?.responseStatus || 200`);
        if (status >= 400) throw new Error(`${target.path} answered ${status}`);
        chat = await run(`Boolean(document.documentElement.dataset.chatView)`);
        if (chat) await until(`(document.querySelector('[data-chat]')?.dataset.view ?? document.documentElement.dataset.chatView) === ${JSON.stringify(view)}`, `the ${view} view`);
        else if (views.length > 1 && tree === trees[0] && size === sizes[0]) console.log(`${target.path} is not drawn by a chat view: shot once`);
        if (opts.thread) await until(`Boolean(document.querySelector('[data-chat-log] .chat-turn'))`, 'the transcript', 30000);
        await run('document.fonts.ready.then(() => true)');
        // The account's own notices ("No models are unlocked yet") are not what
        // is under test; --toasts keeps them.
        if (!opts.toasts) await run(`document.head.append(Object.assign(document.createElement('style'), { textContent: '.fuse-toasts { display: none !important; }' }))`);
        await sleep(300);

        cell = label();
        const combo = cell;
        const about = [target.name, chat && view, size.text, opts.touch && 'touch', tree.root && `static from ${tree.root}`].filter(Boolean).join('  ');
        let shot = 0;
        // Every step runs in the order given, so a script can read what a hover
        // changed and rows can be opened after a live reply arrives.
        for (const [kind, arg, room = pad] of opts.steps) {
          if (kind === 'open') {
            await run(toggles('.chat-step.is-console-group'));
            await sleep(250);
            if (arg === 'rows') await run(toggles('.chat-step:not(.is-console-group)'));
          } else if (kind === 'eval') {
            const value = await run(arg);
            if (value !== undefined) console.log(`${combo ? `[${combo}] ` : ''}${typeof value === 'string' ? value : JSON.stringify(value)}`);
          } else if (kind === 'find') {
            const found = await run(`(${findInPage})(${JSON.stringify(arg)})`);
            console.log(`${combo ? `[${combo}]\n` : ''}${found || `nothing visible holds "${arg}" in its text, label or name`}`);
          } else if (kind === 'shot') {
            shot += 1;
            const which = shots > 1 ? `#${shot} ${arg || 'viewport'}` : arg;
            await shoot(arg, room, size.auto, [combo, shots > 1 || !combo ? which : ''].filter(Boolean).join(' · ') || about, [about, which].filter(Boolean).join('  '), cells);
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
            const at = { x: box[0] + (kind === 'hover' ? Math.min(box[2] / 2, 60) : box[2] / 2), y: box[1] + box[3] / 2 };
            // The pointer goes where a user's would: onto whatever lies on top.
            const cover = await run(`(() => { const el = ${PICK}(${JSON.stringify(arg)}); const hit = document.elementFromPoint(${at.x}, ${at.y});
              return !el || !hit || el.contains(hit) || hit.contains(el) ? '' : hit.tagName.toLowerCase() + [...hit.classList].map((name) => '.' + name).join(''); })()`);
            if (cover) throw new Error(`--${kind} ${arg}: it is covered by ${cover}, which the pointer would land on instead`);
            await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...at });
            if (kind === 'click') {
              await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...at, button: 'left', clickCount: 1 });
              await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...at, button: 'left', clickCount: 1 });
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
  if (opts.fake.size) console.log(`faked (${[...opts.fake].join(', ')}): ${[...new Set(faked)].join(', ') || 'nothing the page asked for matched'}`);
  if (blocked.length) console.log(`writes aborted (${blocked.length}): ${[...new Set(blocked)].slice(0, 6).join(', ')}`);
  // --list prints only titles (one per line, for scripts) unless the page failed.
  if (!opts.list || pageErrors.length) console.log(pageErrors.length ? `page errors (${pageErrors.length}):\n  ${pageErrors.slice(0, 8).join('\n  ')}` : 'page errors: none');
} catch (error) {
  console.error(`shot failed: ${cell ? `[${cell}] ` : ''}${error.message}`);
  process.exitCode = 1;
} finally {
  await cleanup();
  ws?.close();
}

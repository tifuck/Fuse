// Fuse hooks for the qa-shot core (app/bundled_skills/qa-shot/scripts/shot.mjs):
// the QA login, the chat views, the QA chats, --tree and the --fake live
// states. scripts/qa/shot.mjs runs the core with these; its header is the help.
import { existsSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const THEMES = join(ROOT, 'static', 'themes');
const VIEWS = readdirSync(THEMES).filter((id) => existsSync(join(THEMES, id, 'theme.json')));
const FAKES = ['running', 'ask', 'asks', 'skill', 'env', 'secrets', 'error', 'agents', 'stats', 'dictation', 'pool', 'terminal'];
const SESSION = join(ROOT, 'data', 'dev-qa-session.json');

// --fake: states no fixture can hold, made by rewriting what the page reads
// (the chat, its run, its permission asks). The server is never told.
const startedAt = Date.now();
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d+Z$/, 'Z');
const ASK = {
  id: 'qa-perm-1', kind: 'tool_permission', status: 'pending', perm_kind: 'shell', tool: 'run_terminal_command', host: 'qa-box', path: '',
  cwd: '/srv/demo', command: 'rm -rf build && make release VERSION=2.4.0', secret: false, requester: 'Main agent', decision: '',
  grant_scope: '', grant_kind: '', grant_value: '', description: 'Deletes the build directory, then builds and tags the release.',
};
// Detected values a turn waits on (app/secret_guard.py). Invented, and built
// in pieces: a literal shaped like a real key is rejected when pushed.
const rnd = (seed, n) => Array.from({ length: n }, (_, i) => 'Qx7Lm2Vn9Rt4Wp8Zk3Jh6Fd1Sb5Yc0Ga'[(i * seed + seed) % 32]).join('');
const SECRET_OFFER = [
  { key: 'OPENAI_API_KEY', value: 'sk-' + 'proj-' + rnd(3, 48), kind: 'OpenAI key', file: '.env', line: 3 },
  { key: 'DATABASE_URL', value: 'Tr0ub4dor' + '&3xq', kind: 'Password in a URL', file: '.env', line: 7 },
  { key: 'SESSION_SECRET', value: rnd(5, 64).toLowerCase(), kind: 'Secret', file: '.env', line: 9 },
  { key: 'RATELIMIT_BYPASS_KEY', value: 'nightly_suite_bypass_2031', kind: 'Secret', file: '.env', line: 10, plain: true },
  { key: 'STRIPE_SECRET_KEY', value: 'sk_' + 'live_' + rnd(7, 24), kind: 'Stripe key', file: 'apps/api/.env.production', line: 12 },
  { key: '', value: 'npm_' + rnd(9, 36), kind: 'npm token', file: '.npmrc', line: 1 },
  { key: 'GITHUB_TOKEN', value: 'ghp_' + rnd(11, 36), kind: 'GitHub token', file: '.envrc', line: 4 },
  { key: 'JWT_SIGNING_KEY', value: ['eyJ' + rnd(13, 33), 'eyJ' + rnd(15, 120), rnd(17, 43)].join('.'), kind: 'JWT', file: 'config/prod.env', line: 2 },
].map((row, i) => ({ id: i + 1, host: 'qa-box', ...row }));
const MORE_ASKS = [
  { ...ASK, id: 'qa-perm-2', perm_kind: 'file', tool: 'write_file', path: '/srv/demo/notes/findings.md', cwd: '', command: '', requester: 'researcher',
    grant_kind: 'file', description: '', reasons: ['outside allowed folders'] },
  { ...ASK, id: 'qa-perm-3', perm_kind: 'file', tool: 'read_file', path: '/etc/nginx/sites-enabled/fuse.conf', cwd: '', command: '',
    grant_kind: 'file', description: '', reasons: ['outside allowed folders'] },
];
// A skill's script files copied to a machine (permissions.review_skill): one
// made by Fuse, one from GitHub.
const SKILL_ASKS = [
  { ...ASK, id: 'qa-perm-skill', perm_kind: 'skill', tool: 'load_skill', path: '/tmp/fuse/skills/qa-shot-3f2a9c1e', cwd: '',
    command: 'qa-shot', description: '1 file' },
  { ...ASK, id: 'qa-perm-skill-gh', perm_kind: 'skill', tool: 'load_skill', path: '/tmp/fuse/skills/pdf-tools-91c0d2aa', cwd: '',
    command: 'pdf-tools', description: '3 files\nFrom github.com/acme/agent-skills · not made by Fuse' },
];
// A command that gets an environment secret (permissions.review_env).
const ENV_ASK = { ...ASK, id: 'qa-perm-env', perm_kind: 'env', path: 'HCLOUD_TOKEN',
  command: 'python3 /tmp/fuse/skills/hetzner-cloud-1a2b3c4d/scripts/hcloud.py list', grant_kind: 'env', grant_value: 'HCLOUD_TOKEN',
  description: 'List the Hetzner servers' };
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
// --fake terminal: runs in the page (before its scripts; it also works later,
// as --eval, since the page asks only when the terminal opens). The machine
// list, the session list and /terminal/ws answer in the page: a canned shell
// with a prompt, colour samples and box drawing, that echoes what is typed,
// draws a full-screen frame for "top" (q leaves it) and redraws it on resize.
// window.__qaTerm: push(text) writes to the open terminals, frame() draws the
// full-screen frame, close(code) ends their sockets (4410 sends an exit
// first), and sessions, sent, acked and size say what the page did.
function terminalFake() {
  const E = '\x1b';
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const q = window.__qaTerm = { sessions: [], sockets: new Set(), sent: [], acked: 0, size: null, full: false };
  const HOSTS = [
    { id: 990, alias: 'qa-box', os: 'linux', transport: 'agent', online: true, shells: [{ id: 'login', label: 'Login shell' }], default_shell: 'login' },
    { id: 992, alias: 'win-box', os: 'windows', transport: 'agent', online: true, default_shell: 'powershell',
      shells: [{ id: 'powershell', label: 'PowerShell' }, { id: 'cmd', label: 'Command Prompt' }, { id: 'pwsh', label: 'PowerShell 7' }] },
    { id: 993, alias: 'old-vps', os: 'linux', transport: 'ssh', online: false, shells: [{ id: 'login', label: 'Login shell' }], default_shell: 'login' },
  ];
  let seq = 0;
  const prompt = (s) => s.os === 'windows' ? `PS C:\\Users\\qa> ` : `${E}[1;32m${s.user}@${s.alias}${E}[0m:${E}[1;34m${s.cwd}${E}[0m$ `;
  const sgr = (code, text) => `${E}[${code}m${text}${E}[0m`;
  function banner(s) {
    const out = [`${E}]0;${s.user}@${s.alias}: ${s.cwd}\x07`, `Fuse QA shell on ${s.alias} (made up: nothing runs)\r\n\r\n`];
    out.push('16 colours  ' + [30, 31, 32, 33, 34, 35, 36, 37].map((c) => sgr(c, '██')).join('') + '  ' + [90, 91, 92, 93, 94, 95, 96, 97].map((c) => sgr(c, '██')).join('') + '\r\n');
    out.push('            ' + sgr(1, 'bold') + ' ' + sgr(2, 'dim') + ' ' + sgr(3, 'italic') + ' ' + sgr(4, 'underline') + ' ' + sgr(7, 'inverse') + ' ' + sgr('1;31', 'error') + ' ' + sgr('1;32', 'ok') + '\r\n');
    for (let row = 0; row < 6; row += 1) {
      let line = row === 0 ? '256 colours ' : '            ';
      for (let i = 0; i < 36; i += 1) line += `${E}[48;5;${16 + row * 36 + i}m `;
      out.push(line + `${E}[0m\r\n`);
    }
    let grey = '            ';
    for (let i = 232; i < 256; i += 1) grey += `${E}[48;5;${i}m  `;
    out.push(grey + `${E}[0m\r\n`);
    let ramp = 'truecolor   ';
    for (let i = 0; i < 72; i += 1) {
      const t = i / 71;
      ramp += `${E}[48;2;${Math.round(255 * Math.max(0, 1 - 2 * t))};${Math.round(255 * (1 - Math.abs(2 * t - 1)))};${Math.round(255 * Math.max(0, 2 * t - 1))}m `;
    }
    out.push(ramp + `${E}[0m\r\n\r\n`);
    out.push('┌─ box ──────┬────────┐  ╔═══════╗  ╭──────╮  ▁▂▃▄▅▆▇█ ░▒▓\r\n');
    out.push('│ ready      │ 12 ms  │  ║ 中文字 ║  │ ✓ ok │  ⣿⣷⣯⣟ ← ↑ → ↓\r\n');
    out.push('└────────────┴────────┘  ╚═══════╝  ╰──────╯  https://example.com/docs\r\n\r\n');
    out.push(prompt(s));
    return out.join('');
  }
  function frame(cols, rows) {
    const w = Math.max(20, cols);
    const h = Math.max(6, rows);
    const lines = [`${E}[?1049h${E}[H${E}[2J${E}[44;97m` + ' top - 12:00:01 up 3 days, load average: 0.42 0.38 0.31'.padEnd(w).slice(0, w) + `${E}[0m`];
    const bar = (label, pct, tone) => {
      const width = Math.max(4, w - 16);
      const fill = Math.round((width * pct) / 100);
      return ` ${label.padEnd(4)}[${E}[${tone}m${'|'.repeat(fill)}${E}[0m${' '.repeat(width - fill)}] ${String(pct).padStart(3)}%`;
    };
    lines.push(bar('cpu', 37, 32), bar('mem', 64, 33), bar('swp', 3, 31), '');
    lines.push(`${E}[7m` + '  PID USER      %CPU %MEM COMMAND'.padEnd(w).slice(0, w) + `${E}[0m`);
    const procs = [['1182', 'root', '12.0', '1.4', 'uvicorn app.main:app'], ['2210', 'qa', '4.1', '0.6', 'node build.mjs'], ['77', 'root', '0.3', '0.1', 'systemd-journald']];
    procs.forEach((p) => lines.push(` ${p[0].padStart(4)} ${p[1].padEnd(8)} ${p[2].padStart(5)} ${p[3].padStart(4)} ${p[4]}`.slice(0, w)));
    while (lines.length < h - 1) lines.push('');
    lines.push(`${E}[7m q ${E}[0m Quit  ${E}[7m F6 ${E}[0m Sort  ${E}[7m / ${E}[0m Find   ${w}x${h}`);
    return lines.slice(0, h).join('\r\n');
  }
  const bin = (text) => enc.encode(text).buffer;
  const RealSocket = window.WebSocket;
  window.WebSocket = function (url, protocols) {
    if (new URL(url, location.href).pathname !== '/terminal/ws') return new RealSocket(url, protocols);
    let session = null;
    let line = '';
    const sock = {
      readyState: 0, binaryType: 'blob', onopen: null, onmessage: null, onclose: null, onerror: null,
      send(data) {
        if (typeof data === 'string') {
          const msg = JSON.parse(data);
          if (msg.t === 'attach') {
            session = q.sessions.find((s) => s.id === msg.id);
            if (!session) { sock.close(4404); return; }
            q.size = [msg.cols, msg.rows];
            emit(JSON.stringify({ t: 'ready', ...session }));
            emit(bin(banner(session)));
          } else if (msg.t === 'resize') {
            q.size = [msg.cols, msg.rows];
            if (q.full) emit(bin(frame(msg.cols, msg.rows)));
          } else if (msg.t === 'ack') q.acked += msg.n;
          else if (msg.t === 'close') sock.close(4410);
          return;
        }
        const text = dec.decode(data);
        q.sent.push(text);
        if (q.full) {
          if (text === 'q') { q.full = false; emit(bin(`${E}[?1049l`)); }
          return;
        }
        let echo = '';
        for (const ch of text) {
          if (ch === '\r') {
            const cmd = line.trim();
            line = '';
            if (cmd === 'top') { q.full = true; echo += frame(...(q.size || [80, 24])); continue; }
            echo += '\r\n' + (cmd ? `${cmd.split(' ')[0]}: made-up shell, nothing ran\r\n` : '') + prompt(session);
          } else if (ch === '\x7f') {
            if (line) { line = line.slice(0, -1); echo += '\b \b'; }
          } else if (ch === '\x03') { line = ''; echo += '^C\r\n' + prompt(session); }
          else if (ch >= ' ') { line += ch; echo += ch; }
        }
        if (echo) emit(bin(echo));
      },
      close(code = 1000) {
        if (sock.readyState === 3) return;
        sock.readyState = 3;
        q.sockets.delete(sock);
        setTimeout(() => sock.onclose?.({ code, reason: '', wasClean: true }), 0);
      },
    };
    const emit = (data) => setTimeout(() => { if (sock.readyState === 1) sock.onmessage?.({ data }); }, 0);
    sock.push = (text) => emit(bin(text));
    sock.frame = () => { q.full = true; emit(bin(frame(...(q.size || [80, 24])))); };
    sock.end = (code) => {
      if (code === 4410) emit(JSON.stringify({ t: 'exit', code: 0, reason: 'exit' }));
      setTimeout(() => sock.close(code), 10);
    };
    q.sockets.add(sock);
    setTimeout(() => { sock.readyState = 1; sock.onopen?.({}); }, 20);
    return sock;
  };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3, prototype: RealSocket.prototype });
  q.push = (text) => q.sockets.forEach((s) => s.push(text));
  q.frame = () => q.sockets.forEach((s) => s.frame());
  q.close = (code = 1006) => [...q.sockets].forEach((s) => s.end(code));
  const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    const method = (init?.method || 'GET').toUpperCase();
    if (path === '/terminal/hosts') return json({ hosts: HOSTS });
    if (path === '/terminal/sessions' && method === 'GET') return json({ sessions: q.sessions.map((s) => ({ ...s, attached: false, alive: true })) });
    if (path === '/terminal/sessions' && method === 'POST') {
      const body = JSON.parse(init.body || '{}');
      const host = HOSTS.find((h) => h.id === body.host_id) || HOSTS[0];
      const windows = host.os === 'windows';
      seq += 1;
      const s = { id: `qa-term-${seq}`, host_id: host.id, alias: host.alias, os: host.os, transport: host.transport, user: windows ? 'qa' : 'root', root: !windows,
        cwd: body.thread_id ? '/srv/demo' : windows ? 'C:\\Users\\qa' : '/root', shell: body.shell || host.default_shell, title: '', created: new Date().toISOString() };
      q.sessions.push(s);
      return json(s);
    }
    const kill = /^\/terminal\/sessions\/(.+)$/.exec(path);
    if (kill && method === 'DELETE') {
      q.sessions = q.sessions.filter((s) => s.id !== decodeURIComponent(kill[1]));
      return json({ ok: true });
    }
    return realFetch.call(this, input, init);
  };
}
export const TERMINAL_FAKE = `(${terminalFake})();`;
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

let opts = {};
let fake = new Set();
let trace = null;
let micScript = '';
const faked = [];

const FIXES = {
  agents(data) {
    return { ...data, agents: AGENTS, live_count: AGENTS.filter((row) => row.status === 'running').length };
  },
  thread(data) {
    const last = [...(data.messages || [])].reverse().find((message) => message.role === 'assistant');
    if (fake.has('running') && last) {
      if (trace) {
        const running = new Set((last.tools || []).filter((one) => one.status === 'running').map((one) => one.id));
        last.tools = (last.tools || []).filter((one) => !running.has(one.id));
        last.segments = (last.segments || []).filter((part) => !(part.type === 'tool' && running.has(part.id)));
        const first = 'qa-trace-0';
        trace.forEach((fields, i) => {
          const one = { id: `qa-trace-${i}`, status: 'running', host: 'qa-box', ...fields };
          if (fields.parent === true) one.parent = first;
          else last.segments.push({ type: 'tool', id: one.id });
          last.tools.push(one);
        });
      }
      (last.tools || []).forEach((one) => { if (one.status === 'running') one.started_ms = startedAt - 42000; });
      data.run = { ...(data.run || {}), id: data.run?.id || 'qa-live', status: 'running', message_id: last.id, message: last,
        created_at: iso(startedAt - 75000), updated_at: iso(startedAt), cancel_requested: false };
    }
    // An ask only waits inside a run, and the page reads asks only for a thread that is running.
    if (fake.has('ask')) Object.assign(data, { bypass_permissions: false, auto_permissions: false, running: true });
    // The page asks for an offer only for a chat whose turn is running.
    if (fake.has('secrets')) data.running = true;
    if (fake.has('error') && last) last.error = 'Upstream returned 502 Bad Gateway after 3 retries (provider: qa/fixture-model)';
    // The badge app/sources/pool_badge.py would send for a shared Claude account.
    if (fake.has('pool')) {
      const now = Math.round(startedAt / 1000);
      data.compaction = { ...(data.compaction || {}), pool: { model: data.model, provider: 'claude', pool: 'Claude', plan: 'Max 20x',
        id: 902, name: 'Claude plan 2', own: false,
        windows: [{ label: '5-hour', short: '5h', percent: 42, reset_at: now + 7800 }, { label: 'Weekly', short: '7d', percent: 18, reset_at: now + 302400 }],
        limits: [{ label: 'Fable weekly', short: '7d', percent: 61, reset_at: now + 302400 }],
        accounts: [
          { id: 901, name: 'Claude plan 1', own: false, plan: 'Max 20x', percent: 100, status: 'limit', until: now + 4800, primary: false },
          { id: 902, name: 'Claude plan 2', own: false, plan: 'Max 20x', percent: 42, status: 'current', until: null, primary: false },
          { id: 903, name: 'qa.tester@example.invalid', own: true, plan: 'Max 5x', percent: 6, status: 'ready', until: null, primary: true },
          { id: 904, name: 'Claude plan 4', own: false, plan: 'Pro', percent: 81, status: 'ready', until: null, primary: false },
          { id: 905, name: 'qa.spare.account@example.invalid', own: true, plan: 'Pro', percent: null, status: 'off', until: null, primary: false },
        ],
        ready: 3, size: 4, hop: { from: 'Claude plan 1', reason: 'limit' },
        prefs: { can_override: true, open_access: false, share: 'contributors', use: 'pool', saved_share: 'contributors', saved_use: 'pool',
          contributor: true, skip: [905], primary: 903 } } };
    }
    return data;
  },
  run(data) {
    if (!fake.has('running')) return data;
    return { ...data, status: 'running', created_at: iso(startedAt - 47000), updated_at: iso(startedAt), cancel_requested: false,
      compaction: { ...(data.compaction || {}), meter: { used: 18400, limit: 128000, run_id: data.id } } };
  },
  secrets() {
    return { pending: true, items: SECRET_OFFER };
  },
  perms(data) {
    if (!fake.has('ask')) return data;
    const pending = [];
    if (fake.has('skill')) pending.push(...SKILL_ASKS.map((ask, i) => ({ ...ask, expires_at: iso(startedAt + 240000 - i * 60000) })));
    if (fake.has('env')) pending.push({ ...ENV_ASK, expires_at: iso(startedAt + 240000) });
    if (!(fake.has('skill') || fake.has('env')) || fake.has('asks')) pending.push({ ...ASK, expires_at: iso(startedAt + 240000) });
    if (fake.has('asks')) pending.push(...MORE_ASKS.map((ask, i) => ({ ...ask, expires_at: iso(startedAt + 180000 - i * 60000) })));
    return { ...data, bypass: false, auto: false, pending };
  },
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
// Opens the folded work lines of a chat (and, for rows, every call row too).
const toggles = (scope) => `(() => { const closed = [...document.querySelectorAll('${scope} > .chat-step-main > .chat-step-head > .chat-step-toggle[data-step-toggle][aria-expanded="false"]')]; closed.forEach((b) => b.click()); return closed.length; })()`;
// Tail folds a whole burst of calls behind one line; open those first.
const bursts = `(() => { const closed = [...document.querySelectorAll('button[data-activity-summary][aria-expanded="false"]')]; closed.forEach((b) => b.click()); return closed.length; })()`;
async function openSteps(api, rows) {
  await api.run(bursts);
  await api.run(toggles('.chat-step.is-console-group'));
  await api.sleep(250);
  if (rows) await api.run(toggles('.chat-step:not(.is-console-group)'));
}

export default {
  help: join(ROOT, 'scripts', 'qa', 'shot.mjs'),
  base: 'https://127.0.0.1:8088',
  outDir: '/tmp/fuse-qa',
  values: ['thread', 'view', 'tree', 'fake', 'trace'],
  flags: ['list', 'toasts', 'live'],
  repeat: ['fake'],
  steps: {
    'open-groups': (api) => openSteps(api, false),
    'open-rows': (api) => openSteps(api, true),
  },

  configure(api) {
    ({ opts } = api);
    const { bad, list } = api;
    if (opts.static) bad('--static: use --tree DIR for a checkout');
    if (opts.page === undefined) api.pages.splice(0, api.pages.length, '/chat');
    fake = new Set(list(opts.fake || ''));
    fake.forEach((name) => FAKES.includes(name) || bad(`--fake ${name}: use ${FAKES.join(', ')}`));
    if (fake.has('asks') || fake.has('skill') || fake.has('env')) fake.add('ask');
    if (opts.trace !== undefined) {
      if (!fake.has('running')) bad('--trace needs --fake running');
      try { trace = [JSON.parse(opts.trace)].flat(); } catch (error) { bad(`--trace: ${error.message}`); }
    }
    const views = opts.view === 'all' ? VIEWS : list(opts.view || 'desktop');
    views.forEach((view) => VIEWS.includes(view) || bad(`--view ${view}: use ${VIEWS.join(', ')} or all`));
    api.variants = views.map((name) => ({ name }));
    api.statics = list(opts.tree || 'dev').map((dir) => {
      if (dir === 'dev') return { name: 'dev', label: '', prefix: '/static/', root: '' };
      if (!existsSync(resolve(dir, 'static'))) bad(`--tree ${dir} has no static/ directory`);
      const root = resolve(dir, 'static');
      return { name: basename(resolve(dir)), label: root, prefix: '/static/', root };
    });
    if (opts.thread && opts.page) bad('--thread opens a chat and --page another page: use one of them');
    api.listOnly = Boolean(opts.list);
  },

  responsePatterns() {
    return fake.size ? [{ urlPattern: '*/chat/threads/*', requestStage: 'Response' }, { urlPattern: '*/chat/runs/*', requestStage: 'Response' }] : [];
  },

  responseKind(request) {
    const path = new URL(request.url).pathname.replace(/\/$/, '');
    if (!fake.size || request.method !== 'GET') return '';
    const has = (...names) => names.some((name) => fake.has(name));
    if (has('agents') && /^\/chat\/threads\/[^/]+\/agents$/.test(path)) return 'agents';
    if (has('secrets') && /^\/chat\/threads\/[^/]+\/secrets\/offer$/.test(path)) return 'secrets';
    if (has('running', 'ask', 'secrets', 'error', 'pool') && /^\/chat\/threads\/[^/]+$/.test(path)) return 'thread';
    if (has('ask') && /^\/chat\/threads\/[^/]+\/permissions$/.test(path)) return 'perms';
    return has('running') && /^\/chat\/runs\/[^/]+$/.test(path) ? 'run' : '';
  },

  rewriteBody(kind, text, request) {
    faked.push(`${kind} ${new URL(request.url).pathname}`);
    return mapJson(text, kind);
  },

  // --live lets the chat's own writes through, never an admin page's.
  allowWrite(url) {
    return Boolean(opts.live) && url.pathname.startsWith('/chat');
  },

  async start(api) {
    if (fake.has('stats')) await api.send('Page.addScriptToEvaluateOnNewDocument', { source: STATS_FAKE });
    if (fake.has('terminal')) await api.send('Page.addScriptToEvaluateOnNewDocument', { source: TERMINAL_FAKE });
  },

  async signIn(api) {
    const { send, run, go, once } = api;
    const creds = JSON.parse(readFileSync(join(ROOT, 'data', 'dev-qa-login.json'), 'utf8'));
    await send('Network.setCookie', { name: 'fuse_chat_view', value: api.variants[0].name, url: opts.base });
    // Sign-in is rate limited (10 a minute per address, 30 per 15 minutes per
    // account), so the session cookies are kept and reused until they lapse.
    try { await send('Network.setCookies', { cookies: JSON.parse(readFileSync(SESSION, 'utf8')) }); } catch { /* none kept yet */ }
    await go(`${opts.base}/chat`);
    if ((await run('location.pathname')).startsWith('/chat')) return;
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
  },

  async targets(api) {
    const threads = await api.run(`fetch('/chat/sync').then((r) => r.json()).then((d) => (d.threads || []).map((t) => [t.title, t.id]))`);
    if (opts.list) {
      console.log(threads.map(([title]) => title).join('\n') || 'no chats: run scripts/qa/dev_qa_account.py');
      return null;
    }
    if (!opts.thread) return api.pages.map((path) => ({ path, name: path }));
    const want = opts.thread.toLowerCase();
    const exact = threads.filter(([title]) => title.toLowerCase() === want);
    const hits = exact.length ? exact : threads.filter(([title]) => title.toLowerCase().includes(want));
    if (hits.length !== 1) {
      throw new Error(`${hits.length ? 'several chats match' : 'no chat matches'} --thread ${JSON.stringify(opts.thread)}: ${(hits.length ? hits : threads).map(([title]) => title).join(', ') || 'this login has none'}`);
    }
    return [{ path: `/chat?t=${hits[0][1]}`, name: hits[0][0] }];
  },

  async prepare(api, { variant, static: tree }) {
    if (fake.has('dictation')) {
      if (micScript) await api.send('Page.removeScriptToEvaluateOnNewDocument', { identifier: micScript });
      faked.push('the mic and its speech server');
      micScript = (await api.send('Page.addScriptToEvaluateOnNewDocument', { source: dictationFake(tree.root ? dirname(tree.root) : ROOT, variant.name) })).identifier;
    }
    await api.send('Network.setCookie', { name: 'fuse_chat_view', value: variant.name, url: opts.base });
  },

  async ready(api, { target, variant, first }) {
    const chat = await api.run(`Boolean(document.documentElement.dataset.chatView)`);
    if (chat) await api.until(`(document.querySelector('[data-chat]')?.dataset.view ?? document.documentElement.dataset.chatView) === ${JSON.stringify(variant.name)}`, `the ${variant.name} view`);
    else if (api.variants.length > 1 && first) console.log(`${target.path} is not drawn by a chat view: shot once`);
    if (opts.thread) await api.until(`Boolean(document.querySelector('[data-chat-log] .chat-turn'))`, 'the transcript', 30000);
    // The account's own notices ("No models are unlocked yet") are not what
    // is under test; --toasts keeps them.
    if (!opts.toasts) await api.run(`document.head.append(Object.assign(document.createElement('style'), { textContent: '.fuse-toasts { display: none !important; }' }))`);
    return chat;
  },

  report() {
    return fake.size ? [`faked (${[...fake].join(', ')}): ${[...new Set(faked)].join(', ') || 'nothing the page asked for matched'}`] : [];
  },
};

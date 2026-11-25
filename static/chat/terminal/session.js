// One terminal session: its xterm screen and its socket (/terminal/ws).
// The socket reattaches after a drop with backoff and replays the server's
// scrollback; it stays open while the page is hidden, since the shell keeps
// running anyway. The manager (../terminal.js) owns tabs, keys and layout.
import { chunkBinary, chunkText, closeOutcome, createAcker, retryDelay } from './core.js';

export function createSession(info, deps) {
  const { xt, pane, onChange, transform, keyHandler, openLink } = deps;
  const screen = pane.querySelector("[data-term-screen]");
  const term = new xt.Terminal({
    allowProposedApi: true,
    cursorBlink: true,
    fontFamily: deps.font.family,
    fontSize: deps.font.size,
    lineHeight: 1.12,
    minimumContrastRatio: 4.5,
    scrollback: 5000,
    theme: deps.palette,
    macOptionIsMeta: true,
    rightClickSelectsWord: false,
    linkHandler: { activate: (_event, uri) => openLink(uri), allowNonHttpProtocols: false },
  });
  const fit = new xt.FitAddon();
  const search = new xt.SearchAddon();
  term.loadAddon(fit);
  term.loadAddon(search);
  term.loadAddon(new xt.Unicode11Addon());
  term.unicode.activeVersion = "11";
  term.loadAddon(new xt.WebLinksAddon((_event, uri) => openLink(uri)));
  // OSC 52: a program may set the clipboard, never read it. The provider
  // answers reads with nothing, and the handler below (asked first) swallows
  // the read request so no reply goes out at all. A write asks first: output
  // (a `cat` of any file) could otherwise swap the clipboard for a command.
  term.loadAddon(new xt.ClipboardAddon(undefined, {
    readText: () => "",
    writeText: async (_selection, text) => {
      const preview = text.length > 200 ? text.slice(0, 200) + "…" : text;
      if (!text || !(await window.fuseConfirm(`A program in the terminal wants to copy this to your clipboard:\n\n${preview}`, { confirmLabel: "Copy" }))) return;
      navigator.clipboard?.writeText(text).catch(() => {});
    },
  }));
  term.parser.registerOscHandler(52, (data) => data.split(";")[1] === "?");
  term.open(screen);
  const area = term.textarea;
  if (area) {
    for (const [key, value] of Object.entries({ autocapitalize: "off", autocorrect: "off", autocomplete: "off", spellcheck: "false", enterkeyhint: "send" })) area.setAttribute(key, value);
    area.setAttribute("aria-label", "Terminal input");
  }
  // WebGL draws box and block glyphs itself; a lost context falls back to the DOM renderer.
  try {
    const gl = new xt.WebglAddon();
    gl.onContextLoss(() => gl.dispose());
    term.loadAddon(gl);
  } catch (_) { /* no WebGL: the DOM renderer stays */ }
  term.attachCustomKeyEventHandler((event) => keyHandler(event, session));

  const s = {
    state: "connecting",
    note: "",
    exit: null,
    title: "",
  };
  let ws = null;
  // Bumped per socket: xterm's write callbacks for an old socket's bytes ack nothing.
  let generation = 0;
  let attempt = 0;
  let retry = 0;
  let ended = false;
  let fitFrame = 0;

  const send = (msg) => { if (ws?.readyState === 1) ws.send(JSON.stringify(msg)); };
  const acker = createAcker(send);

  function set(state, note = "") {
    s.state = state;
    s.note = note;
    onChange(session);
  }

  function sendBytes(chunks) {
    if (ws?.readyState !== 1) return;
    for (const chunk of chunks) ws.send(chunk);
  }

  function connect() {
    if (ws || ended) return;
    const url = new URL("/terminal/ws", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const sock = new WebSocket(url);
    sock.binaryType = "arraybuffer";
    ws = sock;
    const gen = ++generation;
    acker.reset();
    sock.onopen = () => {
      if (ws === sock) sock.send(JSON.stringify({ t: "attach", id: session.id, cols: term.cols, rows: term.rows }));
    };
    sock.onmessage = (event) => {
      if (ws !== sock) return;
      if (typeof event.data !== "string") {
        const bytes = new Uint8Array(event.data);
        term.write(bytes, () => { if (gen === generation) acker.written(bytes.length); });
        return;
      }
      let msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }
      if (msg?.t === "ready") {
        attempt = 0;
        Object.assign(session.info, msg);
        delete session.info.t;
        // The replay that follows is the whole scrollback: start clean.
        term.reset();
        set("live");
        if (term.cols && term.rows) send({ t: "resize", cols: term.cols, rows: term.rows });
      } else if (msg?.t === "exit") {
        s.exit = { code: msg.code ?? null, reason: String(msg.reason || "") };
        ended = true;
        set("exited", exitNote(s.exit));
      } else if (msg?.t === "error") {
        set(s.state, String(msg.detail || "Terminal error"));
      }
    };
    sock.onclose = (event) => {
      if (ws !== sock) return;
      ws = null;
      acker.reset();
      if (ended) return;
      const outcome = closeOutcome(event.code);
      if (!outcome.retry) {
        ended = true;
        set(event.code === 4410 ? "exited" : "lost", event.code === 4410 && s.exit ? exitNote(s.exit) : outcome.reason);
        return;
      }
      attempt += 1;
      set("connecting", "Reconnecting…");
      window.clearTimeout(retry);
      retry = window.setTimeout(connect, retryDelay(attempt));
    };
  }

  term.onData((data) => {
    if (s.state !== "live") return;
    sendBytes(chunkText(transform(data)));
  });
  term.onBinary((data) => { if (s.state === "live") sendBytes(chunkBinary(data)); });
  term.onResize(({ cols, rows }) => send({ t: "resize", cols, rows }));
  term.onTitleChange((title) => {
    s.title = String(title || "").slice(0, 120);
    onChange(session);
  });

  // Fits the screen to its box once it has one (a hidden tab or a closed dock has none).
  function refit() {
    if (fitFrame) return;
    fitFrame = window.requestAnimationFrame(() => {
      fitFrame = 0;
      if (!screen.clientWidth || !screen.clientHeight) return;
      try { fit.fit(); } catch (_) { /* not measurable yet */ }
    });
  }
  const observer = new ResizeObserver(refit);
  observer.observe(screen);

  // A multi-line paste waits for a yes before anything reaches the shell.
  screen.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text/plain") || "";
    if (!deps.pasteCheck(text)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    session.paste(text);
  }, true);

  const session = {
    id: info.id,
    info,
    term,
    pane,
    search,
    get state() { return s.state; },
    get note() { return s.note; },
    get title() { return s.title; },
    get exit() { return s.exit; },
    connect,
    // Takes a session back from another tab (4409) or after the server lost it.
    reconnect() {
      if (s.state === "exited") return;
      ended = false;
      attempt = 0;
      set("connecting", "");
      connect();
    },
    refit,
    focus: () => term.focus(),
    // Text from the key bar, the line field or a confirmed paste.
    input(data) {
      if (s.state === "live") sendBytes(chunkText(data));
    },
    async paste(text) {
      if (deps.pasteCheck(text) && !(await deps.confirmPaste(text))) return;
      term.paste(text);
    },
    setPalette(palette) { term.options.theme = palette; },
    setFont(font) {
      term.options.fontFamily = font.family;
      term.options.fontSize = font.size;
      refit();
    },
    // Closes the screen and the socket; the shell is the server's to end.
    dispose() {
      ended = true;
      window.clearTimeout(retry);
      window.cancelAnimationFrame(fitFrame);
      observer.disconnect();
      const sock = ws;
      ws = null;
      sock?.close(1000);
      term.dispose();
      pane.remove();
    },
    kill() { send({ t: "close" }); },
  };
  return session;
}

function exitNote(exit) {
  if (exit.reason && exit.reason !== "exit") return `Session closed (${exit.reason}).`;
  return exit.code == null ? "The shell exited." : `The shell exited with code ${exit.code}.`;
}

// In-app notifications on /chat, drawn as cards in the toast stack (fuseUI.toast).
//
// Two kinds, each with its own lifetime:
// - Notices (app/notices.py): the server's persistent messages (a secret
//   swapped, a usage limit). They never time out. Closing one dismisses it on
//   every device; /chat/sync lists the open ones, so a device that was away
//   shows them when it comes back. A chat's notice names that chat when
//   another one is open, and opens it on a click.
// - Turn notifications: a reply finished or failed in a chat that is not on
//   screen. Finished ones leave after a while (the clock runs only while the
//   page is visible); failed ones stay until opened or closed. Opening the
//   chat closes its card. This tab only: the OS alert (notifications.js)
//   covers a page nobody is looking at.
import { S } from '../state.js';

const TURN_TTL_MS = 10000;
const SNIPPET_MAX = 140;

export function createNotices(ctx) {
  const { net, sidebar } = ctx;
  const open = new Map(); // notice id -> view
  const drawn = new Map(); // toast key -> signature of what it shows

  const ui = () => window.fuseUI;

  function chatTitle(threadId) {
    return sidebar.threadById(threadId)?.title || "New chat";
  }

  function openChat(threadId) {
    sidebar.openThread(threadId).catch(() => {
      ui()?.toast({ kind: "err", title: "Chat could not be opened", msg: "It may have been deleted." });
    });
  }

  function close(key) {
    drawn.delete(key);
    document.querySelector(`.fuse-toast[data-toast-key="${CSS.escape(key)}"]`)?._ctrl.close();
  }

  function draw(key, opts) {
    const sig = JSON.stringify([opts.kind, opts.source, opts.title, opts.msg, Boolean(opts.onOpen)]);
    const live = document.querySelector(`.fuse-toast[data-toast-key="${CSS.escape(key)}"]:not(.is-leaving)`);
    if (live && drawn.get(key) === sig) return;
    drawn.set(key, sig);
    ui()?.toast({ key, ...opts });
  }

  function drawNotice(notice) {
    const elsewhere = notice.thread_id && notice.thread_id !== S.currentId;
    draw("notice:" + notice.id, {
      kind: notice.kind,
      source: elsewhere ? chatTitle(notice.thread_id) : "",
      title: notice.title,
      msg: notice.body,
      ttl: 0,
      onOpen: elsewhere ? () => openChat(notice.thread_id) : undefined,
      onDismiss: () => dismiss(notice.id),
    });
  }

  function dismiss(id) {
    open.delete(id);
    drawn.delete("notice:" + id);
    net.api(`/chat/notices/${encodeURIComponent(id)}/dismiss`, { method: "POST", body: "{}" }).catch(() => {});
  }

  // What the open chat changes: a chat's own notices drop their source line,
  // and its turn card is read.
  function render() {
    if (S.currentId) close("turn:" + S.currentId);
    for (const notice of open.values()) drawNotice(notice);
  }

  function accept(list) {
    const ids = new Set((list || []).map((n) => n.id));
    for (const id of open.keys()) {
      if (!ids.has(id)) {
        open.delete(id);
        close("notice:" + id);
      }
    }
    for (const notice of list || []) open.set(notice.id, notice);
    render();
  }

  function receive(type, payload, threadId) {
    if (type === "notice.updated") {
      open.set(payload.id, { ...payload, thread_id: payload.thread_id || threadId || null });
      // A limit that stopped the turn says more than "Reply failed".
      if (payload.thread_id) close("turn:" + payload.thread_id);
      drawNotice(open.get(payload.id));
    } else if (type === "notice.dismissed") {
      open.delete(payload.id);
      close("notice:" + payload.id);
    }
  }

  function turnDone(threadId, payload, type) {
    if (type !== "run.completed" && type !== "run.failed") return;
    if (!threadId || threadId === S.currentId || payload?.settled === false) return;
    const failed = type === "run.failed";
    if (failed && [...open.values()].some((n) => n.thread_id === threadId)) return;
    const msg = payload?.message;
    const last = (msg?.segments || []).filter((part) => part.type === "content" && String(part.text || "").trim()).pop();
    let text = String((failed ? msg?.error : last?.text || msg?.content) || "").replace(/\s+/g, " ").trim();
    if (text.length > SNIPPET_MAX) text = text.slice(0, SNIPPET_MAX - 1).trimEnd() + "…";
    draw("turn:" + threadId, {
      kind: failed ? "err" : "ok",
      source: chatTitle(threadId),
      title: failed ? "Reply failed" : "Reply finished",
      msg: text,
      ttl: failed ? 0 : TURN_TTL_MS,
      onOpen: () => openChat(threadId),
    });
  }

  return { accept, receive, render, turnDone };
}

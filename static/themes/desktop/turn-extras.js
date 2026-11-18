// The transcript pieces the runtime used to build itself: the optimistic
// send, a deferred (load-on-demand) answer, recaps, the compaction turn, the
// rewind list and queued messages. Each keeps the runtime's data-* hooks.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);

  // Local feedback while a send is in flight; never part of the saved transcript.
  // It is drawn as the accepted turn will be (the same bubble, images included,
  // and the status line's place and type), so the swap changes only the word.
  function pendingSendHtml(text, attachments = []) {
    const files = Array.isArray(attachments) ? attachments : [];
    // An image-only send has no text: the runtime's "N attachment(s)" stand-in is not shown.
    const content = files.length && /^\d+ attachment\(s\)$/.test(text) ? "" : text;
    const turn = deps.renderUser({ role: "user", content, attachments: files }, -1).outerHTML;
    return '<div class="chat-log chat-pending-send" role="status" data-pending-send>' + turn +
      '<small class="dk-pending-status"><span class="chat-status-verb">Sending…</span></small></div>';
  }

  // An older answer drawn on demand: [data-load-earlier] hydrates it.
  function deferredShellHtml() {
    return '<article class="chat-turn is-assistant chat-deferred-message" data-deferred-message>' +
      '<button type="button" class="btn ghost sm" data-load-earlier>Load earlier response</button></article>';
  }

  function recapHtml(text) {
    return `<p class="chat-recap" aria-label="Work recap" data-recap><span class="chat-recap-copy"><span class="chat-recap-text">${esc(text)}</span></span></p>`;
  }

  // A compaction checkpoint as its own turn.
  function markerTurnHtml(title, summary) {
    return `<article class="chat-turn is-marker">${deps.compactionMarkHtml(title, summary)}</article>`;
  }

  function rewindListHtml(items) {
    if (!items.length) return '<p class="chat-rewind-empty">No messages to rewind to yet.</p>';
    return items.map((item) =>
      `<button type="button" class="chat-rewind-item" data-rewind-index="${item.index}"><strong>${esc(item.preview.slice(0, 72))}</strong><span>${
        item.after ? item.after + " after this" : "latest"
      }</span></button>`).join("");
  }

  // One queued message. q: {index, open, sending, held, streaming, state}.
  function queueItemHtml(item, q) {
    const off = q.sending ? " disabled" : "";
    return `<li class="chat-queue-item${item.immediate ? " is-immediate" : ""}${q.sending ? " is-sending" : ""}${q.held ? " is-held" : ""}" data-queue-index="${q.index}" data-queue-id="${esc(item.id)}" aria-busy="${q.sending}">` +
      `<span class="chat-queue-dot" aria-hidden="true"></span>` +
      `<span class="chat-queue-text" title="${esc(item.text)}">${esc(item.text || "Attached files")}</span>` +
      `<span class="chat-queue-state" role="status">${q.state}</span>` +
      '<span class="chat-queue-actions">' +
      `<button type="button" class="chat-queue-action" data-queue-send="${q.index}" title="${q.streaming ? "Stop the current reply and send this message now" : "Send this message now"}"${!q.streaming && !q.held ? " hidden" : ""}${q.sending || (!q.streaming && !q.held) ? " disabled" : ""}>Send now</button>` +
      '<span class="chat-queue-more-wrap">' +
      `<button type="button" class="chat-queue-more" data-queue-more="${q.index}" aria-haspopup="menu" aria-expanded="${q.open}" aria-label="More actions for: ${esc((item.text || "Attached files").slice(0, 40))}" title="Edit or cancel"${off}>` +
      '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="3.5" r="1.3" fill="currentColor"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/><circle cx="8" cy="12.5" r="1.3" fill="currentColor"/></svg></button>' +
      `<span class="chat-queue-menu" role="menu"${q.open ? "" : " hidden"}>` +
      `<button type="button" class="chat-queue-action" role="menuitem" data-queue-edit="${q.index}"${off}>Edit</button>` +
      `<button type="button" class="chat-queue-action" role="menuitem" data-queue-cancel="${q.index}"${off}>Cancel</button>` +
      "</span></span></span></li>";
  }

  return { deferredShellHtml, markerTurnHtml, pendingSendHtml, queueItemHtml, recapHtml, rewindListHtml };
}

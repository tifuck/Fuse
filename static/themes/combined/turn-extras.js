// The transcript pieces the runtime used to build itself: the optimistic
// send, a deferred (load-on-demand) answer, recaps, the compaction turn, the
// rewind list and queued messages. Each keeps the runtime's data-* hooks.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);

  // Local feedback while a send is in flight; never part of the saved transcript.
  function pendingSendHtml(text) {
    return '<div class="chat-log chat-pending-send" role="status" data-pending-send>' +
      `<article class="chat-turn is-user"><div class="chat-bubble">${esc(text)}</div></article>` +
      "<small>Sending…</small></div>";
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
    // Fixed columns (number, text, state, actions) so every row's right edge
    // lines up; Send now keeps its slot even when there is nothing to send now.
    const canSend = q.streaming || q.held;
    return `<li class="chat-qrow${item.immediate ? " is-immediate" : ""}${q.sending ? " is-sending" : ""}${q.held ? " is-held" : ""}" data-queue-index="${q.index}" data-queue-id="${esc(item.id)}" aria-busy="${q.sending}">` +
      `<span class="chat-qrow-n mono" aria-hidden="true">${q.index + 1}</span>` +
      `<span class="chat-qrow-text" title="${esc(item.text)}">${esc(item.text || "Attached files")}</span>` +
      '<span class="chat-qrow-meta">' +
      (q.attached ? `<span class="chat-qrow-att" title="${q.attached} attached"><svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M10.5 4.5 5.8 9.2a1.5 1.5 0 0 0 2.1 2.1l5-5a3 3 0 0 0-4.2-4.2l-5 5a4.5 4.5 0 0 0 6.4 6.4l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>${q.attached}<span class="visually-hidden"> attached</span></span>` : "") +
      `<span class="chat-queue-state" role="status">${q.state}</span></span>` +
      '<span class="chat-qrow-actions">' +
      `<button type="button" class="chat-qrow-btn is-send" data-queue-send="${q.index}" aria-label="Send now" title="${q.streaming ? "Stop the current reply and send this message now" : "Send this message now"}"${q.sending || !canSend ? " disabled" : ""}><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` +
      '<span class="chat-queue-more-wrap">' +
      `<button type="button" class="chat-qrow-btn" data-queue-more="${q.index}" aria-haspopup="menu" aria-expanded="${q.open}" aria-label="Edit or remove: ${esc((item.text || "Attached files").slice(0, 40))}" title="Edit or remove"${off}>` +
      '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3" fill="currentColor"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/><circle cx="12.5" cy="8" r="1.3" fill="currentColor"/></svg></button>' +
      `<span class="chat-queue-menu" role="menu"${q.open ? "" : " hidden"}>` +
      `<button type="button" class="chat-queue-action" role="menuitem" data-queue-edit="${q.index}"${off}>Edit</button>` +
      `<button type="button" class="chat-queue-action" role="menuitem" data-queue-cancel="${q.index}"${off}>Remove</button>` +
      "</span></span></span></li>";
  }

  return { deferredShellHtml, markerTurnHtml, pendingSendHtml, queueItemHtml, recapHtml, rewindListHtml };
}

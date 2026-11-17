// todos presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
// The tray's frame is built once so the sheet can slide open and shut and the
// head line can pulse; only the head's contents and the rows are repainted.
// The header's todo button opens it.
function todoTrayHtml(listId) {
    return '<div class="chat-todo-sheet"><div class="chat-todo-drawer"><div class="chat-todo-clip"><div class="chat-todo-page">' +
      `<div class="chat-todo-bar" data-todo-bar></div><ol class="chat-todo-rows" id="${listId}" data-todo-list></ol></div></div></div></div>`;
  }

function todoBarHtml(state, { active }) {
    const items = state.items;
    const done = items.filter(deps.todoClosed).length;
    const dismiss = active ? "" : '<button type="button" class="chat-todo-dismiss" data-todo-dismiss aria-label="Dismiss todo list" title="Dismiss">×</button>';
    return `<span class="chat-todo-head"><span class="chat-todo-title">Todos</span><span class="chat-todo-count" data-todo-count>${done}/${items.length}</span></span>${dismiss}`;
  }

// A done step is checked; a cancelled one is struck and says so.
const CHECK = '<svg viewBox="0 0 16 16" width="10" height="10"><path d="M3.5 8.4l3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function todoRowsHtml(items) {
    return items.map((item) =>
      `<li class="chat-todo-row is-${item.status.replace("_", "-")}"><span class="chat-todo-box" aria-hidden="true">${item.status === "completed" ? CHECK : ""}</span>` +
      `<span class="chat-todo-text">${deps.escapeHtml(item.content)}</span>` +
      `${item.status === "cancelled" ? '<span class="chat-todo-skip">Skipped</span>' : ""}</li>`).join("");
  }

  return { todoTrayHtml, todoBarHtml, todoRowsHtml };
}

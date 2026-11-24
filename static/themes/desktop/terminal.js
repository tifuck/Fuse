// Desktop: the terminal panel docked at the bottom of the window, under the
// composer (a full-height sheet on a phone), and its top-bar button. Markup only;
// static/chat/terminal.js fills the tabs, the machine list and the keys and
// drives everything. Built here rather than in a template so the view's
// static files carry it whole.
const svg = (body, size = 16) => `<svg viewBox="0 0 16 16" width="${size}" height="${size}" aria-hidden="true">${body}</svg>`;
const STROKE = 'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"';
export const ICONS = {
  terminal: svg(`<rect x="2.6" y="3.4" width="10.8" height="9.2" rx="1.6" ${STROKE}/><path d="M5 6.6l1.8 1.5L5 9.6M8.4 10h2.6" ${STROKE}/>`, 17),
  plus: svg(`<path d="M8 3.5v9M3.5 8h9" ${STROKE}/>`),
  find: svg(`<circle cx="7" cy="7" r="3.8" ${STROKE}/><path d="M10 10l3 3" ${STROKE}/>`),
  hide: svg(`<path d="M4 6.5L8 10.5l4-4" ${STROKE}/>`),
  up: svg(`<path d="M4.5 9.5L8 6l3.5 3.5" ${STROKE}/>`),
  down: svg(`<path d="M4.5 6.5L8 10l3.5-3.5" ${STROKE}/>`),
  close: svg(`<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" ${STROKE}/>`),
};

export function dockHtml({ id = "chat-terminal", grip = true, extra = "" } = {}) {
  return `<section class="term-dock" id="${id}" data-terminal hidden aria-label="Terminal">` +
    (grip ? '<div class="term-grip" data-terminal-resize role="separator" aria-orientation="horizontal" aria-label="Resize the terminal" tabindex="0"></div>' : "") +
    '<div class="term-head">' +
      '<div class="term-tabs" data-terminal-tabs aria-label="Terminals"></div>' +
      '<div class="term-new">' +
        `<button type="button" class="term-btn" data-terminal-new aria-haspopup="menu" aria-expanded="false" aria-label="New terminal" title="New terminal">${ICONS.plus}</button>` +
        '<div class="term-menu" data-terminal-menu hidden></div>' +
      '</div>' +
      '<span class="term-gap"></span>' +
      '<button type="button" class="term-btn term-touch" data-terminal-line aria-pressed="false" title="Line mode: type a line, Enter sends it">Line</button>' +
      `<button type="button" class="term-btn" data-terminal-search aria-label="Find" title="Find (Ctrl+Shift+F)">${ICONS.find}</button>` +
      '<button type="button" class="term-btn term-zoom" data-terminal-font="-1" aria-label="Smaller text" title="Smaller text">A−</button>' +
      '<button type="button" class="term-btn term-zoom" data-terminal-font="1" aria-label="Larger text" title="Larger text">A+</button>' +
      extra +
      `<button type="button" class="term-btn" data-terminal-hide aria-label="Hide the terminal" title="Hide the terminal (it keeps running)">${ICONS.hide}</button>` +
    '</div>' +
    '<div class="term-find" data-terminal-find hidden>' +
      '<input type="text" aria-label="Find in the terminal" placeholder="Find" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">' +
      `<button type="button" class="term-btn" data-terminal-find-prev aria-label="Previous match" title="Previous (Shift+Enter)">${ICONS.up}</button>` +
      `<button type="button" class="term-btn" data-terminal-find-next aria-label="Next match" title="Next (Enter)">${ICONS.down}</button>` +
      `<button type="button" class="term-btn" data-terminal-find-close aria-label="Close find" title="Close (Esc)">${ICONS.close}</button>` +
    '</div>' +
    '<div class="term-body" data-terminal-body></div>' +
    '<form class="term-line" data-terminal-line-form hidden><span aria-hidden="true">›</span><input data-terminal-line-input aria-label="Line to send" placeholder="Type a line; Enter sends it"></form>' +
    '<div class="term-keys" data-terminal-keys role="group" aria-label="Terminal keys"></div>' +
  '</section>';
}

export function create() {
  const root = document.querySelector("[data-chat]");
  const form = root?.querySelector(".chat-stage > [data-chat-form]");
  if (!form || root.querySelector("[data-terminal]")) return {};
  form.insertAdjacentHTML("afterend", dockHtml());
  root.querySelector(".dk-actions [data-artifact-toggle]")?.insertAdjacentHTML("beforebegin",
    `<button type="button" class="dk-ib" data-terminal-toggle aria-expanded="false" aria-controls="chat-terminal" aria-label="Terminal" title="Terminal">${ICONS.terminal.replace(/width="17" height="17"/, 'width="16" height="16"')}<span class="visually-hidden">Terminal</span></button>`);
  return {};
}

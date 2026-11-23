// Console's /theme picker: a plain bordered strip of wallpaper cards above the composer.
// The runtime (chat/composer/wallpapers.js) owns the items, the keys and the
// choice; every card carries data-wallpaper-card and a FuseBackdrop preview.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(String(value ?? ""));

  function wallpaperPickerHtml({ items, index, current }) {
    const cards = items.map((item, i) => {
      const on = item.id === current;
      return `<button type="button" class="chat-wp-card${i === index ? " is-active" : ""}${on ? " is-on" : ""}" role="option" data-wallpaper-card="${esc(item.id)}" aria-selected="${i === index}" aria-current="${on}" title="${esc(item.label)}">` +
        `<i class="chat-backdrop-preview chat-wp-thumb" data-kind="${esc(item.id)}" aria-hidden="true"></i>` +
        `<span class="chat-wp-name">${on ? '<b class="chat-wp-check" aria-hidden="true">✓</b>' : ""}${esc(item.label)}</span></button>`;
    }).join("");
    return `<div class="chat-wp" role="dialog" aria-label="Backdrops">` +
      `<div class="chat-wp-head"><span class="chat-wp-cmd">/theme</span><b>Backdrops</b><span class="chat-wp-keys">↵ apply · esc close</span>` +
      `<button type="button" class="chat-wp-x" data-wallpaper-close aria-label="Close backdrops">×</button></div>` +
      `<div class="chat-wp-row" role="listbox" aria-label="Backdrops">${cards}</div></div>`;
  }

  return { wallpaperPickerHtml };
}

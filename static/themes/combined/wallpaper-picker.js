// Combined's /theme picker: a rounded card of wallpaper thumbnails above the composer.
// The runtime (chat/composer/wallpapers.js) owns the items, the keys and the
// choice; every card carries data-wallpaper-card and a FuseBackdrop preview.
// A click outside the open picker closes it through its own close button.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(String(value ?? ""));

  document.addEventListener("pointerdown", (event) => {
    const picker = document.querySelector("[data-wallpaper-picker]:not([hidden])");
    if (picker && !picker.contains(event.target)) picker.querySelector("[data-wallpaper-close]")?.click();
  }, true);

  function wallpaperPickerHtml({ items, index, current }) {
    const cards = items.map((item, i) => {
      const on = item.id === current;
      return `<button type="button" class="cb-wp-card${i === index ? " is-active" : ""}${on ? " is-on" : ""}" role="option" data-wallpaper-card="${esc(item.id)}" aria-selected="${i === index}" aria-current="${on}" title="${esc(item.label)}">` +
        `<i class="chat-backdrop-preview cb-wp-thumb" data-kind="${esc(item.id)}" aria-hidden="true"></i>` +
        `<span class="cb-wp-name">${on ? '<b class="cb-wp-check" aria-hidden="true">✓</b>' : ""}${esc(item.label)}</span></button>`;
    }).join("");
    return `<div class="cb-wp" role="dialog" aria-label="Wallpapers">` +
      `<div class="cb-wp-head"><span class="cb-wp-cmd">/theme</span><b>Wallpapers</b><span class="cb-wp-keys">↵ apply · esc close</span>` +
      `<button type="button" class="cb-wp-x" data-wallpaper-close aria-label="Close wallpapers">×</button></div>` +
      `<div class="cb-wp-row" role="listbox" aria-label="Wallpapers">${cards}</div></div>`;
  }

  return { wallpaperPickerHtml };
}

// Desktop's /theme picker: a translucent rounded card above the composer with a /theme chip, the title, the keys on the right and a row of thumbnails with names and a check on the current one.
// The runtime (chat/composer/wallpapers.js) owns the items, the keys and the
// choice; every card carries data-wallpaper-card and a FuseBackdrop preview.
let outsideBound = false;

// A press outside the open strip closes it, through its own × so the runtime closes it as it does for that button.
function bindOutsideClose() {
  if (outsideBound || typeof document === "undefined") return;
  outsideBound = true;
  document.addEventListener("pointerdown", (event) => {
    const host = document.querySelector("[data-wallpaper-picker]:not([hidden])");
    if (!host || host.contains(event.target) || event.target.closest?.("[data-chat-input]")) return;
    host.querySelector("[data-wallpaper-close]")?.click();
  }, true);
}

export function create(deps) {
  const esc = (value) => deps.escapeHtml(String(value ?? ""));
  bindOutsideClose();

  function wallpaperPickerHtml({ items, index, current }) {
    const cards = items.map((item, i) => {
      const on = item.id === current;
      return `<button type="button" class="dk-wp-card${i === index ? " is-active" : ""}${on ? " is-on" : ""}" role="option" data-wallpaper-card="${esc(item.id)}" aria-selected="${i === index}" aria-current="${on}" title="${esc(item.label)}">` +
        `<i class="chat-backdrop-preview dk-wp-thumb" data-kind="${esc(item.id)}" aria-hidden="true"></i>` +
        `<span class="dk-wp-name">${on ? '<b class="dk-wp-check" aria-hidden="true">✓</b>' : ""}${esc(item.label)}</span></button>`;
    }).join("");
    return `<div class="dk-wp" role="dialog" aria-label="Wallpapers">` +
      `<div class="dk-wp-head"><span class="dk-wp-cmd">/theme</span><b>Wallpapers</b><span class="dk-wp-keys">↵ apply · esc close</span>` +
      `<button type="button" class="dk-wp-x" data-wallpaper-close aria-label="Close wallpapers">×</button></div>` +
      `<div class="dk-wp-row" role="listbox" aria-label="Wallpapers">${cards}</div></div>`;
  }

  return { wallpaperPickerHtml };
}

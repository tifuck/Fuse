// The /theme wallpaper picker: a strip of wallpaper cards above the composer.
// The runtime owns the data (FuseChatAppearance's wallpapers plus "None"),
// the keys (arrows move, Enter applies, Esc closes) and the choice; the theme
// draws the strip (ui.wallpaperPickerHtml). Cards carry data-wallpaper-card;
// their preview frames come from FuseBackdrop.previews, as in Appearance.
import { S } from '../state.js';

export function createWallpapers(ctx) {
  const { ui } = ctx;
  let index = 0;
  let items = [];
  let bound = false;

  const host = () => S.root.querySelector("[data-wallpaper-picker]");
  const appearance = () => window.FuseChatAppearance;

  function catalog() {
    const list = appearance()?.wallpapers || [];
    return [{ id: "none", label: "None", scheme: "" }, ...list.map((w) => ({ id: w.id, label: w.label, scheme: w.scheme }))];
  }

  function isOpen() {
    const el = host();
    return Boolean(el && !el.hidden);
  }

  function paint() {
    const el = host();
    if (!el) return;
    items = catalog();
    const current = appearance()?.backdrop || "none";
    if (index < 0 || index >= items.length) index = Math.max(0, items.findIndex((w) => w.id === current));
    el.innerHTML = ui.wallpaperPickerHtml({ items, index, current });
    el.querySelector(`[data-wallpaper-card="${CSS.escape(items[index]?.id || "")}"]`)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    window.FuseBackdrop?.previews?.(el);
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.addEventListener("keydown", handleKey, true);
    S.root.addEventListener("click", (event) => {
      const card = event.target.closest?.("[data-wallpaper-picker] [data-wallpaper-card]");
      if (card) {
        index = items.findIndex((w) => w.id === card.dataset.wallpaperCard);
        apply(false);
        return;
      }
      if (event.target.closest?.("[data-wallpaper-picker] [data-wallpaper-close]")) close();
    });
    document.addEventListener("fuse-wallpapers", () => { if (isOpen()) paint(); });
    document.addEventListener("fuse-appearance", () => {
      if (!isOpen()) return;
      const current = appearance()?.backdrop || "none";
      if (host().querySelector(`[data-wallpaper-card][aria-current="true"]`)?.dataset.wallpaperCard !== current) paint();
    });
  }

  function open() {
    const el = host();
    if (!el) return;
    bind();
    index = -1;
    el.hidden = false;
    paint();
    S.input?.focus({ preventScroll: true });
  }

  function close() {
    const el = host();
    if (!el || el.hidden) return;
    el.hidden = true;
    el.innerHTML = "";
  }

  function apply(closeAfter) {
    const pick = items[index];
    if (!pick || !appearance()) return;
    appearance().setBackdrop(pick.id);
    if (closeAfter) close();
    else paint();
  }

  // Keys while the strip is open; true when the key was the picker's.
  function handleKey(event) {
    if (!isOpen()) return false;
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (step) {
      index = (index + step + items.length) % items.length;
      paint();
    } else if (event.key === "Enter" && !event.shiftKey) {
      apply(true);
    } else if (event.key === "Escape") {
      close();
    } else return false;
    event.preventDefault();
    event.stopImmediatePropagation();
    return true;
  }

  return { openWallpaperPicker: open, closeWallpaperPicker: close, wallpaperPickerOpen: isOpen, wallpaperPickerKey: handleKey };
}

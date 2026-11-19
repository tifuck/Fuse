// The sidebar's saved state, applied to the chat root before first paint.
// chat.html loads this as the root's first child; the template renders the
// default (collapsed). chat.js applies the same values again when it starts
// (sidebar/drawer.js: historyCollapsedPrefs, applyHistoryWidth) and so finds
// nothing to change: keep the keys and the width bounds in step with it.
(function () {
  const script = document.currentScript;
  const root = script && script.parentElement;
  if (!root) return;
  try {
    const saved = (JSON.parse(localStorage.getItem("fuse.chat.historyCollapsed.v1") || "{}") || {}).console;
    root.classList.toggle("history-collapsed", typeof saved === "boolean" ? saved : true);
  } catch (_) {}
  try {
    const width = Number(localStorage.getItem("fuse.chat.historyWidth")) || 0;
    if (width >= 14 * 16 && width <= 30 * 16) root.style.setProperty("--chat-history", width + "px");
  } catch (_) {}
  script.remove();
})();

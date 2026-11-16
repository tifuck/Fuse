const { ipcRenderer } = require("electron");

// The site's accent lives in <html>'s inline style and data attributes; only
// the background reaches <meta name="theme-color">. Watching <html> lets the
// title bar and window icon follow an accent, wallpaper or light/dark change
// the moment it happens.
// Nothing is exposed to the page.
let timer = 0;
window.addEventListener("DOMContentLoaded", () => {
  for (const el of document.querySelectorAll("[data-desktop-only]")) el.hidden = false;
  new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => ipcRenderer.send("site-theme"), 80);
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["style", "data-chat-theme", "data-chat-backdrop", "data-chat-scheme"] });
});

// A clicked alert asks for the window: the page's window.focus() neither
// restores a minimized Electron window nor raises it over other apps.
window.addEventListener("fuse:raise", () => ipcRenderer.send("site-raise"));

// The update toast's Restart button; main ignores it until an update is downloaded.
window.addEventListener("fuse:restart", () => ipcRenderer.send("site-restart"));

// Change server (the site's sign-in page, load-error.html) and the connect
// page's form. [data-desktop-only] stays hidden in a browser.
document.addEventListener("click", (event) => {
  if (!event.target.closest || !event.target.closest("[data-desktop-change-server]")) return;
  event.preventDefault();
  ipcRenderer.send("site-change-server");
});
document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-desktop-connect]");
  if (!form) return;
  event.preventDefault();
  ipcRenderer.send("site-connect", form.elements.server.value);
});

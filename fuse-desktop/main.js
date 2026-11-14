const { app, BaseWindow, clipboard, dialog, Menu, WebContentsView, ipcMain, nativeImage, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawnSync } = require("child_process");
const { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } = require("fs");
const path = require("path");
const { MAX_INSTALL_TRIES, launchNotice } = require("./update-notice");
const { contextMenuTemplate } = require("./context-menu");
const { checkSiteUrl } = require("./site-url");

// The site this build wraps: FUSE_URL at runtime, else the fuseUrl that
// scripts/dist.js stamps into package.json, else site.json for `npm start`.
function siteUrl() {
  if (process.env.FUSE_URL) return process.env.FUSE_URL;
  const stamped = require("./package.json").fuseUrl;
  if (stamped) return stamped;
  try {
    return JSON.parse(readFileSync(path.join(__dirname, "site.json"), "utf8")).url;
  } catch (err) {
    throw new Error(`No Fuse site URL: set FUSE_URL or fuse-desktop/site.json {"url": ...} (${err.message})`);
  }
}
let APP_URL;
try {
  APP_URL = checkSiteUrl(siteUrl()).href;
} catch (err) {
  // Before ready, Linux prints this to stderr instead of a dialog.
  dialog.showErrorBox("Fuse can't start", err.message);
  process.exit(1);
}

// Windows attributes toasts to the Start-menu shortcut, whose AUMID the NSIS
// installer sets to appId. Electron's default (electron.app.Fuse) matches no
// shortcut, so alerts lost the app's name and icon in the Action Center.
const APP_ID = require("./package.json").fuseAppId;
if (process.platform === "win32" && APP_ID) app.setAppUserModelId(APP_ID);
const APP_ORIGIN = new URL(APP_URL).origin;
const BACKGROUND = "#141615";

// Windows and Linux draw Fuse's own dark title bar instead of the OS frame
// (the light-gray GNOME header on Ubuntu). macOS keeps its native frame.
const CUSTOM_FRAME = process.platform !== "darwin";
const TITLEBAR_HEIGHT = CUSTOM_FRAME ? 34 : 0;

// Chromium sandboxes through user namespaces when it can, else through the
// setuid chrome-sandbox helper. Only when neither is available (AppImage or a
// stripped helper on a kernel that restricts unprivileged user namespaces)
// is the sandbox turned off; it used to be off for every AppImage.
function userNamespacesUsable() {
  const flag = (file) => {
    try {
      return readFileSync(file, "utf8").trim();
    } catch {
      return null;
    }
  };
  if (flag("/proc/sys/kernel/unprivileged_userns_clone") === "0") return false;
  if (flag("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") === "1") return false;
  return true;
}

// Chromium refuses a setuid helper that root does not own, so only that
// one counts.
function setuidSandboxHelper() {
  if (process.env.APPIMAGE) return false;
  try {
    const helper = statSync(path.join(path.dirname(process.execPath), "chrome-sandbox"));
    return helper.uid === 0 && Boolean(helper.mode & 0o4000);
  } catch {
    return false;
  }
}

if (process.platform === "linux" && process.getuid() !== 0 && !setuidSandboxHelper() && !userNamespacesUsable()) {
  console.warn("fuse: no root-owned setuid chrome-sandbox and unprivileged user namespaces are restricted; running without the Chromium sandbox");
  app.commandLine.appendSwitch("no-sandbox");
}

// GPU rendering on Linux flashes random pink/green/black garbage blocks on
// some AMD and NVIDIA machines (seen on Strix Halo and an RTX 5060). Machines
// where Chromium already falls back to software never show it, so draw in
// software everywhere on Linux.
if (process.platform === "linux") app.disableHardwareAcceleration();

let win = null;
let bar = null;
let site = null;

// A view's webContents is undefined while it is torn down, and its events
// (the bar taking focus as the page closes) still fire then.
const alive = (view) => Boolean(view && view.webContents && !view.webContents.isDestroyed());

// The bar takes the site's active theme (Paper, Sand, Midnight, ...). The site
// publishes its background in <meta name="theme-color"> on every theme change;
// the other tokens are read from the page. The last theme is remembered so
// the next launch opens in the right colours instead of flashing dark.
const THEME_FILE = () => path.join(app.getPath("userData"), "frame-theme.json");
const HEX = /^#[0-9a-f]{3,8}$/i;
let frameTheme = null;

function loadFrameTheme() {
  try {
    const saved = JSON.parse(readFileSync(THEME_FILE(), "utf8"));
    if (HEX.test(saved.ink)) frameTheme = saved;
  } catch {}
}

async function syncFrameTheme() {
  if (!alive(site)) return;
  let theme;
  try {
    theme = await site.webContents.executeJavaScript(`(() => {
      const css = getComputedStyle(document.documentElement);
      const read = (name) => css.getPropertyValue(name).trim();
      const meta = document.querySelector('meta[name="theme-color"]');
      return {
        ink: (meta && meta.content.trim()) || read("--ink"),
        mist: read("--mist"),
        mute: read("--mute"),
        line: read("--line"),
        scheme: css.colorScheme === "light" ? "light" : "dark",
      };
    })()`);
  } catch {
    return;
  }
  if (!theme || !HEX.test(theme.ink) || !win) return;
  if (JSON.stringify(theme) === JSON.stringify(frameTheme)) return;
  frameTheme = theme;
  win.setBackgroundColor(theme.ink);
  site.setBackgroundColor(theme.ink);
  if (bar) bar.setBackgroundColor(theme.ink);
  sendFrameState();
  try { writeFileSync(THEME_FILE(), JSON.stringify(theme)); } catch {}
}

function layout() {
  if (!win) return;
  // The content view's own bounds are the window's client area once the OS
  // has applied a maximize/resize; getContentBounds() can still report the
  // old size inside the "maximize" event on Linux.
  const { width, height } = win.contentView.getBounds();
  const top = bar && !win.isFullScreen() ? TITLEBAR_HEIGHT : 0;
  if (bar) bar.setBounds({ x: 0, y: 0, width, height: top });
  site.setBounds({ x: 0, y: top, width, height: Math.max(0, height - top) });
}

function sendFrameState() {
  if (!alive(bar)) return;
  bar.webContents.send("titlebar-state", {
    maximized: win.isMaximized(),
    focused: win.isFocused(),
    theme: frameTheme,
  });
}

function handleShortcuts(event, input) {
  if (input.type !== "keyDown") return;
  const mod = input.control || input.meta;
  const key = input.key.toLowerCase();
  const wc = site.webContents;
  if (input.key === "F5" || (mod && key === "r")) {
    input.shift ? wc.reloadIgnoringCache() : wc.reload();
  } else if (input.control && input.key === "\\") {
    // Push-to-talk chord owned by an external listener; no view should act on it.
  } else if (input.key === "F12" || (mod && input.shift && key === "i")) {
    wc.toggleDevTools();
  } else if (mod && (key === "=" || key === "+")) {
    wc.setZoomLevel(wc.getZoomLevel() + 0.5);
  } else if (mod && key === "-") {
    wc.setZoomLevel(wc.getZoomLevel() - 0.5);
  } else if (mod && key === "0") {
    wc.setZoomLevel(0);
  } else if (input.alt && input.key === "ArrowLeft" && wc.navigationHistory.canGoBack()) {
    wc.navigationHistory.goBack();
  } else if (input.alt && input.key === "ArrowRight" && wc.navigationHistory.canGoForward()) {
    wc.navigationHistory.goForward();
  } else {
    return;
  }
  event.preventDefault();
}

// Window focus can return on the title bar or on the site with nothing
// focused; webContents.focus() alone does not bring the composer caret back.
function restoreFocus() {
  if (!alive(site)) return;
  site.webContents.focus();
  site.webContents.executeJavaScript(`(() => {
    const a = document.activeElement;
    if (a && a !== document.body) return;
    const e = window.__fuseLastEditable;
    if (e && e.isConnected) e.focus();
  })()`).catch(() => {});
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

// Page content picks these URLs: only hand web links to the OS, never a
// custom scheme that would launch some installed protocol handler.
const EXTERNAL_PROTOCOLS = new Set(["https:", "http:", "mailto:"]);
function openExternalSafe(url) {
  let protocol = "";
  try {
    protocol = new URL(url).protocol;
  } catch {
    return;
  }
  if (EXTERNAL_PROTOCOLS.has(protocol)) shell.openExternal(url);
}

// A failed load or a crashed page used to leave a blank window, with only a
// hidden F5 to get out. Say why and retry, backing off to a minute.
let loadFailures = 0;
function showLoadError(title, error, url, autoRetry = true) {
  if (!alive(site)) return;
  loadFailures += 1;
  site.webContents.loadFile(path.join(__dirname, "load-error.html"), {
    query: {
      title,
      error,
      url: originOf(url) === APP_ORIGIN ? url : APP_URL,
      retry: autoRetry ? String(Math.min(60, 2 ** loadFailures)) : "0",
    },
  });
}

// Set by app.quit(); a window that closes because Fuse is quitting quits
// again once its page is gone, since closing it first cancelled the quit.
let quitting = false;
app.on("before-quit", () => {
  quitting = true;
});

function createWindow() {
  const background = (frameTheme && frameTheme.ink) || BACKGROUND;
  win = new BaseWindow({
    width: 1280,
    height: 820,
    // Room for the sidebar beside the chat. The site keeps its desktop layout
    // here at any width, zoom included (static/desktop-layout.js).
    minWidth: 820,
    minHeight: 480,
    backgroundColor: background,
    autoHideMenuBar: true,
    frame: !CUSTOM_FRAME,
    title: "Fuse",
    icon: path.join(__dirname, "build", "icon.png"),
  });

  site = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "site-preload.js"),
    },
  });
  site.setBackgroundColor(background);
  win.contentView.addChildView(site);

  if (CUSTOM_FRAME) {
    bar = new WebContentsView({
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: path.join(__dirname, "titlebar", "preload.js"),
      },
    });
    bar.setBackgroundColor(background);
    win.contentView.addChildView(bar);
    bar.webContents.loadFile(path.join(__dirname, "titlebar", "index.html"));
    bar.webContents.on("did-finish-load", sendFrameState);
    bar.webContents.on("before-input-event", handleShortcuts);
    for (const name of ["maximize", "unmaximize", "focus", "blur"]) win.on(name, sendFrameState);
    site.webContents.on("did-change-theme-color", syncFrameTheme);
    site.webContents.on("dom-ready", syncFrameTheme);
  }

  // bounds-changed fires after the new size is laid out; the window events
  // are a backstop, re-run once more on the next tick for late WM updates.
  win.contentView.on("bounds-changed", layout);
  for (const name of ["resize", "maximize", "unmaximize", "restore", "enter-full-screen", "leave-full-screen"]) {
    win.on(name, () => {
      layout();
      setTimeout(layout, 50);
    });
  }
  layout();

  const wc = site.webContents;
  wc.on("before-input-event", handleShortcuts);
  win.on("focus", restoreFocus);
  if (bar) bar.webContents.on("focus", restoreFocus);
  wc.on("did-finish-load", flushToasts);
  wc.on("did-finish-load", () => {
    if (originOf(wc.getURL()) === APP_ORIGIN) loadFailures = 0;
  });
  wc.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    // -3 is an aborted navigation (a newer one, or one refused here).
    if (!isMainFrame || code === -3 || originOf(url) !== APP_ORIGIN) return;
    showLoadError("Can't reach Fuse", `${description} (${code}) loading ${url}`, url);
  });
  wc.on("render-process-gone", (_event, details) => {
    if (details.reason === "clean-exit") return;
    showLoadError("Fuse stopped", `The page process ended: ${details.reason} (exit code ${details.exitCode}).`, wc.getURL());
  });
  wc.on("dom-ready", () => {
    wc.executeJavaScript(`document.addEventListener("focusin", (ev) => {
      const t = ev.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA)$/.test(t.tagName))) window.__fuseLastEditable = t;
    }, true)`).catch(() => {});
  });

  wc.setWindowOpenHandler(({ url }) => {
    if (originOf(url) === APP_ORIGIN) {
      wc.loadURL(url);
    } else {
      openExternalSafe(url);
    }
    return { action: "deny" };
  });

  wc.on("context-menu", (_event, params) => {
    const items = contextMenuTemplate(params, wc, { openExternal: openExternalSafe, copyText: (text) => clipboard.writeText(text) });
    if (!items.length) return;
    const at = site.getBounds();
    Menu.buildFromTemplate(items).popup({ window: win, x: at.x + params.x, y: at.y + params.y });
  });

  wc.on("will-navigate", (event, url) => {
    if (url !== wc.getURL() && originOf(url) !== APP_ORIGIN) {
      event.preventDefault();
      openExternalSafe(url);
    }
  });
  // will-navigate does not see server redirects: an in-origin URL that
  // redirects elsewhere would otherwise render a foreign site in this window.
  wc.on("will-redirect", (event, url, _inPlace, isMainFrame) => {
    // Newer Electron puts the details on the event; older passes them positionally.
    const target = event.url || url;
    const main = event.isMainFrame !== undefined ? event.isMainFrame : isMainFrame;
    if (main && originOf(target) !== APP_ORIGIN) {
      event.preventDefault();
      openExternalSafe(target);
      // Nothing of the site is showing (the first load): say so instead of a
      // blank window. No auto-retry, which would reopen the browser each time.
      if (originOf(wc.getURL()) !== APP_ORIGIN) {
        showLoadError("Fuse opened in your browser", `${APP_ORIGIN} redirected to ${originOf(target)}, outside this app. Check the site URL this build uses.`, APP_URL, false);
      }
    }
  });

  // A BaseWindow closes without asking its page, so chat.js's beforeunload
  // guard (unsent turns, unsaved memory) never ran and that work was dropped.
  // Close the page first; the window follows once it is gone.
  win.on("close", (event) => {
    if (wc.isDestroyed()) return;
    event.preventDefault();
    wc.close({ waitForBeforeUnload: true });
  });
  wc.on("will-prevent-unload", (event) => {
    const choice = dialog.showMessageBoxSync(win, {
      type: "question",
      buttons: ["Leave", "Stay"],
      defaultId: 0,
      cancelId: 1,
      message: "Leave Fuse?",
      detail: "Changes that haven't reached the server yet will be lost.",
    });
    if (choice === 0) event.preventDefault();
    else quitting = false;
  });
  wc.on("destroyed", () => {
    if (win) win.destroy();
    if (quitting) app.quit();
  });

  win.on("closed", () => {
    if (!wc.isDestroyed()) wc.close();
    if (bar) bar.webContents.close();
    win = bar = site = null;
  });

  wc.loadURL(APP_URL);
  wc.focus();
}

ipcMain.on("titlebar", (event, action) => {
  if (!win || !bar || event.sender !== bar.webContents) return;
  if (action === "minimize") win.minimize();
  else if (action === "maximize") win.isMaximized() ? win.unmaximize() : win.maximize();
  else if (action === "close") win.close();
});

ipcMain.on("site-theme", (event) => {
  if (site && event.sender === site.webContents) syncFrameTheme();
});

ipcMain.on("site-raise", (event) => {
  if (!win || !site || event.sender !== site.webContents) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
});

// The title bar draws the logo in the theme's colours. Windows' taskbar and
// X11 window lists show it; GNOME's dock always shows the .desktop icon.
ipcMain.on("titlebar-icon", (event, url) => {
  if (!win || !bar || event.sender !== bar.webContents) return;
  if (typeof url !== "string" || !url.startsWith("data:image/png;base64,")) return;
  const image = nativeImage.createFromDataURL(url);
  if (!image.isEmpty()) win.setIcon(image);
});

ipcMain.on("site-restart", (event) => {
  if (site && event.sender === site.webContents && restartToUpdate) restartToUpdate();
});

// Update notices are the site's own toasts (window.fuseUI.toast, one keyed
// toast that changes in place). One the page cannot take yet (still loading,
// or a page without it) waits for the next page load.
const queuedToasts = [];

function showToast(toast) {
  const script = `(() => {
    const ui = window.fuseUI;
    if (!ui || !ui.toast) return false;
    const t = ${JSON.stringify({ key: "fuse-desktop-update", ...toast })};
    if (t.restart) t.onAction = () => window.dispatchEvent(new Event("fuse:restart"));
    if (t.href) t.onAction = () => { location.href = t.href; };
    ui.toast(t);
    return true;
  })()`;
  if (!alive(site)) return queuedToasts.push(toast);
  site.webContents.executeJavaScript(script).then(
    (shown) => shown || queuedToasts.push(toast),
    () => queuedToasts.push(toast),
  );
}

function flushToasts() {
  for (const toast of queuedToasts.splice(0)) showToast(toast);
}

// A single launch-time check with install-on-quit never updated a .deb install
// and failed silently, so check every few hours, download in the background,
// install when Fuse quits (or on the toast's Restart), and keep the updater's
// log in <logs>/updater.log. A failed install leaves the running version in
// place; the next launch says so, and a version that fails MAX_INSTALL_TRIES
// times stops installing on quit.
const UPDATE_CHECK_MS = 4 * 60 * 60 * 1000;
const RESTART_TIMEOUT_MS = 15 * 1000;
let restartToUpdate = null;

// Squirrel.Mac installs only an update signed like the running app, and mac
// builds are ad-hoc signed (electron-builder.yml identity: null), so every
// install failed after a "ready" toast. Point at the Download page instead;
// drop this once mac builds carry a Developer ID.
const CAN_INSTALL = process.platform !== "darwin";

// Variables systemd sets per unit; the relaunched unit gets its own.
const UNIT_ENV = new Set(["INVOCATION_ID", "JOURNAL_STREAM", "MANAGERPID", "MANAGERPIDFDID", "SYSTEMD_EXEC_PID", "MEMORY_PRESSURE_WATCH", "MEMORY_PRESSURE_WRITE"]);

// app.relaunch() starts the new Fuse from Electron's relauncher helper, which
// runs under no_new_privs. Where unprivileged user namespaces are restricted
// (Ubuntu 24.04+), Chromium sandboxes through the setuid chrome-sandbox, which
// cannot become root under that flag, so the relaunched Fuse aborted on start
// ("Zygote process exited prematurely") before any of this file ran. The
// user's systemd starts it clean instead, once this process has exited (an
// instance started earlier would only hand off to this one and quit).
function relaunchFromSystemd() {
  const args = [
    "--user", "--collect", "--quiet",
    ...Object.keys(process.env).filter((key) => !UNIT_ENV.has(key)).map((key) => `--setenv=${key}`),
    "--", "/bin/sh", "-c",
    'i=0; while kill -0 "$1" 2>/dev/null && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done; shift; exec "$@"',
    "fuse-relaunch", String(process.pid), process.execPath, ...process.argv.slice(1),
  ];
  const run = spawnSync("systemd-run", args, { stdio: "ignore", timeout: 10000 });
  return !run.error && run.status === 0;
}

function startUpdater() {
  const dir = app.getPath("logs");
  try {
    mkdirSync(dir, { recursive: true });
  } catch {}
  const file = path.join(dir, "updater.log");
  const hadLog = existsSync(file);
  const write = (level) => (...args) => {
    try {
      appendFileSync(file, `${new Date().toISOString()} ${level} ${args.join(" ")}\n`);
    } catch {}
  };
  autoUpdater.logger = { info: write("info"), warn: write("warn"), error: write("error"), debug() {} };
  autoUpdater.autoDownload = CAN_INSTALL;
  autoUpdater.autoInstallOnAppQuit = CAN_INSTALL;

  const stateFile = path.join(app.getPath("userData"), "update-state.json");
  let saved = {};
  try {
    saved = JSON.parse(readFileSync(stateFile, "utf8")) || {};
  } catch (err) {
    if (err.code !== "ENOENT") write("warn")(`Ignoring ${stateFile}: ${err.message}`);
  }
  const { state, toast } = launchNotice(saved, app.getVersion(), hadLog);
  // Written whole and renamed into place, so a crash cannot truncate it. The
  // tries count is what stops a failing install at MAX_INSTALL_TRIES: while
  // it cannot be saved, nothing installs on quit.
  const save = () => {
    const tmp = `${stateFile}.tmp`;
    try {
      writeFileSync(tmp, JSON.stringify(state));
      renameSync(tmp, stateFile);
      return true;
    } catch (err) {
      write("error")(`Cannot save ${stateFile}: ${err.message}; not installing on quit`);
      autoUpdater.autoInstallOnAppQuit = false;
      return false;
    }
  };
  // A notice that cannot be recorded as shown would repeat on every launch.
  // Its own key: the cached update's "ready" toast follows at once and must not replace it.
  if (save() && toast) showToast({ key: "fuse-desktop-launch", ...toast });

  let announced = null;
  let offered = null;
  let unfetched = null;
  let restarting = false;
  const installing = () => {
    state.pending.installing = true;
    save();
  };

  autoUpdater.on("update-available", ({ version }) => {
    if (announced === version) return;
    announced = version;
    showToast(CAN_INSTALL
      ? { title: `Downloading Fuse ${version}`, msg: "In the background. Keep working.", ttl: 6000 }
      : { title: `Fuse ${version} is available`, msg: "Install it from the Download page.", actionLabel: "Download", href: "/download" });
  });

  // Runs before electron-updater's own handler, which the download adds.
  autoUpdater.on("update-downloaded", ({ version }) => {
    if (!state.pending || state.pending.to !== version) state.pending = { to: version, tries: 0 };
    const retry = state.pending.tries >= MAX_INSTALL_TRIES;
    autoUpdater.autoInstallOnAppQuit = !retry;
    save();
    if (offered === version) return;
    offered = version;
    showToast({
      kind: "ok",
      title: `Fuse ${version} is ready`,
      msg: retry ? "Restart to try installing it again." : "Restart to update now, or it installs when you close Fuse.",
      actionLabel: "Restart",
      restart: true,
    });
  });

  autoUpdater.on("error", () => {
    if (!restarting) {
      // A failed download used to show nothing; the next check retries it.
      if (announced && offered !== announced && unfetched !== announced && CAN_INSTALL) {
        unfetched = announced;
        showToast({ kind: "warn", title: `Fuse ${announced} didn't download`, msg: "Fuse tries again in a few hours.", ttl: 10000 });
      }
      return;
    }
    restarting = false;
    const still = `Still on ${app.getVersion()}.`;
    showToast(autoUpdater.autoInstallOnAppQuit
      ? { kind: "err", title: "Update didn't install", msg: `${still} Fuse tries again when it closes.` }
      : { kind: "err", title: "Update didn't install", msg: `${still} Install it from the Download page.`, actionLabel: "Download", href: "/download" });
  });

  // Only an error or the quit clears `restarting`. A quit kept back (Stay in
  // the leave dialog) left Restart dead without a word; Restart stays off
  // then, since a second install call is ignored once one has run.
  const watchRestart = () => setTimeout(() => {
    if (!restarting) return;
    write("warn")("Restart to update did not quit");
    showToast({ kind: "warn", title: "Fuse didn't restart", msg: `Still on ${app.getVersion()}. Close Fuse to finish updating.` });
  }, RESTART_TIMEOUT_MS);

  restartToUpdate = () => {
    if (!offered || restarting) return;
    restarting = true;
    installing();
    if (process.platform !== "linux" || process.env.APPIMAGE) {
      autoUpdater.quitAndInstall(true, true);
      watchRestart();
      return;
    }
    autoUpdater.quitAndInstall(true, false);
    // The .deb install runs synchronously; a failed one already fired "error".
    // The quit itself waits for the next tick, so app.relaunch() is still in time.
    if (!restarting) return;
    watchRestart();
    if (relaunchFromSystemd()) write("info")("Relaunching through systemd-run --user");
    else if (!setuidSandboxHelper() || userNamespacesUsable()) {
      write("warn")("systemd-run --user failed; relaunching through app.relaunch()");
      app.relaunch();
    } else {
      // The setuid sandbox under no_new_privs: app.relaunch() would abort.
      write("warn")("systemd-run --user failed and app.relaunch() cannot start the sandbox here; not relaunching");
      dialog.showMessageBoxSync({ type: "info", message: `Fuse ${offered} is installed`, detail: "Open Fuse again to use it." });
    }
  };

  // Registered before any download, so it runs ahead of the install on quit.
  app.on("quit", (_event, exitCode) => {
    if (offered && exitCode === 0 && autoUpdater.autoInstallOnAppQuit) installing();
  });

  const check = () => autoUpdater.checkForUpdates().catch(() => {});
  check();
  setInterval(check, UPDATE_CHECK_MS);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    // Lets the site drop its own sidebar brand in favour of this title bar.
    if (CUSTOM_FRAME) app.userAgentFallback = `${app.userAgentFallback} FuseDesktop/${app.getVersion()}`;
    loadFrameTheme();
    createWindow();

    if (app.isPackaged) startUpdater();

    app.on("activate", () => {
      if (!win) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}

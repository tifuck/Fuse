// Web search setup. Web search leaves through machines the user picks
// (Settings › Web search); until they pick one it is off. A card asks once,
// a while after the chat opens, and opens the setup window: a list of the
// user's connectors to tick, or a way to add one when there are none.
// Dismissed (Not now, its X, the window's Esc) it stays away in this browser
// for a day. The web search button opens the window whenever it is clicked.
// The theme draws the window (ui.webSetupWindowHtml).
import { S } from './state.js';
import { SNOOZE_MS, needsIp, saveLabel, setupRows, shouldPrompt, snoozeKey, suggested } from './websetup-core.js';

const ASK_AFTER_MS = 1500;

export function createWebSetup(ctx) {
  const { net, panels, composer, ui } = ctx;
  let win = null;
  let card = null;

  const key = () => snoozeKey(S.config.uid);
  const part = (name) => win.el.querySelector("[data-websetup-" + name + "]");
  const boxes = () => [...win.el.querySelectorAll("[data-websetup-row] [data-websetup-check]")];

  function snoozedUntil() {
    try { return Number(localStorage.getItem(key())) || 0; } catch (_) { return 0; }
  }

  function snooze() {
    try { localStorage.setItem(key(), String(Date.now() + SNOOZE_MS)); } catch (_) {}
    card?.close();
    card = null;
  }

  // Dismissed in another tab: this one stops asking too.
  window.addEventListener("storage", (event) => {
    if (event.key === key() && Number(event.newValue) > Date.now()) {
      card?.close();
      card = null;
    }
  });

  const overview = () => net.api("/settings/web");

  async function ask() {
    if (!shouldPrompt(S.config.web, snoozedUntil(), Date.now()) || card || win || !window.fuseUI) return;
    let data;
    try { data = await overview(); } catch (_) { return; } // best effort: the next load asks again
    if (data.available || win) return;
    const has = setupRows(data).length > 0;
    card = window.fuseUI.toast({
      key: "websetup",
      title: has ? "Set up web search" : "Web search needs a machine",
      msg: has
        ? "Pick which of your machines web searches go out through. Until then, web search is off."
        : "Web searches go out through one of your machines. Add one with the Fuse connector to turn web search on.",
      actions: [
        has ? { label: "Set up", run: () => { card = null; open(); } }
          : { label: "Add a machine", run: () => { card = null; window.location.href = "/hosts"; } },
        { label: "Not now", run: snooze },
      ],
      onDismiss: snooze,
    });
  }

  function paint() {
    const ticked = boxes().filter((box) => box.checked).length;
    const save = part("save");
    if (save) {
      save.textContent = saveLabel(ticked);
      save.disabled = win.busy || !ticked;
    }
  }

  function showError(text) {
    const el = part("error");
    el.textContent = text;
    el.hidden = !text;
  }

  function draw(data, ticks, reading = false) {
    const rows = setupRows(data);
    const wrap = document.createElement("template");
    wrap.innerHTML = ui.webSetupWindowHtml(rows, { reading });
    const el = wrap.content.firstElementChild;
    const on = new Set(ticks || suggested(rows));
    el.querySelectorAll("[data-websetup-check]").forEach((box) => {
      box.checked = on.has(Number(box.closest("[data-websetup-row]").getAttribute("data-websetup-row")));
    });
    if (win) {
      // Redrawn in place: the open modal, its focus trap and listeners stay.
      win.el.replaceChildren(...el.childNodes);
    } else {
      win = { el, busy: false, touched: false };
      el.addEventListener("click", onClick);
      el.addEventListener("change", () => { win.touched = true; paint(); });
      // Esc is a dismissal like Not now.
      el.addEventListener("cancel", (event) => { event.preventDefault(); dismiss(); });
      S.root.append(el);
      el.showModal();
    }
    win.data = data;
    win.rows = rows;
    paint();
    (part("save") || part("add"))?.focus({ preventScroll: true });
  }

  // The rows are drawn at once; the public IPs that tell networks apart are
  // read through the connectors meanwhile, and the ticks follow them unless
  // the user has already changed one.
  async function readIps() {
    const mine = win;
    let data = null;
    try { data = await net.api("/settings/web/detect", { method: "POST", body: "{}" }); } catch (_) {}
    if (win !== mine || mine.busy) return;
    draw(data || mine.data, mine.touched ? ticked() : null);
  }

  const ticked = () => boxes().filter((box) => box.checked)
    .map((box) => Number(box.closest("[data-websetup-row]").getAttribute("data-websetup-row")));

  async function open() {
    card?.close();
    card = null;
    if (win) return;
    let data;
    try { data = await overview(); } catch (error) {
      window.fuseUI?.toast({ kind: "err", title: "Could not open web search setup", msg: error?.message || "The request failed." });
      return;
    }
    if (win) return;
    const reading = needsIp(setupRows(data));
    draw(data, null, reading);
    if (reading) readIps();
  }

  async function save() {
    const mine = win;
    if (mine.busy) return;
    mine.busy = true;
    paint();
    showError("");
    let data;
    try {
      data = await net.api("/settings/web", { method: "POST", body: JSON.stringify({ machines: ticked() }) });
    } catch (error) {
      if (win !== mine) return;
      mine.busy = false;
      paint();
      showError("Could not save. " + (error?.message || "The request failed") + ".");
      return;
    }
    if (win !== mine) return;
    close();
    S.config.web = Boolean(data.available);
    if (S.config.web) {
      // Asked for it: this chat searches from the next message.
      S.searchOn = true;
      composer.persist();
      window.fuseUI?.toast({ key: "websetup", title: "Web search is on", msg: "Searches go out through the machines you picked. Change them in Settings › Web search." });
    }
    panels.syncMemoryUi();
  }

  function dismiss() {
    snooze();
    close();
  }

  function onClick(event) {
    const hit = (name) => event.target.closest("[data-websetup-" + name + "]");
    if (hit("save")) save();
    else if (hit("later")) dismiss();
    else {
      const row = hit("row");
      if (!row || event.target.closest("input, button, a, label") || String(window.getSelection())) return;
      const box = row.querySelector("[data-websetup-check]");
      if (box.disabled) return;
      box.checked = !box.checked;
      win.touched = true;
      paint();
    }
  }

  function close() {
    if (!win) return;
    const { el } = win;
    win = null;
    el.close();
    el.remove();
  }

  function start() {
    window.setTimeout(ask, ASK_AFTER_MS);
  }

  return { close, open, start };
}

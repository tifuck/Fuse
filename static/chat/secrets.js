// The Protect secrets window: a turn that found secret-looking values in the
// project's env files waits until the owner picks which of them to protect.
// One modal over the open chat; the theme draws it (ui.secretsWindowHtml).
import { S } from './state.js';
import { snapshotWork } from './sidebar/threads.js';
import { allState, decision, protectLabel, secretRows } from './secrets-core.js';

export function createSecrets(ctx) {
  const { net, ui } = ctx;
  // The open window. The values live only in its DOM text: nothing here, in
  // storage or in a log keeps them, and closing removes the DOM.
  let win = null;
  // Moved by every read and every close: an older read never opens a window.
  let asked = 0;

  const offerUrl = (id) => "/chat/threads/" + encodeURIComponent(id) + "/secrets/offer";
  const part = (name) => win.el.querySelector("[data-secrets-" + name + "]");
  const boxes = () => [...win.el.querySelectorAll("[data-secrets-row] [data-secrets-check]")];

  function paint() {
    const list = boxes();
    const ticked = list.filter((box) => box.checked).length;
    const state = allState(ticked, list.length);
    const all = part("all");
    all.checked = state === "all";
    all.indeterminate = state === "mixed";
    part("protect").textContent = protectLabel(ticked);
  }

  function setBusy(on) {
    win.busy = on;
    ["protect", "skip", "never"].forEach((name) => { part(name).disabled = on; });
  }

  function showError(text) {
    const el = part("error");
    el.textContent = text;
    el.hidden = !text;
  }

  async function decide(ids, never) {
    const mine = win;
    if (mine.busy) return;
    setBusy(true);
    showError("");
    try {
      await net.api(offerUrl(mine.thread), { method: "POST", body: JSON.stringify(decision(ids, never)) });
    } catch (error) {
      if (win !== mine) return;
      setBusy(false);
      showError("Could not save your choice. " + (error?.message || "The request failed") + ".");
      part("protect").focus({ preventScroll: true });
      return;
    }
    if (win === mine) close();
  }

  function onClick(event) {
    const hit = (name) => event.target.closest("[data-secrets-" + name + "]");
    if (hit("protect")) {
      decide(boxes().filter((box) => box.checked).map((box) => Number(box.closest("[data-secrets-row]").getAttribute("data-secrets-row"))), false);
    } else if (hit("skip")) decide([], false);
    else if (hit("never")) decide([], true);
    else {
      // A checkbox toggles itself, and a drag that selected text is not a click on its row.
      const row = hit("row");
      if (!row || event.target.closest("input, button, a, label") || String(window.getSelection())) return;
      const box = row.querySelector("[data-secrets-check]");
      box.checked = !box.checked;
      paint();
    }
  }

  function onChange(event) {
    if (event.target.matches("[data-secrets-all]")) boxes().forEach((box) => { box.checked = event.target.checked; });
    paint();
  }

  function onKey(event) {
    // Esc never closes it: the turn waits on an answer.
    if (event.key === "Escape") event.preventDefault();
    else if (event.key === "Enter" && !event.isComposing && !event.target.closest("button, a")) {
      event.preventDefault();
      part("protect").click();
    }
  }

  function open(thread, items) {
    const rows = secretRows(items);
    if (!rows.length) return;
    const wrap = document.createElement("template");
    wrap.innerHTML = ui.secretsWindowHtml(rows);
    const el = wrap.content.firstElementChild;
    win = { el, thread, busy: false };
    el.addEventListener("click", onClick);
    el.addEventListener("change", onChange);
    el.addEventListener("keydown", onKey);
    el.addEventListener("cancel", (event) => event.preventDefault());
    // A browser may still close a dialog on a repeated Esc: put it back.
    el.addEventListener("close", () => { if (win && win.el === el) el.showModal(); });
    S.root.append(el);
    // A modal dialog: the page behind is inert, focus stays inside and goes
    // back to where it was on close().
    el.showModal();
    paint();
    part("protect").focus({ preventScroll: true });
  }

  function close() {
    asked += 1;
    if (!win) return;
    const { el } = win;
    win = null;
    el.close();
    el.remove();
  }

  // Reads what this chat's turn waits on and shows or drops the window to
  // match. An open window keeps its ticks.
  async function check(id = S.currentId) {
    if (!id) return;
    const ticket = ++asked;
    let data;
    try { data = await net.api(offerUrl(id)); }
    catch (_) { return; } // best effort: the next snapshot or event asks again
    if (ticket !== asked || id !== S.currentId) return;
    const items = data && data.pending && Array.isArray(data.items) ? data.items : [];
    if (!items.length) close();
    else if (!win) open(id, items);
  }

  // A snapshot of the open chat (opened, reloaded or reconciled). Only a
  // running turn can be waiting, and no event replays a wait that began
  // before the snapshot.
  function acceptThread(data) {
    const live = snapshotWork(data).live;
    if (win && (win.thread !== data.id || !live)) close();
    if (live) check(data.id);
  }

  // The run stream: the turn started waiting ("pending") or stopped
  // ("decided" on any device, "timeout"). A replayed event is old news, so
  // the server is asked instead.
  function onEvent(event, passive) {
    if (event.status === "pending" || passive) check();
    else close();
  }

  return { acceptThread, close, onEvent };
}

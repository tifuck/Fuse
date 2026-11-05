// Picker button/sheet placement, composer hint.
import { S } from '../state.js';

const PICKER_CHEV =
  '<svg class="chat-picker-chev" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// A held toast leaves this soon at the earliest, so a fast action never
// flashes one in and out within a frame or two.
const HINT_MIN_HELD_MS = 500;

// `fault`: true for an error, "warn" for a limit or a guardrail.
const toneOf = (fault) => (fault === "warn" ? "warn" : fault ? "err" : "idle");

export function createSheet(ctx) {
  const { composer, context, list, overlays, perms, sidebar } = ctx;
  // Chat notices are the site's toasts (fuseUI.toast in app.js), keyed
  // "chat:<key>". key -> { ctrl, text, mode: "hint" | "hold" | "pin", at }.
  const toasts = new Map();

  function live(key) {
    const entry = toasts.get(key);
    if (entry && entry.ctrl.el.isConnected) return entry;
    toasts.delete(key);
    return null;
  }

  function show(key, mode, text, opts) {
    if (!window.fuseUI) return;
    const ctrl = window.fuseUI.toast({ key: "chat:" + key, msg: text, ...opts });
    window.clearTimeout(toasts.get(key)?.release);
    toasts.set(key, { ctrl, text, mode, at: Date.now() });
  }

  function drop(key) {
    const entry = live(key);
    toasts.delete(key);
    if (entry) {
      window.clearTimeout(entry.release);
      entry.ctrl.close();
    }
  }

    // Something that already happened; it times out. One toast per tone, so
  // a newer hint replaces the last instead of piling up.
    function setHint(text, fault, duration, title) {
    if (!text) return;
    const kind = toneOf(fault);
    show("hint:" + kind, "hint", text, { kind, title, ttl: duration ?? undefined });
  }

    // A toast owned by `key` (e.g. "stop:<run id>") for an action in flight:
  // it stays until releaseHint(key). Pass duration null for one that also
  // times out normally.
    function holdHint(key, text, fault = false, duration = 0, title) {
    const held = live(key);
    if (held && held.mode === "hold" && held.text === text) return;
    show(key, "hold", text, { kind: toneOf(fault), title, ttl: duration ?? undefined });
  }

    // Ends a held toast: closes it, or turns it into a timed `text`.
    function releaseHint(key, text, fault) {
    const held = key && live(key);
    if (!held || held.mode !== "hold") return;
    window.clearTimeout(held.release);
    const wait = HINT_MIN_HELD_MS - (Date.now() - held.at);
    if (wait > 0) {
      held.release = window.setTimeout(() => releaseHint(key, text, fault), wait);
      return;
    }
    if (text) show(key, "hint", text, { kind: toneOf(fault) });
    else drop(key);
  }

    function heldHints() {
    return [...toasts.keys()].filter((key) => live(key)?.mode === "hold");
  }

    // A toast that stays until the user closes it (or the chat changes). An
  // action ({label, run}) adds a button that runs it and closes the toast.
    function pinHint(key, text, action = null) {
    show(key, "pin", text, {
      kind: "warn",
      ttl: 0,
      actionLabel: action?.label,
      onAction: action ? () => { action.run(); unpinHint(key); } : undefined,
    });
  }

    function unpinHint(key) {
    [...toasts.keys()].forEach((k) => {
      if ((!key || k === key) && live(k)?.mode === "pin") drop(k);
    });
  }

    // A new chat starts clean: the last chat's toasts leave with it.
    function defaultHint() {
    [...toasts.keys()].forEach(drop);
    if (!S.allModels.length) {
      show("no-models", "pin", "No models are unlocked yet.", {
        kind: "warn",
        ttl: 0,
        actionLabel: "Open Sources",
        onAction: () => { window.location.href = "/sources"; },
      });
    }
  }

    function closePicker() {
    const wasOpen = S.pickerOpen;
    S.pickerOpen = false;
    if (S.pickerMenuEl) S.pickerMenuEl.hidden = true;
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (wasOpen) {
      overlays.armOverlayGhost(S.pickerMenuEl?.querySelector("[data-picker-close]"));
      overlays.overlayDrop("picker");
    }
  }

    // While a subagent is on screen the button names the model that subagent
    // runs on, read-only: a child's model is fixed when it is spawned.
    function agentModel() {
    if (!S.agentsOpen || !S.agentViewId) return null;
    const row = S.agentRun || S.agentsRows.find((item) => String(item.id) === S.agentViewId);
    const id = String(row?.model || "");
    if (!id) return null;
    return { model: S.modelsById.get(id) || { id, name: id, type: "" }, effort: String(row.reasoning_effort || "") };
  }

    const agentPickerKey = (agent) => agent ? agent.model.id + "\n" + agent.effort : "";

    // A subagent snapshot can bring a model or effort the button lacks.
    function syncAgentPicker() {
    if (agentPickerKey(agentModel()) !== (S.pickerHost.dataset.agentModel || "")) renderPickerButton();
  }

    function openPicker() {
    if (!S.allModels.length) return;
    const agent = agentModel();
    if (agent) {
      setHint(`This subagent runs on ${agent.model.name}, set when it was spawned. Go back to the main agent to change the chat's model.`);
      return;
    }
    perms.setPermsOpen(false);
    S.pickerOpen = true;
    overlays.overlayPush("picker");
    S.pickerFilter = "";
    S.pickerPinId = S.modelId;
    const items = list.filteredModels();
    S.pickerIndex = Math.max(
      0,
      items.findIndex((model) => model.id === S.modelId)
    );
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    const search = S.pickerMenuEl && S.pickerMenuEl.querySelector(".chat-picker-search");
    if (S.pickerMenuEl) S.pickerMenuEl.hidden = false;
    if (btn) btn.setAttribute("aria-expanded", "true");
    // Focus would pop the touch keyboard on mobile; the sheet stays keyboard-free until tapped.
    if (search) {
      search.value = "";
      if (!S.pickerSheetQuery.matches) search.focus();
      else S.pickerMenuEl.focus({ preventScroll: true });
    }
    list.renderPickerList();
    placePicker();
  }

    // Flush with the composer's right edge (the send button), wherever the
    // model button sits in the row.
    function placePicker() {
    const edge = S.form?.querySelector(".chat-send") || S.pickerHost.querySelector(".chat-picker-btn");
    composer.placeComposerPop(S.pickerMenuEl, edge, 26 * 16, true);
  }

    function renderPickerButton() {
    const agent = agentModel();
    const model = agent ? agent.model : context.currentModel();
    S.pickerHost.dataset.agentModel = agentPickerKey(agent);
    S.pickerHost.innerHTML = "";
    // On mobile the sheet lives on the chat root, outside pickerHost; drop it
    // too or every re-render leaves an orphaned sheet behind.
    S.pickerMenuEl?.remove();
    S.pickerMenuEl = null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chat-picker-btn";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", S.pickerOpen ? "true" : "false");
    if (!model) {
      btn.disabled = true;
      btn.innerHTML = "<span>No models</span>";
      S.pickerHost.appendChild(btn);
      return;
    }
    const effort = agent ? agent.effort : list.currentEffort();
    const think = agent && !effort ? "" : list.effortLabel(effort, model);
    btn.innerHTML = `${sidebar.providerLogo(model.type)}<span></span><small></small>${agent ? "" : PICKER_CHEV}`;
    btn.querySelector("span").textContent = model.name;
    btn.querySelector("small").textContent = think;
    btn.title = (agent ? "Subagent model: " : "") + (think ? model.id + " · " + think : model.id);
    if (agent) btn.setAttribute("aria-haspopup", "false");
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (S.pickerOpen) closePicker();
      else openPicker();
    });
    S.pickerHost.appendChild(btn);
    S.pickerMenuEl = renderPickerSheet();
    if (S.pickerSheetQuery.matches) {
      // A fixed sheet cannot live here: transformed/backdrop-filtered composer
      // ancestors (glass dock) would turn it into a contained, clipped box.
      S.root.appendChild(S.pickerMenuEl);
    } else {
      S.pickerHost.appendChild(S.pickerMenuEl);
    }
    S.pickerMenuEl.hidden = !S.pickerOpen;
    if (S.pickerOpen) {
      list.renderPickerList();
      placePicker();
    }
  }

    function renderPickerSheet() {
    const menu = document.createElement("div");
    menu.className = "chat-picker-menu" + (S.pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Model picker");
    menu.tabIndex = -1;
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Model</p><button type="button" class="chat-menu-close chat-picker-done" data-picker-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" placeholder="Search models" aria-label="Search models" aria-controls="chat-picker-list" aria-autocomplete="list"><kbd class="chat-picker-kbd" aria-hidden="true">esc</kbd></div>' +
      '<div class="chat-picker-list" id="chat-picker-list" role="listbox" aria-label="Models"></div>' +
      '<div class="chat-picker-thinking-host"></div>';
    overlays.attachSheetSwipe(menu, ".chat-picker-head", () => menu.classList.contains("is-sheet"), closePicker);
    const search = menu.querySelector(".chat-picker-search");
    search.addEventListener("input", () => {
      S.pickerFilter = search.value;
      S.pickerIndex = 0;
      list.renderPickerList();
    });
    search.addEventListener("keydown", (event) => {
      const items = list.filteredModels();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        S.pickerIndex = Math.min(items.length - 1, S.pickerIndex + 1);
        list.renderPickerList();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        S.pickerIndex = Math.max(0, S.pickerIndex - 1);
        list.renderPickerList();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (items[S.pickerIndex]) list.selectModel(items[S.pickerIndex].id, true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        closePicker();
        const btn = S.pickerHost.querySelector(".chat-picker-btn");
        if (btn) btn.focus();
      }
    });
    menu.addEventListener("click", (event) => {
      event.stopPropagation();
      const closeBtn = event.target.closest("[data-picker-close]");
      if (closeBtn) {
        event.preventDefault();
        closeBtn.blur();
        closePicker();
        const btn = S.pickerHost.querySelector(".chat-picker-btn");
        if (btn && !S.pickerSheetQuery.matches) btn.focus();
        return;
      }
      const star = event.target.closest("[data-star]");
      if (star) {
        event.preventDefault();
        list.toggleFavorite(star.getAttribute("data-star"));
        // The re-render drops the clicked star; keep typing and arrows working.
        if (!S.pickerSheetQuery.matches) menu.querySelector(".chat-picker-search")?.focus();
        return;
      }
      const target = event.target.closest("button[data-id]");
      if (target) {
        const id = target.getAttribute("data-id");
        list.selectModel(id, id === S.modelId);
      }
    });
    menu.addEventListener("input", (event) => {
      const slider = event.target.closest("[data-effort-slider]");
      if (!slider) return;
      const effort = list.modelEfforts()[Number(slider.value)];
      if (effort) list.setEffort(effort);
    });
    return menu;
  }

    // A gesture over a nested scroller (code pane, thinking peek, table) that
  // can still move that way scrolls it, not the transcript, and must not
  // break the follow.
  function innerScrolls(target, dy) {
    for (let el = target instanceof Element ? target : null; el && el !== S.thread; el = el.parentElement) {
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy !== "auto" && oy !== "scroll") continue;
      if (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
    }
    return false;
  }

  return {
    closePicker,
    defaultHint,
    heldHints,
    holdHint,
    innerScrolls,
    pinHint,
    placePicker,
    releaseHint,
    renderPickerButton,
    setHint,
    syncAgentPicker,
    unpinHint,
  };
}

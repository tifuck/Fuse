// Model list, effort mapping, selection.
import { S } from '../state.js';
import { defaultReasoningFor, resolveListedModel } from '../state.js';
import { escapeHtml, formatTokens } from '../core.js';

const EFFORT_LABELS = {
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

const IMAGE_ICON =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6.6" r="1.2" fill="currentColor"/><path d="M2.8 12l3.6-3.6 2.4 2.4 1.6-1.6 3 3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';

const starIcon = (on) =>
  `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 1.9l1.85 3.75 4.15.6-3 2.92.7 4.13L8 11.35 4.3 13.3l.7-4.13-3-2.92 4.15-.6z" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>`;

let favoritesSaving = Promise.resolve();

export function createList(ctx) {
  const { composer, context, net, sheet, sidebar } = ctx;
    function modelEfforts() {
    const model = context.currentModel();
    return (model && model.reasoning) || defaultReasoningFor(model && model.type);
  }

    function defaultEffort() {
    const model = context.currentModel();
    const efforts = modelEfforts();
    const wanted = (model && model.reasoningDefault) || "";
    if (wanted && efforts.includes(wanted)) return wanted;
    if (efforts.includes("medium")) return "medium";
    // A model without levels has no effort to show or send.
    return efforts[0] || "";
  }

    // The open chat's effort wins; the per-model pick is only the default
    // for a chat that has none for this model yet.
    function currentEffort() {
    const efforts = modelEfforts();
    const chat = S.threadEffort;
    if (chat && chat.model === S.modelId && efforts.includes(chat.value)) return chat.value;
    const stored = S.effortByModel[S.modelId];
    if (stored && efforts.includes(stored)) return stored;
    return defaultEffort();
  }

    function effortLabel(effort, model = context.currentModel()) {
    // Older GLM only switches thinking on or off; GLM-5.3+ has real levels.
    if (model && model.type === "zai" && !(model.reasoning || []).includes("max")) return effort === "none" ? "Off" : "On";
    return EFFORT_LABELS[effort] || effort;
  }

    function setEffort(value) {
    const efforts = modelEfforts();
    if (!efforts.includes(value)) return;
    const changed = value !== currentEffort();
    S.effortByModel[S.modelId] = value;
    S.threadEffort = { model: S.modelId, value };
    if (changed) {
      context.warnEffortCacheReset(value);
      composer.persist();
    }
    const model = context.currentModel();
    const label = effortLabel(value);
    const small = S.pickerHost.querySelector(".chat-picker-btn small");
    if (small) small.textContent = label;
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    if (btn && model) btn.title = model.id + " · " + label;
    syncEffortSlider(value);
  }

    function filteredModels() {
    const q = S.pickerFilter.trim().toLowerCase();
    const pinned = S.modelsById.get(S.pickerPinId);
    // Starred models lead, in catalog order; the open model's family follows.
    const starred = S.allModels.filter((m) => S.favorites.has(m.id));
    let list = S.allModels.filter((m) => !S.favorites.has(m.id));
    if (pinned && !S.favorites.has(pinned.id)) {
      const same = list.filter((m) => m.type === pinned.type && m.id !== pinned.id);
      const rest = list.filter((m) => m.type !== pinned.type);
      list = [pinned, ...same, ...rest];
    }
    list = [...starred, ...list];
    if (!q) return list;
    return list.filter(
      (model) =>
        model.name.toLowerCase().includes(q) ||
        model.id.toLowerCase().includes(q) ||
        model.title.toLowerCase().includes(q)
    );
  }

    function renderPickerList() {
    const menu = (S.pickerMenuEl || S.pickerHost).querySelector(".chat-picker-list");
    if (!menu) return;
    renderPickerThinking();
    const items = filteredModels();
    if (S.pickerIndex >= items.length) S.pickerIndex = Math.max(0, items.length - 1);
    const search = (S.pickerMenuEl || S.pickerHost).querySelector(".chat-picker-search");
    if (!items.length) {
      menu.innerHTML = '<p class="chat-picker-empty">No models match.</p>';
      search?.removeAttribute("aria-activedescendant");
      return;
    }
    const groups = [];
    items.forEach((model, index) => {
      const fav = S.favorites.has(model.id);
      const type = fav ? "favorites" : model.type;
      let group = groups[groups.length - 1];
      if (!group || group.type !== type) {
        group = { type, fav, title: fav ? "Favorites" : model.title, items: [] };
        groups.push(group);
      }
      group.items.push({ model, index });
    });
    menu.innerHTML = groups
      .map(
        (group) =>
          // The provider mark sits once on the group label; rows carry the
          // name and context window only, so the list stays dense.
          // Favorites mix providers, so there each row names its provider
          // under the model name.
          `<div class="chat-picker-group${group.fav ? " is-favorites" : ""}" role="group" aria-labelledby="chat-pg-${escapeHtml(group.type)}"><p id="chat-pg-${escapeHtml(group.type)}">${group.fav ? starIcon(true) : sidebar.providerLogo(group.type)}<span>${escapeHtml(group.title)}</span></p>${group.items
            .map(({ model, index }) => {
              const selected = model.id === S.modelId;
              const ctx = model.contextWindow ? formatTokens(model.contextWindow) : "";
              const row =
                `<button type="button" role="option" id="chat-pm-${index}" tabindex="-1" data-id="${escapeHtml(model.id)}" aria-selected="${
                  selected ? "true" : "false"
                }" class="${selected ? "is-selected" : ""}${index === S.pickerIndex ? " is-active" : ""}">` +
                (group.fav
                  ? `<span class="chat-picker-fav-name"><b>${escapeHtml(model.name)}</b><small>${sidebar.providerLogo(model.type)}${escapeHtml(model.title)}</small></span>`
                  : `<span>${escapeHtml(model.name)}</span>`) +
                (model.image ? `<i class="chat-picker-image" title="Reads images">${IMAGE_ICON}<span class="visually-hidden">, reads images</span></i>` : "") +
                (ctx ? `<small class="chat-picker-meta" title="Context window">${ctx}</small>` : "") +
                `</button>`;
              const label = (group.fav ? "Unfavorite " : "Favorite ") + model.name;
              const star =
                `<button type="button" class="chat-picker-star${group.fav ? " is-on" : ""}" data-star="${escapeHtml(model.id)}" tabindex="-1" aria-pressed="${group.fav}" aria-label="${escapeHtml(label)}" title="${group.fav ? "Unfavorite" : "Favorite"}">${starIcon(group.fav)}</button>`;
              return `<div class="chat-picker-row" role="none">${row}${star}</div>`;
            })
            .join("")}</div>`
      )
      .join("");
    // Arrow keys move a highlight while focus stays in the search box.
    search?.setAttribute("aria-activedescendant", "chat-pm-" + S.pickerIndex);
  }

    // Applies the account's favorites from the server, unless a toggle of
    // this tab's is still saving (its own answer lands after it).
    function acceptFavorites(ids) {
    if (!Array.isArray(ids) || S.favoritesPending) return;
    S.favorites = new Set(ids);
    if (S.pickerOpen) renderPickerList();
  }

    // Optimistic; saves are chained so the last answer is the final state.
    function toggleFavorite(id) {
    if (!S.modelsById.has(id)) return;
    const on = !S.favorites.has(id);
    const activeId = filteredModels()[S.pickerIndex]?.id;
    const flip = (value) => {
      if (value) S.favorites.add(id);
      else S.favorites.delete(id);
      const index = filteredModels().findIndex((model) => model.id === activeId);
      if (index >= 0) S.pickerIndex = index;
      if (S.pickerOpen) renderPickerList();
    };
    flip(on);
    S.favoritesPending++;
    favoritesSaving = favoritesSaving
      .then(() => net.api("/chat/favorites", { method: "PUT", body: JSON.stringify({ model: id, on }) }))
      .then(
        (data) => { S.favoritesPending--; acceptFavorites(data.models); },
        (err) => { S.favoritesPending--; flip(!on); sheet.setHint(err?.message || "Couldn't save the favorite.", true); }
      );
  }

    function renderPickerThinking() {
    const host = (S.pickerMenuEl || S.pickerHost).querySelector(".chat-picker-thinking-host");
    if (!host) return;
    const model = context.currentModel();
    const efforts = modelEfforts();
    const effort = currentEffort();
    if (!efforts.length) {
      host.innerHTML = "";
      return;
    }
    const index = Math.max(0, efforts.indexOf(effort));
    const ticks = efforts
      .map(
        (_, i) =>
          `<i style="left:${
            efforts.length > 1 ? ((i / (efforts.length - 1)) * 100).toFixed(4) : 50
          }%"></i>`
      )
      .join("");
    host.innerHTML =
      // One row: label, slider, value.
      `<section class="chat-picker-thinking" aria-label="Thinking mode">` +
      `<p class="chat-picker-thinking-label">Thinking</p>` +
      `<div class="chat-picker-efforts">` +
      `<div class="chat-picker-efforts-ticks" aria-hidden="true">${ticks}</div>` +
      `<input type="range" class="chat-picker-efforts-slider" data-effort-slider min="0" max="${
        efforts.length - 1
      }" step="1" value="${index}" aria-label="Thinking mode" aria-valuetext="${escapeHtml(
        effortLabel(effort)
      )}">` +
      `</div><small class="chat-picker-thinking-value">${escapeHtml(effortLabel(effort))}</small></section>`;
    syncEffortSlider(effort);
  }

    function syncEffortSlider(effort) {
    const menu = S.pickerMenuEl || S.pickerHost;
    const slider = menu.querySelector("[data-effort-slider]");
    if (!slider) return;
    const efforts = modelEfforts();
    const index = Math.max(0, efforts.indexOf(effort));
    if (Number(slider.value) !== index) slider.value = index;
    const span = efforts.length > 1 ? index / (efforts.length - 1) : 1;
    slider.style.setProperty("--effort-fill", (span * 100).toFixed(2) + "%");
    slider.setAttribute("aria-valuenow", String(index));
    slider.setAttribute("aria-valuetext", effortLabel(efforts[index]));
    const value = menu.querySelector(".chat-picker-thinking-value");
    if (value) value.textContent = effortLabel(efforts[index]);
  }

    function selectModel(id, dismiss) {
    id = resolveListedModel(id) || id;
    if (!S.modelsById.has(id)) return;
    if (id !== S.modelId && !net.requireLive()) return;
    if (id !== S.modelId) context.warnModelCacheReset(id);
    S.modelId = id;
    context.warnEffortCacheReset(currentEffort());
    S.pickerIndex = Math.max(0, filteredModels().findIndex((model) => model.id === id));
    composer.persist();
    if (dismiss || !S.pickerOpen) {
      sheet.closePicker();
      sheet.renderPickerButton();
      const btn = S.pickerHost.querySelector(".chat-picker-btn");
      if (btn) btn.focus();
    } else {
      S.pickerFilter = "";
      const model = context.currentModel();
      const effort = currentEffort();
      const btn = S.pickerHost.querySelector(".chat-picker-btn");
      const logo = btn && btn.querySelector(".provider-logo");
      if (logo) logo.outerHTML = sidebar.providerLogo(model.type);
      if (btn) {
        btn.querySelector("span").textContent = model.name;
        btn.querySelector("small").textContent = effortLabel(effort);
        btn.title = effort ? model.id + " · " + effortLabel(effort) : model.id;
      }
      const search = S.pickerMenuEl && S.pickerMenuEl.querySelector(".chat-picker-search");
      if (search) search.value = "";
      renderPickerList();
      if (search && !S.pickerSheetQuery.matches) search.focus();
    }
    composer.syncComposer();
    context.warnTextOnlyModel();
    if (S.liveMeter) {
      const changed = S.liveMeter.model !== id;
      S.liveMeter = { ...S.liveMeter, model: id, limit: context.modelWindow() };
      if (changed) {
        S.liveMeter.estimated = true;
        S.liveMeter.provider_usage = null;
        delete S.liveMeter.calibration;
        if (S.liveMeter.raw_parts) {
          S.liveMeter.used = S.liveMeter.raw_parts.total;
          S.liveMeter.parts = S.liveMeter.raw_parts;
          S.liveMeter.basis = "payload_chars_v1";
          delete S.liveMeter.raw_parts;
        }
      }
    }
    S.meterAnchor = null;
    context.syncContextMeter();
  }

  return {
    acceptFavorites,
    currentEffort,
    effortLabel,
    filteredModels,
    modelEfforts,
    renderPickerList,
    selectModel,
    setEffort,
    toggleFavorite,
  };
}

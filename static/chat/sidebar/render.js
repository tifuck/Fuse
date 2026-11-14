// History list rendering, tabs, search.
import { S, draftKey } from '../state.js';
import { escapeHtml } from '../core.js';

function runningFirst(a, b) {
  const ra = Number(Boolean(a.running));
  const rb = Number(Boolean(b.running));
  if (ra !== rb) return rb - ra;
  return String(b.updated_at).localeCompare(String(a.updated_at));
}

function pinnedFirst(a, b) {
  return (Number(a.pin_order) || 0) - (Number(b.pin_order) || 0);
}

// Pinned rows leave the timeline for their own section at the top, in the
// order drag-and-drop wrote. Everything else keeps the running-first order.
function splitPinned(items) {
  return {
    pinned: items.filter((item) => item.pinned).sort(pinnedFirst),
    rest: items.filter((item) => !item.pinned),
  };
}

// Sorts unsent drafts above every dated row.
const DRAFT_AT = "9999-12-31T00:00:00Z";

function historyIcons() {
  return {
    folder:
      '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    chat:
      '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3.2 3.6h9.6v7.2H6.4L3.2 13.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  };
}

export function createRender(ctx) {
  const { composer, net, overlays, projects, spinners, sync, threads, ui } = ctx;
    function providerLogo(type) {
    const src = S.logoByType.get(type) || "/static/icon.svg";
    return `<img class="provider-logo" src="${escapeHtml(src)}" alt="" aria-hidden="true" draggable="false">`;
  }

    function setHistoryTab(tab, opts) {
    const next = tab === "projects" ? "projects" : "chats";
    const persist = !opts || opts.persist !== false;
    const silent = Boolean(opts && opts.silent);
    if (next !== S.historyTab) S.showArchived = false;
    S.historyTab = next;
    if (persist) {
      try { localStorage.setItem(S.STORE_TAB, next); } catch (_) {}
    }
    syncHistoryChrome();
    if (!silent) renderHistory();
  }

    function syncHistoryChrome() {
    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const on = btn.getAttribute("data-history-tab") === S.historyTab;
      btn.setAttribute("aria-selected", String(on));
      btn.classList.toggle("is-on", on);
    });
    syncHistoryUnread();
    spinners.syncRunSpinners();
    const heading = S.root.querySelector("[data-history-heading]");
    if (heading) heading.textContent = S.historyTab === "projects" ? "Projects" : "Chats";
    const newLabel = S.root.querySelector("[data-chat-new-label]");
    const newBtn = S.root.querySelector("[data-chat-new]");
    const projectLabel = S.historyTab === "projects";
    if (newLabel) newLabel.textContent = projectLabel ? "New project" : "New chat";
    if (newBtn) {
      newBtn.setAttribute("aria-label", projectLabel ? "New project session" : "New chat");
      newBtn.title = projectLabel ? "New project session" : "New chat";
      if (projectLabel) newBtn.setAttribute("aria-haspopup", "dialog");
      else newBtn.removeAttribute("aria-haspopup");
    }
    const filters = S.root.querySelector("[data-project-filters]");
    if (filters) {
      filters.hidden = S.historyTab !== "projects" || Boolean(S.historyQuery.trim()) || S.showArchived;
    }
    projects.fillProjectFilterOptions();
    composer.syncComposer();
  }

    // Unread counts for the tab switch: a finished conversation badges its
  // tab so the other tab's results are visible before switching.
  function syncHistoryUnread() {
    const counts = { chats: 0, projects: 0 };
    S.threads.forEach((item) => {
      if (!item.unread || item.id === S.currentId) return;
      counts[item.kind === "project" ? "projects" : "chats"] += 1;
    });
    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const badge = btn.querySelector("[data-history-unread]");
      if (!badge) return;
      const count = counts[tab] || 0;
      badge.hidden = count === 0 || tab === S.historyTab;
      badge.textContent = count > 9 ? "9+" : String(count);
    });
  }

    // Archived threads belong to the tab of their kind: project sessions under
  // Projects, everything else under Chats.
  function archivedForTab() {
    const projectTab = S.historyTab === "projects";
    return S.archivedThreads.filter((item) => (item.kind === "project") === projectTab);
  }

    function archivedProjects() {
    return S.historyTab === "projects" ? S.projects.filter((item) => item.archived) : [];
  }

    function filteredHistory() {
    const source = S.showArchived ? archivedForTab() : S.threads.filter((item) => item.kind !== "project");
    const q = S.historyQuery.trim().toLowerCase();
    if (!q) return [...source].sort(runningFirst);
    if (S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q) {
      return [...S.searchThreads, ...S.searchArchived].sort(runningFirst);
    }
    return [...source].sort(runningFirst).filter((item) => (item.title || "").toLowerCase().includes(q));
  }

    // Rows are data; the theme draws them (ui.historyListHtml).
  function threadBlock(item, grouped = false) {
    const project = item.kind === "project";
    const running = spinners.threadIsRunning(item);
    const unread = Boolean(item.unread) && item.id !== S.currentId;
    let title = item.title || "New chat";
    if (project && item.project_name && title === item.project_name) title = "New chat";
    return { type: "thread", item, row: {
      on: item.id === S.currentId, project, running, unread, grouped, title,
      chip: searchingChip(item),
      meta: project && !grouped ? { name: item.project_name || "Project", host: item.host_alias || "" } : null,
      spin: "h-" + item.id,
      age: spinners.ageInfo(item, unread),
      icon: project ? projects.projectIcon({ color: item.project_color, logo_url: item.project_logo_url }) : "",
    } };
  }

    // Unsent new chats that have text or files in their box (null key: a
  // plain chat, "p:<host>:<project>": a project session). They live only in
  // this tab, like every other draft.
  function draftItems(projectTab) {
    const items = [];
    new Set([...S.drafts.keys(), ...S.draftFiles.keys()]).forEach((key) => {
      const text = String(S.drafts.get(key) || "").trim();
      if (!text && !(S.draftFiles.get(key) || []).length) return;
      const title = text.split("\n")[0] || "Attachment";
      if (key === null) {
        if (!projectTab) items.push({ draft: "", title, updated_at: DRAFT_AT });
        return;
      }
      if (!projectTab || !String(key).startsWith("p:")) return;
      const [, hostId, projectId] = key.split(":");
      const info = projects.resolveProjectInfo(hostId, projectId);
      if (!info) return;
      items.push({
        draft: key, title, kind: "project", updated_at: DRAFT_AT,
        host_id: info.host_id, project_id: info.project_id, project_name: info.project_name,
        host_alias: info.host_alias, project_color: info.color, project_logo_url: info.logo_url,
      });
    });
    return items;
  }

    function draftBlock(item, grouped = false) {
    const project = item.kind === "project";
    return { type: "draft", item, row: {
      on: !S.currentId && (draftKey() ?? "") === item.draft, project, grouped,
      meta: project && !grouped ? { name: item.project_name || "Project", host: item.host_alias || "" } : null,
      icon: project ? projects.projectIcon({ color: item.project_color, logo_url: item.project_logo_url }) : "",
    } };
  }

    function sessionBlock(item, grouped = false) {
    return item.draft === undefined ? threadBlock(item, grouped) : draftBlock(item, grouped);
  }

  const kicker = (text) => ({ type: "kicker", text });
  const empty = (text) => ({ type: "empty", text });
  const SEP = { type: "sep" };

    function draftMenu(anchor, key, at) {
    overlays.showPop(anchor, [{
      label: "Discard draft",
      danger: true,
      run: () => {
        const slot = key || null;
        S.drafts.delete(slot);
        S.draftFiles.delete(slot);
        if (!S.currentId && draftKey() === slot) {
          S.input.value = "";
          S.pendingAttachments = [];
          composer.renderAttachList();
          composer.resizeInput();
          composer.syncEmptyComposer();
        }
        renderHistory();
      },
    }], at);
  }

    function searchingChip(item) {
    const searching = Boolean(S.historyQuery.trim());
    if (!searching) return "";
    if (item.archived) return "Archived";
    if (item.pinned) return "Pinned";
    return "";
  }

    function syncHistoryFooter() {
    // A repaint replaced the row nodes; put the open menu's flag back.
    if (S.popRowKey && S.historyList) {
      const [attr, key] = S.popRowKey;
      for (const el of S.historyList.querySelectorAll("[data-history-item]")) {
        if (el.getAttribute(attr) === key) {
          el.classList.add("is-menu");
          el.querySelector("[data-row-more]")?.setAttribute("aria-expanded", "true");
          break;
        }
      }
    }
    const footer = S.root.querySelector("[data-history-footer]");
    if (!footer) return;
    footer.hidden = false;
    spinners.syncRunSpinners();
  }

    function renderHistory() {
    if (!S.historyList) return;
    if (S.renaming) return;
    // A repaint would rebuild the nodes under an active pin drag; the drop
    // handler re-renders from the saved order.
    if (S.pinDrag && S.pinDrag.dragging) return;
    const searching = Boolean(S.historyQuery.trim());
    syncHistoryChrome();
    if (S.historyKicker) {
      if (searching || S.showArchived) {
        S.historyKicker.textContent = searching ? "Results" : "Archived";
        S.historyKicker.hidden = false;
      } else {
        S.historyKicker.hidden = true;
      }
    }
    const archived = archivedForTab();
    if (S.archivedBtn) {
      S.archivedBtn.hidden = searching || (archived.length === 0 && archivedProjects().length === 0 && !S.showArchived);
      S.archivedBtn.classList.toggle("is-on", S.showArchived);
      S.archivedBtn.setAttribute("aria-pressed", String(S.showArchived));
      const label = S.archivedBtn.querySelector("[data-history-archived-label]");
      const count = S.archivedBtn.querySelector("[data-history-archived-count]");
      if (label) label.textContent = S.showArchived ? (S.historyTab === "projects" ? "Back to projects" : "Back to chats") : "Archived";
      if (label) S.archivedBtn.title = label.textContent;
      if (count) count.textContent = S.showArchived ? "" : String(archived.length + archivedProjects().length);
    }
    if (searching) {
      const q = S.historyQuery.trim().toLowerCase();
      const nativeHits = S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q
        ? S.searchProjects
        : S.projects.filter((item) =>
            (item.name || "").toLowerCase().includes(q) ||
            (item.host_alias || "").toLowerCase().includes(q) ||
            (item.remote_path || "").toLowerCase().includes(q)
          );
      const chats = (S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q
        ? [...S.searchThreads, ...S.searchArchived]
        : [...filteredHistory(), ...S.threads.filter((item) => item.kind === "project" && (item.title || "").toLowerCase().includes(q))]
      ).sort(runningFirst);
      const threadProjectKeys = new Set(
        chats.filter((item) => item.kind === "project").map((item) => projects.projectKey(item.host_id, item.project_id))
      );
      const unusedHits = nativeHits.filter((item) => !threadProjectKeys.has(projects.projectKey(item.host_id, item.id)));
      const blocks = chats.map((item) => threadBlock(item));
      if (unusedHits.length) {
        if (chats.length) blocks.push(kicker("Projects"));
        blocks.push(...unusedHits.map(projects.projectBlock));
      }
      paint(blocks.length ? blocks : [empty("No chats or projects match your search.")]);
      return;
    }
    if (S.showArchived) {
      const shelved = archivedProjects();
      const blocks = [];
      if (shelved.length) {
        blocks.push(kicker("Projects"), ...shelved.map(projects.projectBlock));
        if (archived.length) blocks.push(kicker("Sessions"));
      }
      blocks.push(...archived.map((item) => threadBlock(item)));
      paint(blocks.length ? blocks : [empty(S.historyTab === "projects" ? "No archived projects or sessions." : "No archived chats.")]);
      return;
    }
    if (S.historyTab === "projects") {
      const sessions = projects.projectSessionList(draftItems(true));
      const { pinned, rest } = splitPinned(sessions);
      if (!sessions.length) {
        paint([empty("No project sessions yet. Use New project to start one.")]);
        return;
      }
      const blocks = [];
      if (pinned.length) blocks.push(kicker("Pinned"), ...pinned.map((item) => threadBlock(item)), ...(rest.length ? [SEP] : []));
      if (S.projectSort === "project") {
        // Drafts sit directly under the pinned section, above every group.
        blocks.push(...rest.filter((item) => item.draft !== undefined).map((item) => sessionBlock(item)));
        projects.groupedProjectSessions(rest.filter((item) => item.draft === undefined)).forEach((group) => {
          blocks.push({ type: "group", name: group.name, host: group.host, color: group.icon.color, icon: projects.projectIcon(group.icon) });
          blocks.push(...group.items.map((item) => sessionBlock(item, true)));
        });
      } else {
        blocks.push(...rest.map((item) => sessionBlock(item)));
      }
      paint(blocks);
      return;
    }
    const items = filteredHistory();
    const drafts = draftItems(false).map((item) => draftBlock(item));
    if (!items.length && !drafts.length && !S.historyLoaded) {
      paint([{ type: "loading" }]);
    } else if (!items.length && !drafts.length) {
      paint([empty("No chats yet.")]);
    } else {
      const { pinned, rest } = splitPinned(items);
      // Pinned first, then drafts (they out-sort every dated row), then the
      // timeline: a newly updated chat never climbs above a draft.
      paint([
        ...(pinned.length ? [kicker("Pinned"), ...pinned.map((item) => threadBlock(item)), ...(rest.length || drafts.length ? [SEP] : [])] : []),
        ...drafts,
        ...rest.map((item) => threadBlock(item)),
      ]);
    }
  }

    function paint(blocks) {
    S.historyList.innerHTML = ui.historyListHtml(blocks);
    syncHistoryFooter();
  }

    function resetSearchLists() {
    S.searchActiveQuery = "";
    S.searchThreads = [];
    S.searchArchived = [];
    S.searchProjects = [];
  }

    function setSearchOpen(open) {
    const top = S.root.querySelector("[data-history-top]");
    const row = S.root.querySelector("[data-history-row]");
    if (!top || !row || !S.historySearch || !S.searchToggle) return;
    const was = S.searchOpen;
    S.searchOpen = open;
    top.classList.toggle("is-searching", open);
    S.searchToggle.setAttribute("aria-expanded", String(open));
    if (open && !was) overlays.overlayPush("search");
    else if (!open && was) overlays.overlayDrop("search");
    if (open) {
      row.hidden = false;
      S.historySearch.focus();
      S.historySearch.select();
    } else {
      row.hidden = true;
      if (S.historyQuery) {
        S.historyQuery = "";
        S.historySearch.value = "";
        resetSearchLists();
        renderHistory();
      }
      S.searchToggle.focus({ preventScroll: true });
    }
  }

    function queueSearch() {
    window.clearTimeout(S.searchTimer);
    const gen = ++S.searchGen;
    const q = S.historyQuery.trim();
    if (!q) {
      resetSearchLists();
      renderHistory();
      return;
    }
    S.searchTimer = window.setTimeout(async () => {
      try {
        const data = await net.api("/chat/threads?q=" + encodeURIComponent(q));
        if (gen !== S.searchGen) return;
        S.searchActiveQuery = q;
        S.searchThreads = data.threads || [];
        S.searchArchived = data.archived || [];
        S.searchProjects = data.projects || [];
        renderHistory();
      } catch (error) {
        if (gen === S.searchGen) sync.showFailure("Chat search failed", error);
      }
    }, 250);
  }

    async function refreshThreads() {
    const before = S.cursor;
    const data = await net.api("/chat/threads");
    if (S.cursor !== before) return;
    S.historyLoaded = true;
    S.threads = data.threads || [];
    S.archivedThreads = data.archived || [];
    spinners.noteServerRunning(S.threads);
    spinners.noteServerRunning(S.archivedThreads);
    if (Array.isArray(data.projects)) S.projects = data.projects;
    threads.syncTitle();
    renderHistory();
  }

  return {
    draftMenu,
    filteredHistory,
    providerLogo,
    queueSearch,
    refreshThreads,
    renderHistory,
    resetSearchLists,
    setHistoryTab,
    setSearchOpen,
    syncHistoryChrome,
    historyIcons,
    runningFirst,
  };
}

export { historyIcons, runningFirst };

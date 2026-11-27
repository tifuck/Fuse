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

// A row's markup with the run spinner's glyph left out: the theme ticks it
// in place, so it says nothing about whether the row changed.
function paintedMarkup(el) {
  if (!el.querySelector("[data-run-spin]")) return el.outerHTML;
  const copy = el.cloneNode(true);
  copy.querySelectorAll("[data-run-spin]").forEach((slot) => slot.replaceChildren());
  return copy.outerHTML;
}

const ROW_BLOCKS = new Set(["thread", "draft", "project"]);

// A list block's place (what it is, and what decides the section, group or
// heading the theme draws above it) and its whole content.
function blockSigs(block) {
  const all = JSON.stringify(block);
  if (!ROW_BLOCKS.has(block.type)) return { place: all, row: "" };
  const { item, row } = block;
  return { place: JSON.stringify([block.type, item.id, item.draft, item.host_id, item.host_alias, row.grouped, row.meta?.host]), row: all };
}

function nodeKey(el, markup) {
  if (!el.hasAttribute("data-history-item")) return "html:" + markup;
  return "row:" + ["thread", "draft", "project"].map((k) => el.dataset[k] ?? "").join("|");
}

// Indexes (into seq) of one longest increasing run of seq's non-negative values.
function longestRun(seq) {
  const tails = [];
  const prev = new Array(seq.length).fill(-1);
  seq.forEach((v, i) => {
    if (v < 0) return;
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (seq[tails[mid]] < v) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) prev[i] = tails[lo - 1];
    tails[lo] = i;
  });
  const keep = new Set();
  for (let i = tails.length ? tails[tails.length - 1] : -1; i >= 0; i = prev[i]) keep.add(i);
  return keep;
}

// A pinned row being dragged moves with the finger instead of scrolling the
// list. Only pinned rows listen, so no other scroll waits on the page.
function vetoPinScroll(event) {
  if (S.pinDrag && S.pinDrag.dragging) event.preventDefault();
}

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
  // What each list node was painted from (paintedMarkup); a node whose
  // markup comes out the same again stays, so hover, focus and a live
  // spinner carry over.
  const paintedFrom = new WeakMap();
  let paintedHtml = "";
  let paintedNodes = [];
  let paintedSigs = [];
  let historyFrame = 0;

    function providerLogo(type) {
    const src = S.logoByType.get(type) || "/static/icon.svg";
    return `<img class="provider-logo" src="${escapeHtml(src)}" alt="" aria-hidden="true" draggable="false">`;
  }

    function setHistoryTab(tab, opts) {
    const next = tab === "projects" ? "projects" : "chats";
    const persist = !opts || opts.persist !== false;
    const silent = Boolean(opts && opts.silent);
    const changed = next !== S.historyTab;
    if (changed) S.showArchived = false;
    S.historyTab = next;
    if (persist) {
      try { localStorage.setItem(S.STORE_TAB, next); } catch (_) {}
    }
    syncHistoryChrome();
    if (silent) return;
    renderHistory();
    // The new tab's list fades up into place, once per switch.
    if (changed && S.historyList && !S.reduceMotion.matches) {
      S.historyList.animate([{ opacity: 0, translate: "0 3px" }, { opacity: 1, translate: "0 0" }], { duration: 200, easing: "cubic-bezier(0.2, 0, 0, 1)" });
    }
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
    if (newLabel) newLabel.textContent = projectLabel ? "New project session" : "New chat";
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

    // Every caller in a frame shares one paint.
  function renderHistory() {
    if (!S.historyList || historyFrame) return;
    historyFrame = requestAnimationFrame(() => {
      historyFrame = 0;
      drawHistory();
    });
  }

    // A row rewritten outside a paint (rename) is redrawn by the next one.
  function forgetRow(row) {
    paintedFrom.delete(row);
  }

    function drawHistory() {
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
      paint(blocks.length ? blocks : [empty("No chats or projects match.")]);
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
        paint([empty("No project sessions yet. Use New project session to start one.")]);
        return;
      }
      const blocks = [];
      if (pinned.length) blocks.push(kicker("Pinned"), ...pinned.map((item) => threadBlock(item)), ...(rest.length ? [SEP] : []));
      // Drafts sit directly under the pinned section, above running rows and every group.
      blocks.push(...rest.filter((item) => item.draft !== undefined).map((item) => sessionBlock(item)));
      const sessionRows = rest.filter((item) => item.draft === undefined);
      if (S.projectSort === "project") {
        projects.groupedProjectSessions(sessionRows).forEach((group) => {
          blocks.push({ type: "group", name: group.name, host: group.host, color: group.icon.color, icon: projects.projectIcon(group.icon) });
          blocks.push(...group.items.map((item) => sessionBlock(item, true)));
        });
      } else {
        blocks.push(...sessionRows.map((item) => threadBlock(item)));
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

    // Only rows changed, and none came, went, moved or changed section: each
  // is drawn on its own and swapped in, instead of drawing and parsing the
  // whole list. A row is the last element the theme draws for its block (a
  // heading may come first), and the k-th row block is the k-th row node.
  function patchRows(blocks, sigs) {
    if (sigs.length !== paintedSigs.length) return false;
    const rows = paintedNodes.filter((node) => node.nodeType === 1 && node.hasAttribute("data-history-item"));
    const swaps = [];
    let k = -1;
    for (let i = 0; i < sigs.length; i += 1) {
      if (sigs[i].place !== paintedSigs[i].place) return false;
      if (ROW_BLOCKS.has(blocks[i].type)) k += 1;
      if (sigs[i].row === paintedSigs[i].row) continue;
      const tpl = document.createElement("template");
      tpl.innerHTML = ui.historyListHtml([blocks[i]]);
      const fresh = tpl.content.lastElementChild;
      const old = rows[k];
      if (!fresh || !old || !fresh.hasAttribute("data-history-item") || nodeKey(fresh) !== nodeKey(old)) return false;
      swaps.push([old, fresh]);
    }
    paintedSigs = sigs;
    if (!swaps.length) return true;
    for (const [old, fresh] of swaps) {
      const markup = paintedMarkup(fresh);
      if (paintedFrom.get(old) === markup) continue;
      paintedFrom.set(fresh, markup);
      if (fresh.hasAttribute("data-pinned")) fresh.addEventListener("touchmove", vetoPinScroll, { passive: false });
      old.replaceWith(fresh);
      paintedNodes[paintedNodes.indexOf(old)] = fresh;
    }
    // The whole list's markup is no longer what is drawn.
    paintedHtml = "";
    syncHistoryFooter();
    return true;
  }

    function paint(blocks) {
    const list = S.historyList;
    const kids = list.childNodes;
    const intact = kids.length === paintedNodes.length && paintedNodes.every((node, i) => kids[i] === node && paintedFrom.has(node));
    const sigs = blocks.map(blockSigs);
    if (intact && patchRows(blocks, sigs)) return;
    paintedSigs = sigs;
    const html = ui.historyListHtml(blocks);
    if (html === paintedHtml && intact) return;
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const spare = new Map();
    for (const node of list.children) {
      const markup = paintedFrom.get(node);
      if (markup === undefined) continue;
      const key = nodeKey(node, markup);
      if (!spare.has(key)) spare.set(key, []);
      spare.get(key).push(node);
    }
    const next = [...tpl.content.children].map((fresh) => {
      const markup = paintedMarkup(fresh);
      const old = spare.get(nodeKey(fresh, markup))?.shift();
      if (old && paintedFrom.get(old) === markup) return old;
      paintedFrom.set(fresh, markup);
      if (fresh.hasAttribute("data-pinned")) fresh.addEventListener("touchmove", vetoPinScroll, { passive: false });
      return fresh;
    });
    // Drop what goes, then move only the nodes outside the longest run
    // already in order.
    const keep = new Set(next);
    [...kids].forEach((node) => { if (!keep.has(node)) node.remove(); });
    const at = new Map([...kids].map((node, i) => [node, i]));
    const stay = longestRun(next.map((node) => at.get(node) ?? -1));
    let ref = null;
    for (let i = next.length - 1; i >= 0; i -= 1) {
      if (!stay.has(i)) list.insertBefore(next[i], ref);
      ref = next[i];
    }
    paintedHtml = html;
    paintedNodes = next;
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

    let searchRead = null;
    let searchLater = 0;

    // `later`: something that can only move content matches (a run writing
  // messages). Worth one refresh every few seconds, not one per thread event.
  function queueSearch(later = false) {
    if (later === true) {
      if (!searchLater) searchLater = window.setTimeout(() => { searchLater = 0; queueSearch(); }, 5000);
      return;
    }
    window.clearTimeout(searchLater);
    searchLater = 0;
    window.clearTimeout(S.searchTimer);
    if (searchRead) searchRead.abort();
    searchRead = null;
    const gen = ++S.searchGen;
    const q = S.historyQuery.trim();
    if (!q) {
      resetSearchLists();
      renderHistory();
      return;
    }
    S.searchTimer = window.setTimeout(async () => {
      const read = searchRead = new AbortController();
      try {
        const data = await net.api("/chat/threads?q=" + encodeURIComponent(q), { signal: read.signal });
        if (gen !== S.searchGen) return;
        S.searchActiveQuery = q;
        S.searchThreads = data.threads || [];
        S.searchArchived = data.archived || [];
        S.searchProjects = data.projects || [];
        renderHistory();
      } catch (error) {
        if (gen === S.searchGen) sync.showFailure("Could not search chats", error);
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
    forgetRow,
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

export { historyIcons, longestRun, runningFirst };

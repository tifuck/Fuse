// Sidebar presentation: the Chats and Projects lists. The runtime sorts,
// filters, pins and groups, then hands historyListHtml a list of blocks
// (kicker, sep, thread, draft, project, group, empty, loading); every row
// keeps the runtime's hooks: data-history-item, data-thread / data-draft /
// data-project, data-pinned, data-row-open and data-row-more.
const DRAFT_ICON = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M10.8 2.7l2.5 2.5-7.6 7.6-3.2.7.7-3.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';

export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);

  // A run spinner slot; the runtime fills [data-run-spin] (setConsoleSpin).
  function runSpinHtml(stamp) {
    return `<span class="chat-run-spin" data-run-spin="${esc(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
      `<span class="chat-console-spin">${deps.consoleSpinGlyph(true)}</span></span>`;
  }

  // The age slot: the runtime keeps [data-age-stamp] text current. An unread
  // row shows a dot instead; the time stays in the tooltip.
  function ageHtml(age) {
    if (!age) return "";
    const title = age.title ? ` title="${esc(age.title)}"` : "";
    if (age.unread) return `<span class="chat-history-age"${title}><span class="chat-history-unread-dot" role="img" aria-label="Unread"></span></span>`;
    return `<span class="chat-history-age" data-age-stamp="${esc(String(age.at))}"${title}>${esc(age.text)}</span>`;
  }

  function metaHtml(meta) {
    if (!meta) return "";
    const hostLabel = meta.host ? `<span class="chat-history-host-label">${esc(meta.host)}</span>` : "";
    return `<span class="chat-history-meta"><span class="chat-history-project" data-row-meta>${esc(meta.name || "Project")}</span>${hostLabel}</span>`;
  }

  function threadRowHtml(item, row) {
    const on = row.on ? " is-on" : "";
    const project = row.project;
    const title = esc(row.title);
    const snip = item.snippet ? `<span class="chat-history-snip">${esc(item.snippet)}</span>` : "";
    const chip = row.chip ? `<span class="chat-history-chip">${esc(row.chip)}</span>` : "";
    const meta = metaHtml(row.meta);
    const age = row.running ? runSpinHtml(row.spin) : ageHtml(row.age);
    const lead = project
      ? age + `<span class="chat-history-indicator is-folder" data-project-color="${esc(item.project_color || "")}">${row.icon}</span>`
      : `<span class="chat-history-indicator">${age}</span>`;
    const groupedClass = project && row.grouped ? " is-grouped" : "";
    const runningClass = row.running ? " is-running" : "";
    const busy = row.running ? ' aria-busy="true"' : "";
    const working = row.running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.snippet || meta);
    const scheduledClass = item.schedule_id ? " is-task" : "";
    const pinClass = item.pinned ? " is-pinned" : "";
    const unreadClass = row.unread ? " is-unread" : "";
    return `<div class="chat-history-item${on}${twoLine ? " has-snip" : ""}${project ? " is-project" : ""}${groupedClass}${runningClass}${scheduledClass}${pinClass}${unreadClass}" data-history-item${item.pinned ? " data-pinned" : ""} data-thread="${esc(String(item.id))}"${busy}><button type="button" class="open" data-row-open title="${title}"${on ? ' aria-current="true"' : ""}>${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name" data-row-name>${title}</span>${meta}${snip}</span></span>${chip}</button><button type="button" class="more" data-row-more aria-label="Actions for ${title}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

  function draftRowHtml(item, row) {
    const project = row.project;
    const on = row.on ? " is-on" : "";
    const title = esc(item.title);
    const meta = metaHtml(row.meta);
    const pen = `<span class="chat-history-age">${DRAFT_ICON}</span>`;
    const lead = project
      ? pen + `<span class="chat-history-indicator is-folder">${row.icon}</span>`
      : `<span class="chat-history-indicator">${pen}</span>`;
    const classes = `${on}${meta ? " has-snip" : ""}${project ? " is-project" : ""}${project && row.grouped ? " is-grouped" : ""}`;
    return `<div class="chat-history-item is-draft${classes}" data-history-item data-draft="${esc(item.draft)}"><button type="button" class="open" data-row-open title="Draft: ${title}"${on ? ' aria-current="true"' : ""}><span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name" data-row-name><span class="chat-history-draft">Draft</span> ${title}</span>${meta}</span></span></button><button type="button" class="more" data-row-more aria-label="Actions for draft ${title}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

  function projectRowHtml(item, row) {
    const on = row.on ? " is-on" : "";
    const name = item.name || "Project";
    const hostLabel = item.host_alias
      ? `<span class="chat-history-host-label">${esc(item.host_alias)}</span>`
      : "";
    const path = item.remote_path
      ? `<span class="chat-history-meta"><span class="chat-history-project" data-row-meta>${esc(item.remote_path)}</span>${hostLabel}</span>`
      : "";
    const chip = row.archived ? '<span class="chat-history-chip">Archived</span>' : "";
    const threadAttr = item.thread_id ? ` data-thread="${esc(String(item.thread_id))}"` : "";
    const lead = (row.running ? runSpinHtml(row.spin) : ageHtml(row.age)) + row.icon;
    const runningClass = row.running ? " is-running" : "";
    const busy = row.running ? ' aria-busy="true"' : "";
    const working = row.running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.remote_path);
    return `<div class="chat-history-item is-project${on}${twoLine ? " has-snip" : ""}${runningClass}" data-history-item data-project="${item.host_id}:${item.id}"${threadAttr}${busy}><button type="button" class="open" data-row-open title="${esc(name)}"${on ? ' aria-current="true"' : ""}>${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name" data-row-name>${esc(name)}</span>${path}</span></span>${chip}</button><button type="button" class="more" data-row-more aria-label="Actions for project ${esc(name)}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

  function blockHtml(block) {
    switch (block.type) {
      case "thread": return threadRowHtml(block.item, block.row);
      case "draft": return draftRowHtml(block.item, block.row);
      case "project": return projectRowHtml(block.item, block.row);
      case "kicker": return `<p class="chat-history-kicker" data-history-section>${esc(block.text)}</p>`;
      // Hairline between the pinned section and the timeline below it.
      case "sep": return '<div class="chat-history-sep" aria-hidden="true"></div>';
      case "group":
        return `<div class="chat-history-project-head" data-history-group data-project-color="${esc(block.color || "")}">${block.icon}<span class="chat-history-project" data-group-name>${esc(block.name)}</span>` +
          (block.host ? `<span class="chat-history-host-label">${esc(block.host)}</span>` : "") + "</div>";
      case "machine":
        return `<p class="chat-history-kicker tail-machine" data-history-section><span class="tail-machine-dot" aria-hidden="true"></span>${esc(block.host || "No machine")}</p>`;
      case "empty": return `<p class="chat-history-empty" data-history-empty>${esc(block.text)}</p>`;
      case "loading": return `<div class="chat-history-loading" data-history-loading role="img" aria-label="Loading chats">${"<span></span>".repeat(6)}</div>`;
      default: return "";
    }
  }

  // Tail: rows that run on a machine sit under it. Within each stretch of
  // rows (a section, or the whole list) the order stays the runtime's; a
  // machine heads its rows where its first one stood. A list the runtime
  // already grouped by project keeps that grouping.
  const ROWS = new Set(["thread", "draft", "project"]);
  const machineOf = (block) => (block.type === "project" ? block.item.host_alias : block.row?.meta?.host) || "";
  function byMachine(blocks) {
    if (blocks.some((block) => block.type === "group") || !blocks.some((block) => ROWS.has(block.type) && machineOf(block))) return blocks;
    const out = [];
    let run = [];
    const flush = () => {
      const groups = new Map();
      run.forEach((block) => {
        const host = machineOf(block);
        if (!groups.has(host)) groups.set(host, []);
        groups.get(host).push(block);
      });
      groups.forEach((rows, host) => out.push({ type: "machine", host }, ...rows));
      run = [];
    };
    blocks.forEach((block) => {
      if (ROWS.has(block.type)) run.push(block);
      else { flush(); out.push(block); }
    });
    flush();
    return out;
  }

  function historyListHtml(blocks) {
    return byMachine(blocks).map(blockHtml).join("");
  }

  return { historyListHtml, runSpinHtml };
}

// Sidebar presentation: one 28px row per chat. Each row opens with its age
// slot (relative time, or the running pulse, unread or draft dot), then the
// project's pastel folder or logo for project rows; the timeline splits into
// Today and Earlier. The runtime sorts, filters, pins and groups;
// historyListHtml draws its blocks. Rows keep the runtime's hooks:
// data-history-item, data-thread / data-draft / data-project, data-pinned,
// data-row-open, data-row-more.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);
  const MORE = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" fill="currentColor"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/><circle cx="12.5" cy="8" r="1.2" fill="currentColor"/></svg>';

  // A run spinner slot; the runtime fills [data-run-spin] (setConsoleSpin).
  // Combined shows it as a pulsing dot (styles/shell.css).
  function runSpinHtml(stamp) {
    return `<span class="chat-run-spin cb-live" data-run-spin="${esc(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
      `<span class="chat-console-spin">${deps.consoleSpinGlyph(true)}</span></span>`;
  }

  // The age slot: what the row is doing, else when it last moved (the
  // runtime keeps [data-age-stamp] current).
  function whenHtml(row) {
    if (row.running) return runSpinHtml(row.spin);
    if (row.unread) return '<i class="cb-unread" role="img" aria-label="Unread"></i>';
    const age = row.age;
    if (!age?.text) return "";
    const title = age.title ? ` title="${esc(age.title)}"` : "";
    return `<time class="cb-when" data-age-stamp="${esc(String(age.at))}"${title}>${esc(age.text)}</time>`;
  }

  // The row's tooltip: its full title, then when it last moved.
  const tip = (title, row) => row.age?.title ? `${title} · ${esc(row.age.title)}` : title;

  function rowHtml({ cls, attrs, title, label, lead = "", extra = "", when = "", menu, on, working = "" }) {
    const mark = lead ? `<span class="cb-lead">${lead}</span>` : "";
    return `<div class="cb-row${cls}" data-history-item${attrs}>` +
      `<button type="button" class="cb-open" data-row-open title="${title}"${on ? ' aria-current="true"' : ""}>${working}<span class="cb-age">${when}</span>${mark}<span class="cb-name" data-row-name>${label}</span>${extra}</button>` +
      `<button type="button" class="cb-more" data-row-more aria-label="${menu}" aria-haspopup="menu" aria-expanded="false">${MORE}</button></div>`;
  }

  function threadRowHtml(item, row) {
    const title = esc(row.title);
    const lead = row.project ? row.icon : "";
    const where = row.meta ? `<span class="cb-sub" data-row-meta>${esc(row.meta.name)}</span>` : "";
    const chip = row.chip ? `<span class="cb-chip">${esc(row.chip)}</span>` : "";
    const snip = item.snippet ? `<span class="cb-snip">${esc(item.snippet)}</span>` : "";
    const cls = (row.on ? " is-on" : "") + (row.running ? " is-running" : "") + (row.unread ? " is-unread" : "") +
      (item.pinned ? " is-pinned" : "") + (item.schedule_id ? " is-task" : "") + (row.grouped ? " is-grouped" : "") + (snip ? " has-snip" : "");
    const attrs = (item.pinned ? " data-pinned" : "") + ` data-thread="${esc(String(item.id))}"` + (row.running ? ' aria-busy="true"' : "");
    const working = row.running ? '<span class="visually-hidden">Working</span>' : "";
    return rowHtml({ cls, attrs, title: tip(title, row), label: title, working, lead, extra: where + chip + snip, when: whenHtml(row), menu: `Actions for ${title}`, on: row.on });
  }

  function draftRowHtml(item, row) {
    const title = esc(item.title);
    const where = row.meta ? `<span class="cb-sub" data-row-meta>${esc(row.meta.name)}</span>` : "";
    return rowHtml({
      cls: " is-draft" + (row.on ? " is-on" : "") + (row.grouped ? " is-grouped" : ""),
      attrs: ` data-draft="${esc(item.draft)}"`,
      title: `Draft: ${title}`, label: `<em>Draft</em> ${title}`, lead: row.project ? row.icon : "", extra: where,
      when: '<i class="cb-dot is-draft" aria-hidden="true"></i>',
      menu: `Actions for draft ${title}`, on: row.on,
    });
  }

  function projectRowHtml(item, row) {
    const name = esc(item.name || "Project");
    const host = item.remote_path ? `<span class="cb-sub" data-row-meta>${esc(item.remote_path)}</span>` : item.host_alias ? `<span class="cb-sub">${esc(item.host_alias)}</span>` : "";
    const chip = row.archived ? '<span class="cb-chip">Archived</span>' : "";
    return rowHtml({
      cls: " is-project" + (row.on ? " is-on" : "") + (row.running ? " is-running" : ""),
      attrs: ` data-project="${item.host_id}:${item.id}"` + (item.thread_id ? ` data-thread="${esc(String(item.thread_id))}"` : "") + (row.running ? ' aria-busy="true"' : ""),
      title: tip(name, row), label: name, lead: row.icon, extra: host + chip, when: whenHtml(row),
      menu: `Actions for project ${name}`, on: row.on,
    });
  }

  // Today or Earlier, by the row's last activity (drafts and live rows are today).
  function bucket(block) {
    if (block.row.running || block.type === "draft") return "Today";
    const at = new Date(block.item.updated_at || "");
    if (!Number.isFinite(at.getTime())) return "Earlier";
    return window.fuseTime.dayNumber(at) === window.fuseTime.dayNumber(Date.now()) ? "Today" : "Earlier";
  }

  function historyListHtml(blocks) {
    let out = "";
    let heading = "";
    let timeline = true;
    for (const block of blocks) {
      switch (block.type) {
        case "kicker":
          out += `<h2 class="cb-group" data-history-section>${esc(block.text)}</h2>`;
          // Rows under a named section (Pinned, Projects, Sessions) are not dated.
          heading = block.text;
          timeline = false;
          break;
        case "sep":
          timeline = true;
          heading = "";
          break;
        case "group":
          out += `<h2 class="cb-group is-project" data-history-group data-project-color="${esc(block.color || "")}">${block.icon}<span data-group-name>${esc(block.name)}</span>${block.host ? `<small>${esc(block.host)}</small>` : ""}</h2>`;
          timeline = false;
          break;
        case "thread":
        case "draft":
          if (timeline && !block.row.grouped) {
            const next = bucket(block);
            if (next !== heading) { out += `<h2 class="cb-group" data-history-section>${next}</h2>`; heading = next; }
          }
          out += block.type === "thread" ? threadRowHtml(block.item, block.row) : draftRowHtml(block.item, block.row);
          break;
        case "project": out += projectRowHtml(block.item, block.row); break;
        case "empty": out += `<p class="cb-empty" data-history-empty>${esc(block.text)}</p>`; break;
        case "loading": out += '<div class="cb-loading" data-history-loading role="img" aria-label="Loading chats"><span></span><span></span><span></span></div>'; break;
        default: break;
      }
    }
    return out;
  }

  return { historyListHtml, runSpinHtml };
}

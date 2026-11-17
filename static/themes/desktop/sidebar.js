import { chaosHtml } from './chaos.js';

// Sidebar presentation: one 28px row per session. Each row opens with its
// age slot (relative time, the unread dot, or the Chaos spinner while it
// runs), then the project's pastel folder or logo for project sessions; the
// timeline sits under Pinned and Recents. The runtime sorts, filters, pins
// and groups; historyListHtml draws its blocks. Rows keep the runtime's
// hooks: data-history-item, data-thread / data-draft / data-project,
// data-pinned, data-row-open, data-row-more.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);

  // A run spinner slot; the runtime fills [data-run-spin] (setConsoleSpin).
  // Desktop shows it as a pulsing dot (styles/shell.css).
  function runSpinHtml(stamp) {
    return `<span class="chat-run-spin dk-live" data-run-spin="${esc(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
      `<span class="chat-console-spin">${deps.consoleSpinGlyph(true)}</span></span>`;
  }

  // The age slot: what the row is doing, else when it last moved (the
  // runtime keeps [data-age-stamp] current). A running row shows the Chaos
  // spinner (chaos.js), tinted with its project colour.
  function whenHtml(row, color) {
    if (row.running) return chaosHtml(row.spin, color) + runSpinHtml(row.spin);
    if (row.unread) return '<i class="dk-unread" role="img" aria-label="Unread"></i>';
    const age = row.age;
    if (!age?.text) return "";
    const title = age.title ? ` title="${esc(age.title)}"` : "";
    return `<time class="dk-when" data-age-stamp="${esc(String(age.at))}"${title}>${esc(age.text)}</time>`;
  }

  function rowHtml({ cls, attrs, title, label, lead = "", extra = "", when = "", menu, on, working = "" }) {
    return `<div class="dk-row${cls}" data-history-item${attrs}>` +
      `<button type="button" class="dk-open" data-row-open title="${title}"${on ? ' aria-current="true"' : ""}>${working}<span class="dk-age">${when}</span>${lead}<span class="dk-name" data-row-name>${label}</span>${extra}</button>` +
      `<button type="button" class="dk-more" data-row-more aria-label="${menu}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

  // The tooltip carries what the quiet row leaves out: the date and a search hit.
  const tip = (title, row, item) => esc([title, row.age?.title, item.snippet].filter(Boolean).join(" · "));

  function threadRowHtml(item, row) {
    const title = esc(row.title);
    const lead = row.project ? row.icon : "";
    const where = row.meta ? `<span class="dk-sub" data-row-meta>${esc(row.meta.name)}</span>` : "";
    const chip = row.chip ? `<span class="dk-chip">${esc(row.chip)}</span>` : "";
    const snip = item.snippet ? `<span class="dk-snip">${esc(item.snippet)}</span>` : "";
    const cls = (row.on ? " is-on" : "") + (row.running ? " is-running" : "") + (row.unread ? " is-unread" : "") +
      (item.pinned ? " is-pinned" : "") + (item.schedule_id ? " is-task" : "") + (row.grouped ? " is-grouped" : "") + (snip ? " has-snip" : "");
    const attrs = (item.pinned ? " data-pinned" : "") + ` data-thread="${esc(String(item.id))}"` + (row.running ? ' aria-busy="true"' : "");
    const working = row.running ? '<span class="visually-hidden">Working</span>' : "";
    return rowHtml({ cls, attrs, title: tip(row.title, row, item), label: title, working, lead, extra: where + chip + snip, when: whenHtml(row, item.project_color), menu: `Actions for ${title}`, on: row.on });
  }

  function draftRowHtml(item, row) {
    const title = esc(item.title);
    const where = row.meta ? `<span class="dk-sub" data-row-meta>${esc(row.meta.name)}</span>` : "";
    return rowHtml({
      cls: " is-draft" + (row.on ? " is-on" : "") + (row.grouped ? " is-grouped" : ""),
      attrs: ` data-draft="${esc(item.draft)}"`,
      title: `Draft: ${title}`, label: `<em>Draft</em> ${title}`, lead: row.project ? row.icon : "", extra: where,
      menu: `Actions for draft ${title}`, on: row.on,
    });
  }

  function projectRowHtml(item, row) {
    const name = esc(item.name || "Project");
    const host = item.remote_path ? `<span class="dk-sub" data-row-meta>${esc(item.remote_path)}</span>` : item.host_alias ? `<span class="dk-sub">${esc(item.host_alias)}</span>` : "";
    const chip = row.archived ? '<span class="dk-chip">Archived</span>' : "";
    return rowHtml({
      cls: " is-project" + (row.on ? " is-on" : "") + (row.running ? " is-running" : ""),
      attrs: ` data-project="${item.host_id}:${item.id}"` + (item.thread_id ? ` data-thread="${esc(String(item.thread_id))}"` : "") + (row.running ? ' aria-busy="true"' : ""),
      title: tip(item.name || "Project", row, item), label: name, lead: row.icon, extra: host + chip, when: whenHtml(row, item.color),
      menu: `Actions for project ${name}`, on: row.on,
    });
  }


  function historyListHtml(blocks) {
    let out = "";
    let heading = "";
    let timeline = true;
    for (const block of blocks) {
      switch (block.type) {
        case "kicker":
          out += `<h2 class="dk-group" data-history-section>${esc(block.text)}</h2>`;
          // Rows under a named section (Pinned, Projects, Sessions) are not dated.
          heading = block.text;
          timeline = false;
          break;
        case "sep":
          timeline = true;
          heading = "";
          break;
        case "group":
          out += `<h2 class="dk-group is-project" data-history-group data-project-color="${esc(block.color || "")}">${block.icon}<span data-group-name>${esc(block.name)}</span>${block.host ? `<small>${esc(block.host)}</small>` : ""}</h2>`;
          timeline = false;
          break;
        case "thread":
        case "draft":
          if (timeline && !block.row.grouped && heading !== "Recents") {
            out += '<h2 class="dk-group" data-history-section>Recents</h2>';
            heading = "Recents";
          }
          out += block.type === "thread" ? threadRowHtml(block.item, block.row) : draftRowHtml(block.item, block.row);
          break;
        case "project": out += projectRowHtml(block.item, block.row); break;
        case "empty": out += `<p class="dk-empty" data-history-empty>${esc(block.text)}</p>`; break;
        case "loading": out += '<div class="dk-loading" data-history-loading role="img" aria-label="Loading chats"><span></span><span></span><span></span></div>'; break;
        default: break;
      }
    }
    return out;
  }

  return { historyListHtml, runSpinHtml };
}

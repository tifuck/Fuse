// Sidebar presentation: one 26px row per chat, relative time on the right,
// the timeline split into Today and Earlier, projects as coloured squares.
// The runtime sorts, filters, pins and groups; historyListHtml draws its
// blocks. Rows keep the runtime's hooks: data-history-item, data-thread /
// data-draft / data-project, data-pinned, data-row-open, data-row-more.
export function create(deps) {
  const esc = (value) => deps.escapeHtml(value);
  const HEX = /^#[0-9a-f]{3,8}$/i;

  // A run spinner slot; the runtime fills [data-run-spin] (setConsoleSpin).
  // Combined shows it as a pulsing dot (styles/shell.css).
  function runSpinHtml(stamp) {
    return `<span class="chat-run-spin cb-live" data-run-spin="${esc(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
      `<span class="chat-console-spin">${deps.consoleSpinGlyph(true)}</span></span>`;
  }

  function whenHtml(row) {
    if (row.running) return runSpinHtml(row.spin);
    const age = row.age;
    if (!age) return "";
    const title = age.title ? ` title="${esc(age.title)}"` : "";
    if (age.unread) return `<span class="cb-when"${title}><i class="cb-unread" role="img" aria-label="Unread"></i></span>`;
    return `<time class="cb-when" data-age-stamp="${esc(String(age.at))}"${title}>${esc(age.text)}</time>`;
  }

  const square = (color) => `<i class="cb-pj" aria-hidden="true"${HEX.test(color || "") ? ` style="--cb-pj:${color}"` : ""}></i>`;

  function rowHtml({ cls, attrs, title, label, lead, extra = "", when = "", menu, on }) {
    return `<div class="cb-row${cls}" data-history-item${attrs}>` +
      `<button type="button" class="cb-open" data-row-open title="${title}"${on ? ' aria-current="true"' : ""}>${lead}<span class="cb-name">${label}</span>${extra}${when}</button>` +
      `<button type="button" class="cb-more" data-row-more aria-label="${menu}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

  function threadRowHtml(item, row) {
    const title = esc(row.title);
    const lead = row.project ? square(item.project_color) : '<i class="cb-dot" aria-hidden="true"></i>';
    const where = row.meta ? `<span class="cb-sub">${esc(row.meta.name)}</span>` : "";
    const chip = row.chip ? `<span class="cb-chip">${esc(row.chip)}</span>` : "";
    const snip = item.snippet ? `<span class="cb-snip">${esc(item.snippet)}</span>` : "";
    const cls = (row.on ? " is-on" : "") + (row.running ? " is-running" : "") + (row.unread ? " is-unread" : "") +
      (item.pinned ? " is-pinned" : "") + (item.schedule_id ? " is-task" : "") + (row.grouped ? " is-grouped" : "") + (snip ? " has-snip" : "");
    const attrs = (item.pinned ? " data-pinned" : "") + ` data-thread="${esc(String(item.id))}"` + (row.running ? ' aria-busy="true"' : "");
    const working = row.running ? '<span class="visually-hidden">Working</span>' : "";
    return rowHtml({ cls, attrs, title, label: working + title, lead, extra: where + chip + snip, when: whenHtml(row), menu: `Actions for ${title}`, on: row.on });
  }

  function draftRowHtml(item, row) {
    const title = esc(item.title);
    const lead = row.project ? square(item.project_color) : '<i class="cb-dot is-draft" aria-hidden="true"></i>';
    const where = row.meta ? `<span class="cb-sub">${esc(row.meta.name)}</span>` : "";
    return rowHtml({
      cls: " is-draft" + (row.on ? " is-on" : "") + (row.grouped ? " is-grouped" : ""),
      attrs: ` data-draft="${esc(item.draft)}"`,
      title: `Draft: ${title}`, label: `<em>Draft</em> ${title}`, lead, extra: where,
      menu: `Actions for draft ${title}`, on: row.on,
    });
  }

  function projectRowHtml(item, row) {
    const name = esc(item.name || "Project");
    const host = item.host_alias ? `<span class="cb-sub">${esc(item.host_alias)}</span>` : "";
    const chip = row.archived ? '<span class="cb-chip">Archived</span>' : "";
    return rowHtml({
      cls: " is-project" + (row.on ? " is-on" : "") + (row.running ? " is-running" : ""),
      attrs: ` data-project="${item.host_id}:${item.id}"` + (item.thread_id ? ` data-thread="${esc(String(item.thread_id))}"` : "") + (row.running ? ' aria-busy="true"' : ""),
      title: name, label: name, lead: square(item.color), extra: host + chip, when: whenHtml(row),
      menu: `Actions for project ${name}`, on: row.on,
    });
  }

  // Today or Earlier, by the row's last activity (drafts and live rows are today).
  function bucket(block) {
    if (block.row.running || block.type === "draft") return "Today";
    const at = new Date(block.item.updated_at || "");
    if (!Number.isFinite(at.getTime())) return "Earlier";
    return at.toDateString() === new Date().toDateString() ? "Today" : "Earlier";
  }

  function historyListHtml(blocks) {
    let out = "";
    let heading = "";
    let timeline = true;
    for (const block of blocks) {
      switch (block.type) {
        case "kicker":
          out += `<h5 class="cb-group">${esc(block.text)}</h5>`;
          // Rows under a named section (Pinned, Projects, Sessions) are not dated.
          heading = block.text;
          timeline = false;
          break;
        case "sep":
          timeline = true;
          heading = "";
          break;
        case "group":
          out += `<h5 class="cb-group is-project">${square(block.color)}<span>${esc(block.name)}</span>${block.host ? `<small>${esc(block.host)}</small>` : ""}</h5>`;
          timeline = false;
          break;
        case "thread":
        case "draft":
          if (timeline && !block.row.grouped) {
            const next = bucket(block);
            if (next !== heading) { out += `<h5 class="cb-group">${next}</h5>`; heading = next; }
          }
          out += block.type === "thread" ? threadRowHtml(block.item, block.row) : draftRowHtml(block.item, block.row);
          break;
        case "project": out += projectRowHtml(block.item, block.row); break;
        case "empty": out += `<p class="cb-empty">${esc(block.text)}</p>`; break;
        case "loading": out += '<div class="cb-loading" role="img" aria-label="Loading chats"><span></span><span></span><span></span></div>'; break;
        default: break;
      }
    }
    return out;
  }

  return { historyListHtml, runSpinHtml };
}

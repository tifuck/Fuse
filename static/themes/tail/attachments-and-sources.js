// attachments-and-sources presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function attachmentRemoveButton(att, position) {
    const key = String(att.id || "");
    return `<button type="button" class="chat-user-file-x" data-remove-attachment="${deps.escapeHtml(key || String(position))}" data-att-index="${position}" aria-label="Remove ${deps.escapeHtml(att.name || "attachment")} from this message" title="Remove from message"><svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>`;
  }

function attachmentChipHtml(att, position) {
    if (att.kind === "image" && att.data) {
      return `<figure class="chat-user-file is-image">${deps.imagePreviewButton(att)}${attachmentRemoveButton(att, position)}</figure>`;
    }
    const ext = deps.fileExtLabel(att.name);
    return `<div class="chat-user-file is-file"><span class="chat-file-icon">${FILE_CHIP_ICON}</span><span class="chat-file-name">${deps.escapeHtml(att.name || "File")}</span>${
      ext ? `<span class="chat-file-ext">${deps.escapeHtml(ext)}</span>` : ""
    }${attachmentRemoveButton(att, position)}</div>`;
  }

function fillUserFiles(article, msg, index) {
    let row = article.querySelector("[data-user-files]");
    const files = Array.isArray(msg.attachments) ? msg.attachments : [];
    if (!files.length) {
      if (row) row.remove();
      return;
    }
    if (!row) {
      row = document.createElement("div");
      row.className = "chat-user-files";
      row.dataset.userFiles = "1";
      const bubble = article.querySelector(".chat-bubble");
      article.insertBefore(row, bubble || article.firstChild);
    }
    row.dataset.msgIndex = String(index);
    const signature = JSON.stringify(files);
    if (row._filesSignature === signature) return;
    row._filesSignature = signature;
    row.innerHTML = files.map((att, position) => attachmentChipHtml(att, position)).join("");
  }

function sourceList(rows) {
    const items = (rows || []).map((row) => {
      const url = deps.safeUrl(row.url);
      if (!url || !/^https?:/i.test(url)) return "";
      let domain = url;
      try {
        domain = new URL(url).hostname.replace(/^www\./, "");
      } catch (_) {}
      const state = row.cited ? "cited" : row.status === "read" ? "read" : "found";
      return `<li><a class="chat-source-row" href="${deps.escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="chat-source-domain">${deps.escapeHtml(domain)}</span><span class="chat-source-title">${deps.escapeHtml(row.title || url)}</span><span class="chat-source-state is-${state}">${state}</span></a></li>`;
    }).filter(Boolean);
    if (!items.length) return "";
    return `<ul class="chat-source-list">${items.join("")}</ul>`;
  }

const FILE_CHIP_ICON =
    '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M4.3 2.7h5.1l2.4 2.5v8.1H4.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.4 2.7v2.5h2.4M6.1 8.4h3.8M6.1 10.6h2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  return { attachmentRemoveButton, attachmentChipHtml, fillUserFiles, sourceList, FILE_CHIP_ICON };
}

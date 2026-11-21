// permissions presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function permissionCardHtml(perm, msg) {
    if (!perm) return "";
    if (perm.kind === "tool_permission") return "";
    const status = perm.status || "pending";
    const pending = status === "pending";
    const files = (perm.files || []).map((name) => deps.escapeHtml(name)).join(" · ") || "SKILL.md";
    const replace = perm.overwrite
      ? `<label class="chat-skill-check"><input type="checkbox" data-perm-replace><span>Replace your existing <code>${deps.escapeHtml(perm.slug || "")}</code>${perm.origin_existing ? ` (${deps.escapeHtml(perm.origin_existing)})` : ""}</span></label>`
      : "";
    const actions = pending
      ? `<div class="chat-perm-actions"><button type="button" class="btn sm ghost" data-perm-reject>Discard</button><button type="button" class="btn sm" data-perm-save>Save skill</button></div>`
      : "";
    const fields = pending
      ? `<p class="chat-skill-scope">Saves to your own skills only. Other users don't see it.</p>
         <label class="chat-skill-field"><span>Name</span><input class="mono" data-perm-slug maxlength="64" spellcheck="false" value="${deps.escapeHtml(perm.slug || "")}"></label>
         <label class="chat-skill-field"><span>When to use</span><textarea data-perm-desc maxlength="1024" rows="3">${deps.escapeHtml(perm.description || "")}</textarea></label>
         <label class="chat-skill-field"><span>Instructions <em class="mono">${files}</em></span><textarea class="mono" data-perm-body rows="10" spellcheck="false"></textarea></label>
         <div class="chat-skill-opts">
           <label class="chat-skill-check"><input type="checkbox" data-perm-enable ${perm.enable === false ? "" : "checked"}><span>Enable after saving</span></label>
           ${replace}
         </div>`
      : `<p class="chat-perm-slug mono">${deps.escapeHtml(perm.slug || "")}</p><p class="usage-sub">${deps.escapeHtml(perm.description || "")}</p>`;
    return `<div class="chat-perm chat-skill-card" data-perm-id="${deps.escapeHtml(perm.id)}" data-msg="${deps.escapeHtml(msg.id || "")}">
      <div class="chat-perm-head"><span>Skill proposal</span><span class="chat-perm-status">${deps.escapeHtml(status)}</span></div>
      ${fields}${actions}
    </div>`;
  }

function permQueueCardHtml(rec) {
    if (rec.status !== "pending") return "";
    const esc = deps.escapeHtml;
    const copy = deps.permCardCopy(rec);
    // Every ask has the same three rows: what and where, the payload, why.
    const sub = rec.requester && rec.requester !== "Main agent" ? rec.requester : "";
    const who = sub ? `<span class="chat-ask-who">${esc(sub)}</span>` : "";
    const title = `${who}<b>${esc(copy.action)}</b>${copy.host ? ` on ${esc(copy.host)}` : ""}`;
    const shell = Boolean(copy.command);
    const outside = copy.why === "Outside allowed folders";
    const value = shell ? copy.command : (copy.path || "this location");
    const note = shell ? (copy.path ? `in ${copy.path}` : "") : copy.why;
    const why = [copy.description, shell && outside ? "Outside allowed folders." : ""].filter(Boolean).join(" ");
    const glyph = shell ? "$" : '<svg viewBox="0 0 10 12" width="10" height="12"><path d="M1 1h5l3 3v7H1z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
    const expires = rec.expires_at || rec.expiresAt || "";
    const left = deps.permLeftS(expires);
    const frac = left == null ? 1 : Math.max(0, Math.min(1, left / deps.PERM_TOTAL_S));
    // The server clears grant_kind on shell asks no grant could ever cover;
    // artifact and skill asks are answered once. Their caret stays, disabled,
    // so Allow keeps its width from one ask to the next.
    const ungrantable = (shell && !rec.grant_kind) || copy.once;
    const menu = ungrantable ? "" : `
            <div class="chat-pop chat-perm-always-menu" data-perm-always-menu hidden>
              <button type="button" data-perm-decide="allow_session">Allow this ${esc(copy.noun)} in this chat</button>
              <button type="button" data-perm-decide="allow_host">Always allow this ${esc(copy.noun)}</button>
            </div>`;
    return `<li class="chat-ask${rec.perm_kind === "skill" ? " is-skill" : ""}${left != null && left <= 15 ? " is-low" : ""}" data-perm-card="${esc(rec.id)}" data-perm-sig="${esc(deps.permCardSignature(rec))}" style="--perm-left:${frac}">
      <span class="chat-ask-track" aria-hidden="true"><span class="chat-ask-bar" data-perm-bar></span></span>
      <div class="chat-ask-head">
        <span class="chat-ask-dot" aria-hidden="true"></span>
        <span class="chat-ask-title" title="${esc([sub, copy.action, copy.host].filter(Boolean).join(" "))}">${title}</span>
        <span class="chat-ask-step" data-perm-step hidden>
          <button type="button" data-perm-step-btn="-1" aria-label="Previous request">‹</button><span class="mono" data-perm-step-text></span><button type="button" data-perm-step-btn="1" aria-label="Next request">›</button>
        </span>
        <div class="chat-ask-actions">
          <button type="button" class="chat-ask-clock mono" data-perm-extend title="More time: restart the countdown" aria-label="More time"><span data-perm-count>${esc(deps.fmtPermCountdown(expires))}</span><span class="chat-ask-plus" aria-hidden="true">+</span></button>
          <button type="button" class="chat-ask-btn" data-perm-decide="deny">Deny</button>
          <span class="chat-perm-always chat-ask-allow">
            <button type="button" class="chat-ask-btn is-allow" data-perm-decide="allow">Allow</button><button type="button" class="chat-ask-btn is-caret" data-perm-always aria-expanded="false" aria-label="Allow always options"${ungrantable ? ' disabled title="This can only be allowed once"' : ""}>
              <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>${menu}
          </span>
        </div>
      </div>
      <p class="chat-ask-pay"><span class="chat-ask-glyph" aria-hidden="true">${glyph}</span><span class="chat-ask-value mono">${esc(value)}</span>${note ? `<span class="chat-ask-note mono">${esc(note)}</span>` : ""}</p>
      ${why ? `<p class="chat-ask-why">${esc(why)}</p>` : ""}
    </li>`;
  }

  return { permissionCardHtml, permQueueCardHtml };
}

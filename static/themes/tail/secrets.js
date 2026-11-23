// The Protect secrets window. Dependencies are live getters supplied by index.js.
export function create(deps) {
// One <dialog data-secrets-window>: chat/secrets.js opens it as a modal and
// finds its parts by the data-secrets-* hooks. It sets the Protect label.
function secretsWindowHtml(rows) {
    const esc = deps.escapeHtml;
    const body = rows.map((row) => {
      const nameId = "chat-secrets-name-" + row.id;
      // A value with no NAME= is named by its kind, in the quieter style.
      const name = row.keyed
        ? `<span class="chat-secrets-key" id="${nameId}">${esc(row.name)}</span><span class="chat-secrets-kind">${esc(row.kind)}</span>`
        : `<span class="chat-secrets-key is-kind" id="${nameId}">${esc(row.name)}</span>`;
      // Words or a number: protecting it also swaps that text wherever else it appears.
      const warn = row.plain
        ? `<p class="chat-secrets-warn">Reads like ordinary text. If you protect it, models get a stand-in wherever this exact text appears, also in code and names.</p>`
        : "";
      const host = row.host ? `<span class="chat-secrets-host">${esc(row.host)}</span> ` : "";
      return `<tr data-secrets-row="${row.id}">
        <td class="chat-secrets-tick"><input type="checkbox" data-secrets-check${row.plain ? "" : " checked"} aria-labelledby="${nameId}"></td>
        <td class="chat-secrets-name">${name}</td>
        <td class="chat-secrets-value"><div>${esc(row.value)}</div>${warn}</td>
        <td class="chat-secrets-where">${host}<span>${esc(row.place)}</span></td>
      </tr>`;
    }).join("");
    return `<dialog class="modal chat-secrets" role="dialog" aria-modal="true" aria-labelledby="chat-secrets-title" data-secrets-window>
      <header class="chat-secrets-head">
        <h2 id="chat-secrets-title">Protect secrets</h2>
        <p>Fuse found these in this project's env files. Ticked values go to models as same-format stand-ins; you and your machines keep the real ones. This applies to all your chats.</p>
      </header>
      <div class="chat-secrets-list">
        <table>
          <thead><tr>
            <th class="chat-secrets-tick" scope="col"><label><input type="checkbox" data-secrets-all checked aria-label="Select all"><span>Select all</span></label></th>
            <th class="chat-secrets-name" scope="col">Name</th>
            <th class="chat-secrets-value" scope="col">Value</th>
            <th class="chat-secrets-where" scope="col">Found in</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <p class="chat-secrets-error" data-secrets-error role="alert" hidden></p>
      <div class="chat-secrets-actions">
        <button type="button" class="chat-secrets-never" data-secrets-never>Turn off detection</button>
        <button type="button" class="btn ghost" data-secrets-later>Not now</button>
        <button type="button" class="btn" data-secrets-protect></button>
      </div>
    </dialog>`;
  }

  // The web search setup window (chat/websetup.js): the user's connectors to
  // tick, or a way to add one. The house dialog of the Protect secrets window,
  // narrower. chat/websetup.js sets the ticks and the save label.
  function webSetupWindowHtml(rows, opts = {}) {
    const esc = deps.escapeHtml;
    const body = rows.map((row) => {
      const nameId = "chat-websetup-name-" + row.id;
      const ip = row.ip || (opts.reading && row.usable && row.online ? "reading…" : "not read yet");
      const shares = row.shares.length ? `<span class="chat-secrets-kind">same IP as ${esc(row.shares.join(", "))}</span>` : "";
      return `<tr data-websetup-row="${row.id}"${row.usable ? "" : ' class="is-off"'}>
        <td class="chat-secrets-tick"><input type="checkbox" data-websetup-check${row.usable ? "" : " disabled"} aria-labelledby="${nameId}"></td>
        <td class="chat-secrets-name"><span class="chat-secrets-key" id="${nameId}">${esc(row.alias)}</span><span class="chat-secrets-kind">${esc(row.status)}</span></td>
        <td class="chat-secrets-where"><span>${esc(ip)}</span>${shares}</td>
      </tr>`;
    }).join("");
    const list = rows.length
      ? `<div class="chat-secrets-list">
          <table>
            <thead><tr>
              <th class="chat-secrets-tick" scope="col"><span class="visually-hidden">Use</span></th>
              <th class="chat-secrets-name" scope="col">Machine</th>
              <th class="chat-secrets-where" scope="col">Public IP</th>
            </tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>`
      : "";
    const why = rows.length
      ? "Web searches and page reads go out through the machines you tick, not from this server, and sites see that machine's public IP. Machines that share an IP only back each other up, so one per network is enough."
      : "Web searches and page reads go out through one of your machines, not from this server. Add a machine with the Fuse connector, then pick it here.";
    const primary = rows.length
      ? '<button type="button" class="btn" data-websetup-save></button>'
      : '<a class="btn" href="/hosts" data-websetup-add>Add a machine</a>';
    return `<dialog class="modal chat-secrets is-web" role="dialog" aria-modal="true" aria-labelledby="chat-websetup-title" data-websetup-window>
      <header class="chat-secrets-head">
        <h2 id="chat-websetup-title">${rows.length ? "Set up web search" : "Web search needs a machine"}</h2>
        <p>${why}</p>
      </header>
      ${list}
      <p class="chat-secrets-error" data-websetup-error role="alert" hidden></p>
      <div class="chat-secrets-actions">
        <a class="chat-secrets-never" href="/settings#web">Web search settings</a>
        <button type="button" class="btn ghost" data-websetup-later>Not now</button>
        ${primary}
      </div>
    </dialog>`;
  }

  return { secretsWindowHtml, webSetupWindowHtml };
}

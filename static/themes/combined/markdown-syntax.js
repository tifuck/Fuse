// markdown-syntax presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function citeHost(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "") || url;
    } catch (_) {
      return url;
    }
  }

function citeUrlFromPayload(payload, catalog) {
    const parts = String(payload || "")
      .split(/\uE202/)
      .map((part) => part.trim())
      .filter(Boolean);
    for (const part of parts) {
      const url = deps.safeUrl(part);
      if (url && /^https?:/i.test(url)) return url;
    }
    const joined = parts.join(" ");
    const indexed = joined.match(/turn\d+(?:search|news|ref)(\d+)/i) || joined.match(/(?:search|news|ref)(\d+)/i);
    if (indexed && catalog && catalog.length) {
      const url = catalog[Number(indexed[1])];
      if (url) return url;
    }
    return "";
  }

function rewriteCites(text, catalog, streaming) {
    let out = String(text || "").replace(/[\uE200\uE206]cite\uE202([^\uE201]*)\uE201/gi, (_, payload) => {
      const url = citeUrlFromPayload(payload, catalog);
      return url ? `[${citeHost(url)}](${url})` : "";
    });
    if (streaming) out = out.replace(/[\uE200\uE206]cite(?:\uE202[^\uE201]*)?$/i, "");
    else out = out.replace(/[\uE200-\uE20F]/g, "");
    // Bounded label: an unbounded one backtracks over every unmatched "[".
    out = out.replace(/\[([^\]\n]{1,300})\]\((https?:\/\/[^)\s]+)\)(?:\s+\2)?/g, "[$1]($2)");
    return out;
  }

function linkHtml(url, label, cls) {
    // Relative /a/<id>, or the same page as a full link on this site (a
    // share URL built from PUBLIC_BASE_URL).
    let art = url.match(/^\/a\/([A-Za-z0-9_-]+)$/);
    if (!art && typeof location !== "undefined") {
      const full = url.match(/^https?:\/\/([^/?#]+)\/a\/([A-Za-z0-9_-]+)$/i);
      if (full && full[1].toLowerCase() === location.host.toLowerCase()) art = [url, full[2]];
    }
    if (art) {
      return `<a class="chat-md-artifact" href="${deps.escapeHtml(url)}" data-open-artifact="${deps.escapeHtml(art[1])}">${label}</a>`;
    }
    let klass = cls || "";
    if (!klass && /^https?:/i.test(url)) {
      try {
        const host = new URL(url).hostname.replace(/^www\./, "");
        if (label === host || label === url) klass = "chat-md-cite";
      } catch (_) {}
    }
    const attr = klass ? ` class="${klass}"` : "";
    const extra = /^mailto:/i.test(url) ? "" : ' target="_blank" rel="noopener noreferrer"';
    return `<a${attr} href="${deps.escapeHtml(url)}"${extra}>${label}</a>`;
  }

// Inline markdown (static/chat/markdown.js parses it) as HTML. `ctx` carries
// the citation catalog and whether the text is still streaming.
function renderInline(text, ctx = {}) {
    const src = rewriteCites(text, ctx.catalog || [], ctx.streaming);
    return renderNodes(deps.parseInline(src));
  }

function renderNodes(nodes) {
    return nodes.map((node) => {
      switch (node.t) {
        case "text": return deps.escapeHtml(node.v);
        case "code": return `<code>${deps.escapeHtml(node.v)}</code>`;
        case "br": return "<br>";
        case "em": return `<em>${renderNodes(node.c)}</em>`;
        case "strong": return `<strong>${renderNodes(node.c)}</strong>`;
        case "del": return `<del>${renderNodes(node.c)}</del>`;
        case "link": {
          const label = renderNodes(node.c);
          const url = deps.safeUrl(node.href);
          return url ? linkHtml(url, label || deps.escapeHtml(url)) : label;
        }
        // Remote images are CSP-blocked (img-src 'self' data:), so image syntax
        // renders as a link to the source instead of a permanently broken <img>.
        case "image": {
          const url = deps.safeUrl(node.href);
          const alt = deps.escapeHtml(node.alt);
          return url && /^https?:/i.test(url) ? linkHtml(url, alt || deps.escapeHtml(url)) : alt;
        }
        case "autolink": {
          const url = deps.safeUrl(node.href);
          return url ? linkHtml(url, deps.escapeHtml(node.label)) : deps.escapeHtml(node.label);
        }
        default: return "";
      }
    }).join("");
  }

function trimFence(code) {
    const lines = String(code || "").replace(/^\n+/, "").replace(/\n+$/, "").split("\n");
    let pad = null;
    for (const line of lines) {
      if (!line.trim()) continue;
      const n = (line.match(/^[ \t]*/) || [""])[0].length;
      pad = pad === null ? n : Math.min(pad, n);
      if (pad === 0) break;
    }
    if (!pad) return lines.join("\n");
    return lines.map((line) => line.slice(pad)).join("\n");
  }

function codeLanguage(lang) {
    const name = String(lang || "").toLowerCase().split(/\s+/)[0];
    const aliases = {
      "c++": "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
      cs: "csharp", html: "xml", py3: "python", python3: "python",
      jsonc: "json", node: "javascript", postgres: "sql", postgresql: "sql",
      sqlite: "sql", console: "shell", patch: "diff", udiff: "diff",
    };
    return aliases[name] || name;
  }

function highlightCode(lang, code) {
    const source = String(code || "");
    if (deps.detailProbe) return deps.escapeHtml(source);
    const language = codeLanguage(lang);
    if (!window.hljs || !language || source.length > 100000 || !window.hljs.getLanguage(language)) {
      return deps.escapeHtml(source);
    }
    const key = language + "\u0000" + source;
    const cached = _hljsCache.get(key);
    if (cached !== undefined) return cached;
    let html;
    try {
      html = window.hljs.highlight(source, { language, ignoreIllegals: true }).value;
    } catch (_) {
      return deps.escapeHtml(source);
    }
    if (_hljsCache.size >= _HLJS_CACHE_MAX) {
      _hljsCache.clear();
    }
    _hljsCache.set(key, html);
    return html;
  }

function renderFence(lang, code) {
    const label = deps.escapeHtml((lang || "code").split(/\s+/)[0] || "code");
    return `<div class="chat-code"><div class="chat-code-head"><span>${label}</span><button type="button" class="chat-icon-btn" data-copy-code title="Copy" aria-label="Copy">${ACTION_ICONS.copy}</button></div><pre><code>${highlightCode(lang, trimFence(code))}</code></pre></div>`;
  }

function renderHeading(token, ctx) {
    return `<h${token.level}>${renderInline(token.text, ctx)}</h${token.level}>`;
  }

function renderTable(token, ctx) {
    const cell = (tag, text, i) => {
      const align = token.align[i];
      return `<${tag}${align ? ` style="text-align:${align}"` : ""}>${renderInline(text, ctx)}</${tag}>`;
    };
    const width = token.head.length;
    const row = (cells, tag) => `<tr>${Array.from({ length: width }, (_, i) => cell(tag, cells[i] || "", i)).join("")}</tr>`;
    return `<table><thead>${row(token.head, "th")}</thead><tbody>${token.rows.map((cells) => row(cells, "td")).join("")}</tbody></table>`;
  }

// A list item's first paragraph sits in the <li> itself; what follows (more
// paragraphs, code, nested lists) renders as blocks under it.
function renderList(token, ctx) {
    const tag = token.ordered ? "ol" : "ul";
    const open = token.ordered && token.start !== 1 ? `<ol start="${token.start}">` : `<${tag}>`;
    const items = token.items.map((item) => {
      const [first, ...rest] = item.children;
      const lead = first && first.type === "paragraph" ? renderInline(first.text, ctx) : "";
      const body = renderTokens(lead ? rest : item.children, ctx);
      // A drawn box: global form styles would restyle a real <input>.
      const box = item.task === null ? "" : `<span class="chat-md-check${item.task ? " is-done" : ""}" role="checkbox" aria-checked="${item.task}" aria-disabled="true"></span>`;
      return `<li${box ? ' class="chat-md-task"' : ""}>${box}${lead}${body}</li>`;
    });
    return open + items.join("") + `</${tag}>`;
  }

function renderQuote(token, ctx) {
    const body = renderTokens(token.children, ctx);
    if (token.alert) {
      return `<blockquote class="chat-md-alert is-${token.alert}"><span class="chat-md-alert-label">${MD_ALERTS[token.alert]}</span>${body}</blockquote>`;
    }
    return `<blockquote>${body}</blockquote>`;
  }

function renderTokens(tokens, ctx) {
    return tokens.map((token) => {
      switch (token.type) {
        case "paragraph": return `<p>${renderInline(token.text, ctx)}</p>`;
        case "heading": return renderHeading(token, ctx);
        case "hr": return "<hr>";
        case "code": return renderFence(token.lang, token.text);
        case "quote": return renderQuote(token, ctx);
        case "list": return renderList(token, ctx);
        case "table": return renderTable(token, ctx);
        default: return "";
      }
    }).join("");
  }

// Markdown without <details> structure: parse, then draw the blocks.
function renderPlain(text, catalog, streaming) {
    return renderTokens(deps.parseBlocks(text), { catalog: catalog || [], streaming });
  }

function renderDetailsBlocks(text, catalog, streaming) {
    const src = String(text || "");
    // Only block-position tags are structural. Inline code, prose examples and
    // fenced HTML must never pair with a closing tag later in the answer.
    const roots = [], stack = [];
    let offset = 0, fence = null;
    for (const line of src.split("\n")) {
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      } else if (marker) {
        fence = marker[1];
      } else {
        const opening = line.match(/^ {0,3}<details(\s+open)?\s*>(?=\s*(?:<summary>|$))/i);
        if (opening && stack.length < 32) {
          const node = { start: offset, body: offset + opening[0].length, open: Boolean(opening[1]), children: [] };
          (stack.length ? stack[stack.length - 1].children : roots).push(node);
          stack.push(node);
        }
        // A close may follow an inline summary/body, but never a backtick
        // example or a tag mentioned in ordinary prose.
        const close = line.match(/<\/details>\s*$/i);
        if (close && stack.length && (opening || /^ {0,3}<\/details>\s*$/i.test(line))) {
          const node = stack.pop();
          node.close = offset + close.index;
          node.end = node.close + 10;
        }
      }
      offset += line.length + 1;
    }
    const renderRange = (start, end, nodes) => {
      let html = "", cursor = start;
      for (const node of nodes) {
        if (node.end == null) continue; // Incomplete streamed markup stays literal.
        html += renderPlain(src.slice(cursor, node.start), catalog, streaming);
        const inner = src.slice(node.body, node.close);
        const summaryMatch = inner.match(/^\s*<summary>([^\n]*?)<\/summary>[ \t]*\n?/i);
        const summary = summaryMatch ? renderInline(summaryMatch[1].trim(), { catalog, streaming }) : "Details";
        const bodyStart = node.body + (summaryMatch ? summaryMatch[0].length : 0);
        html += `<details class="chat-md-details" data-md-details="${node.start}"${node.open ? " open" : ""}><summary>${summary || "Details"}</summary>${renderRange(bodyStart, node.close, node.children)}</details>`;
        cursor = node.end;
      }
      return html + renderPlain(src.slice(cursor, end), catalog, streaming);
    };
    return renderRange(0, src.length, roots);
  }

function looksLikeDiff(text) {
    const lines = String(text || "").split("\n");
    if (lines.length < 2) return false;
    const hits = lines.filter((line) => /^(@@ |\+\+\+ |--- |diff --git |index |\+[^+]|-[^-])/.test(line)).length;
    return hits >= 2;
  }

function renderAnsi(text) {
    const src = String(text || "");
    if (!/\x1b\[/.test(src)) return deps.escapeHtml(src);
    let html = "";
    let i = 0;
    let bold = false;
    let underline = false;
    let color = "";
    const open = () => {
      const cls = [bold ? "is-bold" : "", underline ? "is-underline" : "", color ? "ansi-" + color : ""].filter(Boolean);
      return cls.length ? `<span class="${cls.join(" ")}">` : "";
    };
    let wrap = open();
    const flush = (chunk) => {
      if (!chunk) return;
      html += wrap ? wrap + deps.escapeHtml(chunk) + "</span>" : deps.escapeHtml(chunk);
    };
    while (i < src.length) {
      const esc = src.indexOf("\x1b[", i);
      if (esc === -1) {
        flush(src.slice(i));
        break;
      }
      flush(src.slice(i, esc));
      const end = src.indexOf("m", esc + 2);
      if (end === -1 || end - esc > 24) {
        flush(src.slice(esc, esc + 1));
        i = esc + 1;
        continue;
      }
      const codes = src.slice(esc + 2, end).split(";").map((n) => Number(n) || 0);
      codes.forEach((code) => {
        if (code === 0) { bold = false; underline = false; color = ""; }
        else if (code === 1) bold = true;
        else if (code === 4) underline = true;
        else if (code === 22) bold = false;
        else if (code === 24) underline = false;
        else if (code === 39) color = "";
        else if (ANSI_COLORS[code]) color = ANSI_COLORS[code];
      });
      wrap = open();
      i = end + 1;
    }
    return html;
  }

const ACTION_ICONS = {
    copy: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5.5" y="5.5" width="7" height="8" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 5.5V4.2A1.2 1.2 0 0 0 9.3 3H4.2A1.2 1.2 0 0 0 3 4.2v7.1A1.2 1.2 0 0 0 4.2 12.5H5.5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    copied: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.6 8.6l3 3 5.8-6.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.2 3.4l3.4 3.4L6 13.4H2.6v-3.4zM8.1 4.5l3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    retry: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M12.6 8A4.6 4.6 0 1 1 11.3 4.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M11.2 2.4v2.6h-2.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    more: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="4" cy="8" r="1.05" fill="currentColor"/><circle cx="8" cy="8" r="1.05" fill="currentColor"/><circle cx="12" cy="8" r="1.05" fill="currentColor"/></svg>',
    rewind: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.4 8a4.6 4.6 0 1 0 1.4-3.3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M3.2 3.4v2.7h2.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

const _hljsCache = new Map();

const _HLJS_CACHE_MAX = 600;

const MD_ALERTS = { note: "Note", tip: "Tip", important: "Important", warning: "Warning", caution: "Caution" };

const ANSI_COLORS = { 30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan", 37: "white", 90: "bright-black", 91: "bright-red", 92: "bright-green", 93: "bright-yellow", 94: "bright-blue", 95: "bright-magenta", 96: "bright-cyan", 97: "bright-white" };

  return { citeHost, citeUrlFromPayload, rewriteCites, linkHtml, renderInline, renderNodes, trimFence, codeLanguage, highlightCode, renderFence, renderHeading, renderTable, renderList, renderQuote, renderTokens, renderPlain, renderDetailsBlocks, looksLikeDiff, renderAnsi, ACTION_ICONS, _hljsCache, _HLJS_CACHE_MAX, MD_ALERTS, ANSI_COLORS };
}

// transcript presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
const ARTIFACT_VERBS = { create_artifact: "Created", update_artifact: "Updated", edit_artifact: "Edited" };

// One card per artifact a reply made or changed, from its latest call there.
// The call row says where it happened; the card is what the reader got. An
// inline artifact being written holds its place while it is drawn.
function artifactCards(traces) {
    const byId = new Map();
    traces.forEach((trace) => {
      if (trace && trace.status === "running" && trace.name === "create_artifact" && trace.display === "inline") {
        byId.set("draft:" + trace.id, { id: "", draft: true, display: "inline", title: trace.title || "the chart" });
        return;
      }
      const verb = trace && trace.status === "done" && ARTIFACT_VERBS[trace.name];
      const id = verb ? String(trace.identifier || "") : "";
      if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) return;
      byId.delete(id);
      byId.set(id, { id, verb, title: trace.title || "Artifact", version: trace.version, format: trace.format || "", gone: trace.artifact_gone || "", display: trace.display || "" });
    });
    return [...byId.values()];
  }

function artifactCardHtml(card) {
    const meta = card.gone ? (card.gone === "archived" ? "Archived" : "Deleted")
      : [card.verb, card.version != null ? `v${card.version}` : "", card.format === "markdown" ? "Markdown" : ""].filter(Boolean).join(", ");
    const icon = deps.STEP_ICONS.artifact;
    if (card.gone) {
      return `<div class="chat-artifact-card is-gone">` +
        `<span class="chat-artifact-card-icon" aria-hidden="true">${icon}</span>` +
        `<span class="chat-artifact-card-copy"><span class="chat-artifact-card-title">${deps.escapeHtml(card.title)}</span>` +
        `<span class="chat-artifact-card-meta">${meta}</span></span></div>`;
    }
    return `<button type="button" class="chat-artifact-card" data-open-artifact="${deps.escapeHtml(card.id)}" aria-label="Open artifact ${deps.escapeHtml(card.title)}">` +
      `<span class="chat-artifact-card-icon" aria-hidden="true">${icon}</span>` +
      `<span class="chat-artifact-card-copy"><span class="chat-artifact-card-title">${deps.escapeHtml(card.title)}</span>` +
      `<span class="chat-artifact-card-meta">${deps.escapeHtml(meta)}</span></span>` +
      `<span class="chat-artifact-card-open" aria-hidden="true">Open ↗</span></button>`;
  }

// An inline artifact: its page in the reply, as tall as it says (the
// runtime sizes the frame from the page's size messages), and a caption.
function inlineArtifactHtml(card) {
    const title = deps.escapeHtml(card.title);
    if (card.draft) return `<figure class="chat-artifact-inline is-drawing" aria-busy="true"><div class="chat-artifact-inline-wait">Drawing ${title}…</div></figure>`;
    const inline = deps.artifactInline;
    const id = deps.escapeHtml(card.id);
    const version = deps.escapeHtml(String(card.version ?? ""));
    const height = inline.height(card.id, card.version);
    return `<figure class="chat-artifact-inline" data-artifact-inline="${id}">` +
      `<iframe class="chat-artifact-inline-frame" data-artifact-inline-frame data-id="${id}" data-version="${version}" src="${deps.escapeHtml(inline.url(card.id, card.version))}" title="${title}" sandbox="allow-scripts allow-forms allow-modals allow-downloads" allow="clipboard-write" referrerpolicy="no-referrer" loading="lazy"${height ? ` style="height:${height}px"` : ""}></iframe>` +
      `<figcaption class="chat-artifact-inline-cap"><span class="chat-artifact-inline-icon" aria-hidden="true">${deps.STEP_ICONS.artifact}</span>` +
      `<span class="chat-artifact-inline-title">${title}</span><span class="chat-artifact-inline-note" data-artifact-inline-note hidden></span>` +
      `<button type="button" class="chat-artifact-inline-open" data-open-artifact="${id}" aria-label="Open artifact ${title}">Open ↗</button></figcaption></figure>`;
  }

// A line holding nothing but a link to an artifact that has a card here only
// repeats the card (models put one there, as the tools used to ask).
function dropCardLinks(text, ids) {
    if (!ids.size || !text.includes("/a/")) return text;
    return text.replace(/^[ \t>*_-]*\[[^\]\n]*\]\((?:https?:\/\/[^)\s]+)?\/a\/([A-Za-z0-9_-]{8,64})\)[ \t*_.]*$/gm,
      (line, id) => (ids.has(id) ? "" : line));
  }

function activityEntriesHtml(entries, msg, live, blockKey, blockLive = false) {
    return deps.consoleActivityEntriesHtml(entries, msg, live, blockLive);

  }

// Tail: one line per burst of work. A tick per call (tall: it changed a file,
// failed or is running), how many there were, what they changed and how long
// they took. The line opens the calls. A burst without calls prints bare.
function burstHtml(traces, live, open, aid) {
    // A run_code's own calls are inside its call (its row counts them, its
    // time holds theirs): only what they changed adds up here.
    const calls = traces.filter((trace) => !trace.parent && deps.ccToolClass(trace)?.kind !== "silent");
    if (!calls.length) return "";
    let plus = 0, minus = 0, failed = 0, ms = 0, now = null;
    // The calls that changed a file, a run_code for the edits its code made.
    const changed = new Set();
    for (const trace of traces) {
      const p = deps.toolPresentation(trace, live);
      if ((trace.name !== "search_replace" && trace.name !== "write_file") || p.failed || p.running) continue;
      const counts = deps.consoleEditCounts(trace);
      plus += counts.added;
      minus += counts.removed;
      changed.add(trace.parent ? traces.find((t) => t.id === trace.parent) : trace);
    }
    const ticks = calls.map((trace) => {
      const p = deps.toolPresentation(trace, live);
      // A call the step gate held back did not run, so it did not fail.
      const bad = trace.kind !== "not_run" && (p.failed || (trace.name === "run_terminal_command" && p.state === "warning" &&
        deps.ccExitMeaning(deps.displayCommand(trace), deps.shellExitCode(trace)).isError));
      const change = changed.has(trace);
      if (bad) failed += 1;
      else if (p.running) now = trace;
      const took = Number(trace.latency_ms ?? trace.duration_ms);
      if (Number.isFinite(took) && took > 0) ms += took;
      return `<i class="${p.running ? "is-live" : bad ? "is-bad" : p.interrupted ? "is-stop" : change ? "is-change" : trace.name === "run_terminal_command" ? "is-run" : ""}"></i>`;
    });
    // A long burst keeps its newest ticks and every failed or stopped one;
    // the count still says how many ran.
    const marked = (tick) => /"is-(?:bad|stop)"/.test(tick);
    let room = Math.max(0, 40 - ticks.filter(marked).length);
    const shown = ticks.reduceRight((kept, tick) => (marked(tick) || room-- > 0 ? [tick, ...kept] : kept), []).join("");
    const stat = [
      plus || minus ? `<span><span class="tail-plus">+${plus}</span>${minus ? ` <span class="tail-minus">−${minus}</span>` : ""}</span>` : "",
      failed ? `<span class="tail-fail">${failed} failed</span>` : "",
      ms >= 1000 ? `<span>${deps.escapeHtml(deps.ccDuration(ms))}</span>` : "",
    ].join("");
    return `<button type="button" class="tail-burst" data-activity-summary aria-expanded="${open ? "true" : "false"}" aria-controls="${aid}">` +
      `<span class="tail-burst-chev" aria-hidden="true">${deps.STEP_CHEVRON}</span>` +
      `<span class="tail-ticks" aria-hidden="true">${shown}</span>` +
      `<span class="tail-burst-count"><b>${calls.length}</b> ${calls.length === 1 ? "call" : "calls"}</span>` +
      (now ? `<span class="tail-burst-now">${deps.escapeHtml(String(now.description || deps.displayCommand(now) || now.path || now.url || now.pattern || "working").split("\n")[0])}</span>` : "") +
      `<span class="tail-burst-stat">${stat}</span></button>`;
  }

function activityBlockHtml(entries, msg, live, blockIndex, showElapsed, turnLive = live) {
    const key = String(blockIndex);
    const aid = "chat-activity-" + ++deps.activitySeq;
    const traces = entries.filter((entry) => entry.type === "tool").map((entry) => entry.trace);
    const reasoning = entries.filter((entry) => entry.type === "reasoning").map((entry) => entry.text).join("");
    const groups = deps.groupTraces(traces);
    const waiting = live && !entries.length;
    if (waiting) return "";
    const blockMsg = { ...msg, reasoning, content: "" };
    const kind = deps.activityKind(blockMsg, groups, live);
    const stepLive = turnLive || Boolean(msg.tools_live);
    const steps = activityEntriesHtml(entries, msg, stepLive, key, live);
    // A burst is folded until its line is opened (assistant-disclosure.js).
    const open = Boolean(msg.stepOpen?.["burst-" + key]);
    const summary = burstHtml(traces, stepLive, open, aid);
    const classes = ["chat-activity"];
    if (open || !summary) classes.push("is-open");
    // A model wait/background handoff must not hide already-rendered tools.
    if (kind) classes.push(kind === "waiting" ? "is-pending-phase" : `is-${kind}`);
    if (!summary) classes.push("is-bare");
    if (!live && msg.stopped) classes.push("is-stopped");
    if (!live && msg.error) classes.push("is-failed");
    return (
      `<section class="${classes.join(" ")}" data-activity data-activity-block="${key}">` +
      summary +
      `<div class="chat-activity-panel" id="${aid}" role="region" aria-label="Activity details"><div class="chat-activity-panel-inner"><ol class="chat-timeline">${steps}</ol></div></div>` +
      "</section>"
    );
  }

// Run notices (provider retries, cut-off calls, limits) sit where they
// happened; only a trailing run of them is passed in, newest line bright.
function noticeBlockHtml(lines, live) {
    return `<ul class="chat-notice${live ? " is-live" : ""}" data-run-notice role="status" aria-label="Fuse status">` +
      lines.map((line, i) => `<li class="chat-notice-line${live && i === lines.length - 1 ? " is-current" : ""}">${deps.escapeHtml(line)}</li>`).join("") +
      "</ul>";
  }

function markerBlockHtml(block) {
    if (block.markerKind === "resume") {
      return `<ul class="chat-notice" data-resume-marker role="status" aria-label="Fuse status"><li class="chat-notice-line">${deps.escapeHtml(block.text)}</li></ul>`;
    }
    return deps.compactionMarkHtml(block.text, block.summary);
  }

// Background results handed to the model mid-reply: the same rows a wake
// turn shows for them (consoleAutoStepsHtml), at the point they landed.
function deliveryBlockHtml(block, msg) {
    const steps = deps.consoleAutoStepsHtml({ id: `${msg.id || ""}-d${block.segmentIndex}`, autoSteps: deps.deliveryTraces(block.text) });
    return `<div class="chat-auto-steps is-delivery" data-delivery="${block.segmentIndex}" role="group" aria-label="Background results"${steps ? "" : " hidden"}>` +
      `<ol class="chat-timeline">${steps}</ol></div>`;
  }

function visibleAssistantError(msg) {
    if (!msg.error) return "";
    const index = deps.messages.findIndex(item => item === msg || (msg.id && item.id === msg.id));
    if (index >= 0 && deps.messages.slice(index + 1).some(item => item.role === "user")) return "";
    return String(msg.error).replace(/\s+/g, " ").trim();
  }

function syncNotices(article, lines, live) {
    const box = article.querySelector("[data-notice]");
    if (!box) return;
    const key = lines.join("\n") + "\0" + (live ? 1 : 0);
    if (box._key === key) return;
    box._key = key;
    box.hidden = !lines.length;
    box.classList.toggle("is-live", Boolean(live));
    box.innerHTML = lines.map((line, i) =>
      `<li class="chat-notice-line${live && i === lines.length - 1 ? " is-current" : ""}">${deps.escapeHtml(line)}</li>`).join("");
  }

function updateAssistant(article, msg, live) {
    // Off-screen historical responses are hydrated from the latest message
    // object when they enter the viewport, rather than on every sync event.
    if (!article || article.hasAttribute("data-deferred-message")) return;
    if (deps.log.contains(article) && article.dataset.messageId && article.dataset.messageId !== msg.id) { deps.pendingFullPaint = true; return; }
    article._rebind?.(msg);
    if (deps.toolMotions.has(article)) { deps.motionPaints.set(article, { msg, live }); return; }
    if (deps.toolGestureOwner === article && article.isConnected) {
      deps.pendingToolPaint = { article, msg, live };
      return;
    }
    const flow = article.querySelector("[data-flow]");
    const err = article.querySelector("[data-err]");
    const foot = article.querySelector("[data-foot]");
    const meta = article.querySelector("[data-meta]");
    const traces = Array.isArray(msg.tools) ? msg.tools : [];
    const isNotice = (part) => part && part.type === "marker" && part.kind === "notice";
    const hasMarker = Array.isArray(msg.segments) && msg.segments.some((part) => part && ((part.type === "marker" && !isNotice(part)) || part.type === "delivery"));
    const errText = visibleAssistantError(msg);
    const segments = deps.assistantSegments(msg);
    // A run notice is status for the step it interrupted: shown while nothing
    // has followed it in a live turn, or beside the failure it ended in.
    let noticeFrom = segments.length;
    while (noticeFrom && isNotice(segments[noticeFrom - 1])) noticeFrom -= 1;
    const showNotices = Boolean(live || errText);
    const runNotices = showNotices ? segments.slice(noticeFrom).map((part) => part.text || "").filter(Boolean) : [];
    // msg.notice holds end-of-turn status (Stopped, interrupted, stalled).
    // Older messages also kept every retry line there, so it shows only on a
    // turn that is live or did not finish cleanly.
    const noticeLines = live || errText || msg.stopped
      ? String(msg.notice || "").split("\n").map((line) => line.trim()).filter(Boolean)
      : [];
    const hasOutput = Boolean((msg.content || "").trim() || (msg.reasoning || "").trim() || errText || (noticeLines.length && live) || runNotices.length || traces.length || msg.permissions?.length || hasMarker);
    article.hidden = !hasOutput;
    const sourceMap = new Map();
    traces.forEach((trace) => {
      const rows = trace.results || (trace.name === "read_url" && trace.status === "done" && trace.url ? [{ url: trace.url, status: "read" }] : []);
      rows.filter((row) => /^https?:/i.test(deps.safeUrl(row.url))).forEach((row) => {
        const prior = sourceMap.get(row.url) || {};
        sourceMap.set(row.url, { ...prior, ...row, status: prior.status === "read" || row.status === "read" || trace.name === "read_url" ? "read" : "found" });
      });
    });
    const content = String(msg.content || "");
    const sources = [...sourceMap.values()].map((row) => ({ ...row, cited: content.includes(row.url) }));
    const citations = article.querySelector("[data-citations]");
    const inActivity = Boolean(traces.length);
    // Hidden states are compared before they are written: a streamed frame
    // repeats them all, and a repeated write is still a DOM mutation.
    const noCitations = inActivity || !sources.length || !msg.content;
    if (citations.hidden !== noCitations) citations.hidden = noCitations;
    const sourceGrid = article.querySelector("[data-source-grid]");
    const sourceSignature = JSON.stringify(sources);
    if (sourceGrid._src !== sourceSignature) {
      sourceGrid._src = sourceSignature;
      sourceGrid.innerHTML = deps.sourceList(sources);
      article.querySelector("[data-source-count]").textContent = `${sources.length} ${sources.length === 1 ? "source" : "sources"}`;
    }
    const catalog = sources.map((row) => row.url);
    const allCards = artifactCards(traces);
    const cardIds = new Set(allCards.map((card) => card.id).filter(Boolean));
    const isInline = (card) => card.display === "inline" && !card.gone;
    const inlineCards = allCards.filter(isInline);
    const cards = allCards.filter((card) => !isInline(card));
    const inlineEl = article.querySelector("[data-artifact-inline-list]");
    if (inlineEl) {
      const inlineSig = JSON.stringify(inlineCards);
      if (inlineEl._sig !== inlineSig) {
        inlineEl._sig = inlineSig;
        inlineEl.innerHTML = inlineCards.map(inlineArtifactHtml).join("");
      }
      if (inlineEl.hidden !== !inlineCards.length) inlineEl.hidden = !inlineCards.length;
    }
    const cardsEl = article.querySelector("[data-artifact-cards]");

    if (cardsEl) {
      const cardSig = JSON.stringify(cards);
      if (cardsEl._sig !== cardSig) {
        cardsEl._sig = cardSig;
        cardsEl.innerHTML = cards.map(artifactCardHtml).join("");
      }
      if (cardsEl.hidden !== !cards.length) cardsEl.hidden = !cards.length;
    }
    const toolById = new Map(traces.map((trace, index) => [trace.id, { trace, index }]));
    const permById = new Map((msg.permissions || []).map((perm) => [perm.id, perm]));
    const normalized = segments.flatMap((part, segmentIndex) => {
      if (!part || !part.type) return [];
      if (isNotice(part)) return showNotices && segmentIndex >= noticeFrom && part.text ? [{ type: "notice", segmentIndex, text: part.text }] : [];
      if (part.type === "marker") {
        return [{ type: "marker", segmentIndex, text: part.text || "", kind: part.kind || "compaction", summary: part.summary || "" }];
      }
      if (part.type === "delivery") return part.text ? [{ type: "delivery", segmentIndex, text: part.text }] : [];
      if (part.type === "tool") {
        const found = toolById.get(part.id);
        if (!found) return [];
        return [{ type: "tool", segmentIndex, ...found }];
      }
      if (part.type === "permission") {
        const found = permById.get(part.id);
        if (!found || found.kind === "tool_permission") return [];
        return [{ type: "permission", segmentIndex, perm: found }];
      }
      // Thinking hidden (Show thinking, /thinking): no view prints thoughts.
      if (part.type === "reasoning" && !deps.showThinking) return [];
      if (part.type === "reasoning" && live && segmentIndex === segments.length - 1) return [{ type: part.type, text: part.text || "", segmentIndex }];
      return typeof part.text === "string" && part.text ? [{ type: part.type, text: part.text, segmentIndex }] : [];
    });
    const blocks = [];
    normalized.forEach((part) => {
      if (part.type === "permission") {
        blocks.push({ kind: "permission", entries: [part] });
        return;
      }
      if (part.type === "marker") {
        blocks.push({ kind: "marker", markerKind: part.kind, text: part.text, summary: part.summary || "" });
        return;
      }
      if (part.type === "delivery") {
        blocks.push({ kind: "delivery", text: part.text, segmentIndex: part.segmentIndex });
        return;
      }
      if (part.type === "notice") {
        const last = blocks[blocks.length - 1];
        if (last && last.kind === "notices") last.lines.push(part.text);
        else blocks.push({ kind: "notices", lines: [part.text] });
        return;
      }
      const kind = part.type === "content" ? "content" : "activity";
      const last = blocks[blocks.length - 1];
      if (last && last.kind === kind) last.entries.push(part);
      else blocks.push({ kind, entries: [part] });
    });
    const lastActivity = blocks.map((block) => block.kind).lastIndexOf("activity");
    const liveGroups = deps.groupTraces(traces);
    const liveKind = deps.activityKind(msg, liveGroups, live);
    // Structure only: types, ids and immutable marker text. Including segment
    // text here would rebuild the whole message DOM on every streamed delta
    // (the shape-change path innerHTML-renders every block); text growth is
    // handled per block by the settled/tail markdown streamer below.
    const structure = segments.map((part) => part?.type === "marker"
      ? `marker:${part.kind || ""}:${part.text || ""}:${part.summary || ""}`
      : part?.type === "delivery" ? `delivery:${String(part.text || "").length}`
      : `${part?.type || ""}:${part.id || ""}`).join("|");
    const permSig = (msg.permissions || []).map((p) => `${p.id}:${p.status}`).join("|");
    const traceById = new Map(traces.map((trace) => [trace && trace.id, trace]));
    // Console prints a thought only once it lands, and its live summary line
    // reads the run's phase; streamed thinking text alone repaints nothing.
    const consoleLive = (live);
    const phase = consoleLive ? deps.runPhase(msg) : "";
    const streamingReason = consoleLive && phase !== "calling" && segments.length && segments[segments.length - 1]?.type === "reasoning" ? segments.length - 1 : -1;
    const blockSig = (block, blockIndex) => {
      if (block.kind === "activity") {
        return block.entries.map((entry) => {
          if (entry.type !== "tool") return `r:${entry.segmentIndex}:${entry.segmentIndex === streamingReason ? "" : entry.text || ""}`;
          const trace = entry.trace;
          const running = Boolean(trace && deps.toolPresentation(trace, live).running);
          return `t:${trace && trace.id}:${deps.tracesSig([trace])}:${running}`;
        }).join("~") + (consoleLive && blockIndex === blocks.length - 1 ? `~ph:${phase}` : "");
      }
      if (block.kind === "marker") return `m:${block.markerKind}:${block.text}:${block.summary || ""}`;
      if (block.kind === "delivery") return `d:${block.segmentIndex}:${block.text.length}`;
      if (block.kind === "notices") return `n:${Boolean(live)}:${block.lines.join("\n")}`;
      if (block.kind === "permission") return `p:${block.entries[0] && block.entries[0].perm && block.entries[0].perm.id}`;
      return `c:${blockIndex}`;
    };
    // showThinking changes which blocks exist without changing the segment
    // structure, so it belongs to the shape: a toggle must reach the rebuild.
    const sig = `${Boolean(live)}|${"console"}|${deps.showThinking}|${liveKind}|${structure}|${permSig}|${runNotices.length}`;
    let reasoningPositions = null;
    const sameShape = flow._sig === sig && flow.childElementCount === blocks.length;
    if (sameShape) {
      // One running tool must not remount the settled rows beside it. Replacing
      // a row that is already open replays its expand animation.
      blocks.forEach((block, blockIndex) => {
        const next = blockSig(block, blockIndex);
        const node = flow.children[blockIndex];
        if (!node || node._blockSig === next || block.kind === "content") return;
        if (block.kind !== "activity") {
          const html = block.kind === "permission"
            ? deps.permissionCardHtml(block.entries[0].perm, msg)
            : block.kind === "delivery" ? deliveryBlockHtml(block, msg)
            : block.kind === "notices" ? noticeBlockHtml(block.lines, live) : markerBlockHtml(block);
          if (node.outerHTML === html) { node._blockSig = next; return; }
          const fresh = document.createElement("template");
          fresh.innerHTML = html;
          const replacement = fresh.content.firstElementChild;
          if (!replacement) return;
          replacement._blockSig = next;
          node.replaceWith(replacement);
          return;
        }
        const html = activityBlockHtml(block.entries, msg, live && blockIndex === blocks.length - 1, blockIndex, !live && blockIndex === lastActivity, live);
        const fresh = document.createElement("template");
        fresh.innerHTML = html;
        const incoming = fresh.content.firstElementChild;
        if (!incoming) return;
        FuseChatDOM.patch(node, incoming, deps.transcriptDomOptions);
        node._blockSig = next;
      });
    } else if (flow._sig !== sig) {
      reasoningPositions = new Map([...flow.querySelectorAll("[data-reason-preview]")].map(el =>
        [el.dataset.reasonPreview, deps.thinkingPosition(el)]));
      const focused = flow.contains(document.activeElement) ? document.activeElement : null;
      const focusedStep = focused?.closest("[data-step]")?.dataset.step;
      const focusedControl = focused?.matches(".chat-step-caret") ? ".chat-step-caret" : focused?.matches(".chat-step-toggle") ? ".chat-step-toggle" : focused?.matches(".chat-think-size") ? ".chat-think-size" : focused?.matches(".chat-step-open") ? ".chat-step-open" : "";
      const focusedThinking = focused?.matches("[data-reason-preview]") ? focused.dataset.reasonPreview : null;
      const focusedSection = focused?.matches("[data-copy-tool]") ? focused.closest("[data-tool-section]")?.dataset.toolSection : "";
      const preKey = (el, i) => el.closest("[data-tool-section]") ? `${el.closest("[data-step]")?.dataset.step}:${el.closest("[data-tool-section]").dataset.toolSection}` : `pre-${i}`;
      const positions = new Map([...flow.querySelectorAll("pre")].map((el, i) => [preKey(el, i), [el.scrollLeft, el.scrollTop, el._tailed]]));



      flow._sig = sig;
      const nextFlow = blocks.map((block, blockIndex) => {
        if (block.kind === "permission") {
          return deps.permissionCardHtml(block.entries[0].perm, msg);
        }
        if (block.kind === "marker") {
          return markerBlockHtml(block);
        }
        if (block.kind === "delivery") return deliveryBlockHtml(block, msg);
        if (block.kind === "notices") return noticeBlockHtml(block.lines, live);
        if (block.kind === "content") {
          const text = block.entries.map((entry) => entry.text).join("");
          const writing = live && blockIndex === blocks.length - 1;
          return `<div class="chat-md${writing ? " is-writing" : ""}" data-content-block="${blockIndex}"></div>`;
        }
        const blockLive = live && blockIndex === blocks.length - 1;
         return activityBlockHtml(block.entries, msg, blockLive, blockIndex, !live && blockIndex === lastActivity, live);
       }).join("");
      FuseChatDOM.reconcile(flow, nextFlow, deps.transcriptDomOptions);
      [...flow.children].forEach((node, blockIndex) => { node._blockSig = blockSig(blocks[blockIndex], blockIndex); });
      flow.querySelectorAll("pre").forEach((el, i) => {
        const position = positions.get(preKey(el, i));
        if (position) { el.scrollLeft = position[0]; el.scrollTop = position[1]; el._tailed = position[2]; }
      });
      deps.hydratePermissionCards(flow);
      if (focusedStep && focusedControl) {
        flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] > .chat-step-main > .chat-step-head > ${focusedControl}`)?.focus({ preventScroll: true });
      }
      if (focusedStep && focusedSection) flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] [data-tool-section="${CSS.escape(focusedSection)}"] [data-copy-tool]`)?.focus({ preventScroll: true });
      if (focusedThinking != null) flow.querySelector(`[data-reason-preview="${CSS.escape(focusedThinking)}"]`)?.focus({ preventScroll: true });
    }
    deps.tailConsoleOutput(flow);
    if (live) deps.syncLiveDock();
    normalized.filter((part) => part.type === "reasoning").forEach((part) => {
      const peek = flow.querySelector(`[data-reason-preview="${part.segmentIndex}"]`);
      const li = flow.querySelector(`[data-step="think-${part.segmentIndex}"]`);
      if (!li || !peek) return;
      const later = normalized.some((entry) => entry.segmentIndex > part.segmentIndex);
      const liveThink = Boolean(live && !later);
      let source = String(part.text || "").trim();
      if ((li.dataset.reasonIds)) {
        const ids = new Set(li.dataset.reasonIds.split(","));
        source = normalized
          .filter((entry) => entry.type === "reasoning" && ids.has(String(entry.segmentIndex)))
          .map((entry) => String(entry.text || "").trim())
          .filter(Boolean)
          .join("\n\n");
      }
      // A settled preview whose text is unchanged kept its node and scroll
      // offset through the patch. Reading its position anyway forced a
      // layout per message whenever a snapshot repainted a long transcript.
      const unchanged = !reasoningPositions && peek._thinking && !liveThink && peek._source === source && peek._live === liveThink;
      const saved = unchanged ? null : reasoningPositions?.get(String(part.segmentIndex)) || (peek._thinking ? deps.thinkingPosition(peek) : null);
      deps.initThinking(peek);
      const wasMode = li.dataset.thinkMode;
      const settling = li.classList.contains("is-live-think") && !liveThink;
      li.classList.toggle("is-live-think", liveThink);
      deps.syncThinkingMode(li, msg, settling && Boolean(wasMode) && !deps.reduceMotion.matches);
      if (unchanged) return;
      if (peek._source !== source || peek._live !== liveThink) {
        peek._source = source; peek._live = liveThink;
        deps.streamMarkdown(peek.firstElementChild, source, liveThink, catalog);
        peek.classList.toggle("is-clipped", liveThink && peek.scrollHeight > peek.clientHeight + 1);
        ;
      }
      if (reasoningPositions?.has(String(part.segmentIndex)) && saved) {
        peek.scrollTop = saved.top;
        peek._thinking.lastTop = peek.scrollTop;
        peek._thinking.initialized = saved.initialized;
      }
      if (saved && !saved.pinned) {
        deps.stopThinkingFollow(peek);
        peek.scrollTop = saved.top;
      } else if (liveThink || saved?.pinned && saved?.initialized) {
        peek._thinking.follow = true;
        deps.followThinking(peek, !saved?.initialized);
      } else if (!liveThink) peek._thinking.follow = false;
    });
    blocks.forEach((block, blockIndex) => {
      if (block.kind !== "content") return;
      const md = flow.querySelector(`[data-content-block="${blockIndex}"]`);
      if (!md) return;
      const text = dropCardLinks(block.entries.map((entry) => entry.text).join(""), cardIds);
      const writing = live && blockIndex === blocks.length - 1;
      const srcKey = text + "\0" + catalog.join("\n");
      md.classList.toggle("is-writing", writing);
      // A fence the reply left open was streamed plain: the settled draw highlights it.
      if (md._src === srcKey && (writing || !md.querySelector("[data-code-open]"))) return;
      const positions = [...md.querySelectorAll("pre")].map((el) => [el.scrollLeft, el.scrollTop]);
      deps.paintContentBlock(md, text, writing, catalog, srcKey);
      md.querySelectorAll("pre").forEach((el, i) => {
        if (positions[i]) { el.scrollLeft = positions[i][0]; el.scrollTop = positions[i][1]; }
      });
    });
    deps.syncActivityElapsed(article, msg, live);
    deps.syncActivitySpin(article, msg, live);
    if (live) deps.startLiveClock();
    const toolFailKeys = traces
      .filter((t) => deps.toolFailed(t))
      .map((t) => deps.normErr(t.error || "failed"));
    const errDup = Boolean(errText && toolFailKeys.includes(deps.normErr(errText)));
    if (errText && !errDup) {
      if (err.hidden) err.hidden = false;
      if (err._text !== errText) {
        err._text = errText;
        err.dataset.tone = deps.parseProviderError(errText).tone;
        err.innerHTML = deps.providerErrorHtml(errText);
      }
    } else if (!err.hidden || err._text) {
      err.hidden = true;
      err._text = "";
      err.textContent = "";
    }
    syncNotices(article, noticeLines, live);
    // A failed final turn can pick up where it stopped: Continue keeps the
    // work already done (the failed run's history is retained), unlike Retry,
    // which discards the turn and replays the prompt from scratch.
    const errActions = article.querySelector("[data-err-actions]");
    if (errActions) {
      const last = deps.messages[deps.messages.length - 1] === msg || (msg.id && deps.messages.at(-1)?.id === msg.id);
      const noActions = !(errText && !live && last);
      if (errActions.hidden !== noActions) errActions.hidden = noActions;
      const button = errActions.querySelector("[data-continue]");
      if (button && !button._bound) {
        button._bound = true;
        button.addEventListener("click", (event) => {
          event.preventDefault();
          deps.continueAssistant?.();
        });
      }
    }
    const usageBits = deps.formatTurnMeta(msg);
    const turnLine = (!live) && hasOutput ? deps.ccTurnLineHtml(msg, msg === deps.messages[deps.messages.length - 1]) : "";
    if (turnLine) {
      foot.hidden = false;
      meta.innerHTML = `<span class="chat-console-turn">${turnLine}</span>`;
    } else if (!live && hasOutput) {
      foot.hidden = false;
      meta.innerHTML = usageBits
        .map((bit) => `<span title="${deps.escapeHtml(bit.title)}">${deps.escapeHtml(bit.text)}</span>`)
        .join('<span class="chat-foot-dot" aria-hidden="true">|</span>');
    } else if (!foot.hidden) {
      foot.hidden = true;
    }
  }

  return { activityEntriesHtml, activityBlockHtml, visibleAssistantError, updateAssistant };
}

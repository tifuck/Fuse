// Composer code blocks. A message with a fence is edited as prose fields and
// real code boxes; typing ``` alone on a line opens a box with the caret in
// it. #chat-input (S.input) stays the message: every other feature reads and
// writes it as before, and this module keeps the fields in step both ways.
import { S } from '../state.js';
import { splitBlocks, joinBlocks, locate, offsetOf, fenceTrigger } from './blocks-core.js';
import { marksHtml, italicKeepsWidth } from './marks.js';

const proto = HTMLTextAreaElement.prototype;
const nativeValue = Object.getOwnPropertyDescriptor(proto, "value");
const METRICS = [
  "font-family", "font-size", "font-weight", "line-height", "letter-spacing", "color",
  "padding-top", "padding-right", "padding-bottom", "padding-left", "min-height", "max-height",
];
const FIELD_STYLE = "display:block;width:100%;box-sizing:border-box;margin:0;border:0;border-radius:0;" +
  "background:transparent;box-shadow:none;outline:none;resize:none;min-height:0;max-height:none;color:inherit;";
const UNWRAP_ICON = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

export function createBlocks(ctx) {
  const { input } = ctx;
  let host = null;
  let compose = null;
  let segs = [];
  let parts = [];
  let on = false;
  let writing = false;
  let triggering = false;
  let composing = null;

  const value = () => nativeValue.get.call(S.input);
  const select = (a, b) => proto.setSelectionRange.call(S.input, a, b);
  const segIndex = (el) => segs.findIndex((seg) => seg.el === el || seg.lang === el);
  const sameShape = () => parts.length === segs.length && parts.every((part, i) => part.kind === segs[i].kind);

  function makeSeg(kind) {
    const el = document.createElement("textarea");
    el.rows = 1;
    el.spellcheck = false;
    el.setAttribute("autocomplete", "off");
    el.setAttribute("autocapitalize", "off");
    el.setAttribute("autocorrect", "off");
    el.disabled = S.input.disabled;
    if (kind === "text") {
      el.className = "chat-blocks-text";
      el.setAttribute("aria-label", "Message");
      el.setAttribute("enterkeyhint", "send");
      el.style.cssText = FIELD_STYLE + "position:relative;z-index:1;padding:0;overflow:hidden;font:inherit;letter-spacing:inherit;line-height:inherit;";
      // Inline markdown shows from a mirror behind the field, as in #chat-input.
      const line = document.createElement("div");
      line.className = "chat-blocks-line";
      const mirror = document.createElement("div");
      mirror.className = "chat-blocks-mirror";
      mirror.setAttribute("aria-hidden", "true");
      line.append(mirror, el);
      return { kind, el, mirror, node: line };
    }
    const box = document.createElement("div");
    box.className = "chat-code chat-blocks-code";
    box.style.cssText = "margin:6px 0;";
    const head = document.createElement("div");
    head.className = "chat-code-head chat-blocks-head";
    const lang = document.createElement("input");
    lang.className = "chat-blocks-lang";
    lang.placeholder = "language";
    lang.spellcheck = false;
    lang.setAttribute("autocomplete", "off");
    lang.setAttribute("aria-label", "Code language");
    const unwrap = document.createElement("button");
    unwrap.type = "button";
    unwrap.className = "chat-blocks-unwrap";
    unwrap.dataset.blocksUnwrap = "";
    unwrap.setAttribute("aria-label", "Remove the code box, keep its text");
    unwrap.title = "Remove the code box, keep its text";
    unwrap.innerHTML = UNWRAP_ICON;
    head.append(lang, unwrap);
    el.className = "chat-blocks-codearea";
    el.wrap = "off";
    el.setAttribute("aria-label", "Code");
    el.setAttribute("enterkeyhint", "enter");
    // Below 16px a phone zooms the page on focus.
    const size = window.matchMedia("(pointer: coarse)").matches ? "16px" : "0.88em";
    el.style.cssText = FIELD_STYLE + `padding:10px 14px;overflow-x:auto;overflow-y:hidden;white-space:pre;tab-size:4;` +
      `font-family:var(--mono, ui-monospace, monospace);font-size:${size};line-height:1.6;letter-spacing:0;`;
    box.append(head, el);
    return { kind, el, lang, node: box };
  }

  function copyMetrics() {
    const style = window.getComputedStyle(S.input);
    for (const name of METRICS) host.style.setProperty(name, style.getPropertyValue(name));
  }

  // Each field fits its text. Empty prose between boxes (or above the first)
  // is a thin strip until it has focus, so an empty line costs no space but
  // stays one click or arrow key away. A keystroke (`typed`) redraws only the
  // mirrors whose text changed; every other caller redraws them all.
  function fit(typed) {
    if (!on) return;
    // As in resizeInput: a transcript kept at the bottom stays there while the composer grows.
    const thread = S.thread;
    const follow = thread && (S.stickToBottom || thread.scrollHeight - thread.scrollTop - thread.clientHeight <= 1);
    const threadTop = thread ? thread.scrollTop : 0;
    const hostTop = host.scrollTop;
    const bar = host.scrollHeight > host.clientHeight;
    copyMetrics();
    // Every write, then every read, then the heights: one layout for all the
    // fields instead of one each. While they are collapsed the box keeps its
    // scrollbar, so prose wraps at the width it will have.
    const grow = [];
    segs.forEach((seg, i) => {
      const el = seg.el;
      if (seg.kind === "text") {
        if (!typed) seg.painted = undefined;
        paintLine(seg);
        const gap = !el.value && i < segs.length - 1 && document.activeElement !== el;
        seg.el.classList.toggle("is-gap", gap);
        if (gap) {
          el.style.height = "8px";
          return;
        }
      }
      el.style.height = "auto";
      grow.push(el);
    });
    if (bar) host.style.overflowY = "scroll";
    const heights = grow.map((el) => el.scrollHeight + (el.offsetHeight - el.clientHeight));
    grow.forEach((el, i) => { el.style.height = heights[i] + "px"; });
    host.style.overflowY = "";
    // Collapsed for the measure, the box and the transcript may have been
    // scrolled back; put them where they were.
    host.scrollTop = hostTop;
    if (thread && !follow && thread.scrollTop !== threadTop) thread.scrollTop = threadTop;
    input.syncDockSpace();
    if (follow) thread.scrollTop = Math.max(0, thread.scrollHeight - thread.clientHeight);
  }

  function paintLine(seg) {
    const text = composing === seg.el ? null : seg.el.value;
    if (seg.painted === text) return;
    seg.painted = text;
    const { html, styled } = text === null ? { html: "", styled: false } : marksHtml(text);
    seg.mirror.innerHTML = styled ? html + "\n" : "";
    seg.el.style.webkitTextFillColor = styled ? "transparent" : "";
    seg.node.classList.toggle("is-oblique", styled && italicKeepsWidth(seg.el));
  }

  function render() {
    parts.forEach((part, i) => {
      let seg = segs[i];
      if (!seg || seg.kind !== part.kind) {
        const fresh = makeSeg(part.kind);
        if (seg) seg.node.replaceWith(fresh.node);
        else host.appendChild(fresh.node);
        seg = segs[i] = fresh;
      }
      if (seg.el.value !== part.text) seg.el.value = part.text;
      if (seg.lang && seg.lang.value !== part.lang) seg.lang.value = part.lang;
    });
    segs.splice(parts.length).forEach((seg) => seg.node.remove());
    fit();
  }

  function enter() {
    on = true;
    compose.classList.add("is-blocks");
    host.hidden = false;
  }

  function leave() {
    const focused = host.contains(document.activeElement);
    on = false;
    S.composerInCode = false;
    host.hidden = true;
    host.replaceChildren();
    segs = [];
    compose.classList.remove("is-blocks");
    if (focused) proto.focus.call(S.input, { preventScroll: true });
    input.resizeInput();
  }

  function focusSeg(index, offset, end = offset) {
    const seg = segs[index];
    if (!seg) return;
    const at = offset === "end" ? seg.el.value.length : offset;
    seg.el.focus({ preventScroll: true });
    seg.el.setSelectionRange(at, end === "end" ? seg.el.value.length : end);
    seg.el.scrollIntoView({ block: "nearest" });
  }

  // A message offset (S.input's selection) into the field that shows it.
  function focusAt(start, end) {
    const a = locate(parts, start);
    const b = locate(parts, end);
    focusSeg(a.index, a.offset, b.index === a.index ? b.offset : a.offset);
  }

  // S.input changed outside this module (a draft, the library, dictation,
  // send clearing it): redraw the fields from it.
  function fromInput() {
    parts = splitBlocks(value());
    if (parts.length === 1) {
      if (on) leave();
      return;
    }
    const focused = host.contains(document.activeElement) || document.activeElement === S.input;
    if (!on) enter();
    render();
    if (focused) focusAt(S.input.selectionStart, S.input.selectionEnd);
  }

  // A field changed: write the message back to S.input and let the
  // composer's own input handlers (draft, size, send button) run.
  function commit(ui, at, end) {
    const next = joinBlocks(ui, at);
    const last = end ? joinBlocks(ui, end).caret : next.caret;
    writing = true;
    nativeValue.set.call(S.input, next.value);
    writing = false;
    select(next.caret, last);
    parts = splitBlocks(next.value);
    if (parts.length === 1) leave();
    else if (!sameShape()) {
      render();
      focusAt(next.caret, last);
    } else fit(true);
    S.input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  const uiParts = () => segs.map((seg) => ({ kind: seg.kind, text: seg.el.value, lang: seg.lang ? seg.lang.value.trim() : "" }));

  // Drop code box `index`, joining the prose around it (with its code, when
  // `keep`), the caret where the code was.
  function removeBox(index, keep) {
    const ui = uiParts();
    const before = ui[index - 1].text;
    const code = keep ? ui[index].text : "";
    const after = ui[index + 1].text;
    const merged = [before, code, after].filter(Boolean);
    const caret = (before ? before.length + (code || after ? 1 : 0) : 0) + code.length;
    ui.splice(index - 1, 3, { kind: "text", text: merged.join("\n") });
    commit(ui, { index: index - 1, offset: caret });
  }

  function syncSelection() {
    if (!on) return;
    const i = segIndex(document.activeElement);
    if (i < 0 || segs[i].lang === document.activeElement) return;
    const el = segs[i].el;
    select(offsetOf(parts, i, el.selectionStart), offsetOf(parts, i, el.selectionEnd));
    S.composerInCode = segs[i].kind === "code";
  }

  // Keys the composer acts on (Enter to send, palettes, Escape, Ctrl+S) go
  // to S.input's own handlers; their preventDefault carries back.
  function forward(event) {
    syncSelection();
    const clone = new KeyboardEvent("keydown", {
      key: event.key, code: event.code, location: event.location, repeat: event.repeat,
      shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, altKey: event.altKey, metaKey: event.metaKey, cancelable: true,
    });
    S.input.dispatchEvent(clone);
    if (clone.defaultPrevented) event.preventDefault();
  }

  function onKeydown(event) {
    if (event.isComposing || event.keyCode === 229) return;
    const i = segIndex(event.target);
    if (i < 0) return;
    const seg = segs[i];
    if (event.target === seg.lang) {
      if ((event.key === "Enter" || event.key === "ArrowDown") && !event.shiftKey) {
        event.preventDefault();
        focusSeg(i, 0);
      }
      return;
    }
    const el = seg.el;
    const text = el.value;
    const a = el.selectionStart;
    const plain = !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey;
    const palette = S.slashMenu && !S.slashMenu.hidden;
    if (plain && !palette && a === el.selectionEnd) {
      const lastIndex = segs.length - 1;
      // Code never wraps, so its first and last lines are visual lines too;
      // wrapped prose leaves its edges only from the very start or end.
      const oneRow = seg.kind === "code" || el.scrollHeight <= el.clientHeight + 1 && !text.includes("\n");
      const top = a === 0 || (oneRow && !text.slice(0, a).includes("\n"));
      const bottom = a === text.length || (oneRow && !text.slice(a).includes("\n"));
      let move = null;
      if (i > 0 && ((event.key === "ArrowUp" && top) || (event.key === "ArrowLeft" && a === 0))) move = [i - 1, "end"];
      else if (i < lastIndex && ((event.key === "ArrowDown" && bottom) || (event.key === "ArrowRight" && a === text.length))) move = [i + 1, 0];
      else if (event.key === "Backspace" && a === 0 && i > 0) {
        event.preventDefault();
        if (seg.kind === "code" && !text) removeBox(i, false);
        else focusSeg(i - 1, "end");
        return;
      } else if (event.key === "Delete" && a === text.length && i < lastIndex) {
        event.preventDefault();
        if (segs[i + 1].kind === "code" && !segs[i + 1].el.value) removeBox(i + 1, false);
        else focusSeg(i + 1, 0);
        return;
      }
      if (move) {
        event.preventDefault();
        focusSeg(...move);
        return;
      }
    }
    if (seg.kind === "code") {
      if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        const lineStart = text.lastIndexOf("\n", a - 1) + 1;
        document.execCommand("insertText", false, "\n" + text.slice(lineStart, a).match(/^[ \t]*/)[0]);
        return;
      }
      if (event.key === "Escape" && plain) {
        event.preventDefault();
        focusSeg(i + 1, 0);
        return;
      }
      if (event.ctrlKey || event.metaKey) forward(event);
      return;
    }
    forward(event);
  }

  function onSegInput(event) {
    const i = segIndex(event.target);
    if (i < 0 || triggering) return;
    const seg = segs[i];
    if (event.target === seg.lang) {
      const clean = seg.lang.value.replace(/[`\s]/g, "");
      if (clean !== seg.lang.value) seg.lang.value = clean;
      commit(uiParts(), { index: i, offset: 0 });
      seg.lang.focus();
      return;
    }
    const el = seg.el;
    if (seg.kind === "text" && event.inputType === "insertText" && event.data === "`" && el.selectionStart === el.selectionEnd) {
      const trigger = fenceTrigger(el.value, el.selectionStart);
      if (trigger) {
        triggering = true;
        document.execCommand("insertText", false, trigger.insert);
        triggering = false;
        commit(uiParts(), { index: i, offset: trigger.caret });
        return;
      }
    }
    commit(uiParts(), { index: i, offset: el.selectionStart }, { index: i, offset: el.selectionEnd });
  }

  // Typing in the plain field: ``` opens the first box.
  function onPlainInput(event) {
    if (on || writing || triggering) return;
    if (event.inputType === "insertText" && event.data === "`" && S.input.selectionStart === S.input.selectionEnd) {
      const trigger = fenceTrigger(value(), S.input.selectionStart);
      if (trigger) {
        triggering = true;
        document.execCommand("insertText", false, trigger.insert);
        triggering = false;
        select(trigger.caret, trigger.caret);
        fromInput();
        S.input.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
    }
    if (splitBlocks(value()).length > 1) fromInput();
  }

  function onMouseDown(event) {
    if (event.target !== host) return;
    // The padding and the strips between fields: the nearest field takes it.
    let best = null;
    segs.forEach((seg, i) => {
      const rect = seg.node.getBoundingClientRect();
      const dist = event.clientY < rect.top ? rect.top - event.clientY : Math.max(0, event.clientY - rect.bottom);
      if (!best || dist < best.dist) best = { i, dist, above: event.clientY < rect.top };
    });
    if (!best) return;
    event.preventDefault();
    const seg = segs[best.i];
    focusSeg(best.i, seg.kind === "text" && !best.above ? "end" : 0);
  }

  function mount() {
    compose = S.input.closest("[data-chat-compose]");
    if (!compose || host) return;
    host = document.createElement("div");
    host.className = "chat-blocks";
    host.hidden = true;
    host.setAttribute("role", "group");
    host.setAttribute("aria-label", "Message");
    compose.appendChild(host);

    const field = S.input;
    Object.defineProperty(field, "value", {
      configurable: true,
      get() { return nativeValue.get.call(field); },
      set(next) {
        nativeValue.set.call(field, next);
        if (!writing) fromInput();
        window.FuseComposerSpell?.paintNow?.();
      },
    });
    field.setRangeText = function (...args) {
      proto.setRangeText.apply(field, args);
      if (!writing) fromInput();
      window.FuseComposerSpell?.paintNow?.();
    };
    field.setSelectionRange = function (...args) {
      proto.setSelectionRange.apply(field, args);
      if (on && host.contains(document.activeElement)) focusAt(field.selectionStart, field.selectionEnd);
    };
    field.focus = function (opts) {
      if (on) focusAt(field.selectionStart, field.selectionEnd);
      else proto.focus.call(field, opts);
    };
    field.addEventListener("input", onPlainInput);
    new MutationObserver(() => segs.forEach((seg) => { seg.el.disabled = field.disabled; }))
      .observe(field, { attributes: true, attributeFilter: ["disabled"] });

    host.addEventListener("input", onSegInput);
    host.addEventListener("compositionstart", (event) => {
      composing = event.target;
      const seg = segs.find((item) => item.el === event.target && item.mirror);
      if (seg) paintLine(seg);
    });
    host.addEventListener("compositionend", () => {
      composing = null;
      fit();
    });
    host.addEventListener("keydown", onKeydown);
    host.addEventListener("mousedown", onMouseDown);
    host.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-blocks-unwrap]");
      if (btn) removeBox(segs.findIndex((seg) => seg.node.contains(btn)), true);
    });
    // Pasted files still attach: S.input's paste handler sees the clipboard.
    host.addEventListener("paste", (event) => {
      const clone = new ClipboardEvent("paste", { clipboardData: event.clipboardData, cancelable: true });
      S.input.dispatchEvent(clone);
      if (clone.defaultPrevented) event.preventDefault();
    });
    host.addEventListener("focusin", (event) => {
      if (!host.contains(event.relatedTarget)) S.input.dispatchEvent(new FocusEvent("focus"));
      syncSelection();
      fit();
    });
    host.addEventListener("focusout", (event) => {
      if (!host.contains(event.relatedTarget)) {
        S.composerInCode = false;
        S.input.dispatchEvent(new FocusEvent("blur"));
      }
      window.requestAnimationFrame(() => fit());
    });
    document.addEventListener("selectionchange", syncSelection);
    window.addEventListener("resize", () => window.requestAnimationFrame(() => fit()));
    fromInput();
  }

  return { mountBlocks: mount };
}

// fold-motion: a summary line or a call opens and folds upward. What is under
// it holds its place on screen, however often it is toggled; the line itself,
// and everything above it, moves instead. Dependencies are live getters
// supplied by index.js.
export function create(deps) {
const DURATION = 200;
const EASING = "cubic-bezier(0.2, 0, 0, 1)";

// The transcript (or agent log) that scrolls the row.
function scrollerOf(el) {
    for (let node = el.parentElement; node; node = node.parentElement) {
      if (/^(auto|scroll)$/.test(getComputedStyle(node).overflowY)) return node;
    }
    return null;
  }

// The first thing drawn in the flow after the row, climbing out of its group
// and its turn. A pinned or positioned element (a sticky bar) does not move
// with the content, so it cannot hold the content's place.
function nextBelow(li, box) {
    for (let el = li; el && el !== box; el = el.parentElement) {
      for (let next = el.nextElementSibling; next; next = next.nextElementSibling) {
        if (next.getClientRects().length && !/^(fixed|sticky|absolute)$/.test(getComputedStyle(next).position)) return next;
      }
    }
    return null;
  }

// part: what appears and goes (a group's list, a call's box). apply: sets the
// open state at once; settle: what waits until a fold has stopped moving (the
// box's own open state, so its content does not jump while it shrinks).
function foldInPlace(li, part, open, apply, settle) {
    const box = scrollerOf(li);
    const owner = li.closest(".chat-turn");
    const prev = li._dkFold;
    const height = part?.getClientRects().length ? part.getBoundingClientRect().height : 0;
    const now = height ? getComputedStyle(part) : null;
    // A fold caught mid-motion starts from where it is drawn now.
    const from = { height, top: now?.marginTop || "0px", bottom: now?.marginBottom || "0px", opacity: now?.opacity ?? "0" };
    // Register first: the fold it interrupts must not flush a held repaint in between.
    if (owner) deps.toolMotions.set(owner, (deps.toolMotions.get(owner) || 0) + 1);
    prev?.cancel();
    // A fold toggled again while moving keeps the first spot, so the content
    // under it cannot creep however fast it is clicked.
    const anchor = prev?.anchor?.isConnected ? prev.anchor : box && nextBelow(li, box);
    const run = { anchor, y: prev?.anchor === anchor && anchor ? prev.y : anchor?.getBoundingClientRect().top,
      gap: prev && !anchor ? prev.gap : box ? box.scrollHeight - box.scrollTop : 0, sizes: null, held: true };
    li._dkFold = run;
    // Following the bottom already keeps the end where it is; pinning too
    // would fight the follow as new output arrives.
    const pin = () => {
      if (!box || !run.held || (box === deps.thread && deps.stickToBottom)) return;
      if (run.anchor) {
        if (run.anchor.isConnected) box.scrollTop += run.anchor.getBoundingClientRect().top - run.y;
      } else box.scrollTop = box.scrollHeight - run.gap;
    };
    // The reader scrolling mid-motion wins.
    const release = () => { run.held = false; };
    box?.addEventListener("wheel", release, { passive: true });
    box?.addEventListener("touchstart", release, { passive: true });
    apply();
    let motion = null;
    if (part && !deps.reduceMotion?.matches) {
      // Rows that were folded away are skipped by content-visibility and
      // would be measured at their placeholder height: draw them for real.
      if (open) {
        run.drawn = [...part.querySelectorAll(".chat-step")];
        run.drawn.forEach((row) => row.style.setProperty("content-visibility", "visible"));
      }
      // Measured before the part clips: clipped, it holds its rows' margins
      // and would end taller than it settles.
      const end = getComputedStyle(part);
      const to = open ? { height: part.getBoundingClientRect().height, top: end.marginTop, bottom: end.marginBottom, opacity: "1" }
        : { height: 0, top: "0px", bottom: "0px", opacity: "0" };
      part.style.setProperty("overflow", "hidden");
      // A folding part stays drawn until it has shrunk away.
      if (!open) part.style.setProperty("display", "block", "important");
      motion = part.animate([
        { height: `${from.height}px`, marginTop: from.top, marginBottom: from.bottom, opacity: from.opacity },
        { height: `${to.height}px`, marginTop: to.top, marginBottom: to.bottom, opacity: to.opacity },
      ], { duration: DURATION, easing: EASING });
    }
    const done = (finished) => {
      run.sizes?.disconnect();
      box?.removeEventListener("wheel", release);
      box?.removeEventListener("touchstart", release);
      part?.style.removeProperty("overflow");
      run.drawn?.forEach((row) => row.style.removeProperty("content-visibility"));
      part?.style.removeProperty("display");
      if (finished) settle?.();
      if (li._dkFold === run) li._dkFold = null;
      pin();
      if (!owner) return;
      const n = (deps.toolMotions.get(owner) || 1) - 1;
      if (n) { deps.toolMotions.set(owner, n); return; }
      deps.toolMotions.delete(owner);
      const paint = deps.motionPaints.get(owner);
      deps.motionPaints.delete(owner);
      if (paint && owner.isConnected) {
        deps.updateAssistant(owner, paint.msg, paint.live);
        pin();
      }
    };
    run.cancel = () => {
      if (motion) { motion.onfinish = null; motion.cancel(); }
      done(false);
    };
    pin();
    if (!motion) { done(true); return; }
    // Pinned as the part changes size: after the frame's layout, so reading
    // where the anchor landed forces none, and before it is painted.
    run.sizes = new ResizeObserver(pin);
    run.sizes.observe(part);
    motion.onfinish = () => done(true);
  }

  return { foldInPlace };
}

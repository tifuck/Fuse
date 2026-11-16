---
name: ui-guidelines
description: Review a web UI against practical interface guidelines for layout, spacing, type, forms, focus, states and contrast. Use when asked to review, polish or QA a page or component, or before shipping a UI change.
metadata:
  category: Design
  inspired-by: vercel-labs/agent-skills
---
# UI guidelines review

Review the code and, when possible, the rendered page. Report findings as `file:line - issue - fix`, grouped by severity (broken, degraded, polish). Fix only when asked.

## Layout and spacing
- Spacing comes from one scale (for example 4/8/12/16/24/32/48). Flag one-off values like `13px` or `margin: 7px`.
- Related items sit closer together than unrelated ones; sections separated by more space than items within them.
- Content width is capped for reading (about 60-75 characters per line for prose).
- Alignment: elements share edges; no near-misses of 1-3 px.
- No horizontal scroll at 320-375 px wide. Long words, URLs and numbers wrap or truncate with a full-value tooltip or title.

## Typography
- A small type scale (4-6 sizes), body text at least 16 px on mobile, line-height 1.4-1.6 for body.
- Hierarchy from size and weight, not color alone.
- Numbers that update or align in columns use tabular figures (`font-variant-numeric: tabular-nums`).
- Use real ellipsis and quotes in copy; sentence case for labels and buttons unless the product uses otherwise.

## Forms
- Every input has a visible label (placeholder is not a label) and the right `type`, `inputmode` and `autocomplete`.
- Errors appear next to the field, say how to fix it, and keep the user's input.
- Submit stays enabled until submission starts; then it shows progress and prevents double submit.
- Enter submits single-field forms; paste is never blocked.
- Large tap targets: at least 24x24 px, 44x44 px preferred on touch.

## Focus and keyboard
- Every interactive element is reachable by Tab in a logical order and shows a visible `:focus-visible` ring.
- Use `button` for actions and `a` for navigation. No clickable `div`s.
- Dialogs trap focus, close on Escape and return focus to the trigger.

## States
Check each view in: loading (skeleton or spinner without layout shift), empty (explains why and offers the next action), error (plain message plus retry), partial data, very long content, and success feedback after actions. Destructive actions confirm or offer undo.

## Feedback and motion
- Hover, active, disabled and selected states are distinct.
- Optimistic updates roll back on failure with a message.
- Animations are short, interruptible and honour `prefers-reduced-motion`.

## Color and contrast
- Text contrast at least 4.5:1 (3:1 for large text and UI component boundaries).
- Color is never the only signal; pair it with text or an icon.
- Dark mode, if supported, is checked separately: borders, shadows and images often disappear.

## Responsiveness and performance
- Check 360 px, 768 px and 1280+ px widths, and zoom to 200%.
- Images have dimensions or aspect-ratio to avoid layout shift; below-the-fold images lazy-load.
- Respect safe-area insets on mobile; fixed footers do not cover content.

## Output
List the top issues first with a concrete fix each. Note what you could not check (for example, no browser available) instead of guessing.

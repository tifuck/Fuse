---
name: accessibility
description: Audit and fix web accessibility against WCAG 2.2 AA, covering semantics, labels, keyboard, focus, contrast, motion and screen readers. Use when asked for an a11y audit, fixing a11y bugs, or building forms, dialogs, menus or custom widgets.
metadata:
  category: Design
---
# Accessibility (WCAG 2.2 AA)

Work from the code and the rendered page. Prefer native HTML over ARIA; the first rule of ARIA is not to use it when a native element does the job.

## 1. Automated pass
Run what the project already has (eslint-plugin-jsx-a11y, axe in tests, Lighthouse, pa11y). If nothing exists and a browser is available, axe or Lighthouse in devtools is fine. Automated tools catch roughly a third of issues; never stop there.

## 2. Semantics and structure
- One `h1`; headings in order without skipped levels used for styling.
- Landmarks: `header`, `nav`, `main`, `footer`; one `main`.
- Lists are `ul`/`ol`, tables have `th` with `scope` and a caption when useful.
- `lang` set on `html`; page `title` unique and descriptive.
- Actions are `button`, navigation is `a href`. Replace clickable `div`/`span`.

## 3. Names and labels
- Every form control has a programmatic label (`label for`, wrapping label, or `aria-labelledby`). Placeholder is not a label.
- Icon-only buttons get an accessible name (`aria-label` or visually hidden text).
- Images: meaningful `alt`; decorative images `alt=""`.
- Errors linked to fields with `aria-describedby` and announced (`aria-live="polite"` or focus moved to an error summary).
- Required fields marked in text, not only with color or an asterisk.

## 4. Keyboard and focus
- Tab through the whole page: everything interactive is reachable, nothing invisible is.
- Order follows visual order; avoid positive `tabindex`.
- Visible focus indicator on every element; never `outline: none` without a replacement.
- Focus not hidden behind sticky headers or footers (2.4.11).
- Dialogs: move focus in, trap it, close on Escape, return focus to the trigger. Prefer the native `dialog` element or the project's tested component.
- Custom widgets (tabs, menus, comboboxes, sliders) follow the ARIA Authoring Practices keyboard patterns.
- Drag-and-drop has a single-pointer alternative (2.5.7).

## 5. Visual
- Contrast: 4.5:1 for normal text, 3:1 for large text, icons and control borders.
- Color is not the only cue for state, errors or links in text.
- Target size at least 24x24 CSS px (2.5.8).
- Content reflows at 320 px width and 200% zoom without loss or horizontal scroll.
- Text spacing overrides do not clip content.

## 6. Motion and time
- Honour `prefers-reduced-motion`; no flashing more than three times a second.
- Auto-playing or moving content can be paused.
- Timeouts warn the user and allow extension.

## 7. Screen reader check
If possible, test the changed flow with a screen reader (VoiceOver on macOS/iOS, NVDA on Windows, TalkBack on Android). Otherwise inspect the accessibility tree in browser devtools: name, role and state of each control should make sense on their own. Dynamic updates (toasts, loading, results counts) use a live region.

## Reporting and fixing
For each finding give: WCAG criterion, location (`file:line` or selector), impact on users, and the fix. Order by impact. When fixing, change the shared component rather than each usage, and re-run the automated pass plus a keyboard walk-through. State what you could not verify manually.

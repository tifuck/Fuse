---
name: ui-redesign
description: Redesign an existing interface without breaking it. Audit, choose one direction, change tokens first, then components, keeping behaviour intact. Use when asked to restyle, modernise, refresh or give a new look to an existing app or page.
metadata:
  category: Design
  inspired-by: leonxlnx/taste-skill
---
# UI redesign

A redesign changes how the product looks and feels, not what it does. Every route, handler, data flow, test id and accessible name must survive.

## 1. Audit before touching anything
- Map the styling system: CSS variables, Tailwind config, theme files, component library, global styles. Find where colors, fonts, radii, shadows and spacing are defined.
- List the screens and shared components in scope. Note which are shared widely.
- Record hooks that must not change: `data-testid`, `id`s used by JS or tests, class names referenced in code, ARIA attributes, form field names.
- Capture the current state: screenshots of key screens if a browser is available, or a written inventory of components and their states.
- Note real problems: inconsistent spacing, too many font sizes, weak hierarchy, low contrast, cluttered layouts.

## 2. Pick one clear direction
Write a short design brief before coding: mood (for example calm and dense, or bold and editorial), type pairing, color palette with roles (background, surface, text, muted, accent, success, danger), radius, shadow and density. One accent color used sparingly beats several. If the user gave no direction, propose one or two options in a few lines and take the most fitting; say which you chose.

## 3. Tokens first
Change the design tokens before any component:
- Colors as semantic roles (`--color-surface`, `--color-text-muted`), not raw hex in components.
- Type scale (4-6 sizes) and weights; line heights.
- Spacing scale, radii, shadows, motion durations.
- Light and dark values if the app supports both; check contrast for each pair (4.5:1 text).
A good token pass alone often delivers most of the visual change.

## 4. Then components, shared first
- Update base components (buttons, inputs, cards, tables, navigation, dialogs) in their single source, not per page.
- Replace hardcoded values you find with tokens.
- Keep component APIs (props, slots, events) stable. If an API must change, update every caller.
- Keep markup semantics; restyle, do not replace a `button` with a `div`.

## 5. Then layouts and pages
Fix hierarchy: one focal point per screen, clear grouping by spacing, consistent alignment, fewer borders and boxes. Check loading, empty and error states and responsive widths (360, 768, 1280+ px).

## Rules
- Work in small, reviewable steps; the app must build and run after each.
- No new UI or animation library unless the user agrees.
- Do not remove features, options or content to make a screen look cleaner without asking.
- Keep keyboard focus visible and honour reduced motion.

## Verify
- Run the project's build, lint and tests, including visual or snapshot tests (update snapshots only for intended visual changes, and say so).
- Grep that preserved hooks (`data-testid`, ids, field names) still exist.
- Walk the main flows in a browser if available and compare against the audit screenshots.
- Report the direction chosen, tokens changed, components touched and anything not checked.

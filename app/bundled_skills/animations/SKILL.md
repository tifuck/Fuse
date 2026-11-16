---
name: animations
description: Add purposeful UI motion with sensible durations and easing, transform/opacity only, and reduced-motion support. Use when adding or reviewing transitions, enter/exit animations, gestures, or fixing janky or slow-feeling UI motion.
metadata:
  category: Design
  inspired-by: emilkowalski/skills
---
# Animations

Motion must explain something: where an element came from, what changed, or that an action registered. If it does none of these, leave it out.

## When to animate
- Yes: state changes (open/close, expand/collapse), spatial navigation, feedback on press, drawing attention to a new item, smoothing layout shifts.
- No: actions people repeat many times a day (keyboard-driven menus, command palettes, frequent toggles), anything that delays reading or input, page-load flourishes on every section.
- Never block interaction while an animation plays. Animations must be interruptible: a reversed click mid-transition reverses from the current position.

## Duration
- Micro feedback (press, hover, toggle): 100-150 ms.
- Small UI (dropdowns, tooltips, popovers): 150-250 ms.
- Larger surfaces (modals, drawers, sheets): 200-350 ms.
- Exits are faster than entrances (about 70-80% of the enter time).
- Over 400 ms feels slow for UI; reserve it for rare, large transitions.

## Easing
- Entering or responding to the user: ease-out, e.g. `cubic-bezier(0.22, 1, 0.36, 1)`.
- Moving on screen from A to B: ease-in-out.
- Avoid ease-in for UI entrances; it feels sluggish at the start.
- Never `linear` except for continuous loops (spinners, progress).
- Springs suit drag, gestures and interruptible motion; keep bounce subtle.
- Put durations and curves in shared tokens (CSS custom properties or a theme file) instead of scattering numbers.

## Performance
- Animate only `transform` and `opacity`. Animating `width`, `height`, `top`, `left`, `margin` or `box-shadow` triggers layout or paint each frame.
- For height changes, use a grid-rows `0fr` to `1fr` transition, scale transforms, or a FLIP technique / the project's animation library layout feature.
- Use `will-change` sparingly and only on elements about to animate.
- Prefer CSS transitions for simple state changes; use the Web Animations API or the project's library (Framer Motion/Motion, React Spring, GSAP) for sequences, gestures and exit animations.
- Do not animate on scroll with JS scroll listeners; use `IntersectionObserver` or CSS scroll-driven animations where supported.
- Test on a throttled CPU (devtools 4x-6x slowdown). Look for dropped frames in the Performance panel.

## Details that matter
- Scale from a sensible origin: popovers grow from their trigger (`transform-origin`), not the center.
- Do not scale from 0; start around 0.95 with opacity 0 so it reads as appearing, not exploding.
- Press feedback: a slight scale down (about 0.97) on `:active`.
- Stagger list items by 20-50 ms and cap the total stagger.
- Keep hover effects behind `@media (hover: hover)` so touch devices do not get stuck states.

## Reduced motion
Wrap non-essential motion:
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```
Better: per component, replace movement with a simple opacity fade rather than removing feedback entirely. Libraries usually expose a hook (for example `useReducedMotion`).

## Review checklist
Purpose stated; duration and easing from tokens; transform/opacity only; interruptible; exit faster than enter; reduced-motion handled; smooth on throttled CPU.

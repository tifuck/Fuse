---
name: mobile-design
description: Design or review mobile app and mobile web screens for touch targets, safe areas, navigation, iOS/Android conventions and low-end performance. Use when building or auditing a mobile screen, React Native, Flutter or native UI.
metadata:
  category: Design
  inspired-by: designed-by-ai/skills
---
# Mobile design

Start by identifying the platform (iOS, Android, mobile web, or cross-platform such as React Native or Flutter) and the project's design system. Follow platform conventions unless the product has a deliberate, consistent alternative.

## Touch and reach
- Touch targets at least 44x44 pt (iOS) / 48x48 dp (Android); visual size can be smaller if the hit area is padded.
- At least 8 px between adjacent targets.
- Primary actions in the thumb zone (lower half). Destructive actions away from frequent ones.
- No hover-only affordances. Long-press and swipe gestures always have a visible alternative.
- Give immediate press feedback (highlight, ripple or slight scale) within 100 ms.

## Layout and safe areas
- Respect safe areas: notch, dynamic island, home indicator, rounded corners, Android gesture bar. On web use `viewport-fit=cover` with `env(safe-area-inset-*)`; natively use the platform safe-area APIs.
- Design for 320-430 pt wide first; check landscape and tablets if supported.
- Use `100dvh` (not `100vh`) on mobile web so browser chrome does not hide content.
- Keyboard: inputs scroll into view above the keyboard, and fixed bottom bars move or hide.
- Support dynamic type / font scaling up to at least 200% without clipping.

## Navigation
- iOS: tab bar for 3-5 top-level sections, navigation stack with back swipe, sheets for focused tasks.
- Android: bottom navigation or navigation rail, system back button and predictive back must work, top app bar.
- Keep the back behaviour predictable: back never loses unsaved input without asking.
- Deep links land on a usable screen with a way back.

## Platform conventions
- Use native controls where possible (date pickers, switches, share sheets, action sheets).
- iOS: SF Symbols style icons, large titles where appropriate, swipe actions on list rows.
- Android: Material components, FAB for the main creation action if the design uses one, snackbars for undo.
- Mobile web: correct `input type`, `inputmode` and `autocomplete` so the right keyboard and autofill appear.

## States and content
- Every screen has loading (skeleton matching layout), empty, error with retry, and offline states.
- Pull to refresh where lists update; keep scroll position when returning to a list.
- Truncate long text deliberately; test with long names, large numbers and translated strings (about 30% longer).

## Performance on low-end devices
- Test on a mid/low-end Android device or a throttled emulator, not only a flagship.
- Virtualize long lists (FlatList/FlashList, RecyclerView, ListView.builder, or a web virtualizer).
- Size and compress images for the display density; lazy-load off-screen media.
- Keep animations on transform/opacity and off the JS thread where the framework allows.
- Minimise startup work: defer non-critical requests and libraries.
- Budget network for slow 3G and handle request failures gracefully.

## Accessibility
Screen reader labels on all controls (VoiceOver and TalkBack), logical focus order, contrast at least 4.5:1, and no information carried by color alone. Honour reduce-motion and bold-text settings.

## Review output
List issues per screen with the guideline, the user impact and the concrete fix. Note which device sizes and platforms you actually checked.

# Combined theme: UX spec

A flat, dark reading view: a centred column, one-line work summaries, a pill composer and a status rail. Minimal, quiet, one accent. Markup and CSS live only in `static/themes/combined/` and `templates/themes/combined/`; the runtime finds elements by `data-*` hooks only. `scripts/qa/theme_audit.py --themes combined` is the gate: zero failures.

## Global rules

- **Type:** Bricolage Grotesque for text, Red Hat Mono for code and numbers. 13–13.5px chrome, 15px/1.6 prose, 12–12.5px meta. Text colours are `--mist` or `--mute` (≥4.5:1 on every surface, both schemes); `--cb-dim` only for icons, borders and disabled states. Accent text reaches 4.5:1 in light schemes too.
- **Icons:** one set, 16px box, stroke 1.4, `currentColor`, centred in square buttons. Rest `--mute` (≥3:1), hover = 7% mist fill, on = `--cb-on` fill.
- **Targets:** ≥28px on pointer devices, ≥36px at ≤800px or `(pointer: coarse)`, never under 32px at ≤480px (44px for drawer toggles, send and sheet close). Grow the hit area, not the glyph.
- **Focus:** `:focus-visible { outline: 2px solid var(--brass); outline-offset: 2px }` (inset inside clipping containers) at ≥3:1; the sidebar resize handle shows a 2px bar.
- **Escape** closes exactly the topmost layer and returns focus to its trigger. Where the runtime does not, the theme's `chrome.js` handles Escape in the capture phase for its own popups (context ring, account, row and ⋯ menus, rail dropdown) so the drawer under them stays open.
- **Breakpoints** (one set): ≥1040 docked rail; <1040 the rail is a dropdown card under the status pill; ≤800 sidebar drawer; ≤480 phone. The header adapts to its own width (container query).
- **Never:** horizontal page scroll; text past the viewport; a panel opening by itself.

## Header (36px, one row)

- Bar geometry (shared by both new themes and matching Console): 36px tall with its hairline at y=35 (45px on phones, a 44px row plus the line). The sidebar head has the same height and continues the same hairline, and its controls sit at the bar's control line. While the sidebar is open its collapse control sits at the head's right edge; collapsed, the expand control is the bar's first control at the left edge, on the spot the sidebar's own toggle uses.

- Left: sidebar toggle (while collapsed or a drawer) · project chip (bar ≥640px) · title (600 14px, ellipsis, full title in `title`). Same order at every width; the title is the only flexible item (`min-width: 6rem`).
- Right: the status pill (dot + "Working 2s" / "Done"; meta only when the bar is ≥900px; it toggles the rail, `aria-expanded`), then at most five equal square icon buttons: Context · Artifacts · Todos (only while a list exists) · Skills · Appearance. Alerts only when push is supported.
- Phones (bar <520px): toggle · title · pill (dot + one word) · Todos · ⋯, where ⋯ is a theme menu (`role=menu`, arrow keys) holding Artifacts, Context, Skills, Appearance, Alerts. The brand never sits where the toggle was.

## Sidebar

- Head (the bar's height and hairline): brand (14px mark + "Fuse") left; search and collapse icons right (28px).
- "New chat" / "New project" row (follows the tab), then the Chats | Projects segmented control (28px, inset 2px, each tab ≥24px tall). Tab badges are a 6px dot, never a number.
- Search: a 30px field with one ×; first Esc clears, second closes.
- Projects filters: one 28px quiet text row with chevrons, transparent, `--mute`, no border or black fill.
- Sections: 12px/500 sentence-case headers (Pinned, Today, Earlier), ≥4.5:1, never two in a row.
- Rows 28px (44px at ≤800), radius 7: a 14px lead slot (idle ring, running = pulsing dot with no glyph on top, unread = accent dot, or the project mark), title (ellipsis, full title + date in `title`), age muted 11px mono on the right. Selected = `--cb-on`. No counts. ⋯ on hover/focus at 24px in a reserved slot; on touch always visible at ≥36px.
- Footer: Archived as a quiet row without a count; account row (avatar 24, name, chevron). The account menu anchors to the footer and opens upward fully inside the viewport.
- Over a wallpaper: ≥96% opaque with blur.

## Transcript

- Column 47.5rem (prose ~720px); 12px phone gutters. Headings h1 1.4em … h4 1em, weight 600, never smaller than body. Links blue, no underline until hover. Inline code: subtle pill. Code blocks radius 10, hairline header, Copy 28px (36px at ≤480). Tables: hairlines, muted `th` at ≥4.5:1. Syntax colours meet 4.5:1 in both schemes.
- User message: right-aligned card, radius 14, padding 10px 14px, `--raise`, max-width min(80%, 36rem), clamped to 6 lines with a right-aligned "Show more"/"Show less" (`aria-expanded`).
- Under every settled reply, in order: prose → recap (same left edge and font as prose, 0.92em, `--mute`) → action row: copy · fork · pin · read aloud icons (14px in 28px buttons, `--mute`), then "Just now" (full time in `title`), then work time muted. In a subagent view only copy and read aloud.
- Thinking: one muted italic line, collapsed by default.

## Tool rows and events

- Anatomy, one row: `[12px icon] Verb argument …… result [caret]`. Verb sans 13px/600; argument mono 12px dim, truncated (middle for paths) with `title`; result right-aligned muted, `max-width: min(50%, 56ch)`, ellipsis + `title`; `+N` green, `−N` red. The result never moves when the row opens; at ≤480 it drops to its own line.
- Icon colour carries state: done = dim, running = live, failed = fault, stopped = idle. Notices and reports keep a state dot.
- Caret on every expandable row and only there; hit area ≥24px (≥32px at ≤480).
- Collapse (the design reference): finished calls between two prose blocks, edits and writes included, fold into ONE muted summary line: "Ran 2 commands, edited 3 files, created 1 file +88 −4 ▸" (one line, ellipsis + `title`, never clipped, caret outside the ellipsis). Running calls, the newest call while live, failures and subagent launches stay on their own rows. Opening the summary lists one-line member rows with their own carets; output, diffs and file bodies open from the member row only (max 16rem, scroll inside).
- Agent blocks fold; child rows are buttons that open the subagent; "N agents launched" is clickable wherever it shows a chevron.

## Rail

- **Open policy:** closed by default. Opens only on a user action (the status pill, Todos). The choice is saved per user in localStorage and survives reloads, thread switches, breakpoint changes, subagent navigation and new runs. Runtime events never open it: the pill text is the live summary, plus an unread dot.
- ≥1040: docked, 14.75rem. <1040: a dropdown card under the pill, closed by Esc and outside click, focus returned to the pill.
- Sections: agents, background commands, todos (inline, count and dismiss on the heading row), context meter last. Headings 12/500 at ≥4.5:1. Rows ≥32px (40px at ≤800); stop/kill/fold ≥24px (≥36px at ≤800).
- Subagent view: an always-visible "← Main" control.

## Composer

- One pill card, radius 16, three rows inside: attachments (optional), the textarea at full card width, then the controls row. The textarea never shares a row with the controls.
- Controls row (32px; 36px at ≤480): left `+`, Web toggle and permission as icon + label (labels fold to icons when the card is under 720px); right model · effort (muted), context ring, mic (visible recording state), send/stop.
- Textarea: 24px line box, 14px (16px at ≤800), grows to `min(40dvh, 18rem)` before a thin scrollbar. Placeholder "Type / for commands" in `--mute`.
- The spell mirror shares every metric with the textarea and is `color: transparent`.
- Popovers open upward from the card top inside the column; at ≤800 they are sheets.
- No welcome text sits directly on a wallpaper without the legibility layer.

## Sheets and popovers

- Menus and popovers: `--panel`, 1px `--line`, radius 12, shadow `0 12px 32px rgb(0 0 0 / .35)`, padding 4px, rows 32px (40px at ≤480). No square corners.
- Sheets: same tokens, radius 14, width min(720px, 100vw − 48px), title 14px/600, 32px × (40px on phones), backdrop `color-mix(in srgb, var(--ink) 55%, transparent)`.
- Elements the runtime moves to `<body>` are styled from `html[data-chat-view="combined"] body.is-chat > …`.
- Controls: switches 36×20 with a round knob, segmented 28px, ranges 28px tall with a 14px round thumb.
- `/theme`: panel above the composer, header "/theme · Wallpapers" with a right hint "↵ apply · esc close", thumbnail cards (radius 8) with labels, selected = 2px mist outline + ✓, × 28px, closes on outside click.

## Legibility over wallpapers

One layer of liquid glass, no text shadows: a full-height pane (the stage's `::before`) behind the transcript column, the welcome text and the composer dock with `blur(40px) saturate(1.5)` and the palette's ink at **55%** (**30%** in a light scheme: mostly blur, little colour). A horizontal `mask-image` fades blur and tint out over the gutter, up to 15rem past each side of the column, with eased stops ending 1rem outside the text, so there is no edge and no rectangle; it spans the stage's full height, so it has no top or bottom edge either, and it stays centred on the column when the rail or work strip takes the right side. Header and sidebar are lighter glass at **78%** (light **62%**); rail, composer card and cards denser at **88%** (light **80%**). Under `prefers-reduced-transparency` every layer is opaque. On phones the pane fills the width. Over a wallpaper, secondary text (table headers, blockquotes, the turn's meta line, links, inline code) leans a fifth toward the body colour so it holds up on the glass and still reads as secondary. Every text sample reaches 4.5:1 (icons 3:1) over every wallpaper in the wallpaper's own scheme, the state the /theme picker produces (a light wallpaper switches to the light palette, a dark one keeps the dark palette). A scheme forced the other way with the sun switch is best effort.

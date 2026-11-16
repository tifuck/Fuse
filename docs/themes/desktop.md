# Desktop theme: UX spec

A native coding-app window: quiet chrome, a centred transcript, a floating composer and an inspector of cards. Minimal, monochrome, one accent. No third-party branding. Markup and CSS live only in `static/themes/desktop/` and `templates/themes/desktop/`; the runtime finds elements by `data-*` hooks only. `scripts/qa/theme_audit.py --themes desktop` is the gate: zero failures.

## Global rules

- **Type:** 13–13.5px chrome, 15px/1.6 prose, 12–12.5px meta. Text colours are `--dk-text` or `--dk-mute` (≥4.5:1 on every surface). `--dk-dim` is for icons, borders and disabled states only, never text under 14px. On the default palette `--dk-dim` is at least `#8a8a8a`.
- **Icons:** one set, 16px box, stroke 1.4, `currentColor`, centred in square buttons. Icon buttons rest at `--dk-mute` (≥3:1), hover = 6% mist fill + text colour, on (`aria-pressed`/`aria-expanded`=true) = `--dk-sel` fill. No accent colours or shadows on chrome buttons.
- **Targets:** ≥28px on pointer devices, ≥36px at ≤800px or `(pointer: coarse)`, never under 32px at ≤480px. Grow the hit area with padding or `::after`, not the glyph.
- **Focus:** every focusable shows `:focus-visible { outline: 2px solid; outline-offset: 2px }` (or inset −2px inside clipping containers) at ≥3:1. A hairline handle (sidebar resize) gets a 2px bar on focus.
- **Escape** closes exactly the topmost layer (popover, menu, sheet, drawer, in that order) and returns focus to its trigger. Where the runtime does not, the theme's `chrome.js` handles Escape in the capture phase for its own popups (context ring, account, row, title and ⋯ menus) so the drawer under them stays open.
- **Breakpoints** (one set, every file): ≥1040 docked panels; 801–1039 the inspector is a viewport-anchored overlay; ≤800 sidebar drawer + inspector modal sheet; ≤480 phone. The top bar adapts to its own width (container query), not the viewport.
- **Never:** horizontal page scroll; text past the viewport (code, diff and stdout scroll inside their block, with a visible edge); a panel opening by itself.

## Top bar (36px, one row)

- Bar geometry (shared by both new themes and matching Console): 36px tall with its hairline at y=35 (45px on phones, a 44px row plus the line). The sidebar head has the same height and continues the same hairline, and its controls sit at the bar's control line. While the sidebar is open its collapse control sits at the head's right edge; collapsed, the expand control is the bar's first control at the left edge, on the spot the sidebar's own toggle uses.

- Left: sidebar toggle (only while the sidebar is collapsed or a drawer) · breadcrumb: folder icon, project or "Chats" (`--dk-mute`), "/", title ▾ (`--dk-text`, 500, 13.5px).
- The title is the only flexible item: `flex: 1 1 auto; min-width: 6rem`, ellipsis, full title in `title`. Crumb pieces clip their own overflow; nothing paints over the icons.
- Right, at most five icons: **Todos** (only while a list exists) · **Artifacts** · **Context** · **Inspector** · **⋯**. Below 640px of bar width, Artifacts and Context move into ⋯.
- No back/forward and no Chats|Code segment in the bar (the sidebar owns those).
- **⋯ menu** (`role=menu`, arrow keys with wrap, Home/End, Tab or Escape closes and refocuses ⋯, rows 32px, 40px on touch): New session · Open project… · Web search (checkbox item) · Terminal output · Skills · Appearance… · Wallpapers… `/theme` · Light/Dark · Alerts (only when push is supported) · separator · Scheduled · Projects. A sheet opened from ⋯ returns focus to ⋯ when it closes.
- **Title menu** opens anchored under the title (Rename, Pin, Fork, Archive) with `aria-expanded`, works with the sidebar collapsed, and returns focus to the title.

## Sidebar

- Head (the bar's height and hairline): Chats | Projects segmented tabs (28px, selected = subtle fill), then search and collapse pushed right.
- Action rows 28px, 14px icons, 13.5px text: New session · Scheduled · Customize.
- Search opens as a 30px field with one × (no native cancel button); first Esc clears, second closes.
- Projects-tab filters: one 28px quiet text row ("Recent ▾ · All projects ▾"), transparent, `--dk-mute`, chevron, no border or black fill.
- Section headers: 12px/500, sentence case, `--dk-mute`, never two in a row. Pinned (if any), then Recents.
- Rows 28px (44px at ≤800), radius 7: a fixed 14px lead slot (idle ring, running = pulsing 6px dot, unread = accent dot, or the project mark), the title (one line, ellipsis, full title + date in `title`), selected = `--dk-sel`. No counts. The ⋯ button shows on hover/focus at 24px in a reserved slot; on touch it is always visible at ≥36px.
- Footer: Archived as a quiet row without a count ("← Back" while on); the account row (avatar 24, name, chevron) plus a 28px sun/moon button. The account menu anchors to the footer and opens upward fully inside the viewport.
- Over a wallpaper: ≥96% opaque panel with blur.

## Transcript

- Column 44rem (prose 680px); 12px phone gutters. Headings h1 1.4em … h4 1em, weight 600, never smaller than body. Links blue (no underline until hover). Inline code: small pill. Code blocks radius 10 with a hairline header and a Copy button (28px, 36px at ≤480). Tables: hairlines, `th` muted at ≥4.5:1.
- User message: right-aligned pill, radius 16, padding 8px 14px, max-width 75%, clamped to 6 lines with "Show more"/"Show less".
- Under every settled reply, in order: prose → recap (same left edge and font as prose, 0.92em, `--dk-mute`) → action row: copy (the whole reply's markdown) · fork · pin · read aloud, then "Just now" (full time in `title`), then work time · tok/s. Icons 14px in 28px buttons. In a subagent view only copy and read aloud.
- Thinking: one muted italic line (≥4.5:1), collapsed by default.

## Tool rows and events

- Anatomy, one row: `[12px icon] Verb argument …… result [caret]`. Verb sans 13px/600; argument mono 12px in a chip, truncated (middle for paths) with the full value in `title`; result right-aligned, `--dk-mute`, `max-width: min(50%, 56ch)`, ellipsis + `title`; `+N` green, `−N` red. The result never moves when the row opens; at ≤480 it drops to its own line.
- Icon colour carries state: done = dim, running = live blink, failed = fault, stopped = idle. Notices and reports keep a state dot.
- Caret on every expandable row and only there; hit area ≥24px (≥32px at ≤480).
- Collapse: finished calls between two prose blocks fold into one summary line ("Ran 2 commands, edited 3 files +88 −4 ▸"), chevron after the text and outside the ellipsis, never clipped. Running calls, the newest call while live, failures and subagent launches stay on their own rows. Opening a summary lists one-line member rows; output, diffs and file bodies open from the member row only (max 16rem, scroll inside).
- Agent blocks are foldable; child rows are buttons that open the subagent.

## Inspector

- **Open policy:** closed by default. Opens only on a user action (the Inspector button, Todos/Terminal in the bar or ⋯, opening an artifact). The open/closed choice is saved per user in localStorage and survives reloads, thread switches, breakpoint changes and subagent navigation. New agents, jobs, todos and edits never open it: they put a dot on the Inspector button (and on the card head once open).
- ≥1040: docked column, pushes the transcript. 801–1039: overlay `min(27rem, 88vw)` under the top bar, anchored to the viewport, with a scrim; Esc/outside click close it. ≤800: full-screen modal sheet (`role=dialog`, `aria-modal`, inert page, focus moves in and returns to the toggle).
- Cards appear only with content (otherwise one quiet line): Tasks, Plan, Terminal, Diff. Card: radius 10, 1px line, ≥96% opaque over wallpapers; head 34px with title 13/600, mono sub in `--dk-mute`, × 28px flush right. × hides the card for that thread; new content only badges.
- Plan shows the todo list inline in its card, never as a bar at the window bottom. Diff folds unchanged runs over 6 lines, max-height 22rem, scrolls inside.
- Subagent view: an always-visible "← Main" control in the bar.

## Composer

- One floating card: radius 12, 1px `--dk-comp-line`, `--dk-comp` fill, shadow, width = column, 16px side padding (8px at ≤480). Rows inside the card: attachments (optional), the textarea at full width, the controls row.
- Above the card at card width: the chips row (host, project, branch; 24px chips), queue, permission cards, slash and `/theme` panels.
- Controls row (32px; 36px at ≤480): left `+`, permission mode as muted text; right model · effort (`--dk-mute`), 16px context ring, mic, send/stop (28px round, 32px at ≤480). Nothing sits outside the card on the wallpaper.
- Textarea: 24px line box, 14.5px (16px at ≤800 so iOS does not zoom), grows to `min(40dvh, 18rem)` before a thin themed scrollbar appears. Placeholder "Type / for commands" in `--dk-mute`.
- The spell mirror always shares every metric with the textarea and is `color: transparent`; only misspelling marks paint.
- Popovers open upward from the card top and stay inside the column and viewport; at ≤800 they are sheets.

## Sheets and popovers

- Menus and popovers: `--dk-card` at ≥92% + glass blur, 1px `--dk-card-line`, radius 12, shadow `0 14px 36px rgb(0 0 0 / .4)`, padding 4px, rows 32px (40px at ≤480).
- Sheets (Context, Skills, Appearance, Projects, model picker at ≤800): `--dk-card`, radius 14, title 14px/600, 32px × (40px on phones), backdrop `rgb(0 0 0 / .45)`.
- Elements the runtime moves to `<body>` are styled from `html[data-chat-view="desktop"] body.is-chat > …`.
- Controls: switches 36×20 with a round knob, segmented 28px, ranges 28px tall with a 14px round thumb.
- `/theme`: dark panel above the composer, header "/theme · Wallpapers" with a right hint "↵ apply · esc close" in `--dk-mute`, thumbnail cards with labels, selected = 2px text-colour outline + ✓, × 28px, closes on outside click.

## Legibility over wallpapers

No text shadows: a full-height pane (the stage's `::before`) behind the transcript column and the composer dock, tinted with the palette's base colour at **64%** (**58%** in a light scheme) and **no backdrop blur** (a full-height masked blur over the animated canvas is re-rendered every frame and dropped scrolling to ~15 fps). A horizontal `mask-image` fades the tint out over the gutter, up to 15rem past each side of the column, with eased stops ending 1rem outside the text, so there is no edge and no rectangle; it spans the stage's full height, so it has no top or bottom edge either. Top bar, sidebar and inspector are lighter glass at **78%** (light **62%**); composer card, chips, popovers and cards denser at **88%** (light **80%**). Under `prefers-reduced-transparency` every layer is opaque. On phones the pane fills the width. Over a wallpaper, secondary text (table headers, blockquotes, the turn's meta line, links) leans a fifth toward the body colour so it holds up on the glass and still reads as secondary. Every text sample reaches 4.5:1 (icons 3:1) over every wallpaper in the wallpaper's own scheme, the state the /theme picker produces (a light wallpaper switches to the light palette, a dark one keeps the dark palette). A scheme forced the other way with the sun switch is best effort.

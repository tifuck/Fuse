# Tail theme: UX spec

A modern console: monospace on a strict grid, hairlines and aligned columns
instead of box characters, colour only for added, failed, waiting and the
accent. Markup and CSS live only in `static/themes/tail/` and
`templates/themes/tail/`; the runtime finds elements by `data-*` hooks only.
Check every change with screenshots of the running page in this view
(`scripts/qa/shot.mjs --view tail`).

Tail grew from Console: `styles/tail.css` is its own layer, loaded last over
that foundation. Change Tail there; the files under it are the inherited
base.

## Global rules

- **Type:** JetBrains Mono by default (Appearance › Font changes it; a
  non-mono choice keeps code and chrome in Red Hat Mono). 13px rows and
  prose, 12px status bar. Text is `--mist` or `--mute`; `--tl-dim` only for
  the verb column and marks.
- **Colour:** the user's palette. `--brass` is the accent (prompt mark,
  edits, the mode chip, live ticks), `--ok` added lines, `--fault` removed
  lines and failures, `--status-live` waiting and stopped.
- **Grid:** one 1.5rem gutter. The prompt mark, the burst chevron and the
  receipt mark sit in it; prose, verbs and counts start after it. The
  transcript column is 64rem at most, centred; with the pane open it keeps
  the left.
- **Rows:** 1.75rem (2.25rem at ≤800px), radius 6px, hover and selection a
  6% wash.

## Transcript

- **Your prompt:** a raised band, accent `>` in the gutter, weight 600.
- **A burst of work** (the calls between two pieces of text) is one line:
  chevron, one tick per call, `N calls`, then on the right `+added −removed`,
  `N failed` and the calls' total time. Ticks: short and dim for a read,
  taller for a command, tall and accent for an edit or write, tall and red
  for a failure, amber for a stopped call, pulsing while running. A running
  burst names its running call after the count. The line opens the calls
  and stays folded until opened, live or not (`msg.stepOpen["burst-<block>"]`).
- **A call** is one row: verb (lower case, 8ch), subject, dotted leader,
  result. Edits read `+N −M`; failures are red. No host tag (the pane's head
  has it). A running command prints its newest output line under the row.
- **The receipt** ends a turn: mark, `done` / `interrupted` / `failed`,
  duration, calls, files with `+N −M`, failures, speed, time.

## Output pane

- A click on a call opens its output in a pane on the right (28rem, 40vw at
  most); the transcript keeps its place and gives up that width. One call at
  a time: opening another replaces it. The pane's head names the call and
  its host; the close button, Escape, or the same row closes it.
- At ≤800px the pane is a sheet over a backdrop, 74% of the height; a tap on
  the backdrop closes it.
- A call inside another call's output (run_code) opens in place.
- The pane is the call's own `.chat-console-full`, fixed in place by CSS, so
  the runtime keeps patching a live call as before. The turn that owns it
  drops `content-visibility` while it is open.

## Composer and status bar

- The prompt line: accent `›`, the input, send (stop while running) at the
  right. Its top hairline takes the accent on focus.
- Under it the status bar (26px, 36px on phones): the permission mode as an
  accent chip, attach, the model, web search; on the right the machine's cpu
  and ram while a project chat runs, then context use. Menus open over
  their own control.
- A permission ask sits above the prompt line, inside the column's gutters.

## Top bar and sidebar

- Top bar: sidebar toggle, project chip, title; on the right `working` while
  a reply runs (with a line sweeping under the bar) or `needs you` while an
  ask is pending, then the icon buttons. One row on phones too.
- Sidebar: Chats and Projects as in Console. Rows that run on a machine sit
  under that machine's name (`sidebar.js` `byMachine`); a list already
  grouped by project keeps that grouping. Plain chats have no machine and
  list as they are.

## Not redrawn yet

Still Console's look inside Tail: the agents strip, subagent trees, notices
and compaction marks, todos, the sheets (Context, Skills, Appearance) and the
artifact viewer.

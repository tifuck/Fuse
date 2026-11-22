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

- **Type:** JetBrains Mono by default, bundled in `static/fonts`
  (Appearance › Font changes it; a
  non-mono choice keeps code and chrome in Red Hat Mono). 13px rows and
  prose, 12px status bar. Text is `--mist` or `--mute`; `--tl-dim` only for
  the verb column and marks.
- **Colour:** the user's palette. `--brass` is the accent (prompt mark,
  edits, the mode chip, live ticks), `--ok` added lines, `--fault` removed
  lines and failures, `--status-live` waiting and stopped.
- **Grid:** one 1.5rem gutter. The prompt mark, the burst chevron and the
  receipt mark sit in it; prose, verbs and counts start after it. The
  transcript column spans the full width, from the left; with the pane open
  it gives up the pane's width.
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
- **A call** is one row: verb (lower case, 9ch, so an 8-letter verb keeps a gap), subject, dotted leader,
  result. Edits read `+N −M`; failures are red. No host tag (the pane's head
  has it). A running command prints its newest output line under the row.
- **The receipt** ends a turn: mark, `done` / `interrupted` / `failed`,
  duration, calls, files with `+N −M`, failures, speed, time.

## Pane

- One pane on the right (28rem, 40vw at most; a sheet at ≤800px, 74% of the
  height, over a backdrop). The transcript keeps its place and gives up that
  width. `templates/themes/tail/_pane.html` is its markup,
  `static/themes/tail/pane.js` its behaviour; `data-tail-pane` on the chat
  root names the view on show.
- It opens from the top bar's last button, from a click on a call, and by
  itself when a subagent's transcript opens (its list is the way back to
  main). The close button, the backdrop or Escape closes it. Escape goes to an
  open menu or dialog first.
- **output:** the open call. One call at a time: opening another replaces
  it; closing the pane closes the call. A pane opened by a call closes when
  that call does. The head names the call and its host. The body is the
  call's own `.chat-console-full`, fixed over the pane by CSS, so the runtime
  keeps patching a live call as before; the turn that owns it drops
  `content-visibility` while it shows. A call inside another call's output
  (run_code) opens in place.
- **changes:** every file the chat's finished edits and writes touched,
  newest first, with `+added −removed` summed over its edits. A row opens the
  newest call that changed the file.
- **agents:** the runtime's agents list and background commands
  (`data-agents-list`, `data-jobs-list`), drawn as rows: name, state and what
  it is doing, model, clock and one action. The tab counts them all; the
  top-bar button's badge counts what is still running.

## Composer and status bar

- The prompt line: accent `›`, the input, send (stop while running) at the
  right. Its top hairline takes the accent on focus.
- Under it the status bar (26px, 36px on phones): the permission mode as an
  accent chip, attach, the model, web search; on the right the project's
  machine and path, the machine's cpu and ram while it reports them, then
  context use. Menus open over their own control.
- A permission ask sits above the prompt line, inside the column's gutters.

## Top bar and sidebar

- Top bar: sidebar toggle, project chip, title; on the right `working` while
  a reply runs (with a line sweeping under the bar) or `needs you` while an
  ask is pending, then the icon buttons. One row on phones too.
- Sidebar: Chats and Projects as in Console. Rows that run on a machine sit
  under that machine's name (`sidebar.js` `byMachine`); a list already
  grouped by project keeps that grouping. Plain chats have no machine and
  list as they are.

## The other rows, and the chrome

- `styles/tail-rows.css` draws what else a transcript holds, in the same
  grammar (verb, subject, leader, result, all on the one gutter): subagent
  groups and reports, monitor events, run notices, compaction marks, thoughts,
  todos, run_code's calls, the request-failed card, attachments, artifact
  cards, sources and skill proposals, plus what sits above the prompt line:
  permission asks, queued messages and the context alert.
- `styles/tail-chrome.css` draws the chrome as one surface style (panel,
  hairline, 6px corners, `--tl-row` rows): top bar, sidebar, the Context,
  Skills, Appearance and Todos sheets, the menus, the artifact viewer and the
  empty chat (the greeting over the prompt library, just above the prompt).
- At ≤800px the top bar folds Appearance, Context, Skills and Alerts into one
  More menu (`data-tail-more`, `pane.js`), and the status bar shows the
  machine's name without its load.

## Not seen yet

No QA chat or fake produces these, so they were styled from made-up markup
only: a live thought, a notice with more than two report lines, the
automated-event chip, a recap, a note from the main agent, the rewind dialog.
A subagent's own transcript cannot be opened in the QA chats.

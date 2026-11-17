---
name: refactor
description: Behaviour-preserving refactor in small, safe steps with tests green between each step. Use when restructuring, renaming, extracting, deduplicating or simplifying code without changing what it does.
metadata:
  category: Build
---
# Refactor

Change structure, not behaviour. Every step leaves the code working and the tests green.

## Before touching code
- State the goal in one sentence (extract the pricing logic into its own module; remove the duplicate parsers). Stop when it is reached.
- Find every use of what you will change: grep for the symbol, its string name (reflection, config, templates, routes), re-exports and public API surface.
- Run the tests and record the baseline. If coverage of the area is thin, add characterization tests first: tests that pin current behaviour, including odd behaviour, before changing anything.
- Confirm the working tree is clean or that you know which changes are the user's.

## Steps
Use small, named moves, one at a time:
- Rename (variable, function, module), updating all references.
- Extract function / method / module; inline the reverse.
- Move code to where it belongs; leave nothing behind.
- Replace a conditional chain with a lookup or polymorphism where it clearly simplifies.
- Introduce a parameter object for long argument lists.
- Collapse duplication only when the copies really mean the same thing.
After each move: run the fastest relevant tests and the type checker. If something breaks, fix it now or undo the step; do not stack moves on a red build.

Prefer tooling where it exists (IDE or language-server rename, `gofmt -r`, codemods) over hand edits for wide mechanical changes, and review the result.

## Public interfaces
If a public function, CLI flag, config key or API shape must change, keep the old name working as a thin alias or deprecation shim unless the user agrees to break it. Note any such change explicitly.

## Rules
- No feature changes, bug fixes or behaviour tweaks mixed in. If you find a bug, note it and fix it in a separate change (or ask).
- Do not change test expectations during a refactor. If a test needs to change, the behaviour changed; stop and reconsider.
- Keep error handling, validation and logging when moving code.
- Do not reformat untouched code; it hides the real diff.
- Delete code that becomes dead because of your change; mention, do not delete, unrelated dead code.
- Keep commits small and named after the move if committing is requested.

## Finish
- Run the full relevant suite and lint once.
- Read the final diff top to bottom: it should read as pure restructuring.
- Report the moves made, files touched, any interface changes, and what was run.

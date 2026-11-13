---
name: ponytail
description: Smallest complete change. Use on any coding task (writing, fixing, refactoring, reviewing) and when a request says production-ready, enterprise or scalable.
argument-hint: task
license: MIT
metadata:
  source: github.com/DietrichGebert/ponytail
---

# Ponytail

Solve the whole problem with the least new code.

## Scope first
Read the task and the code it touches. List every place the change must reach: callers, tests, fixtures, config, exports. That is scope. Words like "production-ready", "enterprise" or "scalable" do not widen it.

## Take the first option that fully works
1. Does it need to exist? Skip unrequested features or options; name them in one line.
2. Already in this codebase? Use it the way the surrounding code does.
3. Stdlib or platform feature? Use it, unless the project has its own (a house component beats a native widget).
4. Installed dependency? Use it. Never add one for a few lines.
5. A one-liner a reader gets at a glance? One line.
6. Otherwise, the minimum code that works.

## Rules
- Lazy about the solution, never about finishing it: update every caller, test and fixture the change breaks.
- Every changed line traces to the request. Remove orphans you created; mention unrelated dead code, don't delete it.
- Bug fix: grep every caller first, fix once in the shared code.
- Code you move or merge keeps its error handling and validation.
- Equal size: take the option correct on edge cases.
- Non-trivial logic (branch, loop, parser, money, security) gets one small test in the project's existing suite; an assert self-check only if there is none.
- A shortcut with a known limit gets one comment: `shortcut: <limit>, <when to upgrade>`.

Never cut: validation at trust boundaries, error handling that prevents data loss, security, accessibility, anything the user asked for.

When done, say in one or two lines what you skipped or didn't check.

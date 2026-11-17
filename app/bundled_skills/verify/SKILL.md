---
name: verify
description: Verify a change by actually running it end to end, not only unit tests, and report exactly what ran. Use after implementing a fix or feature, before committing or claiming done, or when asked to check that something works.
metadata:
  category: Quality
---
# Verify

Verification means running the changed behaviour the way a user would hit it. Passing unit tests alone is not verification.

## 1. Find the project's verify steps
Look in this order and use what the project defines instead of inventing commands:
1. A Verify or Testing section in AGENTS.md, CLAUDE.md or CONTRIBUTING.md.
2. README (Development, Testing, Running locally).
3. Makefile / justfile targets (`make check`, `make test`), package.json scripts, pyproject.toml tool config, CI workflow files under `.github/workflows`.
If nothing exists, derive the minimum: the linter and type checker the repo already configures, the test runner, and how to start the app.

## 2. Cheap checks first
Run in order, stopping at the first failure to fix it:
1. Format and lint on changed files.
2. Type check.
3. The narrowest tests covering the change (one file or one test id).
4. The broader suite only if the change is wide or shared.
Write long output to a log file and read the tail instead of flooding context.

## 3. Run it end to end
Exercise the real path the change affects:
- CLI: run the command with realistic input and check output and exit code.
- HTTP service: start it locally, call the endpoint (`curl -sS -i`), check status and body.
- UI: load the page in a browser and perform the flow.
- Library: a small script that imports and calls the public API.
- Bug fix: reproduce the original failure first if possible, then show it gone.
Do not touch production, shared databases or external services to verify. If end to end is impossible here (missing credentials, hardware, paid API), say so and state what you ran instead.

## 4. Fix rounds
- If a check fails, find the cause and fix it. Count each fix-and-rerun as one round.
- After three failed rounds on the same check, stop. Report what you tried, the current error, and your best hypothesis, and hand back to the user.
- Never retry a flaky test until it passes, add sleeps, raise timeouts or mark it skipped to get green. Find the root cause (ordering, shared state, time, network). If a test is truly worthless, deleting it needs a written reason the user can see.
- Never weaken an assertion to make it pass.

## 5. Report
End every report, and every commit message you write, with one line:
```
Verified: <commands and flows actually run, with result>
```
or `Verified: none (<why>)` when nothing ran. Never imply verification that did not happen; "should work" is not verified.

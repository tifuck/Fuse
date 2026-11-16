---
name: implement-plan
description: Execute a written plan one step at a time, running each check, committing at sensible points, and stopping to report on deviations or after three failed attempts. Use when the user says to implement, execute or follow a plan.
metadata:
  category: Build
  inspired-by: mattpocock/skills
---
# Implement Plan

Carry out a written plan faithfully, with evidence at every step. The plan is the contract; changes to it go through the user.

## Before starting

1. Read the whole plan and the design it references. Read the files it names.
2. Check the starting state: `git status` and `git log --oneline -5`. If there are uncommitted changes you did not make, ask before touching them.
3. Confirm the project's test and build commands work on the untouched tree. If they already fail, report that before changing anything, so later failures are not blamed on your work.
4. Raise any step that looks wrong, ambiguous or unsafe now, not halfway through.
5. If the user wants a branch, create one; do not switch or create branches unasked when the tree is dirty.

## Per step

1. Announce the step number and its goal in one line.
2. Write or update the test the step names; run it and confirm it fails for the expected reason.
3. Make the change, matching surrounding code style. Touch only what the step requires.
4. Run the step's check command. Read the output; do not assume.
5. If it passes, run the nearby test suite to catch regressions, then mark the step done in the plan file if it has checkboxes.
6. Commit when the plan says so, or after each green step if the user wants commits: small commits with clear messages. Never skip hooks, force-push or rewrite history you did not create.

## When things go wrong

- **Check fails**: diagnose from the actual error, fix, rerun. Count attempts per step.
- **Three failed attempts on one step**: stop. Report what you tried, the exact error, your best theory, and options. Do not keep flailing or weaken the test.
- **Plan is wrong** (a file does not exist, an API differs, a step conflicts with the code): stop and describe the deviation with a proposed fix. Continue only after the user agrees, then update the plan text.
- **Scope creep** (a tempting refactor, an unrelated bug): note it for the report; do not do it.
- **Destructive operations** (migrations on real data, deletes, deploys): ask before running, even if the plan lists them.

## Finishing

- Run the plan's full verification section.
- Report: steps completed, commits (sha and message), verification commands with pass/fail, deviations agreed, and anything left undone or noted for later.
- Do not claim a step is done without command output that shows it.

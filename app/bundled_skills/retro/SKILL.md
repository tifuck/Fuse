---
name: retro
description: Run a short retrospective on a finished task or session (what went well, what went wrong, root causes) and turn each miss into a rule, test or doc change. Use after a task, incident or session, or when asked what to learn.
metadata:
  category: Agent
  inspired-by: mattpocock/skills
---
# Retro

The output of a retro is changes that prevent repeats, not a diary. Keep it blameless and evidence-based.

## 1. Reconstruct the timeline

From the conversation, git history (`git log --oneline`, `git diff <start>..HEAD --stat`), test runs and notes, list the key events briefly:

- Goal at the start, and whether it changed.
- Major steps and decisions.
- Moments of friction: wrong assumptions, failed attempts, rework, user corrections, time sinks.
- Outcome: what shipped, what did not.

## 2. What went well

Two to five items worth repeating, each specific ("writing the failing test first caught the timezone bug in one run"). Skip generic praise.

## 3. What went wrong

For each problem:

- **What happened**: the observable fact.
- **Impact**: time lost, bug shipped, user had to intervene.
- **Root cause**: ask "why" until you reach something changeable. Stop at a cause you can act on, such as a missing check, unclear instruction, absent test, misleading name or undocumented setup step. "I made a mistake" is not a root cause.

Include user corrections: each one is a signal that an instruction or assumption was missing.

## 4. Turn misses into rules

For each root cause propose exactly one concrete change, the cheapest one that would have caught it:

- **Test**: a regression test that fails on the old behaviour.
- **Check**: a lint rule, CI step, type, or assertion.
- **Doc**: a line in README, CONTRIBUTING or a code comment at the trap.
- **Agent instruction**: a rule for AGENTS.md / CLAUDE.md or a skill, written as a short, testable imperative ("Run `make migrate` before `pytest`; tests assume a migrated DB.").

Prefer automated guards over written advice. Make written rules specific enough that following them is verifiable.

## 5. Report

```
## Outcome
## Went well
## Went wrong
- <problem> -- cause: <root cause> -- fix: <change, file>
## Proposed changes
1. <change> (<type>, <file>)
```

## Rules

- Propose; do not apply. Edit tests, docs or instruction files only after the user picks which changes to make.
- Keep it short: a session retro should fit on one screen.
- Do not invent problems to fill sections. If nothing went wrong, say so and stop.
- No blame on people; focus on systems, instructions and checks.

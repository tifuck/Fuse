---
name: orchestrator-dispatch
description: Plan, brief and check subagent work. Load before spawning subagents or when the user asks to delegate or orchestrate. Not a reason to spawn.
argument-hint: job or slice
license: MIT
---

# Orchestrator Dispatch

How to split a job across subagents, brief them, and check what comes back. This skill decides how to delegate, not whether: loading it is not a reason to spawn.

The lead (you) owns the plan, the decisions and the final answer. Each worker owns one slice and reports back. The payoff is the lead's context and wall-clock time: a worker reads twenty files and returns one paragraph. A same-model clone is not cheaper per token; it is a clean context.

Tool-agnostic: use the host's spawn tool. Without one, run separate sessions and paste back only the report.

## When to delegate

Delegate a slice when all of these hold:

- **Independent:** it needs no other slice's output, and no one else edits its files.
- **Briefable:** you can brief it in a paragraph. If you can't write the brief yet, you don't understand the problem well enough to split it. Orient first.
- **Reads more than it returns:** a sweep, a search, an audit, an implementation with a clear spec, or a fresh-eyes review.

Do it yourself when the work is:

- **Small:** a few tool calls, a one-file edit, a lookup. Spawning costs more than it saves.
- **Coupled or sequential:** step two needs step one's full reasoning. Splitting it loses the reasoning.
- **Exploratory:** you don't know what's broken yet. Investigate until you can name the slices.
- **High-stakes:** production, live data, secrets, anything irreversible. Keep those in the lead, where the user's approvals and your full context are.

If the user explicitly asks for subagents, honor it, but don't fan out beyond what the work needs.

Scale fan-out to the job: one worker for a focused question, two to four for a comparison or a few independent fixes, more only for wide sweeps with clearly divided areas. Past about eight live workers, run waves instead; over-spawning on simple jobs is the common failure.

## Plan, then go

1. Orient: a short map of the layout, the relevant modules and how they fit. Enough to slice, not a full read.
2. Write the plan where the user can see it, in a few lines: each slice, read or write, parallel or sequential, which model. Put it in the todo list if the host has one; that is also your record after compaction.
3. Dispatch. The user asking you to delegate is the go-ahead: do not ask them to approve the plan or pick between options.

Stop and ask first only when a slice would change production, live data or anything irreversible, or when the request is ambiguous enough that a wrong split would waste the run.

If the plan changes mid-run (a different split, model or fan-out), say so in one line and continue.

## Parallel or sequential

**Read-only slices** (audit, search, review, investigation): parallel. Launch them in one step.

**Write slices:** sequential by default. Parallel writers share one working tree, one test run and one git index, and each makes small implicit decisions (naming, error handling, style) the others can't see. Run writes in parallel only when the files are disjoint and no slice touches a shared interface, config, lockfile, schema or generated file. Name each writer's files in its brief.

A shape that fits most coding jobs: parallel read-only workers map and audit, the lead decides, then one writer at a time.

## Pick a model per slice

Use the least capable model that will do the slice well:

- Mechanical work with a complete spec (one or two files, exact changes named): a cheap, fast model.
- Integration, debugging, multi-file judgment: a standard model or a same-model clone.
- Design, architecture, final review: the most capable model available.

Cheap models often take two or three times the turns on multi-step work, so they are not always cheaper overall. When unsure, clone.

## The brief

A worker sees none of your conversation. Everything it needs goes in the brief:

```
Goal: what and why, in a line or two; where this slice fits in the job
Context: decisions already made, conventions to follow, facts it cannot discover (not the transcript)
Scope: files or symbols it owns; what it must not touch
Done when: the concrete finish line
Verify: the test, build or check to run
Constraints: do not spawn subagents; do not commit, push, deploy or restart services unless stated here
Return: the worker report below
```

Thin briefs cause duplicate work and gaps: two workers search the same thing, or both skip the same corner. Pass the decisions, not the history. Don't paste other slices' full results; pass only the interfaces or conclusions this slice depends on. Keep a long brief you may resend in a file or scratch note and reference it.

## Worker report

```
STATUS: done | done_with_concerns | needs_context | blocked
DONE: what changed, or what was found
FILES: paths touched, or path:lines cited for findings
TESTS: what ran and the result, or none
ASSUMPTIONS: judgment calls the lead should know about
LEFTOVER: what was not covered, open questions, blockers
```

Findings cite `path:lines` (or `source | excerpt` for off-code evidence). Implementation lists files, not every edit. Long output (a full audit, logs) goes to a file; the report gives its path.

Workers finish the slice rather than hand it back, but don't guess silently: an uncertain call goes in ASSUMPTIONS with `done_with_concerns`; missing access or context is `needs_context` or `blocked`.

## Check the work

A worker's report is a claim, not evidence. Check in proportion to the slice's risk, not to which model ran it.

- **Every report:** STATUS, FILES and TESTS are present and match the brief. "Fixed it" with no files is not done.
- **After writes:** read the diff summary (stat first, then the hunks that matter), confirm parallel slices did not collide, and run the relevant tests yourself.
- **Security, data loss, concurrency:** read the changed code, or send a fresh read-only reviewer with only the diff and the requirement. A clean context catches what the author can't see.

Don't re-do a slice that checks out, and don't pull a whole diff into your context when the stat and the tests answer the question.

## When a slice fails

Timeout, crash, an empty or malformed report, or coverage well short of the scope:

1. Resend once, tighter: a smaller scope or the specific gap. Resume the same worker if the host allows it, so it keeps what it learned.
2. If it fails again, do the slice yourself or report it as uncovered. Partial coverage is not success.

Two workers in conflict means the slices overlapped: re-slice rather than resend the same briefs. A worker that starts planning the whole repo got too wide a brief: stop it and narrow the scope.

## Keep a record

Track each slice's status (pending, running, done, failed) in the todo list or a scratch note. Compaction drops conversation memory, and re-dispatching finished work is the most expensive orchestration mistake. After compaction, trust the record and `git log` over recollection.

## Audit wave

Parallel read-only workers, one per area; the lead merges, then re-dispatches only failures and gaps. The lead writes the merged result in its own words:

```
FINDINGS:
- id | path:lines | failure mode | why
GAPS:
- what no worker covered
```

## Anti-patterns

- Spawning for small, coupled or exploratory work.
- Asking the user to approve a plan they already asked you to run.
- One-line briefs, or the transcript instead of the decisions.
- Parallel writers on shared files or interfaces.
- Workers spawning workers.
- Accepting a report unchecked, or re-doing one that checks out.
- Resending the same brief after a failure.
- Losing track of finished slices after compaction.

---
name: grill-me
description: Interview the user one question at a time about a plan or design, with a recommended answer each time, until every open decision is settled. Use when the user says "grill me", wants a plan stress-tested, or before building something vague.
metadata:
  category: Plan
  inspired-by: mattpocock/skills
---
# Grill Me

Walk the decision tree of a plan with the user until nothing important is left implicit. You ask, they decide; you never decide silently for them.

## Before the first question

1. Read what already exists: the plan text, linked docs, and the code it touches. Do not ask anything the repo or conversation already answers; state that answer as a fact instead.
2. Write a private list of open decisions, grouped by branch (scope, data model, interfaces, failure handling, rollout, testing). Order it so decisions that unlock others come first.

## Asking

- One question per message. Never batch.
- Each question has three parts:
  - **Question**: concrete and answerable in a sentence ("Should a failed sync retry automatically, or surface an error and stop?").
  - **Options**: two to four realistic choices, each with its main trade-off in one line.
  - **Recommendation**: the option you would pick and the reason, tied to something specific in their code or goals.
- Accept short replies ("yes", "B", "your call"). "Your call" means take your recommendation and record it as an assumption.
- If an answer opens a new branch, add it to the list and follow it before moving sideways.
- If an answer contradicts an earlier one, point out the conflict and ask which wins.
- If the user says "stop" or "enough", stop and summarise.

## What to probe

- Scope edges: what is explicitly out, what happens at the boundary.
- Data: source of truth, ownership, migration of existing data, deletion.
- Failure: timeouts, partial failure, retries, idempotency, what the user sees.
- Concurrency and ordering, if more than one actor can touch the same thing.
- Security and permissions: who may do this, what must never leak.
- Operability: how to tell it is working, how to roll back.
- Testing: what proves it works, and what is hard to test.

Skip branches that clearly do not apply. Ten sharp questions beat thirty generic ones.

## Finishing

When every branch on the list is resolved, post a summary:

```
## Decisions
- <decision> -- <reason>
## Assumptions (user said "your call" or did not object)
- ...
## Still open
- ... (or "none")
```

Offer to turn the summary into a design doc, PRD or implementation plan. Do not start writing code from this skill.

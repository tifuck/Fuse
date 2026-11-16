---
name: to-issues
description: Break a plan or PRD into independently shippable issues (thin vertical slices) with acceptance criteria and dependencies, output as markdown. Use when the user wants work split into tickets or a backlog; file issues only if asked.
metadata:
  category: Plan
  inspired-by: mattpocock/skills
---
# To Issues

Split a plan or PRD into issues that can each be built, reviewed and shipped on their own.

## Steps

1. Read the source: PRD, design, plan, or the conversation. Read enough of the code to know where each piece lands.
2. List the user-visible capabilities. Each is a candidate slice.
3. Cut each capability into vertical slices: a thin path through every layer needed (data, logic, API, UI, tests) that delivers something demonstrable. Avoid horizontal slices like "build the database layer" that ship nothing on their own.
4. Order slices: a walking skeleton first (the thinnest end-to-end path), then slices that widen it. Put risky unknowns early.
5. Mark dependencies. Prefer slices that are independent; when one truly blocks another, say so explicitly.
6. Present the list for review before writing anything to a tracker.

## Issue template

```
### <N>. <Short imperative title>
Type: feature | bug | chore | spike
Blocked by: <issue numbers or "none">

What: <1-3 sentences, user-visible result>
Why: <link back to the PRD goal or story>

Acceptance criteria
- [ ] <observable, testable result>
- [ ] Tests cover <case>

Notes: <files likely touched, gotchas, out-of-scope items>
```

## Sizing rules

- One slice should fit in one focused session and one reviewable PR. If it needs more than about five acceptance criteria, split it.
- Too small is also a smell: merge slices that cannot be demoed alone.
- When something is genuinely unknown, create a time-boxed spike issue whose output is a decision, not code.
- Cross-cutting work (auth, logging, migrations) goes inside the first slice that needs it, not in its own layer issue, unless it is large.

## Filing

- Default output is markdown in chat or a file the user names.
- File to a tracker (for example with `gh issue create`) only when the user asks. Show the final list first, then file in dependency order so later issues can reference earlier numbers. Never close, edit or relabel existing issues without asking.
- After filing, return the created issue numbers or links mapped to the slice numbers.

## Check before handing over

- Every PRD story and acceptance criterion is covered by at least one issue.
- No issue depends on a later one.
- Each title reads as a result ("Users can reset password by email"), not an activity ("Work on reset").

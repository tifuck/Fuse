---
name: to-prd
description: Turn the conversation and code context into a concise product requirements doc: problem, users, goals and non-goals, user stories, acceptance criteria, open questions. Use when the user asks for a PRD, spec or requirements doc.
metadata:
  category: Plan
  inspired-by: mattpocock/skills
---
# To PRD

Distil what has been discussed into a PRD that a product owner can approve and an engineer can build from. Synthesise; do not restart the interview.

## Steps

1. Reread the conversation and any linked notes. Extract: the problem, who has it, decisions made, constraints, and things explicitly ruled out.
2. Skim the relevant code so the PRD matches reality: existing features, naming, current behaviour it changes. Use the project's own terms.
3. If a gap would make the PRD misleading (for example no idea who the user is), ask at most two or three targeted questions. Otherwise record gaps under Open questions and keep going.
4. Write the PRD using the template below. Keep it short: most PRDs fit on one or two screens.
5. Save it where the project keeps docs (ask if unclear) or post it in chat. If the user wants it filed as an issue, show the final text and ask before creating it.

## Template

```
# <Feature name>

## Problem
<What is wrong or missing today, for whom, and the evidence. 2-5 sentences.>

## Users
- <persona or role>: <what they need>

## Goals
- <measurable or observable outcome>

## Non-goals
- <tempting adjacent work that is out of scope>

## User stories
1. As a <role>, I want <capability>, so that <benefit>.

## Acceptance criteria
- [ ] Given <context>, when <action>, then <observable result>.

## Constraints and dependencies
- <technical, legal, performance, compatibility>

## Open questions
- <question> -- <who can answer>
```

## Quality bar

- Describe behaviour, not implementation. Leave class names, schemas and libraries to the plan unless a constraint forces them.
- Every user story has at least one acceptance criterion; every criterion is testable by someone who did not write it.
- Cover the unhappy paths that matter: invalid input, permissions, empty states, failure of a dependency.
- Non-goals are concrete. "Performance" is not a non-goal; "No offline mode in v1" is.
- No filler sections. Drop a heading rather than write "N/A".
- Do not invent metrics, quotes or user research. If a number is a guess, label it as one.

Afterwards, offer to break the PRD into issues (see to-issues).

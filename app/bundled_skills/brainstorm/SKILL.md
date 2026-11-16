---
name: brainstorm
description: Turn a rough idea into an approved written design through short dialogue (purpose, constraints, two or three approaches with trade-offs) before any code. Use when the user has an idea, feature or change to build but no agreed design yet.
metadata:
  category: Plan
  inspired-by: obra/superpowers
---
# Brainstorm

Get from "I want X" to a design the user has approved, quickly and without writing code. Code starts only after approval.

## 1. Understand the context

- Read the relevant parts of the project first: README, the modules the idea touches, recent commits in that area. Note existing patterns the design should reuse.
- Restate the idea in one or two sentences and ask the user to confirm or correct it.

## 2. Pin down purpose and constraints

Ask one question at a time, multiple choice when possible. Cover only what matters for this idea:

- Who uses it and what problem it solves for them.
- What success looks like (observable outcome, not a feature list).
- Hard constraints: platforms, performance, compatibility, deadlines, dependencies the user will or will not accept.
- Explicit non-goals.

Stop asking once you could explain the feature to another engineer without hand-waving.

## 3. Propose approaches

Present two or three genuinely different approaches, not variations of one. For each:

- How it works, in three to five lines.
- What it costs: new code, new dependencies, migration, operational burden.
- Where it breaks or gets awkward.

Recommend one and say why, in terms of the user's stated constraints. Prefer the simplest approach that meets them; cut speculative features (YAGNI).

## 4. Write the design in sections

After the user picks an approach, present the design in short sections (roughly 150-300 words each) and check each one before continuing:

1. Overview and goals / non-goals
2. Components and their responsibilities
3. Data model and data flow
4. Interfaces (APIs, CLI, UI states)
5. Error handling and edge cases
6. Testing approach
7. Rollout and migration, if any

Revise when the user pushes back; do not defend a section just because it is written.

## 5. Save and hand off

- Save the approved design as a markdown file where the project keeps docs (for example `docs/designs/<date>-<topic>.md`). Ask before creating a new docs folder.
- Do not commit unless the user asks.
- End by offering the next step: an implementation plan (see write-plan) or a prototype for any still-uncertain part.

## Rules

- No code, scaffolding or dependency installs until the design is approved.
- Keep each message short; one question or one section at a time.
- If the idea is already well specified, say so and skip straight to step 4.

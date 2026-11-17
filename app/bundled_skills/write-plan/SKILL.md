---
name: write-plan
description: Write an implementation plan a fresh agent could execute without context, listing files to touch, small ordered steps, a test and check per step, and risks. Use after a design is agreed and before coding a multi-step change.
metadata:
  category: Plan
  inspired-by: obra/superpowers
---
# Write Plan

Produce a plan that someone with zero memory of this conversation, but full repo access, could follow step by step and know at every step whether they succeeded.

## Gather first

- Read the design, PRD or request the plan is for. If none exists and scope is unclear, ask or suggest brainstorming first.
- Read the code the change touches: entry points, callers, tests, config. Find the project's real build, test and lint commands in README, Makefile, package.json, pyproject.toml or CI config. Never guess them.
- Note conventions to follow: file layout, naming, test style, error handling.

## Plan structure

Save as markdown (for example `docs/plans/<date>-<topic>.md`, or where the project keeps plans). Use this layout:

```
# <Feature> implementation plan
Goal: <one sentence>
Design: <path or link to the agreed design>
Constraints: <stack, conventions, things not to touch>

## Files
- Create: path -- purpose
- Modify: path -- what changes
- Tests: path

## Steps
### Step 1: <small outcome>
- Change: <exact files and functions; key code or signatures when non-obvious>
- Test: <test to add or update first, and what it asserts>
- Check: <exact command> -> expect <result>
- Commit: <message>

## Verification
<full commands to run at the end, and manual checks if any>

## Risks and open questions
- <risk> -- <mitigation or who decides>
```

## Writing good steps

- Each step is small (a few minutes to an hour) and leaves the tree working: tests pass after every step.
- Order by dependency; put the riskiest unknown early so it fails fast.
- Prefer test-first: write the failing test, run it, implement, run it again.
- Be exact: real paths, real function names, real commands. "Update the handler" is not a step; "In `api/users.py`, make `create_user` return 409 when the email exists" is.
- Include expected output for each check so the executor can tell pass from fail.
- Name what not to change when a nearby refactor is tempting.
- Mark any step that is destructive or touches shared state (migrations, deletes, deploys) as needing user confirmation.

## Before handing over

- Re-read the plan as the fresh agent: can every step be done without asking a question? Fix any that cannot.
- Check every design requirement maps to at least one step.
- Keep it as short as completeness allows.

Tell the user where the plan is saved and offer to execute it (see implement-plan).

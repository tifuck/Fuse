---
name: handoff
description: Write a handoff note so a new session or person can continue: goal, state, decisions and why, what was verified, open problems, exact next steps, key paths. Use when pausing, switching sessions or running out of context.
metadata:
  category: Agent
  inspired-by: mattpocock/skills
---
# Handoff

Write the note you would want if you arrived cold with no memory of this session. The reader has the repo but none of the conversation.

## Gather facts, not memories

Before writing, check the real state instead of recalling it:

- `git status`, `git branch --show-current`, `git log --oneline -10`, and `git diff --stat` for uncommitted work.
- Last test, build or lint result: rerun the narrowest relevant command if the last result is stale or unknown.
- Any running processes, temp files, branches or environments you created.

## Template

Save to the place the user names, or `HANDOFF.md` / `docs/handoff/<date>-<topic>.md`; ask before committing it. Keep it under about one screen of text where possible.

```
# Handoff: <topic> (<date>)

## Goal
<What we are trying to achieve and why; link the issue, PRD or plan.>

## State
- Branch: <name> at <sha>; uncommitted: <yes/no, what>
- Done: <completed items>
- In progress: <the item mid-flight and how far it got>

## Decisions
- <decision> -- <why; alternatives rejected>

## Verified
- <command> -> <result> (<when>)
- Not verified: <what and why>

## Open problems
- <problem> -- <evidence, error text, theory>

## Next steps
1. <exact action: file, function, command>
2. ...

## Key files
- path -- role

## Gotchas
- <environment quirks, flaky tests, things that look wrong but are intentional>
```

## Rules

- Be specific: paths, function names, commands, error messages quoted exactly. "Fix the bug in auth" is useless; "`test_refresh_token` fails with 401 because `expires_at` is compared in local time in `auth/tokens.py:88`" is a handoff.
- Separate what was verified from what is believed. Never mark something working without saying how it was checked.
- Record dead ends briefly so the next person does not retry them.
- Include every user preference or constraint stated during the session that the next agent must respect.
- Never include secrets, tokens or credentials; refer to where they live instead.
- Next steps are ordered and the first one is doable immediately.

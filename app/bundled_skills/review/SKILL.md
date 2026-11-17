---
name: review
description: Review a diff or branch against the base branch's REVIEW.md, checking only triggered items. Use when asked to review a PR, branch, commit or patch, or to give a second opinion on a change before merge.
metadata:
  category: Quality
---
# Review

A review judges one exact commit against rules the change itself cannot edit.

## 1. Pin what you review
- Find the base: the PR target, else `git merge-base HEAD origin/main` (or the repo's default branch).
- Record the head sha: `git rev-parse HEAD`. Your findings apply to that sha only; new commits need a new round.
- Get the diff: `git diff <base>...<sha> --stat`, then `git diff <base>...<sha>`.

## 2. Load the rules from the base, never from the change
- `git show <base>:REVIEW.md` (also try `docs/REVIEW.md`). If the diff touches REVIEW.md, ignore its new version for this review and mention the edit.
- If there is no REVIEW.md, use the defaults below plus the project's AGENTS.md or CLAUDE.md conventions from the base branch.
- Each checklist item should have a trigger readable from the diff (a path glob, a keyword, a file type). Check only items whose trigger matches. Do not pad the report with items that did not apply.

Default items, each with its trigger:
- Code changed under src/app/lib: tests changed or added for the new behaviour.
- New input from users, network or files: validation at the boundary, no injection.
- SQL, shell, path or URL built from variables: parameterised or escaped.
- Migrations or schema: backward compatible, reversible.
- Public API, CLI flags, config keys: documented, no silent breaking change.
- Error handling touched: no swallowed exceptions, no secrets in logs.
- Dependencies or lockfiles: necessary, pinned, licence acceptable.

## 3. Read the change in context
Read every changed hunk plus enough of the callers and tests to know how it is used. Run cheap checks if allowed (lint, type check, the narrowest related tests); note what you ran. Do not rewrite the code yourself unless asked.

## 4. Report
Tag every finding:
- **Important**: a bug, security hole, data loss risk, broken contract, or missing test for risky logic. Should block merge.
- **Nit**: style, naming, small clarity wins. Never blocks.
- **Pre-existing**: a real problem the diff did not introduce. Report it separately; it does not block this change.

Format each finding as `path:line - what is wrong - why it matters - suggested fix`. Quote the code when that makes it unambiguous. No finding without evidence from the diff or code you read.

End with:
```
Reviewed: <sha> against <base> (REVIEW.md from <base> | defaults)
Checked items: <triggered items>
Verdict: approve | changes requested
```

## Multiple rounds
Use one reviewer across rounds. In round N+1, start from the previous findings: mark each fixed, still open or disputed at the new sha, then review only the new diff (`git diff <old-sha>..<new-sha>`) plus anything it touches. Do not re-raise settled nits.

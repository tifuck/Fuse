---
name: release-notes
description: Write a changelog or release notes from git history between two refs, grouped by user-visible change, with breaking changes and migrations called out and internal noise skipped. Use when preparing a release, tag or CHANGELOG entry.
metadata:
  category: Ops
---
# Release Notes

Turn commits into notes a user of the software can act on: what changed for them, what they must do, what broke.

## 1. Pick the range

- If the user gave refs, use them. Otherwise take the latest tag to HEAD: `git describe --tags --abbrev=0` then `<tag>..HEAD`. Confirm the range when it is ambiguous.
- Check the existing CHANGELOG or previous release notes for format, headings and tone, and match them.

## 2. Collect

```
git log --no-merges --pretty='%h %s%n%b' <from>..<to>
git log --merges --pretty='%h %s' <from>..<to>
git diff --stat <from>..<to>
```

- PR numbers in merge commits or squash subjects point to richer descriptions; read them (for example `gh pr view <n>`) when a subject is unclear.
- For conventional commits, `feat`, `fix`, `perf` and `!` / `BREAKING CHANGE:` are strong signals, but read the diff when a subject is vague.
- Look at diffs for user-facing surfaces: CLI flags, config keys, environment variables, API routes and schemas, database migrations, default values, dependency minimum versions.

## 3. Filter and group

Drop internal noise: refactors, test-only changes, CI, formatting, dependency bumps without user effect, reverted pairs. Keep security fixes and notable dependency upgrades.

Group by user impact, in this order, omitting empty groups:

1. **Breaking changes**: what changed, who is affected, exact migration steps.
2. **Security**
3. **New features**
4. **Improvements**
5. **Bug fixes**
6. **Deprecations**: what, replacement, removal timeline if known.

Merge several commits for the same change into one entry.

## 4. Write entries

- One line each, starting with the user-visible effect in plain language: "Exports now include archived projects" not "refactor export query".
- Reference the PR or commit (`(#123)` or short sha) as the project does.
- Migrations get a code block with the exact command or config change.
- Credit contributors only if the project does.
- Do not invent impact. If unsure whether something is user-visible, list it under a "Needs review" note for the user rather than guessing.

## 5. Deliver

Show the notes in chat. Write to CHANGELOG.md (prepending a new version section) only if asked. Never create tags, push, or publish a release without explicit permission.

Final check: every breaking change has migration steps, and nothing in the notes contradicts the diff.

---
name: dependency-upgrade
description: Upgrade a library, framework or runtime safely, one dependency at a time, with changelog review and tests. Use when bumping versions, fixing a vulnerable package, handling deprecation warnings, or migrating to a new major release.
metadata:
  category: Build
---
# Dependency upgrade

One dependency per step, read what changed, run the tests, keep the lockfile honest.

## 1. Inventory
- Identify the package manager and lockfile (requirements*.txt / poetry.lock / uv.lock, package-lock.json / pnpm-lock.yaml / yarn.lock, Cargo.lock, go.sum, Gemfile.lock).
- See current and available versions: `pip list --outdated`, `npm outdated`, `pnpm outdated`, `cargo outdated` (if installed), `go list -m -u all`.
- Check why a package is present before upgrading it: `npm ls <pkg>`, `pipdeptree -r -p <pkg>`, `cargo tree -i <pkg>`, `go mod why <module>`.
- Start from a green baseline: run the tests before changing anything and note pre-existing failures.

## 2. Read the changes
For the range from current to target version:
- Read the CHANGELOG, release notes and any migration guide. Note breaking changes, removed APIs, changed defaults, new minimum runtime versions.
- For majors, upgrade through the latest minor of the current major first; deprecation warnings there usually point at what breaks next.
- Search the codebase for every API the notes mention (`grep` for function names, config keys, imports).

## 3. Upgrade one at a time
- Change only the target package (and peers it strictly requires). Use the manager's command so the lockfile updates consistently: `npm install pkg@x.y.z`, `pnpm up pkg@x.y.z`, `poetry add pkg@^x.y`, `uv add pkg==x.y.z`, `cargo update -p pkg --precise x.y.z`, `go get module@vX.Y.Z && go mod tidy`.
- Do not regenerate the whole lockfile or run a blanket upgrade-all unless the user asked.
- Respect the existing pinning style (exact, caret, range).

## 4. Fix and test
- Run lint, type check and tests. Fix breakages at the call sites, following the migration guide.
- Run with deprecation warnings visible (`python -W error::DeprecationWarning` for a check run, `node --trace-deprecation`) and fix the ones from the upgraded package.
- Run the app end to end for the paths that use the package.
- If the upgrade cannot be completed cleanly, stop and report rather than pinning around the problem with hacks.

## 5. Review the lockfile diff
- `git diff --stat` on the lockfile: expect the target package and its new transitive deps. Investigate unrelated version jumps.
- Check new transitive packages for licence and maintenance concerns.
- Run the security audit if available (`npm audit`, `pip-audit`, `cargo audit`, `govulncheck`).

## 6. Commit
One commit per upgraded dependency, message like `deps: bump foo 2.3.1 -> 3.0.2` with a short note on code changes and how it was verified. Do not commit or push unless asked.

## Report
Package, from and to versions, breaking changes handled, lockfile changes, tests and flows run, anything deferred.

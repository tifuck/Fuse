---
name: github-actions
description: Write or fix GitHub Actions workflows with minimal permissions, pinned actions, caching, matrices, safe secrets handling and fast checks first. Use when adding CI, debugging a failing workflow, or hardening or speeding up .github/workflows.
metadata:
  category: Ops
---
# GitHub Actions

Read existing workflows in `.github/workflows/` and the project's real build, lint and test commands (manifests, Makefile, README) before writing YAML. CI should run the same commands developers run locally.

## Structure
- Triggers: `pull_request` and `push` to the default branch; add `workflow_dispatch` for manual runs. Use `paths` filters only when they cannot skip required checks.
- Cancel superseded runs:
  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: true
  ```
- Fast, cheap checks first (lint, typecheck, unit tests); slow jobs (integration, e2e, builds) after them with `needs:` or in parallel when runners are cheap.
- Set `timeout-minutes` on every job.

## Permissions
- Default to read-only at the top level:
  ```yaml
  permissions:
    contents: read
  ```
- Grant more per job only where needed (`pull-requests: write`, `id-token: write` for OIDC, `packages: write`).
- Prefer OIDC to cloud providers over long-lived keys.

## Pin actions
- Pin third-party actions to a full commit SHA with the version in a comment: `uses: owner/action@<40-char-sha> # v4.2.0`. Look up the SHA from the action's release tag; do not invent one. Official `actions/*` at least to a major tag, SHA preferred.
- Let Dependabot or Renovate keep pins updated (`.github/dependabot.yml` with `package-ecosystem: github-actions`).

## Caching and setup
- Use the setup actions' built-in caching: `actions/setup-node` with `cache: npm`, `actions/setup-python` with `cache: pip`, `actions/setup-go` caches by default.
- `actions/cache` keyed on lockfile hashes for anything else: `key: ${{ runner.os }}-x-${{ hashFiles('**/lock.file') }}`.
- Install with lockfiles (`npm ci`, `pip install -r`, `uv sync --frozen`).

## Matrix
- Use a matrix only for versions or OSes you actually support. Set `fail-fast: false` when you want all results.
- Use `include`/`exclude` rather than duplicating jobs.

## Secrets and untrusted input
- Reference secrets only in the steps that need them, via `env:`. Never echo them; GitHub masks known secrets but not derived values.
- Workflows on `pull_request` from forks get no secrets; do not switch to `pull_request_target` to get them unless the job never checks out or runs PR code.
- Never interpolate untrusted context (`github.event.pull_request.title`, branch names, issue bodies) directly into `run:`; pass it through `env:` and quote it to avoid script injection.
- Set `persist-credentials: false` on `actions/checkout` when later steps do not push.

## Debugging a failing run
1. Read the failing step's log (`gh run view <id> --log-failed` if the CLI is available).
2. Reproduce the command locally with the same versions.
3. Check differences: runner OS, missing env vars or secrets, shallow clone (`fetch-depth`), caches restoring stale state, permissions.
4. Fix the cause; do not add `continue-on-error` or skip tests to go green.

## Verify
Lint YAML with `actionlint` if available. Push or open a PR only when the user asks; otherwise show the diff and say the workflow has not run yet.

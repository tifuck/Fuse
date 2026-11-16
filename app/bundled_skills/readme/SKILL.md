---
name: readme
description: Write or update a README or developer docs with what it is, a quick start that actually runs, configuration, common tasks and troubleshooting. Use when asked to write, fix or improve a README, setup guide, CONTRIBUTING or project docs.
metadata:
  category: Docs
---
# README and developer docs

Docs are only as good as their commands. Base every statement on the code, not on guesses.

## 1. Gather facts from the repo
- Manifests and tooling: `package.json` scripts, `pyproject.toml`, `Makefile`, `Cargo.toml`, `go.mod`, `Dockerfile`, compose files, CI workflows (CI shows the real install and test commands).
- Entry points, CLI flags (`--help` output), environment variables (grep for `os.environ`, `process.env`, `getenv`, `.env.example`), default ports and paths.
- Required runtimes and versions (`.nvmrc`, `.python-version`, `engines`, `requires-python`).
- Existing docs and their tone; update rather than duplicate.
- License file, if any.

## 2. Structure
Use only the sections the project needs, in roughly this order:
1. Name and one-sentence description: what it is and who it is for.
2. Status or badges only if they are maintained.
3. Quick start: prerequisites, install, run, and how to see it working, in under a minute of reading.
4. Usage: the most common tasks with copy-pasteable examples and expected output.
5. Configuration: a table or list of env vars and options with defaults and whether they are required.
6. Development: run tests, lint, build; project layout if non-obvious.
7. Deployment, if relevant.
8. Troubleshooting: real errors users hit and the fix.
9. Contributing and license.

## 3. Writing rules
- Lead with what the reader wants to do; no marketing copy or history.
- Commands in fenced code blocks with a language tag, one command per line, no `$` prompts so they paste cleanly.
- Show placeholders clearly (`<your-api-key>`) and never include real secrets, tokens or internal hostnames.
- State OS-specific differences explicitly.
- Keep it short; move long reference material into `docs/` and link to it with relative links.
- Prefer present tense and second person ("Run", "You can").
- Do not document features that do not exist yet; mark planned items clearly or leave them out.

## 4. Verify
- Run the quick-start commands in a clean shell or fresh clone when feasible (a temp directory is fine). Do not run commands that deploy, publish, delete data or need production credentials; mark those as unverified instead.
- Check every referenced file, script, flag and env var exists (`grep` for each).
- Check relative links resolve and that code samples match the current API.
- Ensure Markdown renders: heading levels in order, closed code fences, tables aligned.

## Report
Summarise sections added or changed, which commands you ran successfully, and which you could not verify and why.

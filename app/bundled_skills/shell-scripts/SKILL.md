---
name: shell-scripts
description: Write robust, safe bash scripts with strict mode, correct quoting, traps, safe deletes and shellcheck-clean code. Use when writing or fixing a shell script, CI step, install or deploy script, or a cron job.
metadata:
  category: Build
---
# Shell scripts

Bash scripts fail silently and destructively by default. Make failure loud and deletes safe.

## Header
```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
```
- `-e` exits on error, `-u` on unset variables, `pipefail` makes a pipeline fail if any stage fails.
- Know the gaps: `-e` is ignored inside `if`, `&&`/`||` chains and functions called from them; check important commands explicitly.
- Use `/bin/sh` only if POSIX portability is required, and then avoid bashisms.

## Quoting and variables
- Quote every expansion: `"$var"`, `"${arr[@]}"`, `"$(cmd)"`. Unquoted variables split on spaces and expand globs.
- Use `${var:-default}` for optional values and `${var:?message}` for required ones.
- Use arrays for argument lists, not strings: `args=(--flag "$value"); cmd "${args[@]}"`.
- `local` for function variables; UPPER_CASE only for exported or environment variables.
- Use `$(...)` not backticks; `[[ ... ]]` for tests in bash.
- `printf '%s\n' "$x"` instead of `echo` for arbitrary data.

## Safe deletes and destructive steps
- Never `rm -rf "$dir"` on a variable you did not validate. Guard it: `[[ -n "${dir:-}" && "$dir" != "/" && -d "$dir" ]] || exit 1`, and prefer `rm -rf -- "${dir:?}/"*` style that aborts when empty.
- Create temp space with `mktemp -d` and clean it with a trap, rather than fixed paths in /tmp.
- Destructive or irreversible actions (deleting data, force pushes, dropping databases) should require an explicit flag or confirmation, and a `--dry-run` mode helps. When running such a script on a user's machine, ask first.

## Traps and cleanup
```bash
tmp=$(mktemp -d)
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT
trap 'echo "error on line $LINENO" >&2' ERR
```

## Idempotency
- Safe to rerun: `mkdir -p`, `ln -sfn`, check before append (`grep -qxF "$line" file || echo "$line" >> file`), `command -v tool >/dev/null` before installing.
- Write files atomically: write to a temp file in the same directory, then `mv`.

## Input and output
- Parse arguments with a `while`/`case` loop or `getopts`; print `usage` on bad input and exit 2.
- Errors to stderr (`>&2`) with a clear message; exit non-zero.
- Read files line by line with `while IFS= read -r line; do ...; done < "$file"`.
- Use `find ... -print0 | xargs -0` or `find -exec ... {} +` for filenames with spaces.
- Do not parse `ls` output.

## Secrets
Do not echo secrets, avoid `set -x` around them, and pass them via environment or files with restricted permissions, not command-line arguments visible in `ps`.

## Check
- Run `shellcheck script.sh` and fix warnings rather than disabling them; disable a specific code only with a comment saying why.
- `bash -n script.sh` for a syntax check.
- Run it once in a scratch directory or with `--dry-run` before running it for real.

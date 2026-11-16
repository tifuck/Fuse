#!/bin/bash
# Fuse test runner (see tests/README.md).
#
#   scripts/test.sh                 core suite (tests/core, parallel) + tests/js
#   scripts/test.sh runs tools      only these areas (tests/core/<area>)
#   scripts/test.sh changed [ref]   areas whose sources differ from ref (default dev),
#                                   committed or not, mapped by tests/areas.txt
#   scripts/test.sh legacy [path..] the frozen pre-redesign suite; never required
#
# Extra pytest arguments go after --. JOBS=N sets parallel workers (0 = off).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=${PYTHON:-/root/fuse/venv/bin/python}
export FUSE_CHAT_TEST_MODE=1
[ -d /dev/shm ] && export TMPDIR=${TMPDIR:-/dev/shm}

args=() extra=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; extra=("$@"); break; fi
  args+=("$1"); shift
done

pytest() { "$PYTHON" -m pytest -q -p no:cacheprovider -o timeout_func_only=true -W "ignore:The 'reconnect' argument:DeprecationWarning" "$@"; }
parallel() { local jobs=${JOBS:-$1}; [ "$jobs" != 0 ] && echo "-n $jobs" || true; }

areas() {  # stdin: changed paths -> stdout: unique existing areas
  "$PYTHON" -c '
import fnmatch, os, sys
rules = [line.split() for line in open("tests/areas.txt") if line.strip() and not line.startswith("#")]
found = []
for path in sys.stdin.read().split():
    for glob, area in rules:
        if fnmatch.fnmatch(path, glob):
            area = path.split("/")[2] if area == "@dir" else area
            if area not in found and os.path.isdir(f"tests/core/{area}"):
                found.append(area)
            break
print(" ".join(found))'
}

case "${args[0]:-}" in
  legacy)
    export FUSE_LEGACY_TESTS=1
    targets=("${args[@]:1}")
    [ ${#targets[@]} -eq 0 ] && targets=(tests/legacy)
    pytest --timeout="${TEST_TIMEOUT:-300}" "${targets[@]}" "${extra[@]}"
    ;;
  changed)
    ref=${args[1]:-dev}
    picked=$( { git diff --name-only "$ref"...HEAD; git status --porcelain --untracked-files=all | cut -c4-; } | areas)
    if [ -z "$picked" ]; then echo "no core test area covers the changed files"; exit 0; fi
    echo "areas: $picked"
    exec "$0" $picked -- "${extra[@]}"
    ;;
  "")
    # shellcheck disable=SC2046
    pytest --timeout="${TEST_TIMEOUT:-30}" $(parallel auto) tests/core "${extra[@]}"
    if compgen -G "tests/js/*.test.cjs" > /dev/null; then node --test tests/js/*.test.cjs; fi
    ;;
  *)
    dirs=()
    for area in "${args[@]}"; do
      [ -d "tests/core/$area" ] || { echo "no such area: tests/core/$area" >&2; exit 2; }
      dirs+=("tests/core/$area")
    done
    # shellcheck disable=SC2046
    pytest --timeout="${TEST_TIMEOUT:-30}" $(parallel 0) "${dirs[@]}" "${extra[@]}"
    ;;
esac

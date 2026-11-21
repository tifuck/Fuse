#!/bin/bash
# Fuse test runner (see tests/README.md).
#
#   scripts/test.sh                 core suite (tests/core, parallel) + tests/js
#   scripts/test.sh runs tools      only these areas (tests/core/<area>; js = tests/js)
#   scripts/test.sh changed [ref]   areas whose sources differ from ref (default dev),
#                                   committed or not, mapped by tests/areas.txt
#
# Extra pytest arguments go after --. JOBS=N sets parallel workers (0 = off).
#
# Everything runs inside the systemd sandbox of scripts/test_sandbox.sh (as
# the unprivileged user fuse-test, everything read-only but the unit's own
# run dir); the tests themselves refuse to run anywhere else
# (tests/safety.py). Options that would skip the test guards are refused.
set -euo pipefail
cd "$(dirname "$0")/.."
# Writable paths are the sandbox's business, never the caller's.
unset FUSE_TEST_RW FUSE_TEST_LOG_DIR LOG_DIR PYTEST_PLUGINS
PYTHON=${PYTHON:-/root/fuse-dev/venv/bin/python}
export PYTHON FUSE_CHAT_TEST_MODE=1

given=("$@") args=() extra=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; extra=("$@"); break; fi
  args+=("$1"); shift
done

refuse() { echo "scripts/test.sh: $1 is not allowed (it would get around the test guards; tests/README.md)" >&2; exit 2; }

check_pytest_args() {  # what a caller may not hand to pytest
  local arg
  for arg in "$@"; do
    case "$arg" in
      --noconftest|--confcutdir|--confcutdir=*|--tx|--tx=*|--rsyncdir|--rsyncdir=*|--rootdir|--rootdir=*\
      |--config-file|--config-file=*|--inifile|--inifile=*|--import-mode|--import-mode=*|--pyargs\
      |--override-ini|--override-ini=*|--basetemp|--basetemp=*|--plugins|--plugins=*)
        refuse "$arg" ;;
      --*) ;;
      -*)  # short options, alone or clustered: -p (plugins), -c (config), -o (ini overrides)
        flags=${arg%%=*}
        if [[ "${flags:1}" == *[pco]* ]]; then refuse "$arg"; fi ;;
      *)
        if [[ "$arg" == /* || "$arg" == *..* ]]; then refuse "path $arg"; fi ;;
    esac
  done
  return 0
}

js_tests() {
  if compgen -G "tests/js/*.test.cjs" > /dev/null; then
    if command -v node > /dev/null; then node --test tests/js/*.test.cjs
    else echo "WARNING: node is not installed: tests/js was NOT run" >&2; fi
  fi
}

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
            if area not in found and (area == "js" or os.path.isdir(f"tests/core/{area}")):
                found.append(area)
            break
print(" ".join(found))'
}

check_pytest_args "${extra[@]}"
read -ra addopts <<< "${PYTEST_ADDOPTS:-}"
check_pytest_args "${addopts[@]}"
for arg in "${args[@]:1}"; do
  case "${args[0]:-}" in
    changed) [[ "$arg" =~ ^[A-Za-z0-9_./-]+$ ]] || refuse "ref $arg" ;;
  esac
done
case "${args[0]:-}" in
  changed|"") ;;
  *) for area in "${args[@]}"; do [[ "$area" =~ ^[a-z0-9_]+$ ]] || refuse "area $area"; done ;;
esac

if [ "${FUSE_TEST_SANDBOX:-}" != "systemd-run" ]; then
  # Outside the sandbox: only pick the areas (git reads the repository's
  # .git, which the sandbox keeps read-only), then rerun this script inside.
  if [ "${args[0]:-}" = changed ]; then
    ref=${args[1]:-dev}
    picked=$( { git diff --name-only "$ref"...HEAD; git status --porcelain --untracked-files=all | cut -c4-; } | areas)
    if [ -z "$picked" ]; then echo "no core test area covers the changed files"; exit 0; fi
    echo "areas: $picked"
    # shellcheck disable=SC2086
    given=($picked -- "${extra[@]}")
  fi
  # The tree is read-only inside: create what the tests expect to exist.
  mkdir -p -- data/desktop-releases
  exec scripts/test_sandbox.sh scripts/test.sh "${given[@]}"
fi

case "${args[0]:-}" in
  changed)
    echo "changed is resolved before entering the sandbox" >&2; exit 2
    ;;
  "")
    # shellcheck disable=SC2046
    pytest --timeout="${TEST_TIMEOUT:-30}" $(parallel auto) tests/core "${extra[@]}"
    js_tests
    ;;
  *)
    dirs=() js=0
    for area in "${args[@]}"; do
      if [ "$area" = js ]; then js=1; continue; fi
      [ -d "tests/core/$area" ] || { echo "no such area: tests/core/$area" >&2; exit 2; }
      dirs+=("tests/core/$area")
    done
    # shellcheck disable=SC2046
    if [ ${#dirs[@]} -gt 0 ]; then pytest --timeout="${TEST_TIMEOUT:-30}" $(parallel 0) "${dirs[@]}" "${extra[@]}"; fi
    if [ "$js" = 1 ]; then js_tests; fi
    ;;
esac

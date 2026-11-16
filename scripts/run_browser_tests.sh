#!/bin/bash
# RETIRED with the legacy suite (tests/legacy/README.md); kept for on-demand runs.
# Run the Playwright browser suites (tests/legacy/*.browser.cjs), each against its own
# fresh isolated fixture server (tests/legacy/chat_browser_fixture.py: disposable
# MariaDB schema, mocked model, loopback port 8099). Never touches fuse-dev.
#
#   NODE_PATH=/path/to/node_modules scripts/run_browser_tests.sh [tests/legacy/x.browser.cjs ...]
#
# Needs the Node "playwright" package (npm install playwright, or on
# NODE_PATH) and its Chromium (npx playwright install chromium).
# Logs go to a fresh /tmp/fuse-browser-tests/run.XXXXXXXX (printed first).
# Runs only inside the test sandbox (scripts/test_sandbox.sh): started
# outside it, it creates that log directory (the only writable path the
# sandbox grants it) and reruns itself there; the fixture server refuses to
# start anywhere else and may not start processes (tests/safety.py).
# FUSE_CHAT_VIEW=combined runs the suites under the Combined chat theme (the
# fixture's login sets the fuse_chat_view cookie Appearance › Layout writes);
# unset, they run under Console, the default.
set -u
cd "$(dirname "$0")/.."
PORT=${FUSE_BROWSER_PORT:-8099}
if [ "${FUSE_TEST_SANDBOX:-}" != "systemd-run" ]; then
  unset FUSE_TEST_RW LOG_DIR
  base=/tmp/fuse-browser-tests
  if [ -L "$base" ] || { [ -e "$base" ] && [ ! -d "$base" ]; }; then echo "$base must be a directory" >&2; exit 2; fi
  mkdir -p -m 0755 "$base"
  FUSE_TEST_LOG_DIR=$(mktemp -d "$base/run.XXXXXXXX")
  chown fuse-test:fuse-test "$FUSE_TEST_LOG_DIR"
  echo "logs: $FUSE_TEST_LOG_DIR"
  export FUSE_TEST_LOG_DIR
  exec scripts/test_sandbox.sh scripts/run_browser_tests.sh "$@"
fi
LOG_DIR=${FUSE_TEST_LOG_DIR:?the sandbox hands the runner its log directory}
export FUSE_BROWSER_ORIGIN="http://127.0.0.1:$PORT" FUSE_BROWSER_PORT="$PORT"
files=("$@")
[ ${#files[@]} -eq 0 ] && files=(tests/legacy/*.browser.cjs)
failed=0
for f in "${files[@]}"; do
  name=$(basename "$f")
  FUSE_CHAT_TEST_MODE=1 "$PYTHON" tests/legacy/chat_browser_fixture.py > "$LOG_DIR/fixture-$name.log" 2>&1 &
  server=$!
  for _ in $(seq 1 60); do curl -s -o /dev/null "$FUSE_BROWSER_ORIGIN/static/app.css" && break; sleep 0.5; done
  if timeout 900 node "$f" > "$LOG_DIR/$name.log" 2>&1; then echo "PASS $f"; else echo "FAIL $f (see $LOG_DIR/$name.log)"; failed=1; fi
  kill "$server" 2>/dev/null; wait "$server" 2>/dev/null
done
exit $failed

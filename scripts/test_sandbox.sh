#!/bin/bash
# Run one test command inside a throwaway systemd sandbox (tests/README.md).
#
#   scripts/test_sandbox.sh <command> [args...]
#
# Used by scripts/test.sh and scripts/run_browser_tests.sh; tests refuse to
# run anywhere else (tests/safety.py). Inside the unit:
#   - the whole file system is read-only (ProtectSystem=strict, home too),
#     except this tree, a fresh per-run temp dir (TMPDIR and HOME) and the
#     paths in FUSE_TEST_RW (space-separated, for logs); inside the tree,
#     .git, .env, config.env and venv stay read-only;
#   - SSH keys and host config are not even readable;
#   - no device nodes, no new privileges, no setuid, no capability to
#     reboot, mount, load modules, use raw I/O, trace or create devices, and
#     the matching system calls are filtered out;
#   - the MariaDB socket and loopback TCP still work (tests use the database).
# The runner itself starts nothing else: every test process is a child of
# the unit and dies with it.
set -euo pipefail
tree=$(cd "$(dirname "$0")/.." && pwd -P)
[ $# -ge 1 ] || { echo "usage: $0 <command> [args...]" >&2; exit 2; }
command -v systemd-run >/dev/null || { echo "systemd-run is required to run tests" >&2; exit 2; }
[ "$tree" != / ] && [ -f "$tree/tests/safety.py" ] || { echo "refusing: $tree is not a Fuse tree" >&2; exit 2; }

run_dir=$(mktemp -d /tmp/fuse-test.XXXXXXXX)
cleanup() {
  # Only ever the directory mktemp made above.
  case "$run_dir" in /tmp/fuse-test.????????) rm -rf -- "$run_dir" ;; esac
}
trap cleanup EXIT

env_args=(-E "FUSE_TEST_SANDBOX=systemd-run" -E "TMPDIR=$run_dir" -E "HOME=$run_dir"
          -E "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" -E "LANG=${LANG:-C.UTF-8}")
# Settings the runners and tests read; nothing else of the caller's
# environment crosses into the unit.
while IFS='=' read -r name _; do
  case "$name" in
    FUSE_TEST_SANDBOX|FUSE_TEST_RW) ;;
    FUSE_*|JOBS|TEST_TIMEOUT|PYTHON|NODE_PATH|LOG_DIR|TERM|NO_COLOR|PYTEST_ADDOPTS) env_args+=(-E "$name=${!name}") ;;
  esac
done < <(env)

rw=("$tree" "$run_dir")
for path in ${FUSE_TEST_RW:-}; do rw+=("$path"); done

status=0
systemd-run --wait --pipe --collect --quiet --service-type=exec \
  --unit="fuse-test-$(basename "$run_dir" | tr -cd 'a-zA-Z0-9')" \
  -p WorkingDirectory="$tree" \
  -p ProtectSystem=strict \
  -p ProtectHome=read-only \
  -p ReadWritePaths="${rw[*]}" \
  -p "ReadOnlyPaths=-$tree/.git -$tree/.env -$tree/config.env -$tree/venv" \
  -p "InaccessiblePaths=-/root/.ssh -/etc/ssh -/home" \
  -p PrivateDevices=yes \
  -p NoNewPrivileges=yes \
  -p RestrictSUIDSGID=yes \
  -p "CapabilityBoundingSet=~CAP_SYS_ADMIN CAP_SYS_BOOT CAP_SYS_MODULE CAP_MKNOD CAP_SYS_RAWIO CAP_SYS_PTRACE CAP_SYS_TIME CAP_NET_ADMIN CAP_SYS_CHROOT CAP_LINUX_IMMUTABLE" \
  -p ProtectKernelTunables=yes \
  -p ProtectKernelModules=yes \
  -p ProtectKernelLogs=yes \
  -p ProtectControlGroups=yes \
  -p ProtectClock=yes \
  -p LockPersonality=yes \
  -p "SystemCallFilter=~@reboot @swap @module @raw-io @mount @clock @debug @obsolete @cpu-emulation" \
  -p SystemCallErrorNumber=EPERM \
  -p RuntimeMaxSec=3600 \
  "${env_args[@]}" \
  -- "$@" || status=$?
exit "$status"

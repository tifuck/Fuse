#!/bin/bash
# Run scripts/test.sh inside a throwaway systemd sandbox (tests/README.md).
# Nothing else runs in it, and tests refuse to run anywhere else
# (tests/safety.py).
#
#   scripts/test_sandbox.sh scripts/test.sh [args...]
#
# Run as root (it starts the unit). Inside the unit:
#   - the process is the unprivileged system user fuse-test, never root;
#   - the whole file system is read-only, the tree under test included (bound
#     read-only at /run/<unit>/tree, the venv at /run/<unit>/venv); /tmp and
#     every home directory (/root, /home) are empty read-only tmpfs mounts;
#   - the only writable place is the unit's own run directory /run/<unit>
#     (TMPDIR, HOME, PYTHONPYCACHEPREFIX), removed when the unit ends;
#   - .env/config.env are not readable: the run gets the test database
#     account and a test-only APP_SECRET from a root-only credential file
#     (FUSE_TEST_CREDENTIALS, default /root/.config/fuse-test/test.env);
#   - no device nodes, no new privileges, no setuid, only unix and IP sockets,
#     no capability or system call to reboot, mount, load modules, use raw
#     I/O or trace; D-Bus, the systemd manager socket, SSH configuration and
#     the MariaDB maintenance credentials are inaccessible.
set -euo pipefail
tree=$(cd "$(dirname "$0")/.." && pwd -P)
self="scripts/$(basename "$0")"

# -- inside the unit: load the test credentials, then run the script ----------
if [ "${1:-}" = --inside ]; then
  shift
  [ "${FUSE_TEST_SANDBOX:-}" = systemd-run ] && [ -n "${CREDENTIALS_DIRECTORY:-}" ] || { echo "--inside is internal" >&2; exit 2; }
  while IFS='=' read -r name value; do
    case "$name" in
      DATABASE_URL|FUSE_FIXTURE_DB_USER|FUSE_FIXTURE_DB_PASSWORD|APP_SECRET) export "$name=$value" ;;
    esac
  done < "$CREDENTIALS_DIRECTORY/fuse-test.env"
  script=$1; shift
  exec /bin/bash "$script" "$@"
fi

# -- outside: start the unit ----------------------------------------------------
die() { echo "test sandbox: $*" >&2; exit 2; }
[ "$(id -u)" = 0 ] || die "run as root (it starts a systemd unit that drops to fuse-test)"
command -v systemd-run >/dev/null || die "systemd-run is required to run tests"
[ "$tree" != / ] && [ -f "$tree/tests/safety.py" ] || die "$tree is not a Fuse tree"
id fuse-test >/dev/null 2>&1 || die "the system user fuse-test is missing (useradd --system --user-group --no-create-home --shell /usr/sbin/nologin fuse-test)"
case "${1:-}" in
  scripts/test.sh) ;;
  *) die "usage: $self scripts/test.sh [args...]" ;;
esac
credentials=${FUSE_TEST_CREDENTIALS:-/root/.config/fuse-test/test.env}
[ -f "$credentials" ] && [ ! -L "$credentials" ] && [ "$(stat -c '%u %a' "$credentials")" = "0 600" ] \
  || die "$credentials must be a root-owned 0600 file with the test database account (see tests/mariadb_fixture.py)"

python=${PYTHON:-/root/fuse-dev/venv/bin/python}
venv=$(cd "$(dirname "$python")/.." && pwd -P)
[ -f "$venv/pyvenv.cfg" ] || die "PYTHON=$python is not a virtualenv interpreter"

unit="fuse-test-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
run="/run/$unit"

binds_ro=("$tree:$run/tree" "$venv:$run/venv")
hidden=(-/run/dbus -/run/systemd/private -/etc/mysql/debian.cnf -/root/.ssh -/etc/ssh -/home)
# .env and config.env: symlinks into /root vanish with the home tmpfs; a real
# file in the tree is covered with /dev/null; any other target is made inaccessible.
for name in .env config.env; do
  path="$tree/$name"
  if [ -L "$path" ]; then
    target=$(readlink -f "$path" || true)
    case "$target" in /root/*|"") ;; *) hidden+=("-$target") ;; esac
  elif [ -e "$path" ]; then
    binds_ro+=("/dev/null:$run/tree/$name")
  fi
done

env_args=(-E "FUSE_TEST_SANDBOX=systemd-run" -E "FUSE_TEST_RUN_DIR=$run" -E "TMPDIR=$run" -E "HOME=$run"
          -E "PYTHONPYCACHEPREFIX=$run/pycache" -E "PYTHON=$run/venv/bin/$(basename "$python")"
          -E "PATH=/usr/local/bin:/usr/bin:/bin" -E "LANG=${LANG:-C.UTF-8}")
# Settings the runners and tests read; nothing else of the caller's
# environment (credentials, database URLs, writable paths) crosses into the unit.
for name in JOBS TEST_TIMEOUT PYTEST_ADDOPTS TERM NO_COLOR FUSE_CHAT_TEST_MODE; do
  [ -n "${!name+x}" ] && env_args+=(-E "$name=${!name}")
done

props=(
  -p User=fuse-test -p Group=fuse-test
  -p RuntimeDirectory="$unit" -p RuntimeDirectoryMode=0700
  -p WorkingDirectory="$run/tree"
  -p ProtectSystem=strict
  -p ProtectHome=tmpfs
  -p "TemporaryFileSystem=/tmp:ro /root:ro /home:ro /run/user:ro /run/lock:ro /dev/shm:ro"
  -p "BindReadOnlyPaths=${binds_ro[*]}"
  -p "InaccessiblePaths=${hidden[*]}"
  -p LoadCredential="fuse-test.env:$credentials"
  -p PrivateDevices=yes
  -p NoNewPrivileges=yes
  -p RestrictSUIDSGID=yes
  -p PrivateIPC=yes
  -p "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"
  -p "CapabilityBoundingSet=~CAP_SYS_ADMIN CAP_SYS_BOOT CAP_SYS_MODULE CAP_MKNOD CAP_SYS_RAWIO CAP_SYS_PTRACE CAP_SYS_TIME CAP_NET_ADMIN CAP_SYS_CHROOT CAP_LINUX_IMMUTABLE"
  -p ProtectKernelTunables=yes
  -p ProtectKernelModules=yes
  -p ProtectKernelLogs=yes
  -p ProtectControlGroups=yes
  -p ProtectClock=yes
  -p LockPersonality=yes
  -p "SystemCallFilter=~@reboot @swap @module @raw-io @mount @clock @debug @obsolete @cpu-emulation"
  -p SystemCallErrorNumber=EPERM
  -p RuntimeMaxSec=3600
)

status=0
systemd-run --wait --pipe --collect --quiet --service-type=exec --unit="$unit" "${props[@]}" "${env_args[@]}" \
  -- /bin/bash "$self" --inside "$@" || status=$?
exit "$status"

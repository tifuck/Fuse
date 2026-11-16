# Tests

`scripts/test.sh` is the only entry point.

| Command | Runs |
|---|---|
| `scripts/test.sh` | the whole core suite (`tests/core`, parallel) and `tests/js` |
| `scripts/test.sh runs tools` | only those areas (`tests/core/<area>`) |
| `scripts/test.sh changed [ref]` | the areas whose sources differ from `ref` (default `dev`), committed or not |
| `scripts/test.sh legacy [path…]` | the frozen old suite (`tests/legacy`); never required |

Extra pytest arguments go after `--`; `JOBS=N` sets parallel workers (`0` = off).

## Safety: tests never execute anything real

On 2026-11-16 a test passed `rm -rf /*` to a fake host that ran it in a real
bash as root, a mutant had disabled the denylist, and the machine was wiped.
Since then:

- **Sandbox only.** `scripts/test.sh` (and `scripts/run_browser_tests.sh`)
  run inside `scripts/test_sandbox.sh`: a systemd unit running as the
  unprivileged system user `fuse-test`, with the whole file system read-only
  (the tree under test included; `/tmp` and the home directories are empty)
  except the unit's own run dir (TMPDIR, HOME, bytecode cache), no devices,
  no new privileges, no capability or system call to reboot, mount or load
  modules. Run it as root; it drops to `fuse-test`. `tests/conftest.py`,
  `tests/core/__init__.py` and `tests/legacy/__init__.py` stop any run
  outside it (`python -m pytest` refuses, `--noconftest` too), and
  `scripts/test.sh` refuses options that would skip them (`--noconftest`,
  `--confcutdir`, `-p`, `-c`, `-o`, `--tx`, `--rsyncdir`, …), in its
  arguments and in `PYTEST_ADDOPTS`.
- **Own database account, no real secrets.** Tests use `fuse_test` (ALL on
  `fuse_fixture_%`, SELECT on `fuse_dev`), never the app's account; its
  credentials and a test-only `APP_SECRET` come from the root-only file
  `/root/.config/fuse-test/test.env` (provisioning: `tests/mariadb_fixture.py`).
  `.env` and `config.env` are unreadable in the sandbox. The fixture refuses
  any statement naming `fuse_dev`/`fuse_live` that is not a plain read.
- **No processes.** `tests/safety.py` makes every way to start a process
  (os, posix, subprocess, pty, asyncio) raise in test processes, and every
  signal to a process the test did not start. The only exceptions are the
  two app workers listed there (image decoder, PDF extractor): exact argv,
  never a shell; and, in the xdist controller only, execnet's exact worker
  bootstrap.
- **Data in the run dir.** App files under `ROOT/data` are redirected to the
  run's temp dir for the whole session (`tests/conftest.py`).
- **Fakes record, never run.** Host transports in tests are recording fakes
  that return scripted output; fake file systems refuse any path outside
  their own sandbox directory, whatever the app code under test decided.
- **No destructive strings near an executor.** Never pass a destructive
  command (`rm -rf`, `mkfs`, `dd`, `chmod -R`, `shutdown`, ...) to anything
  that can execute. Test the denylist as a pure function
  (`workspace.denied`) plus one end-to-end check through the recording
  fake (which executes nothing) that it was never asked to run them.
- **Safety code is not a casual mutation target.** The denylist,
  path confinement and the permission gate may be mutation-tested only with
  recording fakes, inside the sandbox, never with a real executor.
- Do not patch, relax or mock `tests/safety.py` or the sandbox in a test.

## What gets a test

- **Backend logic only**: runs and recovery, persistence, sync, tools and
  permissions, usage and pricing, pool routing, providers, auth. One small test
  per behaviour or bug fix, in its area folder.
- **No frontend tests for features or views.** Themes and views change too
  often; check UI changes with a screenshot (`scripts/dev_qa_shot.py`). Pure
  frontend logic with no DOM (formatters, parsers, state) may get a Node unit
  test in `tests/js/`.
- Never add to `tests/legacy`, and never fix a legacy test: if one is in your
  way, delete it.

## How core tests are written

- Ask for the `fresh_db` fixture to touch the database (`fresh_db_fk` for a
  schema with the real foreign keys, to test cascades and ON DELETE rules). It is a MariaDB schema
  with the dev schema's structure (`fuse_fixture_core_*`, never `fuse_dev`
  itself), kept between runs and emptied before each test in milliseconds.
  SQLite is refused.
- Use the ids the code returns; never assume `id=1` (counters are not reset).
- No fixed sleeps and no wall-clock assertions: wait on a condition with
  `tests.core.helpers.wait_until` / `wait_until_async`, and assert what the code
  did, not how long it took.
- Mock the network (httpx) and providers; never call pooled models or search.
- Test through public functions, not private seams, so refactors do not break
  tests.
- Budget: an area runs in a few seconds. A test over 1 s needs a reason.

## Helpers and samples

`tests/core/helpers.py`:

- `make_user()`: a real account row, returns its id.
- `fake_provider(*script)`: patches the provider boundary (`app.api.invoke_chat`)
  for a block; each call answers with the next scripted frames list
  (`text_frames("…")`, `tool_call_frames([...])`), callable, or exception. The
  real generation loop, tool rounds, persistence and run supervisor still run.
  `provider.bodies` holds every request the loop sent.
- `run_turn(uid, text, thread=None)`: starts a real durable root turn and
  waits for it; `run_async(coro)` runs an async body and drains the run
  supervisor afterwards.
- `wait_until` / `wait_until_async`: wait on a condition, never a sleep.
- `quick_user()`: a bare users row, no scrypt (~80 ms saved); `make_user()` only
  when the test signs in or needs the vault.
- App SQL goes through a SQLite-to-MariaDB translator; raw MariaDB SQL in a
  test (`information_schema`, `DATABASE()`) must use `conn.native(sql, params)`
  inside `db.tx()`, with `%s` placeholders.

Samples to copy: `tests/core/runs/test_turn_lifecycle.py` (async run end to
end), `tests/core/threads/test_revisions.py` (database state and conflicts),
`tests/core/usage/test_costs.py` (exact money, rollup invariants).

Area helpers worth reusing before writing your own (import them from the area):

- `tests/core/providers/_support.py`: `Upstream` / `upstream` fixture, an httpx
  MockTransport that records requests and fails on unrouted calls.
- `tests/core/subscriptions/_support.py`: SSE frame readers (`drain`, `text_of`,
  `tool_calls_of`, `usage_of`, …).
- `tests/core/agents/_support.py`: `RoutedProvider` (a script per run, for
  parent + child or wake turns at once), `start_root`, `coding_user`.
- `tests/core/threads/test_lock_order.py` (`_race`, `_other_waiting`) and
  `tests/core/features/_support.py` (`waiting_on`): pause a real transaction
  and wait on a PROCESSLIST condition instead of sleeping.
- `tests/core/tools/_support.py`: `FakeFS` (in-memory files), `FakeShell`
  (records commands, answers with scripted output, executes nothing),
  `FakeConn`/`FakeSFTP` (an SSH host on those two), `FakeConnector`,
  `ScriptedRunner`, `CardUser`.
- `tests/core/auth/_support.py`: signed session cookies without scrypt
  (`sign_in`), `build_app()` (routes without the lifespan).

## Adding an area

Create `tests/core/<area>/` with an empty `__init__.py`, and map the source
paths it covers in `tests/areas.txt` so `scripts/test.sh changed` finds it.

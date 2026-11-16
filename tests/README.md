# Tests

`scripts/test.sh` is the only entry point.

| Command | Runs |
|---|---|
| `scripts/test.sh` | the whole core suite (`tests/core`, parallel) and `tests/js` |
| `scripts/test.sh runs tools` | only those areas (`tests/core/<area>`) |
| `scripts/test.sh changed [ref]` | the areas whose sources differ from `ref` (default `dev`), committed or not |
| `scripts/test.sh legacy [path…]` | the frozen old suite (`tests/legacy`); never required |

Extra pytest arguments go after `--`; `JOBS=N` sets parallel workers (`0` = off).

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
- `tests/core/tools/_support.py`: `LocalConn`/`LocalSFTP` (a fake SSH host on
  local files), `FakeConnector`, `ScriptedRunner`, `CardUser`.
- `tests/core/auth/_support.py`: signed session cookies without scrypt
  (`sign_in`), `build_app()` (routes without the lifespan).

## Adding an area

Create `tests/core/<area>/` with an empty `__init__.py`, and map the source
paths it covers in `tests/areas.txt` so `scripts/test.sh changed` finds it.

-- Indexes, column types and counters from the 2026-11-24 database
-- performance audit.
--
-- chat_runs_thread_children (thread_id) becomes (thread_id, status(16)):
--   thread-scoped active-run reads (status IN ('queued','running')) run
--   inside write transactions as FOR UPDATE; on the global
--   chat_runs_status_created index their next-key locks covered every
--   user's active runs. Online (LOCK=NONE): chat_runs is the largest table.
-- chat_schedules next_fire_at/firing_at LONGTEXT -> VARCHAR(32) with full
--   keys: the 5 s scheduler tick filesorted every future schedule and
--   full-scanned for firing_at. chat_schedules_firing is added first on a
--   32-character prefix, which becomes the full key once firing_at is
--   VARCHAR(32), so the index exists before the code swap.
-- usage_rollup_hourly_source (source_type, hour): the admin all-users usage
--   view filtered by provider scanned the whole rollup table.
-- credentials.fuse_api_equiv_micros/fuse_priced_events/fuse_unpriced_events:
--   lifetime counters /sources summed from usage_events on every page load.
--   They start at 0 and record_usage keeps them from then on. This file does
--   not fill them: an UPDATE from usage_events here would overwrite them
--   from a snapshot and lose every event a live request records meanwhile.
--   Fill them once, after this file and before starting the code that
--   maintains them, with the service stopped:
--     python -m scripts.rebuild_usage_rollups --apply
--   from the tree whose schema it is (it refuses while another connection
--   uses the schema).
--
-- Left out: chat_threads (updated_at type, its sort key, a schedule key) and
-- command_jobs (finished_at type, its poll key). Every change to those large
-- tables copies them whole, and the production updater refuses that.
--
-- Index changes keep their names, so the runtime index probes still match.
-- Additive statements come first, then every statement that drops or
-- modifies: an updater holds those back until the new code runs and refuses
-- an additive statement after them (docs/development.md, Schema changes).
-- A redefined index is dropped in one ALTER and re-added in the next, so it
-- lands last and SHOW CREATE TABLE matches schema.sql whatever the table's
-- history; the re-adding ALTER repeats the DROP ... IF EXISTS (a no-op by
-- then) so it counts as destructive and runs after the drop.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-24-db-perf.sql
--   mysql fuse_live < scripts/sql/2026-11-24-db-perf.sql
-- Safe to rerun.
ALTER TABLE usage_rollup_hourly
  ADD INDEX IF NOT EXISTS usage_rollup_hourly_source (source_type, hour);

ALTER TABLE credentials
  ADD COLUMN IF NOT EXISTS fuse_api_equiv_micros BIGINT(20) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS fuse_priced_events BIGINT(20) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS fuse_unpriced_events BIGINT(20) NOT NULL DEFAULT 0;

ALTER TABLE chat_schedules ADD INDEX IF NOT EXISTS chat_schedules_firing (firing_at(32));

ALTER TABLE chat_runs DROP INDEX IF EXISTS chat_runs_thread_children;
ALTER TABLE chat_runs
  DROP INDEX IF EXISTS chat_runs_thread_children,
  ADD INDEX chat_runs_thread_children (thread_id, status(16));

ALTER TABLE chat_schedules
  MODIFY next_fire_at VARCHAR(32) DEFAULT NULL,
  MODIFY firing_at VARCHAR(32) DEFAULT NULL,
  DROP INDEX IF EXISTS chat_schedules_due;
ALTER TABLE chat_schedules
  DROP INDEX IF EXISTS chat_schedules_due,
  ADD INDEX chat_schedules_due (enabled, paused, next_fire_at);

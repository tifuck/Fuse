-- Indexes, column types and counters from the 2026-11-24 database
-- performance audit.
--
-- chat_runs_thread_children (thread_id) becomes (thread_id, status(16)):
--   thread-scoped active-run reads (status IN ('queued','running')) run
--   inside write transactions as FOR UPDATE; on the global
--   chat_runs_status_created index their next-key locks covered every
--   user's active runs.
-- chat_threads.updated_at LONGTEXT -> VARCHAR(32), and
--   chat_threads_user_updated becomes a full (user_id, archived, updated_at,
--   id) key: a 191-char prefix cannot serve ORDER BY updated_at, so every
--   sidebar, sync and search query filesorted the user's threads.
-- chat_threads_schedule (user_id, schedule_id): deleting a schedule cleared
--   schedule_id with a full scan that row-locked every user's threads.
-- chat_schedules next_fire_at/firing_at LONGTEXT -> VARCHAR(32) with full
--   keys: the 5 s scheduler tick filesorted every future schedule and
--   full-scanned for firing_at.
-- command_jobs.finished_at LONGTEXT -> VARCHAR(32), and command_jobs_thread
--   (thread_id) becomes (thread_id, background, finished_at) for the 3 s
--   jobs poll.
-- usage_rollup_hourly_source (source_type, hour): the admin all-users usage
--   view filtered by provider scanned the whole rollup table.
-- credentials.fuse_api_equiv_micros/fuse_priced_events/fuse_unpriced_events:
--   lifetime counters /sources summed from usage_events on every page load.
--   Backfilled below. Run this file once more after the code that maintains
--   them is live, so events recorded in between are counted.
--
-- Index changes keep their names, so the runtime index probes still match.
-- Each is dropped and re-added in its own ALTER so it always lands last and
-- SHOW CREATE TABLE matches schema.sql whatever the table's history.
-- The MODIFY statements copy chat_threads, chat_schedules and command_jobs:
-- writes to them wait while it runs.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-24-db-perf.sql
--   mysql fuse_live < scripts/sql/2026-11-24-db-perf.sql
-- Safe to rerun.
ALTER TABLE chat_runs DROP INDEX IF EXISTS chat_runs_thread_children;
ALTER TABLE chat_runs ADD INDEX chat_runs_thread_children (thread_id, status(16));

ALTER TABLE chat_threads
  MODIFY updated_at VARCHAR(32) NOT NULL,
  DROP INDEX IF EXISTS chat_threads_user_updated,
  ADD INDEX IF NOT EXISTS chat_threads_schedule (user_id, schedule_id);
ALTER TABLE chat_threads ADD INDEX chat_threads_user_updated (user_id, archived, updated_at, id);

ALTER TABLE chat_schedules
  MODIFY next_fire_at VARCHAR(32) DEFAULT NULL,
  MODIFY firing_at VARCHAR(32) DEFAULT NULL,
  DROP INDEX IF EXISTS chat_schedules_due,
  ADD INDEX IF NOT EXISTS chat_schedules_firing (firing_at);
ALTER TABLE chat_schedules ADD INDEX chat_schedules_due (enabled, paused, next_fire_at);

ALTER TABLE command_jobs
  MODIFY finished_at VARCHAR(32) DEFAULT NULL,
  DROP INDEX IF EXISTS command_jobs_thread;
ALTER TABLE command_jobs ADD INDEX command_jobs_thread (thread_id, background, finished_at);

ALTER TABLE usage_rollup_hourly
  ADD INDEX IF NOT EXISTS usage_rollup_hourly_source (source_type, hour);

ALTER TABLE credentials
  ADD COLUMN IF NOT EXISTS fuse_api_equiv_micros BIGINT(20) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS fuse_priced_events BIGINT(20) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS fuse_unpriced_events BIGINT(20) NOT NULL DEFAULT 0;

UPDATE credentials c
  LEFT JOIN (
    SELECT credential_id,
           COALESCE(SUM(estimated_cost_micros), 0) AS api_equiv,
           COALESCE(SUM(CASE WHEN total_tokens > 0 AND pricing_known=1 THEN 1 ELSE 0 END), 0) AS priced,
           COALESCE(SUM(CASE WHEN total_tokens > 0 AND pricing_known=0 THEN 1 ELSE 0 END), 0) AS unpriced
    FROM usage_events WHERE credential_id IS NOT NULL GROUP BY credential_id
  ) s ON s.credential_id = c.id
SET c.fuse_api_equiv_micros = COALESCE(s.api_equiv, 0),
    c.fuse_priced_events = COALESCE(s.priced, 0),
    c.fuse_unpriced_events = COALESCE(s.unpriced, 0);

-- Indexes and digest columns for the run lifecycle's periodic sweeps and hot
-- checkpoint reads (resume_queued, reap_orphans, sweep_wakes, wake
-- candidates, save_turn_progress, command job wakes, retention, schedule
-- reconcile), which scanned whole tables or hashed whole blobs every time.
--
-- chat_runs gets STORED generated columns, all INVISIBLE so SELECT * keeps
-- its shape:
--   message_digest        md5(message): the 0.2 s checkpoint read compares it
--                         instead of hashing the message each time.
--   provider_base_digest, provider_base_chars, provider_tail_digest:
--                         save_turn_progress compares the transcript it saves
--                         with the stored one without hashing the virtual
--                         provider_messages concat (megabytes per tool round).
--   wake_pending          0 none; 1 a terminal background child whose report
--                         still owes a wake (sweep_wakes' predicate); 2 still
--                         undelivered but out of wake budget or stopped by
--                         the user (wake_candidates still lists it).
-- Generated, so every writer keeps them exact; request stays the source of
-- truth. Additive. The ALTER rebuilds chat_runs (stored generated columns
-- need a table copy): expect writes to wait for it on a large table.
--
-- Apply: mysql fuse_dev < scripts/sql/2026-11-20-run-sweeps.sql
-- Safe to rerun, also after a run that stopped halfway.
ALTER TABLE chat_runs
  ADD COLUMN IF NOT EXISTS message_digest CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
    GENERATED ALWAYS AS (md5(message)) STORED INVISIBLE,
  ADD COLUMN IF NOT EXISTS provider_base_digest CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
    GENERATED ALWAYS AS (md5(provider_base)) STORED INVISIBLE,
  ADD COLUMN IF NOT EXISTS provider_base_chars BIGINT
    GENERATED ALWAYS AS (char_length(provider_base)) STORED INVISIBLE,
  ADD COLUMN IF NOT EXISTS provider_tail_digest CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
    GENERATED ALWAYS AS (md5(provider_tail)) STORED INVISIBLE,
  ADD COLUMN IF NOT EXISTS wake_pending TINYINT GENERATED ALWAYS AS (
    CASE WHEN parent_run_id IS NULL
              OR status NOT IN ('completed','failed','cancelled','interrupted')
              OR NOT COALESCE(json_extract(request, '$.agent.background') = 1, 0)
              OR COALESCE(json_extract(request, '$.agent.wake_sent') = 1, 0) THEN 0
         WHEN COALESCE(json_extract(request, '$.agent.wake_attempts'), 0) < 2
              AND NOT (status = 'cancelled' AND cancel_requested = 2) THEN 1
         ELSE 2 END
  ) STORED INVISIBLE;

CREATE INDEX IF NOT EXISTS chat_runs_wake ON chat_runs (wake_pending, user_id, thread_id);
-- resume_queued / reap_orphans / live-run counts filter on status alone.
CREATE INDEX IF NOT EXISTS chat_runs_status_created ON chat_runs (status(16), created_at(20));

-- pending_wake_threads every 60 s; retention's per-user busy check; history prune.
CREATE INDEX IF NOT EXISTS command_jobs_undelivered ON command_jobs (notify, delivered, thread_id);
CREATE INDEX IF NOT EXISTS command_jobs_user_thread ON command_jobs (user_id, thread_id);
CREATE INDEX IF NOT EXISTS command_jobs_finished ON command_jobs (finished_at(20));

-- schedules.reconcile scans live fires; retention prunes on finished_at.
CREATE INDEX IF NOT EXISTS schedule_fires_status ON schedule_fires (status(16));
CREATE INDEX IF NOT EXISTS schedule_fires_finished ON schedule_fires (finished_at(20));

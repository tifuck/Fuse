-- Title sweep (app/chat_titles.py pending_title_threads, every 60 s) filters
-- chat_threads on title_state='' and orders by title_attempted_at: without an
-- index it read every thread (transcripts included) and filesorted them.
-- title_state holds short states ('', user, generated, fallback, ...) and
-- title_attempted_at an ISO timestamp (20 chars). Additive.
-- Apply as root:
--   mysql fuse_dev < scripts/sql/2026-11-20-thread-sweeps.sql
CREATE INDEX IF NOT EXISTS chat_threads_title_sweep ON chat_threads (title_state(8), title_attempted_at(20));

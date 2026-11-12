-- Thread lists check each thread for live background jobs
-- (chat_threads._THREAD_RUNNING_SQL). Without an index on thread_id every
-- row scanned all of command_jobs: ~3 s for a few hundred threads at 40k jobs.
CREATE INDEX IF NOT EXISTS command_jobs_thread ON command_jobs (thread_id);

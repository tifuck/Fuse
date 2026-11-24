-- Foreign keys for rows that outlived their owner (2026-11-24 data audit).
--
-- command_jobs.user_id / thread_id: deleting a user left their commands and
--   captured output (only age pruning removed them).
-- chat_permission_grants.host_id, skill_host_approvals.host_id: deleting a
--   host left its grants and skill approvals.
-- pool_sessions.credential_id: deleting a credential left its session pins.
-- All ON DELETE CASCADE. command_jobs.host_id gets none: agent and local jobs
-- store 0 there.
--
-- Rows whose owner is already gone are deleted first, so each constraint
-- validates; nothing reads them. Safe to rerun.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-24-owner-cascades.sql
--   mysql fuse_live < scripts/sql/2026-11-24-owner-cascades.sql
DELETE j FROM command_jobs j LEFT JOIN users u ON u.id = j.user_id WHERE u.id IS NULL;
DELETE j FROM command_jobs j LEFT JOIN chat_threads t ON t.id = j.thread_id
  WHERE j.thread_id IS NOT NULL AND t.id IS NULL;
DELETE g FROM chat_permission_grants g LEFT JOIN user_hosts h ON h.id = g.host_id WHERE h.id IS NULL;
DELETE a FROM skill_host_approvals a LEFT JOIN user_hosts h ON h.id = a.host_id WHERE h.id IS NULL;
DELETE p FROM pool_sessions p LEFT JOIN credentials c ON c.id = p.credential_id WHERE c.id IS NULL;

-- The thread column gets an index of its own: the db-perf migration of the
-- same day drops and re-adds command_jobs_thread, which a constraint relying
-- on it would refuse (1553).
ALTER TABLE command_jobs ADD INDEX IF NOT EXISTS fk_command_jobs_thread (thread_id);
ALTER TABLE command_jobs
  ADD CONSTRAINT fk_command_jobs_user FOREIGN KEY IF NOT EXISTS (user_id) REFERENCES users (id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_command_jobs_thread FOREIGN KEY IF NOT EXISTS (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE;
-- Re-added last, as 2026-11-24-db-perf.sql does, so SHOW CREATE TABLE
-- matches schema.sql whichever file ran last.
ALTER TABLE command_jobs DROP INDEX IF EXISTS command_jobs_thread;
ALTER TABLE command_jobs ADD INDEX command_jobs_thread (thread_id, background, finished_at);
ALTER TABLE chat_permission_grants
  ADD CONSTRAINT fk_chat_permission_grants_host FOREIGN KEY IF NOT EXISTS (host_id) REFERENCES user_hosts (id) ON DELETE CASCADE;
ALTER TABLE skill_host_approvals
  ADD CONSTRAINT skill_host_approvals_host FOREIGN KEY IF NOT EXISTS (host_id) REFERENCES user_hosts (id) ON DELETE CASCADE;
ALTER TABLE pool_sessions
  ADD CONSTRAINT pool_sessions_credential FOREIGN KEY IF NOT EXISTS (credential_id) REFERENCES credentials (id) ON DELETE CASCADE;

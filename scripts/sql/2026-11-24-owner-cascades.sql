-- Foreign keys for rows that outlived their owner (2026-11-24 data audit).
--
-- chat_permission_grants.host_id, skill_host_approvals.host_id: deleting a
--   host left its grants and skill approvals.
-- pool_sessions.credential_id: deleting a credential left its session pins.
-- All ON DELETE CASCADE.
-- command_jobs gets no foreign key: adding one copies the whole table, which
-- the production updater refuses for a large table. Account delete
-- (accounts.delete_user) and thread delete (chat_threads.forget_thread_rows)
-- remove its rows instead; the retention sweep (retention.prune_history)
-- deletes the ones left so far in small batches, not this file: a scan of
-- the whole table under row locks while production serves.
--
-- Rows of the three small tables whose owner is already gone are deleted
-- first, so each constraint validates; nothing reads them. Safe to rerun.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-24-owner-cascades.sql
--   mysql fuse_live < scripts/sql/2026-11-24-owner-cascades.sql
DELETE g FROM chat_permission_grants g LEFT JOIN user_hosts h ON h.id = g.host_id WHERE h.id IS NULL;
DELETE a FROM skill_host_approvals a LEFT JOIN user_hosts h ON h.id = a.host_id WHERE h.id IS NULL;
DELETE p FROM pool_sessions p LEFT JOIN credentials c ON c.id = p.credential_id WHERE c.id IS NULL;

ALTER TABLE chat_permission_grants
  ADD CONSTRAINT fk_chat_permission_grants_host FOREIGN KEY IF NOT EXISTS (host_id) REFERENCES user_hosts (id) ON DELETE CASCADE;
ALTER TABLE skill_host_approvals
  ADD CONSTRAINT skill_host_approvals_host FOREIGN KEY IF NOT EXISTS (host_id) REFERENCES user_hosts (id) ON DELETE CASCADE;
ALTER TABLE pool_sessions
  ADD CONSTRAINT pool_sessions_credential FOREIGN KEY IF NOT EXISTS (credential_id) REFERENCES credentials (id) ON DELETE CASCADE;

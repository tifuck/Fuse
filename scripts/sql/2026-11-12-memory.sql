-- Memory (app/memory.py): typed notes a user's chats save and recall across
-- chats. Opt-in per account (users.chat_memory, off by default) with a
-- per-chat off switch (chat_threads.memory_off). project_id NULL is a global
-- memory; scope_key (project id, 0 for global) is written by the app so the
-- name is unique per scope, which a nullable column alone cannot enforce.
-- Deleting a project deletes its memories; deleting the chat a memory came
-- from only clears the link. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-12-memory.sql
--   mysql fuse_live < scripts/sql/2026-11-12-memory.sql
CREATE TABLE IF NOT EXISTS memories (
  id BIGINT(20) NOT NULL AUTO_INCREMENT,
  user_id BIGINT(20) NOT NULL,
  project_id BIGINT(20) DEFAULT NULL,
  scope_key BIGINT(20) NOT NULL DEFAULT 0,
  name VARCHAR(64) NOT NULL,
  type VARCHAR(16) NOT NULL,
  description VARCHAR(200) NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  thread_id BIGINT(20) DEFAULT NULL,
  created_at VARCHAR(32) NOT NULL,
  updated_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY memories_name (user_id, scope_key, name),
  KEY memories_project (project_id),
  KEY memories_thread (thread_id),
  CONSTRAINT memories_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT memories_project FOREIGN KEY (project_id) REFERENCES user_projects (id) ON DELETE CASCADE,
  CONSTRAINT memories_thread FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS chat_memory BIGINT(20) NOT NULL DEFAULT 0;
ALTER TABLE chat_threads
  ADD COLUMN IF NOT EXISTS memory_off BIGINT(20) NOT NULL DEFAULT 0;

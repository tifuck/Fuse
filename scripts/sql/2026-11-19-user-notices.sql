-- Persistent notices (app/notices.py): a message for one user that stays on
-- screen on every device until it is dismissed on any of them (a secret
-- swapped in a chat, a provider usage limit). One row per user and key;
-- thread_id scopes it to one chat. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-19-user-notices.sql
--   mysql fuse_live < scripts/sql/2026-11-19-user-notices.sql
CREATE TABLE IF NOT EXISTS user_notices (
  id BIGINT(20) NOT NULL AUTO_INCREMENT,
  user_id BIGINT(20) NOT NULL,
  thread_id BIGINT(20) DEFAULT NULL,
  notice_key VARCHAR(191) NOT NULL,
  kind VARCHAR(8) NOT NULL DEFAULT 'idle',
  title VARCHAR(200) NOT NULL DEFAULT '',
  body LONGTEXT NOT NULL,
  data LONGTEXT NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  updated_at VARCHAR(32) NOT NULL,
  dismissed_at VARCHAR(32) DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY user_notices_key (user_id, notice_key),
  KEY user_notices_thread (thread_id),
  CONSTRAINT user_notices_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT user_notices_thread FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

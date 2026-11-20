-- Media retention (app/retention.py purge_idle_media): when each thread's
-- images and attached files were last deleted, so the 10-minute sweep skips it
-- until it is used again. A table of its own: adding a column to chat_threads
-- copies the whole table on MariaDB. Additive and online.
-- Apply as root:
--   mysql fuse_dev < scripts/sql/2026-11-20-media-retention.sql
CREATE TABLE IF NOT EXISTS chat_media_purges (
  thread_id bigint(20) NOT NULL,
  purged_at varchar(32) NOT NULL,
  PRIMARY KEY (thread_id),
  CONSTRAINT chat_media_purges_thread FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

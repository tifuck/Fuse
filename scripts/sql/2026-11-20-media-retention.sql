-- Media retention (app/retention.py purge_idle_media): when a thread's images
-- and attached files were last deleted, so the 10-minute sweep skips it until
-- it is used again. Additive; INSTANT on MariaDB 10.3+.
-- Apply as root:
--   mysql fuse_dev < scripts/sql/2026-11-20-media-retention.sql
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS media_purged_at varchar(32) NOT NULL DEFAULT '';

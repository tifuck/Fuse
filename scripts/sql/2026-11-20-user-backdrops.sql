-- Custom backdrops (app/backdrops.py): per-user HTML documents the chat draws
-- as a wallpaper in a sandboxed iframe (GET /backdrop/<public_id>). At most
-- 8 per user, each at most 128 KB; rows never change once written. They go
-- with their account (ON DELETE CASCADE). Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-20-user-backdrops.sql
--   mysql fuse_live < scripts/sql/2026-11-20-user-backdrops.sql
CREATE TABLE IF NOT EXISTS user_backdrops (
  id BIGINT(20) NOT NULL AUTO_INCREMENT,
  public_id CHAR(12) NOT NULL,
  user_id BIGINT(20) NOT NULL,
  name VARCHAR(40) NOT NULL,
  scheme VARCHAR(8) NOT NULL DEFAULT 'dark',
  html MEDIUMTEXT NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY user_backdrops_public_id (public_id),
  KEY user_backdrops_user (user_id),
  CONSTRAINT user_backdrops_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

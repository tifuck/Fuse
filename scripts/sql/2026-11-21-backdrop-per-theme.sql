-- One custom backdrop per chat theme (app/backdrops.py): saving one for a
-- theme replaces that theme's previous one, so saves no longer pile up.
-- Rows saved before this have view NULL; the next save for any theme
-- deletes them. Additive and idempotent.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-21-backdrop-per-theme.sql
--   mysql fuse_live < scripts/sql/2026-11-21-backdrop-per-theme.sql
ALTER TABLE user_backdrops ADD COLUMN IF NOT EXISTS view VARCHAR(16) DEFAULT NULL AFTER user_id;
CREATE UNIQUE INDEX IF NOT EXISTS user_backdrops_user_view ON user_backdrops (user_id, view);

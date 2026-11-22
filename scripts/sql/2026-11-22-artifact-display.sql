-- Inline artifacts (app/artifacts.py): display says where the chat shows an
-- artifact: 'panel' (a card that opens the side pane, as before) or 'inline'
-- (drawn in full inside the reply). Existing artifacts stay panels. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-22-artifact-display.sql
--   mysql fuse_live < scripts/sql/2026-11-22-artifact-display.sql
ALTER TABLE artifacts
  ADD COLUMN IF NOT EXISTS display VARCHAR(16) NOT NULL DEFAULT 'panel';

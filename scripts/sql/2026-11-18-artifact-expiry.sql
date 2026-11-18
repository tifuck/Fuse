-- Artifact expiry (app/artifacts.py expire_idle): viewed_at is the last time
-- anyone opened an artifact (in Fuse or by its share link); expired_at marks
-- a stub whose pages were dropped after ARTIFACT_MAX_IDLE_DAYS without a view
-- or change. Existing artifacts count as viewed now, so they get the full
-- window before the first sweep. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-18-artifact-expiry.sql
--   mysql fuse_live < scripts/sql/2026-11-18-artifact-expiry.sql
ALTER TABLE artifacts
  ADD COLUMN IF NOT EXISTS viewed_at VARCHAR(32) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS expired_at VARCHAR(32) NOT NULL DEFAULT '';
UPDATE artifacts SET viewed_at = DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ') WHERE viewed_at = '';

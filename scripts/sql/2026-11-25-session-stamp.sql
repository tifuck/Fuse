-- Security stamp (app/accounts.py password_fingerprint): sessions are bound to
-- users.session_stamp when it is set, else to a hash of password_hash as
-- before, so existing sessions keep working with no backfill. A transparent
-- rehash at sign-in stores the old fingerprint here (sessions survive it); a
-- real password change or reset stores a new random stamp (they end).
-- Additive, idempotent. No index (users has a UNIQUE ... USING HASH key). Not
-- online: that key's hidden column makes MariaDB copy users for any ADD
-- COLUMN (error 1846 with LOCK=NONE or ALGORITHM=INSTANT), as for every earlier
-- users column; users is far below the updater's 64 MB limit.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-25-session-stamp.sql
--   mysql fuse_live < scripts/sql/2026-11-25-session-stamp.sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS session_stamp VARCHAR(32) DEFAULT NULL;

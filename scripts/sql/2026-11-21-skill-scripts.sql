-- Skills with script files (app/skill_sync.py). A skill whose folder holds
-- .py/.js/.mjs/.cjs/.ts/.sh files is copied to a machine before it runs, and
-- only after the user allows that machine and that version:
--   user_skills.approved_hash  the content_hash of a third-party (GitHub,
--                              skills.sh) skill's files the user reviewed;
--                              such a skill with scripts stays off until it
--                              matches.
--   skill_host_approvals       one row per (user, machine, skill, files hash)
--                              the user allowed from the permission prompt.
-- Additive. Apply as root, fuse_dev first, then fuse_live as its own step
-- (before promoting the code that reads it). Safe to rerun:
--   mysql fuse_dev  < scripts/sql/2026-11-21-skill-scripts.sql
--   mysql fuse_live < scripts/sql/2026-11-21-skill-scripts.sql
ALTER TABLE user_skills ADD COLUMN IF NOT EXISTS approved_hash VARCHAR(64) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS skill_host_approvals (
  user_id BIGINT(20) NOT NULL,
  host_id BIGINT(20) NOT NULL,
  slug VARCHAR(64) NOT NULL,
  files_hash CHAR(64) NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (user_id, host_id, slug, files_hash),
  CONSTRAINT skill_host_approvals_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

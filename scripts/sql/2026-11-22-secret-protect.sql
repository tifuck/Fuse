-- Secret protection (app/secret_guard.py): a user's protected values, one
-- row each, kept encrypted with APP_SECRET and looked up by HMAC; the
-- stand-in a model sees is stored beside it in clear (it is public by
-- design). users.secret_protect switches masking, users.secret_offer the
-- detection of candidates in env files; both default on, and nothing is
-- replaced until the user accepts a value.
-- Also drops what the removed secret swap left behind (its stored pairs
-- included): secret_thread_uses, secret_tokens, users.chat_secret_swap,
-- chat_threads.secret_swap. Idempotent.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-22-secret-protect.sql
--   mysql fuse_live < scripts/sql/2026-11-22-secret-protect.sql
DROP TABLE IF EXISTS secret_thread_uses;
DROP TABLE IF EXISTS secret_tokens;
ALTER TABLE users DROP COLUMN IF EXISTS chat_secret_swap;
ALTER TABLE chat_threads DROP COLUMN IF EXISTS secret_swap;
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS secret_protect BIGINT(20) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS secret_offer BIGINT(20) NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS user_secrets (
  id BIGINT(20) NOT NULL AUTO_INCREMENT,
  user_id BIGINT(20) NOT NULL,
  label VARCHAR(190) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  origin VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL,
  strong BIGINT(20) NOT NULL DEFAULT 0,
  prefix_len BIGINT(20) NOT NULL DEFAULT 0,
  length BIGINT(20) NOT NULL DEFAULT 0,
  real_hmac CHAR(64) NOT NULL,
  fake TEXT NOT NULL,
  cipher TEXT NOT NULL,
  sources TEXT NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  updated_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY user_secrets_real (user_id, real_hmac),
  CONSTRAINT user_secrets_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

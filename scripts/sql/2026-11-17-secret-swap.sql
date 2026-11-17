-- Secret swap (app/secret_swap.py): models see realistic stand-ins for a
-- user's secrets. Opt-in per account (users.chat_secret_swap, off by
-- default); a chat started while it is on swaps for life
-- (chat_threads.secret_swap). secret_tokens holds one stand-in per secret
-- per user, the pair encrypted with APP_SECRET and looked up by HMAC;
-- secret_thread_uses records per chat whether the model sees a value fake
-- or (seen before it was known) clear. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-17-secret-swap.sql
--   mysql fuse_live < scripts/sql/2026-11-17-secret-swap.sql
CREATE TABLE IF NOT EXISTS secret_tokens (
  id BIGINT(20) NOT NULL AUTO_INCREMENT,
  user_id BIGINT(20) NOT NULL,
  real_hmac CHAR(64) NOT NULL,
  fake_hmac CHAR(64) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  strong BIGINT(20) NOT NULL DEFAULT 0,
  cipher TEXT NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY secret_tokens_real (user_id, real_hmac),
  UNIQUE KEY secret_tokens_fake (user_id, fake_hmac),
  CONSTRAINT secret_tokens_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;
CREATE TABLE IF NOT EXISTS secret_thread_uses (
  thread_id BIGINT(20) NOT NULL,
  token_id BIGINT(20) NOT NULL,
  clear BIGINT(20) NOT NULL DEFAULT 0,
  PRIMARY KEY (thread_id, token_id),
  KEY secret_thread_uses_token (token_id),
  CONSTRAINT secret_thread_uses_thread FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE,
  CONSTRAINT secret_thread_uses_token FOREIGN KEY (token_id) REFERENCES secret_tokens (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS chat_secret_swap BIGINT(20) NOT NULL DEFAULT 0;
ALTER TABLE chat_threads
  ADD COLUMN IF NOT EXISTS secret_swap BIGINT(20) NOT NULL DEFAULT 0;

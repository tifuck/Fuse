-- Sign-ins in progress (device codes, PKCE verifiers), one per user and
-- provider (app/providers/logins.py). `data` is the provider's pending
-- state, Fernet-encrypted JSON; `token` names one attempt so a finished or
-- replaced sign-in is never completed twice; `state_hash` is the SHA-256
-- of an OAuth state for callback lookups. Times are Unix seconds; rows
-- expire after 15 minutes and the refresh loop sweeps them. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-20-provider-logins.sql
--   mysql fuse_live < scripts/sql/2026-11-20-provider-logins.sql
CREATE TABLE IF NOT EXISTS provider_logins (
  user_id BIGINT(20) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  token CHAR(32) NOT NULL,
  state_hash CHAR(64) DEFAULT NULL,
  data LONGTEXT NOT NULL,
  created_at DOUBLE NOT NULL,
  expires_at DOUBLE NOT NULL,
  PRIMARY KEY (user_id, provider),
  KEY provider_logins_state (state_hash),
  KEY provider_logins_expiry (expires_at),
  CONSTRAINT provider_logins_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

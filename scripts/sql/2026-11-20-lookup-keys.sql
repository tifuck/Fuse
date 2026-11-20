-- Plain B-tree prefix keys for exact-match lookups on LONGTEXT columns.
-- MariaDB never uses a UNIQUE ... USING HASH key for lookups (EXPLAIN shows a
-- full scan), so API-key auth (every /v1 request), login, chat id resolution,
-- push endpoints, host pairing and the registration whitelist scanned whole
-- tables. Same fix as user_hosts_device_token(device_token_hash(64)): a prefix
-- key beside the unique hash key, which stays to enforce uniqueness.
-- Prefixes: sha256 hex is 64 chars, chat public ids are at most 32
-- (chat_schema.PUBLIC_ID_RE), emails and push endpoints get 191.
-- Additive and idempotent.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-11-20-lookup-keys.sql
--   mysql fuse_live < scripts/sql/2026-11-20-lookup-keys.sql
CREATE INDEX IF NOT EXISTS api_keys_key_hash ON api_keys (key_hash(64));
CREATE INDEX IF NOT EXISTS users_email ON users (email(191));
CREATE INDEX IF NOT EXISTS chat_threads_public_id_lookup ON chat_threads (public_id(32));
CREATE INDEX IF NOT EXISTS push_subscriptions_endpoint ON push_subscriptions (endpoint(191));
CREATE INDEX IF NOT EXISTS host_pairings_code_hash ON host_pairings (code_hash(64));
CREATE INDEX IF NOT EXISTS registration_whitelist_email ON registration_whitelist (email(191));

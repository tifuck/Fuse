-- Provider catalogs and per-user model visibility. Additive.
--
-- provider_catalogs: the last good model list of each source row, so a
-- restart lists models at once and the refresh loop fetches only what is
-- due (app/providers/catalogs.py). `models` is the JSON list of public model
-- ids; `detail` the provider's richer catalog where it keeps one
-- (OpenRouter, NanoGPT, Refuseless). Times are Unix seconds.
--
-- user_model_prefs: which models each user shows and may call, per
-- provider: group defaults plus per-model overrides as JSON
-- (app/providers/model_prefs.py).
--
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-20-provider-catalogs.sql
--   mysql fuse_live < scripts/sql/2026-11-20-provider-catalogs.sql
CREATE TABLE IF NOT EXISTS provider_catalogs (
  source_id BIGINT(20) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  models LONGTEXT NOT NULL,
  detail LONGTEXT DEFAULT NULL,
  etag VARCHAR(512) DEFAULT NULL,
  last_modified VARCHAR(128) DEFAULT NULL,
  fetched_at DOUBLE NOT NULL DEFAULT 0,
  next_due DOUBLE NOT NULL DEFAULT 0,
  failures INT(11) NOT NULL DEFAULT 0,
  error VARCHAR(300) NOT NULL DEFAULT '',
  PRIMARY KEY (source_id),
  KEY provider_catalogs_due (next_due),
  CONSTRAINT provider_catalogs_source FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

CREATE TABLE IF NOT EXISTS user_model_prefs (
  user_id BIGINT(20) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  prefs LONGTEXT NOT NULL,
  updated_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (user_id, provider),
  CONSTRAINT user_model_prefs_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

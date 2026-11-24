-- Hourly usage rollup for /usage.json; retires usage_hourly.
-- Additive except the last statement (DROP TABLE usage_hourly).
--
-- usage_rollup_hourly: per UTC hour, user, API key (0: Fuse itself), provider
-- and model ('' is stored as 'Unknown'), every sum the usage page shows
-- (stats._EVENT_SUMS) plus the first and last event time. record_usage adds
-- each event; a reprice or price delete moves cost, estimated_cost, priced,
-- priced_tokens and unpriced_events (db._reprice_batch). /usage.json reads
-- whole hours here and only the partial hours at a window's edges from
-- usage_events, so the page no longer scans every event of the range.
--
-- usage_events.source_type: every writer sets it now; old rows left empty
-- get the value the readers used to compute (the source's type, else
-- 'unknown'), so filters can use the column (and its index) directly.
--
-- This file does not fill usage_rollup_hourly: a refill here would hold
-- locks on every usage event while live requests wait to record theirs, and
-- would be a second copy of the sums. scripts/rebuild_usage_rollups.py
-- --apply fills it with record_usage's own sums, with the service stopped
-- (it refuses while another connection uses the schema); without --apply it
-- reports any drift later.
--
-- usage_hourly: the old hourly rollup (per source, no API key, provider,
-- status or pricing counts). Nothing reads it and record_usage no longer
-- writes it; usage_rollup_hourly replaces it. Code before this migration
-- still writes it, so drop it only once the new code runs.
--
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step: the
-- new code writes usage_rollup_hourly (it must exist and be filled before it
-- starts) and the old code writes usage_hourly (it must exist until then).
-- 1. With the old code running, everything but the DROP:
--      grep -v '^DROP TABLE' scripts/sql/2026-11-20-usage-rollups.sql | mysql fuse_live
-- 2. Stop the service. Run step 1 again (it sets source_type on rows the old
--    code wrote meanwhile), then fill the rollup from the new code's tree:
--      python -m scripts.rebuild_usage_rollups --apply
-- 3. Start the new code, then apply the whole file (drops usage_hourly):
--      mysql fuse_live < scripts/sql/2026-11-20-usage-rollups.sql
-- (fuse_dev the same way, with fuse_dev in place of fuse_live.)
CREATE TABLE IF NOT EXISTS usage_rollup_hourly (
  user_id BIGINT(20) NOT NULL,
  hour CHAR(20) NOT NULL,
  api_key_id BIGINT(20) NOT NULL DEFAULT 0,
  source_type VARCHAR(64) NOT NULL,
  model VARCHAR(512) NOT NULL,
  tokens BIGINT(20) NOT NULL DEFAULT 0,
  requests BIGINT(20) NOT NULL DEFAULT 0,
  prompt BIGINT(20) NOT NULL DEFAULT 0,
  completion BIGINT(20) NOT NULL DEFAULT 0,
  output BIGINT(20) NOT NULL DEFAULT 0,
  reasoning BIGINT(20) NOT NULL DEFAULT 0,
  cached BIGINT(20) NOT NULL DEFAULT 0,
  cache_write BIGINT(20) NOT NULL DEFAULT 0,
  ok_requests BIGINT(20) NOT NULL DEFAULT 0,
  failed BIGINT(20) NOT NULL DEFAULT 0,
  cancelled BIGINT(20) NOT NULL DEFAULT 0,
  cost BIGINT(20) NOT NULL DEFAULT 0,
  estimated_cost BIGINT(20) NOT NULL DEFAULT 0,
  priced BIGINT(20) NOT NULL DEFAULT 0,
  priced_tokens BIGINT(20) NOT NULL DEFAULT 0,
  unpriced_events BIGINT(20) NOT NULL DEFAULT 0,
  first_at VARCHAR(32) NOT NULL,
  last_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (user_id, hour, api_key_id, source_type, model),
  KEY usage_rollup_hourly_hour (hour)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

UPDATE usage_events e LEFT JOIN sources s ON s.id = e.source_id
  SET e.source_type = COALESCE(s.type, 'unknown')
  WHERE e.source_type = '';

DROP TABLE IF EXISTS usage_hourly;

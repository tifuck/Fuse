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
-- The backfill rewrites each rollup row from usage_events and is safe to
-- rerun: INSERT ... SELECT locks the events it reads, so usage recorded
-- meanwhile waits. Usage recorded by older code (which does not write the
-- rollup) between applying this and restarting on the new code is missed:
-- apply it again right after that restart. scripts/rebuild_usage_rollups.py
-- reports (and with --apply rebuilds) any drift later.
--
-- usage_hourly: the old hourly rollup (per source, no API key, provider,
-- status or pricing counts). Nothing reads it and record_usage no longer
-- writes it; usage_rollup_hourly replaces it. Code before this migration
-- still writes it, so drop it only once the new code runs.
--
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step, in
-- two runs: the new code writes usage_rollup_hourly (it must exist before the
-- restart) and the old code writes usage_hourly (it must exist until then).
-- Before restarting on the new code, everything but the DROP:
--   grep -v '^DROP TABLE' scripts/sql/2026-11-20-usage-rollups.sql | mysql fuse_live
-- Right after the restart, the whole file (refills the rollup, then drops):
--   mysql fuse_live < scripts/sql/2026-11-20-usage-rollups.sql
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

INSERT INTO usage_rollup_hourly
  (user_id, hour, api_key_id, source_type, model, tokens, requests, prompt, completion, output, reasoning,
   cached, cache_write, ok_requests, failed, cancelled, cost, estimated_cost, priced, priced_tokens,
   unpriced_events, first_at, last_at)
SELECT e.user_id,
       CONCAT(LEFT(e.created_at, 13), ':00:00Z'),
       COALESCE(e.api_key_id, 0),
       e.source_type,
       COALESCE(NULLIF(e.model, ''), 'Unknown'),
       SUM(CASE WHEN e.total_tokens > 0 THEN e.total_tokens ELSE e.prompt_tokens + e.output_tokens END),
       SUM(e.count_request),
       SUM(e.prompt_tokens),
       SUM(e.completion_tokens),
       SUM(e.output_tokens),
       SUM(e.reasoning_tokens),
       SUM(e.cached_tokens),
       SUM(e.cache_write_tokens),
       SUM(CASE WHEN e.count_request = 1 AND e.request_status = 'success' THEN 1 ELSE 0 END),
       SUM(CASE WHEN e.count_request = 1 AND e.request_status = 'failed' THEN 1 ELSE 0 END),
       SUM(CASE WHEN e.count_request = 1 AND e.request_status = 'cancelled' THEN 1 ELSE 0 END),
       SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)),
       SUM(e.estimated_cost_micros),
       SUM(CASE WHEN e.count_request = 1 AND (e.reported_cost_micros IS NOT NULL OR e.pricing_known = 1)
                THEN 1 ELSE 0 END),
       SUM(CASE WHEN e.reported_cost_micros IS NOT NULL OR e.pricing_known = 1
                THEN CASE WHEN e.total_tokens > 0 THEN e.total_tokens ELSE e.prompt_tokens + e.output_tokens END
                ELSE 0 END),
       SUM(CASE WHEN (e.total_tokens > 0 OR e.prompt_tokens + e.output_tokens > 0)
                     AND e.reported_cost_micros IS NULL AND e.pricing_known = 0 THEN 1 ELSE 0 END),
       MIN(e.created_at),
       MAX(e.created_at)
FROM usage_events e
GROUP BY 1, 2, 3, 4, 5
ON DUPLICATE KEY UPDATE
  tokens = VALUES(tokens), requests = VALUES(requests), prompt = VALUES(prompt),
  completion = VALUES(completion), output = VALUES(output), reasoning = VALUES(reasoning),
  cached = VALUES(cached), cache_write = VALUES(cache_write), ok_requests = VALUES(ok_requests),
  failed = VALUES(failed), cancelled = VALUES(cancelled), cost = VALUES(cost),
  estimated_cost = VALUES(estimated_cost), priced = VALUES(priced), priced_tokens = VALUES(priced_tokens),
  unpriced_events = VALUES(unpriced_events), first_at = VALUES(first_at), last_at = VALUES(last_at);

DROP TABLE IF EXISTS usage_hourly;

-- Web access through connectors (app/search/egress.py): user_hosts.web_egress
-- marks a connector the user picked for web searches and page reads;
-- user_hosts.web_ip is the public IP sites see from it, read through the
-- connector, so machines behind one router can be told apart from the rest.
-- Additive, idempotent.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-11-23-web-egress.sql
--   mysql fuse_live < scripts/sql/2026-11-23-web-egress.sql
ALTER TABLE user_hosts
  ADD COLUMN IF NOT EXISTS web_egress BIGINT(20) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS web_ip VARCHAR(64) NOT NULL DEFAULT '';

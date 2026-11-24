-- Interactive terminal audit (app/terminal.py): one row per terminal
-- session, metadata only (who, which machine, as whom, where, when, how it
-- ended, byte counts). Never keystrokes or output. Rows age out with
-- retention.HISTORY_DAYS after they close. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that writes it):
--   mysql fuse_dev  < scripts/sql/2026-11-24-terminal-sessions.sql
--   mysql fuse_live < scripts/sql/2026-11-24-terminal-sessions.sql
CREATE TABLE IF NOT EXISTS terminal_sessions (
  id VARCHAR(64) NOT NULL,
  user_id BIGINT(20) NOT NULL,
  host_id BIGINT(20) NOT NULL,
  alias VARCHAR(191) NOT NULL DEFAULT '',
  transport VARCHAR(8) NOT NULL DEFAULT '',
  remote_user VARCHAR(191) NOT NULL DEFAULT '',
  root BIGINT(20) NOT NULL DEFAULT 0,
  thread_id BIGINT(20) DEFAULT NULL,
  project_id BIGINT(20) DEFAULT NULL,
  cwd LONGTEXT NOT NULL DEFAULT '',
  shell VARCHAR(32) NOT NULL DEFAULT '',
  opened_at VARCHAR(32) NOT NULL,
  closed_at VARCHAR(32) DEFAULT NULL,
  close_reason VARCHAR(32) NOT NULL DEFAULT '',
  exit_code BIGINT(20) DEFAULT NULL,
  client_ip VARCHAR(64) NOT NULL DEFAULT '',
  user_agent VARCHAR(255) NOT NULL DEFAULT '',
  bytes_in BIGINT(20) NOT NULL DEFAULT 0,
  bytes_out BIGINT(20) NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY terminal_sessions_user (user_id, opened_at),
  KEY terminal_sessions_closed (closed_at),
  CONSTRAINT terminal_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin;

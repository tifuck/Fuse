"""Usage area helpers: sources, pool accounts, API keys and rollup checks."""
from app import accounts, db
from app.sources import pool_policy
from scripts import rebuild_usage_rollups as rebuild


def user(email="user@core.test", admin=False):
    """A users row without the password and vault work of accounts.create_user
    (two scrypt runs, ~80 ms); usage and routing never read either."""
    with db.tx() as conn:
        conn.execute("INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?,'unused',?,?)",
                     (email, 1 if admin else 0, db.now()))
        return db.last_id(conn)


def source(kind, name=None):
    """A plain sources row of this type; returns its id."""
    with db.tx() as conn:
        conn.execute("INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,1,'{}',?)",
                     (kind, name or kind, db.now()))
        return db.last_id(conn)


def grok_extra(percent=None, **billing):
    """A signed-in SuperGrok account; percent is its plan usage."""
    data = {"fetched_at": "2026-01-01T00:00:00Z", "period_type": "month", **billing}
    if percent is not None:
        data["credit_usage_percent"] = percent
    return {"access_token": "tok", "billing": data}


def pool_account(kind, owner, extra=None, secret="secret"):
    """A donated credential in this pool (the pool source is created on demand)."""
    return accounts.add_credential(pool_policy.source_id(kind), kind, secret, extra=extra or {}, donated_by=owner)


def pool_rows(kind):
    return db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (pool_policy.source_id(kind),))


def api_key(uid, name="Laptop"):
    accounts.create_api_key(uid, name)
    return int(accounts.keys_for(uid)[-1]["id"])


def rollup_drift():
    """Rollup rows that disagree with SUM(usage_events); {} when every rollup
    (credentials, api_keys, provider_usage, usage_rollup_hourly) matches."""
    with db.tx() as conn:
        return {name: count for name, count in rebuild.report(conn).items() if count}


def cost_sums():
    """Total cost held by the events and by each rollup table."""
    def total(sql):
        return int(db.fetchone(sql)["c"])

    return {
        "events": total("SELECT COALESCE(SUM(COALESCE(reported_cost_micros, estimated_cost_micros)),0) AS c FROM usage_events"),
        "hourly": total("SELECT COALESCE(SUM(cost),0) AS c FROM usage_rollup_hourly"),
        "credentials": total("SELECT COALESCE(SUM(cost_micros),0) AS c FROM credentials"),
        "api_keys": total("SELECT COALESCE(SUM(cost_micros),0) AS c FROM api_keys"),
        "providers": total("SELECT COALESCE(SUM(cost_micros),0) AS c FROM provider_usage"),
    }

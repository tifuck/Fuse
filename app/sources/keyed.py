"""KeyedSource: an API-key provider described by a KeyedPreset
(app/providers/presets.py), plus storing, validating and listing its keys.

Keys live in credentials on the provider's own sources row. A personal key
(donated_by = the user) serves only its owner and never enters a pool. A site
key (donated_by NULL) serves the users sources.config.site_access names:
"none" (the default), "admins" or "everyone". A request uses the caller's own
keys first, then site keys.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .. import accounts, db
from ..crypto import encrypt
from ..providers import creds, registry
from ..proxy import UsageStream
from . import http_pool
from .base import ChatResult, map_effort
from .openai_compat import OpenAICompatSource
from .pool_pick import AUTH_RECHECK_SECONDS, note_failure

logger = logging.getLogger(__name__)

SITE_ACCESS = ("none", "admins", "everyone")
CATALOG_TTL = 21600.0  # per-key catalogs (presets with per_key)
FAILED_TTL = 60.0
BALANCE_TTL = 300.0
# Message fields every OpenAI-compatible API takes; Fuse's own replay fields
# (claude_thinking, reasoning_content, ...) are a 400 on strict ones.
_MESSAGE_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})
# (source id, credential id) -> (fresh until, public model ids)
_catalogs: dict[tuple[int, int], tuple[float, list[str]]] = {}


def preset(type_id: str):
    """How users add a key for this provider (its spec's key), or None."""
    spec = registry.get(type_id)
    return spec.key if spec else None


def ensure_source(type_id: str) -> int:
    from . import _ensure_typed_source

    return _ensure_typed_source(type_id, preset(type_id).label, {})


def clear_catalogs(source_id: int | None = None) -> None:
    for key in [k for k in _catalogs if source_id is None or k[0] == source_id]:
        _catalogs.pop(key, None)


def _cfg(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def site_access(cfg: dict, default: str = "none") -> str:
    level = cfg.get("site_access")
    return level if level in SITE_ACCESS else default


def _is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    row = db.fetchone_memo("SELECT is_admin FROM users WHERE id=?", (user_id,))
    return bool(row and row["is_admin"])


def site_allowed(cfg: dict, user_id: int | None, default: str = "none") -> bool:
    level = site_access(cfg, default)
    return level == "everyone" or (level == "admins" and _is_admin(user_id))


def _extra(row) -> dict:
    try:
        return accounts.cred_extra(row)
    except Exception:
        return {}


def access_states(user_id: int) -> dict[str, dict]:
    """Every keyed provider's verdict for one user, from one query: "own"
    (a personal key), "site" (a site key opened to them) or "none"."""
    out = {s.id: {"ok": False, "reason": "none"} for s in registry.specs() if s.keyed}
    rows = db.fetchall_memo(
        "SELECT s.type, s.config, c.donated_by FROM credentials c JOIN sources s ON s.id=c.source_id"
        " WHERE c.disabled=0 AND s.enabled=1 AND (c.donated_by=? OR c.donated_by IS NULL)",
        (user_id,),
    )
    for row in rows:
        state = out.get(row["type"])
        if state is None or state["reason"] == "own":
            continue
        if row["donated_by"] is not None:
            out[row["type"]] = {"ok": True, "reason": "own"}
        elif not state["ok"] and site_allowed(_cfg(row["config"]), user_id):
            out[row["type"]] = {"ok": True, "reason": "site"}
    return out


def auth_headers(p, key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if p.auth == "x-api-key":
        headers["x-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(dict(p.headers))
    return headers


def catalog_url(p, region: str = "") -> str:
    return p.catalog if p.catalog.startswith("https://") else p.base(region).rstrip("/") + p.catalog


def parse_catalog(p, data) -> list[str]:
    items = data.get("data") if isinstance(data, dict) else data
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        mid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(mid, str) or not mid.strip():
            continue
        mid = mid.strip()
        if p.id_strip and mid.startswith(p.id_strip):
            mid = mid[len(p.id_strip):]
        if p.hide and re.search(p.hide, mid, re.I):
            continue
        name = p.public_prefix + mid
        if name not in names:
            names.append(name)
    return names


async def fetch_models(p, key: str, region: str = "", *, etag: str | None = None,
                       last_modified: str | None = None, meta: dict | None = None) -> list[str] | None:
    """Public ids from the provider's catalog, or None when it failed.

    With the etag / last_modified a previous answer carried, the request is
    conditional: a 304 returns NOT_MODIFIED. meta (when given) receives the
    validators of a 200 answer and its HTTP status."""
    headers = auth_headers(p, key)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(catalog_url(p, region), headers=headers)
        if meta is not None:
            meta["status"] = r.status_code
        if r.status_code == 304 and (etag or last_modified):
            if meta is not None:
                meta.update(etag=etag, last_modified=last_modified)
            return NOT_MODIFIED
        if r.status_code != 200:
            return None
        names = parse_catalog(p, r.json())
        if meta is not None:
            meta.update(etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"))
        return names
    except (httpx.HTTPError, ValueError):
        return None


# fetch_models' answer to a conditional request the provider says is unchanged.
NOT_MODIFIED: list[str] = []


def prepare(p, body: dict) -> dict:
    """The request as this provider takes it: Fuse-only fields dropped, the
    effort mapped onto the model's own levels or left out."""
    out = {k: v for k, v in body.items() if k not in p.strip}
    effort = map_effort(body.get("reasoning_effort"), p.efforts_for(str(body.get("model") or "")))
    if effort:
        out["reasoning_effort"] = effort
    if isinstance(out.get("messages"), list):
        out["messages"] = [
            {k: v for k, v in m.items() if k in _MESSAGE_KEYS} if isinstance(m, dict) else m for m in out["messages"]
        ]
    return out


class KeyedSource(OpenAICompatSource):
    def __init__(self, row, cfg: dict, creds):
        super().__init__(row, cfg, creds)
        self.type_id = row["type"]
        self.preset = preset(self.type_id)
        self.pin_kind = f"{self.type_id}:{self.source_id}"
        self.send_cache_key = self.preset.cache_key
        self.all_creds = list(self.creds)
        self.region = ""

    def usable(self, user_id: int | None) -> list:
        """The caller's own keys, then site keys opened to them.

        shortcut: one region per request (the first usable key's); keys of
        another region wait. Upgrade when a provider needs mixed-region failover."""
        if not user_id:
            return []
        own = [c for c in self.all_creds if c["donated_by"] is not None and int(c["donated_by"]) == int(user_id)]
        site = [c for c in self.all_creds if c["donated_by"] is None]
        if site and not site_allowed(self.cfg, user_id):
            site = []
        creds = own + site
        if not creds:
            return []
        self.region = str(_extra(creds[0]).get("region") or "")
        return [c for c in creds if str(_extra(c).get("region") or "") == self.region]

    def _url(self) -> str:
        return self.preset.base(self.region).rstrip("/")

    def _headers(self, key: str) -> dict:
        return auth_headers(self.preset, key)

    def catalog_creds(self) -> list:
        """Keys the shared catalog may be read with: site keys first, then
        personal ones (every key lists the same models unless the preset
        says per_key)."""
        site = [c for c in self.all_creds if c["donated_by"] is None]
        return site + [c for c in self.all_creds if c["donated_by"] is not None]

    async def shared_models(self, stored: dict | None = None) -> list[str]:
        """The provider's catalog read with one working key, for every user
        with access. stored: the last saved catalog row, whose validators make
        the request conditional; a 304 returns its list. Sets catalog_meta to
        the validators to save. Raises when no key could read it."""
        self.catalog_meta = {}
        for cred in self.catalog_creds()[:3]:
            region = str(_extra(cred).get("region") or "")
            meta: dict = {}
            names = await fetch_models(self.preset, accounts.cred_secret(cred), region,
                                       etag=(stored or {}).get("etag"), last_modified=(stored or {}).get("last_modified"),
                                       meta=meta)
            if names is NOT_MODIFIED:
                self.catalog_meta = {"etag": meta.get("etag"), "last_modified": meta.get("last_modified")}
                return list(stored["models"])
            if names is not None:
                self.catalog_meta = {"etag": meta.get("etag"), "last_modified": meta.get("last_modified")}
                return names
            if meta.get("status") not in (401, 403):
                break  # an outage, not this key: the next key would fail the same way
        raise ValueError(f"{self.preset.label} catalog unavailable")

    async def models(self, *, user_id: int | None = None) -> list[str]:
        if not (self.preset.per_key and user_id):
            from ..providers import catalogs

            return await self.shared_models(catalogs.get(self.source_id))
        creds = self.usable(user_id)
        if not creds:
            return []
        key = (self.source_id, int(creds[0]["id"]))
        hit = _catalogs.get(key)
        if hit and time.time() < hit[0]:
            return list(hit[1])
        names = await fetch_models(self.preset, accounts.cred_secret(creds[0]), self.region)
        if names is None:
            logger.warning("Model catalog unavailable for %s source id=%s", self.type_id, self.source_id)
            names, ttl = (hit[1] if hit else []), FAILED_TTL
        else:
            ttl = CATALOG_TTL
        _catalogs[key] = (time.time() + ttl, names)
        return list(names)

    def cached_models(self, user_id: int | None) -> list[str]:
        """This user's per-key catalog as last read, without a fetch."""
        creds = self.usable(user_id)
        hit = _catalogs.get((self.source_id, int(creds[0]["id"]))) if creds else None
        return list(hit[1]) if hit else []

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        self.creds = self.usable(user_id)
        if not self.creds:
            return ChatResult(status=403, body={"error": {"message": f"Add a {self.preset.label} API key on Sources to use its models."}})
        payload = prepare(self.preset, body)
        if self.preset.protocol == "anthropic_messages":
            return await self._anthropic(payload)
        return await super().chat(payload, session_id=session_id)

    async def _anthropic(self, body: dict) -> ChatResult:
        from ..providers.protocols import anthropic_messages
        from .claude_stream import _aggregate, _error_fields, _error_message, _translated_stream

        upstream = str(body.get("model") or "")
        public = self.preset.public_prefix + upstream
        label = self.preset.label
        payload = anthropic_messages.build_payload(body, upstream, max_tokens=self.preset.max_tokens)
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": {"message": "All keys failed"}}, model=public)
        for cred in self._next_creds():
            client = http_pool.client(httpx.Timeout(300.0, connect=15.0))
            headers = {**self._headers(accounts.cred_secret(cred)), "Accept": "text/event-stream"}
            try:
                req = client.build_request("POST", self._url() + "/messages", headers=headers, json=payload)
                resp = await client.send(req, stream=True)
            except httpx.HTTPError as exc:
                status = 504 if isinstance(exc, httpx.TimeoutException) else 502
                return ChatResult(status=status, body={"error": {"message": f"{label} request failed ({type(exc).__name__})"}},
                                  credential_id=cred["id"], model=public, attempts=attempts)
            if resp.status_code != 200:
                raw = (await resp.aread()).decode("utf-8", errors="replace")
                retry_after = str(resp.headers.get("retry-after") or "").strip()
                await resp.aclose()
                detail = _error_message(raw)
                error = {"message": f"{label} request failed ({resp.status_code})" + (f": {detail[:300]}" if detail else "")}
                error.update(_error_fields(raw))
                last = ChatResult(status=resp.status_code, body={"error": error}, credential_id=cred["id"], model=public,
                                  response_headers={"Retry-After": retry_after} if retry_after else {})
                if resp.status_code == 401:
                    note_failure(cred["id"], cooldown_seconds=AUTH_RECHECK_SECONDS)
                    attempts.append({"credential_id": cred["id"], "status": 401, "usage": {}})
                    continue
                last.attempts = attempts
                return last
            if body.get("stream"):
                collected: dict = {}
                stream = _translated_stream(resp, upstream, collected, public_id=public)
                return ChatResult(status=200, stream=UsageStream(stream, collected), credential_id=cred["id"],
                                  model=public, attempts=attempts)
            obj = await _aggregate(resp, upstream, public_id=public)
            if isinstance(obj.get("error"), dict):
                usage = obj.pop("_fuse_usage", {}) or {}
                return ChatResult(status=502, body=obj, usage=usage, credential_id=cred["id"], model=public, attempts=attempts)
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            return ChatResult(status=200, body=obj, usage=usage, credential_id=cred["id"], model=public, attempts=attempts)
        if attempts and last.credential_id == attempts[-1].get("credential_id"):
            attempts.pop()
        last.attempts = attempts
        return last


def parse_balance(data) -> dict | None:
    """A balance reply as {amount, currency}: DeepSeek's balance_infos (USD
    first) or Moonshot's data.available_balance (USD)."""
    if not isinstance(data, dict):
        return None
    infos = data.get("balance_infos")
    if isinstance(infos, list):
        rows = [r for r in infos if isinstance(r, dict) and creds.as_float(r.get("total_balance")) is not None]
        rows.sort(key=lambda r: r.get("currency") != "USD")
        return {"amount": creds.as_float(rows[0]["total_balance"]), "currency": str(rows[0].get("currency") or "USD")[:8]} if rows else None
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    amount = creds.as_float(inner.get("available_balance"))
    return {"amount": amount, "currency": "USD"} if amount is not None else None


async def refresh_balance(p, row, *, force: bool = False) -> dict | None:
    """The key's account balance, read at most once per BALANCE_TTL and kept
    in extra["balance"]. A failed read keeps the last amount."""
    if not p.balance:
        return None
    extra = _extra(row)
    stored = extra.get("balance") if isinstance(extra.get("balance"), dict) else {}
    if not force and creds.usage_fresh(stored, BALANCE_TTL):
        return stored
    url = p.base(str(extra.get("region") or "")).rstrip("/") + p.balance
    parsed, err = None, ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=auth_headers(p, accounts.cred_secret(row)))
        parsed = parse_balance(r.json()) if r.status_code == 200 else None
        err = "" if parsed else f"balance http {r.status_code}"
    except (httpx.HTTPError, ValueError):
        err = "balance request failed"
    stored = {**stored, **(parsed or {}), "checked_at": db.now(), "last_error": err}
    extra["balance"] = stored
    accounts.update_extra(int(row["id"]), extra)
    return stored


async def validate_key(p, key: str, region: str = "") -> str:
    """"" when the provider accepts the key on its catalog endpoint, else why
    not. Never repeats the key or the provider's response text."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(catalog_url(p, region), headers=auth_headers(p, key))
    except httpx.HTTPError:
        return f"Could not reach {p.label} to check the key. Try again shortly."
    if r.status_code == 200:
        return ""
    if r.status_code in (401, 403):
        return f"{p.label} rejected this key."
    if r.status_code == 429:
        return f"{p.label} rate limited the key check. Try again in a minute."
    return f"{p.label} could not check the key (HTTP {r.status_code})."


def _same_key(rows, key: str):
    for row in rows:
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def _linked_elsewhere(row, user_id: int | None) -> bool:
    return row is not None and not row["disabled"] and row["donated_by"] != user_id


async def store_key(type_id: str, raw: str, *, user_id: int | None, label: str = "", method: str = "",
                    region: str = "") -> tuple[int | None, str]:
    """Validate and save a key: personal for user_id, a site key for None.
    One active personal key per user and method; a re-added key updates its row."""
    p = preset(type_id)
    key = (raw or "").strip()
    if not 8 <= len(key) <= 4096 or any(ord(ch) <= 32 or ord(ch) == 127 for ch in key):
        return None, f"Enter a {p.label} API key."
    methods = [m.id for m in p.methods]
    method = method if method in methods else methods[0]
    regions = [r.id for r in p.regions]
    region = region if region in regions else (regions[0] if regions else "")
    sid = ensure_source(type_id)
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (sid,))
    if _linked_elsewhere(_same_key(rows, key), user_id):
        return None, "That key is already linked to another account."
    err = await (p.validate(key) if p.validate else validate_key(p, key, region))
    if err:
        return None, err
    name = (label or "").strip()[:80] or f"{p.label} key"
    extra = {"method": method, "region": region}
    clear_catalogs(sid)
    with db.tx() as conn:
        # The source row lock serializes key saves, so a key submitted twice
        # during its check is decided on rows the other save already wrote.
        conn.execute("SELECT id FROM sources WHERE id=?" + db.for_update(conn), (sid,)).fetchone()
        rows = conn.execute("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (sid,)).fetchall()
        same = _same_key(rows, key)
        if _linked_elsewhere(same, user_id):
            return None, "That key is already linked to another account."
        target = same
        if target is None and user_id is not None:
            target = next((r for r in rows if r["donated_by"] == user_id and not r["disabled"]
                           and _extra(r).get("method", methods[0]) == method), None)
        if target is not None:
            conn.execute(
                "UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?",
                (user_id, name, encrypt(key), encrypt(json.dumps(extra)), target["id"]),
            )
            return int(target["id"]), ""
        conn.execute(
            """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, name, encrypt(key), encrypt(json.dumps(extra)), db.iso_week_start(), user_id, db.now()),
        )
        return int(db.last_id(conn)), ""


def remove_key(type_id: str, cred_id: int, *, user_id: int | None) -> bool:
    """Disable one key of this provider: user_id's own, or a site key for None."""
    sid = ensure_source(type_id)
    row = db.fetchone("SELECT id, source_id, donated_by FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid or row["donated_by"] != user_id:
        return False
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))
    clear_catalogs(sid)
    return True


def _labels(p, extra: dict) -> tuple[str, str]:
    method = next((m.label for m in p.methods if m.id == extra.get("method")), "") if len(p.methods) > 1 else ""
    region = next((r.label for r in p.regions if r.id == extra.get("region")), "") if len(p.regions) > 1 else ""
    return method, region


def directory(user_id: int) -> list[dict]:
    """Enabled providers that take a personal key, with the user's own keys, for Sources."""
    states = access_states(user_id)
    enabled = {row["type"] for row in db.fetchall("SELECT type FROM sources WHERE enabled=1")}
    own = db.fetchall(
        "SELECT c.id, c.label, c.extra, s.type FROM credentials c JOIN sources s ON s.id=c.source_id"
        " WHERE c.donated_by=? AND c.disabled=0 ORDER BY c.id",
        (user_id,),
    )
    out = []
    for spec in registry.specs():
        p = spec.key
        if p is None or p.id not in enabled:
            continue
        keys = []
        for row in own:
            if row["type"] == p.id:
                method, region = _labels(p, _extra(row))
                keys.append({"id": row["id"], "label": row["label"], "method": method, "region": region})
        out.append({"id": p.id, "label": p.label, "group": p.group, "prefix": p.public_prefix if spec.keyed else "",
                    "key_url": p.key_url, "regions": p.regions, "methods": p.methods, "keys": keys,
                    "site": states.get(p.id, {}).get("reason") == "site"})
    return out


def admin_rows() -> list[dict]:
    """Every keyed provider with its switches and site keys, for the admin page."""
    out = []
    for spec in registry.specs():
        p = spec.keyed
        if p is None:
            continue
        sid = ensure_source(p.id)
        row = db.fetchone("SELECT * FROM sources WHERE id=?", (sid,))
        creds = db.fetchall("SELECT id, label, extra, donated_by FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id", (sid,))
        site = []
        for cred in creds:
            if cred["donated_by"] is None:
                _method, region = _labels(p, _extra(cred))
                site.append({"id": cred["id"], "label": cred["label"], "region": region})
        out.append({"id": p.id, "label": p.label, "enabled": bool(row and row["enabled"]),
                    "site_access": site_access(_cfg(row["config"] if row else None)), "regions": p.regions,
                    "personal": sum(1 for c in creds if c["donated_by"] is not None), "site": site})
    return out

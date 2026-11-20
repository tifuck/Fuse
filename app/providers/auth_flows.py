"""The sign-in flows providers share. Providers supply only their own calls
(start, poll, exchange, store); the shared parts live here:

* api_key_paste: validate and store a pasted key (app.sources.keyed).
* device_code (RFC 8628): token-response checks and the answers a poll gives
  for authorization_pending, slow_down, terminal errors and the unexpected.
* pkce_paste / pkce_redirect: the verifier and state live with the pending
  sign-in (logins.py); a callback is matched by state (logins.by_state).

Pending sign-ins are stored by logins.py. Poll answers keep the JSON contract
static/app.js pollDevice reads: {"ok": true} done; {"pending": true,
"retry_after": s[, "error": text]} keep polling; {"error": text} stop.
"""
from __future__ import annotations

from . import logins


async def api_key_paste(provider: str, raw: str, *, user_id: int | None, label: str = "", method: str = "",
                        region: str = "") -> tuple[int | None, str]:
    from ..sources import keyed

    return await keyed.store_key(provider, raw, user_id=user_id, label=label, method=method, region=region)


def oauth_tokens(raw) -> dict:
    """A token response checked before anything is stored (RFC 6749 5.1)."""
    if not isinstance(raw, dict):
        raise ValueError("Token response was not an object.")
    access = raw.get("access_token")
    refresh = raw.get("refresh_token", "")
    expires = raw.get("expires_in")
    if (
        not isinstance(access, str)
        or not access
        or len(access) > 16_384
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in access)
    ):
        raise ValueError("Token response did not contain a valid access token.")
    if not isinstance(refresh, str) or len(refresh) > 16_384 or any(
        ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in refresh
    ):
        raise ValueError("Token response contained an invalid refresh token.")
    if expires in (None, ""):
        # Optional in OAuth (RFC 6749 5.1): the issued tokens are already
        # spent from the device code, so keep them; expires_at() applies the
        # provider default.
        return {**raw, "access_token": access, "refresh_token": refresh, "expires_in": None}
    if isinstance(expires, bool):
        raise ValueError("Token response contained an invalid expiry.")
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        raise ValueError("Token response contained an invalid expiry.") from None
    if expires <= 0 or expires > 31_536_000:
        raise ValueError("Token response contained an invalid expiry.")
    return {**raw, "access_token": access, "refresh_token": refresh, "expires_in": expires}


# Terminal device-flow error codes, matched exactly: a transient message
# merely containing "expired" or "denied" must keep polling, not kill the
# pending sign-in.
TERMINAL_ERRORS = frozenset(
    {
        "access_denied",
        "authorization_declined",
        "cancelled",
        "expired_token",
        "invalid_grant",
        "invalid_request",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)


def terminal_error(error: str) -> bool:
    return (error or "").strip().lower() in TERMINAL_ERRORS


def retrying(pending: dict, message: str) -> tuple[int, dict]:
    """A transient failure: keep polling at the current interval."""
    return 502, {"pending": True, "error": message, "retry_after": pending.get("interval", 5)}


def device_step(user_id: int, provider: str, label: str, pending: dict, data: dict) -> tuple[int, dict]:
    """The poll answer for a device-code reply that carried no token."""
    err = data.get("error")
    if err == "slow_down":
        try:
            current = int(pending.get("interval") or 5)
        except (TypeError, ValueError):
            current = 5
        pending["interval"] = min(30, max(3, current) + 5)
        logins.update(user_id, provider, pending)
        return 200, {"pending": True, "error": f"{label} asked Fuse to poll more slowly.",
                     "retry_after": pending["interval"]}
    if err == "authorization_pending":
        return 200, {"pending": True, "retry_after": pending.get("interval", 5)}
    if terminal_error(str(err or data.get("error_description") or "")):
        logins.drop(user_id, provider)
        return 400, {"error": f"{label} sign-in was denied or expired. Start again."}
    return retrying(pending, f"{label} returned an unexpected response; retrying.")

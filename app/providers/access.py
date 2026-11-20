"""Which gated providers one user may use. One Access per request: each
provider's state is computed only when the request first needs it, then
reused (listing, routing and the denial message all read the same answer)."""
from __future__ import annotations

from . import registry


class Access:
    def __init__(self, user_id: int | None = None, fixed: dict[str, bool] | None = None):
        self.user_id = user_id
        self._fixed = fixed
        self._states: dict[str, dict] = {}

    @classmethod
    def fixed(cls, user_id: int | None, allowed: dict[str, bool]) -> "Access":
        """Set answers instead of each provider's verdict; gated providers not
        named are denied."""
        return cls(user_id, dict(allowed))

    def state(self, type_id: str) -> dict:
        spec = registry.get(type_id)
        if spec is None or spec.access is None:
            return {"ok": True, "reason": "open"}
        if type_id not in self._states:
            if self._fixed is not None:
                ok = bool(self._fixed.get(type_id))
                self._states[type_id] = {"ok": ok, "reason": "ok" if ok else "none"}
            elif self.user_id is None:
                self._states[type_id] = {"ok": False, "reason": "none"}
            elif spec.keyed is not None:
                # One query answers every keyed provider.
                from ..sources import keyed

                self._states.update(keyed.access_states(self.user_id))
            else:
                self._states[type_id] = spec.access(self.user_id)
        return self._states[type_id]

    def allows(self, type_id: str) -> bool:
        return bool(self.state(type_id)["ok"])

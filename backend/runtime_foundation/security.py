from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .models import Principal, ROLE_CAPABILITIES, Role


class AuthenticationError(PermissionError):
    pass


class AuthorizationError(PermissionError):
    pass


class DisabledInternalAuthAdapter:
    """Approved STAGING boundary: public intake, no staff access or fake identity."""

    def authenticate(self, token: str | None) -> Principal:
        raise AuthenticationError("internal_access_disabled")


class LocalTestAuthAdapter:
    """Injected synthetic tokens only; production IdP intentionally unresolved."""

    def __init__(self, identities: dict[str, Principal]):
        self._identities = dict(identities)

    def authenticate(self, token: str | None) -> Principal:
        principal = self._identities.get(token or "")
        if principal is None:
            raise AuthenticationError("authentication_required")
        return principal


def require(principal: Principal, capability: str, record_id: str | None = None) -> None:
    if capability not in ROLE_CAPABILITIES[principal.role]:
        raise AuthorizationError("insufficient_capability")
    if record_id and principal.role is not Role.ADMIN:
        allowed = principal.allowed_record_ids or frozenset()
        if record_id not in allowed:
            raise AuthorizationError("object_access_denied")


class InMemoryRateLimiter:
    def __init__(self, limit: int = 5, window_seconds: int = 60, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, bucket: str) -> bool:
        now = self.clock()
        events = self._events[bucket]
        while events and events[0] <= now - self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


class DeterministicAbuseAdapter:
    def assess(self, proof: str | None) -> bool:
        return proof != "TEST_REJECT"


def pii_safe_actor_id(raw: str) -> str:
    return "actor_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


@dataclass(frozen=True)
class OriginPolicy:
    allowed_origins: frozenset[str]

    def allow(self, origin: str | None, credentialed: bool = False) -> bool:
        if not origin:
            return True
        return origin in self.allowed_origins

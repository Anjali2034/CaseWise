"""Per-visitor rate limits for the AI endpoints.

The hosted AI provider's free tier has a shared quota. Without limits, one
visitor (or a bot) could use it up for everyone, so each visitor gets a
per-minute and a per-day allowance.

Counts live in memory: that's right for a single server instance (Render's
free plan), and they reset when the server restarts.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Mapping, Optional, Tuple

MINUTE = 60
DAY = 24 * 60 * 60


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int, clock: Callable[[], float] = time.monotonic):
        """A limit of 0 turns that window off."""
        self.per_minute = per_minute
        self.per_day = per_day
        self.clock = clock
        self._hits = defaultdict(deque)   # visitor key -> timestamps of allowed requests (last 24h)
        self._lock = threading.Lock()     # endpoints run in a thread pool

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0 or self.per_day > 0

    def check(self, key: str) -> Optional[Tuple[int, str]]:
        """Record a request for `key` if it's allowed.

        Returns None when allowed, or (retry_after_seconds, "minute" | "day") when not.
        """
        if not self.enabled:
            return None
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= DAY:
                hits.popleft()
            if self.per_day and len(hits) >= self.per_day:
                return max(1, int(hits[0] + DAY - now) + 1), "day"
            if self.per_minute:
                recent = [t for t in hits if now - t < MINUTE]
                if len(recent) >= self.per_minute:
                    return max(1, int(recent[0] + MINUTE - now) + 1), "minute"
            hits.append(now)
            if len(self._hits) > 10_000:
                self._forget_idle(now)
            return None

    def _forget_idle(self, now: float) -> None:
        """Drop visitors with no requests in the last day, so memory stays bounded."""
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= DAY]:
            del self._hits[key]


def client_key(client_host: Optional[str], headers: Mapping[str, str], trust_proxy: bool) -> str:
    """Which visitor a request comes from.

    Behind a proxy (Render, which sits behind Cloudflare) every request arrives from
    the proxy's address, so the visitor's IP has to come from a header. Only used when
    TRUST_PROXY_HEADERS is on, because any client can send these headers directly.

    Order of preference:
    1. True-Client-IP / CF-Connecting-IP: set by Cloudflare, can't be faked through it.
    2. The LAST X-Forwarded-For entry: the one our proxy added. Never the first entry:
       Render appends to a client-supplied header, so the first one can be faked.
       (Worst case this is the proxy's own address and the limit becomes shared,
       which is stricter, never bypassable.)
    """
    if trust_proxy:
        lowered = {k.lower(): v for k, v in headers.items()}
        for name in ("true-client-ip", "cf-connecting-ip"):
            value = (lowered.get(name) or "").strip()
            if value:
                return value
        hops = [hop.strip() for hop in (lowered.get("x-forwarded-for") or "").split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return client_host or "unknown"

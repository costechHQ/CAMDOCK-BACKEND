"""In-process request rate limiting (fixed window, per client address).

State is per process and cleared by a restart. That fits a single API process; several
workers would each keep their own counters and need a shared store instead.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.errors import error_response

MAX_KEYS = 10_000
MESSAGE = "Too many requests. Try again later."


@dataclass
class _Window:
    started: float
    count: int


class RateLimiter:
    """Allows `limit` requests per key in each window of `window_seconds`."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int:
        """Count one request; returns 0 when allowed, else seconds until it may retry."""
        with self._lock:
            now = self._clock()
            window = self._windows.get(key)
            if window is None or now - window.started >= self._window:
                if window is None:
                    self._make_room(now)
                self._windows[key] = window = _Window(started=now, count=0)
            if window.count >= self._limit:
                return math.ceil(window.started + self._window - now)
            window.count += 1
            return 0

    def _make_room(self, now: float) -> None:
        if len(self._windows) < self._max_keys:
            return
        self._windows = {
            k: w for k, w in self._windows.items() if now - w.started < self._window
        }
        while len(self._windows) >= self._max_keys:
            del self._windows[next(iter(self._windows))]


class GlobalRateLimitMiddleware:
    """Applies the app-wide ceiling before routing, so a limited request does no work.

    The limiter is read from `app.state` on each request so every app instance (and
    every test) has its own counters.
    """

    def __init__(self, app: ASGIApp, exempt_paths: frozenset[str]) -> None:
        self.app = app
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        ip = client_ip(Request(scope), get_settings().trusted_proxy_networks)
        wait = scope["app"].state.global_limiter.hit(ip)
        if wait:
            response = error_response(
                429, "rate_limited", MESSAGE, headers={"Retry-After": str(wait)}
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

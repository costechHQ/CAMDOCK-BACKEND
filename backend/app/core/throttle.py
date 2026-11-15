"""In-process throttling of failed sign-ins.

State lives in memory, so it is per process and cleared by a restart. That fits a
single API process; a deployment with several workers would need a shared store.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_KEYS = 10_000


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    blocked_until: float = 0.0


class FailureThrottle:
    """Counts failures per key; the Nth failure inside the window blocks the key.

    Attempts made while a key is blocked are never recorded, so a block cannot be
    extended by someone else's guesses and always ends at the stated time.
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        block_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._block = block_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Whole seconds until the key may try again; 0 when it is not blocked."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0
            remaining = entry.blocked_until - self._clock()
            return math.ceil(remaining) if remaining > 0 else 0

    def record_failure(self, key: str) -> bool:
        """Record one failure; True when this failure started a block."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry.blocked_until > now:
                return False
            if entry is None:
                self._make_room(now)
                entry = self._entries[key] = _Entry()
            entry.failures = [t for t in entry.failures if now - t < self._window]
            entry.failures.append(now)
            if len(entry.failures) >= self._max_failures:
                entry.failures.clear()
                entry.blocked_until = now + self._block
                return True
            return False

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _make_room(self, now: float) -> None:
        if len(self._entries) < self._max_keys:
            return
        self._entries = {
            k: e
            for k, e in self._entries.items()
            if e.blocked_until > now or any(now - t < self._window for t in e.failures)
        }
        while len(self._entries) >= self._max_keys:
            del self._entries[next(iter(self._entries))]


class LoginThrottle:
    """Failed sign-ins are limited per email and, more loosely, per client address."""

    def __init__(self, by_email: FailureThrottle, by_ip: FailureThrottle) -> None:
        self._by_email = by_email
        self._by_ip = by_ip

    def retry_after(self, email: str, ip: str) -> int:
        return max(self._by_email.retry_after(email), self._by_ip.retry_after(ip))

    def record_failure(self, email: str, ip: str) -> bool:
        started_email = self._by_email.record_failure(email)
        started_ip = self._by_ip.record_failure(ip)
        return started_email or started_ip

    def record_success(self, email: str) -> None:
        self._by_email.reset(email)

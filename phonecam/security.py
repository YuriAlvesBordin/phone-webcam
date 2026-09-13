"""PIN authentication, per-IP rate limiting and Origin validation."""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlsplit

from phonecam.config import PIN_LENGTH


def generate_pin(length: int = PIN_LENGTH) -> str:
    """Cryptographically random numeric PIN (default 6 digits)."""
    lower = 10 ** (length - 1)
    return f"{secrets.randbelow(9 * lower) + lower:0{length}d}"


def pin_matches(guess: str, actual: str) -> bool:
    """Constant-time PIN comparison (timing side channels are not observable
    through the async handler, but compare_digest costs nothing)."""
    a, b = guess.encode(), actual.encode()
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


class RateLimiter:
    """Tracks failed attempts per key (IP) with a temporary lockout.

    ``clock`` is injectable for tests.
    """

    def __init__(
        self,
        max_failures: int,
        lockout_s: float,
        clock: object = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._lockout_s = lockout_s
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def lockout_remaining(self, key: str) -> float:
        """Seconds the key is still locked out for (0.0 if not locked)."""
        until = self._locked_until.get(key, 0.0)
        remaining = until - self._clock()
        if remaining <= 0:
            self._locked_until.pop(key, None)
            return 0.0
        return remaining

    def record_failure(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self._max_failures:
            self._locked_until[key] = self._clock() + self._lockout_s
            self._failures[key] = 0

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """Accept browser connections only from our own host.

    Empty/missing Origin means a non-browser client (curl, tests) - allowed.
    The hostname (not the port) must match the Host header of the request.
    """
    if not origin:
        return True
    try:
        origin_host = urlsplit(origin).hostname
        request_host = urlsplit(f"//{host_header or ''}").hostname
    except ValueError:
        return False
    if not origin_host or not request_host:
        return False
    return origin_host == request_host

"""Failed-login throttling.

argon2id costs about 95 ms per verification, and that is often mistaken for a
brute-force control. It is not: it is a price per attempt on one connection, and
an attacker opening fifty connections pays it fifty times in parallel. Measured
on this application before this module existed, twelve wrong passwords completed
in 1.14 seconds with nothing refused.

**Counted on two keys, not one.** Per account, so one address cannot be ground
down from anywhere; and per client, so one client cannot sweep many addresses.
Either key crossing the threshold refuses the attempt. A single key would leave
the other attack wide open.

**In memory, deliberately.** The alternative is a table, and a write per failed
login is a write an unauthenticated caller can force — which hands them the
database as an amplification target and, on SQLite, contends for the single
write lock. The cost is that the counters reset when the process restarts and
are per-process rather than shared. For a single-process deployment that is
exact; for several processes it weakens to per-process counting, which is why
`Retry-After` is also emitted so a proxy can enforce the same limit above.

A *successful* login clears the account's failures immediately. Someone who
mistypes twice and then gets it right is not one attempt from a lockout.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from app.config import Settings


@dataclass
class _Bucket:
    """Recent failure timestamps for one key, and when its lockout ends."""

    failures: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


@dataclass(frozen=True)
class ThrottleDecision:
    """Whether an attempt may proceed, and how long until one can."""

    allowed: bool
    retry_after_seconds: int = 0


class LoginThrottle:
    """Sliding-window failure counter with a lockout, keyed by arbitrary strings.

    Not a general rate limiter: it counts *failures*, so a user signing in
    correctly all day is never throttled however often they do it.
    """

    def __init__(self, *, max_attempts: int, window_seconds: int, lockout_seconds: int) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._buckets: dict[str, _Bucket] = {}
        # Every public method touches the shared dict, and FastAPI serves sync
        # endpoints from a threadpool, so this is contended in practice.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ read
    def check(self, keys: list[str], *, now: float | None = None) -> ThrottleDecision:
        """May an attempt with these keys proceed?"""
        moment = time.monotonic() if now is None else now
        with self._lock:
            worst = 0.0
            for key in keys:
                bucket = self._buckets.get(key)
                if bucket is not None and bucket.locked_until > moment:
                    worst = max(worst, bucket.locked_until - moment)
            if worst > 0:
                # Rounded up: a client told to wait 0 seconds retries immediately
                # and is refused again, which reads as a broken endpoint.
                return ThrottleDecision(
                    allowed=False, retry_after_seconds=max(1, int(worst + 0.999))
                )
        return ThrottleDecision(allowed=True)

    # ----------------------------------------------------------------- write
    def record_failure(self, keys: list[str], *, now: float | None = None) -> None:
        """Count one failed attempt against every key, locking out if over."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            for key in keys:
                bucket = self._buckets.setdefault(key, _Bucket())
                cutoff = moment - self._window
                while bucket.failures and bucket.failures[0] < cutoff:
                    bucket.failures.popleft()
                bucket.failures.append(moment)
                if len(bucket.failures) >= self._max:
                    bucket.locked_until = moment + self._lockout
                    bucket.failures.clear()

    def record_success(self, keys: list[str]) -> None:
        """Forget this key's failures. A correct password ends the suspicion."""
        with self._lock:
            for key in keys:
                self._buckets.pop(key, None)

    # ------------------------------------------------------------ management
    def reset(self) -> None:
        """Drop every counter. For tests, and for an operator unlocking an account."""
        with self._lock:
            self._buckets.clear()

    def prune(self, *, now: float | None = None) -> int:
        """Drop buckets that are neither locked nor holding a recent failure.

        Without this the dict grows once per distinct address ever tried, which
        an attacker controls. Called on each failure path, so the work is paid
        by the caller causing it.
        """
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._window
        with self._lock:
            stale = [
                key
                for key, bucket in self._buckets.items()
                if bucket.locked_until <= moment
                and (not bucket.failures or bucket.failures[-1] < cutoff)
            ]
            for key in stale:
                del self._buckets[key]
            return len(stale)

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._buckets)


_throttle: LoginThrottle | None = None
_throttle_lock = threading.Lock()


def get_login_throttle(settings: Settings) -> LoginThrottle:
    """The process-wide throttle, built once from settings.

    A module-level singleton rather than application state because the counters
    have to outlive any one request and be shared by every worker thread in the
    process. `reset_login_throttle` exists so a test starts from zero.
    """
    global _throttle
    if _throttle is None:
        with _throttle_lock:
            if _throttle is None:
                _throttle = LoginThrottle(
                    max_attempts=settings.login_max_attempts,
                    window_seconds=settings.login_attempt_window_seconds,
                    lockout_seconds=settings.login_lockout_seconds,
                )
    return _throttle


def reset_login_throttle() -> None:
    """Discard the singleton so the next call rebuilds it from current settings."""
    global _throttle
    with _throttle_lock:
        _throttle = None


def keys_for(email: str, client_ip: str | None) -> list[str]:
    """The two keys an attempt counts against.

    The address is folded the same way the account lookup folds it, so
    ``Admin@X`` and ``admin@x`` are one account here as they are there.
    """
    keys = [f"user:{email.strip().lower()}"]
    if client_ip:
        keys.append(f"ip:{client_ip}")
    return keys

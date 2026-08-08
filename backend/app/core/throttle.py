"""Throttling: failed sign-ins, and expensive authenticated work.

Two counters over one mechanism.

**`LoginThrottle` counts failures.** argon2id costs about 95 ms per
verification, and that is often mistaken for a brute-force control. It is not:
it is a price per attempt on one connection, and an attacker opening fifty
connections pays it fifty times in parallel. Measured on this application before
this module existed, twelve wrong passwords completed in 1.14 seconds with
nothing refused. Counted on two keys — per account, so one address cannot be
ground down from anywhere, and per client, so one client cannot sweep many
addresses. A *successful* login clears the account's failures immediately.

**`RateLimiter` counts everything.** Sign-in is not the only request that costs
more than it looks. An import parses a spreadsheet, a sync walks Shopify for
minutes, a rebuild recomputes the rollup, a report renders up to 50,000 rows —
and all four run on the single worker thread that SQLite's one-writer limit
already forces. An authenticated user, or a retry loop in a browser tab nobody
is watching, can queue enough of them to make the application unresponsive
without anything looking like an attack. This counts *admissions* rather than
failures: the work is charged for whether or not it succeeds.

.. rubric:: One process, and what that costs

**Both counters live in this process's memory, deliberately.** The alternative
is a table, and a write per refused attempt is a write an unauthenticated caller
can force — which hands them the database as an amplification target and, on
SQLite, contends for the single write lock.

The consequence is a real deployment constraint, not a detail:

* Counters **reset on restart**, so a deploy grants everyone a fresh budget.
* Counters are **per process**. Run uvicorn with ``--workers 2`` and every limit
  doubles, silently. The systemd unit in ``deploy/`` runs one worker for this
  reason, along with the job runner and SQLite's single writer — all three
  assume one process, and none of them says so at runtime.

`Retry-After` is emitted on every refusal so a reverse proxy can enforce the
same limit above the application, which is the mitigation until the store below
is shared.

.. rubric:: Replacing the store

`ThrottleStore` is the whole seam. It is deliberately small — get, put, drop,
sweep — because those four map onto a Redis hash with a TTL without inventing
anything, and onto nothing else this module would need. A `RedisThrottleStore`
implementing it makes both counters shared and restart-proof, and no caller
changes: `LoginThrottle` and `RateLimiter` take a store and never look inside
it. Locking is the store's business too, so a Redis implementation can drop the
mutex the in-memory one needs.

Nothing here imports Redis, and nothing should until a second process exists.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings


@dataclass
class _Bucket:
    """Recent event timestamps for one key, and when its lockout ends.

    "Event" rather than "failure": the login counter records failed attempts,
    the rate limiter records admissions. The bookkeeping is identical.
    """

    events: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


@dataclass(frozen=True)
class ThrottleDecision:
    """Whether an attempt may proceed, and how long until one can."""

    allowed: bool
    retry_after_seconds: int = 0


class ThrottleStore(Protocol):
    """Where counters live.

    Four operations, chosen so a Redis implementation is a translation rather
    than a redesign: `get`/`put` are a hash read and write, `drop` is DEL, and
    `sweep` is what a TTL does for free. Callers never hold a bucket across
    calls, so a store is free to serialise, expire or shard them.
    """

    def get(self, key: str) -> _Bucket | None: ...

    def put(self, key: str, bucket: _Bucket) -> None: ...

    def drop(self, keys: list[str]) -> None: ...

    def sweep(self, *, now: float, window: int) -> int: ...

    def clear(self) -> None: ...

    def __len__(self) -> int: ...


class InMemoryThrottleStore:
    """The only implementation, and the reason the module needs a lock.

    FastAPI serves sync endpoints from a threadpool, so this dict is genuinely
    contended. A shared store would move the locking to the store's backend and
    this class would simply not be used.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> _Bucket | None:
        with self._lock:
            return self._buckets.get(key)

    def put(self, key: str, bucket: _Bucket) -> None:
        with self._lock:
            self._buckets[key] = bucket

    def drop(self, keys: list[str]) -> None:
        with self._lock:
            for key in keys:
                self._buckets.pop(key, None)

    def sweep(self, *, now: float, window: int) -> int:
        """Drop buckets that are neither locked nor holding a recent event.

        Without this the dict grows once per distinct key ever seen, which for
        the login counter an attacker controls. A Redis store would set a TTL
        instead and make this a no-op.
        """
        cutoff = now - window
        with self._lock:
            stale = [
                key
                for key, bucket in self._buckets.items()
                if bucket.locked_until <= now and (not bucket.events or bucket.events[-1] < cutoff)
            ]
            for key in stale:
                del self._buckets[key]
            return len(stale)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


class LoginThrottle:
    """Sliding-window failure counter with a lockout, keyed by arbitrary strings.

    Not a general rate limiter: it counts *failures*, so a user signing in
    correctly all day is never throttled however often they do it.
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
        store: ThrottleStore | None = None,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._store = store or InMemoryThrottleStore()

    # ------------------------------------------------------------------ read
    def check(self, keys: list[str], *, now: float | None = None) -> ThrottleDecision:
        """May an attempt with these keys proceed?"""
        moment = time.monotonic() if now is None else now
        worst = 0.0
        for key in keys:
            bucket = self._store.get(key)
            if bucket is not None and bucket.locked_until > moment:
                worst = max(worst, bucket.locked_until - moment)
        if worst > 0:
            # Rounded up: a client told to wait 0 seconds retries immediately
            # and is refused again, which reads as a broken endpoint.
            return ThrottleDecision(allowed=False, retry_after_seconds=max(1, int(worst + 0.999)))
        return ThrottleDecision(allowed=True)

    # ----------------------------------------------------------------- write
    def record_failure(self, keys: list[str], *, now: float | None = None) -> None:
        """Count one failed attempt against every key, locking out if over."""
        moment = time.monotonic() if now is None else now
        for key in keys:
            bucket = self._store.get(key) or _Bucket()
            cutoff = moment - self._window
            while bucket.events and bucket.events[0] < cutoff:
                bucket.events.popleft()
            bucket.events.append(moment)
            if len(bucket.events) >= self._max:
                bucket.locked_until = moment + self._lockout
                bucket.events.clear()
            self._store.put(key, bucket)

    def record_success(self, keys: list[str]) -> None:
        """Forget this key's failures. A correct password ends the suspicion."""
        self._store.drop(keys)

    # ------------------------------------------------------------ management
    def reset(self) -> None:
        """Drop every counter. For tests, and for an operator unlocking an account."""
        self._store.clear()

    def prune(self, *, now: float | None = None) -> int:
        """Drop buckets that are neither locked nor holding a recent failure.

        Called on each failure path, so the work is paid by the caller causing
        it — see `InMemoryThrottleStore.sweep`.
        """
        moment = time.monotonic() if now is None else now
        return self._store.sweep(now=moment, window=self._window)

    @property
    def tracked(self) -> int:
        return len(self._store)


class RateLimiter:
    """N admissions per key per window, then refuse until the window drains.

    Different from `LoginThrottle` in the two ways that matter:

    * It counts **every** admission, not failures. The point is the cost of the
      work, and a successful import costs more than a rejected one, not less.
    * There is **no lockout period**. Crossing the limit is not evidence of an
      attack, it is a busy user — so the window simply has to drain, and
      `Retry-After` is how long until the oldest event ages out. A fixed
      lockout would punish a burst long after the load it caused was gone.
    """

    def __init__(
        self, *, max_events: int, window_seconds: int, store: ThrottleStore | None = None
    ) -> None:
        self._max = max_events
        self._window = window_seconds
        self._store = store or InMemoryThrottleStore()

    def check(self, key: str, *, now: float | None = None) -> ThrottleDecision:
        """Would an admission be allowed? Does not consume one."""
        moment = time.monotonic() if now is None else now
        bucket = self._store.get(key)
        if bucket is None:
            return ThrottleDecision(allowed=True)

        recent = [event for event in bucket.events if event >= moment - self._window]
        if len(recent) < self._max:
            return ThrottleDecision(allowed=True)
        # The oldest event in the window is the one whose expiry frees a slot.
        wait = recent[0] + self._window - moment
        return ThrottleDecision(allowed=False, retry_after_seconds=max(1, int(wait + 0.999)))

    def admit(self, key: str, *, now: float | None = None) -> ThrottleDecision:
        """Check and, if allowed, consume one slot.

        One call rather than check-then-record so two threads cannot both pass
        a check before either records. The in-memory store locks per operation
        rather than across them, so this is the narrowest correct unit.
        """
        moment = time.monotonic() if now is None else now
        bucket = self._store.get(key) or _Bucket()

        cutoff = moment - self._window
        while bucket.events and bucket.events[0] < cutoff:
            bucket.events.popleft()

        if len(bucket.events) >= self._max:
            wait = bucket.events[0] + self._window - moment
            # Written back so the trimming above is not repeated on every
            # refused call while a caller hammers the endpoint.
            self._store.put(key, bucket)
            return ThrottleDecision(allowed=False, retry_after_seconds=max(1, int(wait + 0.999)))

        bucket.events.append(moment)
        self._store.put(key, bucket)
        return ThrottleDecision(allowed=True)

    def reset(self) -> None:
        self._store.clear()

    def prune(self, *, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        return self._store.sweep(now=moment, window=self._window)

    @property
    def tracked(self) -> int:
        return len(self._store)


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


#: The expensive operations, and how many one user may start per window.
#:
#: Every one of these runs on the single worker thread, so these are not
#: fairness quotas — they are what stops one user, or one retrying browser tab,
#: from filling a queue everyone else is behind. The numbers are set well above
#: deliberate use and well below a loop: nobody imports six spreadsheets in five
#: minutes on purpose.
LIMITED_OPERATIONS = ("import", "sync", "rebuild", "report")

_rate_limiters: dict[str, RateLimiter] = {}
_rate_lock = threading.Lock()


def get_rate_limiter(settings: Settings, operation: str) -> RateLimiter:
    """The process-wide limiter for one operation, built once from settings."""
    limiter = _rate_limiters.get(operation)
    if limiter is None:
        with _rate_lock:
            limiter = _rate_limiters.get(operation)
            if limiter is None:
                limiter = RateLimiter(
                    max_events=settings.rate_limit_max_events,
                    window_seconds=settings.rate_limit_window_seconds,
                )
                _rate_limiters[operation] = limiter
    return limiter


def reset_rate_limiters() -> None:
    """Discard every limiter so the next call rebuilds from current settings."""
    with _rate_lock:
        _rate_limiters.clear()


def rate_key(operation: str, *, workspace_id: int, user_id: int | None) -> str:
    """Per user, and scoped to the workspace.

    The workspace is in the key because the contention being managed is the
    worker thread and the SQLite writer, both of which are per deployment — so
    a key that collided across workspaces would let one tenant's limit be spent
    by another's traffic.
    """
    return f"{operation}:{workspace_id}:{user_id or 0}"


def keys_for(email: str, client_ip: str | None) -> list[str]:
    """The two keys an attempt counts against.

    The address is folded the same way the account lookup folds it, so
    ``Admin@X`` and ``admin@x`` are one account here as they are there.
    """
    keys = [f"user:{email.strip().lower()}"]
    if client_ip:
        keys.append(f"ip:{client_ip}")
    return keys

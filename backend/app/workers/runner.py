"""A background job runner.

Plan §6 calls for this in M3 so M4's matching has somewhere to run. It is
deliberately the smallest thing that satisfies the requirement:

**One worker thread, not a pool.** SQLite allows a single writer, so two jobs
writing concurrently would not run twice as fast — they would serialise inside
the database while holding connections open, and a user saving a setting would
queue behind both. A single worker makes that serialisation explicit and keeps
each transaction short.

**A thread, not Celery or APScheduler.** A broker buys distributed workers and
retries that a single-instance internal tool does not need, and costs a service
to run and monitor. A thread inside the API process matches the deployment
constraint SQLite already imposes (plan §4.5, Q13). When that constraint stops
holding, this is the seam to replace.

**No in-memory job state.** Progress lives in the ``sync_runs`` row, not here,
so a poll for progress is a database read and a restart mid-job leaves a
visible record rather than a job that silently never existed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger(__name__)

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
#: Set by the test suite to run jobs inline, so a test never races a thread.
_run_inline = False


def configure(*, inline: bool = False) -> None:
    """Choose inline or threaded execution. Called once at start-up."""
    global _run_inline
    _run_inline = inline


def submit(job: Callable[[], None], *, name: str) -> Future[None] | None:
    """Queue a job. Returns None when running inline.

    Exceptions are logged rather than raised: the caller is an HTTP request
    that has already returned, and a job's own error handling is responsible
    for recording the failure where the user will see it.
    """
    if _run_inline:
        _safely(job, name)
        return None

    executor = _get_executor()
    return executor.submit(_safely, job, name)


def _safely(job: Callable[[], None], name: str) -> None:
    try:
        job()
    except Exception:  # a worker thread must not die silently
        log.exception("background job %r failed", name)


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stocksync-job")
        return _executor


def shutdown(wait: bool = True) -> None:
    """Stop the worker. Called from the app's lifespan teardown."""
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait)
            _executor = None

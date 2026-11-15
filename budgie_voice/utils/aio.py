"""Dispatch coroutines without losing their exceptions."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import Any, Coroutine

_LOG = logging.getLogger(__name__)


def log_failure(fut: Any) -> None:
    """Done-callback for a Task or concurrent Future: log its exception, if any."""
    if not fut.cancelled() and fut.exception() is not None:
        _LOG.error("Background coroutine failed", exc_info=fut.exception())


def dispatch(coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> Future:
    """``run_coroutine_threadsafe`` whose failure is logged instead of vanishing."""
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    fut.add_done_callback(log_failure)
    return fut

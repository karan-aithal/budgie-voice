"""Turns session state changes into history rows. Plain Python, no Qt.

Feed it every SessionState change with the two typed-text counters from the DictationService. It
opens a record when recording starts and saves exactly one row when that session ends. A session
that never reached recording (start failure, secure-field block) has no record, so nothing is saved
for it, and a repeated terminal callback finds nothing open and does nothing.

Duration is monotonic recording time (recording until stop is pressed, or until cancel/error).
Mic open, model load, LLM, preview wait and time in Settings are all outside it.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from budgie_voice.analytics.store import HistoryStore, SessionRecord

_LOG = logging.getLogger(__name__)

_STATUS = {"completed": "completed", "cancelled": "cancelled", "error": "failed"}


def _local_now() -> datetime:
    return datetime.now().astimezone()  # aware, in the machine's current zone (DST included)


@dataclass
class _Open:
    id: str
    started_at: datetime
    t0: float
    stop: float | None = None


class SessionRecorder:
    """Args:
        store: Where finished sessions go.
        clock: Aware local "now" (injectable for tests). Fixes ``started_at`` and the local day.
        monotonic: Elapsed-time clock (injectable for tests).
    """

    def __init__(
        self,
        store: HistoryStore,
        clock: Callable[[], datetime] = _local_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._clock = clock
        self._mono = monotonic
        self._open: _Open | None = None

    def on_state(self, state: str, typed_chars: int, typed_words: int) -> None:
        """Never raises: this runs inside the session's state-callback chain."""
        try:
            self._on_state(state, typed_chars, typed_words)
        except Exception as exc:  # noqa: BLE001
            self._open = None
            _LOG.warning("session history skipped (%s)", type(exc).__name__)

    def _on_state(self, state: str, chars: int, words: int) -> None:
        if state == "recording":
            # An unfinished record here means a terminal state was missed: drop it, never invent its end.
            self._open = _Open(uuid.uuid4().hex, self._clock(), self._mono())
            return
        cur = self._open
        if state in ("finalizing", "transcribing"):
            if cur is not None and cur.stop is None:
                cur.stop = self._mono()
            return
        status = _STATUS.get(state)
        if status is None or cur is None:  # idle/starting, or no recording happened, or a repeat
            return
        self._open = None
        started = cur.started_at
        self._store.add(SessionRecord(
            id=cur.id,
            started_at=started.astimezone(timezone.utc),
            local_date=started.date(),
            duration_ms=max(0, round(((cur.stop if cur.stop is not None else self._mono()) - cur.t0) * 1000)),
            words=max(0, words),
            chars=max(0, chars),
            status=status,
        ))

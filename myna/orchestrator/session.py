"""SessionState enum + DictationSession state machine.

Lifecycle::

    IDLE → STARTING → RECORDING → TRANSCRIBING → FINALIZING → COMPLETED → IDLE
    Any active state → CANCELLED → IDLE
    Any state        → ERROR     → IDLE
"""
from __future__ import annotations

import logging
from enum import StrEnum

from myna.utils.exceptions import MynaSessionError

_LOG = logging.getLogger(__name__)


class SessionState(StrEnum):
    """All possible dictation session states."""

    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


_ACTIVE = frozenset(
    {
        SessionState.STARTING,
        SessionState.RECORDING,
        SessionState.TRANSCRIBING,
        SessionState.FINALIZING,
    }
)


class DictationSession:
    """Pure state machine for a single dictation session.

    Does NOT process audio or run inference.
    """

    def __init__(self) -> None:
        self._state = SessionState.IDLE
        self._target_window_id: int | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def target_window_id(self) -> int | None:
        return self._target_window_id

    @property
    def is_active(self) -> bool:
        return self._state in _ACTIVE

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    async def to_starting(self, window_id: int | None) -> None:
        """IDLE → STARTING; locks target window."""
        self._require(SessionState.IDLE)
        self._target_window_id = window_id
        await self._emit(SessionState.STARTING, window_id=window_id)

    async def to_recording(self) -> None:
        """STARTING → RECORDING."""
        self._require(SessionState.STARTING)
        await self._emit(SessionState.RECORDING)

    async def to_finalizing(self) -> None:
        """RECORDING | TRANSCRIBING → FINALIZING."""
        if self._state not in (SessionState.RECORDING, SessionState.TRANSCRIBING):
            raise MynaSessionError(
                f"Cannot finalize from state {self._state!r}"
            )
        await self._emit(SessionState.FINALIZING)

    async def to_completed(self) -> None:
        """FINALIZING → COMPLETED → IDLE (automatic)."""
        self._require(SessionState.FINALIZING)
        await self._emit(SessionState.COMPLETED)
        await self._emit(SessionState.IDLE)

    async def to_cancelled(self) -> None:
        """Any active state → CANCELLED → IDLE."""
        if self._state not in _ACTIVE:
            return
        await self._emit(SessionState.CANCELLED)
        await self._emit(SessionState.IDLE)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require(self, expected: SessionState) -> None:
        if self._state != expected:
            raise MynaSessionError(
                f"Expected state {expected!r}, got {self._state!r}"
            )

    async def _emit(self, state: SessionState, **kwargs: object) -> None:
        self._state = state
        _LOG.debug("Session state → %s", state)

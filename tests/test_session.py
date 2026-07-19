"""Phase 3 tests — DictationSession state machine."""
from __future__ import annotations

import asyncio

import pytest

from myna.orchestrator.session import DictationSession, SessionState
from myna.utils.exceptions import MynaSessionError


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idle_to_starting() -> None:
    session = DictationSession()
    assert session.state == SessionState.IDLE
    await session.to_starting(12345)
    assert session.state == SessionState.STARTING


@pytest.mark.asyncio
async def test_full_happy_path() -> None:
    """IDLE → STARTING → RECORDING → FINALIZING → COMPLETED → IDLE"""
    session = DictationSession()
    await session.to_starting(None)
    await session.to_recording()
    await session.to_finalizing()
    await session.to_completed()

    assert session.state == SessionState.IDLE
    assert not session.is_active


@pytest.mark.asyncio
async def test_cancel_from_recording() -> None:
    session = DictationSession()
    await session.to_starting(None)
    await session.to_recording()
    assert session.is_active

    await session.to_cancelled()
    assert session.state == SessionState.IDLE
    assert not session.is_active


@pytest.mark.asyncio
async def test_cancel_from_idle_is_noop() -> None:
    session = DictationSession()
    await session.to_cancelled()  # must not raise
    assert session.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_starting_from_non_idle_raises() -> None:
    session = DictationSession()
    await session.to_starting(None)  # IDLE → STARTING
    with pytest.raises(MynaSessionError):
        await session.to_starting(None)  # already STARTING → raise


@pytest.mark.asyncio
async def test_recording_before_starting_raises() -> None:
    session = DictationSession()
    with pytest.raises(MynaSessionError):
        await session.to_recording()


@pytest.mark.asyncio
async def test_finalizing_from_idle_raises() -> None:
    session = DictationSession()
    with pytest.raises(MynaSessionError):
        await session.to_finalizing()


@pytest.mark.asyncio
async def test_completed_without_finalizing_raises() -> None:
    session = DictationSession()
    await session.to_starting(None)
    await session.to_recording()
    with pytest.raises(MynaSessionError):
        await session.to_completed()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_active_true_in_recording() -> None:
    session = DictationSession()
    await session.to_starting(None)
    await session.to_recording()
    assert session.is_active


@pytest.mark.asyncio
async def test_target_window_id_locked() -> None:
    session = DictationSession()
    await session.to_starting(99999)
    assert session.target_window_id == 99999
    await session.to_recording()
    # Window ID must not change after STARTING
    assert session.target_window_id == 99999

"""Tests for audio/capture.py — Phase 1 happy path."""

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from assistant.audio.capture import AudioCapture


async def test_callback_enqueues_mono_chunk() -> None:
    """Callback must push a flat float32 array (not 2-D) into the queue."""
    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(queue=queue, sample_rate=16_000, chunk_duration_ms=30)
    capture._loop = asyncio.get_running_loop()

    # sounddevice passes shape (frames, channels)
    fake_indata = np.zeros((480, 1), dtype=np.float32)
    fake_indata[:, 0] = 0.5

    capture._callback(fake_indata, 480, None, None)

    # call_soon_threadsafe schedules on next loop iteration → yield once
    await asyncio.sleep(0)

    chunk = queue.get_nowait()
    assert chunk.ndim == 1
    assert chunk.dtype == np.float32
    assert chunk.shape == (480,)
    assert float(chunk[0]) == pytest.approx(0.5)


async def test_start_stop_lifecycle() -> None:
    """start() opens a stream; stop() closes it and clears the reference."""
    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(queue=queue, sample_rate=16_000, chunk_duration_ms=30)

    mock_stream = MagicMock()

    with patch("sounddevice.InputStream", return_value=mock_stream):
        await capture.start()
        assert capture._stream is not None
        mock_stream.start.assert_called_once()

        await capture.stop()
        assert capture._stream is None
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()


async def test_stop_without_start_is_safe() -> None:
    """Calling stop() before start() must not raise."""
    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(queue=queue)
    await capture.stop()  # should be a no-op


async def test_chunk_size_matches_duration() -> None:
    """chunk_size must equal sample_rate × duration_ms / 1000."""
    capture = AudioCapture(
        queue=asyncio.Queue(),
        sample_rate=16_000,
        chunk_duration_ms=30,
    )
    assert capture._chunk_size == 480

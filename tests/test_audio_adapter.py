"""Phase 1 tests — AudioAdapter, VADFilter, protocol types."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from myna.audio.adapter import AudioAdapter
from myna.audio.vad import VADFilter
from myna.config.settings import AudioSettings, VadSettings
from myna.inference.protocol import AudioFrame, TranscriptSegment


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_audio_frame_defaults() -> None:
    frame = AudioFrame(data=b"\x00" * 64)
    assert frame.sample_rate == 16_000
    assert frame.channels == 1
    assert frame.format == "pcm16"
    assert frame.is_end_of_speech is False


def test_transcript_segment_fields() -> None:
    seg = TranscriptSegment(text="hello", confidence=0.9, is_final=True, language="en")
    assert seg.text == "hello"
    assert seg.is_final is True


# ---------------------------------------------------------------------------
# VADFilter — passthrough mode (before load())
# ---------------------------------------------------------------------------

def test_vad_passthrough_before_load() -> None:
    vad = VADFilter(aggressiveness=2)
    chunk = np.zeros(512, dtype=np.float32)
    assert vad.is_speech(chunk) is True  # passthrough


def test_vad_process_passthrough() -> None:
    vad = VADFilter(aggressiveness=2, speech_pad_ms=100, sample_rate=16_000)
    chunk = np.ones(480, dtype=np.float32) * 0.1
    output, is_end = vad.process(chunk)
    # First speech chunk: forward, not end
    assert output is not None
    assert is_end is False


def test_vad_pad_expiry() -> None:
    """Silence after speech triggers end-of-speech once pad expires."""
    vad = VADFilter(aggressiveness=2, speech_pad_ms=60, sample_rate=16_000)
    # pad_samples = 16000 * 60 / 1000 = 960

    speech = np.ones(512, dtype=np.float32)
    silence = np.zeros(512, dtype=np.float32)

    # Enter speech state
    vad.process(speech)

    # Feed silence until pad expires
    ended = False
    for _ in range(10):
        out, is_end = vad.process(silence)
        if is_end:
            ended = True
            break

    assert ended


# ---------------------------------------------------------------------------
# AudioAdapter — mocked sounddevice
# ---------------------------------------------------------------------------

def test_adapter_open_close() -> None:
    loop = asyncio.new_event_loop()
    a_settings = AudioSettings(sample_rate=16_000, chunk_duration_ms=30, denoise=False)
    v_settings = VadSettings(enabled=False)

    with patch("sounddevice.InputStream") as mock_cls:
        inst = MagicMock()
        mock_cls.return_value = inst
        adapter = AudioAdapter(a_settings, v_settings, loop)
        adapter.open()
        inst.start.assert_called_once()
        adapter.close()
        inst.stop.assert_called_once()
        inst.close.assert_called_once()

    loop.close()


@pytest.mark.asyncio
async def test_adapter_emits_frames_when_vad_disabled() -> None:
    """Adapter with VAD disabled should forward all chunks as AudioFrames."""
    loop = asyncio.get_running_loop()
    a_settings = AudioSettings(sample_rate=16_000, chunk_duration_ms=30, denoise=False)
    v_settings = VadSettings(enabled=False)

    with patch("sounddevice.InputStream") as mock_cls:
        inst = MagicMock()
        mock_cls.return_value = inst
        adapter = AudioAdapter(a_settings, v_settings, loop)
        adapter.open()

        # Manually push a chunk into the capture queue
        chunk = (np.ones(480, dtype=np.float32) * 0.5)
        adapter._capture.queue.put_nowait(chunk)  # type: ignore[attr-defined]

        frames: list[AudioFrame] = []
        async for frame in adapter.stream():
            frames.append(frame)
            break  # one frame is enough

        assert len(frames) == 1
        assert frames[0].sample_rate == 16_000
        adapter.close()

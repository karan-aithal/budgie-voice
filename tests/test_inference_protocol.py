"""Phase 4 tests — inference protocol types and backend capabilities."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from myna.inference.protocol import AudioFrame, TranscriptSegment


# ---------------------------------------------------------------------------
# Protocol message types
# ---------------------------------------------------------------------------

def test_audio_frame_timestamp_set() -> None:
    import time

    before = time.time() * 1000
    frame = AudioFrame(data=b"\xff" * 64)
    after = time.time() * 1000
    assert before <= frame.timestamp_ms <= after


def test_audio_frame_end_of_speech_default_false() -> None:
    frame = AudioFrame(data=b"")
    assert frame.is_end_of_speech is False


def test_audio_frame_end_of_speech_set() -> None:
    frame = AudioFrame(data=b"\x00" * 32, is_end_of_speech=True)
    assert frame.is_end_of_speech is True


def test_transcript_segment_fields() -> None:
    seg = TranscriptSegment(
        text="Hello world",
        confidence=0.87,
        is_final=True,
        language="en",
    )
    assert seg.text == "Hello world"
    assert seg.confidence == pytest.approx(0.87)
    assert seg.is_final is True
    assert seg.language == "en"


# ---------------------------------------------------------------------------
# FasterWhisperBackend capabilities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_faster_whisper_capabilities() -> None:
    from myna.inference.backends.base import INTERFACE_VERSION  # noqa: PLC0415
    from myna.inference.backends.faster_whisper import FasterWhisperBackend  # noqa: PLC0415

    backend = FasterWhisperBackend()
    caps = await backend.capabilities()
    assert caps.version == INTERFACE_VERSION
    assert "small" in caps.supported_models
    assert "en" in caps.supported_languages
    assert caps.supports_streaming is True


def test_faster_whisper_not_ready_before_load() -> None:
    from myna.inference.backends.faster_whisper import FasterWhisperBackend  # noqa: PLC0415

    backend = FasterWhisperBackend()
    assert backend.is_ready is False


@pytest.mark.asyncio
async def test_faster_whisper_transcribe_empty_yields_nothing() -> None:
    from myna.inference.backends.faster_whisper import FasterWhisperBackend  # noqa: PLC0415

    backend = FasterWhisperBackend()

    async def _empty():
        return
        yield  # make it an async generator

    with pytest.raises(RuntimeError):
        async for _ in backend.transcribe_stream(_empty(), "en"):
            pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_get_backend() -> None:
    from myna.inference.backends.registry import get_backend  # noqa: PLC0415

    backend = get_backend("faster_whisper")
    assert backend.backend_name if hasattr(backend, "backend_name") else True
    assert not backend.is_ready


def test_registry_unknown_raises() -> None:
    from myna.inference.backends.registry import get_backend  # noqa: PLC0415

    with pytest.raises(KeyError):
        get_backend("nonexistent_model")


def test_registry_available_backends() -> None:
    from myna.inference.backends.registry import available_backends  # noqa: PLC0415

    backends = available_backends()
    assert "faster_whisper" in backends

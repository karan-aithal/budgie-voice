"""Phase 4 tests — inference protocol types and backend capabilities."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from myna.inference.protocol import (
    AudioFrame,
    CapabilitiesResponse,
    ChannelType,
    ControlMessage,
    TranscriptSegment,
)


# ---------------------------------------------------------------------------
# Protocol message types
# ---------------------------------------------------------------------------

def test_audio_frame_uuid_unique() -> None:
    f1 = AudioFrame(data=b"\x00" * 32)
    f2 = AudioFrame(data=b"\x00" * 32)
    assert f1.frame_id != f2.frame_id


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
        segment_id="abc-123",
    )
    assert seg.text == "Hello world"
    assert seg.confidence == pytest.approx(0.87)
    assert seg.is_final is True
    assert seg.language == "en"


def test_control_message_default_version() -> None:
    msg = ControlMessage(action="capabilities", payload={})
    assert msg.version == "1.0"


def test_capabilities_response() -> None:
    cap = CapabilitiesResponse(
        version="1.0",
        backends=["faster_whisper"],
        languages=["en", "fr"],
        models=["small", "medium"],
    )
    assert "faster_whisper" in cap.backends
    assert "en" in cap.languages


def test_channel_type_values() -> None:
    assert ChannelType.CONTROL == "control"
    assert ChannelType.DATA == "data"


# ---------------------------------------------------------------------------
# FasterWhisperBackend capabilities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_faster_whisper_capabilities() -> None:
    from myna.inference.backends.faster_whisper import FasterWhisperBackend  # noqa: PLC0415

    backend = FasterWhisperBackend()
    caps = await backend.capabilities()
    assert caps.version == "1.0"
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
    from myna.utils.exceptions import MynaInferenceError  # noqa: PLC0415

    backend = FasterWhisperBackend()

    async def _empty():
        return
        yield  # make it an async generator

    with pytest.raises(MynaInferenceError):
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

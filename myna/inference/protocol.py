"""Versioned wire-protocol message types for Myna inference channels.

Control Channel: HTTP GET /capabilities  → CapabilitiesResponse
Data Channel   : WebSocket /ws/transcribe → AudioFrame UP, TranscriptSegment DOWN
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class ChannelType(StrEnum):
    """Inference channel type identifier."""

    CONTROL = "control"
    DATA = "data"


@dataclass
class AudioFrame:
    """Single audio chunk flowing UP the Data Channel.

    Attributes:
        data: Raw PCM bytes (int16 LE or float32 LE — see *format*).
        sample_rate: Samples per second (16000 default).
        channels: Channel count (always 1 — mono).
        format: Encoding — ``"pcm16"`` or ``"float32"``.
        timestamp_ms: Wall-clock milliseconds at capture time.
        frame_id: UUID for deduplication / tracing.
        is_end_of_speech: Hint from AudioAdapter; inference backend decides
            final segmentation independently.
    """

    data: bytes
    sample_rate: int = 16_000
    channels: int = 1
    format: str = "pcm16"
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    frame_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_end_of_speech: bool = False


@dataclass
class TranscriptSegment:
    """Committed text segment flowing DOWN the Data Channel.

    Attributes:
        text: Recognised text (may be partial if ``is_final=False``).
        confidence: Normalised confidence in [0.0, 1.0].
        is_final: ``True`` = committed; ``False`` = live partial hypothesis.
        language: BCP-47 language tag detected by the model.
        segment_id: UUID for deduplication.
    """

    text: str
    confidence: float
    is_final: bool
    language: str
    segment_id: str


@dataclass
class ControlMessage:
    """Message on the Control Channel.

    Attributes:
        action: One of ``"configure"``, ``"capabilities"``, ``"reset"``.
        payload: Action-specific key/value pairs.
        version: Protocol version string.
    """

    action: str
    payload: dict
    version: str = "1.0"


@dataclass
class CapabilitiesResponse:
    """Response to a Control Channel capabilities query.

    Attributes:
        version: Server protocol version.
        backends: Available backend identifiers.
        languages: Supported BCP-47 language tags.
        models: Available model names.
    """

    version: str
    backends: list[str]
    languages: list[str]
    models: list[str]

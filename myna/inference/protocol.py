"""Versioned wire-protocol message types for Myna inference channels.

Data Channel: WebSocket /ws/transcribe → AudioFrame UP, TranscriptSegment DOWN
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AudioFrame:
    """Single audio chunk flowing UP the Data Channel.

    Attributes:
        data: Raw PCM bytes (int16 LE or float32 LE — see *format*).
        sample_rate: Samples per second (16000 default).
        channels: Channel count (always 1 — mono).
        format: Encoding — ``"pcm16"`` or ``"float32"``.
        timestamp_ms: Wall-clock milliseconds at capture time.
        is_end_of_speech: Hint from AudioAdapter; inference backend decides
            final segmentation independently.
    """

    data: bytes
    sample_rate: int = 16_000
    channels: int = 1
    format: str = "pcm16"
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    is_end_of_speech: bool = False


@dataclass
class TranscriptSegment:
    """Committed text segment flowing DOWN the Data Channel.

    Attributes:
        text: Recognised text (may be partial if ``is_final=False``).
        confidence: Normalised confidence in [0.0, 1.0].
        is_final: ``True`` = committed; ``False`` = live partial hypothesis.
        language: BCP-47 language tag detected by the model.
    """

    text: str
    confidence: float
    is_final: bool
    language: str

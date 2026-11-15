"""Versioned wire-protocol message types for Myna inference channels.

Data Channel: WebSocket /ws/transcribe → AudioFrame UP, TranscriptSegment DOWN

Turn control on the same socket (protocol version 2), plain JSON:

  UP   ``{"type": "flush", "id": n}``  close the open turn if there is one, wait
       for its transcription, send its segments, then acknowledge.
  UP   ``{"type": "reset", "id": n}``  discard the open turn without transcribing,
       then acknowledge.
  DOWN ``{"type": "flushed", "id": n}`` acknowledges either, echoing its id.

The server handles messages strictly in order, so every segment of audio sent
before a flush/reset arrives before its acknowledgement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

PROTOCOL_VERSION = 2


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

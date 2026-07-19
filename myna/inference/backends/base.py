"""InferenceBackend ABC — versioned interface every STT backend must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

import numpy as np

from myna.inference.protocol import TranscriptSegment

INTERFACE_VERSION = "1.2"


@dataclass
class BackendCapabilities:
    """Capabilities advertised by a backend on the Control Channel.

    Attributes:
        version: Interface version string.
        supported_languages: BCP-47 tags this backend recognises.
        supported_models: Model size identifiers (e.g. ``"small"``).
        supports_streaming: Whether partial hypotheses are emitted.
        supports_gpu: Whether CUDA acceleration is available.
    """

    version: str
    supported_languages: list[str]
    supported_models: list[str]
    supports_streaming: bool
    supports_gpu: bool


class InferenceBackend(ABC):
    """Abstract STT inference backend.

    All implementations carry ``interface_version`` for capability negotiation.
    """

    interface_version: str = INTERFACE_VERSION

    @abstractmethod
    async def load(
        self,
        model: str,
        device: str,
        compute_type: str,
        beam_size: int = 5,
        no_speech_threshold: float = 0.6,
        log_prob_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
    ) -> None:
        """Load or initialise the model.

        Args:
            model: Model identifier (e.g. ``"small"``).
            device: Target device — ``"auto"``, ``"cpu"``, or ``"cuda"``.
            compute_type: Quantisation type (e.g. ``"int8"``).
            beam_size: Beam width for decoding (higher = more accurate, slower).
            no_speech_threshold: Silence-probability above which a segment is
                dropped as non-speech (hallucination guard). Added in
                interface 1.2; backends may ignore it.
            log_prob_threshold: Average log-probability below which a decode
                is considered low-confidence gibberish and dropped.
            compression_ratio_threshold: Gzip compression ratio above which a
                segment is considered repetitive/hallucinated text.
        """

    @abstractmethod
    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[np.ndarray],
        language: str,
        hotwords: str | None = None,
    ) -> AsyncIterator[TranscriptSegment]:
        """Transcribe an async stream of float32 audio frames.

        The backend owns segmentation — it decides when to commit a segment.

        Args:
            audio_frames: Async iterator of 1-D float32 numpy arrays at 16 kHz.
            language: BCP-47 target language hint.
            hotwords: Optional space-joined words to bias decoding toward
                (user's personal vocabulary). Added in interface 1.1;
                backends may ignore it.

        Yields:
            :class:`~myna.inference.protocol.TranscriptSegment` in order.
        """

    @abstractmethod
    async def capabilities(self) -> BackendCapabilities:
        """Return backend capability metadata."""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """True if the model is loaded and ready to transcribe."""

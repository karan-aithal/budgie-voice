"""Faster-Whisper STT wrapper.

Returns :class:`TranscriptResult` containing text, per-segment mean
confidence, and detected language.

Confidence mapping:
    Faster-Whisper exposes ``segment.avg_logprob`` ∈ (−∞, 0].
    ``confidence = exp(mean_avg_logprob)`` naturally maps to (0, 1].
    Good segments: avg_logprob ≈ −0.2 → conf ≈ 0.82.
    Poor segments: avg_logprob ≈ −1.5 → conf ≈ 0.22.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from assistant.utils.exceptions import RecognizerError

logger = logging.getLogger(__name__)


@dataclass
class TranscriptResult:
    """Structured output from :class:`WhisperRecognizer`.

    Attributes:
        text: Transcribed and stripped text.
        confidence: Mean segment confidence in [0, 1].
        language: BCP-47 language code detected by Whisper.
    """

    text: str
    confidence: float
    language: str


class WhisperRecognizer:
    """Wraps ``faster_whisper.WhisperModel`` for English speech recognition.

    Call :meth:`load` once before :meth:`transcribe`.  Loading is a
    blocking operation (may download ~500 MB on first run); wrap it in
    ``asyncio.get_event_loop().run_in_executor(None, recognizer.load)``.

    Args:
        model_size: Whisper model variant — tiny | small | medium | large-v3.
        device: Compute device — auto | cpu | cuda.
            ``auto`` selects CUDA if available, otherwise CPU.
        compute_type: Quantisation — int8 | float16 | float32.
        language: BCP-47 language code passed to Whisper.
        beam_size: Beam search width (higher = more accurate, slower).
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        language: str = "en",
        beam_size: int = 5,
    ) -> None:
        self._model_size = model_size
        self._device = self._resolve_device(device)
        self._compute_type = compute_type
        self._language = language
        self._beam_size = beam_size
        self._model: Optional[object] = None  # faster_whisper.WhisperModel

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_device(device: str) -> str:
        """Resolve ``'auto'`` → ``'cuda'`` if GPU present, else ``'cpu'``.

        Args:
            device: Raw device string from config.

        Returns:
            Concrete device string accepted by faster-whisper.
        """
        if device != "auto":
            return device
        try:
            import torch  # noqa: PLC0415

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the Whisper model into memory (blocking).

        Raises:
            RecognizerError: If faster-whisper is not installed or the
                model fails to initialise.
        """
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise RecognizerError(
                "faster-whisper not installed — run: pip install faster-whisper"
            ) from exc

        logger.info(
            "Loading Whisper '%s' on %s (%s) — first run downloads model files",
            self._model_size,
            self._device,
            self._compute_type,
        )
        try:
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        except Exception as exc:
            raise RecognizerError(f"WhisperModel init failed: {exc}") from exc

        logger.info("Whisper model ready")

    def transcribe(self, audio: np.ndarray) -> TranscriptResult | None:
        """Transcribe a speech segment.

        Args:
            audio: Float32 mono numpy array sampled at 16 kHz.

        Returns:
            :class:`TranscriptResult` or ``None`` if no speech detected.

        Raises:
            RecognizerError: If :meth:`load` was not called, or if
                transcription raises an unexpected error.
        """
        if self._model is None:
            raise RecognizerError("Model not loaded — call load() before transcribe()")

        try:
            segments_gen, info = self._model.transcribe(  # type: ignore[union-attr]
                audio,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=False,  # VAD handled by audio/vad.py
            )
            segments = list(segments_gen)  # consume generator
        except Exception as exc:
            raise RecognizerError(f"Transcription error: {exc}") from exc

        if not segments:
            return None

        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return None

        # avg_logprob ∈ (−∞, 0]  →  confidence ∈ (0, 1] via exp
        mean_logprob = sum(s.avg_logprob for s in segments) / len(segments)
        confidence = round(math.exp(mean_logprob), 4)

        logger.debug(
            "Transcribed %d segment(s): conf=%.3f  lang=%s  text=%r",
            len(segments),
            confidence,
            info.language,
            text[:80],
        )
        return TranscriptResult(text=text, confidence=confidence, language=info.language)

"""FasterWhisperBackend — local CPU/GPU inference via faster-whisper."""
from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from typing import AsyncIterator

import numpy as np

from budgie_voice.inference.backends.base import BackendCapabilities, InferenceBackend
from budgie_voice.inference.protocol import TranscriptSegment

_LOG = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".local" / "share" / "budgie-voice" / "models"
_SUPPORTED_MODELS = ["tiny", "tiny.en", "small", "small.en", "medium", "medium.en", "large-v3"]
_SUPPORTED_LANGUAGES = ["en", "fr", "de", "es", "it", "pt", "nl", "ru", "zh", "ja", "ko", "ar"]


class FasterWhisperBackend(InferenceBackend):
    """STT backend wrapping faster-whisper.

    Transcription runs in a thread-pool executor to avoid blocking the
    event loop. The backend accumulates all audio frames for a speech
    turn and transcribes them as a single batch.
    """

    SUPPORTED_MODELS = _SUPPORTED_MODELS

    def __init__(self) -> None:
        self._model: object | None = None
        self._model_name: str = "small"
        self._language: str = "en"
        self._beam_size: int = 5
        self._no_speech_threshold: float = 0.6
        self._log_prob_threshold: float = -1.0
        self._compression_ratio_threshold: float = 2.4

    # ------------------------------------------------------------------
    # InferenceBackend interface
    # ------------------------------------------------------------------

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
        """Load WhisperModel in a thread-pool executor.

        Args:
            model: Model size identifier.
            device: ``"auto"``, ``"cpu"``, or ``"cuda"``.
            compute_type: Quantisation type (``"int8"``, ``"float16"``…).
            beam_size: Beam width for decoding (higher = more accurate, slower).

        Raises:
            RuntimeError: Model download or initialisation fails.
        """
        self._model_name = model
        self._beam_size = beam_size
        self._no_speech_threshold = no_speech_threshold
        self._log_prob_threshold = log_prob_threshold
        self._compression_ratio_threshold = compression_ratio_threshold
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_sync, model, device, compute_type)
        _LOG.info("FasterWhisperBackend ready: %s on %s (%s)", model, device, compute_type)

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[np.ndarray],
        language: str,
        hotwords: str | None = None,
    ) -> AsyncIterator[TranscriptSegment]:
        """Accumulate audio frames then transcribe as one batch.

        Args:
            audio_frames: Async iterator of 1-D float32 arrays at 16 kHz.
            language: BCP-47 language hint.

        Yields:
            Final :class:`~budgie_voice.inference.protocol.TranscriptSegment` per sentence.

        Raises:
            RuntimeError: Model not loaded or transcription fails.
        """
        if self._model is None:
            raise RuntimeError("FasterWhisperBackend: call load() first")

        buffer: list[np.ndarray] = []
        async for frame in audio_frames:
            buffer.append(frame)

        if not buffer:
            return

        audio = np.concatenate(buffer).astype(np.float32)
        loop = asyncio.get_running_loop()
        segments = await loop.run_in_executor(None, self._transcribe_sync, audio, language, hotwords)

        for seg in segments:
            yield seg

    async def capabilities(self) -> BackendCapabilities:
        """Return backend capabilities."""
        return BackendCapabilities(
            version=self.interface_version,
            supported_languages=_SUPPORTED_LANGUAGES,
            supported_models=_SUPPORTED_MODELS,
            supports_streaming=False,
            supports_gpu=True,
        )

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def is_cached(self, model: str, compute_type: str) -> bool:  # noqa: ARG002
        from faster_whisper.utils import download_model  # noqa: PLC0415

        try:
            download_model(model, local_files_only=True, cache_dir=str(_CACHE_DIR))
        except Exception:  # noqa: BLE001 — any failure means "not on disk"
            return False
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_sync(self, model: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        resolved_device = device
        if device == "auto":
            try:
                import torch  # noqa: PLC0415

                resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                resolved_device = "cpu"

        cache = _CACHE_DIR
        cache.mkdir(parents=True, exist_ok=True)

        try:
            self._model = WhisperModel(
                model,
                device=resolved_device,
                compute_type=compute_type,
                download_root=str(cache),
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to load WhisperModel '{model}': {exc}") from exc

    def _transcribe_sync(
        self, audio: np.ndarray, language: str, hotwords: str | None = None
    ) -> list[TranscriptSegment]:
        segments_iter, info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=language,
            hotwords=hotwords,
            beam_size=self._beam_size,
            without_timestamps=True,
            condition_on_previous_text=False,
            no_speech_threshold=self._no_speech_threshold,
            log_prob_threshold=self._log_prob_threshold,
            compression_ratio_threshold=self._compression_ratio_threshold,
        )
        results: list[TranscriptSegment] = []
        for seg in segments_iter:
            # exp(avg_logprob) maps log-probability to (0, 1] — properly calibrated.
            # avg_logprob=-0.5 → conf=0.61, avg_logprob=-1.0 → conf=0.37
            confidence = math.exp(seg.avg_logprob)
            text = seg.text.strip()
            if text:
                results.append(
                    TranscriptSegment(
                        text=text,
                        confidence=confidence,
                        is_final=True,
                        language=info.language,
                    )
                )
                _LOG.debug("Transcribed: conf=%.2f lang=%s", confidence, info.language)
        return results

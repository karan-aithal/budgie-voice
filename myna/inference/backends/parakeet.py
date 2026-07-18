"""ParakeetBackend — NVIDIA Parakeet-TDT via onnx_asr (ONNX Runtime, CPU-friendly).

Benchmarked against faster-whisper small.en on this project's target
hardware (CPU-only): 3.6x lower latency on short (~3s) dictation-length
utterances (0.22s vs 0.81s), and correctly silent on pure silence / noise
floor where faster-whisper hallucinated "you". Text quality on real speech
was equivalent. See the benchmark in myna's dev history for the numbers.

Batch-decode only (``model.recognize`` takes a full array) — no incremental
partial hypotheses despite Parakeet's streaming-capable TDT architecture;
onnx_asr doesn't expose chunked decode. Still a real latency win for the
existing per-turn transcription flow.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

import numpy as np

from myna.inference.backends.base import BackendCapabilities, InferenceBackend
from myna.inference.protocol import TranscriptSegment
from myna.utils.exceptions import MynaInferenceError

_LOG = logging.getLogger(__name__)

_SUPPORTED_MODELS = ["nemo-parakeet-tdt-0.6b-v3", "nemo-parakeet-tdt-0.6b-v2", "nemo-parakeet-rnnt-0.6b"]
_SUPPORTED_LANGUAGES = ["en"]  # v3 is English-only; multilingual presets exist but untested here


class ParakeetBackend(InferenceBackend):
    """STT backend wrapping ``onnx_asr``'s Parakeet-TDT preset.

    Args:
        None — configured via :meth:`load`, matching every other backend.
    """

    def __init__(self) -> None:
        self._model: object | None = None
        self._model_name: str = "nemo-parakeet-tdt-0.6b-v3"

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
        """Load the ONNX Parakeet model in a thread-pool executor.

        Args:
            model: One of :data:`_SUPPORTED_MODELS`.
            device: Ignored — onnx_asr picks CPU/CUDA execution providers
                automatically based on what's installed; this project only
                exercises the CPU path.
            compute_type: Mapped to onnx_asr's ``quantization`` — pass
                ``"int8"`` for the quantized (smaller, faster) weights, or
                ``"none"``/anything else for full precision.
            beam_size, no_speech_threshold, log_prob_threshold,
                compression_ratio_threshold: Unused — Parakeet's TDT decode
                has no beam search or Whisper-style hallucination guards to
                configure; accepted only so this backend is a drop-in swap
                for faster-whisper under the shared interface.

        Raises:
            MynaInferenceError: Model download or initialisation fails.
        """
        self._model_name = model
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_sync, model, compute_type)
        _LOG.info("ParakeetBackend ready: %s", model)

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[np.ndarray],
        language: str,
        hotwords: str | None = None,  # noqa: ARG002 — onnx_asr Parakeet has no hotword biasing
    ) -> AsyncIterator[TranscriptSegment]:
        """Accumulate audio frames then transcribe as one batch.

        Batch-decode only — see module docstring. Segmentation is whatever
        the caller hands in per turn (one call = one committed segment),
        same contract as :class:`~myna.inference.backends.faster_whisper.FasterWhisperBackend`.
        """
        if self._model is None:
            raise MynaInferenceError("ParakeetBackend: call load() first")

        buffer: list[np.ndarray] = []
        async for frame in audio_frames:
            buffer.append(frame)
        if not buffer:
            return

        audio = np.concatenate(buffer).astype(np.float32)
        loop = asyncio.get_running_loop()
        seg = await loop.run_in_executor(None, self._transcribe_sync, audio, language)
        if seg is not None:
            yield seg

    async def unload(self) -> None:
        """Release model from memory."""
        self._model = None
        _LOG.info("ParakeetBackend unloaded")

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_sync(self, model: str, compute_type: str) -> None:
        import onnx_asr  # noqa: PLC0415

        quantization = "int8" if compute_type == "int8" else None
        try:
            self._model = onnx_asr.load_model(model, quantization=quantization)
        except Exception as exc:
            raise MynaInferenceError(f"Failed to load Parakeet model '{model}': {exc}") from exc

    def _transcribe_sync(self, audio: np.ndarray, language: str) -> TranscriptSegment | None:
        text: str = self._model.recognize(audio, sample_rate=16_000)  # type: ignore[union-attr]
        text = text.strip()
        if not text:
            return None
        # onnx_asr's TextResultsAsrAdapter doesn't expose a confidence score;
        # 0.9 is a fixed placeholder distinct from the 1.0 "verification
        # disabled" sentinel used elsewhere, so downstream confidence-based
        # logic (e.g. low-confidence warnings) still has a meaningful value
        # to compare against rather than always-pass.
        return TranscriptSegment(
            text=text,
            confidence=0.9,
            is_final=True,
            language=language,
            segment_id=str(uuid.uuid4()),
        )

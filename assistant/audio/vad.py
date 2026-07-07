"""Silero VAD wrapper for real-time streaming speech detection.

Consumes raw audio chunks from an ``asyncio.Queue`` and yields complete
speech segments (numpy arrays) via an async generator.

Silero VAD requires 512-sample windows at 16 kHz (≈ 32 ms each).
Incoming chunks are re-framed into 512-sample windows internally.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Optional

import numpy as np

from assistant.utils.exceptions import VADError

logger = logging.getLogger(__name__)

# Silero VAD fixed window size at 16 kHz
_WINDOW = 512
# Hard cap: emit a segment if speech runs longer than this (avoid OOM)
_MAX_SEGMENT_SAMPLES = 30 * 16_000  # 30 seconds


class SileroVAD:
    """Wraps Silero VAD for streaming, chunk-by-chunk speech detection.

    Args:
        aggressiveness: Sensitivity 0–3 (higher → more speech detected,
            lower speech-probability threshold).
        speech_pad_ms: Milliseconds of trailing silence to include after
            speech ends before emitting the segment.
        sample_rate: Audio sample rate; must be 16 000 or 8 000.
    """

    _THRESHOLD_MAP: dict[int, float] = {0: 0.70, 1: 0.60, 2: 0.50, 3: 0.30}

    def __init__(
        self,
        aggressiveness: int = 2,
        speech_pad_ms: int = 300,
        sample_rate: int = 16_000,
    ) -> None:
        self._threshold: float = self._THRESHOLD_MAP.get(aggressiveness, 0.50)
        self._speech_pad_samples: int = int(sample_rate * speech_pad_ms / 1000)
        self._sample_rate = sample_rate
        self._model: Optional[object] = None  # torch.nn.Module, loaded lazily

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Download (first run) and load the Silero VAD model via torch.hub.

        Raises:
            VADError: If torch or the model cannot be loaded.
        """
        try:
            import torch  # noqa: PLC0415

            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                verbose=False,
            )
            model.eval()
            self._model = model
            logger.info(
                "Silero VAD loaded — threshold=%.2f  pad=%d samples",
                self._threshold,
                self._speech_pad_samples,
            )
        except Exception as exc:
            raise VADError(f"Failed to load Silero VAD model: {exc}") from exc

    def _score(self, window: np.ndarray) -> float:
        """Return speech probability for a 512-sample window.

        Args:
            window: Float32 numpy array of exactly ``_WINDOW`` samples.

        Returns:
            Speech probability in [0, 1].
        """
        import torch  # noqa: PLC0415

        tensor = torch.from_numpy(window).unsqueeze(0)  # shape [1, 512]
        with torch.no_grad():
            prob: float = self._model(tensor, self._sample_rate).item()  # type: ignore[operator]
        return prob

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def stream(
        self, audio_queue: asyncio.Queue
    ) -> AsyncGenerator[np.ndarray, None]:
        """Consume audio chunks and yield complete speech segments.

        The generator runs until the caller's task is cancelled (``CancelledError``
        propagates from ``await audio_queue.get()`` and stops the generator).

        State machine::

            SILENCE → speech prob ≥ threshold → SPEECH (accumulate)
            SPEECH  → silence_samples ≥ pad    → emit segment → SILENCE

        Args:
            audio_queue: Queue fed by :class:`~assistant.audio.capture.AudioCapture`.

        Yields:
            Float32 numpy array containing a single speech utterance.
        """
        if self._model is None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._load_model)

        remainder = np.array([], dtype=np.float32)
        speech_buffer: list[np.ndarray] = []
        in_speech = False
        silence_samples = 0

        while True:
            chunk: np.ndarray = await audio_queue.get()
            combined = np.concatenate([remainder, chunk])

            offset = 0
            while offset + _WINDOW <= len(combined):
                window = combined[offset : offset + _WINDOW]
                offset += _WINDOW

                prob = self._score(window)
                is_speech = prob >= self._threshold

                if is_speech:
                    if not in_speech:
                        in_speech = True
                        silence_samples = 0
                        logger.debug("Speech start (p=%.3f)", prob)
                    speech_buffer.append(window)
                    silence_samples = 0

                    # Guard against runaway long segments
                    total = sum(w.shape[0] for w in speech_buffer)
                    if total >= _MAX_SEGMENT_SAMPLES:
                        logger.warning(
                            "Segment exceeded 30 s — emitting early and resetting"
                        )
                        yield np.concatenate(speech_buffer)
                        speech_buffer = []
                        in_speech = False

                else:
                    if in_speech:
                        speech_buffer.append(window)
                        silence_samples += _WINDOW

                        if silence_samples >= self._speech_pad_samples:
                            segment = np.concatenate(speech_buffer)
                            logger.debug(
                                "Speech segment emitted — %.2f s",
                                len(segment) / self._sample_rate,
                            )
                            yield segment
                            speech_buffer = []
                            in_speech = False
                            silence_samples = 0

            remainder = combined[offset:]

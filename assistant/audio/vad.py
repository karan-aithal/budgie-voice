"""Silero VAD wrapper for real-time streaming speech detection.

Consumes raw audio chunks from an ``asyncio.Queue`` and yields complete
speech segments (numpy arrays) via an async generator.

Silero VAD requires 512-sample windows at 16 kHz (≈ 32 ms each).
Incoming chunks are re-framed into 512-sample windows internally.

Latency tuning knobs
--------------------
* ``speech_pad_ms`` — how long silence must last before a segment is emitted.
  200 ms is a good balance; 300 ms feels sluggish.
* ``min_speech_ms`` — discard segments shorter than this.  Prevents wasting
  Whisper time on noise bursts or mic pops.
* ``aggressiveness`` — Silero probability threshold (0 = gentlest, 3 = most
  aggressive).  2 works well for typical mic conditions.
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
            speech ends before emitting the segment.  Lower = faster response,
            but may cut off the end of utterances.  Default 200 ms.
        min_speech_ms: Discard segments shorter than this (noise / mic pops).
            Default 400 ms — shorter clips rarely transcribe usefully.
        sample_rate: Audio sample rate; must be 16 000 or 8 000.
    """

    _THRESHOLD_MAP: dict[int, float] = {0: 0.70, 1: 0.60, 2: 0.50, 3: 0.30}

    def __init__(
        self,
        aggressiveness: int = 2,
        speech_pad_ms: int = 200,
        min_speech_ms: int = 400,
        live_chunk_ms: int = 2000,
        sample_rate: int = 16_000,
    ) -> None:
        self._threshold: float = self._THRESHOLD_MAP.get(aggressiveness, 0.50)
        self._speech_pad_samples: int = int(sample_rate * speech_pad_ms / 1000)
        self._min_speech_samples: int = int(sample_rate * min_speech_ms / 1000)
        self._live_chunk_samples: int = int(sample_rate * live_chunk_ms / 1000)
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
                "Silero VAD loaded — threshold=%.2f  pad=%d ms  min_speech=%d ms",
                self._threshold,
                self._speech_pad_samples * 1000 // self._sample_rate,
                self._min_speech_samples * 1000 // self._sample_rate,
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

    def _try_emit(self, speech_buffer: list[np.ndarray]) -> np.ndarray | None:
        """Concatenate *speech_buffer* and return it if long enough, else None.

        Args:
            speech_buffer: Accumulated speech windows.

        Returns:
            Concatenated segment numpy array, or ``None`` if below
            ``min_speech_ms``.
        """
        segment = np.concatenate(speech_buffer)
        duration_s = len(segment) / self._sample_rate
        if len(segment) < self._min_speech_samples:
            logger.debug(
                "Segment too short (%.2f s < %.2f s) — discarded",
                duration_s,
                self._min_speech_samples / self._sample_rate,
            )
            return None
        logger.debug("Speech segment ready — %.2f s", duration_s)
        return segment

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def stream(
        self, audio_queue: asyncio.Queue
    ) -> AsyncGenerator[np.ndarray, None]:
        """Consume audio chunks and yield speech segments.

        **Rolling-chunk mode** (``live_chunk_ms > 0``):
        While speech is ongoing, emit a partial segment every
        ``live_chunk_ms`` milliseconds so Whisper can transcribe and inject
        progressively — text appears as you speak rather than waiting for
        end-of-sentence.

        **End-of-utterance flush**:
        Remaining audio (from the last partial emit to end of silence) is
        emitted as the final chunk of the utterance.

        Segments shorter than ``min_speech_ms`` are silently discarded
        (mic pops, background noise).

        State machine::

            SILENCE ──(speech detected)──► SPEECH (accumulate + rolling emit)
            SPEECH  ──(silence ≥ pad)────► flush remainder → SILENCE

        Yields:
            Float32 numpy array of a speech chunk (≥ ``min_speech_ms``).
        """
        if self._model is None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._load_model)

        remainder = np.array([], dtype=np.float32)
        speech_buffer: list[np.ndarray] = []
        in_speech = False
        silence_samples = 0
        speech_since_emit = 0   # speech samples since last partial emit

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
                        speech_since_emit = 0
                        logger.debug("Speech start (p=%.3f)", prob)

                    speech_buffer.append(window)
                    speech_since_emit += _WINDOW
                    silence_samples = 0

                    # ── Rolling partial emit ──────────────────────────────
                    if (
                        self._live_chunk_samples > 0
                        and speech_since_emit >= self._live_chunk_samples
                    ):
                        seg = self._try_emit(speech_buffer)
                        speech_buffer = []
                        speech_since_emit = 0
                        if seg is not None:
                            yield seg

                    # ── Runaway guard (30 s cap) ──────────────────────────
                    total = sum(w.shape[0] for w in speech_buffer)
                    if total >= _MAX_SEGMENT_SAMPLES:
                        logger.warning("Segment >30 s — force-emitting")
                        seg = self._try_emit(speech_buffer)
                        speech_buffer = []
                        speech_since_emit = 0
                        if seg is not None:
                            yield seg

                else:
                    if in_speech:
                        speech_buffer.append(window)
                        silence_samples += _WINDOW

                        if silence_samples >= self._speech_pad_samples:
                            # Flush final chunk of this utterance
                            seg = self._try_emit(speech_buffer)
                            speech_buffer = []
                            speech_since_emit = 0
                            in_speech = False
                            silence_samples = 0
                            if seg is not None:
                                yield seg

            remainder = combined[offset:]

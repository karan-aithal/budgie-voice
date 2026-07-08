"""Silero VAD filter — emits speech-only frames with end-of-speech hints."""
from __future__ import annotations

import logging
from collections import deque

import numpy as np
import torch

_LOG = logging.getLogger(__name__)

_THRESHOLD_MAP: dict[int, float] = {0: 0.85, 1: 0.75, 2: 0.50, 3: 0.35}
_SAMPLE_RATE = 16_000
_VAD_WINDOW = 512  # samples — Silero VAD expects 512 @ 16 kHz


class VADFilter:
    """Silero VAD wrapper with speech-padding and end-of-speech signalling.

    Args:
        aggressiveness: 0 (permissive) – 3 (strict); maps to a confidence threshold.
        speech_pad_ms: Milliseconds of trailing silence forwarded after speech ends.
        sample_rate: Must be 16000 for Silero VAD.
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        speech_pad_ms: int = 300,
        sample_rate: int = _SAMPLE_RATE,
    ) -> None:
        self._threshold = _THRESHOLD_MAP.get(aggressiveness, 0.50)
        self._pad_samples = int(sample_rate * speech_pad_ms / 1000)
        self._sample_rate = sample_rate
        self._model: object | None = None
        self._in_speech = False
        self._pad_remaining = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Download / load Silero VAD via torch.hub. Call once at startup."""
        model, _ = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            force_reload=False,
            verbose=False,
        )
        self._model = model
        _LOG.info("Silero VAD loaded (threshold=%.2f, pad=%d ms)", self._threshold, int(self._pad_samples / self._sample_rate * 1000))

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Return True if *chunk* contains speech.

        Args:
            chunk: 1-D float32 array (any length; processed in 512-sample windows).

        Returns:
            True if any 512-sample window scores above the aggressiveness threshold.
            Always True before :meth:`load` is called (passthrough mode).
        """
        if self._model is None:
            return True
        for i in range(0, max(1, len(chunk) - _VAD_WINDOW + 1), _VAD_WINDOW):
            window = chunk[i : i + _VAD_WINDOW]
            if len(window) < _VAD_WINDOW:
                window = np.pad(window, (0, _VAD_WINDOW - len(window)))
            tensor = torch.from_numpy(window.astype(np.float32))
            with torch.no_grad():
                score = float(self._model(tensor, self._sample_rate).item())  # type: ignore[operator]
            if score >= self._threshold:
                return True
        return False

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray | None, bool]:
        """Run chunk through the VAD state machine.

        Args:
            chunk: 1-D float32 array.

        Returns:
            ``(output_chunk, is_speech_end)`` where:
            - *output_chunk* is the data to forward downstream (``None`` to discard).
            - *is_speech_end* is ``True`` when padding expires and the segment is done.
        """
        speech = self.is_speech(chunk)

        if speech:
            self._in_speech = True
            self._pad_remaining = self._pad_samples
            return chunk, False

        if self._in_speech and self._pad_remaining > 0:
            self._pad_remaining -= len(chunk)
            if self._pad_remaining <= 0:
                self._in_speech = False
                return chunk, True
            return chunk, False

        return None, False

"""Silero VAD filter — emits speech-only frames with end-of-speech hints."""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

import numpy as np
import torch

_LOG = logging.getLogger(__name__)

_THRESHOLD_MAP: dict[int, float] = {0: 0.85, 1: 0.75, 2: 0.50, 3: 0.35}
_SAMPLE_RATE = 16_000
_VAD_WINDOW = 512  # samples — Silero VAD expects 512 @ 16 kHz

# torch.hub defaults to ~/.cache/torch/hub — redirect under the app's own data
# dir so nothing budgie-voice downloads lands in ~/.cache.
_HUB_DIR = Path.home() / ".local" / "share" / "budgie-voice" / "torch_hub"


class VADFilter:
    """Silero VAD wrapper with pre-roll, speech-padding and end-of-speech signalling.

    Chunks of any size go in; the model only ever sees exact 512-sample windows
    of the continuous stream (leftover samples wait for the next chunk).

    Args:
        aggressiveness: 0 (permissive) – 3 (strict); maps to a confidence threshold.
        speech_pad_ms: Milliseconds of trailing silence forwarded after speech ends.
        sample_rate: Must be 16000 for Silero VAD.
        pre_roll_ms: Milliseconds of audio before the first speech chunk of a turn
            that are emitted along with it, so word onsets are not clipped.
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        speech_pad_ms: int = 300,
        sample_rate: int = _SAMPLE_RATE,
        pre_roll_ms: int = 300,
    ) -> None:
        self._threshold = _THRESHOLD_MAP.get(aggressiveness, 0.50)
        self._pad_samples = int(sample_rate * speech_pad_ms / 1000)
        self._pre_roll_samples = int(sample_rate * pre_roll_ms / 1000)
        self._sample_rate = sample_rate
        self._model: object | None = None
        self.reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Download / load Silero VAD via torch.hub (once), then reset session state.

        Blocking (may hit the network the first time) — call from an executor.
        """
        if self._model is None:
            torch.hub.set_dir(str(_HUB_DIR))
            model, _ = torch.hub.load(
                "snakers4/silero-vad",
                "silero_vad",
                force_reload=False,
                verbose=False,
                trust_repo=True,  # non-interactive: never prompt on stdin mid-session
            )
            self._model = model
            _LOG.info(
                "Silero VAD loaded (threshold=%.2f, pad=%d ms, pre-roll=%d ms)",
                self._threshold,
                int(self._pad_samples / self._sample_rate * 1000),
                int(self._pre_roll_samples / self._sample_rate * 1000),
            )
        self.reset()

    def reset(self) -> None:
        """Forget everything from the previous session (model state, buffers, counters)."""
        reset_states = getattr(self._model, "reset_states", None)
        if reset_states is not None:
            reset_states()
        self._in_speech = False
        self._pad_remaining = 0
        self._window_buf = np.zeros(0, dtype=np.float32)
        self._last_speech = False
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_len = 0
        self.max_score = 0.0     # highest speech score this session (diagnostics)
        self.windows = 0         # 512-sample windows scored this session
        self.preroll_chunks = 0  # pre-roll chunks emitted ahead of speech this session

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Return True if the stream, up to and including *chunk*, is speech.

        The chunk is appended to the stream and every completed 512-sample
        window is scored. When *chunk* completes no window, the previous
        answer stands.

        Args:
            chunk: 1-D float32 array (any length).

        Returns:
            True if any newly completed window scores at or above the threshold.
            Always True before :meth:`load` is called (passthrough mode).
        """
        if self._model is None:
            return True
        self._window_buf = np.concatenate([self._window_buf, chunk.astype(np.float32, copy=False)])
        whole = len(self._window_buf) // _VAD_WINDOW * _VAD_WINDOW
        if whole:
            speech = False
            for start in range(0, whole, _VAD_WINDOW):
                tensor = torch.from_numpy(self._window_buf[start : start + _VAD_WINDOW].copy())
                with torch.no_grad():
                    score = float(self._model(tensor, self._sample_rate).item())  # type: ignore[operator]
                self.windows += 1
                self.max_score = max(self.max_score, score)
                speech = speech or score >= self._threshold
            self._last_speech = speech
            self._window_buf = self._window_buf[whole:]
        return self._last_speech

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray | None, bool]:
        """Run chunk through the VAD state machine.

        Args:
            chunk: 1-D float32 array.

        Returns:
            ``(output_chunk, is_speech_end)`` where:
            - *output_chunk* is the data to forward downstream (``None`` to discard).
              The first speech chunk of a turn comes with the pre-roll in front of it.
            - *is_speech_end* is ``True`` when padding expires and the segment is done.
        """
        if self.is_speech(chunk):
            out = chunk
            if not self._in_speech and self._preroll:
                out = np.concatenate([*self._preroll, chunk])
                self.preroll_chunks += len(self._preroll)
                self._preroll.clear()
                self._preroll_len = 0
            self._in_speech = True
            self._pad_remaining = self._pad_samples
            return out, False

        if self._in_speech and self._pad_remaining > 0:
            self._pad_remaining -= len(chunk)
            if self._pad_remaining <= 0:
                self._in_speech = False
                return chunk, True
            return chunk, False

        self._remember(chunk)
        return None, False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _remember(self, chunk: np.ndarray) -> None:
        """Keep the most recent non-speech chunks, at least ``pre_roll_ms`` worth."""
        self._preroll.append(chunk)
        self._preroll_len += len(chunk)
        while len(self._preroll) > 1 and self._preroll_len - len(self._preroll[0]) >= self._pre_roll_samples:
            self._preroll_len -= len(self._preroll.popleft())

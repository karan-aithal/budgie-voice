"""Background QThread workers for the enrollment wizard.

Split from enroller.py to keep module size manageable.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from PyQt6.QtCore import QThread, pyqtSignal

_LOG = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000
_RECORD_SECONDS = 5


class RecordWorker(QThread):
    """Record a fixed-length audio clip from the default microphone.

    Signals:
        finished (np.ndarray): 1-D float32 array of recorded audio.
        error (str): Human-readable error message.
    """

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, duration_s: float = _RECORD_SECONDS, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._duration = duration_s

    def run(self) -> None:
        try:
            frames = int(self._duration * _SAMPLE_RATE)
            audio = sd.rec(frames, samplerate=_SAMPLE_RATE, channels=1, dtype="float32")
            sd.wait()
            self.finished.emit(audio.flatten().copy())
        except Exception as exc:
            _LOG.error("RecordWorker error: %s", exc)
            self.error.emit(str(exc))


class EnrollWorker(QThread):
    """Compute mean ECAPA embedding from recordings and save voiceprint.

    Signals:
        finished (): Emitted on success.
        error (str): Human-readable error message.
    """

    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        recordings: list[np.ndarray],
        verifier: object,
        save_path: Path,
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._recordings = recordings
        self._verifier = verifier
        self._save_path = save_path

    def run(self) -> None:
        try:
            from myna.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

            v: SpeakerVerifier = self._verifier  # type: ignore[assignment]
            embeddings = [v.get_embedding(r) for r in self._recordings]
            mean_emb = np.mean(embeddings, axis=0).astype(np.float32)
            norm = np.linalg.norm(mean_emb)
            if norm > 0:
                mean_emb /= norm
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(torch.tensor(mean_emb), self._save_path)
            _LOG.info("Voiceprint saved to %s", self._save_path)
            self.finished.emit()
        except Exception as exc:
            _LOG.error("EnrollWorker error: %s", exc)
            self.error.emit(str(exc))

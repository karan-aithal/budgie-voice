"""Background QThread workers for the enrollment wizard (internal module)."""

import logging
from pathlib import Path

import numpy as np
import torch
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class RecordThread(QThread):
    """Records a fixed-duration audio clip in a background thread.

    Signals:
        finished: Emits the recorded float32 numpy array (mono, 16 kHz).
        error: Emits an error message string on failure.
    """

    finished: pyqtSignal = pyqtSignal(object)
    error: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        duration_s: float = 3.5,
        sample_rate: int = 16_000,
    ) -> None:
        super().__init__()
        self._duration = duration_s
        self._sample_rate = sample_rate

    def run(self) -> None:
        """Record audio and emit :attr:`finished` with the numpy array."""
        try:
            import sounddevice as sd  # noqa: PLC0415

            n = int(self._duration * self._sample_rate)
            audio = sd.rec(n, samplerate=self._sample_rate, channels=1, dtype="float32")
            sd.wait()
            self.finished.emit(audio[:, 0].copy())
        except Exception as exc:
            self.error.emit(str(exc))


class EnrollThread(QThread):
    """Computes mean speaker embedding across clips and saves voiceprint.pt.

    Signals:
        progress: ``(percent: int, message: str)`` status updates.
        finished: Emitted after the voiceprint is saved successfully.
        error: Emits an error message string on failure.
    """

    progress: pyqtSignal = pyqtSignal(int, str)
    finished: pyqtSignal = pyqtSignal()
    error: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        clips: list[np.ndarray],
        voiceprint_path: Path,
        savedir: str,
    ) -> None:
        super().__init__()
        self._clips = clips
        self._voiceprint_path = voiceprint_path
        self._savedir = savedir

    def run(self) -> None:
        """Load ECAPA-TDNN, embed all clips, save mean embedding."""
        try:
            from speechbrain.inference.speaker import EncoderClassifier  # noqa: PLC0415

            self.progress.emit(5, "Loading ECAPA-TDNN model…")
            classifier = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=self._savedir,
                run_opts={"device": "cpu"},
            )

            embeddings: list[torch.Tensor] = []
            n = len(self._clips)
            for i, clip in enumerate(self._clips):
                pct = 20 + int(i / n * 60)
                self.progress.emit(pct, f"Embedding clip {i + 1} of {n}…")
                signal = torch.from_numpy(clip).unsqueeze(0)
                with torch.no_grad():
                    emb = classifier.encode_batch(signal).squeeze()
                embeddings.append(emb)

            self.progress.emit(90, "Saving voiceprint…")
            mean_emb = torch.stack(embeddings).mean(dim=0)
            self._voiceprint_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(mean_emb, self._voiceprint_path)
            logger.info("Voiceprint saved → %s", self._voiceprint_path)

            self.progress.emit(100, "Voiceprint saved ✓")
            self.finished.emit()

        except Exception as exc:
            logger.exception("Enrollment failed")
            self.error.emit(str(exc))

"""EnrollmentWizard — PyQt6 QWizard for speaker voiceprint enrollment.

4 pages:
  1. Welcome — privacy notice
  2. Recording — record 3 clips; pyqtgraph waveform; re-record
  3. Processing — compute ECAPA embedding + save voiceprint.pt
  4. Confirmation — live test pass/fail

Launch standalone::

    python -m myna.speaker.enroller
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from myna.speaker._workers import EnrollWorker, RecordWorker
from myna.speaker.verifier import SpeakerVerifier

_LOG = logging.getLogger(__name__)

_N_CLIPS = 3
_SAMPLE_RATE = 16_000
_VOICEPRINT_PATH = Path.home() / ".config" / "myna" / "voiceprint.pt"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to Myna Enrollment")
        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "This wizard will record your voice to create a speaker profile.\n\n"
            "🔒 Privacy: All processing is local. No audio is saved to disk.\n"
            "Only a compact mathematical fingerprint (voiceprint) is stored.\n\n"
            "You will record 3 short clips of yourself speaking naturally.\n"
            "Click Next when ready."
        ))
        self.setLayout(layout)


class _RecordingPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Record Your Voice")
        self.setSubTitle(f"Record {_N_CLIPS} clips. Speak naturally for 5 seconds each.")

        self._clips: list[np.ndarray] = []
        self._worker: RecordWorker | None = None

        self._status = QLabel(f"Clips recorded: 0 / {_N_CLIPS}")
        self._record_btn = QPushButton("🎤  Record Clip")
        self._record_btn.clicked.connect(self._start_record)

        self._waveform_label = QLabel("")
        self._waveform_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addWidget(self._status)
        layout.addWidget(self._record_btn)
        layout.addWidget(self._waveform_label)
        self.setLayout(layout)

    def isComplete(self) -> bool:  # noqa: N802
        return len(self._clips) >= _N_CLIPS

    def recordings(self) -> list[np.ndarray]:
        return self._clips

    def _start_record(self) -> None:
        self._record_btn.setEnabled(False)
        self._status.setText("🔴 Recording… speak now!")
        self._worker = RecordWorker(duration_s=5.0)
        self._worker.finished.connect(self._on_recorded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_recorded(self, audio: np.ndarray) -> None:
        self._clips.append(audio)
        self._status.setText(f"Clips recorded: {len(self._clips)} / {_N_CLIPS}")
        self._waveform_label.setText(
            f"Peak level: {float(np.abs(audio).max()):.3f} — "
            + ("✓ Good" if np.abs(audio).max() > 0.05 else "⚠️ Too quiet")
        )
        self._record_btn.setText("🎤  Record Another" if len(self._clips) < _N_CLIPS else "✓  Done")
        self._record_btn.setEnabled(len(self._clips) < _N_CLIPS)
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._record_btn.setEnabled(True)


class _ProcessingPage(QWizardPage):
    def __init__(self, verifier: SpeakerVerifier) -> None:
        super().__init__()
        self.setTitle("Creating Voiceprint")
        self.setSubTitle("Computing speaker embedding — please wait…")

        self._verifier = verifier
        self._done = False
        self._worker: EnrollWorker | None = None

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._info = QLabel("")

        layout = QVBoxLayout()
        layout.addWidget(self._progress)
        layout.addWidget(self._info)
        self.setLayout(layout)

    def initializePage(self) -> None:  # noqa: N802
        recording_page: _RecordingPage = self.wizard().page(1)  # type: ignore[assignment]
        clips = recording_page.recordings()
        self._worker = EnrollWorker(clips, self._verifier, _VOICEPRINT_PATH)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def isComplete(self) -> bool:  # noqa: N802
        return self._done

    def _on_done(self) -> None:
        self._done = True
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._info.setText("✓ Voiceprint saved!")
        # Reload verifier with new voiceprint
        try:
            self._verifier.load()
        except Exception:
            pass
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        self._info.setText(f"Error: {msg}")
        self._done = False


class _ConfirmationPage(QWizardPage):
    def __init__(self, verifier: SpeakerVerifier) -> None:
        super().__init__()
        self.setTitle("Test Your Voiceprint")
        self.setSubTitle("Click Test to verify the enrolled voiceprint works.")
        self._verifier = verifier
        self._result = QLabel("Press Test to begin.")
        self._test_btn = QPushButton("🎤  Test Now")
        self._test_btn.clicked.connect(self._run_test)
        self._worker: RecordWorker | None = None

        layout = QVBoxLayout()
        layout.addWidget(self._test_btn)
        layout.addWidget(self._result)
        self.setLayout(layout)

    def _run_test(self) -> None:
        self._test_btn.setEnabled(False)
        self._result.setText("🔴 Recording… speak now!")
        self._worker = RecordWorker(duration_s=4.0)
        self._worker.finished.connect(self._on_recorded)
        self._worker.start()

    def _on_recorded(self, audio: np.ndarray) -> None:
        is_me, score = self._verifier.verify(audio)
        symbol = "✅" if is_me else "❌"
        self._result.setText(f"{symbol}  Score: {score:.3f}  ({'PASS' if is_me else 'FAIL'})")
        self._test_btn.setEnabled(True)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


class EnrollmentWizard(QWizard):
    """4-page PyQt6 enrollment wizard.

    Args:
        verifier: :class:`~myna.speaker.verifier.SpeakerVerifier` instance
            (may be unloaded; the processing page will load it after saving).
    """

    def __init__(self, verifier: SpeakerVerifier, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self.setWindowTitle("Myna — Enroll Your Voice")
        self.setMinimumSize(500, 350)
        self.addPage(_WelcomePage())
        self.addPage(_RecordingPage())
        self.addPage(_ProcessingPage(verifier))
        self.addPage(_ConfirmationPage(verifier))


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = QApplication(sys.argv)
    verifier = SpeakerVerifier(_VOICEPRINT_PATH)
    wizard = EnrollmentWizard(verifier)
    wizard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

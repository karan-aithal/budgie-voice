"""Speaker enrollment wizard — PyQt6 QWizard.

Records N audio clips from the microphone, computes mean ECAPA-TDNN
embedding, and saves the result as ``voiceprint.pt``.

Launch:
    python -m assistant.speaker.enroller
"""

import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWizard,
    QWizardPage,
)

from assistant.config.settings import Settings, load_settings
from assistant.speaker._workers import EnrollThread, RecordThread

# Try to import pyqtgraph for waveform display; fall back to plain label
try:
    import pyqtgraph as pg  # type: ignore[import-untyped]

    _HAS_PG = True
except ImportError:
    _HAS_PG = False

_RECORD_SECS = 8.0
_SAMPLE_RATE = 16_000

# Wizard page IDs
_PAGE_WELCOME = 0
_PAGE_RECORD = 1
_PAGE_PROCESS = 2
_PAGE_CONFIRM = 3


# ---------------------------------------------------------------------------
# Waveform widget (optional — degrades gracefully if pyqtgraph absent)
# ---------------------------------------------------------------------------


def _make_waveform_widget():
    """Return a pyqtgraph PlotWidget or a plain QLabel placeholder."""
    if _HAS_PG:
        plot = pg.PlotWidget()
        plot.setBackground("k")
        plot.setYRange(-1.0, 1.0)
        plot.setMaximumHeight(120)
        plot.hideAxis("bottom")
        plot.hideAxis("left")
        plot._curve = plot.plot(pen=pg.mkPen("g", width=1))

        def _update(audio: np.ndarray) -> None:
            step = max(1, len(audio) // 800)
            plot._curve.setData(audio[::step].tolist())

        plot.update_audio = _update  # type: ignore[attr-defined]
        return plot
    else:
        lbl = QLabel("(pyqtgraph not installed — waveform preview unavailable)")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.update_audio = lambda _: None  # type: ignore[attr-defined]
        return lbl


# ---------------------------------------------------------------------------
# Page 1 — Welcome
# ---------------------------------------------------------------------------


class WelcomePage(QWizardPage):
    """Welcome screen: explains the enrollment process."""

    def __init__(self, min_samples: int) -> None:
        super().__init__()
        self.setTitle("Voice Enrollment Wizard")
        self.setSubTitle(
            f"Record {min_samples} short speech clips to create your voiceprint.\n"
            f"Each clip is ~ {_RECORD_SECS} seconds.  Speak naturally — read a few sentences "
            "or count out loud."
        )
        body = QLabel(
            "After enrollment the assistant will only type when it hears "
            "<b>your</b> voice.\n\nPress <b>Next</b> to begin."
        )
        body.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(body)


# ---------------------------------------------------------------------------
# Page 2 — Recording
# ---------------------------------------------------------------------------


class RecordingPage(QWizardPage):
    """Records N clips, shows a waveform, and allows re-recording."""

    def __init__(self, min_samples: int) -> None:
        super().__init__()
        self._min_samples = min_samples
        self._clips: list[np.ndarray] = []
        self._thread: Optional[RecordThread] = None
        self._current_pending: Optional[np.ndarray] = None
        self._recording = False

        self.setTitle("Record Your Voice")
        self._counter_lbl = QLabel()
        self._status_lbl = QLabel("Press Record to start.")
        self._waveform = _make_waveform_widget()

        self._rec_btn = QPushButton("⏺  Record")
        self._rec_btn.setFixedHeight(36)
        self._rec_btn.clicked.connect(self._start_recording)

        self._accept_btn = QPushButton("✓  Accept")
        self._accept_btn.setEnabled(False)
        self._accept_btn.clicked.connect(self._accept_clip)

        self._redo_btn = QPushButton("↺  Re-record")
        self._redo_btn.setEnabled(False)
        self._redo_btn.clicked.connect(self._start_recording)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._rec_btn)
        btn_row.addWidget(self._accept_btn)
        btn_row.addWidget(self._redo_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._counter_lbl)
        layout.addWidget(self._waveform)
        layout.addWidget(self._status_lbl)
        layout.addLayout(btn_row)
        self._refresh_counter()

    def _refresh_counter(self) -> None:
        accepted = len(self._clips)
        self._counter_lbl.setText(
            f"Clip {accepted + 1} of {self._min_samples}"
            if accepted < self._min_samples
            else f"✓ All {self._min_samples} clips recorded"
        )

    def _start_recording(self) -> None:
        self._recording = True
        self._rec_btn.setEnabled(False)
        self._accept_btn.setEnabled(False)
        self._redo_btn.setEnabled(False)
        self._current_pending = None
        self._status_lbl.setText("Recording…")

        self._thread = RecordThread(duration_s=_RECORD_SECS, sample_rate=_SAMPLE_RATE)
        self._thread.finished.connect(self._on_recorded)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_recorded(self, audio: np.ndarray) -> None:
        self._recording = False
        self._current_pending = audio
        self._waveform.update_audio(audio)
        self._status_lbl.setText("Recording done.  Accept or re-record.")
        self._rec_btn.setEnabled(False)
        self._accept_btn.setEnabled(True)
        self._redo_btn.setEnabled(True)

    def _accept_clip(self) -> None:
        if self._current_pending is not None:
            self._clips.append(self._current_pending)
            self._current_pending = None
        self._accept_btn.setEnabled(False)
        self._redo_btn.setEnabled(False)
        self._refresh_counter()
        self.completeChanged.emit()

        if len(self._clips) < self._min_samples:
            self._status_lbl.setText("Press Record for the next clip.")
            self._rec_btn.setEnabled(True)
        else:
            self._status_lbl.setText("All clips recorded — press Next.")

    def _on_error(self, msg: str) -> None:
        self._recording = False
        self._rec_btn.setEnabled(True)
        self._status_lbl.setText(f"Error: {msg}")

    def isComplete(self) -> bool:  # noqa: N802
        return len(self._clips) >= self._min_samples


# ---------------------------------------------------------------------------
# Page 3 — Processing
# ---------------------------------------------------------------------------


class ProcessingPage(QWizardPage):
    """Computes mean embedding and saves voiceprint.pt (auto-advances)."""

    def __init__(self, voiceprint_path: Path, savedir: str) -> None:
        super().__init__()
        self._voiceprint_path = voiceprint_path
        self._savedir = savedir
        self._done = False

        self.setTitle("Creating Your Voiceprint")
        self.setSubTitle("Please wait while your voiceprint is computed…")

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._msg_lbl = QLabel("Initialising…")
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addWidget(self._progress)
        layout.addWidget(self._msg_lbl)

    def initializePage(self) -> None:  # noqa: N802
        self._done = False
        self.completeChanged.emit()
        recording_page: RecordingPage = self.wizard().page(_PAGE_RECORD)  # type: ignore[assignment]
        clips = recording_page._clips

        thread = EnrollThread(
            clips=clips,
            voiceprint_path=self._voiceprint_path,
            savedir=self._savedir,
        )
        thread.progress.connect(self._on_progress)
        thread.finished.connect(self._on_done)
        thread.error.connect(self._on_error)
        # Keep thread alive (Qt garbage-collects threads not held by Python)
        self._thread = thread
        thread.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self._progress.setValue(pct)
        self._msg_lbl.setText(msg)

    def _on_done(self) -> None:
        self._done = True
        self.completeChanged.emit()
        # Auto-advance after 800 ms so the user sees "100 % done"
        QTimer.singleShot(800, self.wizard().next)

    def _on_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Enrollment Failed", msg)

    def isComplete(self) -> bool:  # noqa: N802
        return self._done


# ---------------------------------------------------------------------------
# Page 4 — Confirmation
# ---------------------------------------------------------------------------


class ConfirmationPage(QWizardPage):
    """Shows success and offers a live voice-test button."""

    def __init__(self, voiceprint_path: Path, savedir: str, threshold: float) -> None:
        super().__init__()
        self._voiceprint_path = voiceprint_path
        self._savedir = savedir
        self._threshold = threshold
        self._test_thread: Optional[RecordThread] = None

        self.setTitle("Enrollment Complete")
        self.setSubTitle(f"Voiceprint saved to:\n{voiceprint_path}")

        self._result_lbl = QLabel(
            "Press the button below to test your voiceprint."
        )
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_lbl.setWordWrap(True)

        test_btn = QPushButton("🎤  Test Your Voice")
        test_btn.setFixedHeight(36)
        test_btn.clicked.connect(self._run_test)

        layout = QVBoxLayout(self)
        layout.addWidget(self._result_lbl)
        layout.addWidget(test_btn)

    def _run_test(self) -> None:
        self._result_lbl.setText("Recording 8.0 s…")
        self._test_thread = RecordThread(duration_s=_RECORD_SECS, sample_rate=_SAMPLE_RATE)
        self._test_thread.finished.connect(self._on_test_done)
        self._test_thread.error.connect(
            lambda e: self._result_lbl.setText(f"Error: {e}")
        )
        self._test_thread.start()

    def _on_test_done(self, audio: np.ndarray) -> None:
        try:
            from assistant.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

            verifier = SpeakerVerifier(
                voiceprint_path=self._voiceprint_path,
                threshold=self._threshold,
                savedir=self._savedir,
            )
            verifier.load()
            is_me, score = verifier.verify(audio)
            verdict = "✓ Recognised" if is_me else "✗ Not recognised"
            self._result_lbl.setText(
                f"{verdict}   (similarity = {score:.3f},  threshold = {self._threshold:.2f})"
            )
        except Exception as exc:
            self._result_lbl.setText(f"Test failed: {exc}")


# ---------------------------------------------------------------------------
# Wizard shell
# ---------------------------------------------------------------------------


class EnrollmentWizard(QWizard):
    """Four-page enrollment wizard.

    Args:
        settings: Application settings (voiceprint path, thresholds, etc.).
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle("Voice Enrollment Wizard")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(520, 420)

        vp_path = Path(settings.speaker.voiceprint_path).expanduser()
        savedir = str(
            Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa-voxceleb"
        )

        self.setPage(_PAGE_WELCOME, WelcomePage(settings.speaker.min_samples))
        self.setPage(_PAGE_RECORD, RecordingPage(settings.speaker.min_samples))
        self.setPage(_PAGE_PROCESS, ProcessingPage(vp_path, savedir))
        self.setPage(_PAGE_CONFIRM, ConfirmationPage(vp_path, savedir, settings.speaker.threshold))
        self.setStartId(_PAGE_WELCOME)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_enrollment(settings: Settings | None = None) -> None:
    """Launch the enrollment wizard.

    Args:
        settings: App settings; loaded from default path if *None*.
    """
    if settings is None:
        settings = load_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    wizard = EnrollmentWizard(settings)
    wizard.show()
    app.exec()


if __name__ == "__main__":
    run_enrollment()

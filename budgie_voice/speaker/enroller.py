"""EnrollmentWizard — PyQt6 QWizard for speaker voiceprint enrollment.

4 pages:
  1. Welcome — privacy notice
  2. Recording — record 3 clips; pyqtgraph waveform; re-record
  3. Processing — compute ECAPA embedding + save voiceprint.pt
  4. Confirmation — live test pass/fail

Launch standalone::

    python -m budgie_voice.speaker.enroller
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# Re-exec under the venv interpreter if running with system Python.
# Prevents system scipy (incompatible with NumPy ≥1.25) from being imported.

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from budgie_voice.speaker._workers import _RECORD_SECONDS, EnrollWorker, RecordWorker
from budgie_voice.speaker.verifier import SpeakerVerifier

_LOG = logging.getLogger(__name__)

_N_CLIPS = 15
_SAMPLE_RATE = 16_000
_VOICEPRINT_PATH = Path.home() / ".config" / "budgie-voice" / "voiceprint.pt"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to Budgie Enrollment")

        self._device_ids: list[int] = []
        self._combo = QComboBox()
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setFixedWidth(90)
        self._refresh_btn.clicked.connect(self._populate_devices)

        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone:"))
        mic_row.addWidget(self._combo, stretch=1)
        mic_row.addWidget(self._refresh_btn)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "This wizard will record your voice to create a speaker profile.\n\n"
            "🔒 Privacy: All processing is local. No audio is saved to disk.\n"
            "Only a compact mathematical fingerprint (voiceprint) is stored.\n\n"
            "You will record 3 short clips of yourself speaking naturally.\n"
            "Select your microphone below, then click Next when ready."
        ))
        layout.addLayout(mic_row)
        self.setLayout(layout)
        self._populate_devices()

    def _populate_devices(self) -> None:
        self._combo.clear()
        self._device_ids = []  # int = ALSA idx, "bt:<card>" = BT card, "pa:<src>" = PA source

        # --- Bluetooth cards via pactl list cards (works even in A2DP mode) ---
        try:
            cards_out = subprocess.check_output(
                ["pactl", "list", "cards"],
                timeout=5, text=True, stderr=subprocess.DEVNULL,
            )
            card_name = desc = ""
            has_hfp = False
            for line in cards_out.splitlines():
                stripped = line.strip()
                if stripped.startswith("Name:"):
                    card_name = stripped.split(":", 1)[1].strip()
                    has_hfp = False
                    desc = card_name
                elif "device.description" in stripped:
                    # device.description = "OnePlus Buds"
                    desc = stripped.split("=", 1)[1].strip().strip('"')
                elif "headset-head-unit" in stripped and "available: yes" in stripped:
                    has_hfp = True
                elif stripped.startswith("Profiles:") or stripped.startswith("Card #"):
                    pass
                elif stripped == "" and card_name.startswith("bluez_card.") and has_hfp:
                    self._combo.addItem(f"🎧 BT (HFP mic): {desc}")
                    self._device_ids.append(f"bt:{card_name}")  # type: ignore[arg-type]
                    card_name = desc = ""
                    has_hfp = False
            # flush last card
            if card_name.startswith("bluez_card.") and has_hfp:
                self._combo.addItem(f"🎧 BT (HFP mic): {desc}")
                self._device_ids.append(f"bt:{card_name}")  # type: ignore[arg-type]
        except Exception:
            pass

        # --- Other PipeWire/PA sources (non-BT, non-monitor) ---
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sources"],
                timeout=3, text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    src_name = parts[1]
                    if src_name.endswith(".monitor") or "bluez" in src_name.lower():
                        continue
                    self._combo.addItem(f"🞧 PW: {src_name}")
                    self._device_ids.append(f"pa:{src_name}")  # type: ignore[arg-type]
        except Exception:
            pass

        # --- ALSA devices via sounddevice ---
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):  # type: ignore[arg-type]
                if dev["max_input_channels"] > 0:  # type: ignore[index]
                    name = dev["name"]  # type: ignore[index]
                    self._combo.addItem(f"[ALSA] {idx}: {name}")
                    self._device_ids.append(idx)  # type: ignore[arg-type]
        except Exception:
            pass

        if not self._device_ids:
            self._combo.addItem("(no input devices found)")

    def selected_device(self) -> int | str | None:
        """Return ALSA device index (int), PA source name (str), or None for default."""
        i = self._combo.currentIndex()
        if i < 0 or i >= len(self._device_ids):
            return None
        return self._device_ids[i]  # type: ignore[return-value]


class _RecordingPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Record Your Voice")
        self.setSubTitle(
            f"Record {_N_CLIPS} clips of {_RECORD_SECONDS} s each. Speak naturally — "
            "vary your pace and content: read a sentence, speak freely, dictate numbers."
        )

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
        welcome: _WelcomePage = self.wizard().page(0)  # type: ignore[assignment]
        device = welcome.selected_device() if self.wizard() else None
        self._worker = RecordWorker(device=device)  # type: ignore[arg-type]
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
        welcome: _WelcomePage = self.wizard().page(0)  # type: ignore[assignment]
        device = welcome.selected_device() if self.wizard() else None
        self._worker = RecordWorker(duration_s=4.0, device=device)  # type: ignore[arg-type]
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
        verifier: :class:`~budgie_voice.speaker.verifier.SpeakerVerifier` instance
            (may be unloaded; the processing page will load it after saving).
    """

    def __init__(self, verifier: SpeakerVerifier, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self.setWindowTitle("Budgie — Enroll Your Voice")
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

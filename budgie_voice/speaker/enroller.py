"""EnrollmentWizard — PyQt6 QWizard for speaker voiceprint enrollment.

4 pages:
  1. Welcome — privacy notice
  2. Recording — record clips; progress; re-record
  3. Processing — compute ECAPA embedding + save voiceprint.pt
  4. Confirmation — live test pass/fail

A step list sits at the wizard's side. The look (palette, light/dark) comes from the
application theme; nothing here touches recording, embedding or verification.

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
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from budgie_voice.speaker._workers import _RECORD_SECONDS, EnrollWorker, RecordWorker
from budgie_voice.speaker.verifier import SpeakerVerifier
from budgie_voice.ui.status import Status
from budgie_voice.ui.theme import apply_theme, themed_icon
from budgie_voice.ui.widgets import SPACING, StatusRow, muted

_LOG = logging.getLogger(__name__)

_N_CLIPS = 15
_SAMPLE_RATE = 16_000
_VOICEPRINT_PATH = Path.home() / ".config" / "budgie-voice" / "voiceprint.pt"
_QUIET_PEAK = 0.05  # below this a clip is flagged "too quiet"
_STEPS = ("Welcome", "Record", "Create voiceprint", "Verify")
_ROW_NAME_WIDTH = 80  # status rows on the narrow wizard pages


class _Steps(QWidget):
    """Step list for the wizard's side: the current step is bold, later steps are muted."""

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self._names = names
        self._labels = [QLabel(f"{i + 1}.  {name}") for i, name in enumerate(names)]
        col = QVBoxLayout(self)
        col.setContentsMargins(SPACING, 2 * SPACING, SPACING, SPACING)
        col.setSpacing(SPACING)
        for label in self._labels:
            col.addWidget(label)
        col.addStretch()
        self.set_current(0)

    def set_current(self, index: int) -> None:
        for i, label in enumerate(self._labels):
            font = label.font()
            font.setWeight(font.Weight.DemiBold if i == index else font.Weight.Normal)
            label.setFont(font)
            label.setForegroundRole(QPalette.ColorRole.WindowText if i <= index else QPalette.ColorRole.PlaceholderText)
            label.setAccessibleName(
                f"Step {i + 1} of {len(self._names)}: {self._names[i]}" + (", current" if i == index else "")
            )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Enroll your voice")
        self.setSubTitle("Teach Budgie to recognize you, so it types only what you say.")

        self._device_ids: list[int] = []
        self._combo = QComboBox()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setIcon(themed_icon("view-refresh-symbolic"))
        self._refresh_btn.setToolTip("Look for microphones again")
        self._refresh_btn.clicked.connect(self._populate_devices)

        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone:"))
        mic_row.addWidget(self._combo, stretch=1)
        mic_row.addWidget(self._refresh_btn)

        intro = QLabel(
            f"You will record {_N_CLIPS} short clips of about {_RECORD_SECONDS:g} seconds each, "
            "speaking naturally. Choose your microphone, then click Next."
        )
        intro.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(intro)
        layout.addWidget(muted(
            "Everything stays on this computer. No audio is saved: only a compact voiceprint is stored."
        ))
        layout.addLayout(mic_row)
        layout.addStretch()
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
                    self._combo.addItem(f"Bluetooth headset (HFP mic): {desc}")
                    self._device_ids.append(f"bt:{card_name}")  # type: ignore[arg-type]
                    card_name = desc = ""
                    has_hfp = False
            # flush last card
            if card_name.startswith("bluez_card.") and has_hfp:
                self._combo.addItem(f"Bluetooth headset (HFP mic): {desc}")
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
                    self._combo.addItem(f"PipeWire: {src_name}")
                    self._device_ids.append(f"pa:{src_name}")  # type: ignore[arg-type]
        except Exception:
            pass

        # --- ALSA devices via sounddevice ---
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):  # type: ignore[arg-type]
                if dev["max_input_channels"] > 0:  # type: ignore[index]
                    name = dev["name"]  # type: ignore[index]
                    self._combo.addItem(f"ALSA {idx}: {name}")
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
        self.setTitle("Record your voice")
        self.setSubTitle(
            f"Record {_N_CLIPS} clips of {_RECORD_SECONDS} s each. Speak naturally — "
            "vary your pace and content: read a sentence, speak freely, dictate numbers."
        )

        self._clips: list[np.ndarray] = []
        self._worker: RecordWorker | None = None

        self._progress = QProgressBar()
        self._progress.setRange(0, _N_CLIPS)
        self._progress.setFormat("%v of %m clips")
        self._status = QLabel("Click Record clip, then speak.")
        self._status.setWordWrap(True)
        self._busy = QProgressBar()  # moving bar while the microphone is open
        self._busy.setRange(0, 0)
        self._busy.setTextVisible(False)
        self._busy.setMaximumHeight(8)
        self._busy.hide()
        self._record_btn = QPushButton("Record clip")
        self._record_btn.setIcon(themed_icon("audio-input-microphone-symbolic"))
        self._record_btn.clicked.connect(self._start_record)
        self._level = StatusRow("Last clip", name_width=_ROW_NAME_WIDTH)
        self._level.hide()

        layout = QVBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._busy)
        row = QHBoxLayout()
        row.addWidget(self._record_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self._level)
        layout.addStretch()
        self.setLayout(layout)

    def isComplete(self) -> bool:  # noqa: N802
        return len(self._clips) >= _N_CLIPS

    def recordings(self) -> list[np.ndarray]:
        return self._clips

    def _start_record(self) -> None:
        self._record_btn.setEnabled(False)
        self._status.setText("Recording… speak now.")
        self._busy.show()
        welcome: _WelcomePage = self.wizard().page(0)  # type: ignore[assignment]
        device = welcome.selected_device() if self.wizard() else None
        self._worker = RecordWorker(device=device)  # type: ignore[arg-type]
        self._worker.finished.connect(self._on_recorded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_recorded(self, audio: np.ndarray) -> None:
        self._clips.append(audio)
        n = len(self._clips)
        self._busy.hide()
        self._progress.setValue(n)
        self._status.setText(
            f"Clip {n} of {_N_CLIPS} recorded." if n < _N_CLIPS else "All clips recorded. Click Next."
        )
        peak = float(np.abs(audio).max())
        if peak > _QUIET_PEAK:
            self._level.set_status(Status.READY, f"peak level {peak:.2f}", word="Good")
        else:
            self._level.set_status(Status.UNAVAILABLE, f"peak level {peak:.2f}; move closer or speak up", word="Too quiet")
        self._level.show()
        self._record_btn.setText("Record another clip" if n < _N_CLIPS else "All clips recorded")
        self._record_btn.setEnabled(n < _N_CLIPS)
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        self._busy.hide()
        self._status.setText(f"Error: {msg}")
        self._record_btn.setEnabled(True)


class _ProcessingPage(QWizardPage):
    def __init__(self, verifier: SpeakerVerifier) -> None:
        super().__init__()
        self.setTitle("Creating your voiceprint")
        self.setSubTitle("Analyzing your recordings on this computer. This can take a moment.")

        self._verifier = verifier
        self._done = False
        self._worker: EnrollWorker | None = None

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._result = StatusRow(
            "Voiceprint", Status.CONNECTING, "working…", word="Working", name_width=_ROW_NAME_WIDTH
        )

        layout = QVBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._progress)
        layout.addWidget(self._result)
        layout.addStretch()
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
        self._result.set_status(Status.READY, "your voiceprint is created and saved", word="Saved")
        # Reload verifier with new voiceprint
        try:
            self._verifier.load()
        except Exception:
            pass
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._result.set_status(Status.ERROR, msg, word="Failed")
        self._done = False


class _ConfirmationPage(QWizardPage):
    def __init__(self, verifier: SpeakerVerifier) -> None:
        super().__init__()
        self.setTitle("Test your voiceprint")
        self.setSubTitle("Click Test now and say a short sentence to check that Budgie recognizes you.")
        self._verifier = verifier
        self._result = StatusRow(
            "Result", Status.UNKNOWN, "not tested yet", word="Waiting", name_width=_ROW_NAME_WIDTH
        )
        self._test_btn = QPushButton("Test now")
        self._test_btn.setIcon(themed_icon("audio-input-microphone-symbolic"))
        self._test_btn.clicked.connect(self._run_test)
        self._worker: RecordWorker | None = None

        layout = QVBoxLayout()
        layout.setSpacing(SPACING)
        row = QHBoxLayout()
        row.addWidget(self._test_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self._result)
        layout.addStretch()
        self.setLayout(layout)

    def _run_test(self) -> None:
        self._test_btn.setEnabled(False)
        self._result.set_status(Status.CONNECTING, "speak now", word="Listening")
        welcome: _WelcomePage = self.wizard().page(0)  # type: ignore[assignment]
        device = welcome.selected_device() if self.wizard() else None
        self._worker = RecordWorker(duration_s=4.0, device=device)  # type: ignore[arg-type]
        self._worker.finished.connect(self._on_recorded)
        self._worker.start()

    def _on_recorded(self, audio: np.ndarray) -> None:
        is_me, score = self._verifier.verify(audio)
        if is_me:
            self._result.set_status(Status.READY, f"your voice was recognized (score {score:.3f})", word="Match")
        else:
            self._result.set_status(
                Status.ERROR, f"your voice was not recognized (score {score:.3f}). Try again closer to the microphone.",
                word="No match",
            )
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
        self.setMinimumSize(680, 440)
        self.addPage(_WelcomePage())
        self.addPage(_RecordingPage())
        self.addPage(_ProcessingPage(verifier))
        self.addPage(_ConfirmationPage(verifier))

        self._steps = _Steps(_STEPS)
        self.setSideWidget(self._steps)
        self.currentIdChanged.connect(self._steps.set_current)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    from budgie_voice.config.settings import load_settings  # noqa: PLC0415

    app = QApplication(sys.argv)
    apply_theme(app, load_settings().ui.theme)
    verifier = SpeakerVerifier(_VOICEPRINT_PATH)
    wizard = EnrollmentWizard(verifier)
    wizard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

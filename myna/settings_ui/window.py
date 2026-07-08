"""SettingsWindow — PyQt6 QDialog for Myna configuration.

Saves to ``~/.config/myna/config.yaml`` and issues a DBus ``Reload()``
so the running service picks up changes without restart.

Privacy notice is always shown at the bottom of the dialog.
"""
from __future__ import annotations

import logging
from pathlib import Path

import sounddevice as sd
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from myna.config.settings import Settings

_LOG = logging.getLogger(__name__)
_CONFIG_PATH = Path.home() / ".config" / "myna" / "config.yaml"


# ---------------------------------------------------------------------------
# Hotkey capture widget
# ---------------------------------------------------------------------------

_MODIFIERS = frozenset({
    Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
    Qt.Key.Key_Meta, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
})


class _HotkeyEdit(QLineEdit):
    """QLineEdit that captures a key combination and formats it as ``ctrl+space``."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        parts: list[str] = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("super")

        key = event.key()
        if key not in _MODIFIERS:
            seq = QKeySequence(key).toString().lower().strip()
            if seq:
                parts.append(seq)

        if parts:
            self.setText("+".join(parts))


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow(QDialog):
    """Full settings dialog.

    Args:
        settings: Current application settings (read-only source).
        parent: Optional Qt parent widget.
    """

    def __init__(self, settings: Settings, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._settings = settings
        self.setWindowTitle("Myna — Settings")
        self.setMinimumWidth(480)

        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        form = QVBoxLayout(container)
        form.setSpacing(12)

        form.addWidget(self._activation_group())
        form.addWidget(self._inference_group())
        form.addWidget(self._audio_group())
        form.addWidget(self._speaker_group())
        form.addWidget(self._postproc_group())

        privacy = QLabel("🔒 Speech recognition runs locally. No audio is saved.")
        privacy.setStyleSheet("color: gray; font-style: italic;")
        form.addWidget(privacy)

        scroll.setWidget(container)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _activation_group(self) -> QGroupBox:
        box = QGroupBox("Activation")
        fl = QFormLayout(box)

        self._hotkey_edit = _HotkeyEdit(self._settings.activation.hotkey)
        self._hotkey_edit.setPlaceholderText("Click here, then press your hotkey…")
        fl.addRow("Hotkey:", self._hotkey_edit)

        self._mode_toggle = QRadioButton("Toggle")
        self._mode_push = QRadioButton("Push to hold")
        (self._mode_toggle if self._settings.activation.mode == "toggle" else self._mode_push).setChecked(True)
        fl.addRow("Mode:", self._mode_toggle)
        fl.addRow("", self._mode_push)
        return box

    def _inference_group(self) -> QGroupBox:
        box = QGroupBox("Inference")
        fl = QFormLayout(box)

        self._model_combo = QComboBox()
        self._model_combo.addItems(["tiny", "small", "medium", "large-v3"])
        self._model_combo.setCurrentText(self._settings.inference.model)
        fl.addRow("Model:", self._model_combo)

        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["faster_whisper", "parakeet", "nemotron", "qwen3_asr"])
        self._backend_combo.setCurrentText(self._settings.inference.backend)
        fl.addRow("Backend:", self._backend_combo)

        self._lang_edit = QLineEdit(self._settings.inference.language)
        fl.addRow("Language:", self._lang_edit)

        self._conf_slider = QSlider(Qt.Orientation.Horizontal)
        self._conf_slider.setRange(0, 100)
        self._conf_slider.setValue(int(self._settings.inference.confidence_threshold * 100))
        self._conf_label = QLabel(f"{self._settings.inference.confidence_threshold:.2f}")
        self._conf_slider.valueChanged.connect(
            lambda v: self._conf_label.setText(f"{v / 100:.2f}")
        )
        fl.addRow("Confidence threshold:", self._conf_slider)
        fl.addRow("", self._conf_label)
        return box

    def _audio_group(self) -> QGroupBox:
        box = QGroupBox("Audio")
        fl = QFormLayout(box)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem("System default", None)
        try:
            for dev in sd.query_devices():
                if dev["max_input_channels"] > 0:
                    self._mic_combo.addItem(dev["name"], dev["name"])
        except Exception:
            pass
        if self._settings.audio.device:
            idx = self._mic_combo.findData(self._settings.audio.device)
            if idx >= 0:
                self._mic_combo.setCurrentIndex(idx)
        fl.addRow("Microphone:", self._mic_combo)
        return box

    def _speaker_group(self) -> QGroupBox:
        from myna.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
        from myna.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

        box = QGroupBox("Speaker Verification")
        fl = QFormLayout(box)

        self._speaker_chk = QCheckBox("Enable speaker verification")
        self._speaker_chk.setChecked(self._settings.speaker.enabled)
        fl.addRow(self._speaker_chk)

        from PyQt6.QtWidgets import QPushButton  # noqa: PLC0415

        enroll_btn = QPushButton("🎤  Enroll Now")
        vp = Path(self._settings.speaker.voiceprint_path).expanduser()
        enroll_btn.clicked.connect(
            lambda: EnrollmentWizard(SpeakerVerifier(vp), parent=self).show()
        )
        fl.addRow(enroll_btn)
        return box

    def _postproc_group(self) -> QGroupBox:
        box = QGroupBox("Post-Processing")
        fl = QFormLayout(box)
        pp = self._settings.postprocessing

        self._num_chk = QCheckBox("Normalize numbers  (twenty two → 22)")
        self._num_chk.setChecked(pp.normalize_numbers)
        self._punct_chk = QCheckBox("Punctuation commands  (period → .)")
        self._punct_chk.setChecked(pp.punctuation_commands)
        self._cap_chk = QCheckBox("Auto-capitalise")
        self._cap_chk.setChecked(pp.auto_capitalize)
        self._lang_chk = QCheckBox("Language cleanup  (gonna → going to)")
        self._lang_chk.setChecked(pp.language_cleanup)

        for chk in (self._num_chk, self._punct_chk, self._cap_chk, self._lang_chk):
            fl.addRow(chk)
        return box

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self) -> None:
        cfg = {
            "activation": {
                "hotkey": self._hotkey_edit.text(),
                "mode": "toggle" if self._mode_toggle.isChecked() else "push_to_hold",
            },
            "audio": {"device": self._mic_combo.currentData()},
            "inference": {
                "model": self._model_combo.currentText(),
                "backend": self._backend_combo.currentText(),
                "language": self._lang_edit.text().strip(),
                "confidence_threshold": round(self._conf_slider.value() / 100, 2),
            },
            "speaker": {"enabled": self._speaker_chk.isChecked()},
            "postprocessing": {
                "normalize_numbers": self._num_chk.isChecked(),
                "punctuation_commands": self._punct_chk.isChecked(),
                "auto_capitalize": self._cap_chk.isChecked(),
                "language_cleanup": self._lang_chk.isChecked(),
            },
        }

        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)
        _LOG.info("Settings saved to %s", _CONFIG_PATH)

        try:
            import dbus  # noqa: PLC0415

            bus = dbus.SessionBus()
            obj = bus.get_object("com.canonical.Myna", "/com/canonical/Myna")
            dbus.Interface(obj, "com.canonical.Myna").Reload()
        except Exception:
            pass

        self.accept()

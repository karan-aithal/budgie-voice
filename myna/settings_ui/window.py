"""SettingsWindow — PyQt6 QDialog for Myna configuration.

Saves to ``~/.config/myna/config.yaml`` and issues a DBus ``Reload()``
so the running service picks up changes without restart.

Privacy notice is always shown at the bottom of the dialog.
"""
from __future__ import annotations

import logging
import subprocess
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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
        form.addWidget(self._indicator_group())

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
        self._model_combo.addItems([
            "tiny", "tiny.en", "small", "small.en", "medium", "medium.en", "large-v3",
            "nemo-parakeet-tdt-0.6b-v3", "nemo-parakeet-tdt-0.6b-v2", "nemo-parakeet-rnnt-0.6b"
        ])
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
        self._mic_device_values: list[str | None] = []
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(32)
        refresh_btn.setToolTip("Refresh device list")
        refresh_btn.clicked.connect(self._populate_mic_devices)

        mic_row = QHBoxLayout()
        mic_row.addWidget(self._mic_combo, stretch=1)
        mic_row.addWidget(refresh_btn)
        fl.addRow("Microphone:", mic_row)

        self._populate_mic_devices()
        return box

    def _populate_mic_devices(self) -> None:
        # On first call combo is empty — use saved setting as target.
        # On refresh (↻) — keep the current user selection.
        target = (
            self._mic_combo.currentData()
            if self._mic_combo.count() > 0
            else self._settings.audio.device
        )
        self._mic_combo.clear()
        self._mic_device_values = []

        self._mic_combo.addItem("System default", None)
        self._mic_device_values.append(None)

        # --- BT cards via pactl (shows even when in A2DP / no active source) ---
        try:
            cards_out = subprocess.check_output(
                ["pactl", "list", "cards"], timeout=5, text=True, stderr=subprocess.DEVNULL,
            )
            card_name = desc = ""
            has_hfp = False
            for line in cards_out.splitlines():
                s = line.strip()
                if s.startswith("Name:"):
                    card_name = s.split(":", 1)[1].strip()
                    has_hfp = False
                    desc = card_name
                elif "device.description" in s:
                    desc = s.split("=", 1)[1].strip().strip('"')
                elif "headset-head-unit" in s and "available: yes" in s:
                    has_hfp = True
                elif s == "" and card_name.startswith("bluez_card.") and has_hfp:
                    val = f"bt:{card_name}"
                    self._mic_combo.addItem(f"🎧 BT (HFP mic): {desc}", val)
                    self._mic_device_values.append(val)
                    card_name = desc = ""
                    has_hfp = False
            if card_name.startswith("bluez_card.") and has_hfp:
                val = f"bt:{card_name}"
                self._mic_combo.addItem(f"🎧 BT (HFP mic): {desc}", val)
                self._mic_device_values.append(val)
        except Exception:
            pass

        # --- Non-BT PipeWire/PA sources ---
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sources"],
                timeout=3, text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    src = parts[1]
                    if src.endswith(".monitor") or "bluez" in src.lower():
                        continue
                    val = f"pa:{src}"
                    self._mic_combo.addItem(f"🞧 PW: {src}", val)
                    self._mic_device_values.append(val)
        except Exception:
            pass

        # --- ALSA devices ---
        try:
            for dev in sd.query_devices():  # type: ignore[union-attr]
                if dev["max_input_channels"] > 0:
                    name = dev["name"]
                    self._mic_combo.addItem(f"[ALSA] {name}", name)
                    self._mic_device_values.append(name)
        except Exception:
            pass

        # Restore selection: prefer target (saved setting or current user pick)
        restored = self._mic_combo.findData(target)
        self._mic_combo.setCurrentIndex(max(0, restored))

    def _speaker_group(self) -> QGroupBox:
        from myna.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
        from myna.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

        box = QGroupBox("Speaker Verification")
        fl = QFormLayout(box)

        self._speaker_chk = QCheckBox("Enable speaker verification")
        self._speaker_chk.setChecked(self._settings.speaker.enabled)
        fl.addRow(self._speaker_chk)

        self._speaker_thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._speaker_thresh_slider.setRange(0, 100)
        self._speaker_thresh_slider.setValue(int(self._settings.speaker.threshold * 100))
        self._speaker_thresh_label = QLabel(f"{self._settings.speaker.threshold:.2f}")
        self._speaker_thresh_slider.valueChanged.connect(
            lambda v: self._speaker_thresh_label.setText(f"{v / 100:.2f}")
        )
        fl.addRow("Verification threshold:", self._speaker_thresh_slider)
        fl.addRow("", self._speaker_thresh_label)

        from PyQt6.QtWidgets import QPushButton  # noqa: PLC0415  # already imported at top

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

    def _indicator_group(self) -> QGroupBox:
        box = QGroupBox("Recording Overlay")
        fl = QFormLayout(box)

        self._visualizer_combo = QComboBox()
        self._visualizer_combo.addItems(["waveform", "blob"])
        self._visualizer_combo.setCurrentText(self._settings.indicator.visualizer)
        fl.addRow("Visualizer:", self._visualizer_combo)
        return box

    def _save(self) -> None:
        # Merge into whatever's already on disk instead of overwriting wholesale —
        # config.yaml can carry hand-tuned keys this dialog doesn't expose
        # (e.g. audio.denoise, vad.aggressiveness), and a plain overwrite silently
        # discards them on the next Save.
        base: dict = {}
        if _CONFIG_PATH.exists():
            with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
                base = yaml.safe_load(fh) or {}

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
            "speaker": {
                "enabled": self._speaker_chk.isChecked(),
                "threshold": round(self._speaker_thresh_slider.value() / 100, 2),
            },
            "postprocessing": {
                "normalize_numbers": self._num_chk.isChecked(),
                "punctuation_commands": self._punct_chk.isChecked(),
                "auto_capitalize": self._cap_chk.isChecked(),
                "language_cleanup": self._lang_chk.isChecked(),
            },
            "indicator": {"visualizer": self._visualizer_combo.currentText()},
        }

        for section, values in cfg.items():
            base.setdefault(section, {}).update(values)

        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.dump(base, fh, default_flow_style=False, allow_unicode=True)
        _LOG.info("Settings saved to %s", _CONFIG_PATH)

        try:
            import dbus  # noqa: PLC0415

            bus = dbus.SessionBus()
            # introspect=False skips the blocking introspection round-trip.
            obj = bus.get_object("com.canonical.Myna", "/com/canonical/Myna", introspect=False)
            # Fire-and-forget: reply/error handlers make the call non-blocking
            # so the Qt main thread is never frozen waiting for the reply.
            dbus.Interface(obj, "com.canonical.Myna").Reload(
                reply_handler=lambda: None,
                error_handler=lambda e: _LOG.debug("DBus Reload error: %s", e),
            )
        except Exception as exc:
            _LOG.debug("DBus notify skipped: %s", exc)

        self.accept()

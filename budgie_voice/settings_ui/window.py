"""SettingsWindow — PyQt6 QDialog for Budgie configuration.

Sidebar + stacked pages (Overview, Activation, Audio & Inference, Speaker Verification,
Post-processing, Appearance). Opens on Overview, a read-only view of the running app.

Saves to ``~/.config/budgie-voice/config.yaml`` and issues a DBus ``Reload()``
so the running service picks up changes without restart.

Privacy notice is always shown at the bottom of the dialog.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import sounddevice as sd
import yaml
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from budgie_voice.config.settings import Settings
from budgie_voice.inference.backends.registry import available_backends, models_for
from budgie_voice.settings_ui.overview import OverviewPage
from budgie_voice.ui.status import RuntimeStatus
from budgie_voice.ui.icons import pixmap
from budgie_voice.ui.style import apply_scope, set_icon
from budgie_voice.ui.theme import sync_theme
from budgie_voice.ui.widgets import SPACING, Switch, brand, heading, muted, page, section

_LOG = logging.getLogger(__name__)
_CONFIG_PATH = Path.home() / ".config" / "budgie-voice" / "config.yaml"


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


class _Pages(QStackedWidget):
    """Stack that never asks the scroll area for more width than it has.

    QScrollArea sizes its widget from sizeHint(), and a word-wrapped QLabel reports the width of
    its whole text there, which pushed every page past the window's right edge.
    """

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().sizeHint().height())


def _form() -> tuple[QWidget, QFormLayout]:
    """A borderless form block: pages get their margins from page(), not from the form."""
    box = QWidget()
    fl = QFormLayout(box)
    fl.setContentsMargins(0, 0, 0, 0)
    fl.setVerticalSpacing(SPACING)
    return box, fl


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow(QDialog):
    """Full settings dialog.

    Args:
        settings: Current application settings (read-only source).
        parent: Optional Qt parent widget.
        status: Runtime facts for the Overview page. Omitted when Settings runs
            standalone: the Overview then reports what it cannot know as Unknown.
    """

    def __init__(
        self, settings: Settings, parent: object = None, status: RuntimeStatus | None = None
    ) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._settings = settings
        self.setWindowTitle("Budgie — Settings")
        self.setMinimumSize(660, 460)
        self.resize(780, 540)

        # Pages that own widgets other code reads (combos, checkboxes) are built first;
        # the Overview needs the mic list the Audio page populates.
        activation = page(heading("Activation"), self._activation_form())
        audio = page(
            heading("Audio & Inference"),
            section("Audio"), self._audio_form(),
            section("Inference"), self._inference_form(),
        )
        speaker = page(heading("Speaker Verification"), self._speaker_form())
        postproc = page(heading("Post-processing"), self._postproc_form())
        appearance = page(
            heading("Appearance"),
            self._theme_form(),
            section("Recording overlay"), self._indicator_form(),
        )
        self._overview = OverviewPage(settings, status or RuntimeStatus(), self._mic_label)

        pages = [
            ("Overview", "layout-dashboard", self._overview), ("Activation", "keyboard", activation),
            ("Audio & Inference", "audio-lines", audio), ("Speaker Verification", "shield-check", speaker),
            ("Post-processing", "sparkles", postproc), ("Appearance", "sun-moon", appearance),
        ]
        self._stack = _Pages()
        self._nav = QListWidget()
        self._nav.setObjectName("nav")  # styled by ui.style
        self._nav.setFixedWidth(232)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setIconSize(QSize(24, 18))  # 18 px glyph on a canvas with a 6 px gap to the label
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setAccessibleName("Settings sections")
        for name, icon_name, widget in pages:
            item = QListWidgetItem(name)
            set_icon(item, icon_name)
            self._nav.addItem(item)
            self._stack.addWidget(widget)
        self._nav.currentRowChanged.connect(self._on_nav)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._stack)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)

        side = QVBoxLayout()  # app icon + name above the section list
        side.setSpacing(0)
        side.addSpacing(16)
        side.addWidget(brand(left=20))  # 20 = the nav rows' icon edge
        side.addSpacing(4)
        side.addWidget(self._nav)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addLayout(side)
        body.addWidget(divider)
        body.addWidget(scroll, stretch=1)

        lock = QLabel()
        lock.setPixmap(pixmap("lock", QColor("#8e8e93"), 14))  # muted grey, reads on light and dark
        privacy = muted("Speech recognition runs locally. No audio is saved.")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setProperty("primary", True)
        set_icon(save_btn, "check")
        set_icon(buttons.button(QDialogButtonBox.StandardButton.Cancel), "x")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        footer.setSpacing(8)
        footer.addWidget(lock, alignment=Qt.AlignmentFlag.AlignVCenter)
        footer.addWidget(privacy, stretch=1)
        footer.addWidget(buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(body, stretch=1)
        outer.addLayout(footer)

        apply_scope(self)  # font, stylesheet, icon colours: this window only
        self._nav.setCurrentRow(0)  # opens on Overview (and runs its first refresh)

    def _on_nav(self, row: int) -> None:
        self._stack.setCurrentIndex(row)
        if row == 0:
            self._overview.refresh()

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _activation_form(self) -> QWidget:
        box, fl = _form()

        self._hotkey_edit = _HotkeyEdit(self._settings.activation.hotkey)
        self._hotkey_edit.setPlaceholderText("Click here, then press your hotkey…")
        fl.addRow("Hotkey:", self._hotkey_edit)

        self._mode_toggle = QRadioButton("Toggle")
        self._mode_push = QRadioButton("Push to hold")
        (self._mode_toggle if self._settings.activation.mode == "toggle" else self._mode_push).setChecked(True)
        fl.addRow("Mode:", self._mode_toggle)
        fl.addRow("", self._mode_push)
        return box

    def _refresh_models(self, backend: str, keep: str | None = None) -> None:
        """Fill the model combo from the selected backend (first model if *keep* isn't one)."""
        models = models_for(backend)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if keep in models:
            self._model_combo.setCurrentText(keep)

    def _inference_form(self) -> QWidget:
        box, fl = _form()

        self._backend_combo = QComboBox()
        self._backend_combo.addItems(available_backends())
        self._backend_combo.setCurrentText(self._settings.inference.backend)
        fl.addRow("Backend:", self._backend_combo)

        self._model_combo = QComboBox()
        self._backend_combo.currentTextChanged.connect(self._refresh_models)
        self._refresh_models(self._backend_combo.currentText(), keep=self._settings.inference.model)
        fl.addRow("Model:", self._model_combo)

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

    def _audio_form(self) -> QWidget:
        box, fl = _form()

        self._mic_combo = QComboBox()
        # Device names can be very long; the combo shrinks to the window instead of widening it.
        self._mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._mic_combo.setMinimumContentsLength(16)
        self._mic_device_values: list[str | None] = []
        refresh_btn = QPushButton()
        refresh_btn.setProperty("iconOnly", True)
        set_icon(refresh_btn, "refresh-cw")
        refresh_btn.setFixedWidth(32)
        refresh_btn.setToolTip("Refresh device list")
        refresh_btn.setAccessibleName("Refresh device list")
        refresh_btn.clicked.connect(self._populate_mic_devices)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_row.addWidget(self._mic_combo, stretch=1)
        mic_row.addWidget(refresh_btn)
        fl.addRow("Microphone:", mic_row)

        # A checkbox can't wrap, so the caveat sits under it: one long label forced the whole
        # settings stack wider than the window.
        self._denoise_chk = Switch("Reduce background noise")
        self._denoise_chk.setChecked(self._settings.audio.denoise)
        fl.addRow(self._denoise_chk)
        fl.addRow("", muted("Can clip speech. Re-enroll your voice after changing this."))

        self._populate_mic_devices()
        return box

    def _mic_label(self, device: str) -> str | None:
        """Display name of *device* if the system lists it right now (the Overview's mic check)."""
        i = self._mic_combo.findData(device)
        return self._mic_combo.itemText(i) if i >= 0 else None

    def _populate_mic_devices(self) -> None:
        # On first call combo is empty — use saved setting as target.
        # On refresh (↻) — keep the current user selection.
        target = (
            self._mic_combo.currentData()
            if self._mic_combo.count() > 0
            else next(iter(self._settings.audio.device_list()), None)
        )
        self._mic_combo.clear()
        self._mic_device_values = []

        self._mic_combo.addItem("Auto (built-in mic first)", None)
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

    def _speaker_form(self) -> QWidget:
        from budgie_voice.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
        from budgie_voice.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

        box, fl = _form()

        self._speaker_chk = Switch("Enable speaker verification")
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

        enroll_btn = QPushButton("Enroll Now")
        set_icon(enroll_btn, "user-round-plus")
        vp = Path(self._settings.speaker.voiceprint_path).expanduser()
        enroll_btn.clicked.connect(
            lambda: EnrollmentWizard(SpeakerVerifier(vp), parent=self).show()
        )
        fl.addRow(enroll_btn)
        return box

    def _postproc_form(self) -> QWidget:
        box, fl = _form()
        pp = self._settings.postprocessing

        self._num_chk = Switch("Normalize numbers  (twenty two → 22)")
        self._num_chk.setChecked(pp.normalize_numbers)
        self._punct_chk = Switch("Punctuation commands  (period → .)")
        self._punct_chk.setChecked(pp.punctuation_commands)
        self._cap_chk = Switch("Auto-capitalise")
        self._cap_chk.setChecked(pp.auto_capitalize)
        self._lang_chk = Switch("Language cleanup  (gonna → going to)")
        self._lang_chk.setChecked(pp.language_cleanup)

        for chk in (self._num_chk, self._punct_chk, self._cap_chk, self._lang_chk):
            fl.addRow(chk)
        return box

    def _theme_form(self) -> QWidget:
        box, fl = _form()
        self._theme_combo = QComboBox()
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self._theme_combo.addItem(label, value)
        self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(self._settings.ui.theme)))
        fl.addRow("Theme:", self._theme_combo)
        fl.addRow("", muted("System follows your desktop's light or dark setting."))
        return box

    def _indicator_form(self) -> QWidget:
        box, fl = _form()

        self._visualizer_combo = QComboBox()
        self._visualizer_combo.addItems(["waveform", "blob"])
        self._visualizer_combo.setCurrentText(self._settings.indicator.visualizer)
        fl.addRow("Visualizer:", self._visualizer_combo)
        return box

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _mic_devices(self) -> list[str]:
        """Selected mic first; fallbacks already in the config are kept behind it."""
        chosen = self._mic_combo.currentData()
        if chosen is None:
            return []  # Auto
        return [chosen] + [d for d in self._settings.audio.device_list() if d != chosen]

    def _save(self) -> None:
        # Merge into whatever's already on disk instead of overwriting wholesale —
        # config.yaml can carry hand-tuned keys this dialog doesn't expose
        # (e.g. vad.aggressiveness, audio.input_gain_pct), and a plain overwrite silently
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
            "audio": {"devices": self._mic_devices(), "denoise": self._denoise_chk.isChecked()},
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
            "ui": {"theme": self._theme_combo.currentData()},
        }

        for section_name, values in cfg.items():
            current = base.get(section_name)
            if not isinstance(current, dict):  # missing, or left empty (None) in the YAML
                current = base[section_name] = {}
            current.update(values)
        base["audio"].pop("device", None)  # superseded by audio.devices

        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.dump(base, fh, default_flow_style=False, allow_unicode=True)
        _LOG.info("Settings saved to %s", _CONFIG_PATH)

        # Live, no restart: Settings runs inside the app, so the theme is applied right here. The
        # Reload below sees the mode already in effect and does nothing more.
        sync_theme(QApplication.instance(), cfg["ui"]["theme"])

        try:
            import dbus  # noqa: PLC0415

            bus = dbus.SessionBus()
            # introspect=False skips the blocking introspection round-trip.
            obj = bus.get_object("io.gitlab.karanaithal841.BudgieVoice", "/io/gitlab/karanaithal841/BudgieVoice", introspect=False)
            # Fire-and-forget: reply/error handlers make the call non-blocking
            # so the Qt main thread is never frozen waiting for the reply.
            dbus.Interface(obj, "io.gitlab.karanaithal841.BudgieVoice").Reload(
                reply_handler=lambda: None,
                error_handler=lambda e: _LOG.debug("DBus Reload error: %s", e),
            )
        except Exception as exc:
            _LOG.debug("DBus notify skipped: %s", exc)

        self.accept()

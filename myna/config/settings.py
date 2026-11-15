"""Load and validate ~/.config/myna/config.yaml → Settings dataclass."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


_LOG = logging.getLogger(__name__)
_DEFAULT_CONFIG = Path.home() / ".config" / "myna" / "config.yaml"


@dataclass
class ActivationSettings:
    """Hotkey activation configuration."""

    mode: Literal["toggle", "push_to_hold"] = "toggle"
    hotkey: str = "ctrl+space"

    # Which mechanism owns the hotkey. Exactly one may own it: a single key
    # press can otherwise reach DictationService.toggle() twice (pynput sees
    # the combo AND a GNOME custom shortcut fires the DBus Toggle method),
    # which starts a session and immediately stops it.
    #   "pynput" — in-process global listener. X11 only; needs no desktop setup.
    #   "dbus"   — external caller (GNOME custom shortcut) invokes Toggle.
    #              The only option that works on native Wayland.
    #   "auto"   — pick from the session type (see resolved_trigger).
    trigger: Literal["auto", "pynput", "dbus"] = "auto"

    # Window in which a second toggle from the *same* source is treated as a
    # duplicate. Backstop for key auto-repeat and double-clicks; cross-source
    # duplicates are handled by `trigger` above, not by timing. Load-dependent,
    # so it stays a knob: raise it if repeats still slip through.
    debounce_ms: int = 350

    def resolved_trigger(self) -> Literal["pynput", "dbus"]:
        """Resolve ``"auto"`` against the current session type.

        pynput cannot observe global keys on native Wayland (the protocol has
        no client-side global grab), so Wayland must be driven over DBus. X11
        prefers pynput because it needs no desktop configuration.
        """
        if self.trigger != "auto":
            return self.trigger  # type: ignore[return-value]
        return "dbus" if os.environ.get("XDG_SESSION_TYPE") == "wayland" else "pynput"


@dataclass
class AudioSettings:
    """Audio capture configuration."""

    # Ordered preference list; the first entry that works at session start wins.
    # Entries: "bt:<card>" | "pa:<source>" | an ALSA device name. Empty = Auto
    # (first real mic that is not a monitor/loopback/digital/Bluetooth source,
    # else the system default).
    devices: list[str] = field(default_factory=list)
    device: str | None = None  # legacy single entry; read via device_list()
    sample_rate: int = 16000
    chunk_duration_ms: int = 30
    format: Literal["pcm16", "float32"] = "pcm16"
    denoise: bool = True
    # Bluetooth mic gain, applied when a "bt:" device is pinned. None = leave alone.
    # Clamped to 50-150: 150 clipped hard on the Nord Buds 3 Pro.
    input_gain_pct: int | None = None

    def device_list(self) -> list[str]:
        """Preference list, honouring the legacy single ``device`` key."""
        return list(self.devices) or ([self.device] if self.device else [])


@dataclass
class VadSettings:
    """Silero VAD configuration."""

    enabled: bool = True
    aggressiveness: int = 2
    speech_pad_ms: int = 300
    # Audio kept from before the first speech chunk of a turn, so soft word
    # onsets ("fifteen", "should") are not clipped.
    pre_roll_ms: int = 300


@dataclass
class InferenceSettings:
    """Inference server configuration."""

    backend: str = "faster_whisper"
    server_port: int = 18000
    model: str = "small"
    device: str = "auto"
    compute_type: str = "int8"
    language: str = "en"
    confidence_threshold: float = 0.40
    beam_size: int = 3
    # Max wait at stop for the last turn's transcription before giving up.
    finalize_timeout_s: float = 15.0
    # An end-of-speech hint closes a turn only once it holds this much audio, so a
    # short dictation is transcribed in one pass instead of cut at every pause.
    min_turn_s: float = 15.0
    # Hallucination guards (faster-whisper decode-time): reject segments that
    # look like silence-babble rather than real speech.
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4


@dataclass
class SpeakerSettings:
    """Speaker verification configuration."""

    enabled: bool = True
    voiceprint_path: str = "~/.config/myna/voiceprint.pt"
    threshold: float = 0.65


@dataclass
class LearningSettings:
    """On-device adaptation. Both stores hold derived data only — never audio
    or transcripts: voiceprint = one embedding vector, vocab = word counts."""

    adapt_voiceprint: bool = True
    vocab_enabled: bool = True
    vocab_path: str = "~/.config/myna/vocab.json"
    # Hand-edited list of names/jargon (one per line) sent to the decoder as hotwords.
    dictionary_path: str = "~/.config/myna/dictionary.txt"
    corrections_enabled: bool = True
    corrections_path: str = "~/.config/myna/corrections.json"


@dataclass
class PostprocessingSettings:
    """Post-processing pipeline toggles."""

    normalize_numbers: bool = True
    auto_capitalize: bool = True
    punctuation_commands: bool = True
    language_cleanup: bool = True


@dataclass
class InjectionSettings:
    """Text injection configuration."""

    backend: str = "auto"
    delay_ms: int = 12
    block_secure_fields: bool = True
    preview_before_inject: bool = True


@dataclass
class IndicatorSettings:
    """Floating recording-overlay configuration."""

    visualizer: Literal["waveform", "blob"] = "waveform"


@dataclass
class LoggingSettings:
    """Logging configuration."""

    level: str = "INFO"


@dataclass
class NotificationSettings:
    """Desktop notification messages."""

    tool: str = "notify-send"
    low_confidence_message: str = "⚠️ Couldn't hear you — repeat?"
    secure_field_message: str = "🔒 Dictation blocked in secure field."


@dataclass
class Settings:
    """Root application settings."""

    activation: ActivationSettings = field(default_factory=ActivationSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    vad: VadSettings = field(default_factory=VadSettings)
    inference: InferenceSettings = field(default_factory=InferenceSettings)
    speaker: SpeakerSettings = field(default_factory=SpeakerSettings)
    learning: LearningSettings = field(default_factory=LearningSettings)
    postprocessing: PostprocessingSettings = field(default_factory=PostprocessingSettings)
    injection: InjectionSettings = field(default_factory=InjectionSettings)
    indicator: IndicatorSettings = field(default_factory=IndicatorSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)


def _merge(instance: object, data: dict) -> None:
    """Recursively merge *data* dict into a nested dataclass *instance*."""
    for key, val in data.items():
        if not hasattr(instance, key):
            _LOG.warning("Unknown config key ignored: %s", key)
            continue
        attr = getattr(instance, key)
        if hasattr(attr, "__dataclass_fields__") and isinstance(val, dict):
            _merge(attr, val)
        else:
            setattr(instance, key, val)


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from YAML; fall back to defaults when file is missing.

    Args:
        path: Path to config.yaml; defaults to ~/.config/myna/config.yaml.

    Returns:
        Populated Settings dataclass.

    Raises:
        ValueError: File exists but fails YAML parsing.
    """
    cfg_path = path or _DEFAULT_CONFIG
    settings = Settings()

    if not cfg_path.exists():
        _LOG.info("Config not found at %s — using defaults", cfg_path)
        return settings

    try:
        with cfg_path.open("r", encoding="utf-8") as fh:
            data: dict = yaml.safe_load(fh) or {}
        _merge(settings, data)
        _LOG.info("Config loaded from %s", cfg_path)
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse {cfg_path}: {exc}") from exc

    return settings

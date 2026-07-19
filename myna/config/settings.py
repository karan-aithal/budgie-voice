"""Load and validate ~/.config/myna/config.yaml → Settings dataclass."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from myna.utils.exceptions import MynaConfigError

_LOG = logging.getLogger(__name__)
_DEFAULT_CONFIG = Path.home() / ".config" / "myna" / "config.yaml"


@dataclass
class ActivationSettings:
    """Hotkey activation configuration."""

    mode: Literal["toggle", "push_to_hold"] = "toggle"
    hotkey: str = "ctrl+space"


@dataclass
class AudioSettings:
    """Audio capture configuration."""

    device: str | None = None
    sample_rate: int = 16000
    chunk_duration_ms: int = 30
    format: Literal["pcm16", "float32"] = "pcm16"
    max_buffer_seconds: int = 30
    denoise: bool = True


@dataclass
class VadSettings:
    """Silero VAD configuration."""

    enabled: bool = True
    aggressiveness: int = 2
    speech_pad_ms: int = 300


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
    min_samples: int = 5


@dataclass
class LearningSettings:
    """On-device adaptation. Both stores hold derived data only — never audio
    or transcripts: voiceprint = one embedding vector, vocab = word counts."""

    adapt_voiceprint: bool = True
    vocab_enabled: bool = True
    vocab_path: str = "~/.config/myna/vocab.json"
    corrections_enabled: bool = True
    corrections_path: str = "~/.config/myna/corrections.json"


@dataclass
class PostprocessingSettings:
    """Post-processing pipeline toggles."""

    normalize_numbers: bool = True
    auto_capitalize: bool = True
    punctuation_commands: bool = True
    language_cleanup: bool = True
    llm_cleanup: bool = False


@dataclass
class InjectionSettings:
    """Text injection configuration."""

    backend: str = "auto"
    delay_ms: int = 12
    clear_modifiers: bool = True
    block_secure_fields: bool = True
    preview_before_inject: bool = True


@dataclass
class DbusSettings:
    """DBus IPC configuration."""

    well_known_name: str = "com.canonical.Myna"


@dataclass
class IndicatorSettings:
    """System tray indicator configuration."""

    enabled: bool = True
    show_tooltip_state: bool = True


@dataclass
class LoggingSettings:
    """Logging configuration."""

    level: str = "INFO"
    max_bytes: int = 5_242_880
    backup_count: int = 3


@dataclass
class NotificationSettings:
    """Desktop notification messages."""

    tool: str = "notify-send"
    low_confidence_message: str = "⚠️ Couldn't hear you — repeat?"
    secure_field_message: str = "🔒 Dictation blocked in secure field."
    no_mic_message: str = "🎤 No microphone found."


@dataclass
class PerformanceSettings:
    """Performance targets (ms)."""

    mic_start_target_ms: int = 100
    indicator_visible_target_ms: int = 200
    first_transcription_target_ms: int = 800
    final_commit_target_ms: int = 2000


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
    dbus: DbusSettings = field(default_factory=DbusSettings)
    indicator: IndicatorSettings = field(default_factory=IndicatorSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)


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
        MynaConfigError: File exists but fails YAML parsing.
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
        raise MynaConfigError(f"Failed to parse {cfg_path}: {exc}") from exc

    return settings

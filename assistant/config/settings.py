"""Load and expose typed configuration from config.yaml.

Config file location:
    ~/.config/voice-assistant/config.yaml

If absent, the bundled ``config.yaml.example`` is copied there automatically.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from assistant.utils.exceptions import ConfigError

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".config" / "voice-assistant"
_CONFIG_PATH = _CONFIG_DIR / "config.yaml"

# project_root/config.yaml.example
_EXAMPLE_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml.example"


# ---------------------------------------------------------------------------
# Leaf settings
# ---------------------------------------------------------------------------


@dataclass
class HotkeySettings:
    """Keyboard shortcut configuration."""

    toggle_listen: str = "ctrl+space"


@dataclass
class AudioSettings:
    """Microphone and audio stream configuration."""

    device: str | int | None = None

    # Whisper/Faster-Whisper native sample rate
    sample_rate: int = 16000

    # Lower values reduce latency but increase callback frequency.
    # 20 ms is a good desktop balance.
    chunk_duration_ms: int = 20


@dataclass
class VadSettings:
    """Voice activity detection configuration."""

    enabled: bool = True

    # 0–3
    aggressiveness: int = 2

    # Wait this much silence before finalizing speech.
    speech_pad_ms: int = 150

    # Ignore extremely short utterances.
    min_speech_ms: int = 300

    # Emit rolling partial transcription every N milliseconds.
    # 0 disables rolling updates.
    live_chunk_ms: int = 1000


@dataclass
class SttSettings:
    """Speech-to-text configuration."""

    mode: Literal[
        "realtime",
        "batch",
    ] = "realtime"

    model: Literal[
        "tiny",
        "small",
        "medium",
        "large-v3",
    ] = "small"

    realtime_model: Literal[
        "tiny",
        "small",
        "medium",
    ] = "tiny"

    device: Literal[
        "auto",
        "cpu",
        "cuda",
    ] = "auto"

    compute_type: Literal[
        "int8",
        "int8_float16",
        "float16",
        "float32",
    ] = "int8"

    language: str = "en"

    confidence_threshold: float = 0.50

    beam_size: int = 1

    # Optional advanced settings
    initial_prompt: str = ""

    condition_on_previous_text: bool = True

    temperature: float = 0.0


@dataclass
class SpeakerSettings:
    """ECAPA-TDNN speaker verification configuration."""

    enabled: bool = True

    voiceprint_path: str = "~/.config/voice-assistant/voiceprint.pt"

    threshold: float = 0.72

    min_samples: int = 5


@dataclass
class InjectionSettings:
    """xdotool text injection configuration."""

    delay_ms: int = 8

    clear_modifiers: bool = True


@dataclass
class TraySettings:
    """System tray configuration."""

    enabled: bool = True


@dataclass
class LoggingSettings:
    """Logging configuration."""

    level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    ] = "INFO"

    max_bytes: int = 5_242_880

    backup_count: int = 3


@dataclass
class NotificationSettings:
    """Desktop notification configuration."""

    low_confidence_message: str = (
        "⚠️ I couldn't hear you clearly — could you repeat that?"
    )

    tool: Literal[
        "notify-send",
        "libnotify",
    ] = "notify-send"


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """Top-level application settings."""

    hotkey: HotkeySettings = field(default_factory=HotkeySettings)

    audio: AudioSettings = field(default_factory=AudioSettings)

    vad: VadSettings = field(default_factory=VadSettings)

    stt: SttSettings = field(default_factory=SttSettings)

    speaker: SpeakerSettings = field(default_factory=SpeakerSettings)

    injection: InjectionSettings = field(default_factory=InjectionSettings)

    tray: TraySettings = field(default_factory=TraySettings)

    logging: LoggingSettings = field(default_factory=LoggingSettings)

    notifications: NotificationSettings = field(default_factory=NotificationSettings)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _merge(instance: object, data: dict[str, Any]) -> None:
    """Recursively merge *data* into a dataclass instance.

    Unknown keys are ignored to allow forward-compatible configuration files.
    """

    for key, value in data.items():
        if not hasattr(instance, key):
            logger.debug("Ignoring unknown config key: %s", key)
            continue

        current = getattr(instance, key)

        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(instance, key, value)


def _validate(settings: Settings) -> None:
    """Validate configuration values.

    Raises:
        ConfigError: If any configuration value is invalid.
    """

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    if settings.audio.sample_rate <= 0:
        raise ConfigError("audio.sample_rate must be greater than 0")

    if settings.audio.chunk_duration_ms <= 0:
        raise ConfigError("audio.chunk_duration_ms must be greater than 0")

    # ------------------------------------------------------------------
    # VAD
    # ------------------------------------------------------------------

    if settings.vad.aggressiveness not in (0, 1, 2, 3):
        raise ConfigError("vad.aggressiveness must be one of: 0, 1, 2, 3")

    if settings.vad.speech_pad_ms < 0:
        raise ConfigError("vad.speech_pad_ms must be >= 0")

    if settings.vad.min_speech_ms < 0:
        raise ConfigError("vad.min_speech_ms must be >= 0")

    if settings.vad.live_chunk_ms < 0:
        raise ConfigError("vad.live_chunk_ms must be >= 0")

    # ------------------------------------------------------------------
    # STT
    # ------------------------------------------------------------------

    if not 0.0 <= settings.stt.confidence_threshold <= 1.0:
        raise ConfigError("stt.confidence_threshold must be between 0 and 1")

    if settings.stt.beam_size < 1:
        raise ConfigError("stt.beam_size must be at least 1")

    if settings.stt.temperature < 0:
        raise ConfigError("stt.temperature must be >= 0")

    # ------------------------------------------------------------------
    # Speaker verification
    # ------------------------------------------------------------------

    if not 0.0 <= settings.speaker.threshold <= 1.0:
        raise ConfigError("speaker.threshold must be between 0 and 1")

    if settings.speaker.min_samples < 1:
        raise ConfigError("speaker.min_samples must be at least 1")

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    if settings.injection.delay_ms < 0:
        raise ConfigError("injection.delay_ms must be >= 0")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    if settings.logging.max_bytes <= 0:
        raise ConfigError("logging.max_bytes must be greater than 0")

    if settings.logging.backup_count < 0:
        raise ConfigError("logging.backup_count must be >= 0")


def _normalize(settings: Settings) -> None:
    """Normalize loaded configuration."""

    settings.speaker.voiceprint_path = str(
        Path(settings.speaker.voiceprint_path).expanduser()
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML configuration file."""

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    except yaml.YAMLError as exc:
        raise ConfigError(f"Cannot parse configuration '{path}': {exc}") from exc

    except OSError as exc:
        raise ConfigError(f"Unable to read configuration '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a mapping.")

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_settings(config_path: Path | None = None) -> Settings:
    """Load application settings.

    If the configuration file does not exist:

    * Create the parent directory.
    * Copy ``config.yaml.example`` if available.
    * Otherwise fall back to built-in defaults.

    Args:
        config_path: Optional override for the default configuration path.

    Returns:
        Fully populated :class:`Settings`.

    Raises:
        ConfigError:
            If the configuration cannot be read, parsed, or validated.
    """

    path = config_path or _CONFIG_PATH

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

        if _EXAMPLE_PATH.exists():
            try:
                shutil.copy(_EXAMPLE_PATH, path)
                logger.info(
                    "Configuration not found; copied example to %s",
                    path,
                )

            except OSError as exc:
                raise ConfigError(
                    f"Unable to create configuration '{path}': {exc}"
                ) from exc

        else:
            logger.info(
                "No configuration found and no example available; "
                "using built-in defaults."
            )

            settings = Settings()
            _normalize(settings)
            _validate(settings)
            return settings

    raw = _load_yaml(path)

    settings = Settings()

    _merge(settings, raw)

    _normalize(settings)

    _validate(settings)

    logger.debug("Loaded configuration from %s", path)

    return settings

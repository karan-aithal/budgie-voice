"""Load and expose typed configuration from config.yaml.

Config file location: ``~/.config/voice-assistant/config.yaml``.
If absent, the bundled ``config.yaml.example`` is copied there automatically.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from assistant.utils.exceptions import ConfigError

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".config" / "voice-assistant"
_CONFIG_PATH = _CONFIG_DIR / "config.yaml"
# Resolved relative to this file → project root / config.yaml.example
_EXAMPLE_PATH = Path(__file__).parent.parent.parent / "config.yaml.example"


# ---------------------------------------------------------------------------
# Leaf settings dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HotkeySettings:
    """Keyboard shortcut configuration."""

    toggle_listen: str = "ctrl+space"


@dataclass
class AudioSettings:
    """Microphone and audio stream configuration."""

    device: Optional[str | int] = None
    sample_rate: int = 16000
    chunk_duration_ms: int = 30


@dataclass
class VadSettings:
    """Silero VAD configuration."""

    enabled: bool = True
    aggressiveness: int = 2
    speech_pad_ms: int = 300


@dataclass
class SttSettings:
    """Faster-Whisper STT configuration."""

    model: str = "small"
    device: str = "auto"
    compute_type: str = "int8"
    language: str = "en"
    confidence_threshold: float = 0.55
    beam_size: int = 5


@dataclass
class SpeakerSettings:
    """ECAPA-TDNN speaker verification configuration."""

    enabled: bool = True
    voiceprint_path: str = "~/.config/voice-assistant/voiceprint.pt"
    threshold: float = 0.75
    min_samples: int = 5


@dataclass
class InjectionSettings:
    """xdotool text injection configuration."""

    delay_ms: int = 12
    clear_modifiers: bool = True


@dataclass
class TraySettings:
    """System tray icon configuration."""

    enabled: bool = True


@dataclass
class LoggingSettings:
    """Logging configuration."""

    level: str = "INFO"
    max_bytes: int = 5_242_880
    backup_count: int = 3


@dataclass
class NotificationSettings:
    """Desktop notification configuration."""

    low_confidence_message: str = (
        "⚠️ I couldn't hear you clearly — could you repeat that?"
    )
    tool: str = "notify-send"


# ---------------------------------------------------------------------------
# Root settings dataclass
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """Top-level application settings.

    All nested dataclasses carry their own defaults so the app runs even
    without a config file.
    """

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


def _merge(instance: object, data: dict) -> None:
    """Recursively merge *data* dict into a dataclass *instance* in-place.

    Unknown keys are silently ignored so forward-compatible configs don't
    raise errors.

    Args:
        instance: A dataclass instance to update.
        data: Dict of values to apply (may be nested).
    """
    for key, value in data.items():
        if not hasattr(instance, key):
            continue
        attr = getattr(instance, key)
        if hasattr(attr, "__dataclass_fields__") and isinstance(value, dict):
            _merge(attr, value)
        else:
            setattr(instance, key, value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from *config_path* (defaults to ``~/.config/voice-assistant/config.yaml``).

    Behaviour when file is absent:
    - Copies ``config.yaml.example`` to the config dir and logs INFO.
    - Falls back to built-in defaults if the example is also missing.

    Args:
        config_path: Override the default config file location.

    Returns:
        Fully populated :class:`Settings` instance.

    Raises:
        ConfigError: If the YAML file exists but cannot be parsed.
    """
    path = config_path or _CONFIG_PATH

    if not path.exists():
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if _EXAMPLE_PATH.exists():
            shutil.copy(_EXAMPLE_PATH, path)
            logger.info("Config not found — copied example → %s", path)
        else:
            logger.info("No config found and no example; using built-in defaults")
            return Settings()

    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Cannot parse config {path}: {exc}") from exc

    settings = Settings()
    _merge(settings, raw)
    return settings

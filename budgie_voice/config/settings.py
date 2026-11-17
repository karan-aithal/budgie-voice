"""Load and validate ~/.config/budgie-voice/config.yaml → Settings dataclass."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


_LOG = logging.getLogger(__name__)
_DEFAULT_CONFIG = Path.home() / ".config" / "budgie-voice" / "config.yaml"


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
    #   "evdev"  — in-process listener on /dev/input. X11 and Wayland, supports
    #              push_to_hold; needs read access to /dev/input (input group).
    #   "dbus"   — external caller (GNOME custom shortcut) invokes Toggle.
    #              Works everywhere, but has no key-release, so no push_to_hold.
    #   "auto"   — pick from the session type (see resolved_trigger).
    trigger: Literal["auto", "pynput", "evdev", "dbus"] = "auto"

    # Hotkey used when evdev owns the key. Separate from `hotkey` so nothing changes
    # for pynput users: evdev cannot grab the key, so it also reaches the focused app,
    # and ctrl+space collides with editor autocomplete and input-method switching.
    # F9: on laptop and full-size keyboards alike, one key so hold-to-talk needs no
    # modifier handling. Some apps bind it; "pause" is the quieter key if yours has one.
    evdev_hotkey: str = "f9"

    # Command mode (needs llm.enabled): select text, press this, speak an instruction.
    # evdev only; the DBus method Command does the same from a desktop shortcut.
    # Empty = no key observed at all, so nothing changes until you opt in (e.g. "f10").
    command_hotkey: str = ""

    # Window in which a second toggle from the *same* source is treated as a
    # duplicate. Backstop for key auto-repeat and double-clicks; cross-source
    # duplicates are handled by `trigger` above, not by timing. Load-dependent,
    # so it stays a knob: raise it if repeats still slip through.
    debounce_ms: int = 350

    # Pause whatever is playing (via playerctl) while dictating, resume after.
    # Without playerctl this does nothing.
    pause_media: bool = False

    def resolved_trigger(self) -> Literal["pynput", "evdev", "dbus"]:
        """Resolve ``"auto"`` against the current session type.

        pynput cannot observe global keys on native Wayland (the protocol has
        no client-side global grab). X11 prefers pynput because it needs no
        desktop configuration and no ``input`` group. Wayland uses evdev when a
        keyboard device is readable, else DBus.
        """
        if self.trigger != "auto":
            return self.trigger  # type: ignore[return-value]
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            return "pynput"
        try:
            from budgie_voice.orchestrator.evdev_hotkey import auto_backend  # noqa: PLC0415 — evdev only matters on Wayland
        except ImportError:
            _LOG.warning("trigger auto → dbus: python-evdev is not installed")
            return "dbus"
        return auto_backend()

    def active_hotkey(self) -> str:
        """The hotkey string that is live for the resolved trigger."""
        return self.evdev_hotkey if self.resolved_trigger() == "evdev" else self.hotkey


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
    # noisereduce gates isolated 30 ms chunks and can turn real speech into silence,
    # so it is opt-in. Enrollment uses the same setting: re-enroll after changing it.
    denoise: bool = False
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
    language: str = "en"  # BCP-47 code, or "auto" (Whisper detects it; Parakeet uses en)
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
    voiceprint_path: str = "~/.config/budgie-voice/voiceprint.pt"
    threshold: float = 0.65


@dataclass
class LearningSettings:
    """On-device adaptation. Both stores hold derived data only — never audio
    or transcripts: voiceprint = one embedding vector, vocab = word counts."""

    adapt_voiceprint: bool = True
    vocab_enabled: bool = True
    vocab_path: str = "~/.config/budgie-voice/vocab.json"
    # Hand-edited list of names/jargon (one per line) sent to the decoder as hotwords.
    dictionary_path: str = "~/.config/budgie-voice/dictionary.txt"
    corrections_enabled: bool = True
    corrections_path: str = "~/.config/budgie-voice/corrections.json"


@dataclass
class PostprocessingSettings:
    """Post-processing pipeline toggles."""

    normalize_numbers: bool = True
    auto_capitalize: bool = True
    punctuation_commands: bool = True
    language_cleanup: bool = True
    # Remove um/uh/er/hmm. Off: it can't tell a filler from a deliberate "hm".
    strip_fillers: bool = False
    # Spoken "scratch that" deletes the sentence before it.
    spoken_edits: bool = True
    # Drop one final "." — for terminals and code editors, via profiles below.
    strip_trailing_period: bool = False
    # Spoken phrase -> replacement text, e.g. {"my email": "me@example.com"}. Whole
    # phrase, case-insensitive; the text is inserted exactly as written.
    snippets: dict[str, str] = field(default_factory=dict)
    # Per-app overrides, X11 only. First entry whose `match` regex hits the target
    # window's WM_CLASS wins; every other key overrides the toggle of that name
    # (normalize_numbers, auto_capitalize, punctuation_commands, language_cleanup,
    # strip_fillers, spoken_edits, strip_trailing_period). See config.yaml.example.
    profiles: list[dict[str, object]] = field(default_factory=list)


@dataclass
class InjectionSettings:
    """Text injection configuration."""

    backend: str = "auto"
    delay_ms: int = 12
    block_secure_fields: bool = True
    preview_before_inject: bool = True


@dataclass
class LlmSettings:
    """Optional local LLM: transcript polish and command mode. Off by default.

    Any OpenAI-compatible server: LM Studio (1234) or Ollama (11434).
    """

    enabled: bool = False
    url: str = "http://127.0.0.1:1234/v1"  # LM Studio's default; Ollama is http://127.0.0.1:11434/v1
    model: str = ""  # no default: the model id your server lists (LM Studio: "qwen/qwen3.5-4b")
    timeout_s: float = 8.0
    # Dictated text must not leave the machine by accident: a non-loopback url is refused
    # unless this is true.
    allow_remote: bool = False
    min_words: int = 8  # polish only dictations at least this long
    max_len_ratio: float = 0.5  # polish answer is rejected if its length differs by more


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
    llm: LlmSettings = field(default_factory=LlmSettings)
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
        path: Path to config.yaml; defaults to ~/.config/budgie-voice/config.yaml.

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

"""Custom exceptions for the Voice Assistant.

All domain errors inherit from VoiceAssistantError so callers can
catch the base class or specific sub-types as needed.
"""


class VoiceAssistantError(Exception):
    """Base exception for all voice-assistant errors."""


class AudioCaptureError(VoiceAssistantError):
    """Raised when microphone capture fails to open or read."""


class VADError(VoiceAssistantError):
    """Raised when Silero VAD fails to load or process audio."""


class RecognizerError(VoiceAssistantError):
    """Raised when Faster-Whisper fails to load or transcribe."""


class ConfigError(VoiceAssistantError):
    """Raised when config.yaml is missing, unreadable, or invalid."""


class InjectorError(VoiceAssistantError):
    """Raised when xdotool is missing or text injection fails."""


class SpeakerVerificationError(VoiceAssistantError):
    """Raised when ECAPA-TDNN model fails to load or infer."""


class EnrollmentError(VoiceAssistantError):
    """Raised when the enrollment wizard cannot save a voiceprint."""

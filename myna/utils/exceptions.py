"""Custom exceptions for Myna."""


class MynaError(Exception):
    """Base Myna exception."""


class MynaMicError(MynaError):
    """Microphone unavailable or capture failure."""


class MynaInferenceError(MynaError):
    """Inference server/backend failure."""


class MynaInjectionError(MynaError):
    """Text injection failure."""


class MynaConfigError(MynaError):
    """Configuration load/validation failure."""


class MynaDBusError(MynaError):
    """DBus communication failure."""


class MynaSpeakerError(MynaError):
    """Speaker verification failure."""


class MynaSessionError(MynaError):
    """Session state-machine violation."""

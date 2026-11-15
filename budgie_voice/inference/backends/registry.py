"""Backend name → class registry.

Add new backends here; no changes required elsewhere.
"""
from __future__ import annotations

from budgie_voice.inference.backends.base import InferenceBackend
from budgie_voice.inference.backends.faster_whisper import FasterWhisperBackend
from budgie_voice.inference.backends.parakeet import ParakeetBackend

_REGISTRY: dict[str, type[InferenceBackend]] = {
    "faster_whisper": FasterWhisperBackend,
    "parakeet": ParakeetBackend,
}


def get_backend(name: str) -> InferenceBackend:
    """Instantiate an unloaded backend by registry name.

    Args:
        name: Backend identifier (e.g. ``"faster_whisper"``).

    Returns:
        Unloaded :class:`~budgie_voice.inference.backends.base.InferenceBackend` instance.

    Raises:
        KeyError: *name* is not in the registry.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        available = ", ".join(_REGISTRY)
        raise KeyError(f"Unknown backend {name!r}. Available: {available}")
    return cls()


def available_backends() -> list[str]:
    """Return sorted list of registered backend names."""
    return sorted(_REGISTRY)


DEFAULT_BACKEND = "faster_whisper"
DEFAULT_MODEL = "small"


def models_for(name: str) -> list[str]:
    """Models the named backend supports (empty for an unknown backend)."""
    cls = _REGISTRY.get(name)
    return list(cls.SUPPORTED_MODELS) if cls else []


def resolve(backend: str, model: str) -> tuple[str, str, str | None]:
    """Validate a backend/model pair, falling back to defaults.

    Returns:
        ``(backend, model, problem)``; *problem* is a human-readable reason when
        a fallback was applied, else ``None``.
    """
    if backend not in _REGISTRY:
        return DEFAULT_BACKEND, DEFAULT_MODEL, f"Unknown backend {backend!r} — using {DEFAULT_BACKEND}/{DEFAULT_MODEL}"
    models = models_for(backend)
    if model not in models:
        return backend, models[0], f"{backend} has no model {model!r} — using {models[0]}"
    return backend, model, None

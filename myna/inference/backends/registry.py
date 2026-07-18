"""Backend name → class registry.

Add new backends here; no changes required elsewhere.
"""
from __future__ import annotations

from myna.inference.backends.base import InferenceBackend
from myna.inference.backends.faster_whisper import FasterWhisperBackend
from myna.inference.backends.parakeet import ParakeetBackend

_REGISTRY: dict[str, type[InferenceBackend]] = {
    "faster_whisper": FasterWhisperBackend,
    "parakeet": ParakeetBackend,
    # "nemotron":     NemotronBackend,    # future
    # "qwen3_asr":    Qwen3AsrBackend,    # future
}


def get_backend(name: str) -> InferenceBackend:
    """Instantiate an unloaded backend by registry name.

    Args:
        name: Backend identifier (e.g. ``"faster_whisper"``).

    Returns:
        Unloaded :class:`~myna.inference.backends.base.InferenceBackend` instance.

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

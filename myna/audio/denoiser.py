"""Optional noisereduce wrapper — zero-cost passthrough when disabled."""
from __future__ import annotations

import logging
import warnings

import numpy as np

_LOG = logging.getLogger(__name__)


class DenoiseFilter:
    """Wraps the *noisereduce* library for background noise suppression.

    Args:
        enabled: If False, :meth:`apply` is a zero-cost identity.
        sample_rate: Audio sample rate in Hz.
    """

    def __init__(self, enabled: bool, sample_rate: int = 16000) -> None:
        self._enabled = enabled
        self._sample_rate = sample_rate
        self._nr: object | None = None

        if enabled:
            try:
                import noisereduce as nr  # noqa: PLC0415

                self._nr = nr
                _LOG.info("DenoiseFilter enabled (noisereduce)")
            except ImportError:
                _LOG.warning("noisereduce not installed — DenoiseFilter disabled")
                self._enabled = False

    def apply(self, audio: np.ndarray) -> np.ndarray:
        """Denoise a mono float32 array.

        Args:
            audio: 1-D float32 array at the configured sample rate.

        Returns:
            Denoised array of the same shape; passthrough if disabled.
        """
        if not self._enabled or self._nr is None:
            return audio
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return self._nr.reduce_noise(  # type: ignore[union-attr]
                    y=audio.astype(np.float32),
                    sr=self._sample_rate,
                    stationary=False,
                    prop_decrease=0.75,
                )
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("Denoise failed — using raw audio: %s", exc)
            return audio

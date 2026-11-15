"""Lightweight per-frame spectral analysis for the recording visualizer.

CPU-only, no GPU: a single Hann-windowed real FFT on one ~30ms int16 PCM
frame costs microseconds on any modern CPU.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

_BASS_HZ = (20.0, 300.0)
_MID_HZ = (300.0, 2000.0)
_TREBLE_HZ = 2000.0


class AudioLevel(NamedTuple):
    """One frame's loudness + spectral balance.

    Attributes:
        level: Peak amplitude, 0..1.
        bass: Normalized low-frequency energy proportion, 0..1.
        mid: Normalized mid-frequency energy proportion, 0..1.
        treble: Normalized high-frequency energy proportion, 0..1.
    """

    level: float
    bass: float
    mid: float
    treble: float


_SILENT = AudioLevel(0.0, 0.0, 0.0, 0.0)


def analyze(pcm: np.ndarray, sample_rate: int = 16_000) -> AudioLevel:
    """Compute loudness + bass/mid/treble energy split for one PCM frame.

    Args:
        pcm: 1-D int16 samples for a single audio frame (~30ms).
        sample_rate: Samples per second.

    Returns:
        :class:`AudioLevel` for this frame.
    """
    if len(pcm) == 0:
        return _SILENT

    samples = pcm.astype(np.float32) / 32_767.0
    level = float(np.abs(samples).max())

    windowed = samples * np.hanning(len(samples))
    mag = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)

    bass = float(mag[(freqs >= _BASS_HZ[0]) & (freqs < _BASS_HZ[1])].sum())
    mid = float(mag[(freqs >= _MID_HZ[0]) & (freqs < _MID_HZ[1])].sum())
    treble = float(mag[freqs >= _TREBLE_HZ].sum())

    total = bass + mid + treble
    if total <= 1e-9:
        return AudioLevel(level, 0.0, 0.0, 0.0)
    return AudioLevel(level, bass / total, mid / total, treble / total)

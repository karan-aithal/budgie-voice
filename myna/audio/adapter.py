"""AudioAdapter: chains capture → denoise → VAD → AudioFrame async generator."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, AsyncIterator

import numpy as np

from myna.audio.capture import AudioCapture
from myna.audio.denoiser import DenoiseFilter
from myna.audio.vad import VADFilter
from myna.inference.protocol import AudioFrame

if TYPE_CHECKING:
    from myna.config.settings import AudioSettings, VadSettings

_LOG = logging.getLogger(__name__)


class AudioAdapter:
    """Owns the full audio processing chain.

    Chains: sounddevice → DenoiseFilter → VADFilter → AudioFrame stream.
    Audio data is held in-memory only; never written to disk.

    Args:
        audio_settings: Audio capture / format config.
        vad_settings: VAD aggressiveness and padding config.
        loop: Running asyncio event loop (for sounddevice callback).
    """

    def __init__(
        self,
        audio_settings: AudioSettings,
        vad_settings: VadSettings,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._audio_settings = audio_settings
        self._capture = AudioCapture(audio_settings, loop)
        self._denoiser = DenoiseFilter(audio_settings.denoise, audio_settings.sample_rate)
        self._vad: VADFilter | None = (
            VADFilter(
                aggressiveness=vad_settings.aggressiveness,
                speech_pad_ms=vad_settings.speech_pad_ms,
                sample_rate=audio_settings.sample_rate,
            )
            if vad_settings.enabled
            else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Load VAD model and start microphone capture."""
        if self._vad is not None:
            self._vad.load()
        self._capture.start()
        _LOG.info("AudioAdapter opened")

    def close(self) -> None:
        """Stop microphone and release all resources."""
        self._capture.stop()
        _LOG.info("AudioAdapter closed")

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncIterator[AudioFrame]:
        """Yield speech AudioFrames from the microphone.

        Yields:
            :class:`~myna.inference.protocol.AudioFrame` with pcm16/float32 payload.
            ``is_end_of_speech=True`` marks the last frame of a speech segment.
        """
        queue = self._capture.queue
        while True:
            chunk: np.ndarray = await queue.get()

            # Normalise to float32 [-1, 1] for denoising + VAD
            f32 = (
                chunk.astype(np.float32) / 32767.0
                if chunk.dtype == np.int16
                else chunk.astype(np.float32)
            )
            denoised = self._denoiser.apply(f32)

            if self._vad is None:
                yield self._to_frame(denoised, is_end_of_speech=False)
                continue

            output, is_end = self._vad.process(denoised)
            if output is not None:
                yield self._to_frame(output, is_end_of_speech=is_end)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_frame(self, audio: np.ndarray, *, is_end_of_speech: bool) -> AudioFrame:
        """Pack a float32 array into a protocol :class:`AudioFrame`."""
        if self._audio_settings.format == "pcm16":
            raw = (audio * 32767.0).astype(np.int16).tobytes()
        else:
            raw = audio.astype(np.float32).tobytes()

        return AudioFrame(
            data=raw,
            sample_rate=self._audio_settings.sample_rate,
            channels=1,
            format=self._audio_settings.format,
            timestamp_ms=time.time() * 1000.0,
            frame_id=str(uuid.uuid4()),
            is_end_of_speech=is_end_of_speech,
        )

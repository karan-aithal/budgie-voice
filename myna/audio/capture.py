"""sounddevice InputStream → pcm16 chunks → asyncio.Queue.

Audio data is NEVER written to disk. All chunks stay in-memory queue only.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd

from myna.utils.exceptions import MynaMicError

if TYPE_CHECKING:
    from myna.config.settings import AudioSettings

_LOG = logging.getLogger(__name__)


class AudioCapture:
    """Captures raw mono PCM audio from microphone into an asyncio Queue.

    The sounddevice callback runs in a background OS thread and pushes
    chunks into the queue via call_soon_threadsafe — never writes to disk.

    Args:
        settings: Audio capture configuration.
        loop: Running asyncio event loop for thread-safe queue inserts.
    """

    def __init__(self, settings: AudioSettings, loop: asyncio.AbstractEventLoop) -> None:
        self._settings = settings
        self._loop = loop
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=512)
        self._stream: sd.InputStream | None = None
        self._chunk_frames = int(settings.sample_rate * settings.chunk_duration_ms / 1000)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open sounddevice InputStream and begin capture.

        Raises:
            MynaMicError: PortAudio fails to open the device.
        """
        try:
            self._stream = sd.InputStream(
                samplerate=self._settings.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._chunk_frames,
                device=self._settings.device,
                callback=self._callback,
            )
            self._stream.start()
            _LOG.info(
                "Mic capture started — %d Hz, %d ms chunks, device=%r",
                self._settings.sample_rate,
                self._settings.chunk_duration_ms,
                self._settings.device,
            )
        except sd.PortAudioError as exc:
            raise MynaMicError(f"Failed to open microphone: {exc}") from exc

    def stop(self) -> None:
        """Stop and release the microphone."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            _LOG.info("Mic capture stopped")

    @property
    def queue(self) -> asyncio.Queue[np.ndarray]:
        """In-memory queue of raw float32 mono chunks."""
        return self._queue

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,  # noqa: ARG002
        time_info: object,  # noqa: ARG002
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            _LOG.debug("Audio callback status: %s", status)
        chunk = indata[:, 0].copy()  # mono float32
        self._loop.call_soon_threadsafe(self._queue.put_nowait, chunk)

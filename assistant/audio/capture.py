"""Microphone capture via sounddevice.

Captures 16 kHz mono float32 audio and pushes numpy chunks into an
``asyncio.Queue``.  The sounddevice callback fires in a C audio thread;
``loop.call_soon_threadsafe`` bridges it safely into the event loop.
"""

import asyncio
import logging
from typing import Optional

import numpy as np

from assistant.utils.exceptions import AudioCaptureError

logger = logging.getLogger(__name__)


class AudioCapture:
    """Continuously reads from the microphone and enqueues audio chunks.

    Args:
        queue: Destination queue for float32 numpy arrays (mono).
        sample_rate: Sample rate in Hz (must match downstream VAD/STT).
        chunk_duration_ms: Length of each enqueued chunk in milliseconds.
        device: sounddevice device name or integer index.
            ``None`` uses the OS default input device.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        sample_rate: int = 16000,
        chunk_duration_ms: int = 30,
        device: Optional[str | int] = None,
    ) -> None:
        self._queue = queue
        self._sample_rate = sample_rate
        self._chunk_size = int(sample_rate * chunk_duration_ms / 1000)
        self._device = device
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stream = None  # sd.InputStream — imported lazily

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time,   # type: ignore[override]  # CFFI time struct — unused
        status,  # type: ignore[override]  # sd.CallbackFlags
    ) -> None:
        """sounddevice stream callback (runs in the audio thread).

        Copies the mono channel and schedules a thread-safe enqueue.

        Args:
            indata: Shape (frames, channels) float32 array from sounddevice.
            frames: Number of frames in this block.
            time: Timing info (unused).
            status: Stream status flags.
        """
        if status:
            logger.warning("sounddevice status: %s", status)
        chunk: np.ndarray = indata[:, 0].copy()  # take channel 0 → 1-D mono
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, chunk)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the audio input stream and begin capturing.

        Raises:
            AudioCaptureError: If sounddevice is not installed or the
                stream cannot be opened (bad device, wrong sample rate, etc.).
        """
        try:
            import sounddevice as sd  # noqa: PLC0415
        except ImportError as exc:
            raise AudioCaptureError(
                "sounddevice not installed — run: pip install sounddevice"
            ) from exc

        self._loop = asyncio.get_running_loop()
        try:
            self._stream = sd.InputStream(
                device=self._device,
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._chunk_size,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            raise AudioCaptureError(f"Failed to open audio stream: {exc}") from exc

        logger.info(
            "Audio capture started — device=%s  rate=%d Hz  chunk=%d samples (%d ms)",
            self._device or "default",
            self._sample_rate,
            self._chunk_size,
            self._chunk_size * 1000 // self._sample_rate,
        )

    async def stop(self) -> None:
        """Stop and close the audio input stream.

        Safe to call even if ``start()`` was never called.
        """
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("Audio capture stopped")

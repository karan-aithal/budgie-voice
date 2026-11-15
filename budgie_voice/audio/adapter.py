"""AudioAdapter: chains capture → denoise → VAD → AudioFrame async generator."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import TYPE_CHECKING, AsyncIterator, Callable

import numpy as np

from budgie_voice.audio.capture import AudioCapture
from budgie_voice.audio.denoiser import DenoiseFilter
from budgie_voice.audio.vad import VADFilter
from budgie_voice.inference.protocol import AudioFrame

if TYPE_CHECKING:
    from budgie_voice.config.settings import AudioSettings, VadSettings

_LOG = logging.getLogger(__name__)

# A mic that has produced nothing above this level this long after opening is
# wrong (muted, wrong source, wrong BT profile) — tell the user instead of
# silently recording nothing.
_SILENT_PEAK = 0.002
_SILENT_AFTER_MS = 1500


def _play_open_cue() -> None:
    """Fire-and-forget click cue on mic open, via the desktop's XDG sound theme
    (same mechanism GNOME uses for the caps-lock toggle click). Best-effort —
    missing canberra-gtk-play or no sound server must never block capture.
    """
    try:
        subprocess.Popen(
            ["canberra-gtk-play", "-i", "onboard-key-feedback"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        _LOG.debug("canberra-gtk-play not available — skipping open cue")


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
        self._peak = 0.0
        self._chunks = 0
        self._forwarded = 0
        self._end_events = 0
        self._silence_warned = False
        # Called once, with the mic's name, when a session records silence.
        self.on_silent: Callable[[str], None] | None = None
        self._vad: VADFilter | None = (
            VADFilter(
                aggressiveness=vad_settings.aggressiveness,
                speech_pad_ms=vad_settings.speech_pad_ms,
                sample_rate=audio_settings.sample_rate,
                pre_roll_ms=vad_settings.pre_roll_ms,
            )
            if vad_settings.enabled
            else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self, audio_settings: AudioSettings, vad_settings: VadSettings) -> None:
        """Adopt new settings for the next session.

        May load the VAD model if VAD was just enabled, so call it from an
        executor.
        """
        self._audio_settings = audio_settings
        self._capture.configure(audio_settings)
        self._denoiser = DenoiseFilter(audio_settings.denoise, audio_settings.sample_rate)
        if not vad_settings.enabled:
            self._vad = None
        elif self._vad is None:
            self._vad = VADFilter(
                aggressiveness=vad_settings.aggressiveness,
                speech_pad_ms=vad_settings.speech_pad_ms,
                sample_rate=audio_settings.sample_rate,
                pre_roll_ms=vad_settings.pre_roll_ms,
            )
            self._vad.load()
        else:
            self._vad.configure(vad_settings.aggressiveness, vad_settings.speech_pad_ms, vad_settings.pre_roll_ms)

    def preload(self) -> None:
        """Load the VAD model now (blocking — run in an executor at startup)."""
        if self._vad is not None:
            self._vad.load()

    def open(self) -> None:
        """Reset VAD state and start microphone capture. Blocking (pactl, PortAudio)."""
        if self._vad is not None:
            self._vad.load()  # loads only if preload() didn't; always resets state
        self._peak = 0.0
        self._chunks = 0
        self._forwarded = 0
        self._end_events = 0
        self._silence_warned = False
        # Cue must fire before capture.start(): on BT headsets, start() switches
        # the card to HFP/mSBC, which carries mic input over the same single
        # SCO link as any output — a cue played after that would collide with
        # the mic stream it just opened. Play it now, while output still runs
        # over the original (A2DP) path.
        _play_open_cue()
        self._capture.start()
        _LOG.info("AudioAdapter opened")

    def close(self) -> None:
        """Stop microphone and release all resources."""
        self._capture.stop()
        vad_max = self._vad.max_score if self._vad else -1.0
        windows = self._vad.windows if self._vad else 0
        preroll = self._vad.preroll_chunks if self._vad else 0
        _LOG.info(
            "Session audio stats: peak=%.3f chunks=%d forwarded=%d end_events=%d "
            "vad_max_score=%.2f windows=%d preroll_chunks=%d",
            self._peak, self._chunks, self._forwarded, self._end_events, vad_max, windows, preroll,
        )
        _LOG.info("AudioAdapter closed")

    def shutdown(self) -> None:
        """Release the BT profile pin. Call once at app shutdown, not per-session."""
        self._capture.release_bt()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncIterator[AudioFrame]:
        """Yield speech AudioFrames from the microphone.

        Yields:
            :class:`~budgie_voice.inference.protocol.AudioFrame` with pcm16/float32 payload.
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
            self._chunks += 1
            self._peak = max(self._peak, float(np.abs(denoised).max()))
            self._check_silence(len(chunk))

            if self._vad is None:
                yield self._to_frame(denoised, is_end_of_speech=False)
                continue

            output, is_end = self._vad.process(denoised)
            if output is not None:
                self._forwarded += 1
                if is_end:
                    self._end_events += 1
                yield self._to_frame(output, is_end_of_speech=is_end)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_silence(self, chunk_len: int) -> None:
        """Once, shortly after open: warn if nothing audible has arrived."""
        if self._silence_warned:
            return
        if self._chunks * chunk_len * 1000 < _SILENT_AFTER_MS * self._audio_settings.sample_rate:
            return
        self._silence_warned = True  # check once, whatever the answer
        if self._peak < _SILENT_PEAK:
            label = self._capture.source_label
            _LOG.warning("Mic silent: %s recorded nothing for %d ms", label, _SILENT_AFTER_MS)
            if self.on_silent is not None:
                self.on_silent(label)

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
            is_end_of_speech=is_end_of_speech,
        )

"""sounddevice InputStream → pcm16 chunks → asyncio.Queue.

Audio data is NEVER written to disk. All chunks stay in-memory queue only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd


if TYPE_CHECKING:
    from myna.config.settings import AudioSettings

_LOG = logging.getLogger(__name__)


def _get_active_profile(card: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["pactl", "list", "cards"], timeout=5, text=True, stderr=subprocess.DEVNULL,
        )
        in_card = False
        for line in out.splitlines():
            s = line.strip()
            if f"Name: {card}" in s:
                in_card = True
            elif in_card and s.startswith("Active Profile:"):
                return s.split(":", 1)[1].strip()
            elif in_card and s.startswith("Card #") and f"Name: {card}" not in s:
                break
    except Exception:
        pass
    return None


def _set_bt_default_source(card: str) -> tuple[str | None, str | None]:
    """Find the BT card's HFP mic source, make it the default, and pin it.

    Returns ``(source_name, previous_default)``. The caller should also export
    ``PULSE_SOURCE=source_name`` before opening the stream — device="pulse"
    otherwise follows the default source, which can point elsewhere (e.g. an
    snd_aloop loopback → pure silence) or be re-routed mid-session. Polls
    because PipeWire creates the bluez_input source asynchronously after the
    profile switch.
    """
    mac = card.removeprefix("bluez_card.")
    prev: str | None = None
    try:
        prev = subprocess.check_output(
            ["pactl", "get-default-source"], timeout=5, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        pass

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sources"], timeout=5, text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split("\t")
                # PipeWire names the source either with underscores
                # (bluez_input.AA_BB….headset-head-unit) or with colons
                # (bluez_input.AA:BB:…) depending on how the headset connected.
                if len(parts) >= 2 and parts[1].startswith("bluez_input.") and mac in parts[1].replace(":", "_"):
                    subprocess.run(
                        ["pactl", "set-default-source", parts[1]],
                        check=True, timeout=5, capture_output=True,
                    )
                    _LOG.info("BT mic source selected: %s", parts[1])
                    return parts[1], prev
        except Exception:
            pass
        time.sleep(0.2)

    _LOG.warning("BT mic source never appeared for %s — recording from default source %r", card, prev)
    return None, prev


def _restore_default_source(prev: str | None) -> None:
    if not prev:
        return
    try:
        subprocess.run(
            ["pactl", "set-default-source", prev], check=True, timeout=5, capture_output=True,
        )
    except Exception:
        _LOG.debug("Could not restore default source %r", prev)


def _restore_bt_profile(card: str, original: str | None) -> None:
    for profile in ("a2dp-sink-sbc_xq", original, "a2dp-sink"):
        if not profile:
            continue
        try:
            subprocess.run(
                ["pactl", "set-card-profile", card, profile],
                check=True, timeout=5, capture_output=True,
            )
            _LOG.info("BT profile restored: %s → %s", card, profile)
            return
        except subprocess.CalledProcessError:
            continue
    _LOG.warning("Could not restore BT profile for %s", card)


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
        self._bt_card: str | None = None       # set while a session holds the BT card in headset mode
        self._bt_restore_profile: str | None = None
        self._prev_default_source: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open sounddevice InputStream and begin capture.

        Raises:
            RuntimeError: PortAudio fails to open the device.
        """
        # Chunks captured after the previous session's consumer stopped belong
        # to that session — never hand them to this one.
        while not self._queue.empty():
            self._queue.get_nowait()

        device_cfg = self._settings.device
        sd_device: str | int | None

        if isinstance(device_cfg, str) and device_cfg.startswith("bt:"):
            card = device_cfg[3:]
            self._bt_card = card
            # A BT headset has one link: A2DP (high-quality playback, no mic) or
            # HSP/HFP (mic, narrowband playback). The system flips back to A2DP
            # on its own mid-session unless the profile *and* the mic source are
            # both pinned, so do it every session and give the link back in stop().
            self._bt_restore_profile = _get_active_profile(card)
            for profile in ("headset-head-unit-msbc", "headset-head-unit"):
                try:
                    subprocess.run(
                        ["pactl", "set-card-profile", card, profile],
                        check=True, timeout=5, capture_output=True,
                    )
                    _LOG.info("BT profile switched for capture: %s → %s", card, profile)
                    break
                except subprocess.CalledProcessError as exc:
                    _LOG.warning("BT profile %s failed: %s", profile, exc.stderr.decode(errors="replace").strip())
            src, self._prev_default_source = _set_bt_default_source(card)
            if not src:
                self.release_bt()
                raise RuntimeError(f"Headset mic not available (card {card} stayed on a playback profile)")
            os.environ["PULSE_SOURCE"] = src  # pin our stream to the BT mic
            sd_device = "pulse"
        elif isinstance(device_cfg, str) and device_cfg.startswith("pa:"):
            source = device_cfg[3:]
            try:
                subprocess.run(
                    ["pactl", "set-default-source", source],
                    check=True, timeout=5, capture_output=True,
                )
            except Exception as e:
                _LOG.warning("pactl set-default-source failed: %s", e)
            sd_device = "pulse"
        else:
            sd_device = device_cfg  # type: ignore[assignment]

        try:
            self._stream = sd.InputStream(
                samplerate=self._settings.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._chunk_frames,
                device=sd_device,
                callback=self._callback,
            )
            self._stream.start()
            _LOG.info(
                "Mic capture started — %d Hz, %d ms chunks, device=%r",
                self._settings.sample_rate,
                self._settings.chunk_duration_ms,
                device_cfg,
            )
        except sd.PortAudioError as exc:
            raise RuntimeError(f"Failed to open microphone: {exc}") from exc

    def stop(self) -> None:
        """Stop the microphone stream and hand a BT headset back to playback quality."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            _LOG.info("Mic capture stopped")
        self.release_bt()

    def release_bt(self) -> None:
        """Restore the BT card's playback profile and default source. No-op when
        no session holds the card, so safe to call again at shutdown."""
        if self._bt_card:
            os.environ.pop("PULSE_SOURCE", None)
            _restore_bt_profile(self._bt_card, self._bt_restore_profile)
            _restore_default_source(self._prev_default_source)
            self._bt_card = None
            self._bt_restore_profile = None
            self._prev_default_source = None

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

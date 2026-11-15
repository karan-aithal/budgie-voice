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
    from budgie_voice.config.settings import AudioSettings

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


def _source_names() -> list[str]:
    """Names of all Pulse/PipeWire sources, [] if pactl is unavailable."""
    try:
        out = subprocess.check_output(
            ["pactl", "list", "short", "sources"], timeout=5, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    return [p[1] for p in (line.split("\t") for line in out.splitlines()) if len(p) > 1]


# Sources Auto never picks: monitors record playback, snd_aloop is a loopback,
# iec958 is a digital input, bluez is opt-in (a BT mic drags playback to mSBC).
_AUTO_SKIP = (".monitor", "snd_aloop", "iec958", "bluez")


def _auto_source() -> str | None:
    """First real microphone source, or None (= system default)."""
    for name in _source_names():
        if not any(skip in name.lower() for skip in _AUTO_SKIP):
            return name
    return None


def _set_source_gain(source: str, pct: int) -> None:
    pct = max(50, min(150, pct))
    try:
        subprocess.run(
            ["pactl", "set-source-volume", source, f"{pct}%"],
            check=True, timeout=5, capture_output=True,
        )
        _LOG.info("BT mic gain set to %d%% on %s", pct, source)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Could not set mic gain on %s: %s", source, exc)


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
        self._env_pinned = False               # True while we own PULSE_SOURCE
        self.source_label = "system default"    # what the current session records from

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the first working microphone from ``audio.devices`` (or Auto).

        Resolved at every session start, so a headset that was off last time or a
        source that went away is simply skipped.

        Raises:
            RuntimeError: No entry could be opened.
        """
        # Chunks captured after the previous session's consumer stopped belong
        # to that session — never hand them to this one.
        while not self._queue.empty():
            self._queue.get_nowait()

        entries: list[str | None] = list(self._settings.device_list())
        if not entries:
            auto = _auto_source()
            entries = [f"pa:{auto}" if auto else None]  # None = system default

        failures: list[str] = []
        for entry in entries:
            try:
                self._open(entry)
                return
            except RuntimeError as exc:
                self.release_bt()
                failures.append(f"{entry or 'system default'}: {exc}")
                _LOG.warning("Mic %s unavailable: %s", entry or "system default", exc)
        raise RuntimeError("No microphone available — " + "; ".join(failures))

    def _open(self, entry: str | None) -> None:
        sd_device: str | None
        if entry is not None and entry.startswith("bt:"):
            sd_device = self._pin_bt(entry[3:])
        elif entry is not None and entry.startswith("pa:"):
            source = entry[3:]
            if source not in _source_names():
                raise RuntimeError(f"source {source} not found")
            os.environ["PULSE_SOURCE"] = source  # per stream; the system default stays untouched
            self._env_pinned = True
            sd_device = "pulse"
        else:
            sd_device = entry  # ALSA name, or None = PortAudio default

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
        except sd.PortAudioError as exc:
            self._stream = None
            raise RuntimeError(f"Failed to open microphone: {exc}") from exc
        self.source_label = entry or "system default"
        _LOG.info(
            "Mic capture started — %d Hz, %d ms chunks, device=%r",
            self._settings.sample_rate, self._settings.chunk_duration_ms, self.source_label,
        )

    def _pin_bt(self, card: str) -> str:
        """Put a BT headset in its mic profile and pin its mic source; returns the sd device."""
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
            raise RuntimeError(f"headset mic not available (card {card} stayed on a playback profile)")
        os.environ["PULSE_SOURCE"] = src  # pin our stream to the BT mic
        self._env_pinned = True
        if self._settings.input_gain_pct is not None:
            _set_source_gain(src, self._settings.input_gain_pct)
        return "pulse"

    def stop(self) -> None:
        """Stop the microphone stream and hand a BT headset back to playback quality."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            _LOG.info("Mic capture stopped")
        self.release_bt()

    def release_bt(self) -> None:
        """Undo what start() pinned: PULSE_SOURCE, and a BT card's playback profile
        and default source. No-op when nothing is held, so safe to call twice."""
        if self._env_pinned:
            os.environ.pop("PULSE_SOURCE", None)
            self._env_pinned = False
        if self._bt_card:
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

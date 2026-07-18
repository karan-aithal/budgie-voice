"""Background QThread workers for the enrollment wizard.

Split from enroller.py to keep module size manageable.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from PyQt6.QtCore import QThread, pyqtSignal

from myna.audio.capture import _restore_default_source, _set_bt_default_source

_LOG = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000
_RECORD_SECONDS = 12


def _get_active_profile(card: str) -> str | None:
    """Return the current active PipeWire/PA profile for a card, or None."""
    try:
        out = subprocess.check_output(
            ["pactl", "list", "cards"], timeout=5, text=True, stderr=subprocess.DEVNULL,
        )
        in_card = False
        for line in out.splitlines():
            stripped = line.strip()
            if f"Name: {card}" in stripped:
                in_card = True
            elif in_card and stripped.startswith("Active Profile:"):
                return stripped.split(":", 1)[1].strip()
            elif in_card and stripped.startswith("Card #") and f"Name: {card}" not in stripped:
                break  # moved past our card
    except Exception:
        pass
    return None


def _restore_bt_profile(card: str, original: str | None) -> None:
    """Restore BT card to A2DP (SBC-XQ preferred) after HFP recording."""
    # Prefer SBC-XQ, then whatever the original profile was, then plain a2dp-sink
    candidates = ["a2dp-sink-sbc_xq", original, "a2dp-sink"]
    for profile in candidates:
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


class RecordWorker(QThread):
    """Record a fixed-length audio clip from the selected microphone.

    ``device`` may be:
    - ``int``        — sounddevice ALSA device index
    - ``"bt:<card>"`` — PipeWire BT card name; switches profile to mSBC/HFP then records via pulse
    - ``"pa:<src>"``  — PipeWire/PA source name; sets as default then records via pulse
    - ``None``       — system default

    Signals:
        finished (np.ndarray): 1-D float32 array of recorded audio.
        error (str): Human-readable error message.
    """

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, duration_s: float = _RECORD_SECONDS, device: int | str | None = None, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._duration = duration_s
        self._device = device

    def run(self) -> None:
        try:
            frames = int(self._duration * _SAMPLE_RATE)
            sd_device: int | str | None
            _restore_card: str | None = None
            _restore_profile: str | None = None
            _prev_default: str | None = None

            if isinstance(self._device, str) and self._device.startswith("bt:"):
                # BT card in A2DP — switch to HFP/mSBC profile to enable mic
                card = self._device[3:]
                _restore_card = card
                _restore_profile = _get_active_profile(card)
                switched = False
                for profile in ("headset-head-unit-msbc", "headset-head-unit"):
                    try:
                        subprocess.run(
                            ["pactl", "set-card-profile", card, profile],
                            check=True, timeout=5, capture_output=True,
                        )
                        _LOG.info("BT profile switched: %s → %s", card, profile)
                        switched = True
                        break
                    except subprocess.CalledProcessError:
                        continue
                if not switched:
                    _LOG.warning("Could not switch BT profile for %s", card)
                # Pin the recording to the BT mic — "pulse" records the
                # default source, which may be a silent loopback otherwise.
                _src, _prev_default = _set_bt_default_source(card)
                if _src:
                    os.environ["PULSE_SOURCE"] = _src
                sd_device = "pulse"

            elif isinstance(self._device, str) and self._device.startswith("pa:"):
                # explicit PA source — set as default then record via pulse
                source = self._device[3:]
                try:
                    subprocess.run(
                        ["pactl", "set-default-source", source],
                        check=True, timeout=5, capture_output=True,
                    )
                except Exception as e:
                    _LOG.warning("pactl set-default-source failed: %s", e)
                sd_device = "pulse"

            else:
                sd_device = self._device  # type: ignore[assignment]

            audio = sd.rec(frames, samplerate=_SAMPLE_RATE, channels=1, dtype="float32", device=sd_device)
            sd.wait()
            self.finished.emit(audio.flatten().copy())
        except Exception as exc:
            _LOG.error("RecordWorker error: %s", exc)
            self.error.emit(str(exc))
        finally:
            # Restore A2DP/SBC-XQ after BT mic recording
            if _restore_card and _restore_profile:
                os.environ.pop("PULSE_SOURCE", None)
                _restore_bt_profile(_restore_card, _restore_profile)
                _restore_default_source(_prev_default)


class EnrollWorker(QThread):
    """Compute mean ECAPA embedding from recordings and save voiceprint.

    Signals:
        finished (): Emitted on success.
        error (str): Human-readable error message.
    """

    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        recordings: list[np.ndarray],
        verifier: object,
        save_path: Path,
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._recordings = recordings
        self._verifier = verifier
        self._save_path = save_path

    def run(self) -> None:
        try:
            from myna.audio.denoiser import DenoiseFilter  # noqa: PLC0415
            from myna.config.settings import load_settings  # noqa: PLC0415
            from myna.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

            # Verification at dictation time runs on denoised audio (see
            # AudioAdapter); enroll through the same filter or the channel
            # mismatch tanks the cosine scores.
            audio_cfg = load_settings().audio
            denoiser = DenoiseFilter(audio_cfg.denoise, audio_cfg.sample_rate)
            v: SpeakerVerifier = self._verifier  # type: ignore[assignment]
            v.load_model()  # no-op if already loaded; loads model without needing a voiceprint
            embeddings = [v.get_embedding(denoiser.apply(r)) for r in self._recordings]
            mean_emb = np.mean(embeddings, axis=0).astype(np.float32)
            norm = np.linalg.norm(mean_emb)
            if norm > 0:
                mean_emb /= norm
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(torch.tensor(mean_emb), self._save_path)
            _LOG.info("Voiceprint saved to %s", self._save_path)
            self.finished.emit()
        except Exception as exc:
            _LOG.error("EnrollWorker error: %s", exc)
            self.error.emit(str(exc))

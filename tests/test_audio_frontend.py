"""Phase 2: VAD windowing, pre-roll, state reset, mic resolution, silence notice."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import numpy as np

from budgie_voice.audio import capture
from budgie_voice.audio.adapter import AudioAdapter
from budgie_voice.audio.vad import VADFilter
from budgie_voice.config.settings import AudioSettings, VadSettings


class _Score:
    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class _Model:
    """Speech when the window's mean is above 0.5; records every window it sees."""

    def __init__(self) -> None:
        self.window_sizes: list[int] = []
        self.reset_states = MagicMock()

    def __call__(self, tensor, sample_rate) -> _Score:  # noqa: ANN001
        self.window_sizes.append(int(tensor.shape[0]))
        return _Score(1.0 if float(tensor.mean()) > 0.5 else 0.0)


def _vad(model: _Model, **kw) -> VADFilter:
    vad = VADFilter(aggressiveness=2, speech_pad_ms=60, sample_rate=16_000, **kw)
    vad._model = model  # noqa: SLF001 — drive the state machine without the real model
    return vad


def test_vad_only_sees_512_sample_windows() -> None:
    model = _Model()
    vad = _vad(model)
    for _ in range(5):  # 5 x 480 = 2400 samples -> 4 whole windows, 352 left over
        vad.process(np.zeros(480, dtype=np.float32))
    assert model.window_sizes == [512] * 4
    assert vad.windows == 4


def test_preroll_precedes_first_speech_chunk() -> None:
    vad = _vad(_Model(), pre_roll_ms=64)  # 1024 samples = two 512-sample chunks
    quiet = [np.full(512, v, dtype=np.float32) for v in (0.1, 0.2, 0.3)]
    for chunk in quiet:
        assert vad.process(chunk) == (None, False)
    loud = np.ones(512, dtype=np.float32)

    out, is_end = vad.process(loud)

    assert not is_end
    assert len(out) == 3 * 512  # newest two quiet chunks + the speech chunk
    assert out[0] == np.float32(0.2) and out[512] == np.float32(0.3) and out[-1] == 1.0
    assert vad.preroll_chunks == 2


def test_open_resets_vad_state() -> None:
    model = _Model()
    adapter = AudioAdapter(AudioSettings(), VadSettings(), asyncio.new_event_loop())
    adapter._vad._model = model  # noqa: SLF001
    adapter._capture = MagicMock()  # noqa: SLF001
    with patch("budgie_voice.audio.adapter._play_open_cue"):
        adapter.open()
    model.reset_states.assert_called()


# --- mic resolution --------------------------------------------------------------

def _start(monkeypatch, devices, sources):
    monkeypatch.setattr(capture, "_source_names", lambda: sources)
    monkeypatch.setattr(capture.sd, "InputStream", MagicMock())
    monkeypatch.delenv("PULSE_SOURCE", raising=False)
    cap = capture.AudioCapture(AudioSettings(devices=devices), asyncio.new_event_loop())
    cap.start()
    return cap


def test_first_working_device_wins(monkeypatch) -> None:
    cap = _start(monkeypatch, ["pa:gone", "pa:usb_mic", "pa:other"], ["usb_mic", "other"])
    assert cap.source_label == "pa:usb_mic"
    assert os.environ["PULSE_SOURCE"] == "usb_mic"
    cap.stop()
    assert "PULSE_SOURCE" not in os.environ


def test_auto_skips_monitor_loopback_digital_and_bluetooth(monkeypatch) -> None:
    sources = [
        "alsa_output.pci-0000_70_00.1.hdmi-stereo.monitor",
        "alsa_input.platform-snd_aloop.0.analog-stereo",
        "alsa_input.usb-Generic_USB_Audio-00.iec958-stereo",
        "bluez_input.AA:BB",
        "alsa_input.pci-0000_00_1f.3.analog-stereo",
    ]
    assert _start(monkeypatch, [], sources).source_label == "pa:alsa_input.pci-0000_00_1f.3.analog-stereo"
    assert _start(monkeypatch, [], sources[:4]).source_label == "system default"


# --- silent mic ------------------------------------------------------------------

async def test_silent_mic_reported_once() -> None:
    loop = asyncio.get_running_loop()
    adapter = AudioAdapter(AudioSettings(denoise=False), VadSettings(enabled=False), loop)
    adapter._capture = MagicMock(source_label="pa:mic")  # noqa: SLF001
    adapter._capture.queue = asyncio.Queue()  # noqa: SLF001
    adapter.on_silent = MagicMock()

    for _ in range(60):  # 1.8 s of digital silence
        adapter._capture.queue.put_nowait(np.zeros(480, dtype=np.float32))  # noqa: SLF001
    stream = adapter.stream()
    for _ in range(60):
        await anext(stream)

    adapter.on_silent.assert_called_once_with("pa:mic")

"""BT capture: a failed profile switch must be retried, not latched until restart."""
from __future__ import annotations

import asyncio
import os
import subprocess
from unittest.mock import MagicMock

import pytest

from budgie_voice.audio import capture
from budgie_voice.config.settings import AudioSettings

CARD = "bluez_card.AA_BB"


@pytest.fixture
def bt(monkeypatch):
    """Patch every system touchpoint; expose the profile switch calls and the
    state the fake headset is in."""
    state = {"profile": "a2dp-sink-sbc_xq", "switch_ok": False, "source": None}
    switches: list[str] = []

    def run(cmd, **_kw):
        if cmd[:2] == ["pactl", "set-card-profile"]:
            switches.append(cmd[3])
            if not state["switch_ok"]:
                raise subprocess.CalledProcessError(1, cmd, stderr=b"Failure: Input/Output error")
            state["profile"] = cmd[3]
            state["source"] = "bluez_input.AA_BB.headset-head-unit"
        return MagicMock()

    monkeypatch.setattr(capture.subprocess, "run", run)
    monkeypatch.setattr(capture, "_get_active_profile", lambda _card: state["profile"])
    monkeypatch.setattr(capture, "_set_bt_default_source", lambda _card: (state["source"], "default-src"))
    monkeypatch.setattr(capture.sd, "InputStream", MagicMock())
    monkeypatch.delenv("PULSE_SOURCE", raising=False)
    return state, switches


def _capture() -> capture.AudioCapture:
    return capture.AudioCapture(AudioSettings(device=f"bt:{CARD}"), asyncio.new_event_loop())


@pytest.mark.parametrize("name", [
    "bluez_input.AA_BB.headset-head-unit",
    "bluez_input.AA:BB",
])
def test_bt_source_found_with_either_naming(monkeypatch, name) -> None:
    sources = f"1\talsa_input.x\tPipeWire\n94\t{name}\tPipeWire\n"
    monkeypatch.setattr(capture.subprocess, "check_output", lambda *a, **k: sources)
    monkeypatch.setattr(capture.subprocess, "run", MagicMock())
    assert capture._set_bt_default_source(CARD)[0] == name


def test_each_session_switches_to_headset_and_back(bt) -> None:
    state, switches = bt
    state["switch_ok"] = True
    cap = _capture()

    for _ in range(2):  # the system flips back to A2DP between sessions
        cap.start()
        assert switches[-1] == "headset-head-unit-msbc"
        assert os.environ["PULSE_SOURCE"] == "bluez_input.AA_BB.headset-head-unit"
        cap.stop()
        assert switches[-1] == "a2dp-sink-sbc_xq"  # playback quality restored
        assert "PULSE_SOURCE" not in os.environ
    assert switches.count("headset-head-unit-msbc") == 2


def test_input_gain_applied_and_clamped(bt, monkeypatch) -> None:
    state, _ = bt
    state["switch_ok"] = True
    calls: list[list[str]] = []
    real_run = capture.subprocess.run
    monkeypatch.setattr(capture.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), real_run(cmd, **kw))[1])
    cap = capture.AudioCapture(
        AudioSettings(device=f"bt:{CARD}", input_gain_pct=300), asyncio.new_event_loop(),
    )
    cap.start()
    assert ["pactl", "set-source-volume", "bluez_input.AA_BB.headset-head-unit", "150%"] in calls
    cap.stop()


def test_no_headset_mic_raises_and_releases_card(bt) -> None:
    state, switches = bt  # switches fail, no source ever appears
    cap = _capture()

    with pytest.raises(RuntimeError, match="headset mic not available"):
        cap.start()
    assert "a2dp-sink-sbc_xq" in switches  # restore was attempted
    assert "PULSE_SOURCE" not in os.environ

"""Phase 10 Gate 3: Settings sidebar, Overview statuses, activity metadata, LM Studio probe,
and the config.yaml save round-trip.

Offscreen Qt; the LM Studio probe talks to a throwaway loopback HTTP server, never a real one.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from budgie_voice.config.settings import LlmSettings, Settings, load_settings  # noqa: E402
from budgie_voice.settings_ui import overview as overview_mod  # noqa: E402
from budgie_voice.settings_ui import window as window_mod  # noqa: E402
from budgie_voice.settings_ui.overview import OverviewPage  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus, Status, probe_llm  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _text(row) -> str:
    return row._value.text()


# ---------------------------------------------------------------------------
# Activity: metadata only
# ---------------------------------------------------------------------------

def test_completed_dictation_records_the_count() -> None:
    st = RuntimeStatus()
    st.on_session_state("recording", 0)
    st.on_session_state("finalizing", 0)
    st.on_session_state("completed", 16)

    (a,) = st.activity
    assert (a.outcome, a.chars) == ("Completed", 16)
    assert a.duration_s >= 0
    assert set(vars(a)) == {"when", "duration_s", "outcome", "chars"}  # no field could hold text


def test_cancelled_and_failed_are_recorded_and_history_is_bounded() -> None:
    st = RuntimeStatus()
    st.on_session_state("recording", 0)
    st.on_session_state("cancelled", 22)  # progressive typing keeps what it already typed
    st.on_session_state("error", 0)  # failed before recording ever began
    assert [(a.outcome, a.chars) for a in st.activity] == [("Cancelled", 22), ("Failed", 0)]
    for _ in range(40):
        st.on_session_state("recording", 0)
        st.on_session_state("completed", 0)
    assert len(st.activity) == 20


def test_duration_is_recording_time_not_transcription_time(monkeypatch) -> None:
    clock = iter([100.0, 102.5, 130.0])  # recording, stop pressed, transcript done
    monkeypatch.setattr("budgie_voice.ui.status.time.monotonic", lambda: next(clock))
    st = RuntimeStatus()
    st.on_session_state("recording", 0)
    st.on_session_state("finalizing", 0)
    st.on_session_state("completed", 0)
    assert st.activity[0].duration_s == pytest.approx(2.5)


def test_a_broken_subscriber_cannot_break_the_session() -> None:
    st = RuntimeStatus()
    st.subscribe(lambda: 1 / 0)
    ok: list[int] = []
    st.subscribe(lambda: ok.append(1))
    st.on_session_state("recording", 0)
    st.on_session_state("completed", 0)  # must not raise
    assert ok == [1]


# ---------------------------------------------------------------------------
# LM Studio probe
# ---------------------------------------------------------------------------

class _Server:
    def __init__(self, status: int = 200, body: bytes | None = None) -> None:
        self.paths: list[str] = []
        outer = self
        payload = body if body is not None else json.dumps({"data": [{"id": "qwen/qwen3.5-4b"}]}).encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.paths.append(self.path)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a: object) -> None:  # noqa: D401
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_port}/v1"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture()
def server():
    made: list[_Server] = []

    def make(**kw) -> _Server:
        made.append(_Server(**kw))
        return made[-1]

    yield make
    for s in made:
        s.close()


def test_probe_disabled_sends_nothing(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *a, **k: pytest.fail("network used"))
    assert probe_llm(LlmSettings(enabled=False))[0] is Status.DISABLED


def test_probe_ready_when_server_lists_the_model(server) -> None:
    s = server()
    status, detail = probe_llm(LlmSettings(enabled=True, url=s.url, model="qwen/qwen3.5-4b"))
    assert status is Status.READY and "qwen/qwen3.5-4b" in detail
    assert s.paths == ["/v1/models"]  # one GET, nothing else


def test_probe_separates_reachable_server_from_missing_model(server) -> None:
    s = server()
    status, detail = probe_llm(LlmSettings(enabled=True, url=s.url, model="other-model"))
    assert status is Status.UNAVAILABLE and "reachable, but model other-model is not listed" in detail


def test_probe_unreachable_server_is_unavailable() -> None:
    s = _Server()
    url = s.url
    s.close()  # port is now closed
    status, detail = probe_llm(LlmSettings(enabled=True, url=url, model="m"))
    assert status is Status.UNAVAILABLE and "not reachable" in detail


@pytest.mark.parametrize(("kw", "expect"), [
    ({"status": 500}, "HTTP 500"),
    ({"body": b"not json"}, "unexpected response"),
    ({"body": b'{"data": "nope"}'}, "unexpected response"),
])
def test_probe_bad_answers_are_errors(server, kw, expect) -> None:
    s = server(**kw)
    status, detail = probe_llm(LlmSettings(enabled=True, url=s.url, model="m"))
    assert status is Status.ERROR and expect in detail


def test_probe_refuses_non_loopback_without_sending(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *a, **k: pytest.fail("network used"))
    status, _ = probe_llm(LlmSettings(enabled=True, url="http://192.0.2.1:1/v1", model="m"))
    assert status is Status.ERROR


def test_probe_never_echoes_credentials_in_the_url(server) -> None:
    s = server()
    url = s.url.replace("http://", "http://user:hunter2@")
    _, detail = probe_llm(LlmSettings(enabled=True, url=url, model="m"))
    assert "hunter2" not in detail and "user" not in detail.replace("server", "")


def test_probe_without_model_is_an_error() -> None:
    assert probe_llm(LlmSettings(enabled=True, model=""))[0] is Status.ERROR


# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------

class _Backend:
    def __init__(self, ready: bool) -> None:
        self.is_ready = ready


class _Verifier:
    def __init__(self, ready: bool) -> None:
        self.ready = ready


def _page(settings: Settings | None = None, status: RuntimeStatus | None = None, found=()) -> OverviewPage:
    return OverviewPage(settings or Settings(), status or RuntimeStatus(), lambda d: d if d in found else None)


def test_mic_states(qapp) -> None:
    s = Settings()
    p = _page(s)
    p.refresh()
    assert _text(p._mic).startswith("Unknown — Auto")  # nothing to look up for Auto

    s.audio.devices = ["pa:usb-mic"]
    p = _page(s, found={"pa:usb-mic"})
    p.refresh()
    assert _text(p._mic).startswith("Ready — pa:usb-mic")

    p = _page(s, found=())
    p.refresh()
    assert _text(p._mic) == "Unavailable — pa:usb-mic not found"

    s.audio.devices = ["bt:gone", "pa:usb-mic"]
    p = _page(s, found={"pa:usb-mic"})
    p.refresh()
    assert "fallback" in _text(p._mic)


def test_asr_states_follow_the_runtime(qapp) -> None:
    s = Settings()
    st = RuntimeStatus()
    p = _page(s, st)
    p.refresh()
    assert _text(p._asr).startswith("Unknown")  # nothing reported yet

    st.set_asr(_Backend(True), "faster_whisper", "small")
    assert _text(p._asr) == "Ready — faster_whisper / small"  # pushed, no refresh() needed

    st.set_asr_loading(True, "faster_whisper / medium")
    assert _text(p._asr) == (
        "Connecting — loading faster_whisper / medium… (still serving faster_whisper / small)"
    )
    st.set_asr_loading(False)

    st.set_asr(_Backend(False), "faster_whisper", "small")
    assert _text(p._asr).startswith("Unavailable")

    s.inference.model = "medium"
    st.set_asr(_Backend(True), "faster_whisper", "small")  # swap failed: old model still serving
    assert "configured faster_whisper / medium, not applied" in _text(p._asr)


def test_speaker_states(qapp, tmp_path) -> None:
    s = Settings()
    s.speaker.voiceprint_path = str(tmp_path / "vp.pt")
    s.speaker.enabled = False
    p = _page(s)
    p.refresh()
    assert _text(p._speaker) == "Disabled"

    s.speaker.enabled = True
    p = _page(s)
    p.refresh()
    assert _text(p._speaker).startswith("Unavailable — no voiceprint")

    (tmp_path / "vp.pt").write_bytes(b"x")
    p = _page(s, RuntimeStatus(_Verifier(True)))
    p.refresh()
    assert _text(p._speaker).startswith("Ready")
    p = _page(s, RuntimeStatus(_Verifier(False)))
    p.refresh()
    assert _text(p._speaker).startswith("Unavailable — voiceprint found but not loaded")
    p = _page(s, RuntimeStatus())
    p.refresh()
    assert _text(p._speaker).startswith("Unknown")


def test_llm_disabled_starts_no_thread(qapp, monkeypatch) -> None:
    monkeypatch.setattr(overview_mod.threading, "Thread", lambda *a, **k: pytest.fail("thread started"))
    p = _page(Settings())
    p.refresh()
    assert _text(p._llm).startswith("Disabled")


def _wait_for(pred, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return
        time.sleep(0.01)
    pytest.fail("timed out")


def test_llm_check_runs_off_the_ui_thread_and_reports_back(qapp, monkeypatch) -> None:
    release = threading.Event()
    seen: dict[str, object] = {}

    def slow_probe(cfg):
        seen["thread"] = threading.current_thread()
        release.wait(5)
        return Status.READY, "fake — model m available"

    monkeypatch.setattr(overview_mod, "probe_llm", slow_probe)
    s = Settings()
    s.llm.enabled = True
    p = _page(s)
    p.refresh()  # returns at once even though the probe is blocked
    assert _text(p._llm) == "Connecting — checking…"
    p.refresh()  # a second refresh while one is in flight must not start another
    release.set()
    _wait_for(lambda: _text(p._llm).startswith("Ready"))
    assert seen["thread"] is not threading.main_thread()


def test_llm_probe_crash_does_not_leave_checking_forever(qapp, monkeypatch) -> None:
    monkeypatch.setattr(overview_mod, "probe_llm", lambda cfg: 1 / 0)
    s = Settings()
    s.llm.enabled = True
    p = _page(s)
    p.refresh()
    _wait_for(lambda: _text(p._llm).startswith("Error"))


def test_activity_list_is_metadata_only_newest_first(qapp) -> None:
    st = RuntimeStatus()
    p = _page(None, st)
    p.refresh()
    assert not p._empty.isHidden() and p._activity.isHidden()  # empty state

    st.on_session_state("recording", 0)
    st.on_session_state("completed", 15)
    st.on_session_state("recording", 0)
    st.on_session_state("cancelled", 0)
    assert p._empty.isHidden() and not p._activity.isHidden()
    rows = [[p._activity.topLevelItem(i).text(c) for c in range(4)] for i in range(2)]
    assert [r[2] for r in rows] == ["Cancelled", "Completed"]  # newest first
    assert [r[3] for r in rows] == ["0", "15"]


# ---------------------------------------------------------------------------
# Settings window: navigation and save round-trip
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(window_mod, "_CONFIG_PATH", path)
    monkeypatch.setitem(sys.modules, "dbus", None)  # never poke a real running Budgie over DBus
    monkeypatch.setattr(window_mod.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(window_mod.sd, "query_devices", lambda: [])
    return path


def test_settings_opens_on_overview_with_the_approved_sections(qapp, cfg_path) -> None:
    w = SettingsWindow(Settings())
    names = [w._nav.item(i).text() for i in range(w._nav.count())]
    assert names == ["Overview", "Activation", "Audio & Inference", "Speaker Verification",
                     "Post-processing", "Appearance"]
    assert w._nav.currentRow() == 0 and w._stack.currentWidget() is w._overview
    assert [w._theme_combo.itemText(i) for i in range(3)] == ["System", "Light", "Dark"]
    assert w._theme_combo.currentData() == "system"
    assert w._visualizer_combo.count() == 2  # Recording overlay setting kept under Appearance


def test_pages_fit_the_window_at_its_minimum_size(qapp, cfg_path) -> None:
    """Regression: a long checkbox label and a wrapped label's sizeHint once pushed every page
    past the right edge (clipped text, off-screen combos)."""
    w = SettingsWindow(Settings())
    viewport_at_min = w.minimumWidth() - w._nav.width() - 1
    assert w._stack.minimumSizeHint().width() <= viewport_at_min
    assert w._stack.sizeHint().width() == 0  # never asks the scroll area for more width


def test_status_module_imports_without_qt() -> None:
    """main.py imports it even on a headless install without PyQt6."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(repo)!r}); sys.modules['PyQt6'] = None; "
        "import budgie_voice.ui.status"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-300:]


def test_every_existing_control_still_exists(qapp, cfg_path) -> None:
    w = SettingsWindow(Settings())
    for attr in ("_hotkey_edit", "_mode_toggle", "_mode_push", "_backend_combo", "_model_combo", "_lang_edit",
                 "_conf_slider", "_mic_combo", "_denoise_chk", "_speaker_chk", "_speaker_thresh_slider",
                 "_num_chk", "_punct_chk", "_cap_chk", "_lang_chk", "_visualizer_combo"):
        assert hasattr(w, attr), attr


def test_save_preserves_unknown_keys_and_unexposed_settings(qapp, cfg_path, monkeypatch) -> None:
    original = {
        "vad": {"aggressiveness": 3, "pre_roll_ms": 450},
        "audio": {"input_gain_pct": 90, "device": "legacy-mic", "sample_rate": 16000},
        "llm": {"enabled": True, "url": "http://127.0.0.1:1234/v1", "model": "qwen/qwen3.5-4b"},
        "injection": {"progressive": True, "delay_ms": 20},
        "postprocessing": {"snippets": {"my email": "me@example.com"}, "strip_fillers": True},
        "inference": {"beam_size": 5, "min_turn_s": 20.0},
        "learning": {"vocab_enabled": False},
        "something_custom": {"kept": [1, 2, 3]},
    }
    cfg_path.write_text(yaml.dump(original))
    applied: list[str] = []
    monkeypatch.setattr(window_mod, "sync_theme", lambda app, mode: applied.append(mode))

    w = SettingsWindow(load_settings(cfg_path))
    w._theme_combo.setCurrentIndex(w._theme_combo.findData("dark"))
    w._save()

    saved = yaml.safe_load(cfg_path.read_text())
    for section, values in original.items():
        for key, val in values.items():
            if (section, key) == ("audio", "device"):
                continue  # superseded by audio.devices (existing behaviour)
            assert saved[section][key] == val, f"{section}.{key} lost or changed"
    assert "device" not in saved["audio"]
    assert saved["ui"] == {"theme": "dark"} and applied == ["dark"]
    assert load_settings(cfg_path).ui.theme == "dark"


def test_save_without_changes_is_stable(qapp, cfg_path, monkeypatch) -> None:
    monkeypatch.setattr(window_mod, "sync_theme", lambda app, mode: None)
    cfg_path.write_text(yaml.dump({"vad": {"aggressiveness": 3}, "ui": {"theme": "light"}}))
    SettingsWindow(load_settings(cfg_path))._save()
    first = yaml.safe_load(cfg_path.read_text())
    SettingsWindow(load_settings(cfg_path))._save()
    assert yaml.safe_load(cfg_path.read_text()) == first
    assert first["vad"] == {"aggressiveness": 3} and first["ui"]["theme"] == "light"


def test_save_creates_the_file_when_missing(qapp, cfg_path, monkeypatch) -> None:
    monkeypatch.setattr(window_mod, "sync_theme", lambda app, mode: None)
    assert not cfg_path.exists()
    SettingsWindow(Settings())._save()
    assert load_settings(cfg_path).ui.theme == "system"


# ---------------------------------------------------------------------------
# Integration-review regressions
# ---------------------------------------------------------------------------

def test_dropped_status_subscribers_are_freed_and_pruned() -> None:
    import gc

    class Listener:
        def __init__(self) -> None:
            self.calls = 0

        def on_change(self) -> None:
            self.calls += 1

    st = RuntimeStatus()
    kept, dropped = Listener(), Listener()
    st.subscribe(kept.on_change)
    st.subscribe(dropped.on_change)
    del dropped
    gc.collect()
    st.set_asr_loading(True)  # any change emits
    assert kept.calls == 1 and len(st._subs) == 1  # the dead one was pruned, not called
    st.unsubscribe(kept.on_change)
    assert st._subs == []


def test_closed_settings_windows_do_not_pile_up_as_subscribers(qapp, cfg_path) -> None:
    """Regression: each window used to stay alive through the status that held its Overview."""
    import gc

    from PyQt6.QtCore import QEvent

    st = RuntimeStatus()
    for _ in range(5):
        w = SettingsWindow(Settings(), status=st)
        assert len(st._subs) == 1
        del w
        for _ in range(3):
            gc.collect()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
        st.set_asr_loading(False)  # an emit prunes what died
        assert len(st._subs) == 0


def test_a_failing_activity_record_cannot_break_the_callback_chain(monkeypatch) -> None:
    st = RuntimeStatus()
    monkeypatch.setattr(st, "_record", lambda *a: 1 / 0)
    st.on_session_state("completed", 5)  # must not raise: it runs inside main's state-callback chain


def test_save_survives_sections_left_empty_in_the_yaml(qapp, cfg_path, monkeypatch) -> None:
    monkeypatch.setattr(window_mod, "sync_theme", lambda app, mode: None)
    cfg_path.write_text("ui:\nindicator:\nvad:\n  aggressiveness: 3\ncustom: 1\n")  # ui/indicator are None
    w = SettingsWindow(load_settings(cfg_path))
    assert w._theme_combo.currentData() == "system"  # an empty ui: means defaults
    w._save()
    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["ui"] == {"theme": "system"} and saved["indicator"] == {"visualizer": "waveform"}
    assert saved["vad"] == {"aggressiveness": 3} and saved["custom"] == 1

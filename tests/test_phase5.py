"""Phase 5: backend registry/fallback, live backend swap, hot-reload parts, privacy, config docs."""
from __future__ import annotations

import dataclasses
import logging
import re
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from test_session_reliability import _frame, _Rig  # reuse the Phase 1 rig

from budgie_voice.config.settings import ActivationSettings, Settings
from budgie_voice.inference.backends.registry import available_backends, models_for, resolve
from budgie_voice.inference.server import InferenceServer
from budgie_voice.injection.base import run_checked
from budgie_voice.orchestrator import dictation_service
from budgie_voice.utils.exceptions import BudgieInjectionError

MARKER = "SECRETMARKER"


# -- registry ---------------------------------------------------------------

def test_every_backend_lists_models() -> None:
    assert available_backends() == ["faster_whisper", "parakeet"]
    for name in available_backends():
        assert models_for(name)
    assert models_for("nemotron") == []


def test_resolve_falls_back_instead_of_crashing() -> None:
    assert resolve("parakeet", "nemo-parakeet-tdt-0.6b-v3") == ("parakeet", "nemo-parakeet-tdt-0.6b-v3", None)
    backend, model, problem = resolve("bogus", "whatever")
    assert (backend, model) == ("faster_whisper", "small") and "bogus" in problem
    backend, model, problem = resolve("parakeet", "small")  # a Whisper model on Parakeet
    assert backend == "parakeet" and model in models_for("parakeet") and "small" in problem


# -- example config ---------------------------------------------------------

def _unknown_keys(data: dict, cls: type, path: str = "") -> list[str]:
    fields = {f.name: f for f in dataclasses.fields(cls)}
    bad = []
    for key, val in data.items():
        if key not in fields:
            bad.append(path + key)
        elif isinstance(val, dict):
            default = fields[key].default_factory()  # type: ignore[misc]
            if dataclasses.is_dataclass(default):
                bad += _unknown_keys(val, type(default), f"{path}{key}.")
    return bad


def test_example_config_has_no_unknown_keys() -> None:
    example = Path(__file__).parent.parent / "config.yaml.example"
    assert _unknown_keys(yaml.safe_load(example.read_text()), Settings) == []


def test_readme_settings_table_matches_settings() -> None:
    """Every `key` | `default` row in the README names a real setting with that default."""
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    rows = re.findall(r"^\| `([a-z_]+\.[a-z_]+)` \| `([^`]*)` \|", readme, re.MULTILINE)
    assert rows
    for key, shown in rows:
        section, name = key.split(".")
        actual = getattr(getattr(Settings(), section), name)  # AttributeError = stale README key
        if isinstance(actual, bool):
            assert shown == str(actual).lower(), key
        elif actual is None:
            assert shown == "null", key
        elif isinstance(actual, list):
            assert shown == "[]" and actual == [], key
        elif isinstance(actual, dict):
            assert shown == "{}" and actual == {}, key
        elif isinstance(actual, (int, float)):
            assert float(shown) == actual, key
        else:
            assert shown == actual, key


# -- live backend swap ------------------------------------------------------

def test_swap_backend_reaches_the_next_turn() -> None:
    class _Tag:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def transcribe_stream(self, frames, language, hotwords=None):  # noqa: ANN001, ANN201
            return self.tag

    server = InferenceServer(_Tag("old"))  # type: ignore[arg-type]
    assert server.transcribe_stream(None, "en") == "old"
    server.swap_backend(_Tag("new"))  # type: ignore[arg-type]
    assert server.transcribe_stream(None, "en") == "new"


# -- hot reload: hotkey -----------------------------------------------------

def test_hotkey_apply_restarts_listener_only_on_change() -> None:
    from budgie_voice.orchestrator.hotkey import HotkeyManager  # noqa: PLC0415

    with patch("budgie_voice.orchestrator.hotkey.pynput.keyboard.Listener") as listener:
        mgr = HotkeyManager(MagicMock(), MagicMock(), ActivationSettings(trigger="pynput"))
        mgr.start()
        assert listener.call_count == 1

        mgr.apply(ActivationSettings(trigger="pynput", debounce_ms=100))  # unrelated change
        assert listener.call_count == 1

        mgr.apply(ActivationSettings(trigger="pynput", hotkey="ctrl+alt+d"))
        assert listener.call_count == 2
        assert len(mgr._hotkey) == 3  # noqa: SLF001 — the new combo is live

        mgr.apply(ActivationSettings(trigger="dbus", hotkey="ctrl+alt+d"))  # DBus owns it now
        assert listener.call_count == 2 and mgr._listener is None  # noqa: SLF001


# -- privacy: transcript text never reaches INFO+ logs or notifications ------

@pytest.mark.parametrize("script", ["sleep 5", "echo $0 >&2; exit 1"])  # timeout / tool echoing its input
def test_typing_error_does_not_carry_the_text(script: str) -> None:
    with pytest.raises(BudgieInjectionError) as err:
        run_checked(["sh", "-c", script, MARKER], timeout=0.2, what="typing")
    assert MARKER not in "".join(traceback.format_exception(err.value))


async def test_session_never_logs_or_notifies_transcript(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    rig = _Rig(monkeypatch)
    rig.backend.script = [f"{MARKER} one", f"{MARKER} two"]
    await rig.svc.start()

    await rig.dictate(_frame(1000))  # success path
    assert rig.typed() == [f"{MARKER} one"]

    def _boom(text: str) -> None:  # failure path: a real timed-out typing call
        run_checked(["sh", "-c", "echo $0 >&2; sleep 5", text], timeout=0.2, what="typing")

    rig.injector.commit_text.side_effect = _boom
    await rig.dictate(_frame(2000))
    await rig.close()

    notified = " ".join(str(c) for c in dictation_service._notify.call_args_list)  # noqa: SLF001
    assert "Typing failed" in notified  # the failure path really ran
    assert MARKER not in caplog.text
    assert MARKER not in notified

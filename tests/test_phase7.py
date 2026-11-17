"""Phase 7: snippets, filler removal, "scratch that", per-app profiles, pause_media, language auto."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from test_session_reliability import _frame, _Rig

from budgie_voice.config.settings import ActivationSettings, PostprocessingSettings
from budgie_voice.inference.backends import parakeet
from budgie_voice.inference.backends.faster_whisper import FasterWhisperBackend
from budgie_voice.inference.backends.parakeet import ParakeetBackend
from budgie_voice.injection.secure import SecureFieldDetector
from budgie_voice.orchestrator.dictation_service import DictationService
from budgie_voice.postprocessing.pipeline import PostProcessingPipeline
from budgie_voice.utils import media


# -- snippets -----------------------------------------------------------------

def test_snippet_is_whole_phrase_case_insensitive_and_longest_first() -> None:
    pp = PostProcessingPipeline(snippets={
        "my email": "me@example.com",
        "my email address": "ADDR",
    })
    assert pp.run("Send it to MY EMAIL please") == "Send it to me@example.com please"
    assert pp.run("my email address") == "ADDR"  # longest wins over its own prefix
    assert pp.run("my emails") == "My emails"  # whole phrase only


def test_snippet_text_is_inserted_verbatim_even_with_command_words() -> None:
    pp = PostProcessingPipeline(snippets={"sign off": "best comma karan new line twenty two"})

    # no punctuation command, number or capital rewrites the snippet; the rest of the text is processed
    assert pp.run("thanks sign off") == "Thanks best comma karan new line twenty two"
    assert pp.run("sign off period") == "best comma karan new line twenty two."


def test_snippet_does_not_capitalise_what_follows_it() -> None:
    pp = PostProcessingPipeline(snippets={"my email": "me@example.com"})
    assert pp.run("my email is here") == "me@example.com is here"


def test_snippets_default_to_off() -> None:
    assert PostProcessingPipeline().run("my email") == "My email"
    assert PostprocessingSettings().snippets == {}


# -- filler removal ------------------------------------------------------------

def test_strip_fillers_is_off_by_default_and_works_when_on() -> None:
    assert "um" in PostProcessingPipeline().run("hello um world")
    assert PostProcessingPipeline(strip_fillers=True).run("hello um, uh world") == "Hello world"
    assert PostprocessingSettings().strip_fillers is False


# -- scratch that ---------------------------------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("Hello world. This is wrong. Scratch that. Next bit.", "Hello world. Next bit."),
    ("Hello world. This is wrong, scratch that.", "Hello world."),
    ("Scratch that. Hello there.", "Hello there."),            # nothing before it: just the phrase goes
    ("One. Two. Three. Scratch that. Scratch that.", "One."),  # each one removes one more
    ("I need to scratch that itch.", "I need to scratch that itch."),  # phrase inside a sentence
    # a spoken paragraph break also bounds the sentence, so only the last paragraph goes
    ("Hello new paragraph This is wrong. Scratch that.", "Hello"),
])
def test_scratch_that(raw: str, expected: str) -> None:
    assert PostProcessingPipeline().run(raw) == expected


def test_scratch_that_has_its_own_toggle() -> None:
    raw = "Hello world. This is wrong. Scratch that."
    assert PostProcessingPipeline(spoken_edits=False).run(raw) == raw
    assert PostprocessingSettings().spoken_edits is True


def test_my_period_is_late_is_still_not_a_full_stop_with_everything_on() -> None:
    pp = PostProcessingPipeline(
        strip_fillers=True, spoken_edits=True, snippets={"my email": "me@example.com"},
    )
    assert pp.run("my period is late") == "My period is late"
    assert pp.run("my period is late period") == "My period is late."


# -- per-app profiles -----------------------------------------------------------

def test_overrides_change_toggles_and_ignore_unknown_keys(caplog: pytest.LogCaptureFixture) -> None:
    base = PostProcessingPipeline()
    tuned = base.with_overrides({"auto_capitalize": False, "strip_trailing_period": True, "bogus": 1})

    assert base.run("hello world.") == "Hello world."  # the base pipeline is untouched
    assert tuned.run("hello world.") == "hello world"
    assert "bogus" in caplog.text


def test_trailing_period_strip_leaves_ellipsis_and_other_marks() -> None:
    pp = PostProcessingPipeline(strip_trailing_period=True)
    assert pp.run("Wait...") == "Wait..."
    assert pp.run("Really?") == "Really?"


async def test_profile_applies_by_wm_class_at_session_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(
        SecureFieldDetector, "_xprop",
        staticmethod(lambda wid, prop: 'WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"'),
    )
    rig = _Rig(monkeypatch)
    rig.svc._pipeline = PostProcessingPipeline()  # noqa: SLF001
    rig.svc._settings.postprocessing.profiles = [  # noqa: SLF001
        {"match": "firefox"},
        {"match": "terminal|konsole", "auto_capitalize": False, "strip_trailing_period": True},
    ]
    rig.backend.script = ["hello world."]
    await rig.svc.start()
    await rig.dictate(_frame(1000))

    assert rig.typed() == ["hello world"]
    await rig.close()


async def test_no_matching_profile_means_default_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(SecureFieldDetector, "_xprop", staticmethod(lambda wid, prop: '"emacs", "Emacs"'))
    rig = _Rig(monkeypatch)
    rig.svc._pipeline = PostProcessingPipeline()  # noqa: SLF001
    rig.svc._settings.postprocessing.profiles = [{"match": "terminal", "auto_capitalize": False}]  # noqa: SLF001
    rig.backend.script = ["hello world."]
    await rig.svc.start()
    await rig.dictate(_frame(1000))

    assert rig.typed() == ["Hello world."]
    await rig.close()


def test_profiles_on_wayland_use_the_default_and_log_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(SecureFieldDetector, "_xprop", staticmethod(lambda *a: pytest.fail("no WM_CLASS on Wayland")))
    svc = DictationService.__new__(DictationService)
    svc._settings = SimpleNamespace(postprocessing=PostprocessingSettings(profiles=[{"match": "x"}]))  # noqa: SLF001
    svc._wayland_profile_logged = False  # noqa: SLF001
    caplog.set_level(logging.INFO)

    assert svc._resolve_profile(7) is None and svc._resolve_profile(7) is None  # noqa: SLF001
    assert caplog.text.count("need X11") == 1


def test_bad_profile_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(SecureFieldDetector, "_xprop", staticmethod(lambda wid, prop: '"term", "Term"'))
    svc = DictationService.__new__(DictationService)
    profiles = [{"match": "("}, {"auto_capitalize": False}, {"match": "term", "spoken_edits": False}]
    svc._settings = SimpleNamespace(postprocessing=PostprocessingSettings(profiles=profiles))  # noqa: SLF001
    svc._wayland_profile_logged = False  # noqa: SLF001

    assert svc._resolve_profile(7) == {"spoken_edits": False}  # noqa: SLF001
    assert caplog.text.count("Ignoring bad profile") == 2


# -- pause media ----------------------------------------------------------------

def _fake_playerctl(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, str]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake(*args: str) -> str:
        calls.append(args)
        if args == ("-l",):
            return "\n".join(statuses)
        if args[0] == "-p" and args[2] == "status":
            return statuses[args[1]]
        return ""

    monkeypatch.setattr(media, "_playerctl", fake)
    return calls


def test_only_players_that_were_playing_are_paused_and_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_playerctl(monkeypatch, {"spotify": "Playing", "vlc": "Paused"})

    assert media.pause_playing() == ["spotify"]
    media.resume(["spotify"])

    assert ("-p", "spotify", "pause") in calls and ("-p", "vlc", "pause") not in calls
    assert ("-p", "spotify", "play") in calls and ("-p", "vlc", "play") not in calls


def test_missing_playerctl_is_a_silent_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise FileNotFoundError("playerctl")

    monkeypatch.setattr(media.subprocess, "run", missing)

    assert media.pause_playing() == []
    media.resume(["spotify"])  # must not raise


async def test_media_pauses_at_start_and_resumes_when_the_session_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(media, "pause_playing", lambda: events.append("pause") or ["p"])
    monkeypatch.setattr(media, "resume", lambda players: events.append(f"resume {players}"))
    rig = _Rig(monkeypatch)
    rig.svc._settings.activation.pause_media = True  # noqa: SLF001
    await rig.svc.start()

    await rig.dictate(_frame(1000))
    assert events == ["pause", "resume ['p']"]

    await rig.dictate(_frame(2000), stop=False)
    await rig.svc.cancel()  # a cancelled session resumes too
    assert events[2:] == ["pause", "resume ['p']"]
    await rig.close()


async def test_media_is_untouched_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "pause_playing", lambda: pytest.fail("paused"))
    rig = _Rig(monkeypatch)
    assert ActivationSettings().pause_media is False
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    await rig.close()


# -- language auto ---------------------------------------------------------------

@pytest.mark.parametrize(("setting", "passed"), [("auto", None), ("en", "en"), ("de", "de")])
def test_whisper_gets_no_language_for_auto(setting: str, passed: str | None) -> None:
    backend = FasterWhisperBackend()
    backend._model = MagicMock()  # noqa: SLF001
    backend._model.transcribe.return_value = ([], SimpleNamespace(language="en"))  # noqa: SLF001

    backend._transcribe_sync(np.zeros(1600, np.float32), setting)  # noqa: SLF001

    assert backend._model.transcribe.call_args.kwargs["language"] == passed  # noqa: SLF001


def test_parakeet_logs_once_and_uses_english_for_auto(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(parakeet, "_AUTO_LOGGED", False)
    backend = ParakeetBackend()
    backend._model = MagicMock()  # noqa: SLF001
    backend._model.recognize.return_value = "hello"  # noqa: SLF001
    caplog.set_level(logging.INFO)

    first = backend._transcribe_sync(np.zeros(1600, np.float32), "auto")  # noqa: SLF001
    second = backend._transcribe_sync(np.zeros(1600, np.float32), "auto")  # noqa: SLF001

    assert first is not None and first.language == "en" and second is not None
    assert caplog.text.count("cannot detect the language") == 1

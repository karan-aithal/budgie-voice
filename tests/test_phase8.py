"""Phase 8: optional local LLM — polish, command mode, loopback guard, fallbacks.

The HTTP side runs against a fake OpenAI-style server on 127.0.0.1; the service side reuses the
Phase 1 rig (real DictationService, in-memory inference server).
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from evdev import ecodes

from budgie_voice.config.settings import LlmSettings, Settings
from budgie_voice.orchestrator import dictation_service
from budgie_voice.orchestrator import llm as llm_mod
from budgie_voice.orchestrator.dictation_service import _build_llm
from budgie_voice.orchestrator.evdev_hotkey import EvdevHotkey
from budgie_voice.orchestrator.llm import LlmClient
from budgie_voice.orchestrator.session import SessionState
from budgie_voice.utils import selection

from test_phase6 import DOWN, UP, FakeDev, Svc, World, world  # noqa: F401 — world is a fixture
from test_session_reliability import _frame, _Rig

TEN_WORDS = "so um I think we should maybe ship the release on Friday"


# -- fake Ollama --------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.hits += 1  # type: ignore[attr-defined]
        self.server.bodies.append(body)  # type: ignore[attr-defined]
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        time.sleep(self.server.delay)  # type: ignore[attr-defined]
        reply = {"choices": [{"message": {"role": "assistant", "content": self.server.answer}}]}  # type: ignore[attr-defined]
        out = self.server.raw or json.dumps(reply).encode()  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args: object) -> None:
        pass


class _Server(ThreadingHTTPServer):
    hits = 0
    delay = 0.0
    answer = ""
    raw: bytes | None = None  # if set, sent verbatim instead of a well-formed reply

    def handle_error(self, *args: object) -> None:  # client gave up (timeout test)
        pass


@pytest.fixture
def ollama():
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.bodies, srv.paths = [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.cfg = lambda **kw: LlmSettings(  # type: ignore[attr-defined]
        enabled=True, url=f"http://127.0.0.1:{srv.server_port}/v1", model="m", **kw,
    )
    yield srv
    srv.shutdown()
    srv.server_close()


# -- client -------------------------------------------------------------------

async def test_polish_uses_the_models_answer(ollama) -> None:
    ollama.answer = "I think we should ship the release on Friday."
    out = await LlmClient(ollama.cfg()).polish(TEN_WORDS)
    assert out == ollama.answer
    assert ollama.bodies[0]["model"] == "m"
    assert ollama.bodies[0]["reasoning_effort"] == "none"  # no minute-long thinking per dictation
    assert ollama.paths == ["/v1/chat/completions"]  # LM Studio and Ollama both serve this


async def test_polish_drops_inline_thinking(ollama) -> None:
    ollama.answer = "<think>\nthe user wants it cleaner\n</think>\nI think we should ship the release on Friday."
    assert await LlmClient(ollama.cfg()).polish(TEN_WORDS) == "I think we should ship the release on Friday."


async def test_polish_timeout_falls_back(ollama) -> None:
    ollama.delay, ollama.answer = 1.0, "never used"
    assert await LlmClient(ollama.cfg(timeout_s=0.1)).polish(TEN_WORDS) == TEN_WORDS


async def test_polish_server_down_falls_back() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: connection refused
    cfg = LlmSettings(enabled=True, url=f"http://127.0.0.1:{port}", model="m")
    assert await LlmClient(cfg).polish(TEN_WORDS) == TEN_WORDS


async def test_polish_rejects_a_wildly_longer_answer(ollama) -> None:
    ollama.answer = TEN_WORDS + " and also here is a long unrequested essay " * 5
    assert await LlmClient(ollama.cfg()).polish(TEN_WORDS) == TEN_WORDS


async def test_polish_rejects_empty_answer(ollama) -> None:
    ollama.answer = "  "
    assert await LlmClient(ollama.cfg()).polish(TEN_WORDS) == TEN_WORDS


GOOD = "I think we should ship the release on Friday."


@pytest.mark.parametrize("answer", [
    f"Here is the cleaned text: {GOOD}",
    f"Sure! {GOOD}",
    f"Corrected text: {GOOD}",
    f"```\n{GOOD}\n```",
    f'"{GOOD}"',
    f"<think>hmm, the user wants",  # cut off mid-thought
    f"{GOOD}\n\nI removed the filler 'um' and fixed the capitalization.",  # explanation appended
])
async def test_polish_rejects_wrappers_and_explanations(ollama, answer: str) -> None:
    ollama.answer = answer
    assert await LlmClient(ollama.cfg()).polish(TEN_WORDS) == TEN_WORDS


@pytest.mark.parametrize("raw", [
    b"not json at all",
    b"{}",
    b'{"choices": []}',
    b'{"choices": [{"message": {"content": null, "reasoning_content": "thinking only"}}]}',
    b'{"choices": [{"message": {"content": 42}}]}',
])
async def test_polish_survives_malformed_replies(ollama, raw: bytes) -> None:
    ollama.raw = raw
    assert await LlmClient(ollama.cfg()).polish(TEN_WORDS) == TEN_WORDS


async def test_a_dictation_that_starts_with_sure_is_not_mistaken_for_a_wrapper(ollama) -> None:
    src = "sure we can um ship the release on Friday if the review passes"
    ollama.answer = "Sure, we can ship the release on Friday if the review passes."
    assert await LlmClient(ollama.cfg()).polish(src) == ollama.answer


async def test_short_dictation_is_not_sent(ollama) -> None:
    ollama.answer = "x"
    assert await LlmClient(ollama.cfg(min_words=8)).polish("just five words here ok") == "just five words here ok"
    assert ollama.hits == 0


async def test_rewrite_also_suppresses_thinking_and_rejects_wrappers(ollama) -> None:
    client = LlmClient(ollama.cfg())
    ollama.answer = "Could you please send me the report?"
    assert await client.rewrite("hey send me the report", "make it formal") == ollama.answer
    assert ollama.bodies[0]["reasoning_effort"] == "none"
    ollama.answer = "Here is the rewritten text: Could you please send me the report?"
    assert await client.rewrite("hey send me the report", "make it formal") is None
    ollama.raw = b'{"choices": [{"message": {"content": null}}]}'
    assert await client.rewrite("hey send me the report", "make it formal") is None


async def test_rewrite_failure_is_none(ollama) -> None:
    ollama.delay = 1.0
    assert await LlmClient(ollama.cfg(timeout_s=0.1)).rewrite("hello", "shout") is None


# -- loopback guard -----------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434", "http://127.0.0.2:11434", "http://[::1]:11434",
    "http://[::ffff:127.0.0.1]:11434", "http://localhost:11434",
])
def test_loopback_urls_are_accepted(url: str) -> None:
    LlmClient(LlmSettings(enabled=True, url=url, model="m"))


@pytest.mark.parametrize("url", [
    "http://8.8.8.8:11434", "http://192.168.1.5:11434", "http://0.0.0.0:11434",
    "http://[2001:db8::1]:11434", "http://[::ffff:8.8.8.8]:11434", "ftp://127.0.0.1", "127.0.0.1:11434",
])
def test_non_loopback_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        LlmClient(LlmSettings(enabled=True, url=url, model="m"))


def test_allow_remote_is_the_only_way_past_the_guard() -> None:
    LlmClient(LlmSettings(enabled=True, url="http://8.8.8.8:11434", model="m", allow_remote=True))


def test_model_must_be_set() -> None:
    with pytest.raises(ValueError):
        LlmClient(LlmSettings(enabled=True))


def test_disabled_never_constructs_the_client(monkeypatch) -> None:
    ctor = MagicMock()
    monkeypatch.setattr(llm_mod, "LlmClient", ctor)
    assert _build_llm(Settings()) is None
    ctor.assert_not_called()


def test_enabled_with_unsafe_url_leaves_llm_off() -> None:
    s = Settings()
    s.llm = LlmSettings(enabled=True, url="http://8.8.8.8:11434", model="m")
    assert _build_llm(s) is None


# -- service: polish ----------------------------------------------------------

async def test_polished_text_is_typed_and_learning_ignores_untouched_polish(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    rig.backend.script = ["um hello there", "um hello again"]
    rig.svc._llm = SimpleNamespace(polish=AsyncMock(side_effect=lambda t: t.replace("um ", "")))  # noqa: SLF001
    rig.svc._corrections = MagicMock()  # noqa: SLF001
    rig.svc._corrections.apply.side_effect = lambda t: t  # noqa: SLF001
    rig.svc._settings.injection.preview_before_inject = True  # noqa: SLF001
    rig.svc._on_preview = AsyncMock(side_effect=lambda t: t)  # noqa: SLF001
    await rig.svc.start()

    await rig.dictate(_frame(1000))  # accepted as shown
    assert rig.typed() == ["hello there"]
    rig.svc._corrections.learn.assert_not_called()  # noqa: SLF001

    rig.svc._on_preview = AsyncMock(return_value="hello, again")  # noqa: SLF001
    await rig.dictate(_frame(1000))  # edited in the preview
    rig.svc._corrections.learn.assert_called_once_with("um hello again", "hello, again")  # noqa: SLF001
    await rig.close()


# -- service: command mode ----------------------------------------------------

async def test_command_without_selection_does_nothing(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "  \n")
    rig.svc._llm = SimpleNamespace(rewrite=AsyncMock())  # noqa: SLF001
    await rig.svc.start()
    await rig.svc.command_start()
    assert rig.svc.state == SessionState.IDLE
    assert rig.states == []  # no session was even started
    assert "Select some text" in dictation_service._notify.call_args.args[1]  # noqa: SLF001
    rig.svc._llm.rewrite.assert_not_called()  # noqa: SLF001
    await rig.close()


async def test_command_with_llm_off_says_so(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "some text")
    await rig.svc.start()
    await rig.svc.command_start()
    assert rig.svc.state == SessionState.IDLE
    assert "llm.enabled" in dictation_service._notify.call_args.args[1]  # noqa: SLF001
    await rig.close()


async def test_command_model_failure_types_nothing(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "hello world")
    rig.backend.script = ["make it shorter"]
    rig.svc._llm = SimpleNamespace(rewrite=AsyncMock(return_value=None))  # noqa: SLF001
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000)]
    await rig.svc.command_start()
    await asyncio.sleep(0.05)
    await rig.svc.command()  # second press stops

    assert rig.typed() == []
    assert rig.svc.state == SessionState.IDLE
    assert "Command failed" in dictation_service._notify.call_args.args[1]  # noqa: SLF001
    await rig.close()


async def test_command_rewrites_selection_and_next_dictation_is_plain(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "hello world")
    rig.backend.script = ["make it shorter", "plain words"]
    rewrite = AsyncMock(return_value="Hi.")
    rig.svc._llm = SimpleNamespace(rewrite=rewrite, polish=AsyncMock(side_effect=lambda t: t))  # noqa: SLF001
    rig.svc._corrections = MagicMock()  # noqa: SLF001
    rig.svc._corrections.apply.side_effect = lambda t: t  # noqa: SLF001
    await rig.svc.start()

    rig.adapter.frames = [_frame(1000)]
    await rig.svc.command_start()
    await asyncio.sleep(0.05)
    await rig.svc.command()
    assert rig.typed() == ["Hi."]
    rewrite.assert_awaited_once_with("hello world", "make it shorter")
    rig.svc._corrections.learn.assert_not_called()  # noqa: SLF001

    await rig.dictate(_frame(2000))  # the selection must not leak into this one
    assert rig.typed() == ["Hi.", "plain words"]
    assert rewrite.await_count == 1
    await rig.close()


# -- hotkey -------------------------------------------------------------------

class _CmdSvc(Svc):
    def command(self) -> str:
        return "command"

    def command_start(self) -> str:
        return "cstart"


def _cmd_listener(mode: str) -> EvdevHotkey:
    lst = EvdevHotkey(_CmdSvc(), None, "f9", mode, "f10")  # type: ignore[arg-type]
    lst._sync(initial=True)  # noqa: SLF001
    return lst


def test_command_hotkey_toggle_mode(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = _cmd_listener("toggle")
    for code, value in ((ecodes.KEY_F10, DOWN), (ecodes.KEY_F10, UP), (ecodes.KEY_F9, DOWN), (ecodes.KEY_F9, UP)):
        kbd.push(code, value)
        lst.poll(0.1)
    assert world.calls == ["command", ("toggle", "evdev")]


def test_command_hotkey_hold_mode_and_f9_unchanged(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = _cmd_listener("push_to_hold")
    for code, value in (
        (ecodes.KEY_F9, DOWN), (ecodes.KEY_F9, UP), (ecodes.KEY_F10, DOWN), (ecodes.KEY_F10, UP),
    ):
        kbd.push(code, value)
        lst.poll(0.1)
    assert world.calls == ["start", "stop", "cstart", "stop"]


def test_command_hotkey_must_differ_and_be_valid() -> None:
    with pytest.raises(ValueError):
        EvdevHotkey(Svc(), None, "f9", "toggle", "f9")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EvdevHotkey(Svc(), None, "f9", "toggle", "f99")  # type: ignore[arg-type]


# -- selection ----------------------------------------------------------------

@pytest.mark.parametrize(("session", "tool"), [("wayland", "wl-paste"), ("x11", "xclip")])
def test_selection_uses_the_sessions_tool(monkeypatch, session: str, tool: str) -> None:
    seen: list[str] = []

    def run(cmd, **kw):  # noqa: ANN001, ANN003, ANN202
        seen.append(cmd[0])
        return SimpleNamespace(returncode=0, stdout="picked")

    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    monkeypatch.setattr(subprocess, "run", run)
    assert selection.read_primary() == "picked"
    assert seen == [tool]


def test_selection_empty_when_tool_fails(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="junk"))
    assert selection.read_primary() == ""
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError))
    assert selection.read_primary() == ""

"""Phase 4: typing is bounded and checked; secure-field titles only hit dialogs; text is recoverable."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_session_reliability import _frame, _Rig

from myna.injection.base import chunk_text
from myna.injection.secure import SecureFieldDetector
from myna.orchestrator import dictation_service
from myna.utils.exceptions import MynaInjectionError

DELAY_MS = 12


def _xdotool():
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        from myna.injection.xdotool import XdotoolBackend  # noqa: PLC0415

        return XdotoolBackend(delay_ms=DELAY_MS)


def test_long_text_is_typed_in_bounded_pieces_with_matching_timeouts() -> None:
    text = " ".join(f"word{i}" for i in range(600))  # ~4000 chars
    backend = _xdotool()

    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as run:
        backend._type_segment(text)  # noqa: SLF001

    pieces = [call.args[0][-1] for call in run.call_args_list]
    assert len(pieces) >= 15
    assert "".join(pieces) == text
    for call in run.call_args_list:
        piece = call.args[0][-1]
        assert len(piece) <= 200
        assert call.kwargs["timeout"] >= len(piece) * DELAY_MS / 1000  # never shorter than the typing itself


def test_unbroken_run_is_cut_hard() -> None:
    url = "https://example.com/" + "a" * 500
    assert all(len(p) <= 200 for p in chunk_text(url))
    assert "".join(chunk_text(url)) == url


def test_nonzero_exit_raises_with_stderr_and_stops() -> None:
    backend = _xdotool()
    text = " ".join(f"word{i}" for i in range(200))

    with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr=b"X Error: BadWindow")) as run:
        with pytest.raises(MynaInjectionError, match="BadWindow"):
            backend._type_segment(text)  # noqa: SLF001

    assert run.call_count == 1  # first failed piece ends the typing


def test_ydotool_maps_the_non_ascii_the_pipeline_emits() -> None:
    with patch("myna.injection.ydotool_backend.is_ydotool_available", return_value=True), patch(
        "myna.injection.ydotool_backend._ensure_daemon",
    ):
        from myna.injection.ydotool_backend import YdotoolBackend  # noqa: PLC0415

        backend = YdotoolBackend(delay_ms=DELAY_MS)

    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as run:
        asyncio.run(backend.commit_text("“hi” — ok"))

    assert run.call_args.args[0][-1] == '"hi" - ok'


def test_title_only_blocks_dialogs() -> None:
    def props(window_type: str):
        values = {"WM_NAME": "Reset password - docs", "_NET_WM_WINDOW_TYPE": window_type}
        return lambda wid, prop: values.get(prop, "")

    detector = SecureFieldDetector()
    with patch.object(detector, "_xprop", side_effect=props("_NET_WM_WINDOW_TYPE_NORMAL")):
        assert detector.is_secure(1) is False  # a browser tab about passwords
    with patch.object(detector, "_xprop", side_effect=props("_NET_WM_WINDOW_TYPE_DIALOG")):
        assert detector.is_secure(1) is True


async def test_last_transcript_replaced_per_session_and_failure_points_to_it(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    await rig.dictate(_frame(2000))
    assert rig.svc.last_transcript == "2000"

    rig.injector.commit_text = AsyncMock(side_effect=MynaInjectionError("xdotool died"))
    await rig.dictate(_frame(3000))

    assert rig.svc.last_transcript == "3000"  # kept although typing failed
    assert "Copy Last Transcript" in dictation_service._notify.call_args.args[1]  # noqa: SLF001
    await rig.close()

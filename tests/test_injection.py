"""Phase 2 tests — injection backends."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from budgie_voice.injection.secure import SecureFieldDetector


# ---------------------------------------------------------------------------
# SecureFieldDetector
# ---------------------------------------------------------------------------

class TestSecureFieldDetector:
    def test_none_window_id_is_safe(self) -> None:
        detector = SecureFieldDetector()
        assert detector.is_secure(None) is False

    def test_blocked_wm_class(self) -> None:
        detector = SecureFieldDetector()
        with patch.object(
            detector, "_xprop", side_effect=lambda wid, prop: (
                'WM_CLASS = "pinentry"' if prop == "WM_CLASS" else ""
            )
        ):
            assert detector.is_secure(12345) is True

    def test_blocked_title_pattern_on_dialog(self) -> None:
        detector = SecureFieldDetector()
        props = {"WM_NAME": "Enter your password", "_NET_WM_WINDOW_TYPE": "_NET_WM_WINDOW_TYPE_DIALOG"}
        with patch.object(detector, "_xprop", side_effect=lambda wid, prop: props.get(prop, "")):
            assert detector.is_secure(12345) is True

    def test_safe_window(self) -> None:
        detector = SecureFieldDetector()
        with patch.object(detector, "_xprop", return_value="gedit"):
            assert detector.is_secure(12345) is False


# ---------------------------------------------------------------------------
# XdotoolBackend — mocked subprocess
# ---------------------------------------------------------------------------

class TestXdotoolBackend:
    def _make_backend(self) -> object:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            return XdotoolBackend(delay_ms=0)

    def test_commit_text_calls_xdotool_type(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            backend = XdotoolBackend(delay_ms=0)

            async def _run() -> None:
                await backend.start_session(12345)
                with patch("subprocess.run") as inner_run:
                    inner_run.return_value = MagicMock(returncode=0)
                    await backend.commit_text("Hello world")
                    # xdotool type should have been called
                    assert inner_run.called
                    args = inner_run.call_args[0][0]
                    assert "xdotool" in args
                    assert "type" in args

            asyncio.run(_run())

    def test_commit_text_newline_sends_return(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            backend = XdotoolBackend(delay_ms=0)

            calls: list = []

            async def _run() -> None:
                await backend.start_session(12345)
                with patch("subprocess.run") as inner_run:
                    inner_run.return_value = MagicMock(returncode=0)
                    await backend.commit_text("line one\nline two")
                    # Should have at least 2 subprocess calls (type + key Return)
                    assert inner_run.call_count >= 2

            asyncio.run(_run())

    def test_type_never_passes_window(self) -> None:
        """--window switches xdotool to XSendEvent, which Electron/Firefox/GTK
        apps silently reject (xdotool(1) SENDEVENT NOTES). Verified empirically:
        a real GTK entry receives nothing via --window, and the text via XTEST.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            backend = XdotoolBackend(delay_ms=0)

            async def _run() -> None:
                await backend.start_session(12345)
                with patch("subprocess.run") as inner_run:
                    inner_run.return_value = MagicMock(returncode=0, stderr=b"")
                    await backend.commit_text("Hello world")

                cmds = [c[0][0] for c in inner_run.call_args_list]
                type_cmds = [c for c in cmds if "type" in c]
                assert type_cmds, "expected an xdotool type call"
                for cmd in type_cmds:
                    assert "--window" not in cmd, f"--window would use XSendEvent: {cmd}"

                # ...and the target must be focused first, or XTEST hits the wrong window
                assert any("windowactivate" in c for c in cmds)
                assert cmds.index(next(c for c in cmds if "windowactivate" in c)) < cmds.index(type_cmds[0])

            asyncio.run(_run())

    def test_activate_failure_still_types(self) -> None:
        """A dead/unfocusable target must not block injection into current focus."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            backend = XdotoolBackend(delay_ms=0)

            async def _run() -> None:
                await backend.start_session(12345)
                with patch("subprocess.run") as inner_run:
                    # windowactivate fails; type must still run
                    inner_run.side_effect = lambda cmd, **kw: MagicMock(
                        returncode=1 if "windowactivate" in cmd else 0, stderr=b"no such window",
                    )
                    await backend.commit_text("Hello")

                cmds = [c[0][0] for c in inner_run.call_args_list]
                assert any("type" in c for c in cmds)

            asyncio.run(_run())

    def test_sanitize_strips_control_chars(self) -> None:
        from budgie_voice.injection.xdotool import _sanitize  # noqa: PLC0415

        assert _sanitize("hello\x00world") == "helloworld"
        assert _sanitize("hello\nworld") == "hello\nworld"
        assert _sanitize("hello world") == "hello world"


# ---------------------------------------------------------------------------
# BackendSelector
# ---------------------------------------------------------------------------

class TestBackendSelector:
    def test_explicit_xdotool(self) -> None:
        from budgie_voice.injection.selector import select_backend  # noqa: PLC0415

        with patch("budgie_voice.injection.xdotool._check_xdotool", return_value=None):
            backend = select_backend("xdotool", delay_ms=0)
            assert backend.backend_name == "xdotool"

    def test_unknown_raises(self) -> None:
        from budgie_voice.injection.selector import select_backend  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unknown injection backend"):
            select_backend("nonexistent")

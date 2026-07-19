"""Phase 2 tests — injection backends."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from myna.injection.secure import SecureFieldDetector


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

    def test_blocked_title_pattern(self) -> None:
        detector = SecureFieldDetector()
        with patch.object(
            detector, "_xprop", side_effect=lambda wid, prop: (
                "Enter your password" if prop == "WM_NAME" else ""
            )
        ):
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
            from myna.injection.xdotool import XdotoolBackend  # noqa: PLC0415

            return XdotoolBackend(delay_ms=0)

    def test_commit_text_calls_xdotool_type(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999\n")
            from myna.injection.xdotool import XdotoolBackend  # noqa: PLC0415

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
            from myna.injection.xdotool import XdotoolBackend  # noqa: PLC0415

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

    def test_sanitize_strips_control_chars(self) -> None:
        from myna.injection.xdotool import _sanitize  # noqa: PLC0415

        assert _sanitize("hello\x00world") == "helloworld"
        assert _sanitize("hello\nworld") == "hello\nworld"
        assert _sanitize("hello world") == "hello world"


# ---------------------------------------------------------------------------
# BackendSelector
# ---------------------------------------------------------------------------

class TestBackendSelector:
    def test_explicit_xdotool(self) -> None:
        from myna.injection.selector import select_backend  # noqa: PLC0415

        with patch("myna.injection.xdotool._check_xdotool", return_value=None):
            backend = select_backend("xdotool", delay_ms=0)
            assert backend.backend_name == "xdotool"

    def test_unknown_raises(self) -> None:
        from myna.injection.selector import select_backend  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unknown injection backend"):
            select_backend("nonexistent")

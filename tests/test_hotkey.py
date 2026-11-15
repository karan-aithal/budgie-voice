"""DictationService.toggle() trigger ownership + same-source debounce.

A single key press can reach toggle() twice: on X11 both the pynput listener and
a GNOME custom shortcut invoking the DBus Toggle method see the same combo. That
starts a session and immediately kills it (`AudioAdapter opened` → `Mic capture
stopped` → `chunks=0/1` symptom). Ownership is decided by activation.trigger, not
by timing — the observed real-world gap between the two exceeded any usable window.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from myna.config.settings import ActivationSettings, Settings
from myna.orchestrator.dictation_service import DictationService
from myna.orchestrator.session import SessionState


def _service(debounce_ms: int = 350, trigger: str = "pynput") -> DictationService:
    settings = Settings()
    settings.activation.debounce_ms = debounce_ms
    settings.activation.trigger = trigger  # type: ignore[assignment]
    settings.speaker.enabled = False
    settings.learning.vocab_enabled = False
    settings.learning.corrections_enabled = False
    settings.injection.block_secure_fields = False
    settings.injection.preview_before_inject = False

    adapter = MagicMock()
    adapter.stream = lambda: _never_yield()

    client = MagicMock()
    client.send_frame = AsyncMock()
    client.flush = AsyncMock()
    client.reset = AsyncMock()

    injector = MagicMock()
    injector.is_secure_field.return_value = False
    injector.start_session = AsyncMock()
    injector.commit_text = AsyncMock()
    injector.end_session = AsyncMock()

    return DictationService(
        adapter=adapter,
        client=client,
        pipeline=MagicMock(),
        injector=injector,
        verifier=MagicMock(),
        settings=settings,
    )


async def _never_yield():
    """Stand-in audio stream that blocks instead of producing frames."""
    await asyncio.Event().wait()
    yield  # pragma: no cover


async def test_non_owner_source_rejected_regardless_of_delay() -> None:
    """The real bug: pynput owns the hotkey, so the GNOME shortcut's DBus Toggle
    must be dropped even when it lands long after the debounce window."""
    svc = _service(debounce_ms=350, trigger="pynput")

    await svc.toggle("pynput")
    await asyncio.sleep(0.5)  # past the debounce window — timing can't save us here
    await svc.toggle("dbus")

    assert svc._adapter.open.call_count == 1
    svc._adapter.close.assert_not_called()
    assert svc.state == SessionState.RECORDING

    await svc._cancel_session()  # cleanup: stop the send task


async def test_dbus_owner_rejects_pynput() -> None:
    """Mirror case: on Wayland DBus owns it, so a stray pynput event is dropped."""
    svc = _service(trigger="dbus")

    await svc.toggle("pynput")
    svc._adapter.open.assert_not_called()
    assert svc.state == SessionState.IDLE

    await svc.toggle("dbus")
    assert svc._adapter.open.call_count == 1

    await svc._cancel_session()


async def test_tray_always_allowed() -> None:
    """Tray is an explicit click — never gated on trigger ownership."""
    svc = _service(trigger="dbus")

    await svc.toggle("tray")
    assert svc._adapter.open.call_count == 1
    assert svc.state == SessionState.RECORDING

    await svc._cancel_session()


async def test_same_source_repeat_within_window_ignored() -> None:
    """Backstop for key auto-repeat / double-click."""
    svc = _service(debounce_ms=350, trigger="pynput")

    await svc.toggle("pynput")
    await svc.toggle("pynput")  # repeat, immediately after

    assert svc._adapter.open.call_count == 1
    svc._adapter.close.assert_not_called()

    await svc._cancel_session()


def test_auto_resolves_per_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    a = ActivationSettings()
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert a.resolved_trigger() == "dbus"  # pynput is blind on Wayland
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert a.resolved_trigger() == "pynput"

    a.trigger = "dbus"  # explicit setting overrides detection
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert a.resolved_trigger() == "dbus"


async def test_toggle_outside_window_stops_normally() -> None:
    svc = _service(debounce_ms=10)  # tiny window so the sleep stays short

    await svc.toggle("pynput")
    assert svc.state == SessionState.RECORDING

    await asyncio.sleep(0.05)  # well past the window
    await svc.toggle("pynput")

    assert svc._adapter.open.call_count == 1
    assert svc._adapter.close.call_count == 1
    assert svc.state == SessionState.IDLE


async def test_third_press_can_start_a_new_session() -> None:
    """Debounce must not latch — normal start/stop/start still works."""
    svc = _service(debounce_ms=10)

    await svc.toggle("pynput")
    await asyncio.sleep(0.05)
    await svc.toggle("pynput")
    await asyncio.sleep(0.05)
    await svc.toggle("pynput")

    assert svc._adapter.open.call_count == 2
    assert svc.state == SessionState.RECORDING

    await svc._cancel_session()


async def test_push_to_hold_not_debounced() -> None:
    """push_start/push_stop bypass the guard so a short tap isn't swallowed."""
    svc = _service(debounce_ms=350)

    await svc.push_start()
    assert svc.state == SessionState.RECORDING

    await svc.push_stop()  # immediately after — must still stop
    assert svc._adapter.close.call_count == 1
    assert svc.state == SessionState.IDLE


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

"""MynaDBusService — DBus IPC between DictationService and Settings UI.

Well-known name : ``com.canonical.Myna``
Object path     : ``/com/canonical/Myna``
Interface       : ``com.canonical.Myna``

Methods : ``Toggle()``, ``GetState() → s``, ``Reload()``
Signal  : ``StateChanged(state: s)``

Runs in a background GLib event loop thread; dispatches async coroutines
to the asyncio event loop via :func:`asyncio.run_coroutine_threadsafe`.

Gracefully no-ops when ``dbus-python`` is not available (non-Linux / CI).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Callable

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from myna.orchestrator.dictation_service import DictationService

_WELL_KNOWN_NAME = "com.canonical.Myna"
_OBJECT_PATH = "/com/canonical/Myna"
_INTERFACE = "com.canonical.Myna"


class MynaDBusService:
    """Exposes DictationService over DBus.

    Args:
        service: Running :class:`~myna.orchestrator.dictation_service.DictationService`.
        loop: The main asyncio event loop.
    """

    def __init__(
        self,
        service: DictationService,
        loop: asyncio.AbstractEventLoop,
        on_reload: Callable[[], None] | None = None,
    ) -> None:
        self._service = service
        self._loop = loop
        self._on_reload = on_reload
        self._obj: object | None = None
        self._glib_thread: threading.Thread | None = None
        self._available = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Attempt to acquire the DBus well-known name in a GLib thread."""
        try:
            import dbus  # noqa: PLC0415
            import dbus.mainloop.glib  # noqa: PLC0415
            import dbus.service  # noqa: PLC0415
            from gi.repository import GLib  # noqa: PLC0415
        except ImportError:
            _LOG.info("dbus-python / gi not available — DBus service disabled")
            return

        service_ref = self._service
        loop_ref = self._loop
        on_reload_ref = self._on_reload

        class _Impl(dbus.service.Object):
            @dbus.service.method(_INTERFACE, out_signature="s")
            def GetState(self) -> str:  # noqa: N802
                return str(service_ref.state)

            @dbus.service.method(_INTERFACE)
            def Toggle(self) -> None:  # noqa: N802
                asyncio.run_coroutine_threadsafe(service_ref.toggle(), loop_ref)

            @dbus.service.method(_INTERFACE)
            def Reload(self) -> None:  # noqa: N802
                if on_reload_ref is not None:
                    on_reload_ref()

            @dbus.service.signal(_INTERFACE, signature="s")
            def StateChanged(self, state: str) -> None:  # noqa: N802
                pass

        def _run_glib() -> None:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            session_bus = dbus.SessionBus()
            bus_name = dbus.service.BusName(_WELL_KNOWN_NAME, session_bus)
            self._obj = _Impl(bus_name, _OBJECT_PATH)
            self._available = True
            _LOG.info("DBus service registered as %s", _WELL_KNOWN_NAME)
            GLib.MainLoop().run()

        self._glib_thread = threading.Thread(
            target=_run_glib, daemon=True, name="myna-dbus"
        )
        self._glib_thread.start()

    def stop(self) -> None:
        """Stop the GLib loop (daemon thread exits on process end)."""
        _LOG.debug("DBus service stopping")

    # ------------------------------------------------------------------
    # Called by DictationService on state changes
    # ------------------------------------------------------------------

    def emit_state_changed(self, state: str) -> None:
        """Emit ``StateChanged`` DBus signal if service is available."""
        if self._available and self._obj is not None:
            try:
                self._obj.StateChanged(state)  # type: ignore[union-attr]
            except Exception as exc:
                _LOG.debug("DBus signal failed: %s", exc)

"""Runtime facts for Settings → Overview. Plain Python, no Qt: main.py imports it even headless.

Everything here is a snapshot the UI reads on demand. Nothing polls, and nothing here can
block: the LM Studio probe is a plain function the Overview runs on a worker thread.

Privacy: the activity log holds metadata only (time, duration, outcome, character count).
Transcript text is never stored here.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
import urllib.error
import urllib.request
import weakref
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable
from urllib.parse import urlsplit

from budgie_voice.config.settings import LlmSettings

_LOG = logging.getLogger(__name__)

_MAX_ACTIVITY = 20
_PROBE_TIMEOUT_S = 3.0   # a status page must not hang: tighter than llm.timeout_s when that is larger
_PROBE_MAX_BYTES = 1 << 20


class Status(StrEnum):
    """Runtime state of one component, as shown in Settings → Overview."""

    READY = "Ready"
    CONNECTING = "Connecting"
    UNAVAILABLE = "Unavailable"
    ERROR = "Error"
    DISABLED = "Disabled"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class Activity:
    """One finished dictation. No text, ever."""

    when: datetime
    duration_s: float   # recording length (start → stop pressed, or cancelled)
    outcome: str        # "Completed" | "Cancelled" | "Failed"
    chars: int


@dataclass(frozen=True)
class AsrInfo:
    backend: str
    model: str
    ready: bool
    loading: bool       # a different model is being loaded right now
    target: str = ""    # "backend / model" being loaded (the config is not applied until it finishes)


_OUTCOME = {"completed": "Completed", "cancelled": "Cancelled", "error": "Failed"}


class RuntimeStatus:
    """What the running app tells the Settings window. Main thread only."""

    def __init__(self, verifier: object | None = None) -> None:
        self.verifier = verifier              # needs a read-only ``ready`` property
        self.activity: deque[Activity] = deque(maxlen=_MAX_ACTIVITY)
        self._asr: tuple[object, str, str] | None = None
        self._asr_loading = False
        self._asr_target = ""
        self._subs: list[Callable[[], Callable[[], None] | None]] = []
        self._started: float | None = None    # monotonic: recording began
        self._stopped: float | None = None    # monotonic: recording ended

    # -- subscription ---------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Call *callback* on every change.

        A bound method is held weakly: a subscriber that is simply dropped (a closed Settings
        window) is freed and pruned, with no unsubscribe step to forget. Anything else is held
        strongly until :meth:`unsubscribe`.
        """
        if inspect.ismethod(callback):
            self._subs.append(weakref.WeakMethod(callback))
        else:
            self._subs.append(lambda: callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        self._subs = [get for get in self._subs if get() != callback]

    def _emit(self) -> None:
        live = []
        for get in self._subs:
            cb = get()
            if cb is None:  # its owner is gone
                continue
            live.append(get)
            try:
                cb()
            except Exception:  # noqa: BLE001 — a dead window must never break the session
                _LOG.debug("status subscriber failed", exc_info=True)
        self._subs = live

    # -- speech recognition ---------------------------------------------------

    def set_asr(self, backend: object, name: str, model: str) -> None:
        """The backend now serving requests (call again after a swap)."""
        self._asr = (backend, name, model)
        self._emit()

    def set_asr_loading(self, loading: bool, target: str = "") -> None:
        """A model swap started (*target* = "backend / model") or ended."""
        self._asr_loading = loading
        self._asr_target = target if loading else ""
        self._emit()

    def asr_info(self) -> AsrInfo | None:
        """None until main reports a backend (e.g. Settings opened standalone)."""
        if self._asr is None:
            return None
        backend, name, model = self._asr
        return AsrInfo(
            name, model, bool(getattr(backend, "is_ready", False)), self._asr_loading, self._asr_target
        )

    def speaker_ready(self) -> bool | None:
        """None when no verifier was provided."""
        if self.verifier is None:
            return None
        return bool(getattr(self.verifier, "ready", False))

    # -- activity -------------------------------------------------------------

    def on_session_state(self, state: str, typed_chars: int) -> None:
        """Feed every SessionState change with ``DictationService.typed_chars``.

        That counter is reset when a session starts and counts only what was actually typed, so a
        cancelled preview or an empty dictation is 0. It is a number: no transcript text ever
        reaches this class.
        """
        try:
            self._record(state, typed_chars)
        except Exception:  # noqa: BLE001 — this runs inside the session's state-callback chain
            _LOG.debug("activity record failed", exc_info=True)

    def _record(self, state: str, typed_chars: int) -> None:
        now = time.monotonic()
        if state == "recording":
            self._started, self._stopped = now, None
            return
        if state == "finalizing":
            if self._started is not None and self._stopped is None:
                self._stopped = now
            return
        outcome = _OUTCOME.get(state)
        if outcome is None:  # idle / starting
            return
        duration = (self._stopped or now) - self._started if self._started is not None else 0.0
        self._started = self._stopped = None
        self.activity.append(Activity(datetime.now(), duration, outcome, max(0, typed_chars)))
        self._emit()


# -- LM Studio ----------------------------------------------------------------

def _where(url: str) -> str:
    """scheme://host:port only: never userinfo or path."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")


def probe_llm(cfg: LlmSettings) -> tuple[Status, str]:
    """Is the LLM server up, and does it list the configured model? Blocking: run off the UI thread.

    One ``GET {url}/models`` with the same rules as the real client: loopback only unless
    ``allow_remote``, no proxy, no redirects. Disabled sends nothing. Server reachability and
    model availability are reported separately.
    """
    if not cfg.enabled:
        return Status.DISABLED, "off (llm.enabled is false)"
    if not cfg.model:
        return Status.ERROR, "llm.model is not set"
    from budgie_voice.orchestrator.llm import _NoRedirect, _check_url  # noqa: PLC0415 — only when enabled

    try:
        _check_url(cfg.url, cfg.allow_remote)
        where = _where(cfg.url)
    except ValueError:
        return Status.ERROR, "llm.url is unusable (not http(s), does not resolve, or not on this machine)"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(cfg.url.rstrip("/") + "/models", timeout=min(cfg.timeout_s, _PROBE_TIMEOUT_S)) as resp:
            data = json.loads(resp.read(_PROBE_MAX_BYTES))
    except urllib.error.HTTPError as exc:  # before URLError: it is a subclass
        return Status.ERROR, f"{where} answered HTTP {exc.code}"
    except (OSError, TimeoutError):
        return Status.UNAVAILABLE, f"server not reachable at {where}"
    except ValueError:
        return Status.ERROR, f"{where} sent an unexpected response"
    listed = data.get("data") if isinstance(data, dict) else None
    if not isinstance(listed, list):
        return Status.ERROR, f"{where} sent an unexpected response"
    if any(isinstance(m, dict) and m.get("id") == cfg.model for m in listed):
        return Status.READY, f"{where} — model {cfg.model} available"
    return Status.UNAVAILABLE, f"{where} reachable, but model {cfg.model} is not listed"

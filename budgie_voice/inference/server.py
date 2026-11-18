"""Local FastAPI + WebSocket inference server.

Runs uvicorn in a daemon background thread so the main asyncio loop
stays free for orchestration.

Data Channel  : WebSocket ``/ws/transcribe``
  UP   → JSON-encoded AudioFrame messages (PCM base64), ``flush`` / ``reset``
  DOWN ← JSON-encoded TranscriptSegment messages, ``flushed``
  (see :mod:`budgie_voice.inference.protocol`)

Control Channel: HTTP GET ``/capabilities``
  DOWN ← JSON CapabilitiesResponse
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, AsyncIterator

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from budgie_voice.inference.backends.base import InferenceBackend

_LOG = logging.getLogger(__name__)


class InferenceServer:
    """Local WebSocket inference server wrapping any :class:`InferenceBackend`.

    Args:
        backend: Loaded :class:`~budgie_voice.inference.backends.base.InferenceBackend`.
        port: TCP port to bind (default 18000).
        min_turn_s: An end-of-speech hint closes a turn only once it holds this
            many seconds of audio (a flush always does).
    """

    def __init__(self, backend: InferenceBackend, port: int = 18000, min_turn_s: float = 15.0) -> None:
        self._backend = backend
        self._port = port
        self._min_turn_s = min_turn_s
        self._app = self._build_app()
        self._uvicorn: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start uvicorn in a daemon thread; block until server is ready.

        Raises:
            RuntimeError: Server fails to start within 15 seconds.
        """
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
            loop="none",  # we manage the loop ourselves
        )
        self._uvicorn = uvicorn.Server(config)
        server = self._uvicorn

        def _run() -> None:
            asyncio.run(server.serve())

        self._server_thread = threading.Thread(
            target=_run, daemon=True, name="budgie-voice-inference-server"
        )
        self._server_thread.start()

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if server.started:
                _LOG.info("InferenceServer running on 127.0.0.1:%d", self._port)
                return
            time.sleep(0.05)

        raise RuntimeError("InferenceServer did not start within 15 s")

    def stop(self) -> None:
        """Signal uvicorn to exit gracefully."""
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        _LOG.info("InferenceServer stopping")

    def swap_backend(self, new_backend: InferenceBackend) -> None:
        """Route new requests/turns to an already-loaded backend.

        Turns in flight keep the backend they started on; the old one is
        garbage-collected once they finish.
        """
        self._backend = new_backend

    async def capabilities(self):  # noqa: ANN201
        """Capabilities of the *current* backend (same swap rule as transcribe_stream)."""
        return await self._backend.capabilities()

    def transcribe_stream(self, audio_frames, language, hotwords=None):  # noqa: ANN001, ANN201
        """Delegate to the *current* backend, so a long-lived socket sees swaps.

        Lets ``_handle_ws`` take the server where it takes a backend: each turn
        looks the backend up when it starts, not when the socket connected.
        """
        return self._backend.transcribe_stream(audio_frames, language, hotwords)

    # ------------------------------------------------------------------
    # FastAPI app
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Budgie Inference Server", version="1.0")

        @app.get("/capabilities")
        async def capabilities() -> dict:
            caps = await self._backend.capabilities()
            return {
                "version": caps.version,
                "backends": caps.supported_models,
                "languages": caps.supported_languages,
                "models": caps.supported_models,
                "supports_streaming": caps.supports_streaming,
                "supports_gpu": caps.supports_gpu,
                "reports_confidence": caps.reports_confidence,
            }

        @app.websocket("/ws/transcribe")
        async def transcribe_ws(ws: WebSocket) -> None:
            await ws.accept()
            _LOG.debug("WebSocket client connected")
            try:
                await _handle_ws(ws, self, self._min_turn_s)  # type: ignore[arg-type]  # duck-typed backend
            except WebSocketDisconnect:
                _LOG.debug("WebSocket client disconnected")
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("WebSocket error: %s", exc)

        return app


async def _handle_ws(ws: WebSocket, backend: InferenceBackend, min_turn_s: float = 15.0) -> None:
    """Feed frames to the backend continuously per speech turn.

    Each yielded :class:`TranscriptSegment` is sent to the client as soon as
    the backend produces it — not buffered until the turn ends — so a
    streaming-capable backend can emit partial hypotheses (``is_final=False``)
    while the user is still talking. Batch backends (e.g. faster-whisper,
    which only sees the full turn once its async generator is exhausted)
    behave exactly as before: one send, at turn end.

    ``is_end_of_speech`` is only a hint. The turn closes on it once it holds
    ``min_turn_s`` of audio, so a short dictation is transcribed in one pass
    (at flush) instead of being cut at every pause; a ``flush`` always closes it.
    """
    language = "en"
    hotwords: str | None = None
    frame_queue: asyncio.Queue[np.ndarray | None] | None = None
    turn_task: asyncio.Task[None] | None = None
    turn_samples = 0
    turn_sample_rate = 16_000

    async def _frame_source(queue: asyncio.Queue[np.ndarray | None]) -> AsyncIterator[np.ndarray]:
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    async def _run_turn(queue: asyncio.Queue[np.ndarray | None], lang: str, hw: str | None) -> None:
        reports_confidence = (await backend.capabilities()).reports_confidence
        async for seg in backend.transcribe_stream(_frame_source(queue), lang, hw):
            await ws.send_text(
                json.dumps(
                    {
                        "type": "transcript_segment",
                        "text": seg.text,
                        "confidence": seg.confidence if reports_confidence else None,
                        "is_final": seg.is_final,
                        "language": seg.language,
                    }
                )
            )

    async def _close_turn() -> None:
        """End the open turn (if any) and wait for its transcription."""
        nonlocal frame_queue, turn_task, turn_samples
        if frame_queue is None or turn_task is None:
            return
        await frame_queue.put(None)  # close this turn's async generator
        try:
            await turn_task
        except Exception:  # noqa: BLE001 — a failed turn must not kill the connection
            _LOG.exception("Turn transcription failed")
        finally:
            frame_queue = None
            turn_task = None
            turn_samples = 0

    try:
        while True:
            raw = await ws.receive_text()
            msg: dict = json.loads(raw)
            kind = msg.get("type")

            if kind in ("flush", "reset"):
                if kind == "flush":
                    await _close_turn()
                elif turn_task is not None:
                    turn_task.cancel()
                    await asyncio.gather(turn_task, return_exceptions=True)
                    frame_queue = None
                    turn_task = None
                    turn_samples = 0
                await ws.send_text(json.dumps({"type": "flushed", "id": msg.get("id")}))
                continue

            if kind != "audio_frame":
                continue

            pcm_bytes = base64.b64decode(msg["data"])
            fmt = msg.get("format", "pcm16")
            if fmt == "pcm16":
                audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
            else:
                audio = np.frombuffer(pcm_bytes, dtype=np.float32)

            language = msg.get("language", language)
            hotwords = msg.get("hotwords", hotwords)

            if frame_queue is None:
                frame_queue = asyncio.Queue()
                turn_task = asyncio.create_task(_run_turn(frame_queue, language, hotwords))
            await frame_queue.put(audio)
            turn_samples += len(audio)
            turn_sample_rate = msg.get("sample_rate", turn_sample_rate)

            if msg.get("is_end_of_speech") and turn_samples >= min_turn_s * turn_sample_rate:
                await _close_turn()
    finally:
        if turn_task is not None:
            turn_task.cancel()

"""Local FastAPI + WebSocket inference server.

Runs uvicorn in a daemon background thread so the main asyncio loop
stays free for orchestration.

Data Channel  : WebSocket ``/ws/transcribe``
  UP   → JSON-encoded AudioFrame messages (PCM base64)
  DOWN ← JSON-encoded TranscriptSegment messages

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
from typing import TYPE_CHECKING

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from myna.inference.protocol import TranscriptSegment

if TYPE_CHECKING:
    from myna.inference.backends.base import InferenceBackend

_LOG = logging.getLogger(__name__)


class InferenceServer:
    """Local WebSocket inference server wrapping any :class:`InferenceBackend`.

    Args:
        backend: Loaded :class:`~myna.inference.backends.base.InferenceBackend`.
        port: TCP port to bind (default 18000).
    """

    def __init__(self, backend: InferenceBackend, port: int = 18000) -> None:
        self._backend = backend
        self._port = port
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
            target=_run, daemon=True, name="myna-inference-server"
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

    async def reload_backend(self, model: str, device: str, compute_type: str) -> None:
        """Hot-swap the backend model without restarting the server.

        In-flight transcriptions continue on the old model object; the new
        model is assigned atomically when loading completes.
        """
        _LOG.info("Hot-reloading backend: %s (%s / %s)", model, device, compute_type)
        await self._backend.load(model, device, compute_type)
        _LOG.info("Backend hot-reload complete: %s", model)

    # ------------------------------------------------------------------
    # FastAPI app
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Myna Inference Server", version="1.0")
        backend = self._backend

        @app.get("/capabilities")
        async def capabilities() -> dict:
            caps = await backend.capabilities()
            return {
                "version": caps.version,
                "backends": caps.supported_models,
                "languages": caps.supported_languages,
                "models": caps.supported_models,
                "supports_streaming": caps.supports_streaming,
                "supports_gpu": caps.supports_gpu,
            }

        @app.websocket("/ws/transcribe")
        async def transcribe_ws(ws: WebSocket) -> None:
            await ws.accept()
            _LOG.debug("WebSocket client connected")
            try:
                await _handle_ws(ws, backend)
            except WebSocketDisconnect:
                _LOG.debug("WebSocket client disconnected")
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("WebSocket error: %s", exc)

        return app


async def _handle_ws(ws: WebSocket, backend: InferenceBackend) -> None:
    """Accumulate per-speech-turn audio, transcribe, reply with segments."""
    buffer: list[np.ndarray] = []
    language = "en"

    while True:
        raw = await ws.receive_text()
        msg: dict = json.loads(raw)

        if msg.get("type") != "audio_frame":
            continue

        pcm_bytes = base64.b64decode(msg["data"])
        fmt = msg.get("format", "pcm16")
        if fmt == "pcm16":
            audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        else:
            audio = np.frombuffer(pcm_bytes, dtype=np.float32)

        language = msg.get("language", language)
        buffer.append(audio)

        if msg.get("is_end_of_speech") and buffer:
            combined = np.concatenate(buffer)
            buffer.clear()
            for seg in await _transcribe(backend, combined, language):
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "transcript_segment",
                            "text": seg.text,
                            "confidence": seg.confidence,
                            "is_final": seg.is_final,
                            "language": seg.language,
                            "segment_id": seg.segment_id,
                        }
                    )
                )


async def _transcribe(
    backend: InferenceBackend, audio: np.ndarray, language: str
) -> list[TranscriptSegment]:
    """Run backend.transcribe_stream on a single audio array."""

    async def _one() -> object:
        yield audio

    results: list[TranscriptSegment] = []
    async for seg in backend.transcribe_stream(_one(), language):  # type: ignore[arg-type]
        results.append(seg)
    return results

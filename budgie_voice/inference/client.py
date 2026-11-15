"""Async WebSocket client for the Budgie InferenceServer.

Data Channel: WebSocket /ws/transcribe → send AudioFrames, receive TranscriptSegments
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator

from websockets.asyncio.client import connect as _ws_connect

from budgie_voice.inference.protocol import AudioFrame, TranscriptSegment

_LOG = logging.getLogger(__name__)


class InferenceClient:
    """Async client for the local InferenceServer.

    Args:
        host: Server host (default ``127.0.0.1``).
        port: Server port (default 18000).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18_000) -> None:
        self._ws_url = f"ws://{host}:{port}/ws/transcribe"
        self._ws: object | None = None
        self._language: str = "en"
        self._hotwords: str | None = None
        self._next_id = 0
        self._flush_waiters: dict[int, asyncio.Future[None]] = {}
        # id of an unacknowledged reset; segments are dropped until it is acked
        self._reset_id: int | None = None

    def set_language(self, language: str) -> None:
        """Language sent with every frame; takes effect from the next one."""
        self._language = language

    def set_hotwords(self, hotwords: str | None) -> None:
        """Set personal-vocabulary hotwords sent with each frame."""
        self._hotwords = hotwords

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, language: str = "en") -> None:
        """Open WebSocket Data Channel.

        Args:
            language: Default BCP-47 language tag sent with each frame.

        Raises:
            RuntimeError: Connection fails.
        """
        self._language = language
        self._reset_id = None  # a new socket has no stale turn to wait out
        try:
            self._ws = await _ws_connect(self._ws_url)
            _LOG.info("InferenceClient connected → %s", self._ws_url)
        except Exception as exc:
            raise RuntimeError(f"Cannot connect to inference server: {exc}") from exc

    async def disconnect(self) -> None:
        """Close the WebSocket Data Channel."""
        if self._ws is not None:
            await self._ws.close()  # type: ignore[union-attr]
            self._ws = None
            _LOG.info("InferenceClient disconnected")

    # ------------------------------------------------------------------
    # Data Channel
    # ------------------------------------------------------------------

    async def send_frame(self, frame: AudioFrame) -> None:
        """Encode and send an AudioFrame over the Data Channel.

        Args:
            frame: Audio chunk to transmit.

        Raises:
            RuntimeError: Not connected.
        """
        if self._ws is None:
            raise RuntimeError("InferenceClient: not connected — call connect() first")

        msg = {
            "type": "audio_frame",
            "data": base64.b64encode(frame.data).decode(),
            "sample_rate": frame.sample_rate,
            "channels": frame.channels,
            "format": frame.format,
            "timestamp_ms": frame.timestamp_ms,
            "is_end_of_speech": frame.is_end_of_speech,
            "language": self._language,
            "hotwords": self._hotwords,
        }
        await self._ws.send(json.dumps(msg))  # type: ignore[union-attr]

    async def flush(self, timeout: float) -> None:
        """Close the open turn and wait until all of its segments were received.

        Segments are delivered through :meth:`receive_segments`, which must be
        running; this returns once the server's acknowledgement came through it.

        Raises:
            TimeoutError: No acknowledgement within *timeout* seconds.
            RuntimeError: Not connected.
        """
        msg_id = self._new_id()
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._flush_waiters[msg_id] = waiter
        try:
            await self._send_control("flush", msg_id)
            await asyncio.wait_for(waiter, timeout)
        finally:
            self._flush_waiters.pop(msg_id, None)

    async def reset(self) -> None:
        """Discard the open turn. Does not wait.

        Every segment arriving before the server acknowledges is dropped, so a
        turn that was already being transcribed cannot leak into a later session.
        """
        msg_id = self._new_id()
        self._reset_id = msg_id
        await self._send_control("reset", msg_id)

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _send_control(self, kind: str, msg_id: int) -> None:
        if self._ws is None:
            raise RuntimeError("InferenceClient: not connected — call connect() first")
        await self._ws.send(json.dumps({"type": kind, "id": msg_id}))  # type: ignore[union-attr]

    async def receive_segments(self) -> AsyncIterator[TranscriptSegment]:
        """Yield TranscriptSegments as the server commits them.

        Yields:
            One :class:`~budgie_voice.inference.protocol.TranscriptSegment` per committed segment.

        Raises:
            RuntimeError: Not connected.
        """
        if self._ws is None:
            raise RuntimeError("InferenceClient: not connected")

        async for raw in self._ws:  # type: ignore[union-attr]
            try:
                msg: dict = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "flushed":
                if msg.get("id") == self._reset_id:
                    self._reset_id = None
                waiter = self._flush_waiters.get(msg.get("id"))
                if waiter is not None and not waiter.done():
                    waiter.set_result(None)
            elif kind == "transcript_segment" and self._reset_id is None:
                yield TranscriptSegment(
                    text=msg["text"],
                    confidence=msg.get("confidence"),
                    is_final=msg["is_final"],
                    language=msg["language"],
                )

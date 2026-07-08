"""Async WebSocket client for the Myna InferenceServer.

Control Channel: HTTP GET /capabilities   → CapabilitiesResponse
Data Channel   : WebSocket /ws/transcribe → send AudioFrames, receive TranscriptSegments
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator
from urllib.request import urlopen

from websockets.asyncio.client import connect as _ws_connect

from myna.inference.protocol import AudioFrame, CapabilitiesResponse, TranscriptSegment
from myna.utils.exceptions import MynaInferenceError

_LOG = logging.getLogger(__name__)


class InferenceClient:
    """Async client for the local InferenceServer.

    Args:
        host: Server host (default ``127.0.0.1``).
        port: Server port (default 18000).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18_000) -> None:
        self._base_url = f"http://{host}:{port}"
        self._ws_url = f"ws://{host}:{port}/ws/transcribe"
        self._ws: object | None = None
        self._language: str = "en"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, language: str = "en") -> None:
        """Open WebSocket Data Channel.

        Args:
            language: Default BCP-47 language tag sent with each frame.

        Raises:
            MynaInferenceError: Connection fails.
        """
        self._language = language
        try:
            self._ws = await _ws_connect(self._ws_url)
            _LOG.info("InferenceClient connected → %s", self._ws_url)
        except Exception as exc:
            raise MynaInferenceError(f"Cannot connect to inference server: {exc}") from exc

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
            MynaInferenceError: Not connected.
        """
        if self._ws is None:
            raise MynaInferenceError("InferenceClient: not connected — call connect() first")

        msg = {
            "type": "audio_frame",
            "data": base64.b64encode(frame.data).decode(),
            "sample_rate": frame.sample_rate,
            "channels": frame.channels,
            "format": frame.format,
            "timestamp_ms": frame.timestamp_ms,
            "frame_id": frame.frame_id,
            "is_end_of_speech": frame.is_end_of_speech,
            "language": self._language,
        }
        await self._ws.send(json.dumps(msg))  # type: ignore[union-attr]

    async def receive_segments(self) -> AsyncIterator[TranscriptSegment]:
        """Yield TranscriptSegments as the server commits them.

        Yields:
            One :class:`~myna.inference.protocol.TranscriptSegment` per committed segment.

        Raises:
            MynaInferenceError: Not connected.
        """
        if self._ws is None:
            raise MynaInferenceError("InferenceClient: not connected")

        async for raw in self._ws:  # type: ignore[union-attr]
            try:
                msg: dict = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "transcript_segment":
                yield TranscriptSegment(
                    text=msg["text"],
                    confidence=msg["confidence"],
                    is_final=msg["is_final"],
                    language=msg["language"],
                    segment_id=msg["segment_id"],
                )

    # ------------------------------------------------------------------
    # Control Channel
    # ------------------------------------------------------------------

    async def get_capabilities(self) -> CapabilitiesResponse:
        """Query the Control Channel for server capabilities.

        Returns:
            :class:`~myna.inference.protocol.CapabilitiesResponse` from the server.

        Raises:
            MynaInferenceError: HTTP request fails.
        """
        url = f"{self._base_url}/capabilities"
        loop = asyncio.get_running_loop()
        try:
            data: dict = await loop.run_in_executor(None, _fetch_json, url)
        except Exception as exc:
            raise MynaInferenceError(f"Capabilities request failed: {exc}") from exc
        return CapabilitiesResponse(
            version=data.get("version", "1.0"),
            backends=data.get("backends", []),
            languages=data.get("languages", []),
            models=data.get("models", []),
        )


def _fetch_json(url: str) -> dict:
    """Synchronous JSON fetch — runs in executor."""
    with urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read())

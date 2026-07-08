"""SpeakerVerifier — ECAPA-TDNN cosine-similarity speaker verification.

Uses SpeechBrain ``speechbrain/spkrec-ecapa-voxceleb`` to produce
192-dimensional speaker embeddings.

* If the voiceprint file does not exist, verification is disabled and
  :meth:`verify` always returns ``(True, 1.0)`` (passthrough).
* The voiceprint is a normalised mean ECAPA embedding saved with
  :func:`torch.save` by :mod:`myna.speaker.enroller`.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch

from myna.utils.exceptions import MynaSpeakerError

_LOG = logging.getLogger(__name__)

_HUB_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
_SAMPLE_RATE = 16_000


class SpeakerVerifier:
    """Wraps SpeechBrain ECAPA-TDNN for speaker identity verification.

    Args:
        voiceprint_path: Path to ``voiceprint.pt`` saved by the enroller.
            Verification disabled (passthrough) if the file is absent.
        threshold: Cosine similarity threshold in [0, 1].
            Scores above this value are accepted as the enrolled speaker.
    """

    def __init__(
        self,
        voiceprint_path: Path,
        threshold: float = 0.75,
    ) -> None:
        self._voiceprint_path = voiceprint_path
        self._threshold = threshold
        self._model: object | None = None
        self._voiceprint: np.ndarray | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load model and voiceprint.  Call once at startup (blocking)."""
        if not self._voiceprint_path.exists():
            _LOG.info("Voiceprint not found at %s — speaker verify disabled", self._voiceprint_path)
            return

        try:
            from speechbrain.inference.speaker import EncoderClassifier  # noqa: PLC0415

            self._model = EncoderClassifier.from_hparams(
                source=_HUB_SOURCE,
                run_opts={"device": "cpu"},
                savedir=str(Path.home() / ".cache" / "myna" / "speechbrain"),
            )
            vp_tensor: torch.Tensor = torch.load(
                self._voiceprint_path, map_location="cpu", weights_only=True
            )
            self._voiceprint = vp_tensor.numpy()
            _LOG.info("SpeakerVerifier loaded (threshold=%.2f)", self._threshold)
        except Exception as exc:
            raise MynaSpeakerError(f"Failed to load speaker model: {exc}") from exc

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def get_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Compute a normalised ECAPA-TDNN speaker embedding.

        Args:
            audio: 1-D float32 array at 16 kHz.

        Returns:
            1-D float32 normalised embedding vector.

        Raises:
            MynaSpeakerError: Model not loaded.
        """
        if self._model is None:
            raise MynaSpeakerError("SpeakerVerifier not loaded")

        tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            emb = self._model.encode_batch(tensor)  # type: ignore[union-attr]
        vec = emb.squeeze().cpu().numpy().astype(np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def verify(self, audio: np.ndarray) -> tuple[bool, float]:
        """Verify whether *audio* belongs to the enrolled speaker.

        Args:
            audio: 1-D float32 array at 16 kHz.

        Returns:
            ``(is_enrolled_speaker, cosine_similarity_score)``
            Returns ``(True, 1.0)`` when verification is disabled.
        """
        if self._model is None or self._voiceprint is None:
            return True, 1.0  # passthrough — no voiceprint enrolled

        if len(audio) < _SAMPLE_RATE * 0.5:
            _LOG.debug("Audio too short for verification — passthrough")
            return True, 1.0

        try:
            embedding = self.get_embedding(audio)
            score = float(np.dot(embedding, self._voiceprint))
            return score >= self._threshold, score
        except Exception as exc:
            _LOG.warning("Speaker verification error: %s — passthrough", exc)
            return True, 1.0

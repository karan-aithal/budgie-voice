"""ECAPA-TDNN speaker verification via SpeechBrain.

Compares the embedding of an audio segment against a stored voiceprint
using cosine similarity.  Loading is lazy so the model is not downloaded
unless the voiceprint file actually exists.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F

from assistant.utils.exceptions import SpeakerVerificationError

logger = logging.getLogger(__name__)

_DEFAULT_SAVEDIR = Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa-voxceleb"
_MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"


class SpeakerVerifier:
    """Verifies whether audio was produced by the enrolled speaker.

    Usage::

        verifier = SpeakerVerifier(voiceprint_path="~/.config/voice-assistant/voiceprint.pt")
        verifier.load()          # call once from a thread pool
        is_me, score = verifier.verify(audio_np)

    **Disabled mode**: if *voiceprint_path* does not exist, :meth:`verify`
    always returns ``(True, 1.0)`` so the pipeline continues normally and
    the user can run without enrolling.

    Args:
        voiceprint_path: Path to a ``torch.save``-ed 1-D embedding tensor.
        threshold: Cosine similarity threshold ∈ [0, 1].  Scores ≥ threshold
            are accepted as the enrolled speaker.
        savedir: Directory for SpeechBrain model cache files.  Defaults to
            ``~/.cache/speechbrain/spkrec-ecapa-voxceleb``.
    """

    def __init__(
        self,
        voiceprint_path: str | Path,
        threshold: float = 0.75,
        savedir: str | Path | None = None,
    ) -> None:
        self._voiceprint_path = Path(voiceprint_path).expanduser()
        self._threshold = threshold
        self._savedir = str(savedir or _DEFAULT_SAVEDIR)
        self._model: Optional[object] = None          # EncoderClassifier
        self._voiceprint: Optional[torch.Tensor] = None

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Download and load ECAPA-TDNN from SpeechBrain hub (blocking).

        Raises:
            SpeakerVerificationError: If SpeechBrain is not installed or the
                model cannot be downloaded / initialised.
        """
        try:
            from speechbrain.inference.speaker import EncoderClassifier  # noqa: PLC0415
        except ImportError as exc:
            raise SpeakerVerificationError(
                "SpeechBrain not installed — run: pip install speechbrain"
            ) from exc

        try:
            self._model = EncoderClassifier.from_hparams(
                source=_MODEL_SOURCE,
                savedir=self._savedir,
                run_opts={"device": "cpu"},
            )
            logger.info("ECAPA-TDNN loaded from %s", _MODEL_SOURCE)
        except Exception as exc:
            raise SpeakerVerificationError(
                f"Failed to load ECAPA-TDNN: {exc}"
            ) from exc

    def _load_voiceprint(self) -> None:
        """Load the saved voiceprint tensor from disk.

        Raises:
            SpeakerVerificationError: If the file cannot be read.
        """
        try:
            self._voiceprint = torch.load(
                self._voiceprint_path,
                map_location="cpu",
                weights_only=True,
            )
            logger.info(
                "Voiceprint loaded — shape=%s  path=%s",
                tuple(self._voiceprint.shape),
                self._voiceprint_path,
            )
        except Exception as exc:
            raise SpeakerVerificationError(
                f"Failed to load voiceprint {self._voiceprint_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load model and voiceprint (blocking — run in thread pool).

        If *voiceprint_path* does not exist, logs a warning and returns
        without loading anything (disabled mode).
        """
        if not self._voiceprint_path.exists():
            logger.warning(
                "Voiceprint not found at %s — speaker verification disabled",
                self._voiceprint_path,
            )
            return
        self._load_model()
        self._load_voiceprint()

    def get_embedding(self, audio: np.ndarray) -> torch.Tensor:
        """Extract a speaker embedding from a numpy audio array.

        Args:
            audio: Float32 mono numpy array at 16 kHz.

        Returns:
            1-D embedding tensor of shape ``[embedding_dim]``.

        Raises:
            SpeakerVerificationError: If :meth:`load` was not called.
        """
        if self._model is None:
            raise SpeakerVerificationError(
                "Model not loaded — call load() first"
            )
        signal = torch.from_numpy(audio).unsqueeze(0)  # [1, samples]
        with torch.no_grad():
            embeddings = self._model.encode_batch(signal)  # type: ignore[union-attr]
        return embeddings.squeeze()  # [embedding_dim]

    def verify(self, audio: np.ndarray) -> tuple[bool, float]:
        """Test whether *audio* matches the enrolled voiceprint.

        Args:
            audio: Float32 mono numpy array at 16 kHz.

        Returns:
            ``(is_me, cosine_similarity_score)`` where *is_me* is ``True``
            when score ≥ threshold.  Returns ``(True, 1.0)`` in disabled mode.
        """
        if self._voiceprint is None:
            return True, 1.0

        embedding = self.get_embedding(audio)
        score = float(
            F.cosine_similarity(
                embedding.unsqueeze(0),
                self._voiceprint.unsqueeze(0),
            ).item()
        )
        is_me = score >= self._threshold
        logger.debug(
            "Speaker verify — score=%.4f  threshold=%.2f  is_me=%s",
            score,
            self._threshold,
            is_me,
        )
        return is_me, score

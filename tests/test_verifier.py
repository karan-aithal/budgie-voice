"""Tests for speaker/verifier.py — Phase 3."""

import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

from assistant.speaker.verifier import SpeakerVerifier
from assistant.utils.exceptions import SpeakerVerificationError


# ---------------------------------------------------------------------------
# Disabled mode (no voiceprint file)
# ---------------------------------------------------------------------------


def test_verify_disabled_when_no_voiceprint(tmp_path: Path) -> None:
    """Without a voiceprint file, verify() must return (True, 1.0)."""
    verifier = SpeakerVerifier(
        voiceprint_path=tmp_path / "voiceprint.pt",
        threshold=0.75,
    )
    verifier.load()  # file absent → disabled mode, no model loaded

    is_me, score = verifier.verify(np.zeros(16_000, dtype=np.float32))
    assert is_me is True
    assert score == pytest.approx(1.0)


def test_load_does_not_raise_when_no_voiceprint(tmp_path: Path) -> None:
    """load() must succeed silently when voiceprint is absent."""
    verifier = SpeakerVerifier(voiceprint_path=tmp_path / "missing.pt")
    verifier.load()  # should not raise


# ---------------------------------------------------------------------------
# Enabled mode (voiceprint present, mocked model)
# ---------------------------------------------------------------------------


def _make_verifier_with_voiceprint(tmp_path: Path, threshold: float = 0.75):
    """Helper: creates a SpeakerVerifier with a saved unit-vector voiceprint."""
    dim = 192
    voiceprint = torch.ones(dim) / math.sqrt(dim)  # unit vector
    vp_path = tmp_path / "voiceprint.pt"
    torch.save(voiceprint, vp_path)

    verifier = SpeakerVerifier(voiceprint_path=vp_path, threshold=threshold)
    verifier._voiceprint = voiceprint  # bypass load()
    return verifier, voiceprint


def test_verify_identical_embedding_returns_high_score(tmp_path: Path) -> None:
    """Embedding identical to voiceprint → cosine similarity = 1.0 → is_me=True."""
    verifier, voiceprint = _make_verifier_with_voiceprint(tmp_path)

    mock_model = MagicMock()
    # encode_batch returns [1, 1, dim] shaped tensor
    mock_model.encode_batch.return_value = voiceprint.reshape(1, 1, -1)
    verifier._model = mock_model

    is_me, score = verifier.verify(np.zeros(16_000, dtype=np.float32))

    assert is_me is True
    assert score == pytest.approx(1.0, abs=1e-4)


def test_verify_orthogonal_embedding_returns_false(tmp_path: Path) -> None:
    """Orthogonal embedding → cosine similarity ≈ 0 → is_me=False."""
    dim = 192
    verifier, voiceprint = _make_verifier_with_voiceprint(tmp_path, threshold=0.75)

    # Build vector orthogonal to voiceprint
    orthogonal = torch.zeros(dim)
    orthogonal[1] = 1.0  # voiceprint is all-ones-normalised, this is orthogonal

    mock_model = MagicMock()
    mock_model.encode_batch.return_value = orthogonal.reshape(1, 1, -1)
    verifier._model = mock_model

    is_me, score = verifier.verify(np.zeros(16_000, dtype=np.float32))

    assert is_me is False
    assert score < 0.75


def test_verify_below_threshold_returns_false(tmp_path: Path) -> None:
    """Score just below threshold → is_me=False."""
    verifier, voiceprint = _make_verifier_with_voiceprint(tmp_path, threshold=0.80)

    # Embedding that produces similarity of ~0.5 (lower than threshold)
    low_sim = torch.zeros(192)
    low_sim[0] = 1.0  # perpendicular direction

    mock_model = MagicMock()
    mock_model.encode_batch.return_value = low_sim.reshape(1, 1, -1)
    verifier._model = mock_model

    is_me, score = verifier.verify(np.zeros(16_000, dtype=np.float32))

    assert is_me is False
    assert score < 0.80


def test_get_embedding_without_load_raises() -> None:
    """get_embedding() before load() must raise SpeakerVerificationError."""
    verifier = SpeakerVerifier(voiceprint_path="/nonexistent/path.pt")
    with pytest.raises(SpeakerVerificationError, match="not loaded"):
        verifier.get_embedding(np.zeros(16_000, dtype=np.float32))


def test_voiceprint_persists_after_save(tmp_path: Path) -> None:
    """A tensor saved with torch.save can be reloaded correctly."""
    dim = 192
    original = torch.randn(dim)
    path = tmp_path / "vp.pt"
    torch.save(original, path)
    loaded = torch.load(path, map_location="cpu", weights_only=True)
    assert torch.allclose(original, loaded)


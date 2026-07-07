"""Tests for speech/recognizer.py — Phase 1 happy path."""

import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from assistant.speech.recognizer import TranscriptResult, WhisperRecognizer


def test_transcript_result_is_dataclass() -> None:
    """TranscriptResult must hold text, confidence, language."""
    r = TranscriptResult(text="hello world", confidence=0.82, language="en")
    assert r.text == "hello world"
    assert r.confidence == pytest.approx(0.82)
    assert r.language == "en"


def test_transcribe_returns_result_for_valid_segments() -> None:
    """transcribe() with a mocked model should produce TranscriptResult."""
    recognizer = WhisperRecognizer(model_size="tiny", device="cpu")

    seg = MagicMock()
    seg.text = "  hello world  "
    seg.avg_logprob = -0.2  # exp(-0.2) ≈ 0.8187

    info = MagicMock()
    info.language = "en"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([seg], info)
    recognizer._model = mock_model

    audio = np.zeros(16_000, dtype=np.float32)
    result = recognizer.transcribe(audio)

    assert result is not None
    assert result.text == "hello world"
    assert result.language == "en"
    assert result.confidence == pytest.approx(math.exp(-0.2), abs=1e-4)


def test_transcribe_multiple_segments_averages_logprob() -> None:
    """Mean avg_logprob across segments must drive confidence."""
    recognizer = WhisperRecognizer()

    seg1 = MagicMock()
    seg1.text = "Hello"
    seg1.avg_logprob = -0.4

    seg2 = MagicMock()
    seg2.text = "world"
    seg2.avg_logprob = -0.6

    info = MagicMock()
    info.language = "en"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([seg1, seg2], info)
    recognizer._model = mock_model

    result = recognizer.transcribe(np.zeros(16_000, dtype=np.float32))

    assert result is not None
    expected_conf = round(math.exp((-0.4 + -0.6) / 2), 4)
    assert result.confidence == pytest.approx(expected_conf, abs=1e-4)
    assert result.text == "Hello world"


def test_transcribe_empty_segments_returns_none() -> None:
    """Empty segment list must return None (no speech)."""
    recognizer = WhisperRecognizer()

    info = MagicMock()
    info.language = "en"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], info)
    recognizer._model = mock_model

    result = recognizer.transcribe(np.zeros(16_000, dtype=np.float32))
    assert result is None


def test_transcribe_whitespace_only_returns_none() -> None:
    """Segments containing only whitespace must return None."""
    recognizer = WhisperRecognizer()

    seg = MagicMock()
    seg.text = "   "
    seg.avg_logprob = -0.1

    info = MagicMock()
    info.language = "en"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([seg], info)
    recognizer._model = mock_model

    result = recognizer.transcribe(np.zeros(16_000, dtype=np.float32))
    assert result is None


def test_transcribe_without_load_raises() -> None:
    """Calling transcribe() before load() must raise RecognizerError."""
    from assistant.utils.exceptions import RecognizerError

    recognizer = WhisperRecognizer()
    with pytest.raises(RecognizerError, match="not loaded"):
        recognizer.transcribe(np.zeros(16_000, dtype=np.float32))


def test_resolve_device_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """'auto' must resolve to 'cpu' when torch reports no CUDA."""
    import sys
    import types

    # Stub torch with cuda.is_available() → False
    fake_torch = types.ModuleType("torch")
    fake_cuda = types.SimpleNamespace(is_available=lambda: False)
    fake_torch.cuda = fake_cuda  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    device = WhisperRecognizer._resolve_device("auto")
    assert device == "cpu"

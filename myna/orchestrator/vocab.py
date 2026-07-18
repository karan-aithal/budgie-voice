"""PersonalVocab — learned word frequencies that bias inference decoding.

Counts words from successfully injected text and exposes the most frequent
ones as a hotwords string for the inference backend. Stores only word→count
pairs (never full transcripts) at ``~/.config/myna/vocab.json``.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

_LOG = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{4,}")  # 5+ chars; skips stopword noise
_MAX_WORDS = 500          # cap stored vocab
_HOTWORDS_TOP_N = 50      # words handed to the decoder


class PersonalVocab:
    """Persistent word-frequency store feeding decoder hotwords.

    Args:
        path: JSON file of ``{word: count}``. Created on first save.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._counts: Counter[str] = Counter()
        try:
            if path.exists():
                self._counts = Counter(json.loads(path.read_text()))
                _LOG.info("PersonalVocab loaded (%d words)", len(self._counts))
        except Exception as exc:
            _LOG.warning("PersonalVocab load failed: %s — starting empty", exc)

    def update(self, text: str) -> None:
        """Count words from injected *text* and persist."""
        words = [w.lower() for w in _WORD_RE.findall(text)]
        if not words:
            return
        self._counts.update(words)
        if len(self._counts) > _MAX_WORDS:
            self._counts = Counter(dict(self._counts.most_common(_MAX_WORDS)))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(dict(self._counts)))
        except Exception as exc:
            _LOG.warning("PersonalVocab save failed: %s", exc)

    def hotwords(self) -> str | None:
        """Space-joined top words for the decoder, or None if empty.

        Only words seen 2+ times qualify — a single occurrence may be a
        misrecognition, and reinforcing it would train the error in.
        """
        top = [w for w, c in self._counts.most_common(_HOTWORDS_TOP_N) if c >= 2]
        return " ".join(top) if top else None

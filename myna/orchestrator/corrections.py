"""CorrectionMemory — learns literal mishear→fix word pairs from preview edits.

Diffs what the ASR heard against what the user typed in the preview box,
extracts single-word substitutions, and once the same fix has been made
twice, auto-applies it to future ASR output before it's even shown for
review. Stores word pairs only (never full transcripts) at
``~/.config/myna/corrections.json``.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

_LOG = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z']+")
_APPLY_AFTER = 2   # same correction seen this many times before auto-applying
_MAX_KEYS = 300


class CorrectionMemory:
    """Persistent heard-word → corrected-word map, learned from preview edits.

    Args:
        path: JSON file of ``{heard_word: {corrected_word: count}}``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._map: dict[str, Counter[str]] = defaultdict(Counter)
        try:
            if path.exists():
                raw = json.loads(path.read_text())
                for heard, candidates in raw.items():
                    self._map[heard] = Counter(candidates)
                _LOG.info("CorrectionMemory loaded (%d words)", len(self._map))
        except Exception as exc:
            _LOG.warning("CorrectionMemory load failed: %s — starting empty", exc)

    def learn(self, heard: str, corrected: str) -> None:
        """Diff *heard* vs *corrected* (post-preview-edit) and record single-word fixes."""
        heard_words = _WORD_RE.findall(heard)
        corrected_words = _WORD_RE.findall(corrected)
        matcher = SequenceMatcher(a=[w.lower() for w in heard_words], b=[w.lower() for w in corrected_words])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace" or (i2 - i1) != (j2 - j1):
                continue  # skip phrasing/word-count changes — literal mishears only
            for i, j in zip(range(i1, i2), range(j1, j2)):
                heard_w = heard_words[i].lower()
                fix_w = corrected_words[j]
                if heard_w == fix_w.lower():
                    continue
                self._map[heard_w][fix_w] += 1

        if len(self._map) > _MAX_KEYS:
            for k in list(self._map)[: len(self._map) - _MAX_KEYS]:
                del self._map[k]
        self._save()

    def targets(self) -> list[str]:
        """Every corrected spelling the user has typed in the preview (confirmed by them)."""
        return [fix for fixes in self._map.values() for fix in fixes]

    def apply(self, text: str) -> str:
        """Substitute words with a fix seen >= _APPLY_AFTER times."""
        def _sub(m: re.Match) -> str:
            word = m.group(0)
            candidates = self._map.get(word.lower())
            if not candidates:
                return word
            fix, count = candidates.most_common(1)[0]
            return fix if count >= _APPLY_AFTER else word

        return _WORD_RE.sub(_sub, text)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({k: dict(v) for k, v in self._map.items()}))
        except Exception as exc:
            _LOG.warning("CorrectionMemory save failed: %s", exc)

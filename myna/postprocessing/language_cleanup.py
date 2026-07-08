"""LanguageCleanup — en-specific spoken-language normalisation.

Transformations (all optional via config):

* Informal contractions: ``"gonna"`` → ``"going to"``, ``"wanna"`` →
  ``"want to"``, ``"gotta"`` → ``"got to"``, etc.
* Filler word stripping: ``"um"``, ``"uh"``, ``"er"``, ``"hmm"``, ``"ah"``.

All substitutions are word-boundary safe (``\\b`` anchored).
"""
from __future__ import annotations

import logging
import re

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Contraction map
# ---------------------------------------------------------------------------

_CONTRACTIONS: dict[str, str] = {
    "gonna":  "going to",
    "wanna":  "want to",
    "gotta":  "got to",
    "kinda":  "kind of",
    "sorta":  "sort of",
    "dunno":  "don't know",
    "lemme":  "let me",
    "gimme":  "give me",
    "hafta":  "have to",
    "oughta": "ought to",
    "lotta":  "lot of",
    "outta":  "out of",
    "lotsa":  "lots of",
}

_CONTRACTION_PATS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE), v)
    for k, v in _CONTRACTIONS.items()
]

# ---------------------------------------------------------------------------
# Filler words
# ---------------------------------------------------------------------------

_FILLERS = ["um", "uh", "er", "hmm", "hm", "ah", "eh"]
_FILLER_PAT = re.compile(
    r"\b(?:" + "|".join(map(re.escape, _FILLERS)) + r")\b[,\s]*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class LanguageCleanup:
    """en-specific post-processing: contraction expansion + filler removal.

    Args:
        enabled: Master toggle for all transformations.
        expand_contractions: Expand informal spoken contractions.
        strip_fillers: Remove filler words (um, uh, er, hmm, ah).
    """

    def __init__(
        self,
        enabled: bool = True,
        expand_contractions: bool = True,
        strip_fillers: bool = False,
    ) -> None:
        self._enabled = enabled
        self._expand = expand_contractions
        self._fillers = strip_fillers

    def apply(self, text: str) -> str:
        """Apply language cleanup to *text*.

        Args:
            text: Capitalised, punctuated transcription string.

        Returns:
            Cleaned text; unchanged if disabled.
        """
        if not self._enabled:
            return text

        if self._expand:
            for pat, repl in _CONTRACTION_PATS:
                text = pat.sub(repl, text)

        if self._fillers:
            text = _FILLER_PAT.sub("", text).strip()

        return text

"""PunctuationProcessor — voice command → punctuation substitution.

Rules
-----
- Match standalone tokens only (word-boundary ``\\b`` regex).
- Multi-word commands (e.g. ``"new line"``) matched longest-first.
- ``"period"`` / ``"full stop"`` replaced **only** at phrase boundary
  (end of string, or immediately before another known command word).
- Result is tidied: space before punctuation removed; spaces around
  newlines stripped.

Examples::

    "Hello comma world"              → "Hello, world"
    "Hello period"                   → "Hello."
    "My period is late"              → "My period is late"   (noun, not command)
    "Hello period new line"          → "Hello.\\n"
"""
from __future__ import annotations

import logging
import re
from typing import Final

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command map — spec-defined
# ---------------------------------------------------------------------------

COMMANDS: Final[dict[str, str]] = {
    "comma":             ",",
    "period":            ".",
    "full stop":         ".",
    "question mark":     "?",
    "exclamation mark":  "!",
    "exclamation point": "!",
    "new line":          "\n",
    "new paragraph":     "\n\n",
    "open quote":        "\u201c",
    "close quote":       "\u201d",
    "colon":             ":",
    "semicolon":         ";",
    "hyphen":            "-",
    "dash":              "\u2014",
    "open bracket":      "(",
    "close bracket":     ")",
}

# ---------------------------------------------------------------------------
# Pre-compiled patterns
# ---------------------------------------------------------------------------

# All command words (for phrase-boundary lookahead of "period")
_ALL_CMDS_RE = "|".join(
    re.escape(k) for k in sorted(COMMANDS, key=len, reverse=True)
)

# "period"/"full stop" only at phrase end or before another command
_PERIOD_PAT = re.compile(
    r"\b(?:period|full stop)\b"
    r"(?=\s*(?:$|(?:" + _ALL_CMDS_RE + r")\b))",
    re.IGNORECASE,
)

# Safe commands (no word-sense ambiguity) — longest first
_SAFE_SUBS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b" + re.escape(cmd) + r"\b", re.IGNORECASE),
        repl,
    )
    for cmd, repl in sorted(COMMANDS.items(), key=lambda kv: len(kv[0]), reverse=True)
    if cmd not in ("period", "full stop")
]

# Tidy-up
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.!?,:;])")
_SPACE_AROUND_NL = re.compile(r" *\n *")
_MULTI_SPACE = re.compile(r"  +")


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class PunctuationProcessor:
    """Substitute voice punctuation commands with their symbols.

    Args:
        enabled: If ``False``, :meth:`apply` is a passthrough.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def apply(self, text: str) -> str:
        """Replace punctuation commands in *text*.

        Args:
            text: Post-normaliser transcription string.

        Returns:
            Text with command words replaced by punctuation symbols.
        """
        if not self._enabled:
            return text

        # Pass 1 — safe (unambiguous) commands, longest first
        for pat, repl in _SAFE_SUBS:
            text = pat.sub(repl, text)

        # Pass 2 — phrase-boundary "period" / "full stop"
        text = _PERIOD_PAT.sub(".", text)

        # Tidy spacing
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _SPACE_AROUND_NL.sub("\n", text)
        text = _MULTI_SPACE.sub(" ", text)

        return text.strip()

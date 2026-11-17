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
# Public classes
# ---------------------------------------------------------------------------

# Letters on purpose: the capitaliser must treat a held-back snippet as a word
# (so it doesn't capitalise whatever follows); restore() matches case-insensitively.
_SNIPPET_MARK = "zqsnip{}zq"
_SNIPPET_RE = re.compile(r"zqsnip(\d+)zq", re.IGNORECASE)


class SnippetProcessor:
    """Swap spoken phrases for fixed text (``"my email"`` → an address).

    Matches like the punctuation commands: whole phrase (``\\b…\\b``),
    case-insensitive, longest first. The replacement is held back as a marker
    until :meth:`restore`, so no later step (numbers, punctuation commands,
    capitalisation, filler removal) can alter it.

    Args:
        snippets: Spoken phrase → replacement text. Empty/None = passthrough.
    """

    def __init__(self, snippets: dict[str, str] | None = None) -> None:
        items = sorted(
            ((k.strip(), str(v)) for k, v in (snippets or {}).items() if k and k.strip()),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        self._texts = [v for _, v in items]
        self._subs = [
            (re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE), _SNIPPET_MARK.format(i))
            for i, (k, _) in enumerate(items)
        ]

    def apply(self, text: str) -> str:
        """Replace each spoken phrase with a marker for its text."""
        for pat, mark in self._subs:
            text = pat.sub(mark, text)
        return text

    def restore(self, text: str) -> str:
        """Put the snippet texts in place of their markers."""
        if not self._subs:
            return text
        return _SNIPPET_RE.sub(lambda m: self._texts[int(m.group(1))], text)


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

        # Pass 1 — phrase-boundary "period" / "full stop". Must run before the
        # safe-command pass: its lookahead matches literal command words (e.g.
        # "new line"), which pass 2 below rewrites into symbols.
        text = _PERIOD_PAT.sub(".", text)

        # Pass 2 — safe (unambiguous) commands, longest first
        for pat, repl in _SAFE_SUBS:
            text = pat.sub(repl, text)

        # Tidy spacing
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _SPACE_AROUND_NL.sub("\n", text)
        text = _MULTI_SPACE.sub(" ", text)

        # strip() (not full strip) — trailing "\n" from "new line"/"new
        # paragraph" is a meaningful command result, not incidental whitespace.
        return text.strip(" ")

"""NumberNormalizer — spoken numbers/ordinals → written form.

Examples::

    "twenty two"  → "22"
    "third"       → "3rd"
    "one hundred" → "100"
"""
from __future__ import annotations

import logging
import re

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ordinal lookup table
# ---------------------------------------------------------------------------

_ORDINAL_WORDS: dict[str, str] = {
    "zeroth": "0th", "first": "1st", "second": "2nd", "third": "3rd",
    "fourth": "4th", "fifth": "5th", "sixth": "6th", "seventh": "7th",
    "eighth": "8th", "ninth": "9th", "tenth": "10th", "eleventh": "11th",
    "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th",
    "fifteenth": "15th", "sixteenth": "16th", "seventeenth": "17th",
    "eighteenth": "18th", "nineteenth": "19th", "twentieth": "20th",
    "twenty-first": "21st", "twenty-second": "22nd", "twenty-third": "23rd",
    "thirtieth": "30th", "fortieth": "40th", "fiftieth": "50th",
    "sixtieth": "60th", "seventieth": "70th", "eightieth": "80th",
    "ninetieth": "90th", "hundredth": "100th", "thousandth": "1000th",
    "millionth": "1000000th",
}

_ORDINAL_PAT = re.compile(
    r"\b(" + "|".join(sorted(_ORDINAL_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Cardinal number word set (guard before calling word2number)
# ---------------------------------------------------------------------------

_NUMBER_WORDS = frozenset(
    [
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
        "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
        "hundred", "thousand", "million", "billion",
    ]
)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class NumberNormalizer:
    """Convert spoken numbers and ordinals to digit form.

    Args:
        enabled: If ``False``, :meth:`apply` is a zero-cost passthrough.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._w2n: object | None = None

        if enabled:
            try:
                from word2number import w2n  # noqa: PLC0415

                self._w2n = w2n
                _LOG.debug("word2number available — NumberNormalizer active")
            except ImportError:
                _LOG.warning("word2number not installed — cardinal conversion disabled")

    def apply(self, text: str) -> str:
        """Normalise spoken numbers in *text*.

        Args:
            text: Transcription string from STT.

        Returns:
            Text with numbers converted; unchanged on error.
        """
        if not self._enabled:
            return text

        text = _replace_ordinals(text)
        if self._w2n is not None:
            text = _replace_cardinals(text, self._w2n)
        return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replace_ordinals(text: str) -> str:
    def _sub(m: re.Match) -> str:
        return _ORDINAL_WORDS.get(m.group(0).lower(), m.group(0))

    return _ORDINAL_PAT.sub(_sub, text)


def _replace_cardinals(text: str, w2n: object) -> str:
    """Greedily convert longest matching spans of number words."""
    tokens = text.split()
    result: list[str] = []
    i = 0

    while i < len(tokens):
        # Only attempt conversion if this token looks like a number word
        if tokens[i].lower() not in _NUMBER_WORDS:
            result.append(tokens[i])
            i += 1
            continue

        # word2number silently ignores trailing non-number words in a span
        # (e.g. "twenty two items" -> 22), so cap the span to the run of
        # actual number words to avoid swallowing following text.
        run_len = 0
        while i + run_len < len(tokens) and tokens[i + run_len].lower() in _NUMBER_WORDS:
            run_len += 1

        converted = False
        for length in range(min(8, run_len), 0, -1):
            span = " ".join(tokens[i : i + length])
            try:
                num = w2n.word_to_num(span)  # type: ignore[union-attr]
                result.append(str(num))
                i += length
                converted = True
                break
            except (ValueError, AttributeError):
                continue

        if not converted:
            result.append(tokens[i])
            i += 1

    return " ".join(result)

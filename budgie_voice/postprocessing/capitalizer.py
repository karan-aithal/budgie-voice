"""Capitalizer — sentence-start and injection-start capitalisation.

Rules
-----
- Capitalise the first alphabetic character of the string (injection start).
- Capitalise the first alphabetic character after ``. ``, ``! ``, ``? ``
  (sentence-ending punctuation followed by a space).
- Capitalise the first alphabetic character after ``\\n\\n`` (new paragraph).
- Do **not** capitalise after a bare ``\\n`` — that is a line break, not a
  new sentence.

Examples::

    "hello world"          → "Hello world"
    "hello. world"         → "Hello. World"
    "hello.\\n22 items"    → "Hello.\\n22 items"
    "hello.\\n\\nnext"     → "Hello.\\n\\nNext"
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)


class Capitalizer:
    """Capitalise sentence starts in post-processed text.

    Args:
        enabled: If ``False``, :meth:`apply` is a passthrough.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def apply(self, text: str) -> str:
        """Capitalise sentence starts in *text*.

        Args:
            text: Punctuation-processed string.

        Returns:
            Text with sentence starts capitalised.
        """
        if not self._enabled or not text:
            return text

        chars = list(text)
        capitalize_next = True
        i = 0

        while i < len(chars):
            c = chars[i]

            # Apply capitalisation to next eligible alpha char
            if capitalize_next and c.isalpha():
                chars[i] = c.upper()
                capitalize_next = False

            # Sentence-ending punctuation + space → capitalise next alpha
            if c in ".!?" and i + 1 < len(chars) and chars[i + 1] == " ":
                capitalize_next = True

            # Double newline (new paragraph) → capitalise next alpha
            elif c == "\n" and i + 1 < len(chars) and chars[i + 1] == "\n":
                capitalize_next = True

            i += 1

        return "".join(chars)

"""PostProcessingPipeline — chains all text processors in order.

Processing order:
    0. :class:`~budgie_voice.postprocessing.punctuation.SnippetProcessor` (held back, restored last)
    1. :class:`~budgie_voice.postprocessing.normalizer.NumberNormalizer`
    2. :class:`~budgie_voice.postprocessing.punctuation.PunctuationProcessor`
    2b. "scratch that" (``spoken_edits``)
    3. :class:`~budgie_voice.postprocessing.capitalizer.Capitalizer`
    4. :class:`~budgie_voice.postprocessing.language_cleanup.LanguageCleanup`
    5. snippets restored, then ``strip_trailing_period``
"""
from __future__ import annotations

import logging
import re

from budgie_voice.postprocessing.capitalizer import Capitalizer
from budgie_voice.postprocessing.language_cleanup import LanguageCleanup
from budgie_voice.postprocessing.normalizer import NumberNormalizer
from budgie_voice.postprocessing.punctuation import PunctuationProcessor, SnippetProcessor

_LOG = logging.getLogger(__name__)

_SENTENCE_GAP = re.compile(r"((?<=[.!?])\s+|\n+)")  # after . ! ? — or at any line break
_SCRATCH_END = re.compile(r"(?:^|[\s,;])scratch that$", re.IGNORECASE)


def _scratch_that(text: str) -> str:
    """Drop the sentence before a spoken "scratch that" (exact phrase, ends its sentence).

    ``"A. B. Scratch that. C"`` → ``"A. C"``; ``"A. B, scratch that."`` → ``"A."``.
    A sentence that merely contains the phrase ("scratch that itch") is left alone.
    """
    if "scratch that" not in text.lower():
        return text
    parts = _SENTENCE_GAP.split(text)  # sentence, gap, sentence, gap, …, sentence
    kept: list[tuple[str, str]] = []
    for sentence, gap in zip(parts[0::2], parts[1::2] + [""]):
        match = _SCRATCH_END.search(sentence.rstrip(" .!?,;"))
        if match is None:
            kept.append((sentence, gap))
        elif not sentence[: match.start()].strip() and kept:
            kept.pop()  # the phrase stands alone: it scratches the sentence before it
        # else: "B, scratch that." — the sentence is its own scratched content; drop it
    return "".join(a + b for a, b in kept).rstrip()


class PostProcessingPipeline:
    """Apply the full post-processing chain to a raw STT transcript.

    Args:
        normalize_numbers: Enable :class:`NumberNormalizer`.
        auto_capitalize: Enable :class:`Capitalizer`.
        punctuation_commands: Enable :class:`PunctuationProcessor`.
        language_cleanup: Enable :class:`LanguageCleanup`.
        strip_fillers: Remove um/uh/er/hmm (part of :class:`LanguageCleanup`).
        spoken_edits: Honour a spoken "scratch that".
        strip_trailing_period: Drop one final ``.`` (not an ellipsis).
        snippets: Spoken phrase → replacement text.
    """

    # Toggles a per-app profile may override (snippets stay global).
    OVERRIDABLE = frozenset({
        "normalize_numbers", "auto_capitalize", "punctuation_commands", "language_cleanup",
        "strip_fillers", "spoken_edits", "strip_trailing_period",
    })

    def __init__(
        self,
        normalize_numbers: bool = True,
        auto_capitalize: bool = True,
        punctuation_commands: bool = True,
        language_cleanup: bool = True,
        strip_fillers: bool = False,
        spoken_edits: bool = True,
        strip_trailing_period: bool = False,
        snippets: dict[str, str] | None = None,
    ) -> None:
        self._cfg = dict(
            normalize_numbers=normalize_numbers, auto_capitalize=auto_capitalize,
            punctuation_commands=punctuation_commands, language_cleanup=language_cleanup,
            strip_fillers=strip_fillers, spoken_edits=spoken_edits,
            strip_trailing_period=strip_trailing_period, snippets=snippets,
        )
        self._snippets = SnippetProcessor(snippets)
        self._normalizer = NumberNormalizer(enabled=normalize_numbers)
        self._punctuation = PunctuationProcessor(enabled=punctuation_commands)
        self._capitalizer = Capitalizer(enabled=auto_capitalize)
        self._cleanup = LanguageCleanup(enabled=language_cleanup, strip_fillers=strip_fillers)
        self._spoken_edits = spoken_edits
        self._strip_period = strip_trailing_period

    def with_overrides(self, overrides: dict[str, object]) -> PostProcessingPipeline:
        """A copy with some toggles changed (per-app profile). Unknown keys are logged and ignored."""
        unknown = set(overrides) - self.OVERRIDABLE
        if unknown:
            _LOG.warning("Profile keys ignored (not post-processing toggles): %s", sorted(unknown))
        return PostProcessingPipeline(
            **{**self._cfg, **{k: v for k, v in overrides.items() if k in self.OVERRIDABLE}}
        )

    def run(self, text: str) -> str:
        """Run the full post-processing chain.

        Args:
            text: Raw STT transcript, as the backend produced it.

        Returns:
            Cleaned, capitalised, punctuated text ready for injection.
        """
        if not text or not text.strip():
            return ""

        text = self._snippets.apply(text)
        text = self._normalizer.apply(text)
        text = self._punctuation.apply(text)
        if self._spoken_edits:
            text = _scratch_that(text)
        text = self._capitalizer.apply(text)
        text = self._cleanup.apply(text)
        text = self._snippets.restore(text)
        if self._strip_period and text.endswith(".") and not text.endswith(".."):
            text = text[:-1]

        _LOG.debug("Pipeline output: %r", text)
        return text

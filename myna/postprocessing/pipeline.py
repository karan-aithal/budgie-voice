"""PostProcessingPipeline — chains all text processors in order.

Processing order (matches spec):
    1. :class:`~myna.postprocessing.normalizer.NumberNormalizer`
    2. :class:`~myna.postprocessing.punctuation.PunctuationProcessor`
    3. :class:`~myna.postprocessing.capitalizer.Capitalizer`
    4. :class:`~myna.postprocessing.language_cleanup.LanguageCleanup`
"""
from __future__ import annotations

import logging

from myna.postprocessing.capitalizer import Capitalizer
from myna.postprocessing.language_cleanup import LanguageCleanup
from myna.postprocessing.normalizer import NumberNormalizer
from myna.postprocessing.punctuation import PunctuationProcessor

_LOG = logging.getLogger(__name__)


class PostProcessingPipeline:
    """Apply the full post-processing chain to a raw STT transcript.

    Args:
        normalize_numbers: Enable :class:`NumberNormalizer`.
        auto_capitalize: Enable :class:`Capitalizer`.
        punctuation_commands: Enable :class:`PunctuationProcessor`.
        language_cleanup: Enable :class:`LanguageCleanup`.
    """

    def __init__(
        self,
        normalize_numbers: bool = True,
        auto_capitalize: bool = True,
        punctuation_commands: bool = True,
        language_cleanup: bool = True,
    ) -> None:
        self._normalizer = NumberNormalizer(enabled=normalize_numbers)
        self._punctuation = PunctuationProcessor(enabled=punctuation_commands)
        self._capitalizer = Capitalizer(enabled=auto_capitalize)
        self._cleanup = LanguageCleanup(enabled=language_cleanup)

    def run(self, text: str) -> str:
        """Run the full post-processing chain.

        Args:
            text: Raw STT transcript, as the backend produced it.

        Returns:
            Cleaned, capitalised, punctuated text ready for injection.
        """
        if not text or not text.strip():
            return ""

        text = self._normalizer.apply(text)
        text = self._punctuation.apply(text)
        text = self._capitalizer.apply(text)
        text = self._cleanup.apply(text)

        _LOG.debug("Pipeline output: %r", text)
        return text

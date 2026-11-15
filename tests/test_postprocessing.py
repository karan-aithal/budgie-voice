"""Phase 2 tests — PostProcessingPipeline."""
from __future__ import annotations

import pytest

from budgie_voice.postprocessing.capitalizer import Capitalizer
from budgie_voice.postprocessing.language_cleanup import LanguageCleanup
from budgie_voice.postprocessing.normalizer import NumberNormalizer
from budgie_voice.postprocessing.pipeline import PostProcessingPipeline
from budgie_voice.postprocessing.punctuation import PunctuationProcessor


# ---------------------------------------------------------------------------
# NumberNormalizer
# ---------------------------------------------------------------------------

class TestNumberNormalizer:
    def setup_method(self) -> None:
        self.norm = NumberNormalizer(enabled=True)

    def test_passthrough_when_disabled(self) -> None:
        n = NumberNormalizer(enabled=False)
        assert n.apply("twenty two") == "twenty two"

    def test_ordinal_first(self) -> None:
        assert self.norm.apply("the first item") == "the 1st item"

    def test_ordinal_third(self) -> None:
        assert self.norm.apply("third place") == "3rd place"

    def test_cardinal_twenty_two(self) -> None:
        result = self.norm.apply("twenty two items")
        assert result == "22 items"

    def test_no_false_positive(self) -> None:
        # Non-number words must not be converted
        result = self.norm.apply("hello world")
        assert result == "hello world"


# ---------------------------------------------------------------------------
# PunctuationProcessor
# ---------------------------------------------------------------------------

class TestPunctuationProcessor:
    def setup_method(self) -> None:
        self.proc = PunctuationProcessor(enabled=True)

    def test_comma(self) -> None:
        assert self.proc.apply("hello comma world") == "hello, world"

    def test_period_at_end(self) -> None:
        assert self.proc.apply("hello period") == "hello."

    def test_period_not_noun(self) -> None:
        # "period" mid-sentence with non-command word following → unchanged
        result = self.proc.apply("my period is late")
        assert "my period is late" == result

    def test_period_before_new_line(self) -> None:
        result = self.proc.apply("hello period new line")
        assert result == "hello.\n"

    def test_question_mark(self) -> None:
        assert self.proc.apply("really question mark") == "really?"

    def test_new_line(self) -> None:
        result = self.proc.apply("line one new line line two")
        assert result == "line one\nline two"

    def test_new_paragraph(self) -> None:
        result = self.proc.apply("para one new paragraph para two")
        assert result == "para one\n\npara two"

    def test_exclamation_mark(self) -> None:
        assert self.proc.apply("great exclamation mark") == "great!"

    def test_colon(self) -> None:
        assert self.proc.apply("note colon important") == "note: important"

    def test_passthrough_when_disabled(self) -> None:
        proc = PunctuationProcessor(enabled=False)
        assert proc.apply("hello period") == "hello period"


# ---------------------------------------------------------------------------
# Capitalizer
# ---------------------------------------------------------------------------

class TestCapitalizer:
    def setup_method(self) -> None:
        self.cap = Capitalizer(enabled=True)

    def test_injection_start(self) -> None:
        assert self.cap.apply("hello world") == "Hello world"

    def test_after_period_space(self) -> None:
        assert self.cap.apply("hello. world") == "Hello. World"

    def test_after_question_mark(self) -> None:
        assert self.cap.apply("really? yes") == "Really? Yes"

    def test_no_cap_after_single_newline(self) -> None:
        result = self.cap.apply("hello.\n22 items")
        assert result == "Hello.\n22 items"

    def test_cap_after_double_newline(self) -> None:
        result = self.cap.apply("para one.\n\nnext para")
        assert result == "Para one.\n\nNext para"

    def test_passthrough_when_disabled(self) -> None:
        cap = Capitalizer(enabled=False)
        assert cap.apply("hello") == "hello"


# ---------------------------------------------------------------------------
# LanguageCleanup
# ---------------------------------------------------------------------------

class TestLanguageCleanup:
    def test_gonna(self) -> None:
        lc = LanguageCleanup(enabled=True, expand_contractions=True)
        assert lc.apply("I'm gonna go") == "I'm going to go"

    def test_wanna(self) -> None:
        lc = LanguageCleanup(enabled=True, expand_contractions=True)
        assert lc.apply("I wanna eat") == "I want to eat"

    def test_filler_stripped(self) -> None:
        lc = LanguageCleanup(enabled=True, strip_fillers=True)
        assert lc.apply("um hello") == "hello"

    def test_passthrough_when_disabled(self) -> None:
        lc = LanguageCleanup(enabled=False)
        assert lc.apply("gonna") == "gonna"


# ---------------------------------------------------------------------------
# Full pipeline — acceptance test
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_acceptance(self) -> None:
        """hello world period new line twenty two items → Hello world.\\n22 items"""
        pipeline = PostProcessingPipeline(
            normalize_numbers=True,
            auto_capitalize=True,
            punctuation_commands=True,
            language_cleanup=True,
        )
        raw = "hello world period new line twenty two items"
        result = pipeline.run(raw)
        assert result == "Hello world.\n22 items"

    def test_empty_string(self) -> None:
        pipeline = PostProcessingPipeline()
        assert pipeline.run("") == ""

    def test_whitespace_only(self) -> None:
        pipeline = PostProcessingPipeline()
        assert pipeline.run("   ") == ""

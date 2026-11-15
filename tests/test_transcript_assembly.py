"""Phase 3: one pipeline pass per dictation, turns close by length, hotwords from the dictionary."""
from __future__ import annotations

import re

from test_session_reliability import _frame, _Rig

from myna.orchestrator.corrections import CorrectionMemory
from myna.postprocessing.pipeline import PostProcessingPipeline


async def test_pauses_do_not_split_the_sentence(monkeypatch) -> None:
    rig = _Rig(monkeypatch, min_turn_s=0)  # worst case: every pause closes a turn
    rig.svc._pipeline = PostProcessingPipeline()  # noqa: SLF001
    rig.backend.script = ["i think", "we should go", "tomorrow"]
    await rig.svc.start()
    await rig.dictate(_frame(1, end=True), _frame(2, end=True), _frame(3, end=True))

    (text,) = rig.typed()
    assert rig.backend.turns == 3
    assert len(re.findall(r"[A-Z]", text)) == 1
    assert "." not in text.rstrip(".")  # no full stop before the last word
    await rig.close()


async def test_turn_stays_open_below_min_turn_s(monkeypatch) -> None:
    rig = _Rig(monkeypatch)  # default: hints ignored until 15 s of audio
    await rig.svc.start()
    await rig.dictate(_frame(1000, end=True), _frame(2000, end=True))
    assert rig.backend.turns == 1  # one pass, at stop
    assert rig.typed() == ["1000 2000"]
    await rig.close()


async def test_turn_closes_at_hint_once_long_enough(monkeypatch) -> None:
    rig = _Rig(monkeypatch, min_turn_s=0.015)  # a frame is 0.01 s
    await rig.svc.start()
    await rig.dictate(_frame(1000), _frame(2000, end=True), _frame(3000))
    assert rig.backend.turns == 2  # closed at the hint, remainder at stop
    await rig.close()


async def test_hotwords_are_dictionary_then_corrections(monkeypatch, tmp_path) -> None:
    rig = _Rig(monkeypatch)
    dictionary = tmp_path / "dictionary.txt"
    dictionary.write_text("# my terms\nParakeet\n\nMyna\n")
    rig.svc._settings.learning.dictionary_path = str(dictionary)  # noqa: SLF001
    memory = CorrectionMemory(tmp_path / "corrections.json")
    memory.learn("a cube here", "a Kubernetes here")
    rig.svc._corrections = memory  # noqa: SLF001

    rig.svc._refresh_hotwords()  # noqa: SLF001
    assert rig.svc._client._hotwords == "Parakeet Myna Kubernetes"  # noqa: SLF001

    dictionary.write_text("Zephyr\n")  # edits apply without a restart
    rig.svc._refresh_hotwords()  # noqa: SLF001
    assert rig.svc._client._hotwords == "Zephyr Kubernetes"  # noqa: SLF001
    await rig.close()

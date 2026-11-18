"""Phase B2: dictation history persistence.

Store and recorder are tested directly; the word counter and the whole chain are tested with the
real DictationService on the Phase 1/9 rig. Rows are read back with plain sqlite3, not the code under test.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from budgie_voice.analytics.recorder import SessionRecorder, _local_now
from budgie_voice.analytics.store import HistoryStore, SessionRecord
from budgie_voice.ui import status as status_mod
from budgie_voice.ui.status import RuntimeStatus
from budgie_voice.utils import selection

from test_phase9 import TURNS, _rig, _speak
from test_session_reliability import _frame, _Rig

UTC = timezone.utc
NOON = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


def _rows(path: Path) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(
            "SELECT status, words_count, characters_count, duration_ms, local_date, started_at, id "
            "FROM dictation_sessions ORDER BY started_at"
        ).fetchall()
    finally:
        con.close()


def _rec(id_: str = "a" * 32, **kw) -> SessionRecord:
    base = dict(id=id_, started_at=NOON, local_date=date(2026, 11, 18), duration_ms=1500, words=4, chars=21,
                status="completed")
    return SessionRecord(**{**base, **kw})


class _Clock:
    """Fake wall clock and monotonic clock, advanced by hand."""

    def __init__(self, start: datetime = NOON) -> None:
        self.wall, self.t = start, 100.0

    def now(self) -> datetime:
        return self.wall

    def mono(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
        self.wall += timedelta(seconds=seconds)


def _recorder(tmp_path: Path, clock: _Clock | None = None) -> tuple[SessionRecorder, _Clock, Path]:
    clock = clock or _Clock()
    path = tmp_path / "history.db"
    return SessionRecorder(HistoryStore(path), clock.now, clock.mono), clock, path


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_store_creates_the_schema_with_no_column_that_could_hold_text(tmp_path) -> None:
    path = tmp_path / "history.db"
    store = HistoryStore(path)
    assert store.available
    con = sqlite3.connect(path)
    cols = {r[1]: r[2] for r in con.execute("PRAGMA table_info(dictation_sessions)")}
    assert cols == {"id": "TEXT", "started_at": "TEXT", "local_date": "TEXT", "duration_ms": "INTEGER",
                    "words_count": "INTEGER", "characters_count": "INTEGER", "status": "TEXT"}
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


def test_store_files_are_owner_only(tmp_path) -> None:
    path = tmp_path / "fresh" / "history.db"
    store = HistoryStore(path)
    assert store.add(_rec())
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700  # created by the store
    for f in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if f.exists():
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_rows_survive_a_restart_and_keep_their_local_day(tmp_path) -> None:
    path = tmp_path / "history.db"
    # 00:00 on 19 Nov in UTC+14 is 10:00 on 18 Nov in UTC: the stored local day must not be re-derived.
    assert HistoryStore(path).add(_rec(local_date=date(2026, 11, 19), started_at=NOON.replace(hour=10)))
    (row,) = _rows(path)
    assert row[4] == "2026-11-19" and row[5] == "2026-11-18T10:00:00.000Z"
    again = HistoryStore(path)  # restart: opening an existing file must not touch its rows
    assert again.available and again.add(_rec("b" * 32)) and len(_rows(path)) == 2


def test_a_repeated_id_is_saved_once(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    assert store.add(_rec()) and store.add(_rec(words=999))
    assert [r[1] for r in _rows(store.path)] == [4]  # the first write wins


def test_invalid_rows_are_refused_without_raising(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    assert not store.add(_rec(status="weird")) and not store.add(_rec("c" * 32, words=-1))
    assert _rows(store.path) == []


def test_unusable_locations_disable_history_without_raising(tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = HistoryStore(blocker / "sub" / "history.db")  # parent is a file
    assert not store.available and store.add(_rec()) is False


def test_a_corrupt_file_is_left_exactly_as_found(tmp_path) -> None:
    path = tmp_path / "history.db"
    junk = b"this is not a database" * 50
    path.write_bytes(junk)
    store = HistoryStore(path)
    assert not store.available and store.add(_rec()) is False
    assert path.read_bytes() == junk  # never renamed, never recreated


def test_a_newer_schema_is_left_alone(tmp_path) -> None:
    path = tmp_path / "history.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE future (x)")
    con.execute("PRAGMA user_version=99")
    con.commit()
    con.close()
    before = path.read_bytes()
    store = HistoryStore(path)
    assert not store.available and not store.add(_rec())
    assert path.read_bytes() == before


def test_write_failures_warn_once_then_go_quiet(tmp_path, caplog) -> None:
    store = HistoryStore(tmp_path / "history.db")
    store.path.unlink()
    for ext in ("-wal", "-shm"):
        Path(f"{store.path}{ext}").unlink(missing_ok=True)
    store.path.mkdir()  # the file is now a directory: every write fails
    with caplog.at_level(logging.DEBUG, logger="budgie_voice.analytics"):
        assert not store.add(_rec("d" * 32)) and not store.add(_rec("e" * 32))
    levels = [r.levelno for r in caplog.records if r.name.startswith("budgie_voice.analytics")]
    assert levels == [logging.WARNING, logging.DEBUG]


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

def test_completed_session_is_one_row(tmp_path) -> None:
    rec, clock, path = _recorder(tmp_path)
    rec.on_state("starting", 0, 0)
    clock.advance(3.0)                      # mic open + model: not recording time
    rec.on_state("recording", 0, 0)
    clock.advance(2.5)
    rec.on_state("finalizing", 0, 0)
    clock.advance(9.0)                      # transcription, LLM, preview wait: not recording time
    rec.on_state("completed", 21, 4)
    rec.on_state("idle", 21, 4)
    (row,) = _rows(path)
    assert row[:5] == ("completed", 4, 21, 2500, "2026-11-18")
    assert row[5] == "2026-11-18T12:00:03.000Z"  # when recording began
    assert len(row[6]) == 32


def test_a_session_that_never_recorded_leaves_no_row(tmp_path) -> None:
    rec, _, path = _recorder(tmp_path)
    for s in ("starting", "error", "idle"):  # e.g. "Could not start dictation", or a secure-field block
        rec.on_state(s, 0, 0)
    assert _rows(path) == []


def test_a_repeated_terminal_callback_saves_nothing_more(tmp_path) -> None:
    rec, clock, path = _recorder(tmp_path)
    rec.on_state("recording", 0, 0)
    clock.advance(1)
    rec.on_state("finalizing", 0, 0)
    rec.on_state("completed", 5, 1)
    rec.on_state("completed", 5, 1)
    rec.on_state("error", 5, 1)
    assert len(_rows(path)) == 1


def test_cancel_and_failure_are_recorded_with_what_was_typed(tmp_path) -> None:
    rec, clock, path = _recorder(tmp_path)
    rec.on_state("recording", 0, 0)
    clock.advance(4)
    rec.on_state("cancelled", 22, 4)        # no finalizing: duration runs to the cancel
    rec.on_state("recording", 0, 0)
    clock.advance(1)
    rec.on_state("error", 0, 0)
    assert [(r[0], r[1], r[2], r[3]) for r in _rows(path)] == [("cancelled", 4, 22, 4000), ("failed", 0, 0, 1000)]


def test_an_unfinished_session_is_dropped_never_invented(tmp_path) -> None:
    rec, clock, path = _recorder(tmp_path)
    rec.on_state("recording", 0, 0)         # its terminal state is never delivered (crash, missed callback)
    clock.advance(30)
    rec.on_state("recording", 0, 0)
    clock.advance(2)
    rec.on_state("finalizing", 0, 0)
    rec.on_state("completed", 3, 1)
    assert [(r[1], r[3]) for r in _rows(path)] == [(1, 2000)]


def test_the_local_day_is_the_day_the_session_started(tmp_path) -> None:
    east, west = timezone(timedelta(hours=14)), timezone(timedelta(hours=-8))
    rec, clock, path = _recorder(tmp_path, _Clock(datetime(2026, 11, 19, 0, 30, tzinfo=east)))
    rec.on_state("recording", 0, 0)
    rec.on_state("completed", 1, 1)
    clock.wall = datetime(2026, 11, 18, 23, 59, 50, tzinfo=west)  # UTC date is already the 19th
    rec.on_state("recording", 0, 0)
    clock.advance(30)                                              # ends after local midnight
    rec.on_state("completed", 1, 1)
    assert sorted((r[4], r[5][:10]) for r in _rows(path)) == [("2026-11-18", "2026-11-19"),
                                                              ("2026-11-19", "2026-11-18")]


def test_dst_days_in_a_real_zone(tmp_path) -> None:
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        ny = zoneinfo.ZoneInfo("America/New_York")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database")
    spring = datetime(2026, 3, 8, 3, 30, tzinfo=ny)             # 02:00-03:00 does not exist today
    fall_first = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    fall_second = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=1)  # the repeated hour
    rec, clock, path = _recorder(tmp_path)
    for moment in (spring, fall_first, fall_second):
        clock.wall = moment
        rec.on_state("recording", 0, 0)
        rec.on_state("completed", 1, 1)
    got = sorted((r[4], r[5]) for r in _rows(path))
    assert got == [("2026-03-08", "2026-03-08T07:30:00.000Z"),     # EDT = UTC-4
                   ("2026-11-01", "2026-11-01T05:30:00.000Z"),     # EDT
                   ("2026-11-01", "2026-11-01T06:30:00.000Z")]     # EST: the same wall time an hour later


def test_the_default_clock_is_timezone_aware() -> None:
    assert _local_now().utcoffset() is not None


def test_a_broken_store_cannot_break_the_callback() -> None:
    class Boom:
        def add(self, rec):
            raise RuntimeError("disk on fire")

    rec = SessionRecorder(Boom())  # type: ignore[arg-type]
    rec.on_state("recording", 0, 0)
    rec.on_state("completed", 1, 1)  # must not raise
    rec.on_state("recording", 0, 0)  # and it keeps working afterwards
    assert rec._open is not None  # noqa: SLF001


def test_recorder_duration_matches_the_overview_activity_log(tmp_path, monkeypatch) -> None:
    """Two implementations of 'recording time' must never disagree."""
    clock = _Clock()
    monkeypatch.setattr(status_mod.time, "monotonic", clock.mono)
    rec, _, path = _recorder(tmp_path, clock)
    st = RuntimeStatus()
    for state in ("recording", "finalizing", "completed"):
        if state == "finalizing":
            clock.advance(2.25)
        if state == "completed":
            clock.advance(7)
        st.on_session_state(state, 5)
        rec.on_state(state, 5, 1)
    (act,) = st.activity
    assert _rows(path)[0][3] == round(act.duration_s * 1000) == 2250


# ---------------------------------------------------------------------------
# Word counter and the whole chain, on the real DictationService
# ---------------------------------------------------------------------------

def _wire(monkeypatch, tmp_path, store: HistoryStore | None = None, **kw) -> tuple[_Rig, Path]:
    rig = _rig(monkeypatch, **kw)
    store = store or HistoryStore(tmp_path / "history.db")
    rec = SessionRecorder(store)
    # Exactly what main.py registers, in front of the Overview's status callback.
    rig.svc._session._on_change = lambda s: (  # noqa: SLF001
        rig.states.append(s), rec.on_state(str(s), rig.svc.typed_chars, rig.svc.typed_words))
    return rig, store.path


def _brief(path: Path) -> list[tuple]:
    return [r[:3] for r in _rows(path)]


async def test_non_progressive_dictation_counts_what_was_typed(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    rig.backend.script = ["hello brave new world"]
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    assert rig.svc.typed_words == 4
    assert _brief(path) == [("completed", 4, len("hello brave new world"))]
    await rig.close()


async def test_progressive_segments_are_counted_exactly_once(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path)
    await rig.svc.start()
    await _speak(rig)
    assert rig.svc.typed_words == 4  # two turns typed while speaking
    await rig.svc.toggle("tray")
    assert rig.svc.typed_words == 6
    assert _brief(path) == [("completed", 6, len(" ".join(TURNS)))]
    await rig.close()


async def test_progressive_cancel_keeps_the_segments_already_typed(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path)
    await rig.svc.start()
    await _speak(rig)
    await rig.svc.cancel()
    assert _brief(path) == [("cancelled", 4, len("first turn second turn"))]
    await rig.close()


async def test_a_failing_last_segment_is_not_counted(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path)
    rig.injector.commit_text.side_effect = [None, None, RuntimeError("window gone")]
    await rig.svc.start()
    await _speak(rig)
    await rig.svc.toggle("tray")
    assert _brief(path) == [("completed", 4, len("first turn second turn"))]  # the 3rd turn never typed
    await rig.close()


async def test_a_failing_injection_counts_nothing(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    rig.injector.commit_text.side_effect = RuntimeError("window gone")
    rig.backend.script = ["lost words here"]
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    assert _brief(path) == [("failed", 0, 0)]
    await rig.close()


async def test_empty_dictation_and_cancelled_preview_count_zero(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    await rig.svc.start()
    rig.backend.script = [""]
    await rig.dictate(_frame(1000))
    rig.backend.script = ["ASR heard this but the user pressed Esc"]
    rig.svc._on_preview = AsyncMock(return_value=None)  # noqa: SLF001
    rig.svc.settings.injection.preview_before_inject = True
    await rig.dictate(_frame(1000))
    assert _brief(path) == [("completed", 0, 0), ("completed", 0, 0)]  # saved honestly, counted as nothing
    await rig.close()


async def test_command_mode_counts_no_dictated_words(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(selection, "read_primary", lambda: "hello world")
    rig.svc._llm = SimpleNamespace(rewrite=AsyncMock(return_value="Hi there friend."), polish=AsyncMock())  # noqa: SLF001
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000, end=True)]
    await rig.svc.command_start()
    await asyncio.sleep(0.1)
    await rig.svc.command()
    assert rig.typed() == ["Hi there friend."]
    assert _brief(path) == [("completed", 0, len("Hi there friend."))]  # typed, but not words the user said
    await rig.close()


async def test_a_start_failure_leaves_no_row_and_the_next_dictation_is_saved(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    await rig.svc.start()
    rig.adapter.open_error = RuntimeError("no mic")
    await rig.svc.toggle("tray")
    assert _rows(path) == []
    rig.adapter.open_error = None
    rig.backend.script = ["second try works"]
    await rig.dictate(_frame(1000))
    assert _brief(path) == [("completed", 3, len("second try works"))]
    await rig.close()


async def test_each_dictation_is_its_own_row(monkeypatch, tmp_path) -> None:
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    await rig.svc.start()
    for text in ("one two", "three"):
        rig.backend.script = [text]
        await rig.dictate(_frame(1000))
    rows = _rows(path)
    assert [r[1] for r in rows] == [2, 1] and len({r[6] for r in rows}) == 2
    await rig.close()


async def test_unavailable_history_never_stops_dictation(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = HistoryStore(blocker / "history.db")
    assert not store.available
    rig, _ = _wire(monkeypatch, tmp_path, store, progressive=False)
    rig.backend.script = ["still typed fine"]
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    assert rig.typed() == ["still typed fine"]
    assert rig.states[-1].value == "idle"
    await rig.close()


async def test_no_dictated_text_reaches_the_file_or_our_logs(monkeypatch, tmp_path, caplog) -> None:
    marker = "zebrafoxtrot"
    rig, path = _wire(monkeypatch, tmp_path, progressive=False)
    rig.backend.script = [f"say {marker} out loud"]
    await rig.svc.start()
    with caplog.at_level(logging.DEBUG):
        await rig.dictate(_frame(1000))
    assert rig.typed() == [f"say {marker} out loud"]  # it really was dictated
    for f in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if f.exists():
            assert marker.encode() not in f.read_bytes(), f.name
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("budgie_voice.analytics")]
    assert all(marker not in m for m in ours)
    await rig.close()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_main_saves_the_row_before_the_overview_is_told() -> None:
    """The Overview refreshes from the status callback; if the recorder ran after it, the page would
    read the history before this dictation was written."""
    src = (Path(__file__).resolve().parents[1] / "budgie_voice" / "main.py").read_text()
    assert 0 < src.index("recorder.on_state(") < src.index("status.on_session_state(")

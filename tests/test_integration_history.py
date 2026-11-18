"""Phase B5: the whole chain together.

Real DictationService -> SessionRecorder -> HistoryStore -> (restart) -> statistics -> Overview and
History pages, wired in the order main.py uses. Expected numbers are recomputed with plain SQL, never
with the code under test.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from budgie_voice.analytics import sessions, stats  # noqa: E402
from budgie_voice.analytics.recorder import SessionRecorder  # noqa: E402
from budgie_voice.analytics.store import HistoryStore, SessionRecord  # noqa: E402
from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.settings_ui import window as window_mod  # noqa: E402
from budgie_voice.settings_ui.history import HistoryPage  # noqa: E402
from budgie_voice.settings_ui.overview import OverviewPage, _fmt_time  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus  # noqa: E402
from budgie_voice.ui.theme import apply_theme  # noqa: E402

from test_phase9 import TURNS, _rig, _speak  # noqa: E402
from test_session_reliability import _frame  # noqa: E402

BASE = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _chain(monkeypatch, tmp_path):
    """The real service with its state callbacks wired exactly as main.py wires them."""
    rig = _rig(monkeypatch)
    store = HistoryStore(tmp_path / "history.db")
    status = RuntimeStatus()
    rec = SessionRecorder(store)
    rig.svc._session._on_change = lambda s: (  # noqa: SLF001
        rig.states.append(s),
        rec.on_state(str(s), rig.svc.typed_chars, rig.svc.typed_words),   # saves the row first ...
        status.on_session_state(str(s), rig.svc.typed_chars),             # ... then the pages are told
    )
    return rig, store, status


async def _progressive_full(rig) -> None:
    rig.svc.settings.injection.progressive = True
    rig.backend.script = list(TURNS)
    await _speak(rig)
    await rig.svc.toggle("tray")


async def _progressive_cancelled(rig) -> None:
    rig.svc.settings.injection.progressive = True
    rig.backend.script = list(TURNS)
    await _speak(rig)
    await rig.svc.cancel()


async def _plain(rig, text: str) -> None:
    rig.svc.settings.injection.progressive = False
    rig.backend.script = [text]
    await rig.dictate(_frame(1000))


def _sql(path: Path, query: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(query).fetchall()
    finally:
        con.close()


def _cards(page: OverviewPage) -> list[str]:
    return [c.value() for c in page._cards]


def _history_rows(page: HistoryPage) -> list[list[str]]:
    return [[page._list.topLevelItem(i).text(c) for c in range(6)] for i in range(page._list.topLevelItemCount())]


def _expected_cards(path: Path) -> list[str]:
    """What the four cards must say, from raw SQL and the definitions in stats.py."""
    words = _sql(path, "SELECT COALESCE(SUM(words_count),0) FROM dictation_sessions")[0][0]
    n, ms = _sql(path, "SELECT COUNT(*), COALESCE(SUM(duration_ms),0) FROM dictation_sessions "
                       "WHERE status='completed' AND words_count>0")[0]
    rw, rms = _sql(path, "SELECT COALESCE(SUM(words_count),0), COALESCE(SUM(duration_ms),0) FROM dictation_sessions "
                         "WHERE words_count>0 AND duration_ms>0")[0]
    return [f"{words:,}", f"{n:,}", "—" if rms == 0 else f"{rw / (rms / 60_000):.0f}",
            "—" if ms == 0 else _fmt_time(ms)]


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

async def test_a_day_of_dictation_survives_a_restart_and_every_view_agrees(app, monkeypatch, tmp_path) -> None:
    rig, store, status = _chain(monkeypatch, tmp_path)
    overview = OverviewPage(Settings(), status, lambda d: None, store)
    await rig.svc.start()

    await _progressive_full(rig)                        # 6 words, completed
    assert _cards(overview)[:2] == ["6", "1"]           # the page saw it the moment it ended
    await _plain(rig, "hello brave new world")          # 4 words, completed
    assert _cards(overview)[:2] == ["10", "2"]
    await _progressive_cancelled(rig)                   # 4 words typed before the cancel: kept, not a session
    assert _cards(overview)[:2] == ["14", "2"]
    await _plain(rig, "")                               # empty dictation
    rig.injector.commit_text.side_effect = RuntimeError("window gone")
    await _plain(rig, "lost words here")                # injection fails: nothing counted
    rig.injector.commit_text.side_effect = None
    await rig.close()

    path = store.path
    rows = _sql(path, "SELECT status, words_count, characters_count FROM dictation_sessions ORDER BY started_at")
    assert rows == [("completed", 6, len(" ".join(TURNS))), ("completed", 4, len("hello brave new world")),
                    ("cancelled", 4, len("first turn second turn")), ("completed", 0, 0), ("failed", 0, 0)]
    assert _sql(path, "SELECT MIN(duration_ms) FROM dictation_sessions WHERE words_count > 0")[0][0] > 0

    # --- restart: nothing in memory survives; a new store reads the same file -------------------------
    reopened = HistoryStore(path)
    assert reopened.available and reopened.healthy
    fresh = OverviewPage(Settings(), RuntimeStatus(), lambda d: None, reopened)
    assert _cards(fresh) == _cards(overview) == _expected_cards(path)
    assert _cards(fresh)[:2] == ["14", "2"] and "All time" in fresh._stats_note.text()

    # --- the statistics service, History page and chart all tell the same story ----------------------
    total = stats.total(stats.daily(reopened).values())
    assert (total.words, total.sessions) == (14, 2)
    history = HistoryPage(reopened, RuntimeStatus())
    history.refresh()
    table = _history_rows(history)
    assert [r[5] for r in table] == ["Failed", "Completed, nothing typed", "Cancelled", "Completed", "Completed"]  # newest first
    assert sum(int(r[3].replace(",", "")) for r in table) == total.words == 14
    assert sum(int(r[4].replace(",", "")) for r in table) == 33 + 21 + 22
    assert history._summary.text() == "Showing 5 of 5 dictations"
    assert sum(v for _, v in fresh._chart.series()) == 14   # today's bar, last 7 days


# ---------------------------------------------------------------------------
# Storage failure
# ---------------------------------------------------------------------------

async def test_losing_the_history_file_mid_run_never_stops_dictation_or_lies(app, monkeypatch, tmp_path) -> None:
    rig, store, status = _chain(monkeypatch, tmp_path)
    overview = OverviewPage(Settings(), status, lambda d: None, store)
    history = HistoryPage(store, status)
    await rig.svc.start()
    await _plain(rig, "first saved dictation")
    assert _cards(overview)[:2] == ["3", "1"]

    working = store._connect  # noqa: SLF001

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", locked)
    await _plain(rig, "second dictation while storage is broken")
    assert rig.typed()[-1] == "second dictation while storage is broken"     # typing was not affected
    assert rig.svc.state.value == "idle"
    assert not store.healthy
    assert "unavailable" in overview._stats_note.text() and _cards(overview) == ["—"] * 4  # not "no dictations yet"
    history.refresh()
    assert history._empty_title.text() == "History is unavailable"             # not "No dictation history yet"

    monkeypatch.setattr(store, "_connect", working)                          # storage comes back
    await _plain(rig, "third dictation after recovery")
    assert rig.typed()[-1] == "third dictation after recovery"
    overview.refresh()
    history.refresh()
    assert store.healthy and _cards(overview)[:2] == ["7", "2"]              # 3 + 4 words; the lost one is not invented
    assert [r[3] for r in _history_rows(history)] == ["4", "3"]
    await rig.close()


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

async def test_no_dictated_text_anywhere_in_files_logs_or_screens(app, monkeypatch, tmp_path, caplog) -> None:
    marker = "zebrafoxtrot"
    rig, store, status = _chain(monkeypatch, tmp_path)
    overview = OverviewPage(Settings(), status, lambda d: None, store)
    history = HistoryPage(store, status)
    await rig.svc.start()
    with caplog.at_level(logging.DEBUG, logger="budgie_voice.analytics"):
        await _plain(rig, f"say {marker} out loud")
        rig.svc.settings.injection.progressive = True
        rig.backend.script = [f"progressive {marker}", f"again {marker}"]
        await _speak(rig)
        await rig.svc.toggle("tray")
    assert any(marker in t for t in rig.typed())                             # it really was dictated
    history.refresh()
    history._list.setCurrentItem(history._list.topLevelItem(0))
    for f in (store.path, Path(f"{store.path}-wal"), Path(f"{store.path}-shm")):
        if f.exists():
            assert marker.encode() not in f.read_bytes(), f.name
    screens = [w.text() for page in (overview, history) for w in page.findChildren(QLabel)]
    screens += [overview._activity.topLevelItem(i).text(c) for i in range(overview._activity.topLevelItemCount())
                for c in range(4)]
    screens += [history._list.topLevelItem(i).text(c) for i in range(history._list.topLevelItemCount())
                for c in range(6)]
    assert screens and all(marker not in s for s in screens)
    assert all(marker not in r.getMessage() for r in caplog.records)
    await rig.close()


# ---------------------------------------------------------------------------
# Concurrency, scale, themes, selection
# ---------------------------------------------------------------------------

def test_readers_never_fail_while_another_thread_writes(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(150):
                assert store.add(SessionRecord(f"{i:032d}", BASE + timedelta(seconds=i), BASE.date(), 1000, 2, 10,
                                               "completed"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=writer)
    t.start()
    reads = 0
    while t.is_alive():
        stats.daily(store)
        sessions.page(store, limit=20)
        sessions.count(store)
        reads += 1
    t.join()
    assert not errors and reads > 0
    assert sessions.count(store) == 150 and stats.total(stats.daily(store).values()).words == 300
    assert store.healthy


def test_a_year_of_heavy_use_stays_fast(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    con = sqlite3.connect(store.path)
    con.executemany(
        "INSERT INTO dictation_sessions VALUES (?,?,?,?,?,?,?)",
        [(f"{i:032d}", (BASE + timedelta(minutes=i)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
          (BASE + timedelta(days=i // 14)).date().isoformat(), 3000 + i % 90_000, i % 120, 5 * (i % 120),
          ("completed", "cancelled", "failed")[i % 3]) for i in range(5000)],
    )
    con.commit()
    con.close()
    cursor = sessions.page(store, limit=4000)[0][-1].cursor
    for label, call in (("daily", lambda: stats.daily(store)), ("page", lambda: sessions.page(store)),
                        ("page after cursor", lambda: sessions.page(store, after=cursor)),
                        ("count", lambda: sessions.count(store, sessions.Filter(status="failed")))):
        start = time.perf_counter()
        call()
        assert time.perf_counter() - start < 0.5, label  # generous: real figures are a few milliseconds


@pytest.mark.parametrize("mode", ["light", "dark", "system"])
def test_dashboard_and_history_render_in_every_theme(app, tmp_path, monkeypatch, mode) -> None:
    monkeypatch.setattr(window_mod, "_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setitem(sys.modules, "dbus", None)
    monkeypatch.setattr(window_mod.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(window_mod.sd, "query_devices", lambda: [])
    store = HistoryStore(tmp_path / "history.db")
    now = datetime.now(timezone.utc)
    for i in range(12):
        assert store.add(SessionRecord(f"{i:032d}", now - timedelta(days=i), now.astimezone().date() -
                                       timedelta(days=i), 4000 + i * 900, i * 11, i * 55,
                                       ("completed", "cancelled", "failed")[i % 3]))
    apply_theme(app, mode)
    win = SettingsWindow(Settings(), status=RuntimeStatus(), history=store)
    win.resize(860, 760)
    win.show()
    for row in (0, 1):
        win._nav.setCurrentRow(row)
        for _ in range(3):
            QApplication.processEvents()
        assert not win.grab().isNull() and win._stack.currentWidget().isVisible()
    assert _cards(win._overview)[0] == f"{sum(i * 11 for i in range(12)):,}"
    assert len(_history_rows(win._history)) == 12


def test_the_selection_stays_on_the_same_dictation_when_the_list_reloads(app, tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    for i in range(3):
        assert store.add(SessionRecord(f"{i:032d}", BASE + timedelta(days=i), (BASE + timedelta(days=i)).date(),
                                       1000, i + 1, 5 * (i + 1), "completed"))
    page = HistoryPage(store, RuntimeStatus())
    page.refresh()
    page._list.setCurrentItem(page._list.topLevelItem(1))                   # the middle one (id 1)
    assert page._facts["Session"].text() == f"{1:032d}"
    assert store.add(SessionRecord(f"{9:032d}", BASE + timedelta(days=30), date(2026, 12, 10), 1000, 1, 5,
                                   "completed"))                            # a newer dictation shifts every row down
    page.refresh()
    assert page._facts["Session"].text() == f"{1:032d}"                     # still the same session, not the same row
    assert page._list.currentItem() is page._list.topLevelItem(2)

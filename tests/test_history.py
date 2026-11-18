"""Phase B4: the History page. Query layer (filters, sort, keyset paging) and the Qt page on top of it.

Rows are written with the store. Nothing here can show a transcript because none is ever stored.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from budgie_voice.analytics import sessions  # noqa: E402
from budgie_voice.analytics.sessions import Filter  # noqa: E402
from budgie_voice.analytics.store import HistoryStore, SessionRecord  # noqa: E402
from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.settings_ui import window as window_mod  # noqa: E402
from budgie_voice.settings_ui.history import HistoryPage  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus  # noqa: E402

BASE = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
STATUSES = ("completed", "cancelled", "failed")


def _add(store: HistoryStore, n: int, started: datetime, words: int = 3, ms: int = 1500,
         status: str = "completed", day: date | None = None) -> None:
    assert store.add(SessionRecord(f"{n:032d}", started, day or started.date(), ms, words, words * 5, status))


@pytest.fixture()
def store(tmp_path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


def _seven(store: HistoryStore) -> HistoryStore:
    """Row i starts i days after BASE, with words i+1 and a rotating status: 10 Nov (0) .. 16 Nov (6)."""
    for i in range(7):
        _add(store, i, BASE + timedelta(days=i), words=i + 1, status=STATUSES[i % 3])
    return store


def _ids(rows) -> list[int]:
    return [int(r.id) for r in rows]


# ---------------------------------------------------------------------------
# Query layer
# ---------------------------------------------------------------------------

def test_sort_newest_and_oldest_first(store) -> None:
    _seven(store)
    assert _ids(sessions.page(store)[0]) == [6, 5, 4, 3, 2, 1, 0]
    assert _ids(sessions.page(store, newest_first=False)[0]) == [0, 1, 2, 3, 4, 5, 6]


def test_rows_with_the_same_start_are_ordered_by_id(store) -> None:
    for n in (1, 3, 2):
        _add(store, n, BASE)
    assert _ids(sessions.page(store)[0]) == [3, 2, 1]
    assert _ids(sessions.page(store, newest_first=False)[0]) == [1, 2, 3]


@pytest.mark.parametrize("newest_first", [True, False])
def test_keyset_paging_visits_every_row_once(store, newest_first) -> None:
    for i in range(8):
        _add(store, i, BASE + timedelta(hours=i // 2))  # pairs share a start time: ties cross page edges
    seen, flags, cursor = [], [], None
    while True:
        rows, more = sessions.page(store, newest_first=newest_first, after=cursor, limit=3)
        seen += _ids(rows)
        flags.append(more)
        if not more:
            break
        cursor = rows[-1].cursor
    assert sorted(seen) == list(range(8)) and len(seen) == 8  # none skipped, none repeated
    assert flags == [True, True, False]
    assert seen == (sorted(seen, reverse=True) if newest_first else sorted(seen))


def test_a_dictation_saved_between_pages_shifts_nothing(store) -> None:
    for i in range(6):
        _add(store, i, BASE + timedelta(days=i))
    first, more = sessions.page(store, limit=3)
    assert _ids(first) == [5, 4, 3] and more
    _add(store, 99, BASE + timedelta(days=30))  # a new dictation, newer than everything
    second, more = sessions.page(store, after=first[-1].cursor, limit=3)
    assert _ids(second) == [2, 1, 0] and not more  # offset paging would have repeated row 3


def test_filters_are_inclusive_and_combine(store) -> None:
    _seven(store)  # 10 Nov .. 16 Nov
    rows, _ = sessions.page(store, Filter(since=date(2026, 11, 12), until=date(2026, 11, 14)))
    assert _ids(rows) == [4, 3, 2]  # both ends included
    assert _ids(sessions.page(store, Filter(since=date(2026, 11, 15)))[0]) == [6, 5]
    assert _ids(sessions.page(store, Filter(until=date(2026, 11, 10)))[0]) == [0]
    assert _ids(sessions.page(store, Filter(status="cancelled"))[0]) == [4, 1]
    both = Filter(since=date(2026, 11, 11), until=date(2026, 11, 15), status="failed")
    assert _ids(sessions.page(store, both)[0]) == [5, 2]
    assert sessions.page(store, Filter(since=date(2026, 11, 16), until=date(2026, 11, 12)))[0] == []


def test_filters_and_cursor_work_together(store) -> None:
    _seven(store)
    flt = Filter(since=date(2026, 11, 11), status="completed")  # rows 3 and 6
    first, more = sessions.page(store, flt, limit=1)
    assert _ids(first) == [6] and more
    assert _ids(sessions.page(store, flt, after=first[0].cursor, limit=1)[0]) == [3]


def test_filtering_uses_the_saved_local_day(store) -> None:
    started = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)  # 19 Nov in UTC+14
    _add(store, 1, started, day=date(2026, 11, 19))
    assert _ids(sessions.page(store, Filter(since=date(2026, 11, 19), until=date(2026, 11, 19)))[0]) == [1]
    assert sessions.page(store, Filter(until=date(2026, 11, 18)))[0] == []


def test_count_follows_the_filter(store) -> None:
    _seven(store)
    assert sessions.count(store) == 7
    assert sessions.count(store, Filter(status="completed")) == 3
    assert sessions.count(store, Filter(since=date(2030, 1, 1))) == 0


def test_a_hostile_filter_value_is_just_a_value(store) -> None:
    _seven(store)
    assert sessions.page(store, Filter(status="x'; DROP TABLE dictation_sessions; --"))[0] == []
    assert sessions.count(store) == 7  # the table is intact


def test_unavailable_history_is_empty_not_an_error(tmp_path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    broken = HistoryStore(blocker / "h.db")
    assert sessions.page(broken) == ([], False) and sessions.count(broken) == 0


def test_an_unreadable_row_is_skipped_not_fatal(store) -> None:
    _seven(store)
    con = sqlite3.connect(store.path)
    con.execute("INSERT INTO dictation_sessions VALUES ('bad', 'garbage', '2026-11-12', 1, 1, 1, 'completed')")
    con.commit()
    con.close()
    rows = sessions.page(store, limit=50)[0]
    assert all(r.id != "bad" for r in rows) and len(rows) == 7  # no crash; the bad row is dropped


def test_a_sessions_own_speed() -> None:
    row = sessions.SessionRow("a", BASE, BASE.date(), 30_000, 60, 300, "completed", ("", ""))
    assert row.wpm == pytest.approx(120)
    assert sessions.SessionRow("a", BASE, BASE.date(), 30_000, 0, 0, "completed", ("", "")).wpm is None
    assert sessions.SessionRow("a", BASE, BASE.date(), 0, 5, 25, "completed", ("", "")).wpm is None


def test_a_completed_session_that_typed_nothing_says_so(app, store) -> None:
    _add(store, 1, BASE, words=0)
    _add(store, 2, BASE + timedelta(days=1), words=0, status="cancelled")
    _add(store, 3, BASE + timedelta(days=2), words=4)
    page = _page(store)
    assert [r[5] for r in _table(page)] == ["Completed", "Cancelled", "Completed, nothing typed"]  # newest first
    page._list.setCurrentItem(page._list.topLevelItem(2))
    assert page._facts["Result"].text() == "Completed, nothing typed"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def _page(store, status=None) -> HistoryPage:
    page = HistoryPage(store, status or RuntimeStatus())
    page.refresh()
    return page


def _table(page: HistoryPage) -> list[list[str]]:
    return [[page._list.topLevelItem(i).text(c) for c in range(6)] for i in range(page._list.topLevelItemCount())]


def _words(page: HistoryPage) -> list[str]:
    return [r[3] for r in _table(page)]


def test_no_store_says_it_is_not_available_here(app) -> None:
    page = _page(None)
    assert page._controls.isHidden() and not page._empty.isHidden()
    assert "not available here" in page._empty_title.text()


def test_an_unusable_history_file_is_explained(app, tmp_path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    page = _page(HistoryStore(blocker / "h.db"))
    assert page._empty_title.text() == "History is unavailable"
    assert "not affected" in page._empty_text.text()  # dictation itself carries on


def test_empty_history_explains_what_will_appear(app, store) -> None:
    page = _page(store)
    assert page._empty_title.text() == "No dictation history yet"
    assert "will appear here" in page._empty_text.text() and page._controls.isHidden()


def test_rows_show_the_saved_metadata_newest_first(app, store) -> None:
    _seven(store)
    page = _page(store)
    table = _table(page)
    assert [r[3] for r in table] == ["7", "6", "5", "4", "3", "2", "1"]       # words, newest first
    assert table[0][0] == "16 Nov 2026" and table[0][2] == "1.5 s"
    assert table[0][4] == "35" and table[0][5] == "Completed"                  # 7 words * 5 chars
    assert [r[5] for r in table[:3]] == ["Completed", "Failed", "Cancelled"]   # row 6, 5, 4
    assert page._summary.text() == "Showing 7 of 7 dictations"
    assert page._more_btn.isHidden() and page._empty.isHidden()


def test_load_more_appends_the_next_page_and_keeps_the_selection(app, store) -> None:
    for i in range(60):
        _add(store, i, BASE + timedelta(minutes=i), words=i + 1)
    page = _page(store)
    assert len(_table(page)) == 50 and not page._more_btn.isHidden()
    assert page._summary.text() == "Showing 50 of 60 dictations"
    page._list.setCurrentItem(page._list.topLevelItem(2))
    chosen = page._facts["Session"].text()
    page._more_btn.click()
    assert len(_table(page)) == 60 and page._more_btn.isHidden()
    assert len({r[3] for r in _table(page)}) == 60                             # no repeats
    assert page._facts["Session"].text() == chosen                             # still on the same dictation


def test_sort_filter_and_dates_reload_the_list(app, store) -> None:
    _seven(store)
    page = _page(store)
    page._sort.setCurrentIndex(1)
    assert _words(page) == ["1", "2", "3", "4", "5", "6", "7"]
    page._status.setCurrentIndex(page._status.findData("cancelled"))
    assert _words(page) == ["2", "5"]
    page._from.setText("2026-11-12")
    page._from.editingFinished.emit()
    assert _words(page) == ["5"]
    assert page._summary.text() == "Showing 1 of 1 dictations"


def test_an_unreadable_date_is_ignored_and_explained(app, store) -> None:
    _seven(store)
    page = _page(store)
    page._from.setText("last tuesday")
    page._from.editingFinished.emit()
    assert len(_table(page)) == 7 and not page._hint.isHidden()
    page._from.setText("2026-11-15")
    page._from.editingFinished.emit()
    assert len(_table(page)) == 2 and page._hint.isHidden()


def test_a_filter_with_no_match_keeps_the_controls_and_says_so(app, store) -> None:
    _add(store, 1, BASE)  # completed only
    page = _page(store)
    page._status.setCurrentIndex(page._status.findData("failed"))
    assert _table(page) == [] and page._summary.text() == "No dictations match these filters."
    assert not page._controls.isHidden() and page._empty.isHidden()  # not the "no history yet" state


def test_clear_restores_everything(app, store) -> None:
    _seven(store)
    page = _page(store)
    page._from.setText("2026-11-15")
    page._status.setCurrentIndex(1)
    page._sort.setCurrentIndex(1)
    page._clear()
    assert page._from.text() == "" and page._status.currentIndex() == 0 and page._sort.currentIndex() == 0
    assert _words(page) == ["7", "6", "5", "4", "3", "2", "1"]


def test_selecting_a_row_shows_only_metadata(app, store) -> None:
    _add(store, 1, BASE, words=60, ms=30_000)
    _add(store, 2, BASE + timedelta(days=1), words=0, ms=2_000, status="cancelled")
    page = _page(store)
    assert not page._detail_hint.isHidden() and page._facts["Words"].text() == "—"  # nothing selected yet
    page._list.setCurrentItem(page._list.topLevelItem(1))  # the 60-word, 30 s one
    f = {k: v.text() for k, v in page._facts.items()}
    assert (f["Words"], f["Characters"], f["Result"], f["Session"]) == ("60", "300", "Completed", f"{1:032d}")
    assert f["Duration"] == "30.0 s" and f["Speed"] == "120 words per minute"
    assert "2026" in f["Started"] and page._detail_hint.isHidden()
    page._list.setCurrentItem(page._list.topLevelItem(0))  # words 0
    assert page._facts["Speed"].text() == "—" and page._facts["Result"].text() == "Cancelled"
    assert set(page._facts) == {"Started", "Duration", "Words", "Characters", "Speed", "Result", "Session"}


def test_the_page_reloads_when_a_dictation_ends_but_only_while_shown(app, store) -> None:
    status = RuntimeStatus()
    page = _page(store, status)               # empty store
    _add(store, 1, BASE)
    status.on_session_state("recording", 0)
    status.on_session_state("completed", 5)
    assert _table(page) == []                 # hidden page: untouched
    page.show()
    status.on_session_state("recording", 0)
    status.on_session_state("completed", 5)
    assert len(_table(page)) == 1             # visible page: reloaded, and the empty state is gone
    assert page._empty.isHidden()


def test_opening_the_page_from_the_navigation_loads_it(app, store, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(window_mod, "_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setitem(sys.modules, "dbus", None)
    monkeypatch.setattr(window_mod.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(window_mod.sd, "query_devices", lambda: [])
    _seven(store)
    win = SettingsWindow(Settings(), status=RuntimeStatus(), history=store)
    assert win._history._rows == []           # not loaded until opened
    win._nav.setCurrentRow(1)
    assert win._nav.item(1).text() == "Dictation History" and len(win._history._rows) == 7
    assert win._stack.currentWidget().widget() is win._history  # inside its scroll area


def test_the_page_has_no_destructive_controls(app, store) -> None:
    _seven(store)
    labels = {b.text().replace("&", "").lower() for b in _page(store).findChildren(QPushButton)}
    assert labels == {"clear", "load more"}   # "Clear" resets the filters; nothing here deletes history

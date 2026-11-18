"""Phase B3: statistics service and the dashboard bound to real saved history.

Rows are written with the store and checked against hand-computed numbers; deletions go through plain
sqlite3 to prove nothing is cached.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from budgie_voice.analytics import stats  # noqa: E402
from budgie_voice.analytics.recorder import SessionRecorder  # noqa: E402
from budgie_voice.analytics.store import HistoryStore, SessionRecord  # noqa: E402
from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.settings_ui import window as window_mod  # noqa: E402
from budgie_voice.settings_ui.overview import OverviewPage, _fmt_time  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 18)


def _add(store: HistoryStore, n: int, day: date, words: int, ms: int, status: str = "completed",
         started: datetime = NOW) -> None:
    assert store.add(SessionRecord(f"{n:032d}", started, day, ms, words, words * 5, status))


@pytest.fixture()
def store(tmp_path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


@pytest.fixture()
def mixed(store) -> HistoryStore:
    """Six sessions that exercise every rule in stats.py. Hand-computed expectations below."""
    d1, d2, d3 = TODAY - timedelta(days=2), TODAY - timedelta(days=1), TODAY
    _add(store, 1, d1, 10, 6_000)                     # completed, 100 wpm on its own
    _add(store, 2, d1, 100, 120_000)                  # completed,  50 wpm on its own
    _add(store, 3, d2, 4, 3_000, "cancelled")         # words count; not a session; counts towards WPM
    _add(store, 4, d2, 0, 5_000)                      # empty dictation / cancelled preview: nothing
    _add(store, 5, d3, 0, 1_000, "failed")            # nothing typed
    _add(store, 6, d3, 3, 0)                          # completed but no duration: words and session only
    return store


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_empty_history_is_empty_not_zero_rows(store) -> None:
    assert stats.daily(store) == {}
    t = stats.total(stats.daily(store).values())
    assert t == stats.Stats() and t.wpm is None


def test_all_time_totals_follow_the_definitions(mixed) -> None:
    t = stats.total(stats.daily(mixed).values())
    assert t.words == 10 + 100 + 4 + 3                 # every saved session
    assert t.sessions == 3                             # completed AND typed words: #1, #2, #6
    assert t.time_ms == 6_000 + 120_000 + 0            # recording time of those same sessions
    assert (t.rate_words, t.rate_ms) == (114, 129_000)  # sessions with words and a duration: #1, #2, #3


def test_wpm_is_a_ratio_of_totals_never_a_mean_of_rates(mixed) -> None:
    wpm = stats.total(stats.daily(mixed).values()).wpm
    assert wpm == pytest.approx(114 / (129 / 60))                       # 53.02
    per_session_mean = (10 / 0.1 + 100 / 2 + 4 / 0.05) / 3              # 76.67: the wrong way
    assert abs(wpm - per_session_mean) > 20


def test_wpm_is_none_without_recording_time(store) -> None:
    _add(store, 1, TODAY, 5, 0)
    assert stats.total(stats.daily(store).values()).wpm is None


def test_daily_aggregation(mixed) -> None:
    got = stats.daily(mixed)
    assert got == {
        TODAY - timedelta(days=2): stats.Stats(110, 2, 126_000, 110, 126_000),
        TODAY - timedelta(days=1): stats.Stats(4, 0, 0, 4, 3_000),
        TODAY: stats.Stats(3, 1, 0, 0, 0),
    }


def test_seven_and_thirty_day_ranges_are_inclusive_of_today(store) -> None:
    for n, back in enumerate((0, 6, 7, 29, 30)):
        _add(store, n, TODAY - timedelta(days=back), 10 * (n + 1), 1_000)
    for days, expected in ((7, {TODAY, TODAY - timedelta(days=6)}),
                           (30, {TODAY, TODAY - timedelta(days=6), TODAY - timedelta(days=7),
                                 TODAY - timedelta(days=29)})):
        since, until = stats.last_days(days, TODAY)
        assert set(stats.daily(store, since, until)) == expected
    assert len(stats.daily(store)) == 5  # all time


def test_the_day_is_the_stored_local_day_not_the_utc_day(store) -> None:
    # Same UTC date, two different local days (a user in UTC+14 around local midnight).
    _add(store, 1, date(2026, 11, 18), 5, 1_000, started=datetime(2026, 11, 18, 9, 59, tzinfo=timezone.utc))
    _add(store, 2, date(2026, 11, 19), 7, 1_000, started=datetime(2026, 11, 18, 10, 1, tzinfo=timezone.utc))
    assert {d: s.words for d, s in stats.daily(store).items()} == {date(2026, 11, 18): 5, date(2026, 11, 19): 7}


def test_deleting_a_session_recalculates_everything(mixed) -> None:
    before = stats.total(stats.daily(mixed).values())
    con = sqlite3.connect(mixed.path)
    con.execute("DELETE FROM dictation_sessions WHERE id = ?", (f"{2:032d}",))  # the 100-word session
    con.commit()
    con.close()
    after = stats.total(stats.daily(mixed).values())
    assert (before.words, after.words) == (117, 17) and after.sessions == 2
    assert after.wpm == pytest.approx(14 / (9 / 60))


def test_unavailable_history_reads_as_empty_without_raising(tmp_path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert stats.daily(HistoryStore(blocker / "h.db")) == {}


def test_a_failing_query_reads_as_empty_without_raising(store) -> None:
    store.path.unlink()
    for ext in ("-wal", "-shm"):
        Path(f"{store.path}{ext}").unlink(missing_ok=True)
    store.path.mkdir()
    assert stats.daily(store) == {}


# ---------------------------------------------------------------------------
# Dashboard bound to history
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def _page(store, status=None) -> OverviewPage:
    return OverviewPage(Settings(), status or RuntimeStatus(), lambda d: None, store)


def _cards(page: OverviewPage) -> list[str]:
    return [c.value() for c in page._cards]


def test_cards_show_the_saved_totals(app, mixed) -> None:
    page = _page(mixed)
    assert _cards(page) == ["117", "3", "53", "2 min 6 s"]
    assert "All time" in page._stats_note.text()


def test_an_empty_history_is_an_honest_empty_state(app, store) -> None:
    page = _page(store)
    assert _cards(page) == ["—"] * 4 and page._chart.is_empty()
    assert "No dictations recorded yet" in page._stats_note.text()


def test_saved_sessions_with_no_words_show_real_zeros_not_dashes(app, store) -> None:
    _add(store, 1, TODAY, 0, 4_000)  # a cancelled preview: it happened, nothing was typed
    assert _cards(_page(store)) == ["0", "0", "—", "—"]


def test_unavailable_history_and_standalone_settings_say_so(app, tmp_path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    broken = _page(HistoryStore(blocker / "h.db"))
    assert _cards(broken) == ["—"] * 4 and "unavailable" in broken._stats_note.text()
    standalone = _page(None)
    assert _cards(standalone) == ["—"] * 4 and "not available in this window" in standalone._stats_note.text()


def test_chart_draws_the_saved_words_and_ranges_filter_them(app, store) -> None:
    today = date.today()
    _add(store, 1, today, 40, 1_000)
    _add(store, 2, today - timedelta(days=10), 25, 1_000)   # inside 30 days, outside 7
    _add(store, 3, today - timedelta(days=45), 999, 1_000)  # outside both
    chart = _page(store)._chart
    assert sum(v for _, v in chart.series()) == 40          # default range: 7 days
    chart.set_days(30)
    assert sum(v for _, v in chart.series()) == 65
    assert chart.tooltip_at(29) == f"{today:%a %d %b}: 40 words"


def test_the_page_sees_a_dictation_the_moment_it_is_saved(app, store) -> None:
    """The wiring order main.py uses: recorder first, then the status callback that refreshes the page."""
    status = RuntimeStatus()
    page = _page(store, status)
    assert _cards(page)[0] == "—"
    rec = SessionRecorder(store)
    for state, chars, words in (("recording", 0, 0), ("finalizing", 0, 0), ("completed", 21, 5)):
        rec.on_state(state, chars, words)
        status.on_session_state(state, chars)
    assert _cards(page)[:2] == ["5", "1"]
    assert "All time" in page._stats_note.text()


def test_the_settings_window_hands_its_history_to_the_overview(app, store, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(window_mod, "_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setitem(sys.modules, "dbus", None)
    monkeypatch.setattr(window_mod.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(window_mod.sd, "query_devices", lambda: [])
    _add(store, 1, TODAY, 8, 2_000)
    win = SettingsWindow(Settings(), status=RuntimeStatus(), history=store)
    assert _cards(win._overview)[:2] == ["8", "1"]


@pytest.mark.parametrize(("ms", "text"), [(0, "0 s"), (42_000, "42 s"), (245_000, "4 min 5 s"),
                                         (4_980_000, "1 h 23 min")])
def test_time_formatting(ms, text) -> None:
    assert _fmt_time(ms).replace(" ", " ") == text
    assert _fmt_time(ms).count(" ") == text.count(" ") // 2  # only the breaks between units are real spaces

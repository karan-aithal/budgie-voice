"""Overview dashboard (Phase A1): readiness, honest empty states, chart, nav, themes, resizing.

The dashboard shows no productivity numbers yet: every test here would fail if one were invented.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea  # noqa: E402

from budgie_voice.analytics.store import HistoryStore  # noqa: E402
from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.settings_ui import window as window_mod  # noqa: E402
from budgie_voice.settings_ui.history import HistoryPage  # noqa: E402
from budgie_voice.settings_ui.overview import OverviewPage, _greeting, _readiness  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui.chart import BarChart, _nice_top  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus, Status  # noqa: E402
from budgie_voice.ui.theme import apply_theme  # noqa: E402


@pytest.fixture()
def app():
    a = QApplication.instance() or QApplication([])
    saved = a.palette()
    yield a
    a.setPalette(saved)


@pytest.fixture()
def win(app, tmp_path, monkeypatch):
    monkeypatch.setattr(window_mod, "_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setitem(sys.modules, "dbus", None)
    monkeypatch.setattr(window_mod.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(window_mod.sd, "query_devices", lambda: [])
    return SettingsWindow(Settings(), status=RuntimeStatus())


class _Backend:
    is_ready = True


# -- readiness ---------------------------------------------------------------

@pytest.mark.parametrize(("asr", "mic", "dot", "word"), [
    (Status.READY, Status.READY, Status.READY, "Ready"),
    (Status.READY, Status.UNKNOWN, Status.READY, "Ready"),          # Auto mic: chosen at start
    (Status.READY, Status.UNAVAILABLE, Status.UNAVAILABLE, "Not ready"),
    (Status.UNAVAILABLE, Status.READY, Status.UNAVAILABLE, "Not ready"),
    (Status.CONNECTING, Status.READY, Status.CONNECTING, "Loading"),
    (Status.UNKNOWN, Status.READY, Status.UNKNOWN, "Unknown"),
])
def test_readiness(asr, mic, dot, word) -> None:
    got_dot, got_word, sentence = _readiness(asr, mic)
    assert (got_dot, got_word) == (dot, word) and sentence


def test_greeting_follows_the_hour() -> None:
    assert [_greeting(h) for h in (6, 12, 17, 18, 23)] == [
        "Good morning", "Good afternoon", "Good afternoon", "Good evening", "Good evening"]


def test_hero_reports_the_running_runtime(app) -> None:
    st = RuntimeStatus()
    page = OverviewPage(Settings(), st, lambda d: None)
    page.refresh()
    assert page._chip._label.text() == "Unknown"  # nothing reported yet: not claimed ready
    st.set_asr(_Backend(), "faster_whisper", "small")
    assert page._chip._label.text() == "Ready" and "ready" in page._hero_text.text()


def test_hotkey_hint_comes_from_settings(app) -> None:
    s = Settings()
    s.activation.hotkey, s.activation.mode = "ctrl+alt+d", "toggle"
    page = OverviewPage(s, RuntimeStatus(), lambda d: None)
    page.refresh()
    assert page._hint.text() == "Press ctrl+alt+d to dictate"
    s.activation.mode = "push_to_hold"
    page.refresh()
    assert page._hint.text() == "Hold ctrl+alt+d to dictate"
    assert not any(b.text().lower().startswith(("start", "dictate")) for b in page.findChildren(QPushButton))


# -- honest empty states -----------------------------------------------------

def test_productivity_cards_invent_nothing(app) -> None:
    page = OverviewPage(Settings(), RuntimeStatus(), lambda d: None)
    assert len(page._cards) == 4
    assert [c.value() for c in page._cards] == ["—"] * 4


def test_recent_list_is_capped_at_five_newest_first(app) -> None:
    st = RuntimeStatus()
    page = OverviewPage(Settings(), st, lambda d: None)
    for n in range(8):
        st.on_session_state("recording", 0)
        st.on_session_state("completed", n)
    assert page._activity.topLevelItemCount() == 5
    assert [page._activity.topLevelItem(i).text(3) for i in range(5)] == ["7", "6", "5", "4", "3"]


# -- chart -------------------------------------------------------------------

def _chart(days: int = 7, data=None) -> BarChart:
    c = BarChart()
    c._today = date(2026, 11, 18)
    c.set_days(days)
    c.set_data(data or {})
    c.resize(400, 200)
    return c


def test_chart_series_covers_the_range_oldest_first(app) -> None:
    c = _chart(7, {date(2026, 11, 18): 120, date(2026, 11, 12): 30, date(2026, 10, 25): 999, date(2026, 9, 1): 7})
    s = c.series()
    assert s[0] == (date(2026, 11, 12), 30) and s[-1] == (date(2026, 11, 18), 120)
    assert sum(v for _, v in s) == 150  # 25 Oct is outside the 7-day window
    c.set_days(30)
    assert len(c.series()) == 30 and sum(v for _, v in c.series()) == 150 + 999  # 1 Sep is outside both


def test_chart_empty_state_and_tooltip(app) -> None:
    assert _chart().is_empty()
    assert _chart(7, {date(2026, 10, 1): 5}).is_empty()  # data exists, none in the window
    c = _chart(7, {date(2026, 11, 18): 120})
    assert not c.is_empty()
    assert c.tooltip_at(6) == "Wed 18 Nov: 120 words"
    assert c.index_at(c._plot().right() - 1) == 6 and c.index_at(1) is None


def test_chart_nice_axis_top() -> None:
    assert [_nice_top(v) for v in (0, 1, 7, 120, 501)] == [2, 2, 10, 200, 1000]


def test_chart_paints_in_light_dark_and_empty(app) -> None:
    for window, text in (("#fafafa", "#202020"), ("#242424", "#f0f0f0")):
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(window))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(text))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#3584e4"))
        for data in ({}, {date(2026, 11, 18): 120, date(2026, 11, 17): 40}):
            c = _chart(30, data)
            c.setPalette(pal)
            assert not c.grab().isNull()


def test_range_buttons_drive_the_chart(app) -> None:
    page = OverviewPage(Settings(), RuntimeStatus(), lambda d: None)
    seven, thirty = [b for b in page.findChildren(QPushButton) if b.property("seg")]
    assert seven.isChecked() and page._chart._days == 7
    QTest.mouseClick(thirty, Qt.MouseButton.LeftButton)
    assert page._chart._days == 30 and thirty.isChecked() and not seven.isChecked()


# -- navigation, theme, resize -----------------------------------------------

def test_view_all_opens_dictation_history(win) -> None:
    assert win._nav.currentRow() == 0
    win._overview.view_all.emit()
    assert win._nav.item(win._nav.currentRow()).text() == "Dictation History"


def test_history_page_is_an_honest_empty_state(app, tmp_path) -> None:
    page = HistoryPage(HistoryStore(tmp_path / "history.db"), RuntimeStatus())
    page.refresh()
    texts = [lbl.text() for lbl in page.findChildren(QLabel)]
    assert "No dictation history yet" in texts
    assert any("will appear here" in t for t in texts)


def test_only_overview_and_history_scroll(win) -> None:
    scrolls = {win._nav.item(i).text(): isinstance(win._stack.widget(i), QScrollArea)
               for i in range(win._nav.count())}
    assert [name for name, scrolls_ in scrolls.items() if scrolls_] == ["Overview", "Dictation History"]
    for i in range(win._nav.count()):
        if not scrolls[win._nav.item(i).text()]:
            assert win._stack.widget(i).findChildren(QScrollArea) == []  # no scroll bar hiding in a form either


def test_audio_and_inference_comes_before_activation(win) -> None:
    names = [win._nav.item(i).text() for i in range(win._nav.count())]
    assert names.index("Audio & Inference") < names.index("Activation")
    assert names[:2] == ["Overview", "Dictation History"]


def test_no_page_without_a_scroll_bar_is_clipped_at_the_minimum_size(win) -> None:
    win.resize(win.minimumSizeHint())
    win.show()
    for row in range(win._nav.count()):
        win._nav.setCurrentRow(row)
        for _ in range(3):
            QApplication.processEvents()
        page = win._stack.currentWidget()
        assert page.minimumSizeHint().height() <= page.height(), win._nav.item(row).text()
        assert page.minimumSizeHint().width() <= page.width(), win._nav.item(row).text()


def test_cards_reflow_with_the_width(win) -> None:
    page = win._overview
    page._reflow(2)
    assert {page._grid.getItemPosition(i)[:2] for i in range(4)} == {(0, 0), (0, 1), (1, 0), (1, 1)}
    page._reflow(4)
    assert {page._grid.getItemPosition(i)[:2] for i in range(4)} == {(0, 0), (0, 1), (0, 2), (0, 3)}


@pytest.mark.parametrize("mode", ["light", "dark", "system"])
def test_dashboard_renders_in_every_theme(win, mode) -> None:
    apply_theme(QApplication.instance(), mode)
    win.resize(780, 540)
    win.show()
    for _ in range(3):
        QApplication.processEvents()
    assert not win.grab().isNull() and win._overview.isVisible()


def test_dashboard_fits_at_the_minimum_window_size(win) -> None:
    win.resize(win.minimumSize())
    win.show()
    for _ in range(3):
        QApplication.processEvents()
    assert win._overview._cols == 2  # narrow: 2 x 2
    win.resize(780, 540)  # the default size has room for four cards in a row
    for _ in range(3):
        QApplication.processEvents()
    assert win._overview._cols == 4
    win.resize(win.minimumSize())  # and back: it must collapse again, not pin the window wide
    for _ in range(3):
        QApplication.processEvents()
    assert win._overview._cols == 2
    assert win._stack.minimumSizeHint().width() <= win.minimumWidth() - win._nav.width() - 1

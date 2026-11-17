"""Visual polish: icons, Ubuntu font fallback, the Switch, theme following, and that the scoped
style never reaches the recorder overlay or the transcript preview (they must look identical
before and after Settings / the wizard exist)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFontInfo, QPalette  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.indicator.preview_widget import PreviewWidget  # noqa: E402
from budgie_voice.indicator.recorder_widget import FloatingRecorderWidget  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.ui import icons  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus, Status  # noqa: E402
from budgie_voice.ui.style import R_CONTROL, R_PANEL, build_qss, ui_font  # noqa: E402
from budgie_voice.ui.theme import status_color  # noqa: E402
from budgie_voice.ui.widgets import StatusRow, Switch  # noqa: E402


@pytest.fixture()
def app():
    a = QApplication.instance() or QApplication([])
    saved = a.palette()
    yield a
    a.setPalette(saved)


def _palette(window: str, text: str, highlight: str = "#3584e4") -> QPalette:
    pal = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, window), (QPalette.ColorRole.Base, window),
                         (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Highlight, highlight)):
        pal.setColor(role, QColor(colour))
    return pal


def test_every_icon_renders_with_visible_pixels(app) -> None:
    assert len(icons._BODY) >= 20
    for name in icons._BODY:
        image = icons.pixmap(name, QColor("#222222"), 16).toImage()
        assert any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height())), name


def test_icon_colour_is_the_one_asked_for(app) -> None:
    image = icons.pixmap("check", QColor("#ff0000"), 16).toImage()
    solid = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
             if image.pixelColor(x, y).alpha() == 255]
    assert solid and all(c.red() > 200 and c.green() < 40 for c in solid)


def test_font_prefers_ubuntu_and_always_resolves(app) -> None:
    font = ui_font(13)
    assert font.families()[0] == "Ubuntu" and font.pixelSize() == 13
    assert QFontInfo(font).family()  # something real is chosen even without Ubuntu installed


def test_switch_is_a_working_checkbox(app) -> None:
    sw = Switch("Option")
    seen: list[bool] = []
    sw.toggled.connect(seen.append)
    sw.show()
    assert not sw.isChecked()
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton, pos=QPoint(sw.width() - 3, sw.height() // 2))  # on the label
    assert sw.isChecked() and seen == [True]
    QTest.keyClick(sw, Qt.Key.Key_Space)
    assert not sw.isChecked() and seen == [True, False]
    sw.setEnabled(False)
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton)
    assert not sw.isChecked()
    sw.setChecked(True)  # programmatic, even disabled
    assert sw.isChecked()
    assert sw.grab().width() == sw.width()  # paints without error in every state


def test_stylesheet_uses_the_shared_radii_and_follows_the_palette(app) -> None:
    app.setPalette(_palette("#f6f5f4", "#000000"))
    light = build_qss()
    assert f"border-radius: {R_CONTROL}px" in light and f"border-radius: {R_PANEL}px" in light
    app.setPalette(_palette("#242424", "#ffffff"))
    assert build_qss() != light


def test_settings_window_restyles_live_when_the_palette_changes(app) -> None:
    app.setPalette(_palette("#f6f5f4", "#000000"))
    w = SettingsWindow(Settings(), status=RuntimeStatus())
    w.show()
    before = w.styleSheet()
    app.setPalette(_palette("#242424", "#ffffff"))
    for _ in range(5):
        app.processEvents()
        QTest.qWait(10)
    assert w.styleSheet() != before


def test_settings_chrome_has_icons_and_a_primary_save(app) -> None:
    w = SettingsWindow(Settings(), status=RuntimeStatus())
    assert w._nav.count() == 6 and all(not w._nav.item(i).icon().isNull() for i in range(6))
    primary = [b for b in w.findChildren(QPushButton) if b.property("primary")]
    assert [b.text().replace("&", "") for b in primary] == ["Save"] and not primary[0].icon().isNull()
    assert all(isinstance(c, Switch) for c in (w._denoise_chk, w._speaker_chk, w._num_chk))


def test_app_icon_is_on_settings_and_wizard_only(app) -> None:
    from budgie_voice.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
    from budgie_voice.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

    w = SettingsWindow(Settings(), status=RuntimeStatus())
    wizard = EnrollmentWizard(SpeakerVerifier("/nonexistent/voiceprint.pt"))
    assert not w.windowIcon().isNull() and not wizard.windowIcon().isNull()
    assert not w.windowIcon().pixmap(32, 32).isNull()
    for win in (w, wizard):  # the icon is also drawn inside the window, not only in a title bar
        logo = win.findChild(QWidget, "brand").findChildren(QLabel)[0]
        assert not logo.pixmap().isNull()
    assert FloatingRecorderWidget(on_cancel=lambda: None).windowIcon().isNull()
    assert PreviewWidget().windowIcon().isNull()


def test_overview_status_rows_share_one_card(app) -> None:
    w = SettingsWindow(Settings(), status=RuntimeStatus())
    card = w._overview._mic.parentWidget()
    assert card.objectName() == "card"
    assert {w._overview._asr.parentWidget(), w._overview._llm.parentWidget(),
            w._overview._speaker.parentWidget()} == {card}


def test_status_colours_are_distinct_per_meaning(app) -> None:
    for dark in (False, True):
        names = {s: status_color(s, dark).name() for s in
                 (Status.READY, Status.CONNECTING, Status.UNAVAILABLE, Status.ERROR)}
        assert len(set(names.values())) == 4, names
    assert StatusRow("x", Status.READY, "ok")._dot.width() == 10  # one dot size everywhere


def test_recorder_and_preview_are_untouched_by_the_scoped_style(app) -> None:
    recorder = FloatingRecorderWidget(on_cancel=lambda: None)
    preview = PreviewWidget()

    def snapshot() -> tuple:
        return (recorder.grab().toImage(), preview.grab().toImage(), recorder.font(), preview.font(),
                recorder.styleSheet(), preview.styleSheet(), recorder.size(), preview.size(), app.font(),
                app.styleSheet())

    before = snapshot()
    from budgie_voice.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
    from budgie_voice.speaker.verifier import SpeakerVerifier  # noqa: PLC0415

    w = SettingsWindow(Settings(), status=RuntimeStatus())
    w.show()
    wizard = EnrollmentWizard(SpeakerVerifier("/nonexistent/voiceprint.pt"))
    wizard.show()
    for _ in range(5):
        app.processEvents()
    assert before == snapshot()
    assert app.styleSheet() == ""  # nothing app-wide, ever

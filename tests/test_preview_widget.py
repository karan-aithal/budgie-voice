"""PreviewWidget translucency — background see-through, text not.

Renders offscreen onto a transparent canvas and inspects real pixel alpha, since
that's the only thing that actually proves the effect (a stylesheet background
on a translucent QWidget silently fails to paint, which is how this was built).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from myna.indicator.preview_widget import (  # noqa: E402
    _FONT_PX,
    _HEIGHT,
    _PANEL_ALPHA,
    _WIDTH,
    PreviewWidget,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _render() -> "QColor":
    widget = PreviewWidget()
    widget.show_preview("Hello world preview text", lambda r: None)
    QApplication.processEvents()

    px = QPixmap(_WIDTH, _HEIGHT)
    px.fill(QColor(0, 0, 0, 0))  # transparent canvas: nothing painted stays alpha 0
    painter = QPainter(px)
    widget.render(painter)
    painter.end()
    return px.toImage()


def test_panel_background_is_translucent(qapp) -> None:
    img = _render()
    # Bottom margin strip is panel only — no child widget covers it.
    panel = img.pixelColor(_WIDTH // 2, _HEIGHT - 10)

    assert panel.alpha() == _PANEL_ALPHA, "panel must use the configured alpha"
    assert panel.alpha() < 255, "background must be see-through"


def test_text_stays_fully_opaque(qapp) -> None:
    img = _render()
    glyph_alphas = [
        img.pixelColor(x, y).alpha()
        for y in range(img.height())
        for x in range(img.width())
        if img.pixelColor(x, y).lightness() > 200
    ]

    assert glyph_alphas, "expected rendered glyph pixels"
    assert max(glyph_alphas) == 255, "text must not be dimmed by the translucency"


def test_no_window_opacity_used(qapp) -> None:
    """setWindowOpacity would fade the text too — the whole point is to avoid it."""
    widget = PreviewWidget()
    assert widget.windowOpacity() == 1.0


def test_edit_area_dominates_the_widget(qapp) -> None:
    """The transcript is the point of this widget — chrome must not crowd it out."""
    widget = PreviewWidget()
    widget.show_preview("some text", lambda r: None)
    QApplication.processEvents()

    edit = widget._edit.geometry()
    assert edit.height() > _HEIGHT * 0.6, "edit box should own most of the height"
    assert edit.width() > _WIDTH * 0.9, "edit box should span nearly the full width"


def test_font_size_applied_to_edit(qapp) -> None:
    """Guards the constant actually reaching the stylesheet."""
    widget = PreviewWidget()
    assert f"font-size: {_FONT_PX}px" in widget._edit.styleSheet()

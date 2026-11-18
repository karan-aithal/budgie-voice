"""Daily bar chart, painted natively: no plotting dependency.

Colours come from the palette at paint time, so light, dark and live theme changes follow. It draws
exactly the numbers it is given; with none (or all zero) in the window it says so instead.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPalette
from PyQt6.QtWidgets import QToolTip, QWidget

from budgie_voice.ui.style import mix, ui_font


def _nice_top(v: int) -> int:
    """Smallest 1/2/5 x 10^k at or above *v* (and at least 2, so the middle gridline is a whole number)."""
    mag = 10 ** (len(str(max(v, 1))) - 1)
    return max(2, next(m * mag for m in (1, 2, 5, 10) if v <= m * mag))


class BarChart(QWidget):
    """One bar per calendar day for the last ``days`` days, ending today."""

    _L, _R, _T, _B = 40, 8, 10, 24  # plot margins: y labels, right, top, x labels

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[date, int] = {}
        self._days = 7
        self._today: date | None = None  # tests pin it; otherwise the real date
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(180)
        self.setAccessibleName("Words per day")

    # -- data ------------------------------------------------------------------

    def set_data(self, daily: dict[date, int]) -> None:
        self._data = dict(daily)
        self.update()

    def set_days(self, days: int) -> None:
        self._days = days
        self.update()

    def series(self) -> list[tuple[date, int]]:
        end = self._today or date.today()
        days = [end - timedelta(days=self._days - 1 - i) for i in range(self._days)]
        return [(d, self._data.get(d, 0)) for d in days]

    def is_empty(self) -> bool:
        return not any(v for _, v in self.series())

    def tooltip_at(self, index: int) -> str:
        d, v = self.series()[index]
        return f"{d:%a %d %b}: {v} words"

    # -- geometry --------------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(self._L, self._T, max(1, self.width() - self._L - self._R),
                      max(1, self.height() - self._T - self._B))

    def index_at(self, x: float) -> int | None:
        plot = self._plot()
        i = int((x - plot.left()) // (plot.width() / self._days))
        return i if 0 <= i < self._days and plot.left() <= x <= plot.right() else None

    # -- interaction -----------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        i = None if self.is_empty() else self.index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()
        if i is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_at(i), self)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.update()
        super().leaveEvent(event)

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(ui_font(11))
        pal, role = self.palette(), QPalette.ColorRole
        muted = pal.color(role.PlaceholderText)
        if self.is_empty():
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No dictation data yet")
            return

        series, plot = self.series(), self._plot()
        top = _nice_top(max(v for _, v in series))
        grid = mix(pal.color(role.Window), pal.color(role.WindowText), 0.12)
        for tick in (0, top / 2, top):
            y = plot.bottom() - plot.height() * tick / top
            p.setPen(grid)
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, self._L - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{tick:g}")

        slot = plot.width() / len(series)
        bar_w = min(slot * 0.6, 28.0)
        hl = pal.color(role.Highlight)
        step = math.ceil(len(series) / 7)  # at most 7 date labels
        p.setPen(Qt.PenStyle.NoPen)
        for i, (d, v) in enumerate(series):
            cx = plot.left() + slot * (i + 0.5)
            if v:
                h = plot.height() * v / top
                p.setBrush(hl.lighter(115) if i == self._hover else hl)
                p.drawRoundedRect(QRectF(cx - bar_w / 2, plot.bottom() - h, bar_w, h), 4, 4)
            if i % step == 0:
                p.setPen(muted)
                p.drawText(QRectF(cx - slot * step / 2, plot.bottom() + 4, slot * step, 16),
                           Qt.AlignmentFlag.AlignCenter, f"{d.day} {d:%b}")
                p.setPen(Qt.PenStyle.NoPen)

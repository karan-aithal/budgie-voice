"""Shared building blocks: type scale, status row, card, switch.

Ubuntu font (with fallbacks) and the palette only. Hierarchy comes from weight, size and the muted
text colour. The look of buttons, inputs and frames is the scoped stylesheet in ``ui.style``.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from budgie_voice.ui.icons import pixmap
from budgie_voice.ui.status import Status
from budgie_voice.ui.style import app_icon, mix, ui_font
from budgie_voice.ui.theme import status_color

SPACING = 12   # between controls
MARGIN = 20    # page edge
_DOT = 10
_Medium = QFont.Weight.Medium


def section(text: str) -> QLabel:
    """Sub-heading inside a page."""
    label = QLabel(text)
    label.setFont(ui_font(15, _Medium))
    return label


def heading(text: str) -> QLabel:
    """Page title."""
    label = QLabel(text)
    label.setFont(ui_font(22, _Medium))
    return label


def muted(text: str) -> QLabel:
    """Secondary text: palette placeholder colour, so it adapts to light and dark."""
    label = QLabel(text)
    label.setFont(ui_font(12))
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


class _Dot(QWidget):
    """Status dot, coloured at paint time so it follows theme changes. Its box is one text line
    tall, so the circle sits on the first line's centre however long the value wraps."""

    def __init__(self, status: Status, line_height: int) -> None:
        super().__init__()
        self._status = status
        self.setFixedSize(QSize(_DOT, max(line_height, _DOT)))

    def set_status(self, status: Status) -> None:
        self._status = status
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(status_color(self._status))
        painter.drawEllipse(QRectF(0, (self.height() - _DOT) / 2, _DOT, _DOT))


class _OneLine(QLabel):
    """Never wraps: text that does not fit is cut with an ellipsis, and the full text is the tooltip.
    ``text()`` still returns the whole string."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._sync_tooltip()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        cut = self.fontMetrics().horizontalAdvance(self.text()) > self.width()
        self.setToolTip(self.text() if cut else "")

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        shown = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)


class StatusRow(QWidget):
    """``● Name   Status — detail``: one line per component. The state is also written
    out in words, so colour is never the only signal."""

    def __init__(
        self, name: str, status: Status = Status.UNKNOWN, detail: str = "", word: str | None = None,
        name_width: int = 140,
    ) -> None:
        super().__init__()
        self._name = QLabel(name)
        self._name.setFont(ui_font(13, _Medium))
        self._name.setMinimumWidth(name_width)  # default fits the longest Overview name, so values line up
        self._value = _OneLine()
        self._value.setFont(ui_font(13))
        self._dot = _Dot(status, QFontMetrics(self._value.font()).height())

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 10, 0, 10)
        row.setSpacing(SPACING)
        row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._name, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._value, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
        self.set_status(status, detail, word)

    def set_status(self, status: Status, detail: str = "", word: str | None = None) -> None:
        """*word* replaces the status name for results that are not component states ("Match")."""
        self.status = status
        self._dot.set_status(status)
        label = word or status.value
        self._value.setText(f"{label} — {detail}" if detail else label)


class Glyph(QWidget):
    """A Lucide icon in the palette's muted text colour, drawn at paint time so it follows the theme."""

    def __init__(self, name: str, size: int = 18) -> None:
        super().__init__()
        self._name, self._size = name, size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        color = self.palette().color(QPalette.ColorRole.PlaceholderText)
        QPainter(self).drawPixmap(0, 0, pixmap(self._name, color, self._size))


class Chip(QWidget):
    """``● Ready``: a status dot with its word, for a one-glance state."""

    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel()
        self._label.setFont(ui_font(13, _Medium))
        self._dot = _Dot(Status.UNKNOWN, QFontMetrics(self._label.font()).height())
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self._dot)
        row.addWidget(self._label)

    def set_status(self, status: Status, text: str) -> None:
        self._dot.set_status(status)
        self._label.setText(text)


class StatCard(QFrame):
    """Icon + title, a big value, and what the value means. Styled by ``QFrame#card`` in ui.style."""

    def __init__(self, icon: str, title: str, desc: str) -> None:
        super().__init__()
        self.setObjectName("card")
        # Equal columns whatever the text: width comes from the grid's stretch, not from each card's hint.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._value = QLabel("—")
        self._value.setFont(ui_font(26, _Medium))
        self._value.setWordWrap(True)  # a long value wraps between its words instead of being clipped
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Glyph(icon, 16))
        head.addWidget(muted(title), stretch=1)
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(6)
        col.addLayout(head)
        col.addWidget(self._value)
        col.addWidget(muted(desc))

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def value(self) -> str:
        return self._value.text()


def brand(left: int = 0) -> QWidget:
    """The app icon with its name, for the top of a window's side column."""
    box = QWidget()
    box.setObjectName("brand")
    row = QHBoxLayout(box)
    row.setContentsMargins(left, 0, 0, 0)
    row.setSpacing(10)
    logo = QLabel()
    logo.setFixedSize(32, 32)
    pm = app_icon().pixmap(64, 64)
    pm.setDevicePixelRatio(2)
    logo.setPixmap(pm)
    name = QLabel("Budgie")
    name.setFont(ui_font(16, _Medium))
    row.addWidget(logo)
    row.addWidget(name)
    row.addStretch()
    return box


def card(*rows: QWidget) -> QFrame:
    """Rows in one rounded panel, with a hairline between them. Styled by ``QFrame#card`` in ui.style."""
    frame = QFrame()
    frame.setObjectName("card")
    col = QVBoxLayout(frame)
    col.setContentsMargins(16, 6, 16, 6)  # + the row's own 10 px: 16 px on all four sides
    col.setSpacing(0)
    for i, row in enumerate(rows):
        if i:
            sep = QFrame()
            sep.setObjectName("sep")
            sep.setFixedHeight(1)
            col.addWidget(sep)
        col.addWidget(row)
    return frame


class Switch(QCheckBox):
    """A QCheckBox drawn as a toggle switch: same state, signals, keyboard and accessibility,
    only the painting differs."""

    _W, _H, _GAP, _PAD = 36, 20, 10, 2

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._t = 1.0 if self.isChecked() else 0.0   # 0 = off, 1 = on; animated
        self._ring = False                            # keyboard focus ring
        self._anim = QVariantAnimation(self, duration=120)
        self._anim.valueChanged.connect(self._step)
        self.toggled.connect(self._on_toggled)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _step(self, value: float) -> None:
        self._t = float(value)
        self.update()

    def _on_toggled(self, on: bool) -> None:
        end = 1.0 if on else 0.0
        if not self.isVisible():  # programmatic setup: no animation
            self._anim.stop()
            self._step(end)
            return
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(end)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        text = self.text()
        width = 2 * self._PAD + self._W + (self._GAP + fm.horizontalAdvance(text) if text else 0)
        return QSize(width, max(self._H + 2 * self._PAD, fm.height() + 4))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._ring = event.reason() != Qt.FocusReason.MouseFocusReason
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._ring = False
        super().focusOutEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        pal = self.palette()
        role = QPalette.ColorRole
        enabled = self.isEnabled()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not enabled:
            p.setOpacity(0.45)
        y = (self.height() - self._H) / 2
        track = QRectF(self._PAD, y, self._W, self._H)
        off = mix(pal.color(role.Window), pal.color(role.WindowText), 0.22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(off, pal.color(role.Highlight), self._t))
        p.drawRoundedRect(track, self._H / 2, self._H / 2)

        d = self._H - 4
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(track.x() + 2 + (self._W - self._H) * self._t, y + 2, d, d))

        if self._ring and enabled:
            p.setPen(QPen(pal.color(role.Highlight), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-1, -1, 1, 1), self._H / 2 + 1, self._H / 2 + 1)

        p.setOpacity(1.0)
        group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
        p.setPen(pal.color(group, role.WindowText))
        p.setFont(self.font())
        text_rect = QRectF(track.right() + self._GAP, 0, self.width() - track.right() - self._GAP, self.height())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


def page(*widgets: QWidget) -> QWidget:
    """A settings page: consistent margins and spacing, content pinned to the top."""
    root = QWidget()
    col = QVBoxLayout(root)
    col.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
    col.setSpacing(SPACING)
    for w in widgets:
        col.addWidget(w)
    col.addStretch()
    return root

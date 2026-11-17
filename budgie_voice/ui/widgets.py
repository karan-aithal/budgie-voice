"""Shared Native Minimal building blocks: type scale and a status row.

System font and palette only. Hierarchy comes from weight, size and the muted text colour,
not from boxes or shadows.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPainter, QPalette
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from budgie_voice.ui.status import Status
from budgie_voice.ui.theme import status_color

SPACING = 12   # between controls
MARGIN = 20    # page edge
_DOT = 8


def section(text: str) -> QLabel:
    """Sub-heading inside a page: same size as body text, semibold."""
    label = QLabel(text)
    font = label.font()
    font.setWeight(font.Weight.DemiBold)
    label.setFont(font)
    return label


def heading(text: str) -> QLabel:
    """Page title: system font, one step up, semibold. Not oversized."""
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 1.35)
    font.setWeight(font.Weight.DemiBold)
    label.setFont(font)
    return label


def muted(text: str) -> QLabel:
    """Secondary text: palette placeholder colour, so it adapts to light and dark."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


class _Dot(QWidget):
    """Status dot, coloured at paint time so it follows theme changes."""

    def __init__(self, status: Status) -> None:
        super().__init__()
        self._status = status
        self.setFixedSize(QSize(_DOT + 2, _DOT + 2))

    def set_status(self, status: Status) -> None:
        self._status = status
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(status_color(self._status))
        painter.drawEllipse(1, 1, _DOT, _DOT)


class StatusRow(QWidget):
    """``● Name   Status — detail``: one line per component. The state is also written
    out in words, so colour is never the only signal."""

    def __init__(
        self, name: str, status: Status = Status.UNKNOWN, detail: str = "", word: str | None = None,
        name_width: int = 170,
    ) -> None:
        super().__init__()
        self._dot = _Dot(status)
        self._name = QLabel(name)
        self._name.setMinimumWidth(name_width)  # default fits the longest Overview name, so values line up
        self._value = QLabel()
        self._value.setWordWrap(True)
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(SPACING)
        row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._name, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._value, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
        self.set_status(status, detail, word)

    def set_status(self, status: Status, detail: str = "", word: str | None = None) -> None:
        """*word* replaces the status name for results that are not component states ("Match")."""
        self._dot.set_status(status)
        label = word or status.value
        self._value.setText(f"{label} — {detail}" if detail else label)


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

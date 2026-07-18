"""FloatingRecorderWidget — always-on-top mini overlay shown while recording.

Frameless, corner-docked, never steals keyboard focus (WA_ShowWithoutActivating)
so the injection target window stays focused throughout the session. Shows a
live waveform driven by :meth:`push_level` and a Cancel button that aborts
the session without injecting anything.
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

_WIDTH = 220
_HEIGHT = 56
_MARGIN = 24
_N_BARS = 28
_BAR_GAP = 3


class FloatingRecorderWidget(QWidget):
    """Corner-docked recording overlay: waveform + status label + cancel.

    Args:
        on_cancel: Called when the user clicks the cancel button.
    """

    def __init__(self, on_cancel: Callable[[], None]) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_WIDTH, _HEIGHT)

        self._levels: deque[float] = deque([0.0] * _N_BARS, maxlen=_N_BARS)
        self._label = "Recording"
        self._pulse_on = True

        self._cancel_btn = QPushButton("✕", self)
        self._cancel_btn.setGeometry(_WIDTH - 26, 6, 20, 20)
        self._cancel_btn.setFlat(True)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(
            "QPushButton { color: #ccc; background: transparent; border: none; font-size: 12px; }"
            "QPushButton:hover { color: white; }"
        )
        self._cancel_btn.clicked.connect(on_cancel)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(500)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._position_bottom_right()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_recording(self) -> None:
        """Show the overlay in "Recording" state."""
        self._label = "Recording"
        self._levels.clear()
        self._levels.extend([0.0] * _N_BARS)
        self._pulse_on = True
        self._pulse_timer.start()
        self._position_bottom_right()
        self.show()
        self.raise_()

    def show_transcribing(self) -> None:
        """Switch the overlay to "Transcribing" state (still visible, waveform frozen)."""
        self._label = "Transcribing"
        self._pulse_timer.stop()
        self._pulse_on = True
        self.update()

    def hide_widget(self) -> None:
        """Hide the overlay (any state that isn't recording/transcribing)."""
        self._pulse_timer.stop()
        self.hide()

    def push_level(self, level: float) -> None:
        """Append a new amplitude sample (0..1) and repaint the waveform."""
        self._levels.append(max(0.0, min(1.0, level)))
        if self.isVisible():
            self.update()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _toggle_pulse(self) -> None:
        self._pulse_on = not self._pulse_on
        self.update()

    def _position_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - _WIDTH - _MARGIN, geo.bottom() - _HEIGHT - _MARGIN)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, _WIDTH, _HEIGHT), 12, 12)
        painter.fillPath(bg, QColor(24, 24, 28, 235))

        dot_color = QColor(220, 50, 50) if self._pulse_on else QColor(120, 30, 30)
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(12, 12, 10, 10)

        painter.setPen(QColor(230, 230, 230))
        painter.drawText(30, 21, self._label)

        bar_area_x = 12
        bar_area_y = 30
        bar_area_w = _WIDTH - 24
        bar_area_h = _HEIGHT - bar_area_y - 8
        bar_w = (bar_area_w - _BAR_GAP * (_N_BARS - 1)) / _N_BARS

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(90, 180, 255))
        for i, level in enumerate(self._levels):
            h = max(2.0, level * bar_area_h)
            x = bar_area_x + i * (bar_w + _BAR_GAP)
            y = bar_area_y + (bar_area_h - h) / 2
            painter.drawRoundedRect(QRectF(x, y, bar_w, h), 1, 1)

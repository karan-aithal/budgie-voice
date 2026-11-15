"""PreviewWidget — editable text preview shown before injection.

Unlike FloatingRecorderWidget this one DOES take focus (WA_ShowWithoutActivating
is deliberately absent) since the user needs to type corrections into it.
Enter accepts, Shift+Enter inserts a newline, Esc cancels (nothing injected).
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

_WIDTH = 520
_HEIGHT = 210
_MARGIN = 24  # gap from the screen edge when docked

# --- Layout / typography -----------------------------------------------------
# Everything the eye notices lives here so it can be tuned without touching the
# stylesheets below. The edit box is laid out by QVBoxLayout, so it absorbs all
# space left over by _CONTENT_MARGIN and the button row: growing _HEIGHT or
# shrinking _CONTENT_MARGIN grows the text area directly.
_FONT_PX = 17          # transcript text
_BTN_FONT_PX = 13
_CONTENT_MARGIN = 12   # panel edge → controls
_EDIT_PADDING = 10     # edit border → first glyph
_EDIT_RADIUS = 8
_PANEL_RADIUS = 12

# --- Background alphas (0-255) ----------------------------------------------
# Only surfaces are translucent — every `color:` below stays fully opaque so the
# text itself never dims. Window-level opacity is deliberately NOT used, as it
# would fade the text too.
#
# The edit box draws no fill of its own: stacking a second translucent layer on
# the panel composites back toward opaque (165 over 140 measured as 214), which
# defeats the point. One panel fill keeps the see-through effect uniform.
_PANEL_ALPHA = 150
_BTN_ALPHA = 110


class _EditBox(QTextEdit):
    def __init__(self, on_accept: Callable[[], None], on_cancel: Callable[[], None]) -> None:
        super().__init__()
        self._on_accept = on_accept
        self._on_cancel = on_cancel

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._on_accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


class PreviewWidget(QWidget):
    """Corner-docked editable preview, shown once per session before inject."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setFixedSize(_WIDTH, _HEIGHT)
        # Real alpha compositing for the panel. The panel itself is drawn in
        # paintEvent rather than via a stylesheet: a stylesheet background on a
        # translucent plain QWidget doesn't reliably paint (verified — the
        # margin ring rendered fully transparent), and drawing it directly also
        # avoids the fill cascading into the children.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._result_cb: Callable[[str | None], None] | None = None

        self._edit = _EditBox(self._accept, self._cancel)
        self._edit.setStyleSheet(
            "QTextEdit {"
            " background: transparent;"  # panel behind it supplies the tint
            " color: #ffffff;"  # opaque: text must stay fully legible
            " border: 1px solid rgba(255, 255, 255, 30);"  # keeps the edit area readable
            f" border-radius: {_EDIT_RADIUS}px;"
            f" padding: {_EDIT_PADDING}px;"
            f" font-size: {_FONT_PX}px; }}"
        )

        accept_btn = QPushButton("Accept (Enter)")
        cancel_btn = QPushButton("Cancel (Esc)")
        for btn in (accept_btn, cancel_btn):
            btn.setStyleSheet(
                "QPushButton { color: #f0f0f4;"
                f" background: rgba(44, 44, 51, {_BTN_ALPHA});"
                " border: none; border-radius: 4px; padding: 5px 12px;"
                f" font-size: {_BTN_FONT_PX}px; }}"
                "QPushButton:hover { color: #ffffff;"
                f" background: rgba(58, 58, 66, {min(255, _BTN_ALPHA + 40)}); }}"
            )
        accept_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self._cancel)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(accept_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _CONTENT_MARGIN, _CONTENT_MARGIN, _CONTENT_MARGIN, _CONTENT_MARGIN
        )
        layout.addWidget(self._edit)
        layout.addLayout(btn_row)

        self._position_bottom_right()

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        """Draw the translucent rounded panel behind the controls.

        Only this surface is see-through — every ``color:`` in the child
        stylesheets stays fully opaque, so the text reads at full strength
        against whatever is behind the window.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
        painter.setBrush(QColor(24, 24, 28, _PANEL_ALPHA))
        painter.drawRoundedRect(
            self.rect().adjusted(0, 0, -1, -1), _PANEL_RADIUS, _PANEL_RADIUS
        )

    def show_preview(self, text: str, result_cb: Callable[[str | None], None]) -> None:
        """Show *text* for editing; *result_cb* fires once with edited text or None (cancelled)."""
        self._result_cb = result_cb
        self._edit.setPlainText(text)
        cursor = self._edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._edit.setTextCursor(cursor)
        self._position_bottom_right()
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()
        QTimer.singleShot(0, self._position_bottom_right)

    def _accept(self) -> None:
        self._finish(self._edit.toPlainText().strip())

    def _cancel(self) -> None:
        self._finish(None)

    def _finish(self, result: str | None) -> None:
        self.hide()
        cb, self._result_cb = self._result_cb, None
        if cb is not None:
            cb(result)

    def _position_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - _WIDTH - _MARGIN, geo.bottom() - _HEIGHT - _MARGIN)

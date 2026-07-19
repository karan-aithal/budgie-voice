"""PreviewWidget — editable text preview shown before injection.

Unlike FloatingRecorderWidget this one DOES take focus (WA_ShowWithoutActivating
is deliberately absent) since the user needs to type corrections into it.
Enter accepts, Shift+Enter inserts a newline, Esc cancels (nothing injected).
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

_WIDTH = 380
_HEIGHT = 140
_MARGIN = 24


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
        self.setStyleSheet("background: #18181c; border-radius: 10px;")

        self._result_cb: Callable[[str | None], None] | None = None

        self._edit = _EditBox(self._accept, self._cancel)
        self._edit.setStyleSheet(
            "QTextEdit { background: #232328; color: #eee; border: none; border-radius: 6px; padding: 8px; font-size: 13px; }"
        )

        accept_btn = QPushButton("Accept (Enter)")
        cancel_btn = QPushButton("Cancel (Esc)")
        for btn in (accept_btn, cancel_btn):
            btn.setStyleSheet(
                "QPushButton { color: #ccc; background: #2c2c33; border: none; border-radius: 4px; padding: 4px 10px; }"
                "QPushButton:hover { color: white; background: #3a3a42; }"
            )
        accept_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self._cancel)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(accept_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self._edit)
        layout.addLayout(btn_row)

        self._position_bottom_right()

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

"""Scoped visual style: radii, Ubuntu font, one stylesheet, theme-aware icons.

Everything here is applied to a *root window* (Settings, the enrollment wizard) and cascades only to
its descendants. Nothing is set on the QApplication, so the recorder overlay and the transcript
preview (parentless windows with their own look) are never touched.

The stylesheet is built from the application palette and rebuilt on a palette change, so light,
dark and live desktop switches follow without a restart.
"""
from __future__ import annotations

from pathlib import Path
from string import Template

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import QAbstractButton, QApplication, QComboBox, QListWidget, QListWidgetItem, QWidget

from budgie_voice.ui import icons

R_CONTROL = 8   # buttons, inputs, combos, tooltips, menu items
R_PANEL = 10    # cards, frames, popups, menus
_FAMILIES = ["Ubuntu", "Ubuntu Sans", "Cantarell", "Noto Sans", "Sans Serif"]
_ASSETS = Path(__file__).parent / "assets"
_CHEVRON = (_ASSETS / "chevron-down.svg").as_posix()
_ICON_NAME = "lucide"           # dynamic property / item data holding the icon name
_ROLE = Qt.ItemDataRole.UserRole + 1
_BTN_GAP = 4                    # extra px between a button's icon and its label
_NAV_GAP = 6                    # extra px between a nav icon and its label


def ui_font(px: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Ubuntu at *px* pixels, falling back through the families above to the desktop default."""
    font = QFont()
    font.setFamilies(_FAMILIES)
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


# -- colour helpers ----------------------------------------------------------------------------

def mix(a: QColor, b: QColor, t: float) -> QColor:
    """*a* moved *t* (0..1) of the way towards *b*."""
    return QColor.fromRgbF(*(x + (y - x) * t for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3])))


def _tokens() -> dict[str, str]:
    pal = QApplication.palette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    win, base, text = pal.color(role.Window), pal.color(role.Base), pal.color(role.WindowText)
    hl, ht = pal.color(role.Highlight), pal.color(role.HighlightedText)
    dark = win.lightness() < 128
    t = {
        "text": text, "base": base, "hl": hl, "ht": ht,
        "muted": pal.color(role.PlaceholderText),
        "disabled": pal.color(group.Disabled, role.WindowText),
        "border": mix(win, text, 0.20),
        "btn": mix(win, text, 0.07), "btn_hover": mix(win, text, 0.12), "btn_press": mix(win, text, 0.18),
        "btn_off": mix(win, text, 0.03), "border_off": mix(win, text, 0.10),
        "hl_hover": hl.lighter(112) if dark else hl.darker(110),
        "hl_press": hl.lighter(125) if dark else hl.darker(125),
        "groove": mix(win, text, 0.16),
        "thumb": mix(win, text, 0.32), "thumb_hover": mix(win, text, 0.50),
    }
    return {k: v.name() for k, v in t.items()}


_QSS = Template("""
QPushButton { background: $btn; color: $text; border: 1px solid $border; border-radius: ${rc}px;
  padding: 4px 14px; min-height: 22px; font-weight: 500; }
QPushButton:hover { background: $btn_hover; }
QPushButton:pressed { background: $btn_press; }
QPushButton:focus { border: 2px solid $hl; padding: 3px 13px; }
QPushButton:disabled { background: $btn_off; color: $disabled; border-color: $border_off; }
QPushButton[iconOnly="true"] { padding: 4px; }
QPushButton[primary="true"] { background: $hl; color: $ht; border-color: $hl; }
QPushButton[primary="true"]:hover { background: $hl_hover; border-color: $hl_hover; }
QPushButton[primary="true"]:pressed { background: $hl_press; border-color: $hl_press; }
QPushButton[primary="true"]:focus { border: 2px solid $ht; padding: 3px 13px; }
QPushButton[primary="true"]:disabled { background: $btn_off; color: $disabled; border-color: $border_off; }

QLineEdit, QComboBox { background: $base; color: $text; border: 1px solid $border; border-radius: ${rc}px;
  padding: 5px 12px; min-height: 20px; selection-background-color: $hl; selection-color: $ht; }
QLineEdit:focus, QComboBox:focus, QComboBox:on { border: 2px solid $hl; padding: 4px 11px; }
QLineEdit:disabled, QComboBox:disabled { background: $btn_off; color: $disabled; border-color: $border_off; }
QComboBox { padding-right: 40px; combobox-popup: 0; }
QComboBox:focus, QComboBox:on { padding-right: 39px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 16px;
  margin-right: 12px; border: none; }
QComboBox::down-arrow { image: url($chevron); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: $base; color: $text; border: 1px solid $border;
  border-radius: ${rp}px; padding: 4px; outline: 0; selection-background-color: $hl; selection-color: $ht; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 0 12px; border-radius: 6px; }
QComboBox QAbstractItemView::item:selected { background: $hl; color: $ht; }

QRadioButton { spacing: 8px; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid $border; background: $base; }
QRadioButton::indicator:hover { border-color: $hl; }
QRadioButton::indicator:checked { border-color: $hl; background: qradialgradient(cx: .5, cy: .5, radius: .5,
  fx: .5, fy: .5, stop: 0 $ht, stop: .32 $ht, stop: .40 $hl, stop: 1 $hl); }
QRadioButton::indicator:focus { border: 2px solid $hl; }
QRadioButton::indicator:disabled { background: $btn_off; border-color: $border_off; }

QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: $groove; }
QSlider::sub-page:horizontal { background: $hl; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -7px 0; border-radius: 9px;
  background: #ffffff; border: 1px solid $border; }
QSlider::handle:horizontal:hover { border-color: $hl; }
QSlider::handle:horizontal:focus { border: 2px solid $hl; }
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled { background: $btn_off; }
QSlider::handle:horizontal:disabled { background: $btn; border-color: $border_off; }

QListWidget#nav { background: transparent; border: none; outline: 0; padding: 8px; }
QListWidget#nav::item { padding: 8px 12px; margin: 1px 0; border-radius: ${rc}px; color: $text; }
QListWidget#nav::item:hover:!selected { background: $btn_hover; }
QListWidget#nav::item:selected { background: $hl; color: $ht; }

QFrame#card { background: $base; border: 1px solid $border; border-radius: ${rp}px; }
QFrame#sep { background: $border; border: none; }

QTreeView { background: $base; color: $text; border: 1px solid $border; border-radius: ${rp}px;
  padding: 4px; outline: 0; }
QTreeView::item { min-height: 26px; padding: 0 4px; }
QTreeView::item:selected { background: $hl; color: $ht; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $muted; border: none; border-bottom: 1px solid $border;
  padding: 6px 8px; font-weight: 500; }

QProgressBar { background: $groove; color: $text; border: none; border-radius: 10px; min-height: 20px;
  text-align: center; }
QProgressBar::chunk { background: $hl; border-radius: 10px; }
QProgressBar[slim="true"] { min-height: 8px; max-height: 8px; border-radius: 4px; }
QProgressBar[slim="true"]::chunk { border-radius: 4px; }

QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $thumb; border-radius: 4px; min-height: 32px; margin: 2px; }
QScrollBar::handle:horizontal { background: $thumb; border-radius: 4px; min-width: 32px; margin: 2px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: $thumb_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QToolTip { background: $base; color: $text; border: 1px solid $border; border-radius: ${rc}px; padding: 5px 8px; }
QMenu { background: $base; color: $text; border: 1px solid $border; border-radius: ${rp}px; padding: 4px; }
QMenu::item { padding: 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: $hl; color: $ht; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
""")


def build_qss() -> str:
    return _QSS.substitute(_tokens(), rc=R_CONTROL, rp=R_PANEL, chevron=_CHEVRON)


# -- icons -------------------------------------------------------------------------------------

def _paint(target: QAbstractButton | QListWidgetItem) -> None:
    """(Re)render *target*'s Lucide icon for the current palette."""
    pal = QApplication.palette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    if isinstance(target, QListWidgetItem):
        name, size = target.data(_ROLE), 18
        normal, selected = pal.color(role.WindowText), pal.color(role.HighlightedText)
        target.setIcon(icons.icon(name, normal, pal.color(group.Disabled, role.WindowText), selected, size, _NAV_GAP))
        return
    name = target.property(_ICON_NAME)
    primary = bool(target.property("primary"))
    normal = pal.color(role.HighlightedText if primary else role.ButtonText)
    disabled = pal.color(group.Disabled, role.ButtonText)
    gap = 0 if target.property("iconOnly") else _BTN_GAP  # a glyph alone stays centred
    target.setIcon(icons.icon(name, normal, disabled, gap=gap))
    target.setIconSize(QSize(16 + gap, 16))


def set_icon(target: QAbstractButton | QListWidgetItem, name: str) -> None:
    """Give a button or list item the Lucide icon *name*; it follows later palette changes.

    Call ``setProperty("primary", True)`` on a button *before* this, so its icon matches the label.
    """
    if isinstance(target, QListWidgetItem):
        target.setData(_ROLE, name)
    else:
        target.setProperty(_ICON_NAME, name)
    _paint(target)


def _refresh_icons(root: QWidget) -> None:
    for button in root.findChildren(QAbstractButton):
        if button.property(_ICON_NAME):
            _paint(button)
    for lst in root.findChildren(QListWidget):
        for i in range(lst.count()):
            if lst.item(i).data(_ROLE):
                _paint(lst.item(i))


# -- scope -------------------------------------------------------------------------------------

class _Scope(QObject):
    """Rebuilds the root's stylesheet and icons when the application palette changes."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self._root = root
        self._qss = ""
        root.installEventFilter(self)
        self._apply()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._root and event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            QTimer.singleShot(0, self._apply)  # after Qt has finished updating the palette
        return False

    def _apply(self) -> None:
        qss = build_qss()
        if qss == self._qss:  # setStyleSheet itself triggers palette events: never loop
            return
        self._qss = qss
        self._root.setStyleSheet(qss)
        _refresh_icons(self._root)


def _round_popup(combo: QComboBox) -> None:
    """Let the popup's rounded corners show: its top-level frame must not paint a square background."""
    popup = combo.view().window()
    popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    popup.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)


def apply_scope(root: QWidget) -> None:
    """Font, window icon, stylesheet and icon colours for *root* and its descendants only. Call once, last in __init__."""
    root.setFont(ui_font(13))
    root.setWindowIcon(QIcon(str(_ASSETS / "budgie.svg")))  # per window: the recorder/preview keep none
    for combo in root.findChildren(QComboBox):
        _round_popup(combo)
    _Scope(root)

"""Lucide icons (https://lucide.dev, ISC licence) as inline SVG bodies, rendered at the size and
colour asked for. Icon bodies are from lucide-static 0.469.0.

Lucide: Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather
(MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022. Permission to use,
copy, modify, and/or distribute this software for any purpose with or without fee is hereby
granted, provided that the above copyright notice and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES.
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_BODY = {
    "audio-lines": (
        '<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/>'
    ),
    "check": (
        '<path d="M20 6 9 17l-5-5"/>'
    ),
    "chevron-left": (
        '<path d="m15 18-6-6 6-6"/>'
    ),
    "circle-check": (
        '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'
    ),
    "circle-dot": (
        '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>'
    ),
    "circle": (
        '<circle cx="12" cy="12" r="10"/>'
    ),
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "file-text": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
    "keyboard": (
        '<path d="M10 8h.01"/><path d="M12 12h.01"/><path d="M14 8h.01"/><path d="M16 12h.01"/><path d="M18 8h.01"/><path d="M6 8h.01"/><path d="M7 16h10"/><path d="M8 12h.01"/><rect width="20" height="16" x="2" y="4" rx="2"/>'
    ),
    "layout-dashboard": (
        '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>'
    ),
    "loader-circle": (
        '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>'
    ),
    "lock": (
        '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
    ),
    "log-out": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>'
    ),
    "mic": (
        '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>'
    ),
    "play": (
        '<polygon points="6 3 20 12 6 21 6 3"/>'
    ),
    "refresh-cw": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'
    ),
    "rotate-cw": (
        '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
    ),
    "shield-check": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>'
    ),
    "sliders-horizontal": (
        '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>'
    ),
    "sparkles": (
        '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>'
    ),
    "square": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
    ),
    "sun-moon": (
        '<path d="M12 8a2.83 2.83 0 0 0 4 4 4 4 0 1 1-4-4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.3 17.7-1.4 1.4"/><path d="m19.1 4.9-1.4 1.4"/>'
    ),
    "user-round-plus": (
        '<path d="M2 21a8 8 0 0 1 13.292-6"/><circle cx="10" cy="8" r="5"/><path d="M19 16v6"/><path d="M22 19h-6"/>'
    ),
    "x": (
        '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'
    ),
}

_SCALE = 2  # render at 2x and tag the pixmap: crisp on HiDPI, merely downscaled on 1x


def pixmap(name: str, color: QColor, size: int, gap: int = 0) -> QPixmap:
    """Icon *name* as a *size*-px pixmap with a *color* stroke, on a canvas *gap* px wider on the right
    (a list item's icon-to-text distance is not otherwise adjustable)."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        f' stroke="{color.name()}" stroke-opacity="{color.alphaF():.3f}" stroke-width="2"'
        f' stroke-linecap="round" stroke-linejoin="round">{_BODY[name]}</svg>'
    )
    px = size * _SCALE
    image = QImage(px + gap * _SCALE, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, px, px))
    painter.end()
    out = QPixmap.fromImage(image)
    out.setDevicePixelRatio(_SCALE)
    return out


def icon(name: str, normal: QColor, disabled: QColor | None = None, selected: QColor | None = None,
         size: int = 16, gap: int = 0) -> QIcon:
    """A QIcon with Normal, Disabled and Selected pixmaps (colours are resolved by the caller)."""
    result = QIcon()
    result.addPixmap(pixmap(name, normal, size, gap), QIcon.Mode.Normal)
    if disabled is not None:
        result.addPixmap(pixmap(name, disabled, size, gap), QIcon.Mode.Disabled)
    if selected is not None:
        result.addPixmap(pixmap(name, selected, size, gap), QIcon.Mode.Selected)
    return result

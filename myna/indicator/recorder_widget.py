"""FloatingRecorderWidget — always-on-top, chromeless overlay shown while recording.

Frameless, transparent, bottom-center docked, never steals keyboard focus
(WA_ShowWithoutActivating) so the injection target window stays focused
throughout the session. Two continuously-animated visualizer styles —
a flowing multi-strand waveform, and a rotating wireframe globe — both
driven by :meth:`push_level`, plus a Cancel button that aborts the session
without injecting anything.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Callable, Literal

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from myna.audio.spectrum import AudioLevel

_WIDTH = 320
_HEIGHT = 140
_MARGIN = 24
_TICK_MS = 33
_N_SAMPLES = 40

_N_STRANDS = 12

# Frequency → color anchors, shared by the waveform heatmap and the globe's
# per-band ring groups: bass=warm, mid=yellow-green, treble=cool.
_BASS_COLOR = (255, 100, 70)
_MID_COLOR = (255, 210, 90)
_TREBLE_COLOR = (100, 190, 255)
_LEVEL_COLOR = (210, 235, 255)

# Globe: 96 independent wire strands (within the requested 64-128 range),
# split into two geometrically distinct families so it reads as an actual
# wireframe mesh (crossing lines) rather than concentric breathing rings —
# latitude parallels (fixed shape, don't rotate — physically correct: a
# horizontal circle's silhouette doesn't change as a sphere spins around
# its vertical axis) and longitude meridians (the family that visibly
# spins). Single stroke pass (no separate halo) — at this density the
# overlapping strands already read as a glow; a second pass would double
# 96 drawPath calls/frame for little visible gain.
_N_LAT = 48
_N_LONG = 48
_LAT_FLATTEN = 0.3
_N_VERTS = 20          # points sampled around each ring's own contour
_NOISE_AMOUNT = 0.30   # base per-vertex radius jitter (scaled by impact too)

# Squash-and-stretch spring: underdamped (K, DAMP chosen so 2*sqrt(K) > DAMP)
# so a loud hit overshoots and wobbles back rather than snapping — the
# "natural, wobbly lag" the bounce needs.
_SPRING_K = 110.0
_SPRING_DAMP = 8.0
_STRETCH_AMOUNT = 0.85  # max extra height at scale_y=1.0 target on a full hit
_SQUASH_COUPLING = 0.6  # how strongly X compresses as Y stretches (inverse)

_SILENT = AudioLevel(0.0, 0.0, 0.0, 0.0)

VisualizerStyle = Literal["waveform", "blob"]


def _mix_color(level: AudioLevel) -> QColor:
    """Blend bass/mid/treble anchor colors by this frame's spectral balance."""
    total = level.bass + level.mid + level.treble
    if total <= 1e-6:
        return QColor(60, 70, 90)
    r = level.bass * _BASS_COLOR[0] + level.mid * _MID_COLOR[0] + level.treble * _TREBLE_COLOR[0]
    g = level.bass * _BASS_COLOR[1] + level.mid * _MID_COLOR[1] + level.treble * _TREBLE_COLOR[1]
    b = level.bass * _BASS_COLOR[2] + level.mid * _MID_COLOR[2] + level.treble * _TREBLE_COLOR[2]
    return QColor(int(r), int(g), int(b))


def _vertex_noise(ring_idx: int, point_idx: int, t: float) -> float:
    """Cheap deterministic, continuously time-evolving pseudo-noise (~-1..1).

    A few summed sine "octaves" keyed by both indices so every point on
    every ring bulges/recedes independently rather than the ring breathing
    as one rigid unit — no noise library needed for this small an effect.
    """
    a = math.sin(point_idx * 1.3 + ring_idx * 0.37 + t * 1.6)
    b = math.sin(point_idx * 3.1 - ring_idx * 0.21 + t * 2.4)
    c = math.sin(point_idx * 0.5 + ring_idx * 1.5 - t * 0.7)
    return a * 0.5 + b * 0.3 + c * 0.2


def _smooth_path(path: QPainterPath, points: list[QPointF]) -> None:
    """Append *points* to *path* as a smooth curve (quad-through-midpoints)."""
    if len(points) < 3:
        for p in points[1:]:
            path.lineTo(p)
        return
    for i in range(len(points) - 1):
        mid = QPointF((points[i].x() + points[i + 1].x()) / 2, (points[i].y() + points[i + 1].y()) / 2)
        path.quadTo(points[i], mid)
    path.lineTo(points[-1])


class FloatingRecorderWidget(QWidget):
    """Bottom-center, chromeless recording overlay: visualizer + label + cancel.

    Args:
        on_cancel: Called when the user clicks the cancel button.
        style: Initial visualizer style — ``"waveform"`` or ``"blob"``.
    """

    def __init__(self, on_cancel: Callable[[], None], style: VisualizerStyle = "waveform") -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_WIDTH, _HEIGHT)

        self._style: VisualizerStyle = style
        self._levels: deque[AudioLevel] = deque([_SILENT] * _N_SAMPLES, maxlen=_N_SAMPLES)
        self._smoothed_level = 0.0
        self._phase = 0.0
        self._rotation = 0.0
        self._scale_y = 1.0
        self._scale_y_vel = 0.0
        self._impact = 0.0  # decaying "energy" from recent spring velocity, drives turbulence
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

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(_TICK_MS)
        self._anim_timer.timeout.connect(self._on_tick)

        self._position_bottom_center()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_recording(self) -> None:
        """Show the overlay in "Recording" state."""
        self._label = "Recording"
        self._levels.clear()
        self._levels.extend([_SILENT] * _N_SAMPLES)
        self._smoothed_level = 0.0
        self._scale_y = 1.0
        self._scale_y_vel = 0.0
        self._impact = 0.0
        self._pulse_on = True
        self._pulse_timer.start()
        self._anim_timer.start()
        self._position_bottom_center()
        self.show()
        self.raise_()
        # X11 WMs often ignore move() issued before the window is mapped —
        # re-place once the window actually exists on screen.
        QTimer.singleShot(0, self._position_bottom_center)

    def show_transcribing(self) -> None:
        """Switch the overlay to "Transcribing" state — dot freezes, animation keeps flowing."""
        self._label = "Transcribing"
        self._pulse_timer.stop()
        self._pulse_on = True
        self._anim_timer.start()
        self.raise_()

    def hide_widget(self) -> None:
        """Hide the overlay (any state that isn't recording/transcribing)."""
        self._pulse_timer.stop()
        self._anim_timer.stop()
        self.hide()

    def push_level(self, level: AudioLevel) -> None:
        """Record a new frame's spectral sample (state only — the animation timer repaints)."""
        self._levels.append(level)
        self._smoothed_level = 0.7 * self._smoothed_level + 0.3 * level.level

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802, ARG002
        self._style = "blob" if self._style == "waveform" else "waveform"

    def _toggle_pulse(self) -> None:
        self._pulse_on = not self._pulse_on

    def _on_tick(self) -> None:
        self._phase += 0.18
        self._rotation += 0.035

        dt = _TICK_MS / 1000.0
        latest = self._levels[-1] if self._levels else _SILENT
        target_y = 1.0 + _STRETCH_AMOUNT * min(1.0, latest.level)
        accel = _SPRING_K * (target_y - self._scale_y) - _SPRING_DAMP * self._scale_y_vel
        self._scale_y_vel += accel * dt
        self._scale_y = max(0.4, min(1.8, self._scale_y + self._scale_y_vel * dt))
        # Asymmetric attack/release: snap up fast on a fresh hit, decay slower —
        # reads as "reacts immediately" instead of a fixed-lag EMA.
        vel_energy = abs(self._scale_y_vel)
        attack, release = 0.5, 0.88
        blend = attack if vel_energy > self._impact else release
        self._impact = blend * self._impact + (1.0 - blend) * vel_energy

        # Force-reassert top stacking every tick — some WMs (e.g. Mutter)
        # drop WindowStaysOnTopHint's effect once another window is focused;
        # raise_() here keeps clicking into other apps from burying us.
        self.raise_()
        self.update()

    def _position_bottom_center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center().x() - _WIDTH // 2, geo.bottom() - _HEIGHT - _MARGIN)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._style == "blob":
            self._paint_globe(painter)
        else:
            self._paint_waveform(painter)

        self._paint_label(painter)

    def _paint_label(self, painter: QPainter) -> None:
        dot_color = QColor(220, 50, 50) if self._pulse_on else QColor(120, 30, 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawEllipse(13, 13, 10, 10)
        painter.setBrush(dot_color)
        painter.drawEllipse(12, 12, 10, 10)

        painter.setPen(QColor(0, 0, 0, 160))
        painter.drawText(31, 22, self._label)
        painter.setPen(QColor(235, 235, 235))
        painter.drawText(30, 21, self._label)

    def _paint_waveform(self, painter: QPainter) -> None:
        """12 flowing strands over a shared heatmap gradient: hue = dominant
        frequency band at that point in time, intensity = volume at that
        point in time — a rolling spectrogram traced as a waveform.

        Shares the blob's spring/fizz state (computed once per tick in
        _on_tick regardless of style): self._scale_y stretches the whole
        waveform taller on a loud hit with the same overshoot-and-settle
        wobble, and self._impact drives per-point noise jitter (_vertex_noise)
        for a fizzy, reactive line — rougher right after a hit, smoothing as
        the spring settles.
        """
        area_x, area_w = 10, _WIDTH - 20
        area_y = 34
        area_h = _HEIGHT - area_y - 10
        cy = area_y + area_h / 2
        amp_max = area_h / 2 * 0.85
        n = len(self._levels)

        grad_core = QLinearGradient(area_x, 0, area_x + area_w, 0)
        grad_halo = QLinearGradient(area_x, 0, area_x + area_w, 0)
        for j, lvl in enumerate(self._levels):
            t = j / (n - 1)
            base = _mix_color(lvl)
            vol = min(1.0, lvl.level)
            core_c = QColor(base)
            core_c.setAlpha(int(60 + 195 * vol))
            halo_c = QColor(base)
            halo_c.setAlpha(int(15 + 45 * vol))
            grad_core.setColorAt(t, core_c)
            grad_halo.setColorAt(t, halo_c)

        halo_pen = QPen(QBrush(grad_halo), 6)
        halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        core_pen = QPen(QBrush(grad_core), 1.6)
        core_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        # Same spring/fizz state the blob uses (already ticking every frame
        # in _on_tick regardless of style): beat-reactive amplitude stretch,
        # and per-point noise scaled by post-hit "impact" energy.
        amp_max_eff = amp_max * self._scale_y
        jitter_amount = _NOISE_AMOUNT * (0.5 + 3.0 * self._impact)

        for i in range(_N_STRANDS):
            freq = 1.4 + i * 0.28
            phase_i = i * 0.75
            amp_scale = 1.0 - i * 0.045

            pts = []
            for j, lvl in enumerate(self._levels):
                x_norm = j / (n - 1)
                env = min(1.0, lvl.level)
                ripple = math.sin(x_norm * math.tau * freq * 0.5 + self._phase + phase_i)
                jitter = _vertex_noise(i, j, self._phase) * amp_max_eff * jitter_amount
                y = cy + ripple * amp_max_eff * amp_scale * (0.15 + 0.85 * env) + jitter
                pts.append(QPointF(area_x + x_norm * area_w, y))

            path = QPainterPath()
            path.moveTo(pts[0])
            _smooth_path(path, pts)

            painter.setPen(halo_pen)
            painter.drawPath(path)
            painter.setPen(core_pen)
            painter.drawPath(path)

    def _paint_globe(self, painter: QPainter) -> None:
        """A wireframe sphere made of two ring families — latitude parallels
        and longitude meridians — each strand assigned one audio parameter
        (bass / mid / treble / overall level) driving its own pulse size and
        band color, so the mesh shimmers with many small, differently-timed
        movements. Only the meridian family rotates (physically correct: a
        latitude circle's silhouette doesn't change as a sphere spins around
        its own vertical axis) — the crossing of static parallels against
        spinning meridians is what makes this read as an actual wireframe
        mesh instead of concentric breathing rings.

        The whole blob also squash-and-stretches on a spring (see _on_tick):
        Y stretches toward loud frames and overshoots/wobbles back rather
        than snapping, X compresses inversely to conserve "volume" like a
        gelatinous bounce. Each ring's radius additionally jitters with the
        spring's post-hit velocity ("impact energy") for a turbulent, still-
        settling surface right after a beat — decaying to smooth as it rests.
        """
        area_x, area_w = 10, _WIDTH - 20
        area_y = 34
        area_h = _HEIGHT - area_y - 10
        cx = area_x + area_w / 2
        cy = area_y + area_h / 2
        base_r = min(area_w, area_h) / 2 * 0.85

        latest = self._levels[-1]
        breathing = base_r * (0.7 + 0.3 * min(1.0, self._smoothed_level))
        band_values = (latest.bass, latest.mid, latest.treble, latest.level)
        band_colors = (_BASS_COLOR, _MID_COLOR, _TREBLE_COLOR, _LEVEL_COLOR)

        scale_y = self._scale_y
        scale_x = 1.0 + (1.0 - scale_y) * _SQUASH_COUPLING

        pen = QPen()
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setWidthF(1.5)

        painter.save()
        painter.translate(cx, cy)
        painter.scale(scale_x, scale_y)
        painter.translate(-cx, -cy)

        def _draw_ring(idx: int, cx_r: float, cy_r: float, rw: float, rh: float, shade: float) -> None:
            band = idx % 4
            weight = band_values[band]
            impact_jitter = _NOISE_AMOUNT * (0.5 + 3.0 * self._impact)
            scale = 0.82 + 0.18 * min(1.0, weight)

            pts = []
            for p in range(_N_VERTS):
                a = (p / _N_VERTS) * math.tau
                noise = _vertex_noise(idx, p, self._phase) * impact_jitter
                vr = scale * (1.0 + noise)
                pts.append(QPointF(cx_r + rw * vr * math.cos(a), cy_r + rh * vr * math.sin(a)))
            pts.append(pts[0])

            path = QPainterPath()
            path.moveTo(pts[0])
            _smooth_path(path, pts)
            path.closeSubpath()

            c = band_colors[band]
            alpha = int(50 + 150 * shade * (0.4 + 0.6 * min(1.0, weight)))
            pen.setColor(QColor(c[0], c[1], c[2], min(255, alpha)))
            painter.setPen(pen)
            painter.drawPath(path)

        # Longitude meridians — great circles through both poles, rotated
        # around the vertical axis. This family visibly spins.
        for k in range(_N_LONG):
            speed = 0.4 + ((k * 37) % 11) / 10.0
            theta = (k / _N_LONG) * math.pi + self._rotation * speed
            edge_on = max(0.05, abs(math.cos(theta)))
            _draw_ring(k, cx, cy, breathing * edge_on, breathing, edge_on)

        # Latitude parallels — horizontal circles at fixed elevations; shape
        # doesn't depend on rotation (only meridians spin, see docstring).
        for k in range(_N_LAT):
            phi = -math.pi / 2 + ((k + 0.5) / _N_LAT) * math.pi
            radius_at_lat = breathing * math.cos(phi)
            y_offset = -breathing * math.sin(phi)
            _draw_ring(
                _N_LONG + k, cx, cy + y_offset, radius_at_lat, radius_at_lat * _LAT_FLATTEN, 0.7
            )

        painter.restore()


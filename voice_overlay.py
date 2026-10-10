"""
voice_overlay.py — a small floating, draggable record button that sits over
the page, instead of a plain toolbar button. Click to start recording —
speech is transcribed live, in short chunks, and lands on the page as you
talk; click again to stop. Drag the button anywhere it's not in the way.

This mirrors a UX pattern that's already proven to work well: a circular
button that changes color with state (idle / loading / listening /
transcribing / error) rather than relying on text-label swapping. A soft
shadow gives it a sense of floating above the page rather than sitting
flush on it; a slow ambient pulse while listening is the same "this is
actively recording" cue most dictation/voice-memo UIs use. On top of
that, a second ring reacts to the actual input level in real time (see
set_audio_level, driven by main.py's recording poll) — an honest "yes,
I'm hearing you" the person can watch respond to their own voice, rather
than a generic animation that would look identical whether the mic is
picking anything up or not.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QIcon
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRect, QTimer

import theme

STATE_COLORS = {
    "idle": theme.FIELD,
    "loading": theme.TEXT_DISABLED,
    "listening": theme.DANGER,
    "transcribing": theme.ACCENT,
    "error": theme.DANGER,
}
STATE_TOOLTIPS = {
    "idle": "Click to record (drag to reposition)",
    "loading": "Loading Urdu speech model…",
    "listening": "Recording — transcribing live as you speak. Click to stop.",
    "transcribing": "Finishing up the last few words…",
    "error": "Couldn't load the speech model — click to retry",
}
PULSE_INTERVAL_MS = 40   # animation tick rate for the ambient "listening" pulse
PULSE_PERIOD_MS = 1600   # time for one full ambient pulse cycle
LEVEL_REFERENCE_RMS = 0.06  # an input level around here or above reads as the
                             # level ring's "full" size — see set_audio_level
LEVEL_SMOOTHING = 0.5       # 0..1, higher = the ring follows new readings faster
                             # (vs. gliding toward them) — see set_audio_level


class VoiceOverlayButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, icons_dir, parent=None):
        super().__init__(parent)
        # A little larger than the button itself so the soft shadow and
        # listening-state pulse ring have room to extend past the circle's
        # own edge without getting clipped.
        self.setFixedSize(72, 72)
        self._state = "loading"
        self._dragging = False
        self._user_positioned = False
        self._hovered = False
        self._press_pos = QPointF()
        self._start_top_left = QPointF()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._mic_icon = QIcon(str(icons_dir / "mic.svg"))
        self._mic_recording_icon = QIcon(str(icons_dir / "mic-recording.svg"))
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self._audio_level = 0.0  # smoothed 0..1, see set_audio_level
        self.set_state("loading")
        self.raise_()

    def set_state(self, state: str):
        changed = state != self._state
        self._state = state
        self.setToolTip(STATE_TOOLTIPS.get(state, ""))
        if state in ("listening", "transcribing"):
            if not self._pulse_timer.isActive():
                self._pulse_phase = 0.0
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
        if state != "listening":
            self._audio_level = 0.0
        if changed:
            self.update()

    def state(self) -> str:
        return self._state

    def set_audio_level(self, rms: float):
        """Called from main.py's recording poll (every ~200ms) with the
        most recent short-time RMS level from UtteranceSegmenter.last_rms.
        Smoothed rather than jumping straight to each new reading so the
        ring doesn't flicker between polls — still fast enough to read as
        responsive, not laggy. LEVEL_REFERENCE_RMS is a rough "speaking at
        a normal distance" level, not a precise calibration; a sqrt curve
        keeps quieter sound visible instead of the low end looking
        indistinguishable from silence, since RMS values bunch up there."""
        target = max(0.0, min(1.0, (max(0.0, rms) / LEVEL_REFERENCE_RMS) ** 0.5))
        self._audio_level += (target - self._audio_level) * LEVEL_SMOOTHING
        self.update()

    def _advance_pulse(self):
        self._pulse_phase = (self._pulse_phase + PULSE_INTERVAL_MS / PULSE_PERIOD_MS) % 1.0
        self.update()

    def _button_rect(self):
        # The fixed-size widget is bigger than the visible circle (see
        # __init__) so shadow/pulse have breathing room; the circle itself
        # stays centered within it at a constant 56px diameter.
        size = 56
        x = (self.width() - size) / 2
        y = (self.height() - size) / 2
        return QRect(int(x), int(y), size, size)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._button_rect()
        center = QPointF(rect.center())

        # Ambient pulse while actively listening -- a slow, subtle
        # expanding/fading ring that's always present during recording,
        # regardless of current volume, as a baseline "still going" cue.
        if self._state == "listening":
            base_radius = rect.width() / 2
            pulse_radius = base_radius + self._pulse_phase * 16
            alpha = max(0, int(70 * (1.0 - self._pulse_phase)))
            pulse_color = QColor(theme.DANGER)
            pulse_color.setAlpha(alpha)
            painter.setBrush(QBrush(pulse_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, pulse_radius, pulse_radius)

            # Level ring, layered on top: tracks the *actual* input level
            # in real time (see set_audio_level) rather than a fixed
            # animation, so it visibly responds within a couple hundred
            # ms of actually speaking -- an honest "yes, I hear you"
            # instead of something that would look the same either way.
            if self._audio_level > 0.02:
                level_radius = base_radius + 3 + self._audio_level * 13
                level_color = QColor(255, 255, 255, int(50 + 110 * self._audio_level))
                painter.setBrush(QBrush(level_color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(center, level_radius, level_radius)

        elif self._state == "transcribing":
            # A short rotating arc -- the standard "actively working on
            # it" spinner idiom -- so the tail end of a recording (draining
            # whatever's still queued after you click stop) visibly keeps
            # doing something instead of just sitting on a static color
            # that looks identical whether it's working or stuck.
            arc_rect = QRect(rect).adjusted(-6, -6, 6, 6)
            pen = QPen(QColor(theme.ACCENT))
            pen.setWidth(3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            start_angle = int(-self._pulse_phase * 360 * 16)
            span_angle = 100 * 16
            painter.drawArc(arc_rect, start_angle, span_angle)

        # Soft shadow: a few oversized, low-alpha circles stacked with a
        # slight downward offset, faking a drop shadow without Qt's
        # graphics-effect machinery (which QPainter.drawEllipse-based
        # custom paint can't easily combine with — this keeps the whole
        # button self-contained in one paintEvent).
        shadow_center = center + QPointF(0, 3)
        for i, alpha in enumerate((28, 18, 10)):
            r = rect.width() / 2 + i * 1.5
            painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(shadow_center, r, r)

        color = QColor(STATE_COLORS.get(self._state, theme.FIELD))
        if self._hovered and self._state in ("idle", "listening", "transcribing"):
            color = color.lighter(112)
        painter.setBrush(QBrush(color))
        ring_color = QColor(theme.ACCENT) if self._hovered else QColor(theme.BG)
        painter.setPen(QPen(ring_color, 2))
        painter.drawEllipse(rect)

        icon = self._mic_recording_icon if self._state == "listening" else self._mic_icon
        icon_size = 22
        icon_rect = QRect(
            rect.center().x() - icon_size // 2, rect.center().y() - icon_size // 2,
            icon_size, icon_size,
        )
        icon.paint(painter, icon_rect)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._press_pos = event.globalPosition()
            self._start_top_left = QPointF(self.pos())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition() - self._press_pos
            if not self._dragging and delta.manhattanLength() < 4:
                return
            self._dragging = True
            self._user_positioned = True
            new_pos = (self._start_top_left + delta).toPoint()
            parent = self.parentWidget()
            if parent is not None:
                new_pos.setX(max(0, min(new_pos.x(), parent.width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), parent.height() - self.height())))
            self.move(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._dragging:
            self.clicked.emit()
        self._dragging = False

    def reposition_default(self):
        """Bottom-right corner of the parent, with a margin — called on
        first show and on window resize, unless the user has dragged the
        button somewhere else, in which case resize just keeps it clamped
        within the (possibly now-smaller) parent bounds instead of
        overriding their choice."""
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 20
        if self._user_positioned:
            x = max(0, min(self.x(), parent.width() - self.width()))
            y = max(0, min(self.y(), parent.height() - self.height()))
            self.move(x, y)
        else:
            self.move(parent.width() - self.width() - margin,
                      parent.height() - self.height() - margin)

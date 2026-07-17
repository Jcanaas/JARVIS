from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QBrush, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QPushButton, QWidget

from ..theme import C, qcol
from .anim import HiFpsAnimation


class _MediaBtn(QPushButton):
    """Botón de control multimedia dibujado con QPainter — sin emoji."""
    PREV = "prev"; PLAY = "play"; PAUSE = "pause"; NEXT = "next"

    def __init__(self, shape: str, parent=None):
        super().__init__(parent)
        self._shape = shape
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        self.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.035);
                border: 1px solid rgba(255,255,255,0.075);
                border-radius: 10px;
            }
            QPushButton:hover {
                background: rgba(182,196,255,0.10);
                border-color: rgba(182,196,255,0.28);
            }
            QPushButton:focus {
                border: 2px solid rgba(182,196,255,0.56);
            }
        """)

    def set_shape(self, shape: str):
        if self._shape != shape:
            self._shape = shape
            self.update()

    def enterEvent(self, e):  self._hovered = True;  self.update()
    def leaveEvent(self, e):  self._hovered = False; self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2.0, H / 2.0
        col = qcol(C.PRI if self._hovered else C.TEXT_DIM)
        p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
        if self._shape == self.PREV:
            # |◄  barra izquierda + triángulo izquierda
            p.fillRect(QRectF(cx - 12, cy - 7, 3, 14), col)
            path = QPainterPath()
            path.moveTo(cx - 1,  cy)
            path.lineTo(cx + 9,  cy - 7)
            path.lineTo(cx + 9,  cy + 7)
            path.closeSubpath(); p.drawPath(path)
        elif self._shape == self.NEXT:
            # ►|  triángulo derecha + barra derecha
            path = QPainterPath()
            path.moveTo(cx + 1,  cy)
            path.lineTo(cx - 9,  cy - 7)
            path.lineTo(cx - 9,  cy + 7)
            path.closeSubpath(); p.drawPath(path)
            p.fillRect(QRectF(cx + 9, cy - 7, 3, 14), col)
        elif self._shape == self.PLAY:
            path = QPainterPath()
            path.moveTo(cx + 9,  cy)
            path.lineTo(cx - 5,  cy - 9)
            path.lineTo(cx - 5,  cy + 9)
            path.closeSubpath(); p.drawPath(path)
        elif self._shape == self.PAUSE:
            p.fillRect(QRectF(cx - 7, cy - 8, 4, 16), col)
            p.fillRect(QRectF(cx + 3, cy - 8, 4, 16), col)


class _LikeBtn(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._liked = False
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.035);
                border: 1px solid rgba(255,255,255,0.075);
                border-radius: 10px;
            }
            QPushButton:hover {
                background: rgba(182,196,255,0.10);
                border-color: rgba(182,196,255,0.28);
            }
            QPushButton:disabled {
                background: rgba(255,255,255,0.02);
                border-color: rgba(255,255,255,0.04);
            }
        """)

    def set_liked(self, liked: bool):
        liked = bool(liked)
        if liked != self._liked:
            self._liked = liked
            self.update()

    def is_liked(self) -> bool:
        return self._liked

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(20, 31)
        path.cubicTo(17, 28, 9, 23, 9, 16)
        path.cubicTo(9, 10, 16, 8, 20, 13)
        path.cubicTo(24, 8, 31, 10, 31, 16)
        path.cubicTo(31, 23, 23, 28, 20, 31)
        color = qcol(C.PRI if self._liked else (C.PRI if self.underMouse() else C.TEXT_DIM))
        p.setPen(QPen(color, 1.8))
        p.setBrush(QBrush(color) if self._liked else Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class ToggleSwitch(QWidget):
    """Compact iOS-style on/off switch, painted to match the app's accent."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = HiFpsAnimation(self, setter=self._set_knob)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool, *, emit: bool = False) -> None:
        value = bool(value)
        changed = value != self._checked
        self._checked = value
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if value else 0.0)
        self._anim.start()
        if emit and changed:
            self.toggled.emit(value)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = pyqtProperty(float, _get_knob, _set_knob)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked, emit=True)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        from PyQt6.QtGui import QColor
        from PyQt6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = r.height() / 2
        on = max(0.0, min(1.0, self._pos))

        def mix(a: QColor, b: QColor, t: float) -> QColor:
            return QColor(
                int(a.red()   + (b.red()   - a.red())   * t),
                int(a.green() + (b.green() - a.green()) * t),
                int(a.blue()  + (b.blue()  - a.blue())  * t),
            )

        off_track = qcol("#1E293B")
        on_track = qcol(C.PRI_DIM)
        track = mix(off_track, on_track, on)
        if not self.isEnabled():
            track.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)

        d = r.height() - 6
        x = r.left() + 3 + on * (r.width() - d - 6)
        knob = qcol(C.WHITE) if self.isEnabled() else qcol("#94A3B8")
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


__all__ = [
    '_MediaBtn',
    '_LikeBtn',
    'ToggleSwitch',
]

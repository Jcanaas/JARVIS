from __future__ import annotations

import time

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPoint, QPointF, Qt, QTimer, pyqtSignal

# Intervalo del timer de animación: 6 ms ≈ 166-180 fps reales.
# QPropertyAnimation/QVariantAnimation usan el driver interno de Qt,
# clavado a ~60 fps sin API pública para subirlo; este helper lo sustituye.
ANIM_INTERVAL_MS = 6


def _lerp(a, b, f: float):
    if isinstance(a, QPointF) or isinstance(b, QPointF):
        return QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f)
    if isinstance(a, QPoint) and isinstance(b, QPoint):
        return QPoint(round(a.x() + (b.x() - a.x()) * f),
                      round(a.y() + (b.y() - a.y()) * f))
    return a + (b - a) * f


class HiFpsAnimation(QObject):
    """Animación de valor a alta frecuencia (~180 fps).

    API compatible en espíritu con QVariantAnimation: duración, easing,
    valores inicial/final o keyframes, señales valueChanged/finished.
    `setter` es un callable opcional que recibe el valor en cada tick.
    """

    valueChanged = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, parent=None, *, setter=None):
        super().__init__(parent)
        self._dur_ms = 250
        self._loops = 1
        self._curve = QEasingCurve(QEasingCurve.Type.Linear)
        self._keys: list[tuple[float, object]] = []
        self._setter = setter
        self._start_t = 0.0
        self._delete_when_stopped = False
        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.setInterval(ANIM_INTERVAL_MS)
        self._tmr.timeout.connect(self._on_tick)

    # -- configuración ----------------------------------------------------
    def setDuration(self, ms: int):
        self._dur_ms = max(0, int(ms))

    def setEasingCurve(self, curve):
        self._curve = QEasingCurve(curve)

    def setLoopCount(self, loops: int):
        self._loops = int(loops)  # -1 = infinito, como QAbstractAnimation

    def state(self) -> QAbstractAnimation.State:
        return (QAbstractAnimation.State.Running if self._tmr.isActive()
                else QAbstractAnimation.State.Stopped)

    def setStartValue(self, value):
        self.setKeyValueAt(0.0, value)

    def setEndValue(self, value):
        self.setKeyValueAt(1.0, value)

    def setKeyValueAt(self, step: float, value):
        step = max(0.0, min(1.0, float(step)))
        self._keys = [(s, v) for s, v in self._keys if s != step]
        self._keys.append((step, value))
        self._keys.sort(key=lambda kv: kv[0])

    # -- control -----------------------------------------------------------
    def start(self, *, delete_when_stopped: bool = False):
        self._delete_when_stopped = delete_when_stopped
        if not self._keys:
            return
        if self._dur_ms <= 0:
            self._apply(self._keys[-1][1])
            self._end()
            return
        self._start_t = time.perf_counter()
        self._tmr.start()

    def stop(self):
        self._tmr.stop()

    # -- interno -----------------------------------------------------------
    def _value_at(self, eased: float):
        keys = self._keys
        if eased <= keys[0][0]:
            return keys[0][1]
        for i in range(1, len(keys)):
            s0, v0 = keys[i - 1]
            s1, v1 = keys[i]
            if eased <= s1:
                span = s1 - s0
                f = 1.0 if span <= 0 else (eased - s0) / span
                return _lerp(v0, v1, f)
        return keys[-1][1]

    def _apply(self, value):
        if self._setter is not None:
            self._setter(value)
        self.valueChanged.emit(value)

    def _on_tick(self):
        t = (time.perf_counter() - self._start_t) * 1000.0 / self._dur_ms
        if t >= 1.0:
            if self._loops == -1 or self._loops > 1:
                if self._loops > 1:
                    self._loops -= 1
                # conserva el sobrante para no acumular deriva entre vueltas
                self._start_t += (self._dur_ms / 1000.0) * int(t)
                t %= 1.0
            else:
                self._apply(self._value_at(1.0))
                self._end()
                return
        self._apply(self._value_at(self._curve.valueForProgress(t)))

    def _end(self):
        self._tmr.stop()
        self.finished.emit()
        if self._delete_when_stopped:
            self.deleteLater()


__all__ = ['HiFpsAnimation', 'ANIM_INTERVAL_MS']

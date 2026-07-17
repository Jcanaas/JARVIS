from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QObject
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from ..theme import qcol
from .anim import HiFpsAnimation


_ANIM_DUR = 240
_HOVER_DUR = 150


class HoverGlow(QObject):
	"""Halo azul animado al pasar el ratón por un control interactivo."""

	def __init__(self, w: QWidget, color: str | None = None, radius: int = 40):
		super().__init__(w)
		self._radius = radius
		eff = QGraphicsDropShadowEffect(w)
		eff.setOffset(0, 0)
		eff.setBlurRadius(0.0)
		eff.setColor(qcol(color or "#6E8EFF", 255))
		w.setGraphicsEffect(eff)
		self._eff = eff
		self._anim = HiFpsAnimation(self, setter=eff.setBlurRadius)
		self._anim.setDuration(_HOVER_DUR)
		self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
		w.installEventFilter(self)

	def eventFilter(self, obj, ev):
		t = ev.type()
		if t == QEvent.Type.Enter:
			self._to(self._radius)
		elif t == QEvent.Type.Leave:
			self._to(0.0)
		return False

	def _to(self, end: float):
		self._anim.stop()
		self._anim.setStartValue(self._eff.blurRadius())
		self._anim.setEndValue(end)
		self._anim.start()


def pulse_glow(w: QWidget, color: str | None = None, radius: int = 78):
	"""Destello puntual (feedback de acción: enviar orden/mensaje)."""
	eff = w.graphicsEffect()
	if not isinstance(eff, QGraphicsDropShadowEffect):
		eff = QGraphicsDropShadowEffect(w)
		eff.setOffset(0, 0)
		eff.setBlurRadius(0.0)
		w.setGraphicsEffect(eff)
	eff.setColor(qcol(color or "#7C9AFF", 255))
	anim = HiFpsAnimation(w, setter=eff.setBlurRadius)
	anim.setDuration(550)
	anim.setKeyValueAt(0.0, eff.blurRadius())
	anim.setKeyValueAt(0.25, float(radius))
	anim.setKeyValueAt(1.0, 0.0)
	anim.setEasingCurve(QEasingCurve.Type.OutCubic)
	anim.start(delete_when_stopped=True)


class _SnapshotVeil(QWidget):
	"""Instantánea de la página saliente pintada con opacidad decreciente.

	Pinta directo con QPainter.setOpacity — mucho más barato que un
	QGraphicsOpacityEffect, que re-renderiza el widget a cada frame.
	"""

	def __init__(self, parent: QWidget, pixmap: QPixmap):
		super().__init__(parent)
		self._pix = pixmap
		self._opacity = 1.0
		from PyQt6.QtCore import Qt
		self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

	def set_opacity(self, value: float):
		self._opacity = max(0.0, min(1.0, float(value)))
		self.update()

	def paintEvent(self, _event):
		p = QPainter(self)
		p.setOpacity(self._opacity)
		p.drawPixmap(0, 0, self._pix)


__all__ = ['HoverGlow', 'pulse_glow', '_SnapshotVeil', '_ANIM_DUR', '_HOVER_DUR']

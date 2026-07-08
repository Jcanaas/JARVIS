from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
	QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
)
from PyQt6.QtWidgets import QPushButton

from .theme import C, qcol


def _build_app_icon() -> QIcon:
	pm = QPixmap(256, 256)
	pm.fill(Qt.GlobalColor.transparent)
	p = QPainter(pm)
	p.setRenderHint(QPainter.RenderHint.Antialiasing)

	p.setPen(Qt.PenStyle.NoPen)
	tile = QRadialGradient(QPointF(116, 108), 180)
	tile.setColorAt(0.0, QColor("#17204A"))
	tile.setColorAt(0.55, QColor("#080A1C"))
	tile.setColorAt(1.0, QColor("#05060F"))
	p.setBrush(QBrush(tile))
	p.drawEllipse(QRectF(8, 8, 240, 240))

	for radius, alpha, width in (
		(108, 72, 4.0),
		(88, 105, 3.5),
		(65, 155, 3.0),
	):
		p.setPen(QPen(QColor(182, 196, 255, alpha), width))
		p.setBrush(Qt.BrushStyle.NoBrush)
		p.drawEllipse(QRectF(128 - radius, 128 - radius, radius * 2, radius * 2))

	p.setPen(QPen(QColor(182, 196, 255, 185), 5))
	p.drawArc(QRectF(20, 20, 216, 216), 15 * 16, 82 * 16)
	p.drawArc(QRectF(20, 20, 216, 216), 195 * 16, 82 * 16)

	glow = QRadialGradient(QPointF(128, 128), 82)
	glow.setColorAt(0.0, QColor(240, 255, 255, 245))
	glow.setColorAt(0.16, QColor(74, 222, 128, 230))
	glow.setColorAt(0.43, QColor(182, 196, 255, 190))
	glow.setColorAt(0.74, QColor(94, 130, 255, 75))
	glow.setColorAt(1.0, QColor(8, 11, 18, 0))
	p.setPen(Qt.PenStyle.NoPen)
	p.setBrush(QBrush(glow))
	p.drawEllipse(QRectF(47, 47, 162, 162))

	core = QRadialGradient(QPointF(128, 128), 38)
	core.setColorAt(0.0, QColor(255, 255, 255, 255))
	core.setColorAt(0.35, QColor(74, 222, 128, 235))
	core.setColorAt(1.0, QColor(94, 130, 255, 0))
	p.setBrush(QBrush(core))
	p.drawEllipse(QRectF(90, 90, 76, 76))

	p.setPen(QPen(QColor(200, 255, 255, 205), 4.0))
	wave = QPainterPath()
	wave.moveTo(47, 128)
	wave.cubicTo(76, 128, 85, 112, 106, 120)
	wave.cubicTo(123, 126, 133, 143, 151, 135)
	wave.cubicTo(170, 126, 179, 128, 209, 128)
	p.drawPath(wave)
	p.end()
	return QIcon(pm)


def _line_icon(name: str, color: str = C.TEXT_DIM, size: int = 20) -> QIcon:
	pm = QPixmap(size, size)
	pm.fill(Qt.GlobalColor.transparent)
	p = QPainter(pm)
	p.setRenderHint(QPainter.RenderHint.Antialiasing)
	scale = size / 24.0
	p.scale(scale, scale)
	pen = QPen(qcol(color), 1.8)
	pen.setCapStyle(Qt.PenCapStyle.RoundCap)
	pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
	p.setPen(pen)
	p.setBrush(Qt.BrushStyle.NoBrush)

	def line(x1, y1, x2, y2):
		p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

	if name == "send":
		path = QPainterPath()
		path.moveTo(4, 5); path.lineTo(21, 12); path.lineTo(4, 19)
		path.lineTo(7, 12); path.closeSubpath()
		p.drawPath(path); line(7, 12, 21, 12)
	elif name == "mic":
		p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
		p.drawArc(QRectF(6, 8, 12, 10), 180 * 16, 180 * 16)
		line(12, 18, 12, 21); line(9, 21, 15, 21)
	elif name == "mic_off":
		p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
		p.drawArc(QRectF(6, 8, 12, 10), 180 * 16, 180 * 16)
		line(12, 18, 12, 21); line(9, 21, 15, 21); line(4, 4, 20, 20)
	elif name == "fullscreen":
		line(4, 9, 4, 4); line(4, 4, 9, 4)
		line(15, 4, 20, 4); line(20, 4, 20, 9)
		line(20, 15, 20, 20); line(20, 20, 15, 20)
		line(9, 20, 4, 20); line(4, 20, 4, 15)
	elif name == "panel_close":
		p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
		line(16, 4, 16, 20); line(12, 9, 9, 12); line(9, 12, 12, 15)
	elif name == "panel_open":
		p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
		line(16, 4, 16, 20); line(10, 9, 13, 12); line(13, 12, 10, 15)
	elif name == "chevron_down":
		line(6, 9, 12, 15); line(12, 15, 18, 9)
	elif name == "chevron_up":
		line(6, 15, 12, 9); line(12, 9, 18, 15)
	elif name == "chevron_left":
		line(15, 5, 8, 12); line(8, 12, 15, 19)
	elif name == "chevron_right":
		line(9, 5, 16, 12); line(16, 12, 9, 19)
	elif name == "search":
		p.drawEllipse(QRectF(4, 4, 11, 11)); line(14, 14, 20, 20)
	elif name == "filter":
		path = QPainterPath()
		path.moveTo(4, 6)
		path.lineTo(20, 6)
		path.lineTo(14, 12)
		path.lineTo(14, 18)
		path.lineTo(10, 20)
		path.lineTo(10, 12)
		path.closeSubpath()
		p.drawPath(path)
	elif name == "more":
		p.setBrush(QBrush(qcol(color)))
		for x in (6, 12, 18):
			p.drawEllipse(QRectF(x - 1, 11, 2, 2))
	elif name == "upload":
		line(12, 16, 12, 4); line(7, 9, 12, 4); line(17, 9, 12, 4)
		p.drawRoundedRect(QRectF(4, 15, 16, 6), 2, 2)
	elif name == "download":
		line(12, 4, 12, 16); line(7, 11, 12, 16); line(17, 11, 12, 16)
		p.drawRoundedRect(QRectF(4, 15, 16, 6), 2, 2)
	elif name == "share":
		p.drawEllipse(QRectF(4, 10, 4, 4))
		p.drawEllipse(QRectF(16, 4, 4, 4))
		p.drawEllipse(QRectF(16, 16, 4, 4))
		line(8, 11, 16, 7); line(8, 13, 16, 17)
	elif name == "edit":
		path = QPainterPath()
		path.moveTo(5, 19); path.lineTo(8, 14); path.lineTo(16, 6)
		path.lineTo(19, 9); path.lineTo(11, 17); path.closeSubpath()
		p.drawPath(path); line(14, 8, 17, 11); line(5, 19, 10, 18)
	elif name == "trash":
		p.drawRoundedRect(QRectF(7, 7, 10, 13), 1, 1)
		line(5, 7, 19, 7); line(9, 4, 15, 4); line(10, 10, 10, 17); line(14, 10, 14, 17)
	elif name == "folder":
		path = QPainterPath()
		path.moveTo(3, 7); path.lineTo(9, 7); path.lineTo(11, 10)
		path.lineTo(21, 10); path.lineTo(19, 20); path.lineTo(3, 20)
		path.closeSubpath(); p.drawPath(path)
	elif name == "external":
		p.drawRoundedRect(QRectF(4, 7, 13, 13), 2, 2)
		line(11, 4, 20, 4); line(20, 4, 20, 13)
		line(20, 4, 10, 14)
	elif name == "play":
		path = QPainterPath()
		path.moveTo(8, 5); path.lineTo(19, 12); path.lineTo(8, 19)
		path.closeSubpath(); p.drawPath(path)
	elif name == "pause":
		line(9, 6, 9, 18); line(15, 6, 15, 18)
	elif name == "close":
		line(6, 6, 18, 18); line(18, 6, 6, 18)
	elif name == "refresh":
		p.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 285 * 16)
		line(17, 4, 20, 4); line(20, 4, 20, 7)
	elif name == "home":
		path = QPainterPath()
		path.moveTo(4, 11); path.lineTo(12, 4); path.lineTo(20, 11)
		path.moveTo(6, 10); path.lineTo(6, 20); path.lineTo(18, 20); path.lineTo(18, 10)
		p.drawPath(path); p.drawRect(QRectF(10, 14, 4, 6))
	elif name == "chat":
		p.drawRoundedRect(QRectF(3, 4, 18, 14), 4, 4)
		path = QPainterPath()
		path.moveTo(8, 18); path.lineTo(7, 21); path.lineTo(12, 18)
		p.drawPath(path)
	elif name == "mail":
		p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
		line(4, 7, 12, 13); line(12, 13, 20, 7)
	elif name == "drive":
		path = QPainterPath()
		path.moveTo(9, 3); path.lineTo(15, 3); path.lineTo(21, 14)
		path.lineTo(18, 20); path.lineTo(6, 20); path.lineTo(3, 14)
		path.closeSubpath(); p.drawPath(path)
		line(9, 3, 3, 14); line(15, 3, 21, 14); line(3, 14, 21, 14)
	elif name == "music":
		line(10, 6, 19, 4); line(10, 6, 10, 17); line(19, 4, 19, 15)
		p.drawEllipse(QRectF(6, 16, 4, 3)); p.drawEllipse(QRectF(15, 14, 4, 3))
	elif name == "youtube":
		p.drawRoundedRect(QRectF(3, 6, 18, 12), 4, 4)
		tri = QPainterPath()
		tri.moveTo(10, 9); tri.lineTo(15, 12); tri.lineTo(10, 15)
		tri.closeSubpath(); p.drawPath(tri)
	elif name == "plus":
		line(12, 5, 12, 19); line(5, 12, 19, 12)
	elif name == "calendar":
		p.drawRoundedRect(QRectF(3, 5, 18, 16), 3, 3)
		line(3, 10, 21, 10); line(8, 3, 8, 7); line(16, 3, 16, 7)
		p.setBrush(qcol(color))
		for _dx, _dy in ((7, 14), (12, 14), (17, 14), (7, 18), (12, 18)):
			p.drawEllipse(QRectF(_dx - 1, _dy - 1, 2, 2))
	elif name == "settings":
		p.drawEllipse(QRectF(9, 9, 6, 6))
		import math as _m
		for _i in range(8):
			_ang = _m.radians(_i * 45)
			_cx, _cy = 12, 12
			_x1 = _cx + 7 * _m.cos(_ang); _y1 = _cy + 7 * _m.sin(_ang)
			_x2 = _cx + 9.5 * _m.cos(_ang); _y2 = _cy + 9.5 * _m.sin(_ang)
			line(_x1, _y1, _x2, _y2)
	elif name == "volume":
		spk = QPainterPath()
		spk.moveTo(4, 9); spk.lineTo(8, 9); spk.lineTo(12, 5)
		spk.lineTo(12, 19); spk.lineTo(8, 15); spk.lineTo(4, 15)
		spk.closeSubpath(); p.drawPath(spk)
		p.drawArc(QRectF(13, 8, 5, 8), -70 * 16, 140 * 16)
		p.drawArc(QRectF(13, 5, 9, 14), -60 * 16, 120 * 16)
	elif name == "pip":
		p.drawRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
		p.drawRoundedRect(QRectF(12, 12, 7, 5), 1.2, 1.2)
	elif name == "forward":
		p.setBrush(qcol(color))
		for off in (0, 8):
			tri = QPainterPath()
			tri.moveTo(4 + off, 6); tri.lineTo(11 + off, 12); tri.lineTo(4 + off, 18)
			tri.closeSubpath(); p.drawPath(tri)
	elif name == "backward":
		p.setBrush(qcol(color))
		for off in (0, 8):
			tri = QPainterPath()
			tri.moveTo(20 - off, 6); tri.lineTo(13 - off, 12); tri.lineTo(20 - off, 18)
			tri.closeSubpath(); p.drawPath(tri)
	elif name == "fullscreen_exit":
		line(8, 4, 8, 8); line(8, 8, 4, 8)
		line(16, 4, 16, 8); line(16, 8, 20, 8)
		line(8, 20, 8, 16); line(8, 16, 4, 16)
		line(16, 20, 16, 16); line(16, 16, 20, 16)
	elif name == "volume_off":
		spk = QPainterPath()
		spk.moveTo(4, 9); spk.lineTo(8, 9); spk.lineTo(12, 5)
		spk.lineTo(12, 19); spk.lineTo(8, 15); spk.lineTo(4, 15)
		spk.closeSubpath(); p.drawPath(spk)
		line(15, 9, 21, 15); line(21, 9, 15, 15)
	elif name == "playlist":
		for y in (6, 12, 18):
			p.drawEllipse(QRectF(4, y - 1, 2, 2))
			line(9, y, 20, y)
	elif name == "heart":
		path = QPainterPath()
		path.moveTo(12, 20)
		path.cubicTo(10, 18, 4, 14, 4, 9)
		path.cubicTo(4, 5, 9, 3, 12, 7)
		path.cubicTo(15, 3, 20, 5, 20, 9)
		path.cubicTo(20, 14, 14, 18, 12, 20)
		p.drawPath(path)
	elif name == "shuffle":
		line(4, 7, 7, 7); line(7, 7, 17, 17); line(17, 17, 20, 17)
		line(16, 14, 20, 17); line(20, 17, 16, 20)
		line(4, 17, 7, 17); line(7, 17, 11, 13)
		line(13, 11, 17, 7); line(17, 7, 20, 7)
		line(16, 4, 20, 7); line(20, 7, 16, 10)
	elif name == "film":
		p.drawRoundedRect(QRectF(4, 5, 16, 14), 2, 2)
		line(8, 5, 8, 19); line(16, 5, 16, 19); line(4, 10, 20, 10); line(4, 14, 20, 14)
		for x in (5.8, 17.2):
			for y in (7, 12, 17):
				p.drawEllipse(QRectF(x - 0.8, y - 0.8, 1.6, 1.6))
	elif name == "tv":
		p.drawRoundedRect(QRectF(3, 5, 18, 13), 2, 2)
		line(8, 18, 16, 18); line(12, 18, 12, 21)
		line(7, 9, 10, 12); line(10, 12, 7, 15)
		p.drawEllipse(QRectF(13, 10, 3, 3)); p.drawEllipse(QRectF(13, 14, 1.5, 1.5))
	elif name in {"file", "image", "video", "audio", "code", "archive", "chart"}:
		path = QPainterPath()
		path.moveTo(6, 3); path.lineTo(15, 3); path.lineTo(19, 7)
		path.lineTo(19, 21); path.lineTo(6, 21); path.closeSubpath()
		p.drawPath(path); line(15, 3, 15, 7); line(15, 7, 19, 7)
		if name == "image":
			p.drawEllipse(QRectF(9, 9, 2.5, 2.5))
			line(8, 18, 12, 14); line(12, 14, 14, 16); line(14, 16, 17, 13)
		elif name == "video":
			p.drawRoundedRect(QRectF(8, 10, 6, 6), 1, 1)
			path = QPainterPath()
			path.moveTo(14, 12); path.lineTo(17, 10); path.lineTo(17, 16)
			path.lineTo(14, 14); path.closeSubpath(); p.drawPath(path)
		elif name == "audio":
			line(14, 9, 14, 16); line(14, 9, 18, 8); line(18, 8, 18, 14)
			p.drawEllipse(QRectF(11, 15, 3, 2)); p.drawEllipse(QRectF(15, 13, 3, 2))
		elif name == "code":
			line(11, 11, 8, 14); line(8, 14, 11, 17)
			line(15, 11, 18, 14); line(18, 14, 15, 17)
		elif name == "archive":
			p.drawRect(QRectF(8, 10, 9, 8)); line(8, 13, 17, 13); line(11, 16, 14, 16)
		elif name == "chart":
			line(9, 17, 9, 14); line(13, 17, 13, 11); line(17, 17, 17, 8)
		else:
			line(9, 11, 16, 11); line(9, 15, 16, 15)
	p.end()
	return QIcon(pm)


def _icon_button(
	name: str,
	tooltip: str,
	size: int = 38,
	icon_size: int = 19,
	accent: bool = False,
) -> QPushButton:
	from .widgets import HoverGlow
	button = QPushButton()
	button.setFixedSize(size, size)
	button.setIcon(_line_icon(name, C.PRI if accent else C.TEXT_DIM, icon_size))
	button.setIconSize(QSize(icon_size, icon_size))
	button.setToolTip(tooltip)
	button.setAccessibleName(tooltip)
	button.setCursor(Qt.CursorShape.PointingHandCursor)
	button.setStyleSheet(f"""
		QPushButton {{
			background: {"rgba(94, 130, 255, 0.16)" if accent else "rgba(255, 255, 255, 0.045)"};
			border: 1px solid {"rgba(182, 196, 255, 0.30)" if accent else "rgba(255, 255, 255, 0.09)"};
			border-radius: {min(10, size // 3)}px;
			padding: 0;
		}}
		QPushButton:hover {{
			background: {"rgba(94, 130, 255, 0.24)" if accent else "rgba(255, 255, 255, 0.09)"};
			border-color: rgba(182, 196, 255, 0.38);
		}}
		QPushButton:pressed {{ background: rgba(94, 130, 255, 0.12); }}
		QPushButton:focus {{ border: 2px solid rgba(182, 196, 255, 0.62); }}
		QPushButton:disabled {{ background: rgba(255, 255, 255, 0.02); border-color: rgba(255, 255, 255, 0.04); }}
	""")
	if accent:
		HoverGlow(button)
	return button


__all__ = ['_build_app_icon', '_line_icon', '_icon_button']

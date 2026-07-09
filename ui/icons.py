from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
	QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QPushButton

from .theme import C, qcol


# Full-SVG icons (user-supplied). Rendered via QSvgRenderer and tinted to the
# theme colour with a SourceIn composite, so any black fill/stroke — or
# currentColor, normalised to black first — takes the icon colour while keeping
# antialiased edges. Keeps complex bezier/multi-subpath art out of the manual
# QPainterPath drawing below.
_SVG_ICONS = {
	"gamepad": (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
		'<path d="M467.51,248.83c-18.4-83.18-45.69-136.24-89.43-149.17A91.5,91.5,0,0,0,352,96'
		'c-26.89,0-48.11,16-96,16s-69.15-16-96-16a99.09,99.09,0,0,0-27.2,3.66'
		'C89,112.59,61.94,165.7,43.33,248.83c-19,84.91-15.56,152,21.58,164.88'
		'c26,9,49.25-9.61,71.27-37c25-31.2,55.79-40.8,119.82-40.8s93.62,9.6,118.66,40.8'
		'c22,27.41,46.11,45.79,71.42,37.16C487.1,399.86,486.52,334.74,467.51,248.83Z" '
		'fill="none" stroke="#000" stroke-miterlimit="10" stroke-width="48"/>'
		'<circle cx="292" cy="224" r="20" fill="#000"/>'
		'<path d="M336,288a20,20,0,1,1,20-19.95A20,20,0,0,1,336,288Z" fill="#000"/>'
		'<circle cx="336" cy="180" r="20" fill="#000"/>'
		'<circle cx="380" cy="224" r="20" fill="#000"/>'
		'<line x1="160" y1="176" x2="160" y2="272" fill="none" stroke="#000" '
		'stroke-linecap="round" stroke-linejoin="round" stroke-width="48"/>'
		'<line x1="208" y1="224" x2="112" y2="224" fill="none" stroke="#000" '
		'stroke-linecap="round" stroke-linejoin="round" stroke-width="48"/></svg>'
	),
	"film": (
		'<svg xmlns="http://www.w3.org/2000/svg" fill="#000" viewBox="0 0 16 16">'
		'<path d="M6 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0M1 3a2 2 0 1 0 4 0 2 2 0 0 0-4 0"/>'
		'<path d="M9 6h.5a2 2 0 0 1 1.983 1.738l3.11-1.382A1 1 0 0 1 16 7.269v7.462'
		'a1 1 0 0 1-1.406.913l-3.111-1.382A2 2 0 0 1 9.5 16H2a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z'
		'm6 8.73V7.27l-3.5 1.555v4.35zM1 8v6a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H2'
		'a1 1 0 0 0-1 1"/>'
		'<path d="M9 6a3 3 0 1 0 0-6 3 3 0 0 0 0 6M7 3a2 2 0 1 1 4 0 2 2 0 0 1-4 0"/></svg>'
	),
	"youtube": (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
		'stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
		'<path d="M2 8a4 4 0 0 1 4 -4h12a4 4 0 0 1 4 4v8a4 4 0 0 1 -4 4h-12a4 4 0 0 1 -4 -4v-8z"/>'
		'<path d="M10 9l5 3l-5 3z"/></svg>'
	),
	"drive_svg": (
		'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
		'<path d="M12 10l-6 10l-3 -5l6 -10z"/>'
		'<path d="M9 15h12l-3 5h-12"/>'
		'<path d="M15 15l-6 -10h6l6 10z"/>'
		'</svg>'
	),
	"corona": (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 224 280">'
		'<path d="M 115 12 L 111 80 L 110 81 L 110 89 L 109 90 L 107 109 L 104 119 L 102 121 L 95 121 L 92 116 L 90 98 L 89 97 L 89 90 L 88 89 L 84 38 L 82 48 L 78 93 L 77 94 L 74 117 L 72 124 L 65 128 L 61 127 L 57 112 L 51 66 L 50 78 L 49 79 L 44 143 L 57 149 L 71 159 L 83 171 L 94 186 L 101 199 L 109 222 L 113 242 L 115 268 L 116 268 L 117 249 L 118 248 L 118 242 L 121 226 L 126 209 L 134 191 L 147 172 L 165 155 L 174 149 L 187 143 L 187 138 L 186 137 L 184 101 L 183 100 L 183 90 L 182 89 L 182 79 L 181 78 L 180 66 L 175 106 L 170 127 L 166 128 L 159 124 L 153 93 L 149 48 L 147 38 L 143 89 L 142 90 L 142 97 L 141 98 L 138 119 L 136 121 L 129 121 L 127 119 L 124 109 L 122 91 L 121 90 L 119 57 L 118 56 L 118 41 L 117 40 L 117 24 L 116 23 L 116 13 Z" fill="none" stroke="#000000" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>'
		'</svg>'
	),
	"settings_svg": (
		'<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="#000">'
		'<path stroke-linecap="round" stroke-linejoin="round" d="M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008Z"/>'
		'</svg>'
	),
	"calendar_svg": (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512">'
		'<path fill="#000" d="M120 0c13.3 0 24 10.7 24 24l0 40 160 0 0-40c0-13.3 10.7-24 24-24s24 10.7 24 24l0 40 32 0c35.3 0 64 28.7 64 64l0 288c0 35.3-28.7 64-64 64L64 480c-35.3 0-64-28.7-64-64L0 128C0 92.7 28.7 64 64 64l32 0 0-40c0-13.3 10.7-24 24-24zM384 432c8.8 0 16-7.2 16-16l0-64-88 0 0 80 72 0zm16-128l0-80-88 0 0 80 88 0zm-136 0l0-80-80 0 0 80 80 0zm-128 0l0-80-88 0 0 80 88 0zM48 352l0 64c0 8.8 7.2 16 16 16l72 0 0-80-88 0zm136 0l0 80 80 0 0-80-80 0zM120 112l-56 0c-8.8 0-16 7.2-16 16l0 48 352 0 0-48c0-8.8-7.2-16-16-16l-264 0z"/>'
		'</svg>'
	),
}


def _svg_icon(svg: str, color: str, size: int) -> QIcon:
	pm = QPixmap(size, size)
	pm.fill(Qt.GlobalColor.transparent)
	p = QPainter(pm)
	p.setRenderHint(QPainter.RenderHint.Antialiasing)
	# Small inset so strokes don't clip the box edges, matching the visual
	# weight of the hand-drawn 24-unit icons (content sits ~2..22).
	inset = size * 0.08
	QSvgRenderer(svg.encode("utf-8")).render(
		p, QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
	)
	p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
	p.fillRect(pm.rect(), qcol(color))
	p.end()
	return QIcon(pm)


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


# Filled anime silhouette (user-supplied SVG, viewBox 692x712). Only M/L/Z
# commands, absolute coords — parsed once into a QPainterPath in its native
# coordinate space; _line_icon fits it into the 24-unit icon box.
_ANIME_SVG_D = (
	"M 456 201 L 450 197 L 434 192 L 418 192 L 402 195 L 383 202 L 280 251 L 237 269 "
	"L 234 268 L 235 257 L 250 183 L 252 165 L 205 217 L 130 305 L 82 368 L 59 404 "
	"L 51 420 L 45 439 L 46 451 L 51 457 L 56 459 L 69 458 L 79 455 L 114 440 L 252 369 "
	"L 285 356 L 299 353 L 314 356 L 320 361 L 323 370 L 323 377 L 299 528 L 290 530 "
	"L 67 534 L 36 592 L 17 640 L 12 665 L 12 679 L 15 691 L 20 698 L 38 699 L 60 695 "
	"L 317 633 L 372 623 L 381 619 L 393 608 L 408 584 L 414 565 L 419 445 L 425 391 "
	"L 434 341 L 451 282 L 467 244 L 468 229 L 466 217 L 463 209 Z "
	"M 613 35 L 550 19 L 492 12 L 451 47 L 416 84 L 393 113 L 379 134 L 368 157 L 445 178 "
	"L 487 135 L 554 74 L 595 43 Z "
	"M 679 93 L 651 73 L 635 65 L 621 62 L 580 90 L 522 136 L 481 175 L 478 182 L 500 205 "
	"L 519 220 L 532 227 L 539 227 L 575 205 L 620 168 L 661 124 L 674 105 Z"
)
_ANIME_VIEWBOX = (692.0, 712.0)


def _parse_svg_subpaths(d: str) -> QPainterPath:
	path = QPainterPath()
	path.setFillRule(Qt.FillRule.OddEvenFill)
	tokens = d.split()
	i = 0
	while i < len(tokens):
		cmd = tokens[i]
		if cmd == "M":
			path.moveTo(float(tokens[i + 1]), float(tokens[i + 2])); i += 3
		elif cmd == "L":
			path.lineTo(float(tokens[i + 1]), float(tokens[i + 2])); i += 3
		elif cmd == "Z":
			path.closeSubpath(); i += 1
		else:
			i += 1
	return path


_ANIME_PATH = _parse_svg_subpaths(_ANIME_SVG_D)


def _line_icon(name: str, color: str = C.TEXT_DIM, size: int = 20) -> QIcon:
	svg = _SVG_ICONS.get(name)
	if svg is not None:
		return _svg_icon(svg, color, size)

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
	elif name == "anime":
		# Filled silhouette: fit the native 692x712 path into the 24-unit box,
		# preserving aspect and centring, then fill (no stroke).
		vw, vh = _ANIME_VIEWBOX
		k = 22.0 / max(vw, vh)
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(qcol(color))
		p.save()
		p.translate((24 - vw * k) / 2, (24 - vh * k) / 2)
		p.scale(k, k)
		p.drawPath(_ANIME_PATH)
		p.restore()
	elif name == "cards":
		p.drawRoundedRect(QRectF(3, 6, 12, 16), 2, 2)
		p.save()
		p.translate(17, 6)
		p.rotate(18)
		p.drawRoundedRect(QRectF(0, 0, 10, 14), 2, 2)
		p.restore()
	elif name == "gamepad":
		p.drawRoundedRect(QRectF(2, 8, 20, 11), 5, 5)
		line(7, 11, 7, 16); line(4.5, 13.5, 9.5, 13.5)
		p.setBrush(qcol(color))
		p.drawEllipse(QRectF(15.5, 10.5, 2, 2))
		p.drawEllipse(QRectF(18, 13, 2, 2))
		p.drawEllipse(QRectF(7, 12, 4, 4))
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

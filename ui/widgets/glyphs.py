from __future__ import annotations

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from ..theme import C


class _SearchSvgIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        g1 = QLinearGradient(2, 2, 16, 16)
        g1.setColorAt(0.0, QColor(C.PRI))
        g1.setColorAt(0.5, QColor(C.PRI_DIM))
        pen = QPen()
        pen.setBrush(QBrush(g1))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(3, 3, 11, 11))
        g2 = QLinearGradient(13.0, 13.0, 17, 17)
        g2.setColorAt(0.0, QColor(C.PRI_DIM))
        g2.setColorAt(0.5, QColor("#7d93ff"))
        pen.setBrush(QBrush(g2))
        p.setPen(pen)
        p.drawLine(QPointF(17, 17), QPointF(13.0, 13.0))


class _FilterIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 40)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0.0, QColor("#10142a"))
        bg.setColorAt(0.52, QColor("#060915"))
        bg.setColorAt(1.0, QColor("#16204b"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)
        path = QPainterPath()
        path.moveTo(8.16, 6.65002)
        path.lineTo(15.83, 6.65002)
        path.cubicTo(16.47, 6.65002, 16.99, 7.17002, 16.99, 7.81002)
        path.lineTo(16.99, 9.09002)
        path.cubicTo(16.99, 9.56002, 16.70, 10.14, 16.41, 10.43)
        path.lineTo(13.91, 12.64)
        path.cubicTo(13.56, 12.93, 13.33, 13.51, 13.33, 13.98)
        path.lineTo(13.33, 16.48)
        path.cubicTo(13.33, 16.83, 13.10, 17.29, 12.81, 17.47)
        path.lineTo(12.00, 17.98)
        path.cubicTo(11.24, 18.45, 10.20, 17.92, 10.20, 16.99)
        path.lineTo(10.20, 13.91)
        path.cubicTo(10.20, 13.50, 9.97, 12.98, 9.73, 12.69)
        path.lineTo(7.52, 10.36)
        path.cubicTo(7.23, 10.08, 7.00, 9.55002, 7.00, 9.20002)
        path.lineTo(7.00, 7.87002)
        path.cubicTo(7.00, 7.17002, 7.52, 6.65002, 8.16, 6.65002)
        path.closeSubpath()
        p.save()
        p.translate((self.width() - 27) / 2, (self.height() - 27) / 2)
        p.scale(27 / 14.832, 27 / 15.408)
        p.translate(-4.8, -4.56)
        pen = QPen(QColor(C.PRI), 1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()


__all__ = [
    '_SearchSvgIcon',
    '_FilterIcon',
]

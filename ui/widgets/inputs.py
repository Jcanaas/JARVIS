from __future__ import annotations

import math

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import C, qcol, _scrollbar_qss
from .glyphs import _SearchSvgIcon, _FilterIcon

class _InputMask(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(self.rect(), g)


class _BlueMask(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity = 0.8
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def setOpacity(self, value: float):
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        g = QRadialGradient(QPointF(45, 40), 42)
        g.setColorAt(0.0, QColor(94, 130, 255, int(255 * self._opacity)))
        g.setColorAt(0.45, QColor(94, 130, 255, int(130 * self._opacity)))
        g.setColorAt(1.0, QColor(94, 130, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))


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


class SearchGlowInput(QFrame):
    textChanged = pyqtSignal(str)
    returnPressed = pyqtSignal()

    CANVAS_W = 280
    CANVAS_H = 46

    MAIN_W = 268
    MAIN_H = 40

    BASE_ANGLES = {"dark": 82.0, "glow": 60.0, "white": 83.0, "border": 70.0}
    HOVER_ANGLES = {"dark": -98.0, "glow": -120.0, "white": -97.0, "border": -110.0}
    FOCUS_ANGLES = {"dark": 442.0, "glow": 420.0, "white": 443.0, "border": 430.0}

    def __init__(self, placeholder: str = "Search...", text: str = "", parent=None, show_filter: bool = False):
        super().__init__(parent)
        self.setObjectName("SearchGlowInput")
        self.setMinimumHeight(self.CANVAS_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        self._show_filter = bool(show_filter)
        self._hovered = False
        self._glow_level = 0.55
        self._pulse = 0.0
        self._angles = dict(self.BASE_ANGLES)
        self._angle_from = dict(self.BASE_ANGLES)
        self._angle_to = dict(self.BASE_ANGLES)
        self._layer_cache: dict = {}
        self._filter_angle = 90.0

        self._line = QLineEdit(text, self)
        self._line.setPlaceholderText(placeholder)
        self._line.setFrame(False)
        self._line.setTextMargins(36, 0, 16 if not self._show_filter else 40, 0)
        self._line.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                color: {C.TEXT};
                font-size: 14px;
                padding: 0px;
                selection-background-color: rgba(182, 196, 255, 0.30);
            }}
        """)
        pal = self._line.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(C.TEXT))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8EA0D9"))
        self._line.setPalette(pal)
        self._line.textChanged.connect(self._on_text_changed)
        self._line.returnPressed.connect(self.returnPressed.emit)
        self._line.installEventFilter(self)

        self._input_mask = _InputMask(self)
        self._blue_mask = _BlueMask(self)
        self._search_icon = _SearchSvgIcon(self)
        self._filter_icon = _FilterIcon(self) if self._show_filter else None

        self._input_mask.hide()
        self._blue_mask.hide()
        self._line.raise_()
        if self._filter_icon is not None:
            self._filter_icon.raise_()
        self._search_icon.raise_()

        self._angle_anim = QVariantAnimation(self)
        self._angle_anim.setStartValue(0.0)
        self._angle_anim.setEndValue(1.0)
        self._angle_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._angle_anim.valueChanged.connect(self._apply_angle_progress)

        self._blue_anim = QVariantAnimation(self)
        self._blue_anim.setDuration(2000)
        self._blue_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._blue_anim.valueChanged.connect(self._set_blue_opacity)

        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(1200)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._pulse_anim.valueChanged.connect(self._set_pulse)

        self._spin_anim = QVariantAnimation(self)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(360.0)
        self._spin_anim.setDuration(4000)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._spin_anim.valueChanged.connect(self._set_filter_angle)
        self._spin_anim.start()

        self._update_children_geometry()
        self._update_state()

    def sizeHint(self):
        return QSize(self.CANVAS_W, self.CANVAS_H)

    def minimumSizeHint(self):
        return QSize(180, self.CANVAS_H)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_children_geometry()
        self._layer_cache.clear()

    def clear(self):
        self._line.clear()

    def text(self) -> str:
        return self._line.text()

    def setText(self, text: str):
        self._line.setText(text)

    def setPlaceholderText(self, placeholder: str):
        self._line.setPlaceholderText(placeholder)

    def selectAll(self):
        self._line.selectAll()

    def setFocus(self):
        self._line.setFocus()

    def setReadOnly(self, read_only: bool):
        self._line.setReadOnly(read_only)

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self._line.setEnabled(enabled)

    def eventFilter(self, obj, event):
        if obj is self._line and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._update_state()
        return super().eventFilter(obj, event)

    def enterEvent(self, event):
        self._hovered = True
        self._update_state()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._update_state()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._line.setFocus()

    def mousePressEvent(self, event):
        self._line.setFocus()
        super().mousePressEvent(event)

    def _main_rect(self) -> QRectF:
        return QRectF(1, 1, max(10, self.width() - 2), max(10, self.height() - 2))

    def _main_rect_i(self):
        return 1, 1, max(10, self.width() - 2), max(10, self.height() - 2)

    def _center_rect(self, w: int, h: int) -> QRectF:
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _update_children_geometry(self):
        x, y, w, h = self._main_rect_i()
        self._line.setGeometry(x, y, w, h)
        self._input_mask.setGeometry(0, 0, 0, 0)
        self._blue_mask.setGeometry(0, 0, 0, 0)
        self._search_icon.setGeometry(x + 13, y + 11, 18, 18)
        if self._filter_icon is not None:
            self._filter_icon.setGeometry(x + w - 44, y + 3, 38, 40)
            self._filter_icon.setVisible(True)

    def _on_text_changed(self, text: str):
        self.textChanged.emit(text)

    def _update_state(self):
        focused = self._line.hasFocus()
        has_text = bool(self._line.text().strip())
        self._input_mask.hide()
        if focused:
            target = self.FOCUS_ANGLES
            duration = 4000
        elif self._hovered:
            target = self.HOVER_ANGLES
            duration = 2000
        else:
            target = self.BASE_ANGLES
            duration = 2000
        self._animate_angles(target, duration)
        if focused or has_text:
            self._animate_blue(1.0)
            if self._pulse_anim.state() != QAbstractAnimation.State.Running:
                self._pulse_anim.start()
        else:
            self._animate_blue(0.78 if self._hovered else 0.55)
            if self._pulse_anim.state() == QAbstractAnimation.State.Running:
                self._pulse_anim.stop()
            self._pulse = 0.0
        self._search_icon.update()
        if self._filter_icon is not None:
            self._filter_icon.setVisible(True)
            self._filter_icon.update()

    def _animate_angles(self, target: dict[str, float], duration: int):
        if self._angle_anim.state() == QAbstractAnimation.State.Running and self._angle_to == target:
            return
        if all(abs(self._angles[k] - target[k]) < 0.01 for k in target):
            return
        if self._angle_anim.state() == QAbstractAnimation.State.Running:
            self._angle_anim.stop()
        self._angle_from = dict(self._angles)
        self._angle_to = dict(target)
        self._angle_anim.setDuration(duration)
        self._angle_anim.setStartValue(0.0)
        self._angle_anim.setEndValue(1.0)
        self._angle_anim.start()

    def _apply_angle_progress(self, value):
        t = float(value)
        for key in self._angles:
            a = self._angle_from[key]
            b = self._angle_to[key]
            self._angles[key] = a + (b - a) * t
        self._layer_cache.clear()
        self.update()

    def _animate_blue(self, target: float):
        if abs(self._glow_level - target) < 0.01:
            return
        if self._blue_anim.state() == QAbstractAnimation.State.Running:
            self._blue_anim.stop()
        self._blue_anim.setStartValue(self._glow_level)
        self._blue_anim.setEndValue(target)
        self._blue_anim.start()

    def _set_blue_opacity(self, value):
        self._glow_level = float(value)
        self.update()

    def _set_pulse(self, value):
        t = float(value)
        self._pulse = math.sin(t * math.pi)
        self.update()

    def _set_filter_angle(self, value):
        self._filter_angle = 90.0 + float(value)
        if self._filter_icon is not None:
            self._filter_icon.update()

    def _make_conic_layer(self, name: str, w: int, h: int, radius: float, blur: float, angle: float, stops: list[tuple[float, QColor]]):
        cache_key = (name, w, h, round(angle, 2))
        cached = self._layer_cache.get(cache_key)
        if cached is not None:
            return cached
        margin = max(2, int(math.ceil(blur * 2 + 2)))
        img = QImage(w + margin * 2, h + margin * 2, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(margin, margin, w, h), radius, radius)
        g = QConicalGradient(QPointF(margin + w / 2, margin + h / 2), angle)
        for pos, color in stops:
            g.setColorAt(pos, color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)
        p.end()
        result = (img, margin)
        self._layer_cache[cache_key] = result
        return result

    def _draw_conic_layer(self, painter: QPainter, name: str, rect: QRectF, radius: float, blur: float, angle: float, stops: list[tuple[float, QColor]], opacity: float = 1.0):
        img, margin = self._make_conic_layer(name, int(rect.width()), int(rect.height()), radius, blur, angle, stops)
        painter.save()
        painter.setOpacity(opacity)
        painter.drawImage(QPointF(rect.left() - margin, rect.top() - margin), img)
        painter.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        main = self._main_rect()
        glow = min(1.0, self._glow_level + (0.16 * self._pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#01040d"))
        p.drawRoundedRect(main, 10, 10)

        border = QLinearGradient(main.left(), main.top(), main.right(), main.bottom())
        border.setColorAt(0.0, QColor(94, 130, 255, int(22 + 62 * glow)))
        border.setColorAt(0.48, QColor(182, 196, 255, int(14 + 44 * glow)))
        border.setColorAt(1.0, QColor(94, 130, 255, int(18 + 56 * glow)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(border, 1.0))
        p.drawRoundedRect(main.adjusted(0, 0, -1, -1), 10, 10)

        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(main), 10, 10)
        p.setClipPath(clip)

        left_glow = QRadialGradient(QPointF(main.left() + 22, main.center().y()), 38)
        left_glow.setColorAt(0.0, QColor(94, 130, 255, int(78 + 50 * glow)))
        left_glow.setColorAt(0.42, QColor(94, 130, 255, int(36 + 24 * glow)))
        left_glow.setColorAt(0.72, QColor(94, 130, 255, int(12 + 12 * glow)))
        left_glow.setColorAt(1.0, QColor(94, 130, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(left_glow)
        p.drawEllipse(QRectF(main.left() - 17, main.top() - 13, 86, main.height() + 28))

        if self._filter_icon is not None:
            right_glow = QRadialGradient(QPointF(main.right() - 34, main.center().y() - 2), 30)
            right_glow.setColorAt(0.0, QColor(182, 196, 255, int(30 + 28 * glow)))
            right_glow.setColorAt(0.7, QColor(182, 196, 255, int(10 + 8 * glow)))
            right_glow.setColorAt(1.0, QColor(182, 196, 255, 0))
            p.setBrush(right_glow)
            p.drawEllipse(QRectF(main.right() - 68, main.top() - 10, 62, main.height() + 20))


__all__ = ['SearchGlowInput', '_InputMask', '_BlueMask']

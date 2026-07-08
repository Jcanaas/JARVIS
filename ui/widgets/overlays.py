from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from .buttons import _MediaBtn

__all__ = ["_CornerGrip", "_FloatOverlay", "_DetachWindow"]


class _CornerGrip(QWidget):
    """Bottom-right resize handle for the frameless floating overlay."""

    def __init__(self, overlay):
        super().__init__(overlay)
        self._ov = overlay
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Subtle rounded backing so the handle is visible over bright video
        path = QPainterPath()
        path.moveTo(28, 4)
        path.lineTo(28, 28)
        path.lineTo(4, 28)
        path.arcTo(QRectF(4, 4, 24, 24), 180, -90)
        path.closeSubpath()
        p.fillPath(path, qcol("#000000", 120))
        pen = QPen(qcol("#FFFFFF", 220), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for off in (0, 7, 14):
            p.drawLine(QPointF(24 - off, 11), QPointF(11, 24 - off))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ov._begin_resize(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._ov._do_resize(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event):
        self._ov._end_resize()
        event.accept()


class _FloatOverlay(QWidget):
    """Transparent always-on-top window placed over the floating video.

    Shows playback controls + title/artist only while the cursor is over it
    (polled, to avoid child enter/leave flicker), and drags the video with it.
    """

    def __init__(self, callbacks: dict, draggable: bool = True, resizable: bool = False):
        super().__init__()
        self._cb = callbacks
        self._drag = None
        self._origin = None
        self._draggable = draggable
        self._resizable = resizable
        self._min_w = 320
        self._resize_anchor = None
        self._resize_w0 = 0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._top = QWidget(self)
        self._top.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0.62), stop:1 rgba(0,0,0,0));"
        )
        top_l = QHBoxLayout(self._top)
        top_l.setContentsMargins(12, 8, 8, 8)
        top_l.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self._title = QLabel("")
        self._title.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 800; background: transparent;")
        self._artist = QLabel("")
        self._artist.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 10px; background: transparent;")
        text_col.addWidget(self._title)
        text_col.addWidget(self._artist)
        top_l.addLayout(text_col, 1)
        self._restore = _icon_button("close", "Cerrar", size=30, icon_size=16)
        self._restore.clicked.connect(lambda: self._cb.get("restore", lambda: None)())
        top_l.addWidget(self._restore, alignment=Qt.AlignmentFlag.AlignTop)

        self._bottom = QWidget(self)
        self._bottom.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0), stop:1 rgba(0,0,0,0.62));"
        )
        bot_l = QHBoxLayout(self._bottom)
        bot_l.setContentsMargins(8, 8, 8, 10)
        bot_l.setSpacing(16)
        bot_l.addStretch()
        self._prev = _MediaBtn(_MediaBtn.PREV)
        self._prev.setToolTip("Vídeo anterior")
        self._prev.clicked.connect(lambda: self._cb.get("prev", lambda: None)())
        self._rwd = _icon_button("backward", "Retroceder 10 s", size=40, icon_size=18)
        self._rwd.clicked.connect(lambda: self._cb.get("rewind", lambda: None)())
        self._play = _MediaBtn(_MediaBtn.PLAY)
        self._play.clicked.connect(lambda: self._cb.get("toggle", lambda: None)())
        self._fwd = _icon_button("forward", "Adelantar 10 s", size=40, icon_size=18)
        self._fwd.clicked.connect(lambda: self._cb.get("forward", lambda: None)())
        self._next = _MediaBtn(_MediaBtn.NEXT)
        self._next.setToolTip("Vídeo siguiente")
        self._next.clicked.connect(lambda: self._cb.get("next", lambda: None)())
        bot_l.addWidget(self._prev)
        bot_l.addWidget(self._rwd)
        bot_l.addWidget(self._play)
        bot_l.addWidget(self._fwd)
        bot_l.addWidget(self._next)
        bot_l.addStretch()

        self._grip = _CornerGrip(self) if self._resizable else None

        self._set_controls_visible(False)
        # The resize handle stays visible (when resizable) so it's discoverable
        # even while the playback controls are hidden.
        if self._grip is not None:
            self._grip.setVisible(True)
            self._grip.raise_()
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(220)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start()

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        self._top.setGeometry(0, 0, w, 58)
        self._bottom.setGeometry(0, h - 64, w, 64)
        if self._grip is not None:
            self._grip.move(w - self._grip.width() - 3, h - self._grip.height() - 3)
            self._grip.raise_()

    def _begin_resize(self, gpos):
        self._resize_anchor = gpos
        self._resize_w0 = self.width()

    def _do_resize(self, gpos):
        if self._resize_anchor is None:
            return
        dx = gpos.x() - self._resize_anchor.x()
        dy = gpos.y() - self._resize_anchor.y()
        # Keep 16:9 but respond to a diagonal drag: use whichever axis the
        # cursor moved more (vertical scaled to the aspect ratio).
        delta = dx if abs(dx) >= abs(dy * 16 / 9) else dy * 16 / 9
        new_w = self._resize_w0 + delta
        try:
            max_w = QApplication.primaryScreen().availableGeometry().width() - 40
        except Exception:
            max_w = 1600
        new_w = max(self._min_w, min(int(new_w), max_w))
        new_h = int(round(new_w * 9 / 16))
        self.resize(new_w, new_h)
        resizer = self._cb.get("resized")
        if resizer:
            resizer(new_w, new_h)

    def _end_resize(self):
        self._resize_anchor = None

    def _set_controls_visible(self, visible: bool):
        self._top.setVisible(visible)
        self._bottom.setVisible(visible)
        # Keep the resize grip visible regardless of hover state.
        if self._grip is not None:
            self._grip.setVisible(True)
        if visible:
            self._top.raise_()
            self._bottom.raise_()
        if self._grip is not None:
            self._grip.raise_()

    def _check_hover(self):
        inside = self.frameGeometry().contains(QCursor.pos())
        if inside != self._top.isVisible():
            self._set_controls_visible(inside)

    def set_meta(self, title: str, artist: str):
        title = str(title or "")
        short = title if len(title) <= 42 else title[:41].rstrip() + "…"
        self._title.setText(short)
        self._title.setToolTip(title)
        self._artist.setText(str(artist or ""))

    def set_playing(self, playing: bool):
        self._play.set_shape(_MediaBtn.PAUSE if playing else _MediaBtn.PLAY)

    def mousePressEvent(self, event):
        if self._draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint()
            self._origin = self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = self._origin + (event.globalPosition().toPoint() - self._drag)
            self.move(new_pos)
            mover = self._cb.get("moved")
            if mover:
                mover(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None

    def closeEvent(self, event):
        try:
            self._cb.get("restore", lambda: None)()
        except Exception:
            pass
        super().closeEvent(event)


class _DetachWindow(QWidget):
    """Top-level window hosting the detached video. If the user closes it from the
    OS (Alt+F4, taskbar), it first hands the video back to the panel so the shared
    mpv surface is never destroyed (which would crash the app)."""

    def __init__(self, on_close):
        super().__init__()
        self._on_close = on_close

    def closeEvent(self, event):
        try:
            if self._on_close is not None:
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)

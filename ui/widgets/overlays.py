from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from .buttons import _MediaBtn

__all__ = ["_CornerGrip", "_FloatOverlay", "_DetachWindow", "CameraLiveWidget"]


class _ClickSlider(QSlider):
    """QSlider that jumps to the clicked position instead of only stepping.

    Plain QSlider only supports dragging the handle or paging by one step
    per click on the groove. The fullscreen overlay's seek/volume bars used
    plain QSlider, so clicking anywhere on the bar (like the movies/youtube
    seek bar does) silently did nothing — this mirrors the working _SeekSlider
    pattern used elsewhere (movies.py/music.py/youtube.py).
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setTracking(True)

    def _value_from_pos(self, pos):
        span = max(1, self.width() - 1) if self.orientation() == Qt.Orientation.Horizontal else max(1, self.height() - 1)
        coord = max(0, min(pos.x() if self.orientation() == Qt.Orientation.Horizontal else pos.y(), span))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        inverted = self.invertedAppearance() ^ rtl
        if inverted:
            coord = span - coord
        value = self.minimum() + (self.maximum() - self.minimum()) * (coord / span)
        return int(round(value))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.sliderPressed.emit()
            self.sliderMoved.emit(self.value())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.isSliderDown() and (e.buttons() & Qt.MouseButton.LeftButton):
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.sliderMoved.emit(self.value())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.setSliderDown(False)
            self.sliderReleased.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)


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

    def __init__(self, callbacks: dict, draggable: bool = True, resizable: bool = False,
                full_controls: bool = False):
        super().__init__()
        self._cb = callbacks
        self._drag = None
        self._origin = None
        self._draggable = draggable
        self._resizable = resizable
        self._min_w = 320
        self._resize_anchor = None
        self._resize_w0 = 0
        # Seek/volume/subtitle/audio-track controls, shown only in fullscreen
        # (not the small draggable PiP window, which has no room for them).
        # Opt-in via full_controls so music.py's separate _FloatOverlay class
        # and youtube.py's plain usage of this one are unaffected.
        self._show_extra = full_controls and not resizable
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
        self._title.setStyleSheet("color: #FFFFFF; font-size: 14px; font-weight: 800; background: transparent;")
        self._artist = QLabel("")
        self._artist.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 11px; background: transparent;")
        text_col.addWidget(self._title)
        text_col.addWidget(self._artist)
        top_l.addLayout(text_col, 1)
        self._restore = _icon_button("close", "Cerrar", size=30, icon_size=16)
        self._restore.setIcon(_line_icon("close", "#FFFFFF", 16))
        self._restore.clicked.connect(lambda: self._cb.get("restore", lambda: None)())
        top_l.addWidget(self._restore, alignment=Qt.AlignmentFlag.AlignTop)

        # Seek row (fullscreen only): time label + progress slider, sitting
        # just above the transport row. This — plus volume/audio/subtitle in
        # the transport row below — was previously missing entirely in
        # fullscreen: only prev/rewind/play/forward/next existed there, so
        # switching to fullscreen silently dropped seek/volume/subtitles.
        self._seekrow = None
        self.seek: "QSlider | None" = None
        self.time_lbl: "QLabel | None" = None
        self.volume: "QSlider | None" = None
        self.subs_btn: "QPushButton | None" = None
        self.audio_btn: "QPushButton | None" = None
        self.filters_btn: "QPushButton | None" = None
        if self._show_extra:
            self._seekrow = QWidget(self)
            self._seekrow.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 rgba(0,0,0,0), stop:1 rgba(0,0,0,0.5));"
            )
            sr_l = QHBoxLayout(self._seekrow)
            sr_l.setContentsMargins(16, 4, 16, 4)
            sr_l.setSpacing(10)
            self.time_lbl = QLabel("0:00 / 0:00")
            self.time_lbl.setStyleSheet("color:#FFFFFF; font-size:11px; background:transparent;")
            self.seek = _ClickSlider(Qt.Orientation.Horizontal)
            self.seek.setRange(0, 1000)
            self.seek.setCursor(Qt.CursorShape.PointingHandCursor)
            self.seek.setStyleSheet(
                "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
                "QSlider::sub-page:horizontal { background:#5E82FF; border-radius:2px; }"
                "QSlider::handle:horizontal { background:#DCE1FF; width:13px; height:13px; margin:-5px 0; border-radius:6px; }"
            )
            self.seek.sliderPressed.connect(lambda: self._cb.get("seek_pressed", lambda: None)())
            self.seek.sliderReleased.connect(
                lambda: self._cb.get("seek_released", lambda: None)(self.seek.value()))
            sr_l.addWidget(self.time_lbl)
            sr_l.addWidget(self.seek, 1)

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
        self._rwd.setIcon(_line_icon("backward", "#FFFFFF", 18))
        self._rwd.clicked.connect(lambda: self._cb.get("rewind", lambda: None)())
        self._play = _MediaBtn(_MediaBtn.PLAY)
        self._play.clicked.connect(lambda: self._cb.get("toggle", lambda: None)())
        self._fwd = _icon_button("forward", "Adelantar 10 s", size=40, icon_size=18)
        self._fwd.setIcon(_line_icon("forward", "#FFFFFF", 18))
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

        if self._show_extra:
            self.volume = _ClickSlider(Qt.Orientation.Horizontal)
            self.volume.setRange(0, 100)
            self.volume.setValue(90)
            self.volume.setFixedWidth(88)
            self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
            self.volume.setStyleSheet(
                "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
                "QSlider::sub-page:horizontal { background:#B6C4FF; border-radius:2px; }"
                "QSlider::handle:horizontal { background:#DCE1FF; width:12px; height:12px; margin:-4px 0; border-radius:6px; }"
            )
            self.volume.valueChanged.connect(
                lambda v: self._cb.get("volume_changed", lambda _v: None)(v))
            self.audio_btn = _icon_button("audio", "Pista de audio", size=36, icon_size=18)
            self.audio_btn.setIcon(_line_icon("audio", "#FFFFFF", 18))
            self.audio_btn.clicked.connect(lambda: self._cb.get("audio", lambda: None)())
            self.subs_btn = QPushButton("CC")
            self.subs_btn.setToolTip("Subtítulos")
            self.subs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.subs_btn.setFixedSize(36, 36)
            self.subs_btn.setStyleSheet(
                "QPushButton { color:#FFFFFF; background:transparent; border:none;"
                " font-size:12px; font-weight:800; }"
                "QPushButton:hover { color:#B6C4FF; }"
            )
            self.subs_btn.clicked.connect(lambda: self._cb.get("subs", lambda: None)())
            self.filters_btn = _icon_button("tune", "Filtros de color", size=36, icon_size=18)
            self.filters_btn.setIcon(_line_icon("tune", "#FFFFFF", 18))
            self.filters_btn.clicked.connect(lambda: self._cb.get("filters", lambda: None)())
            bot_l.addWidget(self.volume)
            bot_l.addSpacing(4)
            bot_l.addWidget(self.audio_btn)
            bot_l.addWidget(self.subs_btn)
            bot_l.addWidget(self.filters_btn)

        self._grip = _CornerGrip(self) if self._resizable else None

        self._set_controls_visible(False)
        # The resize handle stays visible (when resizable) so it's discoverable
        # even while the playback controls are hidden.
        if self._grip is not None:
            self._grip.setVisible(True)
            self._grip.raise_()
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(lambda: self._cb.get("toggle", lambda: None)())

        self._last_cursor = None
        self._idle_ms = 0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(220)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start()

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        self._top.setGeometry(0, 0, w, 58)
        self._bottom.setGeometry(0, h - 64, w, 64)
        if self._seekrow is not None:
            self._seekrow.setGeometry(0, h - 64 - 34, w, 34)
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
        if self._seekrow is not None:
            self._seekrow.setVisible(visible)
        # Keep the resize grip visible regardless of hover state.
        if self._grip is not None:
            self._grip.setVisible(True)
        if visible:
            self._top.raise_()
            self._bottom.raise_()
            if self._seekrow is not None:
                self._seekrow.raise_()
        if self._grip is not None:
            self._grip.raise_()

    def _check_hover(self):
        # In fullscreen the overlay covers the whole screen, so a plain
        # "cursor inside" check kept the gradient bands ("shadow") and the
        # controls permanently visible. Require recent cursor movement (or
        # hovering one of the control strips) and fade out after ~2.5 s idle.
        pos = QCursor.pos()
        inside = self.frameGeometry().contains(pos)
        moved = self._last_cursor is None or pos != self._last_cursor
        self._last_cursor = pos
        if not inside:
            visible = False
            self._idle_ms = 0
        elif moved:
            visible = True
            self._idle_ms = 0
        else:
            self._idle_ms += self._hover_timer.interval()
            lp = self.mapFromGlobal(pos)
            over_controls = self._top.isVisible() and (
                self._top.geometry().contains(lp)
                or self._bottom.geometry().contains(lp)
                or (self._seekrow is not None and self._seekrow.geometry().contains(lp))
            )
            visible = over_controls or self._idle_ms < 2500
        if visible != self._top.isVisible():
            self._set_controls_visible(visible)

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
        elif event.button() == Qt.MouseButton.LeftButton:
            # Fullscreen: click on the video toggles play/pause (debounced so
            # a double-click, which exits fullscreen, doesn't also toggle).
            self._click_timer.start()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if not self._draggable and event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._cb.get("restore", lambda: None)()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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


class CameraLiveWidget(QWidget):
    """Vista en vivo de la webcam — pequeño flotante en la esquina del HUD
    mientras la tool de visión tiene la cámara activa. Sondea frames con un
    QTimer sobre el mismo cv2.VideoCapture que usa screen_processor."""

    _POLL_MS = 33  # ~30 fps, tope real de la webcam; más rápido solo quema CPU

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 165)
        self.setStyleSheet("""
            QWidget#CamCard {
                background: #060912;
                border: 2px solid rgba(122, 162, 255, 0.55);
                border-radius: 10px;
            }
        """)
        self.setObjectName("CamCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self._label = QLabel()
        self._label.setScaledContents(True)
        self._label.setStyleSheet("border-radius: 6px; background: #000;")
        lay.addWidget(self._label)

        self._cap = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_frame)
        self.hide()

    def start(self) -> bool:
        try:
            import cv2
            from actions.screen_processor import _cv2_backend, _get_camera_index
        except Exception as e:
            print(f"[Vision] ⚠️  Camera live view unavailable: {e}")
            return False

        index   = _get_camera_index()
        backend = _cv2_backend()
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return False

        self._cap = cap
        self._timer.start(self._POLL_MS)
        self.show()
        self.raise_()
        return True

    def stop(self) -> None:
        self._timer.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self.hide()

    def _poll_frame(self) -> None:
        if self._cap is None:
            return
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb = np_ascontiguous(rgb)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self._label.setPixmap(QPixmap.fromImage(img))


def np_ascontiguous(arr):
    import numpy as np
    return np.ascontiguousarray(arr)

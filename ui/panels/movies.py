from __future__ import annotations
from pathlib import Path
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
import time

import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *

try:
    import vlc
    HAS_VLC = True
except Exception:
    HAS_VLC = False

class _AspectVideo(QWidget):
    """Keeps a child surface at a fixed aspect ratio, centered (no double black bars)."""

    def __init__(self, surface: QWidget, ratio: float = 16 / 9, parent=None):
        super().__init__(parent)
        self._surface = surface
        self._ratio = ratio
        surface.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        outer_w, outer_h = self.width(), self.height()
        w = outer_w
        h = int(round(w / self._ratio))
        if h > outer_h:
            h = outer_h
            w = int(round(h * self._ratio))
        self._surface.setGeometry((outer_w - w) // 2, (outer_h - h) // 2, w, h)




class _VLCBackend:
    """Thin wrapper around a VLC media player with an mpv-like interface.

    Renders into an existing native Qt surface (via set_hwnd), so the same
    _AspectVideo / _PanelControls / _FloatOverlay machinery the YouTube player
    uses can drive it unchanged.
    """

    def __init__(self):
        self.instance = None
        self.player = None
        if HAS_VLC:
            try:
                self.instance = vlc.Instance()
                self.player = self.instance.media_player_new()
            except Exception:
                self.instance = None
                self.player = None

    def available(self) -> bool:
        return self.player is not None

    def play_url(self, url: str, hwnd: int, volume: int = 90):
        if not self.player:
            return
        media = self.instance.media_new(url)
        self.player.set_media(media)
        self.set_hwnd(hwnd)
        self.player.audio_set_volume(int(volume))
        self.player.play()

    def set_hwnd(self, hwnd: int):
        if self.player and hwnd:
            self.player.set_hwnd(int(hwnd))

    # -- transport (names mirror the mpv wrapper used by YouTube) -----------
    def toggle(self):
        if self.player:
            self.player.pause()  # VLC's pause() toggles

    def pause(self):
        if self.player:
            self.player.set_pause(1)

    def set_volume(self, v: int):
        if self.player:
            self.player.audio_set_volume(int(v))

    def seek_abs(self, seconds: float):
        if self.player:
            self.player.set_time(int(max(0.0, seconds) * 1000))

    def seek_rel(self, delta: float):
        if self.player:
            t = self.player.get_time()
            if t is not None and t >= 0:
                self.player.set_time(max(0, int(t + delta * 1000)))

    def position(self):
        if self.player:
            t = self.player.get_time()
            return t / 1000.0 if t and t > 0 else 0.0
        return 0.0

    def duration(self):
        if self.player:
            length = self.player.get_length()
            return length / 1000.0 if length and length > 0 else 0.0
        return 0.0

    def paused(self):
        return not self.is_playing()

    def is_playing(self):
        return bool(self.player and self.player.is_playing())

    def is_running(self):
        return bool(self.player and self.player.get_media() is not None)

    def stop(self):
        if self.player:
            try:
                self.player.stop()
            except Exception:
                pass

    # -- track selection ----------------------------------------------------
    def audio_tracks(self):
        return self.player.audio_get_track_description() if self.player else []

    def current_audio(self):
        return self.player.audio_get_track() if self.player else -1

    def set_audio_track(self, tid):
        if self.player:
            self.player.audio_set_track(tid)

    def subtitle_tracks(self):
        return self.player.video_get_spu_description() if self.player else []

    def current_subtitle(self):
        return self.player.video_get_spu() if self.player else -1

    def set_subtitle(self, tid):
        if self.player:
            self.player.video_set_spu(tid)

    def get_subtitle_delay(self):
        # microseconds
        return self.player.video_get_spu_delay() if self.player else 0

    def set_subtitle_delay(self, microseconds):
        if self.player:
            self.player.video_set_spu_delay(int(microseconds))

    def add_subtitle_file(self, path: str) -> bool:
        """Load an external subtitle file and select it."""
        if not self.player:
            return False
        try:
            uri = Path(path).as_uri()
            # add_slave(type, uri, select_now)
            self.player.add_slave(vlc.MediaSlaveType.Subtitle, uri, True)
            return True
        except Exception:
            try:  # older python-vlc fallback
                self.player.video_set_subtitle_file(path)
                return True
            except Exception:
                return False





class _VideoCard(QWidget):
    activated = pyqtSignal(str)

    def __init__(self, video: dict, parent=None):
        super().__init__(parent)
        self._vid = str(video.get("id") or "")
        self.setObjectName("YtCard")
        self.setFixedWidth(248)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)

        self.thumb = QLabel()
        self.thumb.setFixedSize(248, 140)
        self.thumb.setObjectName("YtCardThumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.thumb)

        title = QLabel(video.get("title", ""))
        title.setWordWrap(True)
        title.setFixedHeight(38)
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 700;")
        title.setToolTip(video.get("title", ""))
        lay.addWidget(title)

        meta = QLabel(self._meta(video))
        meta.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 10px;")
        lay.addWidget(meta)

    @staticmethod
    def _meta(video: dict) -> str:
        parts = []
        if video.get("channel"):
            parts.append(str(video["channel"]))
        try:
            total = int(video.get("duration") or 0)
        except Exception:
            total = 0
        if total > 0:
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        return "  ·  ".join(parts)

    def thumb_label(self) -> QLabel:
        return self.thumb

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._vid)




class _PanelControls(QWidget):
    """Translucent control bar overlaid on the in-panel video (YouTube style).

    A frameless tool window that tracks the video area's screen rect and shows a
    bottom control bar on hover. Controls are exposed as attributes so the panel
    keeps its existing wiring.
    """

    def __init__(self, panel, video_box, is_active=None):
        super().__init__(panel)
        self._panel = panel
        self._box = video_box
        # Optional predicate deciding when the bar may show. Defaults to the
        # YouTube panel's own layout check; other panels pass their own.
        self._is_active = is_active
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self._bar = QWidget(self)
        self._bar.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0), stop:1 rgba(0,0,0,0.74));"
        )
        bar_l = QHBoxLayout(self._bar)
        bar_l.setContentsMargins(14, 24, 14, 12)
        bar_l.setSpacing(10)

        self.play_btn = _MediaBtn(_MediaBtn.PLAY)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color: #FFFFFF; font-size: 11px; background: transparent;")
        self.seek = _SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#5E82FF; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DCE1FF; width:13px; height:13px; margin:-5px 0; border-radius:6px; }"
        )
        self.vol_icon = QLabel()
        self.vol_icon.setPixmap(_line_icon("volume", "#FFFFFF", 17).pixmap(17, 17))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(90)
        self.volume.setFixedWidth(88)
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#B6C4FF; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DCE1FF; width:12px; height:12px; margin:-4px 0; border-radius:6px; }"
        )
        self.like_btn = _LikeBtn()
        self.download_btn = _icon_button("download", "Descargar vídeo", size=36, icon_size=18)
        # Audio-track + subtitle buttons: hidden by default (YouTube doesn't use
        # them); the Movies panel reveals and wires them up.
        self.audio_btn = _icon_button("audio", "Pista de audio", size=36, icon_size=18)
        self.subs_btn = QPushButton("CC")
        self.subs_btn.setToolTip("Subtítulos")
        self.subs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.subs_btn.setFixedSize(36, 36)
        self.subs_btn.setStyleSheet(
            "QPushButton { color:#FFFFFF; background:transparent; border:none;"
            " font-size:12px; font-weight:800; }"
            "QPushButton:hover { color:#B6C4FF; }"
        )
        self.audio_btn.hide()
        self.subs_btn.hide()
        self.float_btn = _icon_button("pip", "Vídeo flotante", size=36, icon_size=18)
        self.fullscreen_btn = _icon_button("fullscreen", "Pantalla completa", size=36, icon_size=18)

        bar_l.addWidget(self.play_btn)
        bar_l.addWidget(self.time_lbl)
        bar_l.addWidget(self.seek, stretch=1)
        bar_l.addWidget(self.vol_icon)
        bar_l.addWidget(self.volume)
        bar_l.addSpacing(8)
        bar_l.addWidget(self.like_btn)
        bar_l.addWidget(self.download_btn)
        bar_l.addWidget(self.audio_btn)
        bar_l.addWidget(self.subs_btn)
        bar_l.addWidget(self.float_btn)
        bar_l.addWidget(self.fullscreen_btn)

        self._bar.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._sync)
        self._timer.start()

    def resizeEvent(self, event):
        self._bar.setGeometry(0, self.height() - 62, self.width(), 62)

    def _sync(self):
        panel = self._panel
        try:
            if self._is_active is not None:
                active = self._is_active()
            else:
                active = (panel.stack.currentIndex() == 1
                          and panel._detached_mode is None)
            show = (
                active
                and panel.isVisible()
                and self._box.isVisible()
                and not panel.window().isMinimized()
            )
        except Exception:
            show = False
        if not show:
            if self.isVisible():
                self.hide()
            return
        top_left = self._box.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left.x(), top_left.y(), self._box.width(), self._box.height())
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()
        inside = rect.contains(QCursor.pos())
        if inside != self._bar.isVisible():
            self._bar.setVisible(inside)
            if inside:
                self._bar.raise_()





class _SeekSlider(QSlider):
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





# Shared, bounded pool for per-episode thumbnail downloads. A long anime
# (One Piece = 1389 episodes) would otherwise spawn one thread per row; this
# caps concurrent network fetches while rows still populate progressively.
_THUMB_POOL = ThreadPoolExecutor(max_workers=6)


def _download_image(url: str, timeout: int = 15) -> bytes:
    """Fetch image bytes with a browser User-Agent.

    Some CDNs (notably media.kitsu.app, which serves anime posters) return HTTP
    403 for the default 'Python-urllib/x.y' User-Agent, so anime posters showed
    the 🎬 placeholder. TMDB's CDN doesn't block it, which is why movie posters
    worked. Sending a browser UA fixes both.
    """
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _round_pixmap(pixmap: "QPixmap", radius: int) -> "QPixmap":
    out = QPixmap(pixmap.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(pixmap.rect()), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pixmap)
    p.end()
    return out


class _MovieCard(QWidget):
    """Clickable movie card — Netflix mockup style: rounded poster, hover fade."""

    clicked = pyqtSignal(object)  # movie data

    _W, _H = 148, 200

    def __init__(self, movie, parent=None):
        super().__init__(parent)
        self.movie = movie
        self.setStyleSheet("background:transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Fixed size so cards line up in a clean grid regardless of how long the
        # title is (1- vs 2-line titles were breaking row alignment).
        self.setFixedWidth(self._W)
        self._orig_pixmap: "QPixmap | None" = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 7)
        lay.setSpacing(7)

        self.poster = QLabel()
        self.poster.setFixedSize(self._W, self._H)
        self.poster.setStyleSheet("background:#1a2226; border-radius:13px;")
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if movie.poster_url:
            try:
                data = _download_image(movie.poster_url)
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self._W, self._H,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    x = (scaled.width() - self._W) // 2
                    y = (scaled.height() - self._H) // 2
                    cropped = scaled.copy(x, y, self._W, self._H)
                    self.poster.setPixmap(_round_pixmap(cropped, 13))
            except Exception:
                self.poster.setText("🎬")
                self.poster.setFont(QFont(FONT_UI, 24))
        else:
            self.poster.setText("🎬")
            self.poster.setFont(QFont(FONT_UI, 24))
        lay.addWidget(self.poster)

        title_label = QLabel(movie.title)
        title_label.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        title_label.setStyleSheet("color:#f4f4f2; background:transparent;")
        title_label.setWordWrap(True)
        # Fixed 2-line height keeps every card the same total height.
        title_label.setFixedHeight(34)
        title_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        title_label.setToolTip(movie.title)
        lay.addWidget(title_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        if movie.release_year:
            year_lbl = QLabel(str(movie.release_year))
            year_lbl.setFont(QFont("Inter", 8))
            year_lbl.setStyleSheet("color:#9aa6ab; background:transparent;")
            meta_row.addWidget(year_lbl)
        if movie.rating:
            rating_lbl = QLabel(f"★ {movie.rating:.1f}")
            rating_lbl.setFont(QFont("Inter", 8, QFont.Weight.DemiBold))
            rating_lbl.setStyleSheet("color:#f1c64a; background:transparent;")
            meta_row.addWidget(rating_lbl)
        meta_row.addStretch(1)
        lay.addLayout(meta_row)

    def enterEvent(self, ev):
        # QGraphicsOpacityEffect is intentionally avoided here: Qt caches its
        # rendered pixmap at the widget's backing-store position, and inside a
        # QScrollArea that cache goes stale on scroll — cards visually "stick"
        # at their old spot instead of following the scroll. Dimming the
        # poster's own pixmap on hover avoids that entirely.
        self._set_dim(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._set_dim(False)
        super().leaveEvent(ev)

    def _set_dim(self, dim: bool):
        pm = self.poster.pixmap()
        if pm is None or pm.isNull():
            return
        if dim:
            dimmed = QPixmap(pm.size())
            dimmed.fill(Qt.GlobalColor.transparent)
            p = QPainter(dimmed)
            p.setOpacity(0.82)
            p.drawPixmap(0, 0, pm)
            p.end()
            if self._orig_pixmap is None:
                self._orig_pixmap = pm
            self.poster.setPixmap(dimmed)
        elif self._orig_pixmap is not None:
            self.poster.setPixmap(self._orig_pixmap)

    def mousePressEvent(self, ev):
        self.clicked.emit(self.movie)


class _TorrentSelectDialog(QDialog):
    """Dialog to select a torrent from a list of search results."""

    def __init__(self, torrents: list, parent=None):
        super().__init__(parent)
        self.selected_torrent = None
        self.setWindowTitle("Seleccionar Torrent")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        lay = QVBoxLayout(self)

        # Info label with a summary of how many are in Spanish.
        n_es = sum(1 for t in torrents if getattr(t, "spanish", False))
        info = QLabel(
            f"Selecciona un torrent  ·  {len(torrents)} resultados, "
            f"{n_es} en español (marcados en verde arriba):"
        )
        info.setStyleSheet(f"color:{C.TEXT_MED};")
        lay.addWidget(info)

        # Torrent list
        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background:{C.PANEL}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:6px;
            }}
            QListWidget::item {{ padding:12px; border-radius:4px; }}
            QListWidget::item:hover {{ background:{C.PANEL2}; }}
            QListWidget::item:selected {{ background:{C.PRI}; color:{C.DARK}; font-weight:bold; }}
        """)
        self._list.itemDoubleClicked.connect(self._on_select)
        lay.addWidget(self._list)

        # Keep the incoming order (already sorted by seeders); don't re-sort.
        for t in torrents:
            size_str = t.size if t.size else ""
            provider = getattr(t, "provider", "") or "torrent"
            is_es = getattr(t, "spanish", False)
            # Text label (not just a flag emoji, which Qt may not render on
            # Windows) so Spanish vs. original is unmistakable.
            lang = "[ESPAÑOL]" if is_es else "[ORIGINAL/VO]"
            meta = f"📤 {t.seeders} seeders   {size_str}   ·  {provider}"
            item = QListWidgetItem(f"{lang}  {t.title}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, t)
            # Colour Spanish results green, others dimmer, so the list scans fast.
            item.setForeground(QColor("#4ADE80") if is_es else QColor(C.TEXT_DIM))
            self._list.addItem(item)

        # Buttons
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()

        btn_select = QPushButton("Reproducir")
        btn_select.setFixedHeight(36)
        btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_select.setStyleSheet(f"""
            QPushButton {{
                background:{C.PRI}; color:{C.DARK};
                border:none; border-radius:6px; font-weight:bold; padding:0 20px;
            }}
            QPushButton:hover {{ background:{C.PRI_DIM}; }}
        """)
        btn_select.clicked.connect(self._on_select)
        btn_lay.addWidget(btn_select)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setFixedHeight(36)
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:6px; padding:0 20px;
            }}
            QPushButton:hover {{ border-color:{C.PRI}; }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addWidget(btn_cancel)

        lay.addLayout(btn_lay)

    def _on_select(self):
        item = self._list.currentItem()
        if item:
            self.selected_torrent = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class _UserAvatar(QWidget):
    """Circular avatar showing user initials. Clickable (emits `clicked`)."""

    clicked = pyqtSignal()

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_username(self, username: str):
        self.username = username
        self.update()

    def mousePressEvent(self, ev):
        self.clicked.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, 40, 40)
        p.setClipPath(path)

        grad = QLinearGradient(0, 0, 40, 40)
        grad.setColorAt(0.0, QColor("#3a4a52"))
        grad.setColorAt(1.0, QColor("#273035"))
        p.fillRect(self.rect(), grad)

        initials = "".join(w[0].upper() for w in self.username.split())[:2]
        p.setPen(Qt.PenStyle.NoPen)
        p.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#f4f4f2")))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, initials or "?")


class _HeroSearchGlyph(QWidget):
    """Small grey magnifier icon for the mockup-style search bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#9aa6ab"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2.5, 2.5, 10, 10))
        p.drawLine(QPointF(15.5, 15.5), QPointF(11.5, 11.5))


class _HeroBanner(QWidget):
    """Featured banner — mockup style: rounded corners, meta chips on top,
    carousel dots, circular play button with title, and a heart button."""

    play_clicked = pyqtSignal(object)   # movie/anime
    info_clicked = pyqtSignal(object)
    _bg_ready = pyqtSignal(int, QImage)  # (carousel index, backdrop image)

    _RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(420)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._items: list = []
        self._index = 0
        self._pixmaps: dict[int, QPixmap] = {}
        self._pixmap: "QPixmap | None" = None
        self._movie = None
        self._liked: set = set()
        self._dot_btns: list = []
        self._bg_ready.connect(self._on_bg_ready)
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self._advance)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(0)

        # Top row: meta chips left, carousel dots right
        top = QHBoxLayout()
        top.setSpacing(8)
        self._chips_box = QWidget()
        self._chips_box.setStyleSheet("background:transparent;")
        chips_lay = QHBoxLayout(self._chips_box)
        chips_lay.setContentsMargins(0, 0, 0, 0)
        chips_lay.setSpacing(8)
        top.addWidget(self._chips_box)
        top.addStretch(1)
        self._dots_box = QWidget()
        self._dots_box.setStyleSheet("background:transparent;")
        dots_lay = QHBoxLayout(self._dots_box)
        dots_lay.setContentsMargins(0, 4, 4, 0)
        dots_lay.setSpacing(6)
        top.addWidget(self._dots_box, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)

        outer.addStretch(1)

        # Bottom row: ⏵ circle + title/subtitle left, heart right
        bottom = QHBoxLayout()
        bottom.setSpacing(14)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(46, 46)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setToolTip("Reproducir")
        self._play_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,255,255,235); color:#111820;
                border:none; border-radius:23px;
                font-size:13pt; padding-left:4px;
            }
            QPushButton:hover { background:#ffffff; }
        """)
        self._play_btn.clicked.connect(lambda: self.play_clicked.emit(self._movie))
        bottom.addWidget(self._play_btn)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title_lbl = QLabel("")
        self._title_lbl.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        self._title_lbl.setStyleSheet("color:#ffffff; background:transparent;")
        text_col.addWidget(self._title_lbl)
        self._sub_lbl = QLabel("")
        self._sub_lbl.setFont(QFont("Inter", 9))
        self._sub_lbl.setStyleSheet("color:rgba(255,255,255,175); background:transparent;")
        text_col.addWidget(self._sub_lbl)
        bottom.addLayout(text_col)
        bottom.addStretch(1)

        self._like_btn = QPushButton("♡")
        self._like_btn.setFixedSize(40, 40)
        self._like_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._like_btn.setToolTip("Me gusta")
        self._like_btn.clicked.connect(self._toggle_like)
        bottom.addWidget(self._like_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self._style_like()

        outer.addLayout(bottom)

    # -- carousel API ---------------------------------------------------

    def set_items(self, items):
        """Show a rotating featured carousel (grid view)."""
        self._items = list(items)[:5]
        self._pixmaps.clear()
        self._rebuild_dots()
        if self._items:
            self._show_index(0)
        if len(self._items) > 1:
            self._timer.start()
        else:
            self._timer.stop()

    def set_movie(self, movie, pixmap=None):
        """Show a single fixed item (detail view / legacy callers)."""
        self._timer.stop()
        self._items = [movie] if movie else []
        self._pixmaps = {0: pixmap} if (movie and pixmap) else {}
        self._rebuild_dots()
        if movie:
            self._show_index(0)

    def _show_index(self, i: int):
        if not self._items:
            return
        self._index = i % len(self._items)
        m = self._items[self._index]
        self._movie = m
        self._title_lbl.setText(getattr(m, "title", "") or "")
        sub = "  ·  ".join(filter(None, [
            str(m.release_year) if getattr(m, "release_year", 0) else "",
            f"★ {m.rating:.1f}" if getattr(m, "rating", 0) else "",
        ]))
        self._sub_lbl.setText(sub or "Reproducir ahora")
        self._set_chips(m)
        self._style_dots()
        self._style_like()
        self._pixmap = self._pixmaps.get(self._index)
        self.update()
        if self._pixmap is None:
            url = getattr(m, "backdrop_url", "") or getattr(m, "poster_url", "")
            if url:
                self._fetch(self._index, url)

    def _advance(self):
        if len(self._items) > 1 and self.isVisible():
            self._show_index(self._index + 1)

    # -- backdrop loading (thread-safe: QImage in worker, QPixmap here) --

    def _fetch(self, index: int, url: str):
        def work():
            try:
                import urllib.request
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"}  # Más compatible con CDNs
                )
                data = urllib.request.urlopen(req, timeout=15).read()
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    # Escalar a resolución más alta si es pequeña
                    if img.width() < 1200:
                        img = img.scaledToWidth(
                            1920, Qt.TransformationMode.SmoothTransformation
                        )
                    self._bg_ready.emit(index, img)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_bg_ready(self, index: int, img: QImage):
        px = QPixmap.fromImage(img)
        self._pixmaps[index] = px
        if index == self._index:
            self._pixmap = px
            self.update()

    # -- subcomponents ---------------------------------------------------

    def _chip_texts(self, m) -> list:
        out = []
        eps = int(getattr(m, "total_episodes", 0) or 0)
        if eps:
            out.append(f"{eps} eps")
        if getattr(m, "mal_id", 0):
            out.append("Anime")
        else:
            out.append("Película" if getattr(m, "media_type", "") == "movie" else "Serie")
        if getattr(m, "release_year", 0):
            out.append(str(m.release_year))
        if getattr(m, "rating", 0):
            out.append(f"★ {m.rating:.1f}")
        return out

    def _set_chips(self, m):
        lay = self._chips_box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        for text in self._chip_texts(m):
            lbl = QLabel(text)
            lbl.setFont(QFont("Inter", 8, QFont.Weight.DemiBold))
            lbl.setStyleSheet(
                "background:rgba(40,48,53,195); color:#e9ece9;"
                "border:none; border-radius:12px; padding:5px 12px;"
            )
            lay.addWidget(lbl)

    def _rebuild_dots(self):
        lay = self._dots_box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self._dot_btns = []
        for i in range(len(self._items)):
            d = QPushButton("")
            d.setFixedSize(8, 8)
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.clicked.connect(lambda _=False, ix=i: self._on_dot(ix))
            lay.addWidget(d)
            self._dot_btns.append(d)
        self._dots_box.setVisible(len(self._items) > 1)
        self._style_dots()

    def _style_dots(self):
        for i, d in enumerate(self._dot_btns):
            on = i == self._index
            d.setStyleSheet(
                f"background:{'#ffffff' if on else 'rgba(255,255,255,80)'};"
                "border:none; border-radius:4px;"
            )

    def _on_dot(self, ix: int):
        self._show_index(ix)
        if self._timer.isActive():
            self._timer.start()   # restart the rotation countdown

    def _toggle_like(self):
        key = id(self._movie)
        if key in self._liked:
            self._liked.discard(key)
        else:
            self._liked.add(key)
        self._style_like()

    def _style_like(self):
        liked = id(self._movie) in self._liked
        self._like_btn.setText("♥" if liked else "♡")
        self._like_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(15,22,26,150);
                color:{'#ff6470' if liked else '#ffffff'};
                border:1px solid rgba(255,255,255,70);
                border-radius:20px; font-size:13pt;
            }}
            QPushButton:hover {{ background:rgba(255,255,255,40); }}
        """)

    def mousePressEvent(self, ev):
        # Clicking the artwork opens the detail view (buttons eat their own clicks).
        if self._movie is not None:
            self.info_clicked.emit(self._movie)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Rounded-corner clip (mockup card look)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._RADIUS, self._RADIUS)
        p.setClipPath(path)

        p.fillRect(self.rect(), QColor("#131a1e"))

        if self._pixmap and not self._pixmap.isNull() and self.width() > 0 and self.height() > 0:
            src_w, src_h = self._pixmap.width(), self._pixmap.height()
            cover_scale = max(self.width() / src_w, self.height() / src_h)
            if cover_scale > 1.6:
                # The source is too small (or portrait) to cover the hero
                # without a heavy upscale — a low-res anime poster stretched
                # ~8x to fill a wide hero looked pixelated and not landscape
                # at all. Fit it instead (no crop, no upscale past 1.6x) and
                # let the dark background/gradient fill the rest.
                fit_scale = min(1.6, self.width() / src_w, self.height() / src_h)
                target_w = max(1, int(src_w * fit_scale))
                target_h = max(1, int(src_h * fit_scale))
                scaled = self._pixmap.scaled(
                    target_w, target_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                scaled = self._pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)

        # Gradient overlay: subtle top → dark bottom for text legibility
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(0, 0, 0, 30))
        grad.setColorAt(0.55, QColor(0, 0, 0, 70))
        grad.setColorAt(1.0, QColor(7, 17, 21, 215))
        p.fillRect(self.rect(), grad)
        p.end()


class _EpisodeRow(QWidget):
    """Netflix-style episode list item: thumbnail, number badge, title, meta."""

    play_clicked = pyqtSignal(int)  # episode number
    _thumb_ready = pyqtSignal(QPixmap)  # marshals the bg-thread download to the UI thread

    _TW, _TH = 140, 79

    def __init__(self, ep: dict, poster_pixmap=None, parent=None):
        super().__init__(parent)
        self._ep = ep
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            _EpisodeRow { background:transparent; border-radius:8px; }
            _EpisodeRow:hover { background:rgba(255,255,255,18); }
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 10, 6)
        lay.setSpacing(12)

        self.thumb = QLabel()
        self.thumb.setFixedSize(self._TW, self._TH)
        self.thumb.setStyleSheet("background:#1a2226; border-radius:8px;")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_ready.connect(self._on_thumb_ready)
        # Kept so a failed per-episode fetch (long series often proxy stills
        # through metahub.space, which 404s for many season/episode combos)
        # can fall back to the series poster instead of a bare number.
        self._poster_fallback = poster_pixmap

        thumb_url = ep.get("thumbnail") or ""
        if thumb_url:
            # Placeholder (episode number) while its own still loads in the
            # background; the still — not the series poster — actually shows
            # this episode.
            self.thumb.setText(str(ep.get("number", "?")))
            self.thumb.setFont(QFont("Inter", 14, QFont.Weight.Bold))
            self.thumb.setStyleSheet(
                "background:#1a2226; border-radius:8px; color:#9aa6ab;"
            )
            _THUMB_POOL.submit(self._fetch_thumb, thumb_url)
        elif poster_pixmap and not poster_pixmap.isNull():
            # No per-episode still available (e.g. movies/specials) — fall
            # back to the series poster rather than a bare number.
            self._set_thumb_pixmap(poster_pixmap)
        else:
            self.thumb.setText(str(ep.get("number", "?")))
            self.thumb.setFont(QFont("Inter", 14, QFont.Weight.Bold))
            self.thumb.setStyleSheet(
                "background:#1a2226; border-radius:8px; color:#9aa6ab;"
            )
        lay.addWidget(self.thumb)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        num = ep.get("number", "?")
        title_lbl = QLabel(f"{num}. {ep.get('title', '')}")
        title_lbl.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        title_lbl.setStyleSheet("color:#f4f4f2; background:transparent;")
        title_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)

        meta_bits = []
        if ep.get("aired"):
            meta_bits.append(ep["aired"])
        if ep.get("filler"):
            meta_bits.append("Filler")
        if ep.get("recap"):
            meta_bits.append("Recap")
        if meta_bits:
            meta_lbl = QLabel("  ·  ".join(meta_bits))
            meta_lbl.setFont(QFont("Inter", 8))
            meta_lbl.setStyleSheet("color:#9aa6ab; background:transparent;")
            text_col.addWidget(meta_lbl)
        lay.addLayout(text_col, 1)

        play_btn = QPushButton("▶")
        play_btn.setFixedSize(36, 36)
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.setStyleSheet("""
            QPushButton {
                background:#273035; color:#f4f4f2; border:none;
                border-radius:18px; font-size:11pt;
            }
            QPushButton:hover { background:#2f3d43; }
        """)
        play_btn.clicked.connect(lambda: self.play_clicked.emit(self._ep.get("number", 0)))
        lay.addWidget(play_btn)

    def mousePressEvent(self, ev):
        self.play_clicked.emit(self._ep.get("number", 0))

    def _fetch_thumb(self, url: str):
        """Runs on a _THUMB_POOL worker thread."""
        pm = QPixmap()
        try:
            data = _download_image(url, timeout=10)
            pm.loadFromData(data)
        except Exception:
            pm = QPixmap()  # empty/null -> _on_thumb_ready falls back to the poster
        self._thumb_ready.emit(pm)  # cross-thread signal, queued to the UI thread

    def _on_thumb_ready(self, pm: QPixmap):
        # The row may have been deleted (user navigated away) by the time the
        # background download finishes; guard against the dangling C++ object.
        try:
            if not pm.isNull():
                self._set_thumb_pixmap(pm)
            elif self._poster_fallback and not self._poster_fallback.isNull():
                self._set_thumb_pixmap(self._poster_fallback)
        except RuntimeError:
            pass

    def _set_thumb_pixmap(self, pm: "QPixmap"):
        scaled = pm.scaled(
            self._TW, self._TH, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (scaled.width() - self._TW) // 2
        y = (scaled.height() - self._TH) // 2
        self.thumb.setText("")
        self.thumb.setPixmap(_round_pixmap(scaled.copy(x, y, self._TW, self._TH), 8))


class MoviesModePanel(QWidget):
    """Movie/TV discovery with poster grid and detail view."""

    _results_ready = pyqtSignal(list, str, str)
    _status_sig = pyqtSignal(str)
    _show_detail = pyqtSignal(object)
    _torrents_found = pyqtSignal(list, object, int)  # (torrents, movie, episode)
    _stream_ready = pyqtSignal(str, object)  # (stream_url, movie)
    _pos_sig = pyqtSignal(float, float, bool)  # position, duration, playing
    _subtitle_ready = pyqtSignal(str, str)  # (srt_path, label)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._current_view = "grid"  # "grid" | "detail" | "player"
        self._detail_movie = None
        self._playing_movie = None
        self._playing_episode = 0  # absolute episode number (0 for movies)

        # Playback backend + player state (mirrors the YouTube player).
        self._player = _VLCBackend() if HAS_VLC else None
        self._duration = 0.0
        self._user_dragging = False
        self._detached_mode = None          # None | "floating" | "fullscreen"
        self._float_window = None
        self._fs_window = None
        self._float_overlay = None
        self._poll_thread = None
        self._poll_stop = threading.Event()

        self._build_ui()
        self._results_ready.connect(self._on_results)
        self._status_sig.connect(self._set_status)
        self._show_detail.connect(self._show_movie_detail)
        self._torrents_found.connect(self._show_torrent_select)
        self._stream_ready.connect(self._on_stream_ready)
        self._pos_sig.connect(self._apply_position)
        self._subtitle_ready.connect(self._on_subtitle_ready)
        QTimer.singleShot(0, self._load_trending)

    def _build_ui(self):
        self.setStyleSheet("""
            MoviesModePanel, AnimeModePanel {
                background:#071115;
            }
            QScrollArea { background:transparent; border:none; }
            QWidget#MovieGrid { background:transparent; }
            QScrollBar:vertical {
                background:transparent; width:8px; margin:0;
            }
            QScrollBar::handle:vertical {
                background:#2f3d43; border-radius:4px; min-height:40px;
            }
            QScrollBar::handle:vertical:hover { background:#3a4a52; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background:transparent;
            }
        """)
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(10)

        # Top bar — back, title pill and search bar in one row (mockup)
        header_lay = QHBoxLayout()
        header_lay.setSpacing(10)
        self._back_btn = QPushButton("←")
        self._back_btn.setFixedSize(40, 40)
        self._back_btn.clicked.connect(self._on_back)
        self._back_btn.setVisible(False)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(
            "background:#273035; border:none; border-radius:12px;"
            "color:#f4f4f2; font-size:13pt;"
        )
        header_lay.addWidget(self._back_btn)

        self._title = QLabel("Películas y Series")
        self._title.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        self._title.setFixedHeight(40)
        self._title.setStyleSheet(
            "color:#f4f4f2; background:#273035; border:none;"
            "border-radius:12px; padding:0 16px;"
        )
        header_lay.addWidget(self._title)

        # Search bar — dark pill, magnifier left (mockup)
        search_frame = QFrame()
        search_frame.setObjectName("MockupSearch")
        search_frame.setFixedHeight(40)
        search_frame.setStyleSheet(
            "QFrame#MockupSearch { background:#273035; border:none; border-radius:12px; }"
        )
        sf_lay = QHBoxLayout(search_frame)
        sf_lay.setContentsMargins(14, 0, 6, 0)
        sf_lay.setSpacing(8)
        sf_lay.addWidget(_HeroSearchGlyph())

        self._search = QLineEdit()
        self._search.setPlaceholderText("Películas, series, anime…")
        self._search.setFrame(False)
        self._search.setStyleSheet(
            "background:transparent; border:none; color:#f4f4f2;"
            "font-size:10pt; font-family:Inter;"
        )
        pal = self._search.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9aa6ab"))
        self._search.setPalette(pal)
        self._search.returnPressed.connect(self._do_search)
        sf_lay.addWidget(self._search, 1)

        header_lay.addWidget(search_frame, 1)

        # Usuario (avatar con iniciales) — al lado de la barra, no dentro
        self._user_btn = _UserAvatar("?")
        self._user_btn.setVisible(False)  # mostrado por AnimeModePanel si está logeado
        self._user_btn.clicked.connect(self._show_user_menu)
        header_lay.addWidget(self._user_btn)

        root.addLayout(header_lay)

        # Stack: grid view | detail view
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")

        # Grid view — hero + chips + poster grid scroll together as one page.
        grid_container = QWidget()
        grid_container.setStyleSheet("background:transparent;")
        gc_lay = QVBoxLayout(grid_container)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        gc_lay.setSpacing(0)

        self._page_scroll = QScrollArea()
        self._page_scroll.setStyleSheet("background:transparent; border:none;")
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        page.setStyleSheet("background:transparent;")
        grid_lay = QVBoxLayout(page)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(10)

        # Hero banner (featured carousel) — large, scrolls with the page.
        self._hero = _HeroBanner()
        self._hero.setFixedHeight(600)
        self._hero.play_clicked.connect(self._search_and_play)
        self._hero.info_clicked.connect(lambda m: self._show_detail.emit(m))
        self._hero.setVisible(False)
        grid_lay.addWidget(self._hero)

        # Genre pill row with paddle arrows, below the hero (mockup)
        chips_row = QWidget()
        chips_row.setStyleSheet("background:transparent;")
        cr_lay = QHBoxLayout(chips_row)
        cr_lay.setContentsMargins(0, 2, 0, 2)
        cr_lay.setSpacing(8)

        self._chips_scroll = QScrollArea()
        self._chips_scroll.setWidgetResizable(True)
        self._chips_scroll.setFixedHeight(40)
        self._chips_scroll.setStyleSheet("background:transparent; border:none;")
        self._chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips_widget = QWidget()
        chips_widget.setStyleSheet("background:transparent;")
        chips = QHBoxLayout(chips_widget)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        for i, (label, cb) in enumerate(self._chip_defs()):
            chips.addWidget(self._chip(label, cb, primary=(i == 0)))
        chips.addStretch(1)
        self._chips_scroll.setWidget(chips_widget)
        cr_lay.addWidget(self._chips_scroll, 1)

        for glyph, delta in (("‹", -260), ("›", 260)):
            arrow = QPushButton(glyph)
            arrow.setFixedSize(36, 36)
            arrow.setCursor(Qt.CursorShape.PointingHandCursor)
            arrow.setStyleSheet("""
                QPushButton {
                    background:#273035; color:#f4f4f2; border:none;
                    border-radius:18px; font-size:13pt; padding-bottom:3px;
                }
                QPushButton:hover { background:#2f3d43; }
            """)
            arrow.clicked.connect(
                lambda _=False, d=delta: self._chips_scroll.horizontalScrollBar().setValue(
                    self._chips_scroll.horizontalScrollBar().value() + d
                )
            )
            cr_lay.addWidget(arrow)

        grid_lay.addWidget(chips_row)

        # Poster grid — no inner scroll; it grows with content and the whole
        # page (hero included) scrolls as one, per the requested behaviour.
        self._grid_widget = QWidget()
        self._grid_widget.setObjectName("MovieGrid")
        self._grid_widget.setStyleSheet("background:transparent;")
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(14)
        self._grid.setContentsMargins(4, 12, 4, 12)
        grid_lay.addWidget(self._grid_widget)
        grid_lay.addStretch(1)

        self._page_scroll.setWidget(page)
        gc_lay.addWidget(self._page_scroll)
        self._stack.addWidget(grid_container)

        # Detail view
        detail_container = QWidget()
        detail_container.setStyleSheet("background:transparent;")
        detail_lay = QVBoxLayout(detail_container)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_scroll = QScrollArea()
        detail_scroll.setStyleSheet("background:transparent; border:none;")
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail_content = QWidget()
        self._detail_content.setStyleSheet("background:transparent;")
        self._detail_layout = QVBoxLayout(self._detail_content)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_scroll.setWidget(self._detail_content)
        detail_lay.addWidget(detail_scroll)
        self._stack.addWidget(detail_container)

        # Player view (index 2) — YouTube-style embedded video with a hover
        # control bar and a floating/detach mode. VLC renders into the native
        # surface via set_hwnd.
        player_page = QWidget()
        pp = QVBoxLayout(player_page)
        pp.setContentsMargins(0, 0, 0, 0)
        pp.setSpacing(8)

        self.video_surface = QWidget()
        self.video_surface.setObjectName("MoviesSurface")
        self.video_surface.setStyleSheet("QWidget#MoviesSurface { background:#000000; border-radius:12px; }")
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surf_lay = QVBoxLayout(self.video_surface)
        surf_lay.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel("Preparando reproducción…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:rgba(203,213,225,0.45); font-size:13px; background:transparent;")
        surf_lay.addWidget(self._placeholder)

        self.video_box = _AspectVideo(self.video_surface)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.video_box.setMinimumHeight(220)

        self._player_col = QVBoxLayout()
        self._player_col.setContentsMargins(0, 0, 0, 0)
        self._player_col.setSpacing(0)
        self._player_col.addWidget(self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        pp.addLayout(self._player_col)
        pp.addStretch(1)
        self._stack.addWidget(player_page)

        # Hover control bar overlaid on the in-panel video (reused from YouTube).
        if self._player and self._player.available():
            self._pc = _PanelControls(
                self, self.video_box,
                is_active=lambda: self._current_view == "player" and self._detached_mode is None,
            )
            # Movies don't use like/download; hide those buttons.
            self._pc.like_btn.hide()
            self._pc.download_btn.hide()
            self.play_btn = self._pc.play_btn
            self.seek = self._pc.seek
            self.time_lbl = self._pc.time_lbl
            self.volume = self._pc.volume
            self.play_btn.clicked.connect(self._toggle_play)
            self.seek.sliderPressed.connect(lambda: setattr(self, "_user_dragging", True))
            self.seek.sliderReleased.connect(self._on_seek_released)
            self.volume.valueChanged.connect(self._on_volume)
            self._pc.float_btn.clicked.connect(self._toggle_floating_video)
            self._pc.fullscreen_btn.clicked.connect(lambda: self._detach_video("fullscreen"))
            # Movies expose audio-track + subtitle menus.
            self._pc.audio_btn.show()
            self._pc.subs_btn.show()
            self._pc.audio_btn.clicked.connect(self._show_audio_menu)
            self._pc.subs_btn.clicked.connect(self._show_subs_menu)
        else:
            self._pc = None

        root.addWidget(self._stack, stretch=1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent; font-size:11px;")
        root.addWidget(self._status)

    def _chip(self, label: str, cb, primary: bool = False) -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(True)
        b.setChecked(primary)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(36)
        b.setStyleSheet("""
            QPushButton {
                background:#273035; color:#ffffff;
                border:none; border-radius:18px;
                padding:0 18px; font-size:9pt; font-weight:600;
                font-family:Inter;
            }
            QPushButton:checked {
                background:#ffffff; color:#111820;
            }
            QPushButton:hover:!checked { background:#2f3d43; }
        """)
        def _activate():
            # Deactivate siblings in the same parent layout
            p = b.parentWidget()
            if p:
                for sib in p.findChildren(QPushButton):
                    if sib is not b and sib.isCheckable():
                        sib.setChecked(False)
            b.setChecked(True)
            cb()
        b.clicked.connect(lambda _=False: _activate())
        return b

    def _chip_defs(self) -> list:
        """(label, callback) pairs for the pill row. Panels override this."""
        return [
            ("Tendencias", self._load_trending),
            ("Recientes", self._load_recent),
        ]

    def _show_user_menu(self):
        """Menú de usuario (para override en AnimeModePanel)."""
        pass

    def _set_status(self, text: str):
        self._status.setText(text)

    def _run_async(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    @staticmethod
    def _clear_layout(layout):
        """Recursively remove every item from a layout: widgets are deleted and
        nested layouts are cleared too. A plain `takeAt` loop that only deletes
        `item.widget()` leaves nested layouts (and their widgets) alive."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    MoviesModePanel._clear_layout(sub)

    def _do_search(self):
        query = self._search.text().strip()
        if not query:
            return
        self._set_status(f"Buscando «{query}»…")

        def work():
            try:
                from actions import cinemeta
                # Cinemeta ranks by title relevance, so "The Furious" returns the
                # actual film first instead of the more popular "Fast & Furious".
                items = cinemeta.search(query, kind="multi", limit=18)
                self._results_ready.emit(items, f"Resultados para «{query}»", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_trending(self):
        self._set_status("Cargando tendencias…")

        def work():
            try:
                from actions import cinemeta
                items = cinemeta.get_trending(kind="movie", limit=18)
                self._results_ready.emit(items, "Películas en tendencia", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_recent(self):
        self._set_status("Cargando series en tendencia…")

        def work():
            try:
                from actions import cinemeta
                items = cinemeta.get_trending(kind="series", limit=18)
                self._results_ready.emit(items, "Series en tendencia", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _on_results(self, items: list, header: str, error: str):
        self._items = items
        self._show_grid_view()
        if error:
            self._set_status(error)
            return
        self._set_status(header)
        self._title.setText(header)

        # Featured carousel in the hero banner (first results)
        if hasattr(self, "_hero"):
            self._hero.set_items(items[:5])
            self._hero.setVisible(bool(items))

        # Clear grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Add movie cards
        COLS = 5
        for i, movie in enumerate(items):
            card = _MovieCard(movie)
            card.clicked.connect(lambda m: self._show_detail.emit(m))
            col = i % COLS
            row = i // COLS
            self._grid.addWidget(card, row, col,
                                 Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Scroll back to the top when new results arrive.
        if hasattr(self, "_page_scroll"):
            self._page_scroll.verticalScrollBar().setValue(0)

    def _on_back(self):
        """Contextual back: from the player, stop it; otherwise go to the grid."""
        if self._current_view == "player":
            self._close_player()
        else:
            self._show_grid_view()

    def _show_grid_view(self):
        self._current_view = "grid"
        self._back_btn.setVisible(False)
        self._stack.setCurrentIndex(0)

    def _show_movie_detail(self, movie):
        self._current_view = "detail"
        self._detail_movie = movie
        self._back_btn.setVisible(True)
        self._title.setText(movie.title)

        # Clear detail layout
        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Build detail view
        detail_widget = QWidget()
        detail = QVBoxLayout(detail_widget)
        detail.setSpacing(16)

        # Poster + meta
        header = QHBoxLayout()
        poster_label = QLabel()
        poster_label.setFixedSize(180, 270)
        poster_label.setStyleSheet(f"background:{C.DARK}; border-radius:6px;")
        if movie.poster_url:
            try:
                data = _download_image(movie.poster_url)
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    pixmap = pixmap.scaledToWidth(180, Qt.TransformationMode.SmoothTransformation)
                    poster_label.setPixmap(pixmap)
            except:
                poster_label.setText("🎬")
                poster_label.setFont(QFont(FONT_UI, 32))
        else:
            poster_label.setText("🎬")
            poster_label.setFont(QFont(FONT_UI, 32))
        poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(poster_label)

        # Meta info
        meta = QVBoxLayout()
        meta.setSpacing(10)

        year_label = QLabel(f"Año: {movie.release_year}" if movie.release_year else "Año: N/A")
        year_label.setFont(QFont(FONT_UI, 11))
        year_label.setStyleSheet(f"color:{C.TEXT_MED};")
        meta.addWidget(year_label)

        rating_label = QLabel(f"Rating: ★ {movie.rating:.1f}/10" if movie.rating else "Rating: N/A")
        rating_label.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        rating_label.setStyleSheet(f"color:{C.TEXT};")
        meta.addWidget(rating_label)

        type_label = QLabel(f"Tipo: {'Película' if movie.media_type == 'movie' else 'Serie'}")
        type_label.setFont(QFont(FONT_UI, 11))
        type_label.setStyleSheet(f"color:{C.TEXT_MED};")
        meta.addWidget(type_label)

        meta.addStretch()
        header.addLayout(meta, 1)
        detail.addLayout(header)

        # Overview
        if movie.overview:
            synopsis_label = QLabel("Sinopsis")
            synopsis_label.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
            synopsis_label.setStyleSheet(f"color:{C.TEXT};")
            detail.addWidget(synopsis_label)

            overview = QLabel(movie.overview)
            overview.setWordWrap(True)
            overview.setFont(QFont(FONT_UI, 10))
            overview.setStyleSheet(f"color:{C.TEXT_MED};")
            detail.addWidget(overview)

        # Play button
        play_btn = QPushButton("▶ Reproducir")
        play_btn.setFixedHeight(42)
        play_btn.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.setStyleSheet(f"""
            QPushButton {{
                background:{C.PRI_DIM}; color:{C.DARK};
                border:none; border-radius:8px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{C.PRI}; }}
        """)
        play_btn.clicked.connect(lambda: self._search_and_play(movie))
        detail.addWidget(play_btn)

        detail.addStretch()
        self._detail_layout.addWidget(detail_widget)
        self._stack.setCurrentIndex(1)

    def _search_and_play(self, movie):
        self._set_status(f"Buscando torrents de «{movie.title}»…")

        def work():
            import re
            from actions import torrent_search as ts

            # Use "anime" kind if mal_id present, else defer to media_type
            if getattr(movie, "mal_id", 0):
                kind = "anime"
            else:
                kind = getattr(movie, "media_type", "movie")
            found: list = []
            errors: list[str] = []

            # Sources 1 & 2: Stremio addons keyed by IMDb id that aggregate
            # Spanish-only sites (Peerflix: DonTorrent/MejorTorrent/Wolfmax4k,
            # 100% Castilian; Torrentio: MejorTorrent/Cinecalidad + intl).
            # Cinemeta results already carry the IMDb id, so no TMDB→IMDb bridge.
            imdb = getattr(movie, "imdb_id", "")
            if not imdb:
                try:
                    from actions import movie_search as ms
                    tmdb_id = getattr(movie, "tmdb_id", 0)
                    if tmdb_id:
                        imdb = ms.get_imdb_id(tmdb_id, kind=kind)
                    else:
                        imdb = ms.get_imdb_id_by_title(movie.title, kind=kind)
                except Exception as exc:
                    imdb = ""
                    errors.append(f"imdb_id: {exc}")
                if imdb:
                    # Persist it on the Movie so subtitle lookup (which runs
                    # later, once playback starts) doesn't have to re-resolve it.
                    movie.imdb_id = imdb

            if imdb:
                self._status_sig.emit(
                    f"Buscando «{movie.title}» en Peerflix + Torrentio ({imdb})…"
                )
                for mod_name in ("peerflix_addon", "torrentio"):
                    try:
                        mod = __import__(f"actions.{mod_name}", fromlist=[mod_name])
                        results = mod.search(imdb, kind=kind, spanish=True, limit=15)
                        for s in results:
                            found.append(ts.Torrent(
                                title=s.title, magnet=s.magnet, seeders=s.seeders,
                                leechers=0, size=s.size, spanish=s.spanish,
                                provider=getattr(s, "provider", "") or mod_name))
                    except Exception as exc:
                        errors.append(f"{mod_name}: {exc}")
            else:
                errors.append("sin IMDb id → Peerflix/Torrentio omitidos")

            # Source 3: title search across YTS/TPB/1337x (with castellano pass).
            try:
                self._status_sig.emit(f"Buscando «{movie.title}» en YTS/1337x…")
                found.extend(ts.search(movie.title, kind=kind, limit=10, spanish=True))
            except Exception as exc:
                errors.append(f"torlink: {exc}")

            if not found:
                diag = "  |  ".join(errors) if errors else ""
                self._status_sig.emit(
                    f"No encontré torrents para «{movie.title}»"
                    + (f"  [{diag}]" if diag else "")
                )
                return

            # De-duplicate by infohash, then Castilian first, then seeders.
            seen, unique = set(), []
            for t in found:
                m = re.search(r"btih:([a-zA-Z0-9]+)", t.magnet or "")
                key = m.group(1).lower() if m else (t.magnet or t.title)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(t)
            unique.sort(key=lambda t: (getattr(t, "spanish", False), t.seeders), reverse=True)

            self._torrents_found.emit(unique, movie, 0)

        self._run_async(work)

    def _show_torrent_select(self, torrents: list, movie, episode: int = 0):
        """Show the torrent selection dialog (runs on the main thread)."""
        dialog = _TorrentSelectDialog(torrents, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_torrent:
            self._set_status("Reproducción cancelada")
            return

        torrent = dialog.selected_torrent
        if not (self._player and self._player.available()):
            self._set_status("VLC no disponible. Instala la app VLC de VideoLAN (videolan.org) en 64 bits.")
            return

        # Switch to the player view immediately with a "loading" placeholder,
        # then start the stream on a worker thread.
        self._playing_movie = movie
        self._playing_episode = episode
        self._current_view = "player"
        self._back_btn.setVisible(True)
        self._title.setText(f"▶ {movie.title}")
        self._stack.setCurrentIndex(2)
        self._placeholder.setText(f"Preparando «{movie.title}» (seeders: {torrent.seeders})…")
        self._placeholder.show()
        QTimer.singleShot(0, self._size_video)
        self._set_status(f"▶ Preparando «{movie.title}»… puede tardar unos segundos")

        def work():
            try:
                from actions import vlc_player as vp

                stream_url = vp.start_streaming(
                    torrent.magnet, movie.title,
                    file_index=getattr(torrent, "file_idx", -1))
                # Playback touches Qt widgets, so hand the URL to the main thread.
                self._stream_ready.emit(stream_url, movie)
            except Exception as e:
                self._status_sig.emit(f"Error iniciando reproducción: {e}")

        self._run_async(work)

    def _on_stream_ready(self, stream_url: str, movie):
        """Start VLC playback into the native surface (main thread)."""
        if self._current_view != "player" or self._playing_movie is not movie:
            return  # user backed out before the stream was ready
        self._placeholder.hide()
        # Give the surface a real size before VLC grabs its HWND, otherwise it
        # renders into a 0-sized window and only audio comes through.
        self._size_video()
        hwnd = int(self.video_surface.winId())
        self._player.play_url(stream_url, hwnd, self.volume.value())
        for b in (self.play_btn, self.seek, self.volume,
                  self._pc.float_btn, self._pc.fullscreen_btn,
                  self._pc.audio_btn, self._pc.subs_btn):
            b.setEnabled(True)
        self.play_btn.set_shape(_MediaBtn.PAUSE)
        self._start_poller()
        self._set_status(f"▶ Reproduciendo «{movie.title}»")

    # -- transport controls -------------------------------------------------
    def _toggle_play(self):
        if self._player:
            self._player.toggle()

    def _on_volume(self, value: int):
        if self._player:
            self._player.set_volume(value)

    def _on_seek_released(self):
        self._user_dragging = False
        if not self._player or self._duration <= 0:
            return
        frac = self.seek.value() / 1000.0
        self._player.seek_abs(frac * self._duration)

    def _forward_video(self):
        if self._player:
            self._player.seek_rel(10)

    def _rewind_video(self):
        if self._player:
            self._player.seek_rel(-10)

    # -- audio-track / subtitle menus --------------------------------------
    @staticmethod
    def _track_label(name) -> str:
        return name.decode("utf-8", "ignore") if isinstance(name, bytes) else str(name)

    def _styled_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:8px; padding:6px;
            }}
            QMenu::item {{ padding:6px 22px; border-radius:5px; }}
            QMenu::item:selected {{ background:{C.PRI_GHO}; }}
            QMenu::separator {{ height:1px; background:{C.BORDER}; margin:5px 8px; }}
        """)
        return menu

    def _show_audio_menu(self):
        if not (self._player and self._player.available()):
            return
        menu = self._styled_menu()
        current = self._player.current_audio()
        tracks = self._player.audio_tracks() or []
        if not tracks:
            menu.addAction("Sin pistas de audio").setEnabled(False)
        for tid, name in tracks:
            act = menu.addAction(("● " if tid == current else "     ") + self._track_label(name))
            act.triggered.connect(lambda _=False, i=tid: self._player.set_audio_track(i))
        menu.exec(self._pc.audio_btn.mapToGlobal(self._pc.audio_btn.rect().topLeft()))

    def _show_subs_menu(self):
        if not (self._player and self._player.available()):
            return
        menu = self._styled_menu()
        current = self._player.current_subtitle()
        tracks = self._player.subtitle_tracks() or []
        # VLC lists "Disable" (id -1) as the first entry; show all as-is.
        if not tracks:
            menu.addAction("Sin subtítulos").setEnabled(False)
        for tid, name in tracks:
            act = menu.addAction(("● " if tid == current else "     ") + self._track_label(name))
            act.triggered.connect(lambda _=False, i=tid: self._player.set_subtitle(i))

        # Download subtitles from OpenSubtitles.
        menu.addSeparator()
        online = menu.addAction("Buscar subtítulos online…")
        online.setEnabled(False)
        es = menu.addAction("   Español (OpenSubtitles)")
        es.triggered.connect(lambda: self._fetch_online_subs("es"))
        en = menu.addAction("   Inglés (OpenSubtitles)")
        en.triggered.connect(lambda: self._fetch_online_subs("en"))

        # Subtitle sync adjustment (delay is in microseconds).
        menu.addSeparator()
        delay_ms = int(self._player.get_subtitle_delay() / 1000)
        header = menu.addAction(f"Sincronía: {delay_ms:+d} ms")
        header.setEnabled(False)
        later = menu.addAction("Retrasar subtítulos +0.5 s")
        later.triggered.connect(lambda: self._nudge_subs(500_000))
        sooner = menu.addAction("Adelantar subtítulos −0.5 s")
        sooner.triggered.connect(lambda: self._nudge_subs(-500_000))
        reset = menu.addAction("Restablecer sincronía")
        reset.triggered.connect(lambda: self._player.set_subtitle_delay(0))

        menu.exec(self._pc.subs_btn.mapToGlobal(self._pc.subs_btn.rect().topLeft()))

    def _nudge_subs(self, delta_us: int):
        if self._player and self._player.available():
            self._player.set_subtitle_delay(self._player.get_subtitle_delay() + delta_us)

    def _fetch_online_subs(self, language: str):
        """Search + download subtitles via Stremio subtitle addons, keyed by
        the same id used for torrents (Kitsu for anime, IMDb otherwise) — no
        title-based search, so no title/language mismatches."""
        movie = self._playing_movie
        if movie is None:
            return
        episode = getattr(self, "_playing_episode", 0)
        lang_name = {"es": "español", "en": "inglés"}.get(language, language)
        self._set_status(f"Buscando subtítulos en {lang_name}…")

        def work():
            from actions import stremio_subs as subs
            kitsu_id = getattr(movie, "kitsu_id", "")
            imdb_id = getattr(movie, "imdb_id", "")
            try:
                if kitsu_id:
                    path = subs.fetch_subtitle_by_kitsu(
                        kitsu_id, episode=episode, language=language)
                elif imdb_id:
                    kind = getattr(movie, "media_type", "movie")
                    path = subs.fetch_subtitle_by_imdb(
                        imdb_id, kind=kind, episode=episode, language=language)
                else:
                    raise subs.StremioSubsError(
                        "Sin id (IMDb/Kitsu) para buscar subtítulos.")
                self._subtitle_ready.emit(path, lang_name)
            except Exception as e:
                self._status_sig.emit(f"Subtítulos: {e}")

        self._run_async(work)

    def _on_subtitle_ready(self, srt_path: str, label: str):
        """Load a downloaded subtitle into VLC (main thread)."""
        if self._player and self._player.add_subtitle_file(srt_path):
            self._set_status(f"✓ Subtítulos en {label} cargados")
        else:
            self._set_status("No se pudieron cargar los subtítulos")

    def _start_poller(self):
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()

        def loop():
            while not self._poll_stop.is_set():
                player = self._player
                if player is not None and player.is_running():
                    pos = player.position()
                    dur = player.duration()
                    playing = player.is_playing()
                    self._pos_sig.emit(float(pos), float(dur or 0.0), playing)
                time.sleep(0.6)

        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

    def _apply_position(self, pos: float, dur: float, playing: bool):
        self._duration = dur
        self.play_btn.set_shape(_MediaBtn.PAUSE if playing else _MediaBtn.PLAY)
        if self._float_overlay is not None:
            self._float_overlay.set_playing(playing)
        if not self._user_dragging and dur > 0:
            self.seek.setValue(int(max(0.0, min(1.0, pos / dur)) * 1000))
        self.time_lbl.setText(f"{self._fmt_clock(pos)} / {self._fmt_clock(dur)}")

    @staticmethod
    def _fmt_clock(seconds: float) -> str:
        seconds = int(max(0, seconds))
        h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _size_video(self):
        """Fix the in-panel video box to a centered 16:9 size.

        Both width and height must be fixed: the box is added with AlignHCenter,
        so without a fixed width it collapses to its (tiny) size hint and VLC has
        no surface to draw into.
        """
        if self._detached_mode is not None:
            return
        box = getattr(self, "video_box", None)
        if box is None:
            return
        avail_w = self.width() - 40
        max_h = int(self.height() * 0.74)
        if avail_w <= 0 or max_h <= 0:
            return
        w = avail_w
        h = int(w * 9 / 16)
        if h > max_h:
            h = max_h
            w = int(h * 16 / 9)
        box.setFixedSize(max(240, w), max(135, h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_view == "player":
            self._size_video()

    # -- floating / fullscreen ---------------------------------------------
    def _toggle_floating_video(self):
        if self._detached_mode == "floating":
            self._reattach_video()
        else:
            self._detach_video("floating")

    def _rebind_surface(self):
        """Re-point VLC at the surface's current HWND after a reparent."""
        if self._player and self._player.available():
            try:
                self._player.set_hwnd(int(self.video_surface.winId()))
            except Exception:
                pass

    def _on_pip_moved(self, p):
        if self._float_window is not None:
            self._float_window.move(p)

    def _on_pip_resized(self, w, h):
        if self._float_window is not None:
            self._float_window.resize(w, h)

    def _detach_video(self, mode: str):
        if self.video_box is None:
            return
        if self._detached_mode is not None:
            self._reattach_video()

        win = _DetachWindow(self._reattach_video)
        win.setWindowTitle("JARVIS — Película")
        win.setStyleSheet("background:#000000;")
        wlay = QVBoxLayout(win)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        self._player_col.removeWidget(self.video_box)
        self.video_box.setMinimumSize(0, 0)
        self.video_box.setMaximumSize(16777215, 16777215)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        wlay.addWidget(self.video_box, stretch=1)

        hint = QLabel(
            "Reproduciendo en pantalla completa" if mode == "fullscreen"
            else "Reproduciendo en ventana flotante"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            "color:rgba(203,213,225,0.5); font-size:13px;"
            "background:rgba(10,12,26,0.4); border:1px solid rgba(182,196,255,0.10);"
            "border-radius:12px;"
        )
        self._player_col.insertWidget(0, hint, stretch=1)
        self._detached_hint = hint
        self._detached_mode = mode

        overlay = _FloatOverlay({
            "toggle": self._toggle_play,
            "rewind": self._rewind_video,
            "forward": self._forward_video,
            "restore": self._reattach_video,
            "moved": self._on_pip_moved,
            "resized": self._on_pip_resized,
        }, draggable=(mode == "floating"), resizable=(mode == "floating"))
        self._float_overlay = overlay
        if self._playing_movie is not None:
            overlay.set_meta(getattr(self._playing_movie, "title", ""), "")

        if mode == "fullscreen":
            self._fs_window = win
            win.showFullScreen()
            screen = win.screen() or QApplication.primaryScreen()
            overlay.setGeometry(screen.geometry())
            for target in (win, overlay):
                shortcut = QShortcut(QKeySequence("Escape"), target)
                shortcut.activated.connect(self._reattach_video)
        else:
            self._float_window = win
            win.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            win.setMinimumSize(320, 180)
            pip_w, pip_h = 480, 270
            try:
                geo = QApplication.primaryScreen().availableGeometry()
                pos = QPoint(geo.right() - pip_w - 20, geo.bottom() - pip_h - 40)
            except Exception:
                pos = QPoint(80, 80)
            win.resize(pip_w, pip_h)
            win.move(pos)
            win.show()
            overlay.setMinimumSize(320, 180)
            overlay.resize(pip_w, pip_h)
            overlay.move(pos)

        overlay.show()
        overlay.raise_()
        QTimer.singleShot(0, self._rebind_surface)

    def _reattach_video(self):
        if self._detached_mode is None:
            return
        self._detached_mode = None
        win = self._fs_window or self._float_window
        overlay = self._float_overlay
        self._fs_window = None
        self._float_window = None
        self._float_overlay = None
        hint = getattr(self, "_detached_hint", None)
        if hint is not None:
            self._player_col.removeWidget(hint)
            hint.deleteLater()
            self._detached_hint = None
        # Reparent the video back BEFORE the window dies (destroying the surface
        # while VLC renders into it would crash).
        if win is not None and win.layout() is not None:
            win.layout().removeWidget(self.video_box)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._player_col.insertWidget(0, self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()
        if win is not None:
            win.close()
            win.deleteLater()
        QTimer.singleShot(0, self._size_video)
        QTimer.singleShot(0, self._rebind_surface)

    def _close_player(self):
        """Stop playback + streaming and return to the detail view."""
        if self._detached_mode is not None:
            self._reattach_video()
        self._poll_stop.set()
        if self._player:
            self._player.stop()
        self._playing_movie = None
        self._playing_episode = 0
        try:
            from actions import vlc_player as vp
            self._run_async(vp.stop_streaming)
        except Exception:
            pass
        if self._detail_movie:
            self._show_movie_detail(self._detail_movie)
        else:
            self._show_grid_view()


_MONTH_NAMES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
_WEEKDAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


class CalendarEventDialog(QDialog):
    """Modal para crear, ver y editar un evento de Google Calendar, incluida
    la gestión de invitados desde la libreta de contactos local
    (actions.contacts) — Google Calendar no expone una API de contactos con
    el scope que ya tenemos, así que usamos una libreta propia en su lugar."""

    def __init__(self, parent=None, event: dict | None = None, default_date: _date | None = None):
        super().__init__(parent)
        self._event = event or {}
        self._event_id = self._event.get("id")
        self._attendees: list[dict] = [
            {"name": a.get("displayName") or a.get("email"), "email": a.get("email")}
            for a in (self._event.get("attendees") or [])
            if a.get("email")
        ]
        self._delete_requested = False
        is_edit = bool(self._event_id)

        self.setWindowTitle("Editar evento" if is_edit else "Nuevo evento")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(self._style())
        self._result_payload: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(9)

        title = QLabel("Editar evento" if is_edit else "Nuevo evento")
        title.setObjectName("ComposeTitle")
        lay.addWidget(title)

        self.title_input = QLineEdit(str(self._event.get("summary") or ""))
        self.title_input.setObjectName("ComposeField")
        self.title_input.setPlaceholderText("Título del evento")
        lay.addWidget(self.title_input)

        start_dt, end_dt = self._resolve_times(default_date)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.date_input = QDateEdit()
        self.date_input.setObjectName("ComposeField")
        self.date_input.setCalendarPopup(True)
        self.date_input.setDate(QDate(start_dt.year, start_dt.month, start_dt.day))
        row.addWidget(self.date_input, stretch=1)
        self.start_time = QTimeEdit(QTime(start_dt.hour, start_dt.minute))
        self.start_time.setObjectName("ComposeField")
        row.addWidget(self.start_time)
        self.end_time = QTimeEdit(QTime(end_dt.hour, end_dt.minute))
        self.end_time.setObjectName("ComposeField")
        row.addWidget(self.end_time)
        lay.addLayout(row)

        self.location_input = QLineEdit(str(self._event.get("location") or ""))
        self.location_input.setObjectName("ComposeField")
        self.location_input.setPlaceholderText("Ubicación (opcional)")
        lay.addWidget(self.location_input)

        self.desc_input = QTextEdit(str(self._event.get("description") or ""))
        self.desc_input.setObjectName("ComposeBody")
        self.desc_input.setPlaceholderText("Descripción (opcional)")
        self.desc_input.setFixedHeight(64)
        lay.addWidget(self.desc_input)

        # -- invitados ------------------------------------------------------
        attendees_title = QLabel("Invitados")
        attendees_title.setObjectName("ComposeSectionLabel")
        lay.addWidget(attendees_title)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        self.contact_combo = QComboBox()
        self.contact_combo.setObjectName("ComposeField")
        pick_row.addWidget(self.contact_combo, stretch=1)
        add_contact_btn = QPushButton("Añadir")
        add_contact_btn.setObjectName("ComposeAttach")
        add_contact_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_contact_btn.clicked.connect(self._add_selected_contact)
        pick_row.addWidget(add_contact_btn)
        lay.addLayout(pick_row)
        self._reload_contacts()

        new_row = QHBoxLayout()
        new_row.setSpacing(6)
        self.new_name_input = QLineEdit()
        self.new_name_input.setObjectName("ComposeField")
        self.new_name_input.setPlaceholderText("Nombre (nuevo invitado)")
        new_row.addWidget(self.new_name_input, stretch=1)
        self.new_email_input = QLineEdit()
        self.new_email_input.setObjectName("ComposeField")
        self.new_email_input.setPlaceholderText("email@ejemplo.com")
        self.new_email_input.returnPressed.connect(self._add_new_attendee)
        new_row.addWidget(self.new_email_input, stretch=1)
        add_new_btn = QPushButton()
        add_new_btn.setObjectName("ComposeAttach")
        add_new_btn.setIcon(_line_icon("plus", C.PRI, 13))
        add_new_btn.setIconSize(QSize(13, 13))
        add_new_btn.setFixedSize(30, 30)
        add_new_btn.setToolTip("Añadir invitado y guardarlo como contacto")
        add_new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_new_btn.clicked.connect(self._add_new_attendee)
        new_row.addWidget(add_new_btn)
        lay.addLayout(new_row)

        self.attendees_list = QListWidget()
        self.attendees_list.setObjectName("ComposeAttendeesList")
        self.attendees_list.setMaximumHeight(104)
        self.attendees_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.attendees_list)
        self._refresh_attendees_list()

        self.feedback = QLabel("")
        self.feedback.setObjectName("ComposeFeedback")
        self.feedback.setWordWrap(True)
        lay.addWidget(self.feedback)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        if is_edit:
            delete_btn = QPushButton("Eliminar evento")
            delete_btn.setObjectName("ComposeCancel")
            delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            delete_btn.clicked.connect(self._request_delete)
            buttons.addWidget(delete_btn)
        buttons.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("ComposeCancel")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.save_btn = QPushButton("Guardar cambios" if is_edit else "Crear evento")
        self.save_btn.setObjectName("ComposeSend")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setIcon(_line_icon("plus", "#0a0e26", 15))
        self.save_btn.setIconSize(QSize(15, 15))
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn)
        lay.addLayout(buttons)

    # -- helpers de fecha/hora ----------------------------------------------
    def _resolve_times(self, default_date: _date | None) -> tuple[_datetime, _datetime]:
        default_date = default_date or _date.today()
        fallback_start = _datetime(default_date.year, default_date.month, default_date.day, 9, 0)
        fallback_end = fallback_start + _timedelta(hours=1)
        if not self._event:
            return fallback_start, fallback_end
        try:
            from dateutil import parser as dtparser
            start_raw = str(self._event.get("start") or "")
            end_raw = str(self._event.get("end") or "")
            start_dt = dtparser.parse(start_raw).replace(tzinfo=None) if start_raw else fallback_start
            end_dt = dtparser.parse(end_raw).replace(tzinfo=None) if end_raw else (start_dt + _timedelta(hours=1))
            return start_dt, end_dt
        except Exception:
            return fallback_start, fallback_end

    # -- invitados / contactos -----------------------------------------------
    def _reload_contacts(self):
        from actions import contacts as contacts_mod
        self.contact_combo.clear()
        self.contact_combo.addItem("Elegir contacto…", "")
        for c in contacts_mod.list_contacts():
            email = c.get("email") or ""
            if any(a["email"].lower() == email.lower() for a in self._attendees):
                continue
            name = c.get("name") or email
            label = f"{name} <{email}>" if name != email else email
            self.contact_combo.addItem(label, email)

    def _add_selected_contact(self):
        email = self.contact_combo.currentData()
        if not email:
            return
        from actions import contacts as contacts_mod
        contact = contacts_mod.find_contact(email) or {"name": email, "email": email}
        self._add_attendee(contact.get("name") or email, email)
        self._reload_contacts()

    def _add_new_attendee(self):
        from actions import contacts as contacts_mod
        name = self.new_name_input.text().strip()
        email = self.new_email_input.text().strip()
        if not contacts_mod.is_valid_email(email):
            self.feedback.setText("Introduce un email válido para el invitado.")
            self.feedback.setStyleSheet("color:#FF5E82; background: transparent;")
            return
        try:
            contacts_mod.upsert_contact(name, email)
        except Exception:
            pass
        self._add_attendee(name or email, email)
        self.new_name_input.clear()
        self.new_email_input.clear()
        self.feedback.setText("")
        self._reload_contacts()

    def _add_attendee(self, name: str, email: str):
        if any(a["email"].lower() == email.lower() for a in self._attendees):
            return
        self._attendees.append({"name": name or email, "email": email})
        self._refresh_attendees_list()

    def _remove_attendee(self, email: str):
        self._attendees = [a for a in self._attendees if a["email"].lower() != email.lower()]
        self._refresh_attendees_list()
        self._reload_contacts()

    def _refresh_attendees_list(self):
        self.attendees_list.clear()
        if not self._attendees:
            item = QListWidgetItem("Sin invitados.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.attendees_list.addItem(item)
            return
        for a in self._attendees:
            item = QListWidgetItem()
            self.attendees_list.addItem(item)
            row = QWidget()
            row.setStyleSheet("background: rgba(255,255,255,0.04); border-radius: 6px;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 6, 4)
            rl.setSpacing(6)
            text = a["email"] if a["name"] == a["email"] else f"{a['name']}  ·  {a['email']}"
            label = QLabel(text)
            label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; background: transparent;")
            rl.addWidget(label, stretch=1)
            rm_btn = QPushButton()
            rm_btn.setIcon(_line_icon("close", C.TEXT_DIM, 11))
            rm_btn.setIconSize(QSize(11, 11))
            rm_btn.setFixedSize(20, 20)
            rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rm_btn.setToolTip("Quitar invitado")
            rm_btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; }
                QPushButton:hover { background: rgba(255,94,130,0.16); border-radius: 5px; }
            """)
            rm_btn.clicked.connect(lambda _c=False, em=a["email"]: self._remove_attendee(em))
            rl.addWidget(rm_btn)
            item.setSizeHint(row.sizeHint())
            self.attendees_list.setItemWidget(item, row)

    # -- resultado ------------------------------------------------------------
    def payload(self) -> dict | None:
        return self._result_payload

    def delete_requested(self) -> bool:
        return self._delete_requested

    def event_id(self) -> str | None:
        return self._event_id

    def _request_delete(self):
        self._delete_requested = True
        self.accept()

    def _save(self):
        title = self.title_input.text().strip()
        if not title:
            self.feedback.setText("Escribe un título para el evento.")
            self.feedback.setStyleSheet("color:#FF5E82; background: transparent;")
            return
        d = self.date_input.date()
        start_t = self.start_time.time()
        end_t = self.end_time.time()
        start_dt = _datetime(d.year(), d.month(), d.day(), start_t.hour(), start_t.minute())
        end_dt = _datetime(d.year(), d.month(), d.day(), end_t.hour(), end_t.minute())
        if end_dt <= start_dt:
            end_dt = start_dt + _timedelta(hours=1)
        self._result_payload = {
            "summary": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "location": self.location_input.text().strip(),
            "description": self.desc_input.toPlainText().strip(),
            "attendees": [a["email"] for a in self._attendees],
        }
        self.accept()

    def _style(self) -> str:
        return f"""
            QDialog {{
                background: #0a1422;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
            }}
            QLabel#ComposeTitle {{
                color: #f8fafc; font-size: 18px; font-weight: 900;
            }}
            QLabel#ComposeSectionLabel {{
                color: {C.TEXT_MED};
                background: transparent;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.5px;
                margin-top: 4px;
            }}
            QLineEdit#ComposeField, QTextEdit#ComposeBody, QDateEdit#ComposeField,
            QTimeEdit#ComposeField, QComboBox#ComposeField {{
                background: rgba(3, 9, 17, 0.72);
                color: #e8ebff;
                border: 1px solid rgba(182, 196, 255, 0.16);
                border-radius: 7px;
                padding: 8px 11px;
                font-size: 13px;
                selection-background-color: #5e82ff;
            }}
            QLineEdit#ComposeField:focus, QTextEdit#ComposeBody:focus,
            QDateEdit#ComposeField:focus, QTimeEdit#ComposeField:focus,
            QComboBox#ComposeField:focus {{
                border-color: rgba(182, 196, 255, 0.55);
            }}
            QListWidget#ComposeAttendeesList {{
                background: rgba(3, 9, 17, 0.5);
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 7px;
                padding: 4px;
            }}
            QPushButton#ComposeAttach {{
                background: rgba(255, 255, 255, 0.05);
                color: #dce1ff;
                border: 1px solid rgba(182, 196, 255, 0.25);
                border-radius: 7px;
                padding: 0 12px;
                min-height: 30px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#ComposeAttach:hover {{
                background: rgba(94, 130, 255, 0.14);
                border-color: rgba(182, 196, 255, 0.45);
            }}
            QLabel#ComposeFeedback {{
                color: rgba(188, 198, 238, 0.62);
                background: transparent;
                font-size: 11px;
            }}
            QPushButton#ComposeSend {{
                background: {C.PRI};
                color: #0a0e26;
                border: none;
                border-radius: 7px;
                padding: 0 20px 0 14px;
                min-height: 34px;
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton#ComposeSend:hover {{ background: #a7afff; }}
            QPushButton#ComposeCancel {{
                background: rgba(255, 255, 255, 0.05);
                color: #dce1ff;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 7px;
                padding: 0 16px;
                min-height: 34px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#ComposeCancel:hover {{ background: rgba(255,255,255,0.09); }}
        """ + _scrollbar_qss()


class _EventRow(QFrame):
    """Fila clicable de la lista de eventos: abre el modal de detalle/edición
    al pulsar en cualquier punto salvo en el botón de borrar (que consume su
    propio evento de clic antes de que llegue aquí)."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit()


class _CalendarDayCell(QFrame):
    """Celda de día del grid mensual: número + puntos de evento como widgets
    reales (no texto multilínea embebido en un botón), para que la altura de
    la celda sea estable sin depender de cuántos eventos tenga ese día."""

    clicked = pyqtSignal(object)

    def __init__(self, day: _date, parent=None):
        super().__init__(parent)
        self.day = day
        self.setObjectName("CalDayCell")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(44)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(3)

        self.number_label = QLabel(str(day.day))
        self.number_label.setObjectName("CalDayNumber")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.number_label)

        self.dots_row = QHBoxLayout()
        self.dots_row.setSpacing(3)
        self.dots_row.setContentsMargins(0, 0, 0, 0)
        dots_host = QWidget()
        dots_host.setLayout(self.dots_row)
        dots_host.setStyleSheet("background: transparent;")
        lay.addWidget(dots_host, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def set_event_count(self, n: int):
        while self.dots_row.count():
            item = self.dots_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        shown = min(n, 3)
        for _ in range(shown):
            dot = QLabel()
            dot.setFixedSize(5, 5)
            dot.setStyleSheet(f"background: {C.PRI_DIM}; border-radius: 2px;")
            self.dots_row.addWidget(dot)
        if n > shown:
            more = QLabel(f"+{n - shown}")
            more.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 8px; font-weight: 700; background: transparent;")
            self.dots_row.addWidget(more)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.day)


class AnimeModePanel(MoviesModePanel):
    """Anime discovery and streaming — same UI as Movies but backed by Nyaa/Torrentio-nyaa.

    Adds MyAnimeList account integration: connect, browse watchlist, and update
    watch status / episode progress / score from within Jarvis.
    """

    _mal_login_done = pyqtSignal(str, str)   # (username, error)
    _mal_status_ready = pyqtSignal(int, dict) # (mal_id, status_data)
    _mal_save_done = pyqtSignal(str)          # (result_message)
    _episodes_ready = pyqtSignal(list, object, object)   # (episodes, anime, poster_pixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title.setText("Anime")
        self._search.setPlaceholderText("Buscar anime…")

        # MAL state
        self._mal_detail_card = None
        self._mal_detail_anime = None
        self._ep_loading_lbl = None

        # Insert MAL row between the top bar (index 0) and the stack (index 1)
        self._mal_row = self._build_mal_row()
        self.layout().insertWidget(1, self._mal_row)

        # Wire new signals
        self._mal_login_done.connect(self._on_mal_login)
        self._mal_status_ready.connect(self._on_mal_status_ready)
        self._mal_save_done.connect(self._on_mal_save_done)

    # ------------------------------------------------------------------
    # Genre pill row
    # ------------------------------------------------------------------

    _GENRE_CHIPS = (
        ("Acción", 1), ("Aventura", 2), ("Comedia", 4), ("Drama", 8),
        ("Fantasía", 10), ("Romance", 22), ("Terror", 14), ("Misterio", 7),
        ("Sci-Fi", 24), ("Deportes", 30),
    )

    def _chip_defs(self) -> list:
        defs = [
            ("Viendo", lambda: self._load_mal_list("watching")),
            ("Planificado", lambda: self._load_mal_list("plan_to_watch")),
            ("Completado", lambda: self._load_mal_list("completed")),
            ("Tendencias", self._load_trending),
            ("En emisión", self._load_recent),
        ]
        for label, gid in self._GENRE_CHIPS:
            defs.append((label, lambda g=gid, l=label: self._load_genre(g, l)))
        return defs

    def _load_genre(self, genre_id: int, label: str):
        self._set_status(f"Cargando anime de {label}…")

        def work():
            try:
                from actions import kitsu
                # kitsu maps the Spanish label ("Acción") to its genre string.
                items = kitsu.get_anime_by_genre(label, limit=15)
                self._results_ready.emit(items, f"Anime — {label}", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    # ------------------------------------------------------------------
    # MAL header row
    # ------------------------------------------------------------------

    def _build_mal_row(self) -> QWidget:
        from actions.mal_auth import is_logged_in, get_username
        w = QWidget()
        w.setFixedHeight(38)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        if is_logged_in():
            self._add_logged_in_widgets(lay, get_username())
            w.setVisible(False)   # el avatar de la barra de búsqueda ya lo indica
        else:
            lbl = QLabel("MyAnimeList:")
            lbl.setFont(QFont("Inter", 9))
            lbl.setStyleSheet("color:#9aa6ab; background:transparent;")
            lay.addWidget(lbl)
            self._add_connect_widget(lay)

        lay.addStretch(1)
        return w

    def _add_connect_widget(self, lay: QHBoxLayout):
        btn = QPushButton("Conectar cuenta")
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background:transparent; color:#f4f4f2;
                border:1px solid #2f3d43; border-radius:14px;
                font-size:9pt; font-family:Inter; padding:0 14px;
            }
            QPushButton:hover { background:#2f3d43; }
        """)
        btn.clicked.connect(self._do_mal_login)
        lay.addWidget(btn)

        from actions.mal_auth import get_client_id
        if not get_client_id():
            help_lbl = QLabel("(necesita client_id en api_keys.json → mal_client_id)")
            help_lbl.setFont(QFont("Inter", 8))
            help_lbl.setStyleSheet("color:#9aa6ab; background:transparent;")
            lay.addWidget(help_lbl)

        if hasattr(self, "_user_btn"):
            self._user_btn.setVisible(False)

    def _add_logged_in_widgets(self, lay: QHBoxLayout, username: str):
        # No widgets propios: el avatar vive en la barra de búsqueda.
        if hasattr(self, "_user_btn"):
            self._user_btn.set_username(username)
            self._user_btn.setVisible(True)

    def _refresh_mal_row(self):
        """Rebuild the MAL row in place after login/logout."""
        lay = self._mal_row.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        from actions.mal_auth import is_logged_in, get_username
        if is_logged_in():
            self._add_logged_in_widgets(lay, get_username())
            self._mal_row.setVisible(False)
        else:
            lbl = QLabel("MyAnimeList:")
            lbl.setFont(QFont("Inter", 9))
            lbl.setStyleSheet("color:#9aa6ab; background:transparent;")
            lay.addWidget(lbl)
            self._add_connect_widget(lay)
            self._mal_row.setVisible(True)
        lay.addStretch(1)

    # ------------------------------------------------------------------
    # MAL login / logout
    # ------------------------------------------------------------------

    def _do_mal_login(self):
        from actions.mal_auth import get_client_id
        client_id = get_client_id()
        if not client_id:
            self._set_status(
                "Añade 'mal_client_id' a config/api_keys.json "
                "(registra tu app en myanimelist.net/apiconfig)"
            )
            return
        self._set_status("Abriendo navegador en http://localhost:8765/callback para autorizar MyAnimeList…")

        def work():
            try:
                from actions.mal_auth import login
                tokens = login(client_id)
                username = tokens.get("username", "")
                if not username:
                    raise RuntimeError("El login devolvió un token pero sin usuario.")
                self._mal_login_done.emit(username, "")
            except Exception as exc:
                import traceback
                err_msg = f"{type(exc).__name__}: {exc}"
                print(f"MAL login failed: {err_msg}\n{traceback.format_exc()}")
                self._mal_login_done.emit("", err_msg)

        self._run_async(work)

    def _on_mal_login(self, username: str, error: str):
        if error:
            self._set_status(f"❌ Error de login MAL: {error}")
        elif username:
            self._set_status(f"✓ Conectado a MyAnimeList como @{username}")
            self._refresh_mal_row()
        else:
            self._set_status("❌ Login MAL: respuesta inválida (sin usuario ni error)")

    def _do_mal_logout(self):
        from actions.mal_auth import logout
        logout()
        self._refresh_mal_row()
        self._set_status("Sesión de MyAnimeList cerrada")

    def _show_user_menu(self):
        """Menú de usuario en la barra de búsqueda."""
        from actions.mal_auth import get_username
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background:#1a2226; color:#f4f4f2;
                border:1px solid #2f3d43; border-radius:8px; padding:6px;
            }
            QMenu::item { padding:6px 18px; border-radius:5px; }
            QMenu::item:selected { background:#2f3d43; }
        """)

        username = get_username()
        if username:
            user_act = menu.addAction(f"👤  @{username}")
            user_act.setEnabled(False)
            menu.addSeparator()

        logout_act = menu.addAction("Cerrar sesión")
        logout_act.triggered.connect(self._do_mal_logout)

        menu.exec(self._user_btn.mapToGlobal(self._user_btn.rect().bottomLeft()))

    # ------------------------------------------------------------------
    # MAL watchlist loading
    # ------------------------------------------------------------------

    def _load_mal_list(self, status: str):
        from actions.mal_api import STATUS_LABELS
        label = STATUS_LABELS.get(status, status)
        self._set_status(f"Cargando lista MAL: {label}…")

        def work():
            try:
                from actions.mal_api import get_watchlist
                from actions.movie_search import Movie
                raw = get_watchlist(status=status, limit=50)
                items = [
                    Movie(
                        tmdb_id=0,
                        title=r["title"],
                        poster_url=r["poster_url"],
                        rating=r["rating"],
                        media_type="tv",
                        mal_id=r["mal_id"],
                        total_episodes=r.get("mal_total", 0),
                    )
                    for r in raw
                ]
                header = f"MAL — {label} ({len(items)})"
                self._results_ready.emit(items, header, "")
            except Exception as exc:
                self._results_ready.emit([], "", str(exc))

        self._run_async(work)

    # ------------------------------------------------------------------
    # Overridden data sources
    # ------------------------------------------------------------------

    def _do_search(self):
        query = self._search.text().strip()
        if not query:
            return
        self._set_status(f"Buscando anime «{query}»…")

        def work():
            try:
                from actions import kitsu
                items = kitsu.search_anime(query, limit=15)
                self._results_ready.emit(items, f"Anime: «{query}»", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_trending(self):
        self._set_status("Cargando anime en tendencia…")

        def work():
            try:
                from actions import kitsu
                items = kitsu.get_trending_anime(limit=15)
                self._results_ready.emit(items, "Anime en tendencia", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_recent(self):
        self._set_status("Buscando anime en emisión…")

        def work():
            try:
                from actions import kitsu
                items = kitsu.get_airing_anime(limit=15)
                self._results_ready.emit(items, "Anime en emisión", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    # ------------------------------------------------------------------
    # Detail view with episode picker + MAL status section
    # ------------------------------------------------------------------

    def _show_movie_detail(self, anime):
        self._current_view = "detail"
        self._detail_movie = anime
        self._mal_detail_anime = anime
        self._mal_detail_card = None
        self._back_btn.setVisible(True)
        self._title.setText(anime.title)

        # Clear the previous detail view. Delete widgets AND any nested layouts
        # (the episode list used to be a bare layout that survived this loop, so
        # a prior anime's episodes bled into the new one).
        self._clear_layout(self._detail_layout)

        # -- Hero banner at the top of the detail view (same large hero) --
        hero = _HeroBanner()
        hero.setFixedHeight(480)
        hero.play_clicked.connect(lambda m: self._search_and_play(m))
        hero.info_clicked.connect(lambda _: None)
        hero.set_movie(anime)   # fetches the backdrop by itself
        self._detail_layout.addWidget(hero)

        # -- Meta row (year · rating · episodes) --
        meta_widget = QWidget()
        meta_lay = QHBoxLayout(meta_widget)
        meta_lay.setContentsMargins(4, 8, 4, 4)
        meta_lay.setSpacing(16)

        total_ep = int(getattr(anime, "total_episodes", 0) or 0)
        for text in filter(None, [
            str(anime.release_year) if anime.release_year else None,
            f"★ {anime.rating:.1f}" if anime.rating else None,
            f"{total_ep} eps" if total_ep else None,
        ]):
            lbl = QLabel(text)
            lbl.setFont(QFont(FONT_UI, 10))
            lbl.setStyleSheet(f"color:{C.TEXT_MED};")
            meta_lay.addWidget(lbl)
        meta_lay.addStretch(1)
        self._detail_layout.addWidget(meta_widget)

        # -- Synopsis --
        if anime.overview:
            overview = QLabel(anime.overview)
            overview.setWordWrap(True)
            overview.setFont(QFont(FONT_UI, 10))
            overview.setStyleSheet(f"color:{C.TEXT_MED}; padding:0 4px;")
            self._detail_layout.addWidget(overview)

        # -- MAL status card (only if logged in and we have a mal_id) --
        from actions.mal_auth import is_logged_in
        mal_id = int(getattr(anime, "mal_id", 0) or 0)
        if is_logged_in() and mal_id:
            self._mal_detail_card = self._build_mal_status_card(anime, mal_id)
            self._detail_layout.addWidget(self._mal_detail_card)
            self._load_mal_status(mal_id)

        # -- Episodes section --
        ep_title = QLabel("Episodios")
        ep_title.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
        ep_title.setStyleSheet(f"color:{C.TEXT}; padding:8px 4px 0 4px;")
        self._detail_layout.addWidget(ep_title)

        self._ep_loading_lbl = QLabel("Cargando episodios…")
        self._ep_loading_lbl.setFont(QFont(FONT_UI, 10))
        self._ep_loading_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; padding:4px;")
        self._detail_layout.addWidget(self._ep_loading_lbl)

        eps_widget = QWidget()
        eps_widget.setStyleSheet("background:transparent;")
        self._episodes_list_container = QVBoxLayout(eps_widget)
        self._episodes_list_container.setSpacing(6)
        self._episodes_list_container.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.addWidget(eps_widget)

        self._detail_layout.addStretch()
        self._stack.setCurrentIndex(1)

        kitsu_id = getattr(anime, "kitsu_id", "")
        if kitsu_id:
            self._load_episodes(anime, kitsu_id)
        else:
            self._ep_loading_lbl.setText("No se encontraron episodios.")

    def _load_episodes(self, anime, kitsu_id: str):
        try:
            self._episodes_ready.disconnect()
        except Exception:
            pass
        self._episodes_ready.connect(self._on_episodes_ready)

        def work():
            px = None
            if anime.poster_url:
                try:
                    data = _download_image(anime.poster_url)
                    px = QPixmap()
                    px.loadFromData(data)
                except Exception:
                    px = None
            try:
                from actions import kitsu
                episodes = kitsu.get_episodes(kitsu_id)
                self._episodes_ready.emit(episodes, anime, px)
            except Exception:
                self._episodes_ready.emit([], anime, px)

        self._run_async(work)

    def _on_episodes_ready(self, episodes: list, anime, poster_pixmap):
        if anime is not self._detail_movie:
            return   # el usuario ya navegó a otro anime

        if self._ep_loading_lbl:
            self._ep_loading_lbl.setText(
                "No se encontraron episodios." if not episodes else ""
            )
            self._ep_loading_lbl.setVisible(not episodes)

        for ep in episodes:
            row = _EpisodeRow(ep, poster_pixmap)
            row.play_clicked.connect(
                lambda n, a=anime: self._search_and_play(a, episode=n)
            )
            self._episodes_list_container.addWidget(row)

    # ------------------------------------------------------------------
    # MAL status card in the detail view
    # ------------------------------------------------------------------

    def _build_mal_status_card(self, anime, mal_id: int) -> QWidget:
        from actions.mal_api import STATUS_LABELS, STATUS_KEYS
        card = QFrame()
        card.setStyleSheet(
            f"background:{C.PANEL2}; border:1px solid {C.BORDER}; border-radius:10px;"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        title = QLabel("MyAnimeList")
        title.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.PRI};")
        lay.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self._mk_label("Estado:"))
        status_combo = QComboBox()
        status_combo.addItem("— Sin añadir —", "")
        for key in STATUS_KEYS:
            status_combo.addItem(STATUS_LABELS[key], key)
        row.addWidget(status_combo)

        row.addWidget(self._mk_label("Ep:"))
        ep_minus = QPushButton("◀")
        ep_minus.setFixedSize(24, 24)
        ep_spin = QSpinBox()
        ep_spin.setRange(0, max(1, int(getattr(anime, "total_episodes", 0) or 999)))
        ep_plus = QPushButton("▶")
        ep_plus.setFixedSize(24, 24)
        ep_minus.clicked.connect(lambda: ep_spin.setValue(max(0, ep_spin.value() - 1)))
        ep_plus.clicked.connect(lambda: ep_spin.setValue(ep_spin.value() + 1))
        row.addWidget(ep_minus)
        row.addWidget(ep_spin)
        row.addWidget(ep_plus)
        if int(getattr(anime, "total_episodes", 0) or 0):
            row.addWidget(self._mk_label(f"/ {anime.total_episodes}"))

        row.addWidget(self._mk_label("Nota:"))
        score_combo = QComboBox()
        score_combo.addItem("—", 0)
        for s in range(1, 11):
            score_combo.addItem(str(s), s)
        row.addWidget(score_combo)

        save_btn = QPushButton("Guardar")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background:{C.PRI_DIM}; color:{C.DARK};
                border:none; border-radius:6px; padding:4px 14px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{C.PRI}; }}
        """)
        save_btn.clicked.connect(lambda: self._save_mal_status(
            mal_id, status_combo, ep_spin, score_combo
        ))
        row.addWidget(save_btn)
        row.addStretch(1)
        lay.addLayout(row)

        card._status_combo = status_combo
        card._ep_spin = ep_spin
        card._score_combo = score_combo
        return card

    @staticmethod
    def _mk_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont(FONT_UI, 9))
        lbl.setStyleSheet(f"color:{C.TEXT_MED};")
        return lbl

    def _load_mal_status(self, mal_id: int):
        def work():
            try:
                from actions.mal_api import get_anime_status
                data = get_anime_status(mal_id)
                self._mal_status_ready.emit(mal_id, data)
            except Exception:
                self._mal_status_ready.emit(mal_id, {})

        self._run_async(work)

    def _on_mal_status_ready(self, mal_id: int, data: dict):
        card = self._mal_detail_card
        if card is None or not hasattr(card, "_status_combo"):
            return
        ls = data.get("list_status", {}) or {}
        status = ls.get("status", "")
        idx = card._status_combo.findData(status)
        card._status_combo.setCurrentIndex(idx if idx >= 0 else 0)
        card._ep_spin.setValue(int(ls.get("num_episodes_watched") or 0))
        score_idx = card._score_combo.findData(int(ls.get("score") or 0))
        card._score_combo.setCurrentIndex(score_idx if score_idx >= 0 else 0)

    def _save_mal_status(self, mal_id: int, status_combo, ep_spin, score_combo):
        status = status_combo.currentData() or None
        watched = ep_spin.value()
        score = score_combo.currentData() or None
        self._set_status("Guardando en MyAnimeList…")

        def work():
            try:
                from actions.mal_api import update_status
                update_status(mal_id, status=status, num_watched=watched, score=score)
                self._mal_save_done.emit("✓ Guardado en MyAnimeList")
            except Exception as exc:
                self._mal_save_done.emit(f"Error al guardar en MAL: {exc}")

        self._run_async(work)

    def _on_mal_save_done(self, message: str):
        self._set_status(message)

    def _search_and_play(self, anime, episode: int = 0):
        """Anime torrents via the Stremio addon flow: Kitsu id → Torrentio.

        Torrentio indexes anime by Kitsu id with absolute episode numbering, so
        we hand it "kitsu:<id>:<episode>" straight — no title matching, no
        "Monster Hunter" collisions. See actions/kitsu.py + actions/torrentio.py.
        """
        ep_txt = f" ep {episode}" if episode else ""
        self._set_status(f"Buscando torrents de «{anime.title}»{ep_txt}…")
        kitsu_id = getattr(anime, "kitsu_id", "")

        def work():
            from actions import torrentio
            from actions import torrent_search as ts

            kid = kitsu_id
            # Watchlist/MAL items may lack a kitsu_id; resolve it by title.
            if not kid:
                try:
                    from actions import kitsu
                    hits = kitsu.search_anime(anime.title, limit=1)
                    kid = hits[0].kitsu_id if hits else ""
                except Exception:
                    kid = ""
            if not kid:
                self._status_sig.emit(
                    f"No encontré «{anime.title}» en Kitsu.")
                return

            # Build the Stremio id: series episode, or movie when the title has
            # no episode list.
            if episode:
                video_id, stype = f"kitsu:{kid}:{episode}", "series"
            elif int(getattr(anime, "total_episodes", 0) or 0) > 1:
                video_id, stype = f"kitsu:{kid}:1", "series"
            else:
                video_id, stype = f"kitsu:{kid}", "movie"

            self._status_sig.emit(
                f"Buscando «{anime.title}»{ep_txt} en Torrentio ({video_id})…")
            try:
                streams = torrentio.search_by_id(video_id, stream_type=stype,
                                                 limit=25)
            except Exception as exc:
                self._status_sig.emit(
                    f"No encontré torrents para «{anime.title}»  [{exc}]")
                return

            # Torrentio already returns only streams for this exact id, ranked by
            # seeders. Adapt Stream → torrent_search.Torrent for the dialog.
            torrents = [
                ts.Torrent(
                    title=s.title, magnet=s.magnet, seeders=s.seeders,
                    leechers=0, size=s.size, spanish=s.spanish,
                    provider=s.provider or "Torrentio",
                    file_idx=getattr(s, "file_idx", -1))
                for s in streams
            ]
            self._torrents_found.emit(torrents, anime, episode)

        self._run_async(work)






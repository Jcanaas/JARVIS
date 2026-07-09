from __future__ import annotations
from actions import app_settings
import time

import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *
from actions.perf_helpers import DiskImageCache

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





class YouTubeModePanel(QWidget):
    """YouTube-like mode: search on top, responsive grid of recommended videos,
    and an embedded mpv player page."""

    _results_sig = pyqtSignal(object, str, str)
    _thumb_sig   = pyqtSignal(str, object)
    _like_sig    = pyqtSignal(str, bool, str)
    _play_sig    = pyqtSignal(str, bool)
    _pos_sig     = pyqtSignal(float, float, bool)
    _comments_sig = pyqtSignal(str, object, str)
    _details_sig = pyqtSignal(str, object, str)

    def __init__(self, progress_hook=None, parent=None):
        super().__init__(parent)
        self._progress_hook = progress_hook
        self._by_id: dict[str, dict] = {}
        self._thumb_cache: dict[str, bytes] = {}
        self._thumb_disk_cache = DiskImageCache("yt_thumbs")
        self._thumb_loading: set[str] = set()
        self._thumb_targets: dict[str, list[QLabel]] = {}
        self._thumb_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="yt-art")
        self._thumb_executor_closed = False
        self._player = None
        self._current: dict = {}
        self._liked = False
        self._duration = 0.0
        self._user_dragging = False
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._loaded_feed = False
        self._feed = "recommended"
        self._detached_mode: str | None = None
        self._fs_window: QWidget | None = None
        self._float_window: QWidget | None = None
        self._float_overlay = None
        self._float_drag_pos = None
        self._ordered_ids: list[str] = []
        self._desc_full = ""
        self._desc_expanded = False

        self._results_sig.connect(self._apply_results)
        self._thumb_sig.connect(self._apply_thumb)
        self._like_sig.connect(self._apply_like)
        self._play_sig.connect(self._apply_play_started)
        self._pos_sig.connect(self._apply_position)
        self._comments_sig.connect(self._apply_comments)
        self._details_sig.connect(self._apply_details)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

        self.setStyleSheet(self._panel_style())
        self._build_ui()
        QTimer.singleShot(150, self._load_initial_feed)

    # ----------------------------------------------------------------- styles
    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
            }}
            QLineEdit#YtSearch {{
                min-height: 44px;
                background: rgba(10, 12, 26, 0.88);
                color: {C.TEXT};
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 22px;
                padding: 0 18px;
                font-size: 13px;
                selection-background-color: {C.PRI};
                selection-color: #090c20;
            }}
            QLineEdit#YtSearch:focus {{
                background: rgba(14, 15, 18, 0.94);
                border-color: rgba(182, 196, 255, 0.55);
            }}
            QPushButton#YtSearchButton {{
                min-height: 44px;
                background: rgba(248, 113, 113, 0.18);
                color: #FFE4E4;
                border: 1px solid rgba(248, 113, 113, 0.34);
                border-radius: 22px;
                padding: 0 20px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton#YtSearchButton:hover {{
                background: rgba(248, 113, 113, 0.28);
                border-color: rgba(248, 113, 113, 0.55);
            }}
            QWidget#YtCardThumb, QLabel#YtCardThumb {{
                background: rgba(255, 255, 255, 0.05);
                border-radius: 12px;
            }}
            QScrollArea#YtScroll {{ background: transparent; border: none; }}
            QLabel#YtHeader {{ color: {C.TEXT}; font-size: 15px; font-weight: 800; }}
            QLabel#YtStatus {{ color: {C.TEXT_MED}; font-size: 11px; }}
            QLabel#YtTitle {{ color: {C.TEXT}; font-size: 19px; font-weight: 800; }}
            QLabel#YtChannel {{ color: {C.TEXT_MED}; font-size: 12px; }}
            QPushButton#YtBack {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 9px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#YtBack:hover {{ background: rgba(182, 196, 255, 0.12); color: {C.TEXT}; }}
        """ + _scrollbar_qss()

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.nav_back_btn = _icon_button("chevron_left", "Volver", size=44, icon_size=20)
        self.nav_back_btn.clicked.connect(self._go_home)
        self.nav_back_btn.setVisible(False)
        top.addWidget(self.nav_back_btn)
        self.home_btn = _icon_button("home", "Inicio (recomendados)", size=44, icon_size=19)
        self.home_btn.clicked.connect(self._go_home)
        top.addWidget(self.home_btn)
        self.search_input = SearchGlowInput("Buscar en YouTube")
        self.search_input.returnPressed.connect(self._do_search)
        top.addWidget(self.search_input, stretch=1)
        root.addLayout(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        # ---- grid page ----
        grid_page = QWidget()
        gp = QVBoxLayout(grid_page)
        gp.setContentsMargins(0, 0, 0, 0)
        gp.setSpacing(10)
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        self.tab_recommended = self._make_tab("Recomendaciones", "recommended")
        self.tab_foryou = self._make_tab("Suscripciones", "home")
        self.tab_trending = self._make_tab("Tendencias", "trending")
        head_row.addWidget(self.tab_recommended)
        head_row.addWidget(self.tab_foryou)
        head_row.addWidget(self.tab_trending)
        head_row.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("YtStatus")
        head_row.addWidget(self.status)
        gp.addLayout(head_row)
        # Hidden label kept for compatibility with result headers
        self.header_lbl = QLabel("")
        self.header_lbl.setObjectName("YtHeader")
        self.header_lbl.setVisible(False)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("YtScroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.viewport().setStyleSheet("background: transparent;")
        self.grid_host = QWidget()
        self.grid_host.setStyleSheet("background: transparent;")
        self.flow = FlowLayout(self.grid_host, margin=2)
        self.grid_scroll.setWidget(self.grid_host)
        gp.addWidget(self.grid_scroll, stretch=1)
        self.stack.addWidget(grid_page)

        # ---- player page ----
        player_page = QWidget()
        pp = QVBoxLayout(player_page)
        pp.setContentsMargins(0, 0, 0, 0)
        pp.setSpacing(8)

        # --- video at the top (16:9, centered; height via _size_video) ---
        self.video_surface = QWidget()
        self.video_surface.setObjectName("YtSurface")
        self.video_surface.setStyleSheet("QWidget#YtSurface { background: #000000; border-radius: 12px; }")
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surf_lay = QVBoxLayout(self.video_surface)
        surf_lay.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel("Selecciona un vídeo")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: rgba(203,213,225,0.45); font-size: 13px; background: transparent;")
        surf_lay.addWidget(self.placeholder)
        self.video_box = _AspectVideo(self.video_surface)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.video_box.setMinimumHeight(200)

        video_holder = QVBoxLayout()
        video_holder.setContentsMargins(0, 0, 0, 0)
        video_holder.setSpacing(0)
        video_holder.addWidget(self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        self._left_col = video_holder
        pp.addLayout(video_holder)

        # --- YouTube-style controls overlaid on the video (shown on hover) ---
        self._pc = _PanelControls(self, self.video_box)
        self.play_btn = self._pc.play_btn
        self.seek = self._pc.seek
        self.time_lbl = self._pc.time_lbl
        self.volume = self._pc.volume
        self.like_btn = self._pc.like_btn
        self.download_btn = self._pc.download_btn
        self.float_btn = self._pc.float_btn
        self.fullscreen_btn = self._pc.fullscreen_btn
        self.like_btn.setToolTip("Me gusta (en tu cuenta de YouTube)")
        for _b in (self.play_btn, self.seek, self.volume, self.like_btn,
                   self.download_btn, self.float_btn, self.fullscreen_btn):
            _b.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_user_dragging", True))
        self.seek.sliderReleased.connect(self._on_seek_released)
        self.volume.valueChanged.connect(self._on_volume)
        self.like_btn.clicked.connect(self._toggle_like)
        self.download_btn.clicked.connect(self._download_current)
        self.float_btn.clicked.connect(self._toggle_floating_video)
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen_video)

        # --- scrollable content: title, description, comments ---
        self.watch_scroll = QScrollArea()
        self.watch_scroll.setObjectName("YtScroll")
        self.watch_scroll.setWidgetResizable(True)
        self.watch_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.watch_scroll.viewport().setStyleSheet("background: transparent;")
        watch = QWidget()
        watch.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(watch)
        wl.setContentsMargins(2, 6, 8, 4)
        wl.setSpacing(12)

        self.title_lbl = QLabel("—")
        self.title_lbl.setObjectName("YtTitle")
        self.title_lbl.setWordWrap(True)
        wl.addWidget(self.title_lbl)
        self.channel_lbl = QLabel("")
        self.channel_lbl.setObjectName("YtChannel")
        wl.addWidget(self.channel_lbl)

        # description card
        self.desc_card = QFrame()
        self.desc_card.setObjectName("YtDesc")
        self.desc_card.setStyleSheet(
            "QFrame#YtDesc { background: rgba(255,255,255,0.04);"
            " border: 1px solid rgba(255,255,255,0.07); border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        dcl = QVBoxLayout(self.desc_card)
        dcl.setContentsMargins(14, 12, 14, 12)
        dcl.setSpacing(6)
        self.desc_meta = QLabel("")
        self.desc_meta.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: 700;")
        dcl.addWidget(self.desc_meta)
        self.desc_text = QLabel("")
        self.desc_text.setWordWrap(True)
        self.desc_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc_text.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        dcl.addWidget(self.desc_text)
        self.desc_toggle = QPushButton("Mostrar más")
        self.desc_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desc_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.PRI}; border: none;"
            " padding: 2px 0; font-size: 11px; font-weight: 800; text-align: left; }"
        )
        self.desc_toggle.clicked.connect(self._toggle_description)
        self.desc_toggle.setVisible(False)
        dcl.addWidget(self.desc_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        wl.addWidget(self.desc_card)

        # comments
        self.comments_header = QLabel("Comentarios")
        self.comments_header.setStyleSheet(f"color: {C.TEXT}; font-size: 14px; font-weight: 800;")
        wl.addWidget(self.comments_header)
        self.comments_host = QWidget()
        self.comments_host.setStyleSheet("background: transparent;")
        self.comments_layout = QVBoxLayout(self.comments_host)
        self.comments_layout.setContentsMargins(0, 0, 0, 0)
        self.comments_layout.setSpacing(14)
        self.comments_layout.addStretch()
        wl.addWidget(self.comments_host)
        wl.addStretch()

        self.watch_scroll.setWidget(watch)
        pp.addWidget(self.watch_scroll, stretch=1)

        self.stack.addWidget(player_page)

    # ------------------------------------------------------------- feeds/grid
    def _make_tab(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, k=key: self._select_feed(k))
        btn.setStyleSheet(self._tab_style())
        return btn

    @staticmethod
    def _tab_style() -> str:
        return f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.04);
                color: {C.TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 16px;
                padding: 7px 16px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(182, 196, 255, 0.12); color: {C.TEXT}; }}
            QPushButton:checked {{
                background: rgba(94, 130, 255, 0.18);
                color: #E8EBFF;
                border-color: rgba(182, 196, 255, 0.40);
            }}
        """

    def _load_initial_feed(self):
        try:
            from actions.youtube_player import is_authenticated
            authed = is_authenticated()
        except Exception:
            authed = False
        self._select_feed("recommended" if authed else "trending")

    def _select_feed(self, key: str):
        self._feed = key
        self.tab_recommended.setChecked(key == "recommended")
        self.tab_foryou.setChecked(key == "home")
        self.tab_trending.setChecked(key == "trending")
        self.stack.setCurrentIndex(0)
        self.load_feed()

    def load_feed(self):
        self._loaded_feed = True
        feed = self._feed
        self.status.setText("Cargando…")

        def worker():
            results = None
            error = ""
            try:
                if feed == "recommended":
                    from actions.youtube_player import fetch_home_recommendations
                    results = fetch_home_recommendations(limit=24)
                elif feed == "home":
                    from actions.youtube_player import fetch_subscriptions_feed
                    results = fetch_subscriptions_feed(limit=24)
                    if not results:
                        from actions.youtube_player import fetch_recommended
                        results = fetch_recommended(limit=24)
                else:
                    from actions.youtube_player import fetch_recommended
                    results = fetch_recommended(limit=24)
            except Exception as exc:
                error = str(exc)
            self._results_sig.emit(results, error, feed)

        threading.Thread(target=worker, daemon=True).start()

    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            return
        self.tab_recommended.setChecked(False)
        self.tab_foryou.setChecked(False)
        self.tab_trending.setChecked(False)
        self.stack.setCurrentIndex(0)
        self.status.setText("Buscando…")

        def worker():
            results = None
            error = ""
            try:
                from actions.youtube_player import search_videos
                results = search_videos(query, limit=24)
            except Exception as exc:
                error = str(exc)
            self._results_sig.emit(results, error, "search")

        threading.Thread(target=worker, daemon=True).start()

    def _go_home(self):
        self.nav_back_btn.setVisible(False)
        self.stack.setCurrentIndex(0)
        if not self._loaded_feed:
            self._load_initial_feed()

    def _clear_flow(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

    def _apply_results(self, results, error: str, header: str):
        self.header_lbl.setText(header)
        if results is None:
            self.status.setText(f"Error: {error}" if error else "No se pudo cargar.")
            return
        self._clear_flow()
        self._thumb_targets.clear()
        self._by_id = {v["id"]: v for v in results}
        self._ordered_ids = [v["id"] for v in results]
        if not results:
            self.status.setText("Sin resultados.")
            return
        for video in results:
            card = _VideoCard(video)
            card.activated.connect(self._play_video_by_id)
            self._thumb_targets.setdefault(video["id"], []).append(card.thumb_label())
            self.flow.addWidget(card)
            self._request_thumb(video["id"])
        self.status.setText(f"{len(results)} vídeos")

    # -------------------------------------------------------------- thumbnails
    def _request_thumb(self, vid: str):
        if not vid:
            return
        if vid in self._thumb_cache:
            self._apply_thumb(vid, self._thumb_cache[vid])
            return
        if vid in self._thumb_loading or self._thumb_executor_closed:
            return
        self._thumb_loading.add(vid)

        def worker():
            raw = b""
            try:
                raw = self._thumb_disk_cache.get(vid)
                if not raw:
                    import requests
                    resp = requests.get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", timeout=6)
                    if resp.ok:
                        raw = resp.content
                        if raw:
                            self._thumb_disk_cache.put(vid, raw)
            except Exception:
                raw = b""
            self._thumb_sig.emit(vid, raw)

        try:
            self._thumb_executor.submit(worker)
        except RuntimeError:
            pass

    def _apply_thumb(self, vid: str, raw):
        self._thumb_loading.discard(vid)
        if not raw:
            return
        self._thumb_cache[vid] = bytes(raw)
        base = QPixmap()
        if not base.loadFromData(bytes(raw)):
            return
        for label in list(self._thumb_targets.get(vid, [])):
            try:
                w = label.width() or 248
                h = label.height() or 140
                scaled = base.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
                if scaled.width() > w or scaled.height() > h:
                    x = max(0, (scaled.width() - w) // 2)
                    y = max(0, (scaled.height() - h) // 2)
                    scaled = scaled.copy(x, y, w, h)
                label.setPixmap(scaled)
            except RuntimeError:
                try:
                    self._thumb_targets[vid].remove(label)
                except (KeyError, ValueError):
                    pass

    # ---------------------------------------------------------------- playback
    def _play_video_by_id(self, vid: str):
        video = self._by_id.get(vid) or {"id": vid, "title": vid, "channel": ""}
        self._play_video(video)

    def _play_video(self, video: dict):
        self._current = dict(video)
        self._liked = False
        self.like_btn.set_liked(False)
        for _b in (self.like_btn, self.download_btn, self.float_btn,
                   self.fullscreen_btn, self.play_btn, self.seek, self.volume):
            _b.setEnabled(True)
        self.title_lbl.setText(video.get("title", ""))
        self.channel_lbl.setText(video.get("channel", ""))
        self.placeholder.hide()
        if self._float_overlay is not None:
            self._float_overlay.set_meta(video.get("title", ""), video.get("channel", ""))
        if self._detached_mode is None:
            self.nav_back_btn.setVisible(True)
            self.stack.setCurrentIndex(1)
            QTimer.singleShot(0, self._size_video)

        if self._player is None:
            wid = int(self.video_surface.winId())
            from actions.youtube_player import EmbeddedVideoPlayer
            self._player = EmbeddedVideoPlayer(wid)

        vid = video["id"]
        url = f"https://www.youtube.com/watch?v={vid}"
        player = self._player
        volume = self.volume.value()

        def worker():
            ok = False
            try:
                ok = player.play(url)
                if ok:
                    player.set_volume(volume)
            except Exception:
                ok = False
            self._play_sig.emit(vid, ok)

        threading.Thread(target=worker, daemon=True).start()
        self._start_poller()
        self._load_comments(vid)
        self._load_details(vid)

    def _size_video(self):
        if self._detached_mode is not None:
            return
        box = getattr(self, "video_box", None)
        if box is None:
            return
        avail_w = self.width() - 40
        # controls are overlaid on the video now, so it can be larger; keep room
        # for the title + description/comments below.
        max_h = int(self.height() * 0.74)
        if avail_w <= 0 or max_h <= 0:
            return
        w = avail_w
        h = int(w * 9 / 16)
        if h > max_h:
            h = max_h
            w = int(h * 16 / 9)
        box.setFixedSize(max(240, w), max(135, h))

    def _load_details(self, vid: str):
        self._set_description("", "")
        self.desc_text.setText("Cargando descripción…")

        def worker():
            details = None
            error = ""
            try:
                from actions.youtube_player import fetch_video_details
                details = fetch_video_details(vid)
            except Exception as exc:
                error = str(exc)
            self._details_sig.emit(vid, details, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_details(self, vid: str, details, error: str):
        if vid != self._current.get("id"):
            return
        if not details:
            self._set_description("", "Descripción no disponible")
            return
        if details.get("title"):
            self.title_lbl.setText(details["title"])
            self._current["title"] = details["title"]
        if details.get("channel"):
            self.channel_lbl.setText(details["channel"])
        self._set_description(details.get("description", ""), self._details_meta(details))

    @staticmethod
    def _details_meta(details: dict) -> str:
        parts = []
        views = details.get("views")
        if views:
            try:
                parts.append(f"{int(views):,} vistas")
            except (TypeError, ValueError):
                pass
        published = str(details.get("publishedAt", ""))
        if published:
            parts.append(published[:10])
        return "  ·  ".join(parts)

    def _set_description(self, text: str, meta: str):
        self._desc_full = str(text or "")
        self._desc_expanded = False
        self.desc_meta.setText(meta or "")
        self.desc_meta.setVisible(bool(meta))
        self._apply_desc_collapsed()

    def _apply_desc_collapsed(self):
        full = self._desc_full
        if not full:
            self.desc_text.setText("Sin descripción.")
            self.desc_toggle.setVisible(False)
            return
        limit = 280
        if len(full) <= limit:
            self.desc_text.setText(full)
            self.desc_toggle.setVisible(False)
            return
        if self._desc_expanded:
            self.desc_text.setText(full)
            self.desc_toggle.setText("Mostrar menos")
        else:
            self.desc_text.setText(full[:limit].rstrip() + "…")
            self.desc_toggle.setText("Mostrar más")
        self.desc_toggle.setVisible(True)

    def _toggle_description(self):
        self._desc_expanded = not self._desc_expanded
        self._apply_desc_collapsed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_video()

    def _load_comments(self, vid: str):
        self._clear_comments()
        self.comments_header.setText("Comentarios")
        loading = QLabel("Cargando comentarios…")
        loading.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        self.comments_layout.insertWidget(0, loading)

        def worker():
            comments = None
            error = ""
            try:
                from actions.youtube_player import fetch_comments
                comments = fetch_comments(vid, limit=30)
            except Exception as exc:
                error = str(exc)
            self._comments_sig.emit(vid, comments, error)

        threading.Thread(target=worker, daemon=True).start()

    def _clear_comments(self):
        while self.comments_layout.count():
            item = self.comments_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self.comments_layout.addStretch()

    def _apply_comments(self, vid: str, comments, error: str):
        if vid != self._current.get("id"):
            return
        self._clear_comments()
        if comments is None:
            note = QLabel("Comentarios no disponibles para este vídeo.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
            self.comments_layout.insertWidget(0, note)
            if error:
                self._log(f"ERR: comentarios YouTube — {error[:140]}")
            return
        self.comments_header.setText(f"Comentarios · {len(comments)}")
        if not comments:
            note = QLabel("Sin comentarios.")
            note.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
            self.comments_layout.insertWidget(0, note)
            return
        for index, comment in enumerate(comments):
            self.comments_layout.insertWidget(index, self._make_comment_widget(comment))

    def _make_comment_widget(self, comment: dict) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        author = QLabel(str(comment.get("author", "")))
        author.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: 700;")
        lay.addWidget(author)
        text = QLabel(str(comment.get("text", "")))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        lay.addWidget(text)
        likes = int(comment.get("likes") or 0)
        if likes > 0:
            meta = QLabel(f"♥ {likes:,}")
            meta.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 10px;")
            lay.addWidget(meta)
        return box

    def _apply_play_started(self, vid: str, ok: bool):
        if vid != self._current.get("id"):
            return
        if ok:
            self.play_btn.set_shape(_MediaBtn.PAUSE)
            if self._float_overlay is not None:
                self._float_overlay.set_playing(True)
        else:
            self.placeholder.setText("No se pudo reproducir (¿mpv disponible?)")
            self.placeholder.show()

    def _toggle_play(self):
        if self._player is not None:
            self._player.toggle()

    def pause_playback(self):
        if self._player is not None:
            try:
                self._player.pause()
            except Exception:
                pass

    def _on_volume(self, value: int):
        if self._player is not None:
            self._player.set_volume(value)

    def _on_seek_released(self):
        self._user_dragging = False
        if self._player is None or self._duration <= 0:
            return
        frac = self.seek.value() / 1000.0
        self._player.seek_abs(frac * self._duration)

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
                    paused = player.paused()
                    if pos is not None:
                        playing = (not bool(paused)) if paused is not None else True
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
        try:
            total = int(seconds or 0)
        except Exception:
            return "0:00"
        if total <= 0:
            return "0:00"
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # -------------------------------------------------------------------- like
    def _toggle_like(self):
        vid = self._current.get("id")
        if not vid:
            return
        desired = not self._liked
        self.like_btn.setEnabled(False)

        def worker():
            error = ""
            try:
                from actions.youtube_player import rate_video
                rate_video(vid, "like" if desired else "none")
            except Exception as exc:
                error = str(exc)
            self._like_sig.emit(vid, desired, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_like(self, vid: str, liked: bool, error: str):
        self.like_btn.setEnabled(True)
        if vid != self._current.get("id"):
            return
        if error:
            self._log(f"ERR: YouTube like — {error[:140]}")
            return
        self._liked = liked
        self.like_btn.set_liked(liked)

    # ----------------------------------------------------------------- actions
    def _download_current(self):
        vid = self._current.get("id")
        if not vid:
            return
        url = f"https://www.youtube.com/watch?v={vid}"

        def worker():
            try:
                from actions.youtube_video import download_video
                download_video(url, quality="best", progress_hook=self._progress_hook)
            except Exception as exc:
                self._log(f"ERR: descarga YouTube — {str(exc)[:140]}")

        threading.Thread(target=worker, daemon=True).start()

    def _open_external(self):
        vid = self._current.get("id")
        if vid:
            QDesktopServices.openUrl(QUrl(f"https://www.youtube.com/watch?v={vid}"))

    # ------------------------------------------------------- fullscreen / float
    def is_floating(self) -> bool:
        return self._detached_mode == "floating"

    def _toggle_fullscreen_video(self):
        if self._detached_mode == "fullscreen":
            self._reattach_video()
        else:
            self._detach_video("fullscreen")

    def _toggle_floating_video(self):
        if self._detached_mode == "floating":
            self._reattach_video()
        else:
            self._detach_video("floating")

    def _on_pip_moved(self, p):
        if self._float_window is not None:
            self._float_window.move(p)
            self._save_pip_geometry()

    def _on_pip_resized(self, w, h):
        if self._float_window is not None:
            self._float_window.resize(w, h)
            self._save_pip_geometry()

    def _save_pip_geometry(self):
        if self._detached_mode != "floating" or self._float_window is None:
            return
        if not bool(app_settings.get("youtube_remember_pip", True)):
            return
        win = self._float_window
        app_settings.set("youtube_pip_geometry", {
            "x": win.x(), "y": win.y(), "w": win.width(), "h": win.height(),
        })

    def _detach_video(self, mode: str):
        if self.video_box is None:
            return
        if self._detached_mode is not None:
            self._reattach_video()

        win = _DetachWindow(self._reattach_video)
        win.setWindowTitle("JARVIS — YouTube")
        win.setStyleSheet("background: #000000;")
        wlay = QVBoxLayout(win)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        self._left_col.removeWidget(self.video_box)
        # In a detached window the video should FILL it, so drop the panel's fixed 16:9 size.
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
            "color: rgba(203,213,225,0.5); font-size: 13px;"
            "background: rgba(10,12,26,0.4); border: 1px solid rgba(182,196,255,0.10);"
            "border-radius: 12px;"
        )
        self._left_col.insertWidget(0, hint, stretch=1)
        self._detached_hint = hint
        self._detached_mode = mode

        overlay = _FloatOverlay({
            "toggle": self._toggle_play,
            "prev": self._prev_video,
            "next": self._next_video,
            "rewind": self._rewind_video,
            "forward": self._forward_video,
            "restore": self._reattach_video,
            "moved": self._on_pip_moved,
            "resized": self._on_pip_resized,
        }, draggable=(mode == "floating"), resizable=(mode == "floating"))
        self._float_overlay = overlay
        overlay.set_meta(self._current.get("title", ""), self._current.get("channel", ""))

        if mode == "fullscreen":
            self._fs_window = win
            self.fullscreen_btn.setIcon(_line_icon("fullscreen_exit", C.PRI, 18))
            win.showFullScreen()
            screen = win.screen() or QApplication.primaryScreen()
            overlay.setGeometry(screen.geometry())
            for target in (win, overlay):
                shortcut = QShortcut(QKeySequence("Escape"), target)
                shortcut.activated.connect(self._reattach_video)
        else:
            self._float_window = win
            self.float_btn.setIcon(_line_icon("pip", C.PRI, 18))
            win.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            win.setMinimumSize(320, 180)
            # Restore the last PiP size/position if the user enabled that.
            remember = bool(app_settings.get("youtube_remember_pip", True))
            saved = app_settings.get("youtube_pip_geometry", None) if remember else None
            if isinstance(saved, dict) and all(k in saved for k in ("x", "y", "w", "h")):
                pip_w = max(320, int(saved["w"]))
                pip_h = max(180, int(saved["h"]))
                pos = QPoint(int(saved["x"]), int(saved["y"]))
            else:
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
            self._left_col.removeWidget(hint)
            hint.deleteLater()
            self._detached_hint = None
        # Reparent the video back to the panel BEFORE the window dies (avoids
        # destroying the shared mpv surface, which would crash).
        if win is not None and win.layout() is not None:
            win.layout().removeWidget(self.video_box)
        self._left_col.insertWidget(0, self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()
        if win is not None:
            win.close()
            win.deleteLater()
        self.fullscreen_btn.setIcon(_line_icon("fullscreen", C.TEXT_DIM, 18))
        self.float_btn.setIcon(_line_icon("pip", C.TEXT_DIM, 18))
        QTimer.singleShot(0, self._size_video)

    def _prev_video(self):
        if not self._ordered_ids:
            return
        current = self._current.get("id")
        try:
            idx = self._ordered_ids.index(current)
        except ValueError:
            idx = 0
        prev = self._ordered_ids[(idx - 1) % len(self._ordered_ids)]
        video = self._by_id.get(prev) or {"id": prev, "title": prev, "channel": ""}
        self._play_video(video)

    def _next_video(self):
        if not self._ordered_ids:
            return
        current = self._current.get("id")
        try:
            idx = self._ordered_ids.index(current)
        except ValueError:
            idx = -1
        nxt = self._ordered_ids[(idx + 1) % len(self._ordered_ids)]
        video = self._by_id.get(nxt) or {"id": nxt, "title": nxt, "channel": ""}
        self._play_video(video)

    def _forward_video(self):
        if self._player is not None:
            self._player.seek_rel(10)

    def _rewind_video(self):
        if self._player is not None:
            self._player.seek_rel(-10)

    # ----------------------------------------------------------------- helpers
    def _log(self, text: str):
        win = self.window()
        if hasattr(win, "_log_sig"):
            try:
                win._log_sig.emit(text)
            except Exception:
                pass

    def _shutdown(self):
        self._poll_stop.set()
        pc = getattr(self, "_pc", None)
        if pc is not None:
            try:
                pc._timer.stop()
                pc.close()
            except Exception:
                pass
        for win in (self._fs_window, self._float_window, self._float_overlay):
            if win is not None:
                try:
                    win.close()
                except Exception:
                    pass
        if not self._thumb_executor_closed:
            self._thumb_executor_closed = True
            self._thumb_executor.shutdown(wait=False, cancel_futures=True)
        if self._player is not None:
            self._player.shutdown()
            self._player = None





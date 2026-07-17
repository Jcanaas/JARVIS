"""TV mode — live channels from iptv-org playlists (actions/tv_channels.py),
rendered with the same embedded VLC backend the Movies mode uses.

Two views on a stacked layout: a filterable channel grid (search + country +
category) and a live player. Live streams have no seek, so the player keeps a
minimal control row (play/pause, volume, channel zapping) instead of the full
Movies transport.
"""
from __future__ import annotations

import time as _time

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from actions.perf_helpers import DiskImageCache, SharedThreadPool
from actions.tv_channels import (
    COUNTRIES, DEFAULT_COUNTRY, Channel, channel_groups, fetch_channels,
)
from .movies import HAS_VLC, _AspectVideo, _download_image, _VLCBackend
from .music import FlowLayout

_LOGO_CACHE = DiskImageCache("tv_logos")

# Rendering every channel of a big lineup at once (US ~1000+) freezes the UI
# thread building widgets; the search box narrows past this cap.
_MAX_CARDS = 240


class _TVBackend(_VLCBackend):
    """VLC backend accepting per-media options (EXTVLCOPT user-agent etc.)."""

    def play_url(self, url: str, hwnd: int, volume: int = 90, options=()):
        if not self.player:
            return
        # adaptive-logic=highest: start HLS on the top variant (720p for RTVE)
        # instead of ramping up from the lowest one.
        media = self.instance.media_new(
            url, ":network-caching=1500", ":adaptive-logic=highest", *options)
        self.player.set_media(media)
        self.set_hwnd(hwnd)
        self.player.audio_set_volume(int(volume))
        self.player.play()


class _ChannelCard(QFrame):
    """Clickable channel tile: logo (lazy, shared pool) + name + category."""

    activated = pyqtSignal(object)
    _logo_ready = pyqtSignal(QImage)

    _W, _H = 176, 128
    _LOGO = 56

    def __init__(self, channel: Channel, parent=None):
        super().__init__(parent)
        self._channel = channel
        self.setFixedSize(self._W, self._H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("TvCard")
        self.setStyleSheet(f"""
            QFrame#TvCard {{
                background: {C.GLASS};
                border: 1px solid {C.BORDER_A};
                border-radius: 12px;
            }}
            QFrame#TvCard:hover {{ border-color: {C.PRI}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(6)

        self._logo = QLabel()
        self._logo.setFixedHeight(self._LOGO)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setStyleSheet("background: transparent;")
        self._logo.setPixmap(_line_icon("tv", C.TEXT_MED, 34).pixmap(34, 34))
        lay.addWidget(self._logo)

        name = QLabel(channel.name)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet(f"color: {C.TEXT}; font-size: 11px; font-weight: 700; background: transparent;")
        name.setToolTip(channel.name)
        lay.addWidget(name, 1)

        group = (channel.group or "").split(";")[0].strip()
        meta = QLabel(group)
        meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 9px; background: transparent;")
        lay.addWidget(meta)

        self._logo_ready.connect(self._on_logo)
        if channel.logo:
            SharedThreadPool().submit(self._fetch_logo, channel.logo)

    def _fetch_logo(self, url: str):
        try:
            data = _download_image(url, timeout=10, cache=_LOGO_CACHE)
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                self._logo_ready.emit(img)
        except Exception:
            pass

    def _on_logo(self, img: QImage):
        pm = QPixmap.fromImage(img).scaled(
            self._W - 40, self._LOGO,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._logo.setPixmap(pm)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._channel)


class TVModePanel(QWidget):
    """Live TV: iptv-org lineup grid + embedded VLC live player."""

    _channels_ready = pyqtSignal(str, list)   # (country, [Channel])
    _channels_error = pyqtSignal(str, str)    # (country, message)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = _TVBackend() if HAS_VLC else None
        self._channels: list[Channel] = []
        self._filtered: list[Channel] = []
        self._current: Channel | None = None
        self._loaded_once = False
        self._loading = False

        self._channels_ready.connect(self._on_channels_ready)
        self._channels_error.connect(self._on_channels_error)

        # Polls the stream after tuning: hides the placeholder as soon as a
        # video output exists, or reports failure/audio-only after a grace
        # period (iptv-org streams die and geo-block often).
        self._poller = QTimer(self)
        self._poller.setInterval(500)
        self._poller.timeout.connect(self._poll_stream)
        self._tune_started = 0.0
        self._surface_retries = 0

        self._build_ui()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 20, 26, 18)
        root.setSpacing(12)

        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._stack)

        self._stack.addWidget(self._build_grid_page())
        self._stack.addWidget(self._build_player_page())

    def _build_grid_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar canal…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(38)
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {C.GLASS}; color: {C.TEXT};
                border: 1px solid {C.BORDER_A}; border-radius: 10px;
                padding: 0 12px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {C.PRI}; }}
        """)
        self._search.textChanged.connect(self._apply_filters)
        bar.addWidget(self._search, 1)

        combo_qss = f"""
            QComboBox {{
                background: {C.GLASS}; color: {C.TEXT};
                border: 1px solid {C.BORDER_A}; border-radius: 10px;
                padding: 0 12px; font-size: 12px; min-height: 36px;
            }}
            QComboBox:hover {{ border-color: {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                selection-background-color: {C.PRI_GHO};
            }}
        """
        self._country = QComboBox()
        for code, label in COUNTRIES.items():
            self._country.addItem(label, code)
        self._country.setCurrentIndex(
            max(0, list(COUNTRIES).index(DEFAULT_COUNTRY)))
        self._country.setStyleSheet(combo_qss)
        self._country.currentIndexChanged.connect(
            lambda _i: self._load_channels())
        bar.addWidget(self._country)

        self._group = QComboBox()
        self._group.addItem("Todas las categorías", "")
        self._group.setStyleSheet(combo_qss)
        self._group.currentIndexChanged.connect(lambda _i: self._apply_filters())
        bar.addWidget(self._group)

        refresh = _icon_button("refresh", "Actualizar lista de canales", size=38, icon_size=17)
        refresh.clicked.connect(lambda: self._load_channels(force=True))
        bar.addWidget(refresh)
        lay.addLayout(bar)

        self._grid_status = QLabel("")
        self._grid_status.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px; background: transparent;")
        lay.addWidget(self._grid_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;" + _scrollbar_qss())
        self._grid_host = QWidget()
        self._grid_host.setStyleSheet("background: transparent;")
        self._flow = FlowLayout(self._grid_host, margin=2, hspacing=14, vspacing=14)
        scroll.setWidget(self._grid_host)
        lay.addWidget(scroll, 1)
        return page

    def _build_player_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        back = QPushButton("  Canales")
        back.setIcon(_line_icon("chevron_left", C.TEXT, 16))
        back.setFixedHeight(34)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(f"""
            QPushButton {{
                background: {C.GLASS}; color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px; padding: 0 14px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {C.PRI}; color: {C.PRI}; }}
        """)
        back.clicked.connect(self._back_to_grid)
        head.addWidget(back)

        self._now_playing = QLabel("")
        self._now_playing.setFont(QFont(FONT_UI, 14, QFont.Weight.DemiBold))
        self._now_playing.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        head.addWidget(self._now_playing, 1)

        live = QLabel("EN DIRECTO")
        live.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        live.setStyleSheet(f"""
            color: {C.RED}; background: rgba(255, 94, 130, 0.10);
            border: 1px solid rgba(255, 94, 130, 0.35);
            border-radius: 8px; padding: 4px 10px;
        """)
        head.addWidget(live)
        lay.addLayout(head)

        self.video_surface = QWidget()
        self.video_surface.setObjectName("TvSurface")
        self.video_surface.setStyleSheet("QWidget#TvSurface { background:#000000; border-radius:12px; }")
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surf_lay = QVBoxLayout(self.video_surface)
        surf_lay.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel("Sintonizando…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:rgba(203,213,225,0.45); font-size:13px; background:transparent;")
        surf_lay.addWidget(self._placeholder)

        self.video_box = _AspectVideo(self.video_surface)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_box.setMinimumHeight(260)
        lay.addWidget(self.video_box, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self._prev_btn = _icon_button("chevron_left", "Canal anterior", size=36, icon_size=17)
        self._prev_btn.clicked.connect(lambda: self._zap(-1))
        controls.addWidget(self._prev_btn)

        self._play_btn = _icon_button("pause", "Pausar", size=36, icon_size=17)
        self._play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_btn)

        self._next_btn = _icon_button("chevron_right", "Canal siguiente", size=36, icon_size=17)
        self._next_btn.clicked.connect(lambda: self._zap(1))
        controls.addWidget(self._next_btn)

        controls.addSpacing(8)
        vol_icon = QLabel()
        vol_icon.setPixmap(_line_icon("volume", C.TEXT_MED, 16).pixmap(16, 16))
        vol_icon.setStyleSheet("background: transparent;")
        controls.addWidget(vol_icon)

        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(85)
        self._volume.setFixedWidth(140)
        self._volume.valueChanged.connect(
            lambda v: self._player and self._player.set_volume(v))
        controls.addWidget(self._volume)

        self._player_status = QLabel("")
        self._player_status.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px; background: transparent;")
        controls.addWidget(self._player_status, 1)
        lay.addLayout(controls)
        return page

    # ------------------------------------------------------------ channels --
    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._loaded_once:
            self._loaded_once = True
            self._load_channels()

    def _load_channels(self, force: bool = False):
        if self._loading:
            return
        self._loading = True
        country = self._country.currentData() or DEFAULT_COUNTRY
        self._grid_status.setText("Cargando canales…")

        def work():
            try:
                chans = fetch_channels(country, force=force)
                self._channels_ready.emit(country, chans)
            except Exception as e:
                self._channels_error.emit(country, str(e))

        SharedThreadPool().submit(work)

    def _on_channels_ready(self, country: str, chans: list):
        self._loading = False
        if country != (self._country.currentData() or DEFAULT_COUNTRY):
            return  # user switched country while this fetch ran
        self._channels = chans
        current_group = self._group.currentData() or ""
        self._group.blockSignals(True)
        self._group.clear()
        self._group.addItem("Todas las categorías", "")
        for g in channel_groups(chans):
            self._group.addItem(g, g)
        idx = self._group.findData(current_group)
        if idx > 0:
            self._group.setCurrentIndex(idx)
        self._group.blockSignals(False)
        self._apply_filters()

    def _on_channels_error(self, country: str, msg: str):
        self._loading = False
        self._grid_status.setText(f"No se pudo cargar la lista de canales ({msg})")

    def _apply_filters(self):
        text = self._search.text().strip().lower()
        group = self._group.currentData() or ""
        out = []
        for ch in self._channels:
            if text and text not in ch.name.lower():
                continue
            if group and group not in (ch.group or ""):
                continue
            out.append(ch)
        self._filtered = out
        self._rebuild_grid()

    def _rebuild_grid(self):
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        shown = self._filtered[:_MAX_CARDS]
        for ch in shown:
            card = _ChannelCard(ch, parent=self._grid_host)
            card.activated.connect(self._play_channel)
            self._flow.addWidget(card)
        total = len(self._filtered)
        if not self._channels:
            pass  # keep whatever status the loader set
        elif total == 0:
            self._grid_status.setText("Sin resultados")
        elif total > len(shown):
            self._grid_status.setText(
                f"{total} canales — mostrando {len(shown)}, afina la búsqueda")
        else:
            self._grid_status.setText(f"{total} canales")

    # ------------------------------------------------------------ playback --
    def _play_channel(self, channel: Channel):
        if self._player is None or not self._player.available():
            self._grid_status.setText("VLC no disponible: no se puede reproducir")
            return
        self._current = channel
        self._now_playing.setText(channel.name)
        self._player_status.setText("Conectando…")
        self._placeholder.setText("Sintonizando…")
        self._placeholder.show()
        self._play_btn.setIcon(_line_icon("pause", C.TEXT, 17))
        self._play_btn.setToolTip("Pausar")
        self._stack.setCurrentIndex(1)
        self._poller.stop()
        self._player.stop()
        # Let the layout size the native surface before VLC grabs its HWND —
        # a 0-sized window renders audio only (same trick as Movies).
        self._surface_retries = 0
        QTimer.singleShot(60, self._start_stream)

    def _start_stream(self):
        ch = self._current
        if ch is None or self._stack.currentIndex() != 1:
            return
        # The stacked page may not have finished its first layout pass yet;
        # handing VLC a 0-sized HWND yields audio with a black picture.
        if self.video_surface.width() < 10 and self._surface_retries < 20:
            self._surface_retries += 1
            QTimer.singleShot(60, self._start_stream)
            return
        hwnd = int(self.video_surface.winId())
        self._player.play_url(ch.url, hwnd, self._volume.value(),
                              options=tuple(ch.vlc_opts))
        self._tune_started = _time.monotonic()
        self._poller.start()

    def _has_video_out(self) -> bool:
        try:
            return bool(self._player and self._player.player.has_vout())
        except Exception:
            return False

    def _poll_stream(self):
        if self._stack.currentIndex() != 1 or self._player is None:
            self._poller.stop()
            return
        if self._has_video_out():
            self._placeholder.hide()
            self._player_status.setText("")
            self._poller.stop()
            return
        elapsed = _time.monotonic() - self._tune_started
        if elapsed > 12:
            self._poller.stop()
            if self._player.is_playing():
                self._player_status.setText("El canal emite solo audio")
            else:
                self._player_status.setText(
                    "El canal no responde — prueba otro (los streams de iptv-org van y vienen)")
                self._placeholder.setText("Canal no disponible")

    def _toggle_play(self):
        if not self._player:
            return
        self._player.toggle()
        paused = self._player.paused()
        self._play_btn.setIcon(_line_icon("play" if paused else "pause", C.TEXT, 17))
        self._play_btn.setToolTip("Reanudar" if paused else "Pausar")

    def _zap(self, delta: int):
        if not self._filtered or self._current is None:
            return
        try:
            i = self._filtered.index(self._current)
        except ValueError:
            i = 0
        self._play_channel(self._filtered[(i + delta) % len(self._filtered)])

    def _back_to_grid(self):
        self.stop_playback()
        self._stack.setCurrentIndex(0)

    def stop_playback(self):
        self._poller.stop()
        if self._player:
            self._player.stop()
        self._current = None

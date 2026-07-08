from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *

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





class MoviesModePanel(QWidget):
    """Movie/TV discovery with poster grid and detail view."""

    _results_ready = pyqtSignal(list, str, str)
    _status_sig = pyqtSignal(str)
    _show_detail = pyqtSignal(object)
    _torrents_found = pyqtSignal(list, object)  # (torrents, movie)
    _stream_ready = pyqtSignal(str, object)  # (stream_url, movie)
    _pos_sig = pyqtSignal(float, float, bool)  # position, duration, playing
    _subtitle_ready = pyqtSignal(str, str)  # (srt_path, label)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._current_view = "grid"  # "grid" | "detail" | "player"
        self._detail_movie = None
        self._playing_movie = None

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
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Header
        header_lay = QHBoxLayout()
        self._back_btn = QPushButton("←")
        self._back_btn.setFixedSize(36, 36)
        self._back_btn.clicked.connect(self._on_back)
        self._back_btn.setVisible(False)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(f"background:{C.PANEL2}; border:1px solid {C.BORDER}; border-radius:6px;")
        header_lay.addWidget(self._back_btn)

        self._title = QLabel("Películas y Series")
        self._title.setFont(QFont(FONT_UI, 15, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color:{C.TEXT}; background:transparent;")
        header_lay.addWidget(self._title, stretch=1)
        header_lay.addStretch()
        root.addLayout(header_lay)

        # Search row
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._search = SearchGlowInput("Buscar una película o serie…")
        self._search.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search, stretch=1)
        root.addLayout(search_row)

        # Filter chips
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for label, cb in (
            ("Tendencias", self._load_trending),
            ("Recientes", self._load_recent),
        ):
            chips.addWidget(self._chip(label, cb))
        chips.addStretch(1)
        root.addLayout(chips)

        # Stack: grid view | detail view
        self._stack = QStackedWidget()

        # Grid view
        grid_container = QWidget()
        grid_lay = QVBoxLayout(grid_container)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setStyleSheet(f"background:{C.PANEL}; border:1px solid {C.BORDER_A}; border-radius:11px;")
        scroll.setWidgetResizable(True)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(12)
        scroll.setWidget(self._grid_widget)
        grid_lay.addWidget(scroll)
        self._stack.addWidget(grid_container)

        # Detail view
        detail_container = QWidget()
        detail_lay = QVBoxLayout(detail_container)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_scroll = QScrollArea()
        detail_scroll.setStyleSheet(f"background:{C.PANEL}; border:1px solid {C.BORDER_A}; border-radius:11px;")
        detail_scroll.setWidgetResizable(True)
        self._detail_content = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_content)
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
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(34)
        bg = C.PRI_DIM if primary else "transparent"
        fg = C.DARK if primary else C.TEXT_DIM
        b.setStyleSheet(f"""
            QPushButton {{
                background:{bg}; color:{fg};
                border:1px solid {C.BORDER}; border-radius:8px;
                padding:0 14px; font-size:11px; font-weight:600;
            }}
            QPushButton:hover {{ border-color:{C.PRI_DIM}; color:{C.TEXT}; }}
        """)
        b.clicked.connect(lambda _=False: cb())
        return b

    def _set_status(self, text: str):
        self._status.setText(text)

    def _run_async(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _do_search(self):
        query = self._search.text().strip()
        if not query:
            return
        self._set_status(f"Buscando «{query}»…")

        def work():
            try:
                from actions import movie_search as ms
                items = ms.search(query, limit=12)
                self._results_ready.emit(items, f"Resultados para «{query}»", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_trending(self):
        self._set_status("Cargando tendencias…")

        def work():
            try:
                from actions import movie_search as ms
                items = ms.get_trending(kind="movie", limit=12)
                self._results_ready.emit(items, "Películas en tendencia", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_recent(self):
        self._set_status("Buscando películas recientes…")

        def work():
            try:
                from actions import movie_search as ms
                items = ms.get_trending(kind="movie", window="week", limit=12)
                self._results_ready.emit(items, "Películas de esta semana", "")
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

        # Clear grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Add movie cards (4 columns)
        for i, movie in enumerate(items):
            card = _MovieCard(movie)
            card.clicked.connect(lambda m: self._show_detail.emit(m))
            col = i % 4
            row = i // 4
            self._grid.addWidget(card, row, col)

        self._grid.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding),
                          (len(items) // 4) + 1, 0, 1, 4)

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
                import urllib.request
                data = urllib.request.urlopen(movie.poster_url).read()
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

            kind = getattr(movie, "media_type", "movie")
            found: list = []
            errors: list[str] = []

            # Sources 1 & 2: Stremio addons keyed by IMDb id that aggregate
            # Spanish-only sites (Peerflix: DonTorrent/MejorTorrent/Wolfmax4k,
            # 100% Castilian; Torrentio: MejorTorrent/Cinecalidad + intl).
            try:
                from actions import movie_search as ms
                imdb = ms.get_imdb_id(getattr(movie, "tmdb_id", 0), kind=kind)
            except Exception as exc:
                imdb = ""
                errors.append(f"imdb_id: {exc}")

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

            self._torrents_found.emit(unique, movie)

        self._run_async(work)

    def _show_torrent_select(self, torrents: list, movie):
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

                stream_url = vp.start_streaming(torrent.magnet, movie.title)
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
        """Search + download subtitles from OpenSubtitles on a worker thread."""
        movie = self._playing_movie
        if movie is None:
            return
        lang_name = {"es": "español", "en": "inglés"}.get(language, language)
        self._set_status(f"Buscando subtítulos en {lang_name}…")

        def work():
            try:
                from actions import opensubtitles as osub
                year = int(getattr(movie, "release_year", 0) or 0)
                path = osub.fetch_subtitle(movie.title, language=language, year=year)
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
    """Anime discovery and streaming — same UI as Movies but backed by Nyaa/Torrentio-nyaa."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Relabel after MoviesModePanel.__init__ built the widgets.
        self._title.setText("Anime")
        self._search.setPlaceholderText("Buscar anime…")

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
                from actions import anime_search
                items = anime_search.search_anime(query, limit=12)
                self._results_ready.emit(items, f"Anime: «{query}»", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_trending(self):
        self._set_status("Cargando anime en tendencia…")

        def work():
            try:
                from actions import anime_search
                items = anime_search.get_trending_anime(limit=12)
                self._results_ready.emit(items, "Anime en tendencia", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_recent(self):
        self._set_status("Buscando anime en emisión…")

        def work():
            try:
                from actions import anime_search
                items = anime_search.get_airing_anime(limit=12)
                self._results_ready.emit(items, "Anime en emisión", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _search_and_play(self, anime):
        """Like MoviesModePanel but uses Nyaa via Torrentio + torlink --kind anime.

        Jikan results have tmdb_id=0; in that case we do a title-based TMDB lookup
        to resolve the IMDb id needed by Torrentio.
        """
        self._set_status(f"Buscando torrents de «{anime.title}»…")

        def work():
            import re
            from actions import torrent_search as ts

            kind = getattr(anime, "media_type", "tv")
            found: list = []
            errors: list[str] = []

            # Resolve IMDb id — Jikan results have tmdb_id=0 so fall back to
            # a TMDB title search to bridge Jikan → Torrentio.
            try:
                from actions import movie_search as ms
                tmdb_id = getattr(anime, "tmdb_id", 0)
                if tmdb_id:
                    imdb = ms.get_imdb_id(tmdb_id, kind=kind)
                else:
                    imdb = ms.get_imdb_id_by_title(anime.title, kind="tv")
            except Exception as exc:
                imdb = ""
                errors.append(f"imdb_id: {exc}")

            if imdb:
                self._status_sig.emit(
                    f"Buscando «{anime.title}» en Nyaa via Torrentio ({imdb})…"
                )
                try:
                    from actions import torrentio
                    results = torrentio.search_anime(imdb, kind=kind, limit=15)
                    for s in results:
                        found.append(ts.Torrent(
                            title=s.title, magnet=s.magnet, seeders=s.seeders,
                            leechers=0, size=s.size, spanish=s.spanish,
                            provider=s.provider or "Nyaa/Torrentio"))
                except Exception as exc:
                    errors.append(f"torrentio-nyaa: {exc}")
            else:
                errors.append("sin IMDb id → Torrentio-Nyaa omitido")

            # Source 2: torlink --kind anime (Nyaa RSS + 1337x Anime category).
            try:
                self._status_sig.emit(f"Buscando «{anime.title}» en Nyaa/1337x…")
                found.extend(ts.search(anime.title, kind="anime", limit=10))
            except Exception as exc:
                errors.append(f"torlink-anime: {exc}")

            if not found:
                diag = "  |  ".join(errors) if errors else ""
                self._status_sig.emit(
                    f"No encontré torrents para «{anime.title}»"
                    + (f"  [{diag}]" if diag else "")
                )
                return

            # De-duplicate by infohash, sort by seeders.
            seen, unique = set(), []
            for t in found:
                m = re.search(r"btih:([a-zA-Z0-9]+)", t.magnet or "")
                key = m.group(1).lower() if m else (t.magnet or t.title)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(t)
            unique.sort(key=lambda t: t.seeders, reverse=True)

            self._torrents_found.emit(unique, anime)

        self._run_async(work)




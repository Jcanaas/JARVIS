"""Games mode — Steam metadata (labeled "SteamDB" in the UI, see
docs/plan-games.md §1.6) + FitGirl Repacks downloads, reusing MoviesModePanel's
grid UI. Games have no playback path: the detail view exposes a single
"Descargar" action instead of "Reproducir", and the hero banner's play button
is rewired to the same download flow. The detail view itself is a bespoke
Steam-store-style layout (media viewer + thumbnail strip + trailers on the
left, reviews/tags/developer sidebar on the right) rather than the
Movies/Anime poster-and-synopsis layout.
"""
from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from actions.perf_helpers import SharedThreadPool
from .movies import MoviesModePanel, _download_image


class _AspectBox(QWidget):
    """Fluid-width container that keeps its height locked to a fixed aspect
    ratio (default 16:9), filling whatever width its parent layout gives it.

    Used for the detail-view media viewer and the sidebar capsule so they
    scale with the window instead of being pinned to a fixed pixel size.
    """

    def __init__(self, ratio: float = 16 / 9, parent=None):
        super().__init__(parent)
        self._ratio = ratio
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

    def resizeEvent(self, ev):
        # PyQt6's QVBoxLayout doesn't reliably call heightForWidth() during
        # its own setGeometry pass, so drive this the other way: whenever the
        # parent layout changes our width, immediately re-request a fixed
        # height for it via setFixedHeight (which calls updateGeometry(), not
        # a raw resize() — raw resize() here would race the parent layout's
        # own geometry pass and was the cause of an overlap bug where the
        # thumbnail strip below this box got positioned before the box's
        # real height was settled).
        target_h = round(self.width() / self._ratio)
        if self.maximumHeight() != target_h:
            self.setFixedHeight(target_h)
        super().resizeEvent(ev)

    def add_layer(self, widget: QWidget):
        self._stack.addWidget(widget)

    def show_layer(self, widget: QWidget):
        self._stack.setCurrentWidget(widget)


class _MediaThumb(QLabel):
    """Small clickable thumbnail (screenshot or trailer) in the media strip.

    Loads its own image lazily on a shared worker thread pool, mirroring
    _EpisodeRow's thumbnail pattern (QImage decoded off-thread, QPixmap
    applied on the UI thread — QPixmap isn't thread-safe to construct).
    """

    activated = pyqtSignal(str, str, bool)  # (thumb_url_for_display, target_url, is_trailer)
    _image_ready = pyqtSignal(QImage)

    _W, _H = 120, 67

    def __init__(self, thumb_url: str, target_url: str, is_trailer: bool = False, parent=None):
        super().__init__(parent)
        self._target_url = target_url
        self._is_trailer = is_trailer
        self.setFixedSize(self._W, self._H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background:{C.PANEL2}; border-radius:6px;")
        self._selected = False
        self._image_ready.connect(self._on_image_ready)
        if thumb_url:
            SharedThreadPool().submit(self._fetch, thumb_url)

    def _fetch(self, url: str):
        try:
            data = _download_image(url, timeout=10)
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                self._image_ready.emit(img)
        except Exception:
            pass

    def _on_image_ready(self, img: QImage):
        pm = QPixmap.fromImage(img).scaled(
            self._W, self._H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pm)
        if self._is_trailer:
            self._draw_play_badge()
        if self._selected:
            # The main viewer may have tried to show this thumbnail's frame
            # before the async download finished (empty at the time); now
            # that it's here, re-activate so the main viewer picks it up.
            self.activated.emit("", self._target_url, self._is_trailer)

    def _draw_play_badge(self):
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return
        pm = QPixmap(pm)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(0, 0, 0, 160))
        p.setPen(Qt.PenStyle.NoPen)
        cx, cy, r = pm.width() // 2, pm.height() // 2, 16
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setBrush(QColor("#ffffff"))
        tri = QPolygonF([QPointF(cx - 5, cy - 8), QPointF(cx - 5, cy + 8), QPointF(cx + 8, cy)])
        p.drawPolygon(tri)
        p.end()
        self.setPixmap(pm)

    def set_selected(self, selected: bool):
        self._selected = selected
        border = C.PRI if selected else "transparent"
        self.setStyleSheet(f"background:{C.PANEL2}; border-radius:6px; border:2px solid {border};")

    def mousePressEvent(self, ev):
        self.activated.emit("", self._target_url, self._is_trailer)


class GamesModePanel(MoviesModePanel):
    """Game discovery (Steam storefront) + repack download (FitGirl via torlink)."""

    _repacks_ready = pyqtSignal(list, object)     # (repacks, game)
    _game_details_ready = pyqtSignal(object, int)  # (full Game, request_id)
    _main_image_ready = pyqtSignal(int, QImage)    # (request_id, image)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title.setText("Juegos")
        self._search.setPlaceholderText("Buscar juegos…")
        self._detail_request_id = 0
        self._media_thumbs: list[_MediaThumb] = []

        # Games have no reproduction path: the hero's play button starts a
        # repack download instead of streaming.
        self._hero.play_clicked.disconnect(self._search_and_play)
        self._hero.play_clicked.connect(self._search_and_download)

        self._repacks_ready.connect(self._show_repack_select)
        self._game_details_ready.connect(self._render_game_detail)
        self._main_image_ready.connect(self._on_main_image_ready)

    # ------------------------------------------------------------------
    # Genre pill row
    # ------------------------------------------------------------------

    def _chip_defs(self) -> list:
        return [
            ("Más vendidos", self._load_trending),
            ("Novedades", self._load_recent),
        ]

    # ------------------------------------------------------------------
    # Search / discovery (Steam storefront instead of Cinemeta)
    # ------------------------------------------------------------------

    def _do_search(self):
        query = self._search.text().strip()
        if not query:
            return
        self._set_status(f"Buscando «{query}»…")

        def work():
            try:
                from actions import steam_catalog as sc
                items = sc.search(query, limit=18)
                self._results_ready.emit(items, f"Resultados para «{query}»", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_trending(self):
        self._set_status("Cargando más vendidos…")

        def work():
            try:
                from actions import steam_catalog as sc
                items = sc.get_trending(limit=18)
                self._results_ready.emit(items, "Más vendidos en Steam", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    def _load_recent(self):
        self._set_status("Cargando novedades…")

        def work():
            try:
                from actions import steam_catalog as sc
                items = sc.get_new_releases(limit=18)
                self._results_ready.emit(items, "Novedades en Steam", "")
            except Exception as e:
                self._results_ready.emit([], "", str(e))

        self._run_async(work)

    # ------------------------------------------------------------------
    # Detail view — Steam-store style: media viewer + thumbnail strip on
    # the left, reviews/tags/developer sidebar on the right. Grid cards only
    # carry title/poster, so this fetches full details (screenshots,
    # trailers, reviews, tags) on open and renders a loading skeleton first.
    # ------------------------------------------------------------------

    def _show_grid_view(self):
        self._stop_trailer()
        super()._show_grid_view()

    def _show_movie_detail(self, game):
        self._stop_trailer()
        self._current_view = "detail"
        self._detail_movie = game
        self._back_btn.setVisible(True)
        self._title.setText(game.title)
        self._media_thumbs = []
        self._detail_request_id += 1
        request_id = self._detail_request_id

        self._clear_layout(self._detail_layout)
        loading = QLabel("Cargando ficha…")
        loading.setFont(QFont(FONT_UI, 11))
        loading.setStyleSheet(f"color:{C.TEXT_DIM}; padding:24px 4px;")
        self._detail_layout.addWidget(loading)
        self._stack.setCurrentIndex(1)

        def work():
            try:
                from actions import steam_catalog as sc
                full = sc.get_details(game.appid) or game
            except Exception:
                full = game
            self._game_details_ready.emit(full, request_id)

        self._run_async(work)

    def _render_game_detail(self, game, request_id: int):
        if request_id != self._detail_request_id:
            return  # user navigated to a different game before this arrived
        self._detail_movie = game
        self._clear_layout(self._detail_layout)

        root = QVBoxLayout()
        root.setSpacing(16)

        columns = QHBoxLayout()
        columns.setSpacing(20)

        # -- Left: media viewer + thumbnail strip -----------------------
        left = QVBoxLayout()
        left.setSpacing(10)

        self._media_box = _AspectBox(16 / 9)

        self._main_image = QLabel()
        self._main_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_image.setStyleSheet(f"background:{C.DARK}; border-radius:10px;")
        self._main_image.setScaledContents(True)

        self._trailer_surface = QWidget()
        self._trailer_surface.setStyleSheet(f"background:{C.DARK}; border-radius:10px;")
        self._trailer_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)

        self._media_box.add_layer(self._main_image)
        self._media_box.add_layer(self._trailer_surface)
        self._main_trailer_url = ""
        self._apply_media_cap()
        left.addWidget(self._media_box)

        thumbs_scroll = QScrollArea()
        thumbs_scroll.setFixedHeight(79)
        # Ignored (not Expanding): a horizontally-scrollable strip of ~16
        # thumbnails has a huge natural sizeHint (~2000px) that would otherwise
        # force every ancestor layout wider than the viewport. Ignored tells
        # the layout to size this from available space, not from content.
        thumbs_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        thumbs_scroll.setWidgetResizable(True)
        thumbs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        thumbs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        thumbs_scroll.setStyleSheet("background:transparent; border:none;")
        thumbs_widget = QWidget()
        thumbs_widget.setStyleSheet("background:transparent;")
        thumbs_lay = QHBoxLayout(thumbs_widget)
        thumbs_lay.setContentsMargins(2, 2, 2, 2)
        thumbs_lay.setSpacing(8)

        media_items = [
            (t.thumbnail_url, t.video_url, True) for t in game.trailers
        ] + [
            (s.thumbnail_url, s.full_url, False) for s in game.screenshots
        ]
        for thumb_url, target_url, is_trailer in media_items[:16]:
            thumb = _MediaThumb(thumb_url, target_url, is_trailer)
            thumb.activated.connect(self._on_thumb_activated)
            self._media_thumbs.append(thumb)
            thumbs_lay.addWidget(thumb)
        thumbs_lay.addStretch(1)
        thumbs_scroll.setWidget(thumbs_widget)
        left.addWidget(thumbs_scroll)
        left.addStretch(1)
        columns.addLayout(left, 2)

        # Show the first item (trailer if any, else first screenshot) immediately.
        if media_items:
            first_thumb, first_target, first_is_trailer = media_items[0]
            self._on_thumb_activated(first_thumb, first_target, first_is_trailer)
            if self._media_thumbs:
                self._media_thumbs[0].set_selected(True)
        else:
            self._main_image.setText("🎮")
            self._main_image.setFont(QFont(FONT_UI, 40))

        # -- Right: sidebar (capsule, description, reviews, tags) -------
        sidebar = QVBoxLayout()
        sidebar.setSpacing(12)

        capsule_box = _AspectBox(16 / 9)
        capsule = QLabel()
        capsule.setStyleSheet(f"background:{C.PANEL2}; border-radius:8px;")
        capsule.setScaledContents(True)
        capsule_box.add_layer(capsule)
        if game.header_url:
            try:
                data = _download_image(game.header_url)
                pm = QPixmap()
                pm.loadFromData(data)
                if not pm.isNull():
                    capsule.setPixmap(pm)
            except Exception:
                pass
        sidebar.addWidget(capsule_box)

        if game.overview:
            overview = QLabel(game.overview)
            overview.setWordWrap(True)
            overview.setFont(QFont(FONT_UI, 10))
            overview.setStyleSheet(f"color:{C.TEXT_MED};")
            sidebar.addWidget(overview)

        if game.review_summary:
            pct = game.review_positive_pct
            color = "#66C0F4" if pct >= 70 else ("#B9A074" if pct >= 40 else "#A34C25")
            review_lbl = QLabel(
                f"Reseñas: <span style='color:{color}; font-weight:bold;'>{game.review_summary}</span>"
                f"  ({game.review_total:,})".replace(",", ".")
            )
            review_lbl.setTextFormat(Qt.TextFormat.RichText)
            review_lbl.setFont(QFont(FONT_UI, 10))
            review_lbl.setStyleSheet(f"color:{C.TEXT_MED};")
            review_lbl.setWordWrap(True)
            sidebar.addWidget(review_lbl)

        for label, value in (
            ("Fecha de lanzamiento", game.release_date_str or (str(game.release_year) if game.release_year else "")),
            ("Desarrollador", ", ".join(game.developers)),
            ("Editor", ", ".join(game.publishers)),
        ):
            if not value:
                continue
            row = QLabel(f"<b>{label}:</b> {value}")
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setFont(QFont(FONT_UI, 10))
            row.setStyleSheet(f"color:{C.TEXT_MED};")
            row.setWordWrap(True)
            sidebar.addWidget(row)

        if game.rating:
            meta_lbl = QLabel(f"<b>Metacritic:</b> {game.rating*10:.0f}/100")
            meta_lbl.setTextFormat(Qt.TextFormat.RichText)
            meta_lbl.setFont(QFont(FONT_UI, 10))
            meta_lbl.setStyleSheet(f"color:{C.TEXT_MED};")
            sidebar.addWidget(meta_lbl)

        tags = (game.genres or []) + (game.tags or [])
        if tags:
            tags_title = QLabel("Etiquetas")
            tags_title.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
            tags_title.setStyleSheet(f"color:{C.TEXT};")
            sidebar.addWidget(tags_title)

            # A QHBoxLayout of pill widgets has no wrapping — with a long tag
            # list its sizeHint (sum of every pill's width, all on one line)
            # forced the whole sidebar column wider than the viewport. A
            # single rich-text QLabel wraps naturally within its own width,
            # like the overview label above it, sidestepping that entirely.
            pill_style = (
                f"background:{C.PANEL2}; color:{C.TEXT_MED}; border-radius:10px; "
                "padding:4px 10px; margin:0 4px 4px 0;"
            )
            tags_html = "".join(
                f'<span style="{pill_style}">{tag}</span>' for tag in tags[:10]
            )
            tags_lbl = QLabel(tags_html)
            tags_lbl.setTextFormat(Qt.TextFormat.RichText)
            tags_lbl.setWordWrap(True)
            tags_lbl.setFont(QFont(FONT_UI, 8))
            sidebar.addWidget(tags_lbl)

        sidebar.addStretch(1)
        columns.addLayout(sidebar, 1)

        root.addLayout(columns)

        download_btn = QPushButton("⬇ Descargar")
        download_btn.setFixedHeight(42)
        download_btn.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background:{C.PRI_DIM}; color:{C.DARK};
                border:none; border-radius:8px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{C.PRI}; }}
        """)
        download_btn.clicked.connect(lambda: self._search_and_download(game))
        root.addWidget(download_btn)

        detail_widget = QWidget()
        detail_widget.setLayout(root)
        self._detail_layout.addWidget(detail_widget)

    def _apply_media_cap(self):
        """Bound the detail media viewer so its 16:9 height never outgrows the
        visible panel: the box is width-driven (_AspectBox), so on wide windows
        the trailer/screenshot could get taller than the viewport and the
        player didn't adapt to the available space. Capping max width caps the
        derived height; the box still shrinks fluidly on narrow windows."""
        box = getattr(self, "_media_box", None)
        if box is None:
            return
        try:
            max_h = max(220, int(self.height() * 0.52))
            box.setMaximumWidth(max(320, int(max_h * 16 / 9)))
        except RuntimeError:
            self._media_box = None  # widget already deleted by a re-render

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_media_cap()

    def _on_thumb_activated(self, thumb_url: str, target_url: str, is_trailer: bool):
        for t in self._media_thumbs:
            t.set_selected(t._target_url == target_url)
        if is_trailer:
            self._main_trailer_url = target_url
            if self._player and self._player.available():
                self._media_box.show_layer(self._trailer_surface)
                self._play_trailer_muted_loop(target_url)
            else:
                # No VLC: fall back to the thumbnail's own decoded frame plus
                # a click-to-open-in-browser affordance.
                self._stop_trailer()
                self._media_box.show_layer(self._main_image)
                for t in self._media_thumbs:
                    if t._target_url == target_url and t.pixmap():
                        self._main_image.setPixmap(t.pixmap())
                        break
        else:
            self._main_trailer_url = ""
            self._stop_trailer()
            self._media_box.show_layer(self._main_image)
            self._set_main_image(target_url)

    def _play_trailer_muted_loop(self, url: str):
        """Autoplay a trailer muted and looping in the embedded surface.

        Reuses the inherited VLC backend (self._player): Games never shows
        the shared "player" stack page (no reproduction path), so that
        instance is otherwise idle here. Looping is done via a libVLC media
        option rather than Python-side event handling.
        """
        player = self._player
        if not player or not player.available():
            return
        try:
            media = player.instance.media_new(url, "input-repeat=65535")
            player.player.set_media(media)
            player.set_hwnd(int(self._trailer_surface.winId()))
            player.player.audio_set_volume(0)
            player.player.play()
        except Exception:
            pass

    def _stop_trailer(self):
        if self._player and self._player.available():
            self._player.stop()

    def _open_current_trailer(self):
        if self._main_trailer_url:
            QDesktopServices.openUrl(QUrl(self._main_trailer_url))

    def _set_main_image(self, url: str):
        req = self._detail_request_id

        def work():
            try:
                data = _download_image(url, timeout=12)
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    self._main_image_ready.emit(req, img)
            except Exception:
                pass

        SharedThreadPool().submit(work)

    def _on_main_image_ready(self, request_id: int, img: QImage):
        if request_id != self._detail_request_id:
            return
        self._main_image.setPixmap(QPixmap.fromImage(img))

    # ------------------------------------------------------------------
    # Repack search + download (replaces the streaming flow entirely)
    # ------------------------------------------------------------------

    def _search_and_download(self, game):
        self._set_status(f"Buscando repack de «{game.title}»…")

        def work():
            try:
                from actions import game_search as gs
                repacks = gs.search(game.title, limit=10)
                self._repacks_ready.emit(repacks, game)
            except Exception as exc:
                self._status_sig.emit(f"No encontré repack para «{game.title}»: {exc}")

        self._run_async(work)

    def _show_repack_select(self, repacks: list, game):
        # No torrent picker for games: FitGirl is the single source and there's
        # no "Spanish vs. original" choice to make (a repack is a repack), so
        # auto-pick the best match (the list is already ranked newest-first) and
        # hand it straight to the persistent download manager. Progress is shown
        # and controlled in the right-panel downloads list, not here.
        if not repacks:
            self._set_status(f"No encontré repack para «{game.title}»")
            return
        repack = repacks[0]
        try:
            from actions import torrent_download as td
            from actions.download_manager import get_manager
            dest = td.default_downloads_dir("Games")
            get_manager().add_download(
                title=game.title, magnet=repack.magnet, dest_dir=dest,
                file_index=-1, kind="game", poster_url=game.poster_url,
            )
            self._set_status(f"⬇ Descarga añadida: «{game.title}» — ver panel de descargas")
        except Exception as exc:
            self._set_status(f"Error iniciando descarga: {exc}")

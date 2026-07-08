from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *

class MusicModePanelV2(QWidget):
    _thumb_sig = pyqtSignal(object, object)
    _result_sig = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._table_kind = "playlists"
        self._current_playlist: dict | None = None
        self._search_results: dict[str, list[dict]] = {"songs": [], "playlists": [], "artists": []}
        self._thumb_cache: dict[str, bytes] = {}
        self._thumb_loading: set[str] = set()
        self._thumb_rows: dict[str, set[int]] = {}
        self._thumb_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="music-art")
        self._thumb_executor_closed = False
        self._header_data: dict = {}
        self._now_playing_data: dict = {}
        self._now_playing_key: tuple[str, str] = ("", "")
        self._detail_request = 0
        self._details_loading_key: tuple[str, str, str] = ("", "", "")
        self._detail_render_fingerprint: tuple = ()
        self._detail_render_track_key: tuple[str, str] = ("", "")
        self._details_visible_once = False
        self._artist_page_data: dict = {}
        self._artist_image_targets: dict[str, list[tuple[object, int]]] = {}
        self._artist_page_open = False
        self._table_revision = 0
        self._playing_mark_revision = -1
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_thumb_executor)

        self.setStyleSheet(self._panel_style())
        self._thumb_sig.connect(self._apply_thumb)
        self._result_sig.connect(self._handle_result)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.query_input = SearchGlowInput("Buscar playlists, canciones o artistas")
        self.query_input.returnPressed.connect(self.search)
        search_row.addWidget(self.query_input, stretch=1)
        root.addLayout(search_row)

        # Persistent section tabs (Recomendaciones / Playlists). Hidden while a
        # search is active or the artist page is open.
        self.section_row = QWidget()
        section_layout = QHBoxLayout(self.section_row)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)
        self._section_buttons: dict[str, QPushButton] = {}
        for key, label in (("recommendations", "Recomendaciones"), ("playlists", "Playlists")):
            btn = QPushButton(label)
            btn.setObjectName("MusicFilterButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self._show_section(k))
            self._section_buttons[key] = btn
            section_layout.addWidget(btn)
        section_layout.addStretch()
        self._section_buttons["playlists"].setChecked(True)
        root.addWidget(self.section_row)

        self.filter_row = QWidget()
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        self._filter_buttons: dict[str, QPushButton] = {}
        for key, label in (("songs", "Canciones"), ("playlists", "Playlists"), ("artists", "Artistas")):
            btn = QPushButton(label)
            btn.setObjectName("MusicFilterButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self._show_search_filter(k))
            self._filter_buttons[key] = btn
            filter_layout.addWidget(btn)
        filter_layout.addStretch()
        self.filter_row.setVisible(False)
        root.addWidget(self.filter_row)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        body.addWidget(left, stretch=7)

        self.music_content_stack = AnimatedStack()
        left_layout.addWidget(self.music_content_stack)
        self.browse_page = QWidget()
        browse_layout = QVBoxLayout(self.browse_page)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.setSpacing(0)
        self.music_content_stack.addWidget(self.browse_page)

        self.header = QFrame()
        self.header.setObjectName("MusicHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(16)
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(128, 128)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setObjectName("MusicCover")
        header_layout.addWidget(self.cover_label)

        header_text = QVBoxLayout()
        header_text.setSpacing(8)
        header_text.addStretch()
        self.type_label = QLabel("Playlists")
        self.type_label.setObjectName("MusicType")
        self.title_label = QLabel("Playlists")
        self.title_label.setObjectName("MusicTitle")
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("Tu biblioteca de YouTube Music")
        self.meta_label.setObjectName("MusicMeta")
        self.meta_label.setWordWrap(True)
        header_text.addWidget(self.type_label)
        header_text.addWidget(self.title_label)
        header_text.addWidget(self.meta_label)
        header_actions = QHBoxLayout()
        header_actions.setSpacing(8)
        self.shuffle_btn = QPushButton("Aleatorio")
        self.shuffle_btn.setObjectName("MusicHeaderAction")
        self.shuffle_btn.setIcon(_line_icon("shuffle", "#DCE1FF", 17))
        self.shuffle_btn.setIconSize(QSize(17, 17))
        self.shuffle_btn.setToolTip("Reproducir esta playlist en orden aleatorio")
        self.shuffle_btn.clicked.connect(self._play_current_playlist_shuffled)
        self.shuffle_btn.setVisible(False)
        header_actions.addWidget(self.shuffle_btn)
        header_actions.addStretch()

        header_text.addLayout(header_actions)
        header_text.addStretch()
        header_layout.addLayout(header_text, stretch=1)

        # "⋯" settings button — top-right corner of the header banner
        corner_col = QVBoxLayout()
        corner_col.setContentsMargins(0, 0, 0, 0)
        corner_col.setSpacing(0)
        self._hdr_menu_btn = QPushButton("⋯")
        self._hdr_menu_btn.setObjectName("MusicHeaderMenuBtn")
        self._hdr_menu_btn.setToolTip("Opciones")
        self._hdr_menu_btn.setFixedSize(32, 32)
        self._hdr_menu_btn.clicked.connect(self._show_header_menu)
        corner_col.addWidget(self._hdr_menu_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        corner_col.addStretch()
        header_layout.addLayout(corner_col)

        # Crossfade state (no longer a visible widget — controlled via ⋯ menu
        # and the Ajustes screen; persisted in app_settings).
        self._cf_enabled: bool = bool(app_settings.get("crossfade_enabled", False))
        self._cf_secs: int = int(app_settings.get("crossfade_seconds", 3))

        browse_layout.addWidget(self.header)

        self.table = QTableWidget()
        self.table.setObjectName("MusicTable")
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setIconSize(QSize(44, 44))
        self.table.cellClicked.connect(self._select_row)
        self.table.cellDoubleClicked.connect(self._activate_row)
        self.table.cellActivated.connect(self._activate_row)
        self.table.verticalScrollBar().valueChanged.connect(self._prefetch_visible_thumbnails)
        browse_layout.addWidget(self.table, stretch=1)

        self.status = QLabel("Listo")
        self.status.setObjectName("MusicStatus")
        self.status.setVisible(False)
        browse_layout.addWidget(self.status)

        self.artist_page = self._build_artist_page()
        self.music_content_stack.addWidget(self.artist_page)

        self.details_panel = QFrame()
        self.details_panel.setObjectName("NowPlayingPanel")
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(14, 14, 14, 14)
        details_layout.setSpacing(10)
        self.details_heading = QLabel("REPRODUCIENDO")
        self.details_heading.setObjectName("NowPlayingHeading")
        details_layout.addWidget(self.details_heading)
        self.detail_cover = QLabel()
        self.detail_cover.setObjectName("NowPlayingCover")
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setMinimumHeight(260)
        self.detail_cover.setMaximumHeight(360)
        self.detail_cover.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.details_scroll = QScrollArea()
        self.details_scroll.setObjectName("NowPlayingScroll")
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_content = QWidget()
        self.details_content.setObjectName("NowPlayingContent")
        details_content_layout = QVBoxLayout(self.details_content)
        details_content_layout.setContentsMargins(0, 0, 0, 0)
        details_content_layout.setSpacing(12)
        details_content_layout.addWidget(self.detail_cover)
        self.details = QLabel()
        self.details.setOpenExternalLinks(False)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.details.setObjectName("NowPlayingDetails")
        self.details.linkActivated.connect(self._on_details_link)
        details_content_layout.addWidget(self.details)
        details_content_layout.addStretch()
        self.details_scroll.setWidget(self.details_content)
        details_layout.addWidget(self.details_scroll, stretch=1)
        self.details_panel.setMinimumWidth(310)
        self.details_panel.setVisible(False)
        body.addWidget(self.details_panel, stretch=3)

        self._set_header("Playlists", "Tu biblioteca de YouTube Music", "Playlists", {})
        QTimer.singleShot(500, lambda: self._send_playback("warmup", {}))
        QTimer.singleShot(200, self.load_playlists)

    def _shutdown_thumb_executor(self):
        if self._thumb_executor_closed:
            return
        self._thumb_executor_closed = True
        self._thumb_executor.shutdown(wait=False, cancel_futures=True)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QLineEdit#MusicSearch {{
                min-height: 42px;
                background: rgba(10, 12, 26, 0.88);
                color: {C.TEXT};
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 10px;
                padding: 0 14px;
                font-size: 13px;
                selection-background-color: {C.PRI};
                selection-color: #090c20;
            }}
            QLineEdit#MusicSearch:focus {{
                background: rgba(14, 15, 18, 0.94);
                border-color: rgba(182, 196, 255, 0.58);
            }}
            QPushButton#MusicSearchButton {{
                min-height: 40px;
                background: rgba(94, 130, 255, 0.16);
                color: #DCE1FF;
                border: 1px solid rgba(182, 196, 255, 0.28);
                border-radius: 10px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton#MusicSearchButton:hover {{
                background: rgba(94, 130, 255, 0.24);
                border-color: rgba(182, 196, 255, 0.48);
            }}
            QPushButton#MusicFilterButton {{
                min-height: 32px;
                background: rgba(10, 12, 26, 0.74);
                color: rgba(255, 255, 255, 0.72);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 0 15px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#MusicFilterButton:hover {{
                background: rgba(255, 255, 255, 0.10);
                color: {C.TEXT};
            }}
            QPushButton#MusicFilterButton:checked {{
                background: rgba(94, 130, 255, 0.16);
                color: #DCE1FF;
                border-color: rgba(182, 196, 255, 0.32);
            }}
            QFrame#MusicHeader {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(15, 63, 94, 0.96),
                    stop:0.48 rgba(11, 36, 59, 0.94),
                    stop:1 rgba(7, 17, 31, 0.96));
                border: 1px solid rgba(182, 196, 255, 0.14);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
            QLabel#MusicCover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #145274,
                    stop:1 #B6C4FF);
                color: white;
                border-radius: 10px;
                font-size: 50px;
                font-weight: 900;
            }}
            QLabel#MusicType {{
                color: rgba(255, 255, 255, 0.88);
                font-size: 12px;
                font-weight: 900;
                background: transparent;
            }}
            QLabel#MusicTitle {{
                color: white;
                font-size: 34px;
                font-weight: 900;
                background: transparent;
            }}
            QLabel#MusicMeta {{
                color: rgba(255, 255, 255, 0.76);
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }}
            QPushButton#MusicHeaderAction {{
                min-height: 32px;
                background: rgba(94, 130, 255, 0.16);
                color: #DCE1FF;
                border: 1px solid rgba(182, 196, 255, 0.28);
                border-radius: 8px;
                padding: 0 13px;
                font-size: 11px;
                font-weight: 800;
            }}
            QPushButton#MusicHeaderAction:hover {{
                background: rgba(94, 130, 255, 0.24);
                border-color: rgba(182, 196, 255, 0.46);
            }}
            QPushButton#MusicHeaderMenuBtn {{
                background: transparent;
                color: rgba(180, 210, 240, 0.55);
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: 900;
                padding: 0;
            }}
            QPushButton#MusicHeaderMenuBtn:hover {{
                background: rgba(94, 130, 255, 0.14);
                color: #DCE1FF;
            }}
            QPushButton#MusicHeaderMenuBtn:pressed {{
                background: rgba(94, 130, 255, 0.22);
            }}
            QTableWidget#MusicTable {{
                background: rgba(5, 11, 20, 0.90);
                color: #f2f2f2;
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-top: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                outline: none;
                padding: 8px 10px 10px 10px;
            }}
            QTableWidget#MusicTable::item {{
                border: none;
                padding: 7px 8px;
                color: #e8e8e8;
            }}
            QTableWidget#MusicTable::item:selected {{
                background: rgba(255, 255, 255, 0.13);
                color: white;
                border: none;
            }}
            QHeaderView::section {{
                background: rgba(7, 14, 24, 0.96);
                color: rgba(255, 255, 255, 0.62);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.10);
                padding: 10px 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#MusicStatus {{
                color: rgba(255, 255, 255, 0.58);
                background: transparent;
                padding: 8px 4px 0 4px;
                font-size: 12px;
            }}
            QFrame#NowPlayingPanel {{
                background: rgba(10, 12, 26, 0.90);
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 12px;
            }}
            QLabel#NowPlayingHeading {{
                color: #8aa0b4;
                background: transparent;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1.4px;
                padding-bottom: 2px;
            }}
            QLabel#NowPlayingCover {{
                background: rgba(255, 255, 255, 0.040);
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-radius: 12px;
                color: rgba(255, 255, 255, 0.46);
                font-size: 42px;
                font-weight: 900;
            }}
            QScrollArea#NowPlayingScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#NowPlayingContent {{
                background: transparent;
            }}
            QLabel#NowPlayingDetails {{
                background: transparent;
                color: #f8fafc;
                border: none;
                padding: 0;
                font-size: 13px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 8px 2px 8px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.22);
                min-height: 42px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.34);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
                border: none;
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 2px 8px 2px 8px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(255,255,255,0.22);
                min-width: 42px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
                border: none;
                width: 0;
            }}
            QScrollArea#ArtistPageScroll {{
                background: rgba(5, 11, 20, 0.90);
                border: 1px solid rgba(182, 196, 255, 0.10);
                border-radius: 12px;
            }}
            QWidget#ArtistPageContent {{
                background: transparent;
            }}
            QFrame#ArtistHero {{
                background: rgba(15, 22, 34, 0.94);
                border: 1px solid rgba(182, 196, 255, 0.16);
                border-radius: 12px;
            }}
            QLabel#ArtistHeroImage {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            QLabel#ArtistPageName {{
                color: white;
                font-size: 38px;
                font-weight: 900;
            }}
            QLabel#ArtistPageStats {{
                color: #b6c4ff;
                font-size: 13px;
                font-weight: 800;
            }}
            QLabel#ArtistPageDescription {{
                color: rgba(255, 255, 255, 0.72);
                font-size: 13px;
                line-height: 1.35;
            }}
            QLabel#ArtistSectionTitle {{
                color: white;
                font-size: 20px;
                font-weight: 900;
                padding-top: 8px;
            }}
            QPushButton#ArtistBackButton {{
                background: rgba(255, 255, 255, 0.07);
                color: #dce1ff;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 800;
            }}
            QPushButton#ArtistBackButton:hover {{
                background: rgba(182, 196, 255, 0.15);
                border-color: rgba(182, 196, 255, 0.45);
            }}
            QListWidget#ArtistCardList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#ArtistCardList::item {{
                color: #f8fafc;
                border-radius: 8px;
                padding: 6px;
            }}
            QListWidget#ArtistCardList::item:hover {{
                background: rgba(255, 255, 255, 0.08);
            }}
            QTableWidget#ArtistTrackTable {{
                background: rgba(8, 10, 15, 0.70);
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 10px;
                outline: none;
            }}
            QTableWidget#ArtistTrackTable::item {{
                border: none;
                padding: 6px 8px;
            }}
            QTableWidget#ArtistTrackTable::item:selected {{
                background: rgba(182, 196, 255, 0.14);
            }}
        """

    def _build_artist_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("ArtistPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("ArtistPageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 24)
        layout.setSpacing(14)

        nav = QHBoxLayout()
        self.artist_back_btn = QPushButton("Volver")
        self.artist_back_btn.setObjectName("ArtistBackButton")
        self.artist_back_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 17))
        self.artist_back_btn.setIconSize(QSize(17, 17))
        self.artist_back_btn.clicked.connect(self._show_browse_content)
        nav.addWidget(self.artist_back_btn)
        nav.addStretch()
        layout.addLayout(nav)

        hero = QFrame()
        hero.setObjectName("ArtistHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 18, 18, 18)
        hero_layout.setSpacing(20)
        self.artist_hero_image = QLabel()
        self.artist_hero_image.setObjectName("ArtistHeroImage")
        self.artist_hero_image.setFixedSize(210, 210)
        self.artist_hero_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_hero_image.setText("♪")
        hero_layout.addWidget(self.artist_hero_image)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(8)
        self.artist_page_name = QLabel("Artista")
        self.artist_page_name.setObjectName("ArtistPageName")
        self.artist_page_name.setWordWrap(True)
        self.artist_page_stats = QLabel("")
        self.artist_page_stats.setObjectName("ArtistPageStats")
        self.artist_page_stats.setWordWrap(True)
        self.artist_page_description = QLabel("")
        self.artist_page_description.setObjectName("ArtistPageDescription")
        self.artist_page_description.setWordWrap(True)
        self.artist_page_description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hero_text.addStretch()
        hero_text.addWidget(self.artist_page_name)
        hero_text.addWidget(self.artist_page_stats)
        hero_text.addWidget(self.artist_page_description)
        hero_text.addStretch()
        hero_layout.addLayout(hero_text, stretch=1)
        layout.addWidget(hero)

        self.artist_popular_table = self._make_artist_track_table()
        self.artist_popular_table.cellDoubleClicked.connect(
            lambda row, _col: self._play_artist_page_track("top_songs", row)
        )
        layout.addWidget(self._artist_section_label("Mas escuchadas"))
        layout.addWidget(self.artist_popular_table)

        self.artist_recommended_table = self._make_artist_track_table()
        self.artist_recommended_table.cellDoubleClicked.connect(
            lambda row, _col: self._play_artist_page_track("recommendations", row)
        )
        self.artist_recommended_title = self._artist_section_label("Canciones recomendadas")
        layout.addWidget(self.artist_recommended_title)
        layout.addWidget(self.artist_recommended_table)

        self.artist_albums_list = self._make_artist_card_list()
        self.artist_albums_list.itemClicked.connect(self._open_artist_album_item)
        self.artist_albums_title = self._artist_section_label("Albumes")
        layout.addWidget(self.artist_albums_title)
        layout.addWidget(self.artist_albums_list)

        self.artist_singles_list = self._make_artist_card_list()
        self.artist_singles_list.itemClicked.connect(self._open_artist_album_item)
        self.artist_singles_title = self._artist_section_label("Singles y EPs")
        layout.addWidget(self.artist_singles_title)
        layout.addWidget(self.artist_singles_list)

        self.artist_videos_list = self._make_artist_card_list()
        self.artist_videos_list.itemClicked.connect(self._play_artist_video_item)
        self.artist_videos_title = self._artist_section_label("Videos")
        layout.addWidget(self.artist_videos_title)
        layout.addWidget(self.artist_videos_list)

        self.artist_related_list = self._make_artist_card_list()
        self.artist_related_list.itemClicked.connect(self._open_related_artist_item)
        self.artist_related_title = self._artist_section_label("Artistas relacionados")
        layout.addWidget(self.artist_related_title)
        layout.addWidget(self.artist_related_list)

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _artist_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ArtistSectionTitle")
        return label

    def _make_artist_track_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setObjectName("ArtistTrackTable")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["#", "Titulo", "Album", "Duracion"])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setIconSize(QSize(42, 42))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 42)
        table.setColumnWidth(2, 220)
        table.setColumnWidth(3, 82)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return table

    def _make_artist_card_list(self) -> QListWidget:
        widget = QListWidget()
        widget.setObjectName("ArtistCardList")
        widget.setViewMode(QListView.ViewMode.IconMode)
        widget.setFlow(QListView.Flow.LeftToRight)
        widget.setWrapping(False)
        widget.setResizeMode(QListView.ResizeMode.Adjust)
        widget.setMovement(QListView.Movement.Static)
        widget.setIconSize(QSize(142, 142))
        widget.setGridSize(QSize(166, 205))
        widget.setSpacing(4)
        widget.setFixedHeight(218)
        widget.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return widget

    def _show_browse_content(self):
        self._artist_page_open = False
        self.music_content_stack.setCurrentWidget(self.browse_page)

    def _show_artist_loading(self, name: str, data: dict):
        self._artist_page_open = True
        self._artist_page_data = dict(data or {})
        self.artist_page_name.setText(name or "Artista")
        self.artist_page_stats.setText("Cargando página del artista...")
        self.artist_page_description.setText("")
        self.artist_hero_image.setPixmap(QPixmap())
        self.artist_hero_image.setText("♪")
        for table in (self.artist_popular_table, self.artist_recommended_table):
            table.setRowCount(0)
            table.setFixedHeight(56)
        for widget in (
            self.artist_albums_list, self.artist_singles_list,
            self.artist_videos_list, self.artist_related_list,
        ):
            widget.clear()
        self.music_content_stack.setCurrentWidget(self.artist_page)

    def _populate_artist_track_table(self, table: QTableWidget, tracks: list[dict]):
        table.setRowCount(0)
        for row, raw in enumerate(tracks):
            data = dict(raw)
            table.insertRow(row)
            table.setRowHeight(row, 54)
            number = QTableWidgetItem(str(row + 1))
            number.setData(Qt.ItemDataRole.UserRole, data)
            number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, number)
            title = self._safe_text(data.get("title"))
            artists = self._safe_text(data.get("artists"))
            title_item = QTableWidgetItem(f"{title}\n{artists}" if artists else title)
            title_item.setData(Qt.ItemDataRole.UserRole, data)
            table.setItem(row, 1, title_item)
            album_item = QTableWidgetItem(self._safe_text(data.get("album")))
            album_item.setData(Qt.ItemDataRole.UserRole, data)
            table.setItem(row, 2, album_item)
            duration_item = QTableWidgetItem(self._safe_text(data.get("duration")))
            duration_item.setData(Qt.ItemDataRole.UserRole, data)
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 3, duration_item)
            url = self._safe_text(data.get("thumbnail"))
            if url:
                self._queue_artist_image(url, title_item, 42)
        table.setFixedHeight(38 + max(1, len(tracks)) * 54)

    def _populate_artist_cards(self, widget: QListWidget, items: list[dict], subtitle_key: str):
        widget.clear()
        for raw in items:
            data = dict(raw)
            title = self._safe_text(data.get("title") or data.get("name"))
            subtitle = self._safe_text(data.get(subtitle_key))
            item = QListWidgetItem(f"{title}\n{subtitle}" if subtitle else title)
            item.setData(Qt.ItemDataRole.UserRole, data)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setToolTip(title)
            widget.addItem(item)
            url = self._safe_text(data.get("thumbnail"))
            if url:
                self._queue_artist_image(url, item, 142)

    def _queue_artist_image(self, url: str, target, size: int):
        url = str(url or "").strip()
        if not url:
            return
        self._artist_image_targets.setdefault(url, []).append((target, int(size)))
        cached = self._thumb_cache.get(url)
        if cached:
            self._apply_thumb(url, cached)
        else:
            self._ensure_thumb_async({"thumbnail": url})

    def _apply_artist_target_image(self, target, raw: bytes, size: int):
        pix = QPixmap()
        if not pix.loadFromData(raw) or pix.isNull():
            return
        scaled = pix.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if isinstance(target, QLabel):
            target.setText("")
            target.setPixmap(scaled)
        elif isinstance(target, QListWidgetItem):
            target.setIcon(QIcon(scaled))
        elif isinstance(target, QTableWidgetItem):
            target.setIcon(QIcon(scaled))

    def _render_artist_page(self, data: dict):
        self._artist_page_open = True
        self._artist_page_data = dict(data or {})
        self._artist_image_targets.clear()
        name = self._safe_text(data.get("name")) or "Artista"
        stats = [
            self._safe_text(data.get("monthlyListeners")),
            self._safe_text(data.get("subscribers")),
            self._safe_text(data.get("views")),
        ]
        stats = [value for value in stats if value]
        self.artist_page_name.setText(name)
        self.artist_page_stats.setText("  ·  ".join(stats))
        self.artist_page_description.setText(self._safe_text(data.get("description")))
        self.artist_hero_image.setPixmap(QPixmap())
        self.artist_hero_image.setText("♪")
        hero_url = self._safe_text(data.get("thumbnail"))
        if hero_url:
            self._queue_artist_image(hero_url, self.artist_hero_image, 210)

        popular = list(data.get("top_songs") or [])
        recommended = list(data.get("recommendations") or [])
        albums = list(data.get("albums") or [])
        singles = list(data.get("singles") or [])
        videos = list(data.get("videos") or [])
        related = list(data.get("related") or [])
        self._populate_artist_track_table(self.artist_popular_table, popular)
        self._populate_artist_track_table(self.artist_recommended_table, recommended)
        self.artist_recommended_title.setVisible(bool(recommended))
        self.artist_recommended_table.setVisible(bool(recommended))
        self._populate_artist_cards(self.artist_albums_list, albums, "year")
        self._populate_artist_cards(self.artist_singles_list, singles, "year")
        self._populate_artist_cards(self.artist_videos_list, videos, "views")
        self._populate_artist_cards(self.artist_related_list, related, "subscribers")
        for title, widget, values in (
            (self.artist_albums_title, self.artist_albums_list, albums),
            (self.artist_singles_title, self.artist_singles_list, singles),
            (self.artist_videos_title, self.artist_videos_list, videos),
            (self.artist_related_title, self.artist_related_list, related),
        ):
            title.setVisible(bool(values))
            widget.setVisible(bool(values))
        self.music_content_stack.setCurrentWidget(self.artist_page)
        self.artist_page.verticalScrollBar().setValue(0)

    def _play_artist_page_track(self, key: str, row: int):
        tracks = list(self._artist_page_data.get(key) or [])
        if not (0 <= row < len(tracks)):
            return
        playable = self._audio_tracks_from_data(tracks)
        if playable:
            self._send_playback("play_tracks", {
                "tracks": playable,
                "start_index": row,
                "shuffle": False,
            })

    def _audio_tracks_from_data(self, tracks: list[dict]) -> list[dict]:
        return [
            {
                "videoId": item.get("videoId", ""),
                "title": item.get("title", ""),
                "artists": item.get("artists", ""),
            }
            for item in tracks
            if item.get("videoId")
        ]

    def _open_artist_album_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self._open_album_page(data)

    def _play_artist_video_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("videoId"):
            self._send_playback("play_track", data)

    def _open_related_artist_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self._open_artist_page(data)

    def _run(self, op: str, fn):
        self.status.setVisible(True)
        self.status.setText("Cargando...")

        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            try:
                self._result_sig.emit(op, result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _safe_text(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return str(value.get("name") or value.get("title") or value.get("text") or value.get("id") or "")
        if isinstance(value, list):
            parts = [self._safe_text(item) for item in value]
            return ", ".join(part for part in parts if part)
        return str(value)

    def _esc(self, value) -> str:
        return html_lib.escape(self._safe_text(value))

    def _playlist_title(self, data: dict) -> str:
        title = self._safe_text(data.get("title") or data.get("name") or "")
        if (data.get("playlistId") or data.get("browseId")) == "LM" or title.lower() in {"liked music", "liked songs"}:
            return "Canciones que te gustan"
        return title or "Playlist"

    def _playlist_meta(self, data: dict, track_count: int | None = None) -> str:
        author = self._safe_text(data.get("author") or "YouTube Music")
        count = track_count if track_count is not None else data.get("itemCount") or data.get("trackCount") or ""
        if count:
            return f"{author} - {count} canciones"
        return author

    def _format_date(self, value) -> str:
        text = self._safe_text(value).strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        return text

    def _set_header(self, title: str, subtitle: str = "", kind: str = "", data: dict | None = None):
        self._header_data = dict(data or {})
        self.type_label.setText(kind or "Music")
        self.title_label.setText(title)
        self.meta_label.setText(subtitle)
        self._set_cover(self._header_data)
        self._ensure_thumb_async(self._header_data)

    def _set_cover(self, data: dict):
        pix = self._thumb_pixmap(data, 128)
        if pix is not None:
            self.cover_label.setText("")
            self.cover_label.setPixmap(pix)
        else:
            pid = data.get("playlistId") or data.get("browseId") or ""
            liked = pid == "LM" or "liked" in self._safe_text(data.get("title")).lower()
            icon_name = "heart" if liked else "playlist" if pid or self._table_kind == "playlists" else "music"
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("")
            self.cover_label.setPixmap(_line_icon(icon_name, "#F8FAFC", 58).pixmap(58, 58))

    def _thumb_pixmap(self, data: dict, size: int = 44) -> QPixmap | None:
        raw = data.get("thumb_b64") or ""
        if not raw:
            return None
        try:
            pix = QPixmap()
            pix.loadFromData(base64.b64decode(raw))
            if not pix.isNull():
                scaled = pix.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, (scaled.width() - size) // 2)
                y = max(0, (scaled.height() - size) // 2)
                cropped = scaled.copy(x, y, size, size)
                result = QPixmap(size, size)
                result.fill(Qt.GlobalColor.transparent)
                painter = QPainter(result)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, size, size), 4, 4)
                painter.setClipPath(clip)
                painter.drawPixmap(0, 0, cropped)
                painter.end()
                return result
        except Exception:
            pass
        return None

    def _playlist_cover_icon(self, liked: bool, size: int = 44) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        background = QLinearGradient(QPointF(0, 0), QPointF(size, size))
        if liked:
            background.setColorAt(0.0, QColor("#245A86"))
            background.setColorAt(1.0, QColor("#6FC7EA"))
        else:
            background.setColorAt(0.0, QColor("#172A3D"))
            background.setColorAt(1.0, QColor("#244B69"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(QRectF(0, 0, size, size), 5, 5)

        icon_size = max(20, int(size * 0.52))
        icon = _line_icon(
            "heart" if liked else "playlist",
            "#F8FAFC",
            icon_size,
        ).pixmap(icon_size, icon_size)
        offset = (size - icon_size) // 2
        painter.drawPixmap(offset, offset, icon)
        painter.end()
        return QIcon(pix)

    def _mime_from_raw(self, raw: bytes) -> str:
        if raw.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if raw.startswith(b"\x89PNG"):
            return "image/png"
        if raw.startswith(b"GIF"):
            return "image/gif"
        if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            return "image/webp"
        return "image/jpeg"

    def _data_uri(self, raw: bytes) -> str:
        if not raw:
            return ""
        return f"data:{self._mime_from_raw(raw)};base64,{base64.b64encode(raw).decode('ascii')}"

    def _image_html(self, src: str, width: int, margin: str = "0 0 14px 0") -> str:
        if not src:
            return ""
        return (
            f'<img src="{html_lib.escape(src, quote=True)}" width="{width}" '
            f'style="display:block; margin:{margin};">'
        )

    def _details_image_width(self) -> int:
        try:
            width = self.details_scroll.viewport().width()
        except Exception:
            width = 320
        return max(260, min(560, int(width or 320) - 6))

    def _image_src(self, data: dict, b64_key: str, src_key: str, url_key: str, src_url_key: str = "") -> str:
        url = str(data.get(url_key) or "")
        src_url = str(data.get(src_url_key) or "") if src_url_key else ""
        if data.get(src_key) and (not src_url or not url or src_url == url):
            return str(data.get(src_key))
        if data.get(b64_key):
            return f"data:image/jpeg;base64,{data.get(b64_key)}"
        if url:
            return url
        return ""

    def _raw_from_image_data(self, data: dict, b64_key: str, src_key: str, url_key: str, src_url_key: str = "") -> bytes:
        url = str(data.get(url_key) or "")
        src_url = str(data.get(src_url_key) or "") if src_url_key else ""
        src = str(data.get(src_key) or "")
        if src.startswith("data:") and (not src_url or not url or src_url == url):
            try:
                return base64.b64decode(src.split(",", 1)[1])
            except Exception:
                return b""
        if data.get(b64_key) and (not src_url or not url or src_url == url):
            try:
                return base64.b64decode(str(data.get(b64_key)))
            except Exception:
                return b""
        if url and url in self._thumb_cache:
            return self._thumb_cache[url]
        return b""

    def _rounded_pixmap(self, src: QPixmap, radius: int = 10) -> QPixmap:
        if src.isNull():
            return src
        out = QPixmap(src.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, src.width(), src.height()), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, src)
        painter.end()
        return out

    def _set_detail_cover(self, data: dict):
        raw = self._raw_from_image_data(data, "thumb_b64", "thumb_src", "thumbnail", "thumb_src_url")
        if not raw:
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText("♪")
            self._ensure_thumb_async(data)
            return
        pix = QPixmap()
        pix.loadFromData(raw)
        if pix.isNull():
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText("♪")
            return
        max_w = max(260, min(520, self.detail_cover.width() or 320))
        max_h = max(260, min(360, self.detail_cover.maximumHeight() or 340))
        scaled = pix.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.detail_cover.setText("")
        self.detail_cover.setPixmap(self._rounded_pixmap(scaled, 10))

    def _fetch_thumb_b64(self, url: str) -> str:
        url = str(url or "").strip()
        if not url:
            return ""
        cached = self._thumb_cache.get(url)
        if cached is not None:
            return base64.b64encode(cached).decode("ascii")
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            self._thumb_cache[url] = resp.content
            return base64.b64encode(resp.content).decode("ascii")
        except Exception:
            return ""

    def _ensure_thumb_async(self, data: dict):
        if not data:
            return
        url = str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "").strip()
        src_url = str(data.get("thumb_src_url") or "")
        if data.get("thumb_b64") and (not url or not src_url or src_url == url):
            return
        if not url:
            return
        cached = self._thumb_cache.get(url)
        if cached is not None:
            self._apply_thumb(url, cached)
            return
        if url in self._thumb_loading:
            return
        if self._thumb_executor_closed:
            return
        self._thumb_loading.add(url)

        def worker():
            raw = b""
            try:
                resp = requests.get(url, timeout=6)
                resp.raise_for_status()
                raw = resp.content
            except Exception:
                raw = b""
            try:
                self._thumb_sig.emit(url, raw)
            except RuntimeError:
                pass

        try:
            self._thumb_executor.submit(worker)
        except RuntimeError:
            self._thumb_loading.discard(url)

    def _apply_thumb(self, url, raw):
        url = str(url or "")
        self._thumb_loading.discard(url)
        if not url or not raw:
            return
        self._thumb_cache[url] = bytes(raw)
        encoded = base64.b64encode(bytes(raw)).decode("ascii")

        header_url = str(self._header_data.get("thumbnail") or self._header_data.get("cover") or "")
        if header_url == url:
            self._header_data["thumb_b64"] = encoded
            self._set_cover(self._header_data)

        for row in list(self._thumb_rows.get(url, set())):
            data = self._row_data(row)
            if str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "") != url:
                continue
            data["thumb_b64"] = encoded
            data["thumb_src"] = self._data_uri(bytes(raw))
            data["thumb_src_url"] = url
            self._set_row_data(row, data)
            self._set_row_icon(row, data)

        now_url = str(self._now_playing_data.get("thumbnail") or self._now_playing_data.get("cover") or "")
        artist_url = str(self._now_playing_data.get("artistThumbnail") or "")
        if now_url == url:
            self._now_playing_data["thumb_b64"] = encoded
            self._now_playing_data["thumb_src"] = self._data_uri(bytes(raw))
            self._now_playing_data["thumb_src_url"] = url
            self._render_now_playing()
        elif artist_url == url:
            self._now_playing_data["artist_thumb_b64"] = encoded
            self._now_playing_data["artist_thumb_src"] = self._data_uri(bytes(raw))
            self._now_playing_data["artist_thumb_src_url"] = url
            self._render_now_playing()

        targets = self._artist_image_targets.pop(url, [])
        for target, size in targets:
            try:
                self._apply_artist_target_image(target, bytes(raw), size)
            except RuntimeError:
                pass

    def _prefetch_thumbnails(self, count: int | None = None):
        total = self.table.rowCount() if count is None else min(self.table.rowCount(), max(0, int(count)))
        for row in range(total):
            self._ensure_thumb_async(self._row_data(row))

    def _prefetch_visible_thumbnails(self, *_):
        viewport_h = self.table.viewport().height()
        for row in range(self.table.rowCount()):
            y = self.table.rowViewportPosition(row)
            h = self.table.rowHeight(row)
            if y + h < -240:
                continue
            if y > viewport_h + 420:
                continue
            self._ensure_thumb_async(self._row_data(row))

    def _configure_table(self, headers: list[str], widths: dict[int, int] | None = None, stretch: int = 1):
        self._table_revision += 1
        self._thumb_rows.clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        if 0 <= stretch < len(headers):
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        for col, width in (widths or {}).items():
            self.table.setColumnWidth(col, width)

    def _item(self, text: str, data: dict, align=None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, data)
        if align is not None:
            item.setTextAlignment(align)
        return item

    def _set_row_data(self, row: int, data: dict):
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, data)

    def _row_data(self, row: int) -> dict:
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                return data
        return {}

    def _set_row_icon(self, row: int, data: dict):
        icon_col = 1 if self._table_kind in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        } else 0
        item = self.table.item(row, icon_col)
        if not item:
            return
        if self._table_kind == "playlists":
            pid = data.get("playlistId") or data.get("browseId") or ""
            liked = pid == "LM" or "liked" in self._safe_text(data.get("title")).lower()
            if liked:
                item.setIcon(self._playlist_cover_icon(True))
                return
        pix = self._thumb_pixmap(data, 44)
        if pix is not None:
            item.setIcon(QIcon(pix))
            return
        url = str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "").strip()
        if url:
            self._thumb_rows.setdefault(url, set()).add(row)
        if self._table_kind == "playlists":
            item.setIcon(self._playlist_cover_icon(False))

    def _add_song_row(self, row: int, data: dict, index: int):
        self.table.insertRow(row)
        self.table.setRowHeight(row, 58)
        number = "▶" if data.get("_playing") else str(index + 1)
        self.table.setItem(row, 0, self._item(number, data, Qt.AlignmentFlag.AlignCenter))
        title = self._safe_text(data.get("title") or "(sin titulo)")
        artists = self._safe_text(data.get("artists"))
        title_text = f"{title}\n{artists}" if artists else title
        self.table.setItem(row, 1, self._item(title_text, data))
        self.table.setItem(row, 2, self._item(self._safe_text(data.get("album")), data))
        self.table.setItem(row, 3, self._item(self._safe_text(data.get("duration")), data, Qt.AlignmentFlag.AlignCenter))
        self._set_row_icon(row, data)

    def _show_songs(self, items: list[dict], table_kind: str = "songs", playlist: dict | None = None):
        self._show_browse_content()
        self._table_kind = table_kind
        self._items = []
        self._configure_table(
            ["#", "Titulo", "Album", "Duracion"],
            widths={0: 44, 2: 260, 3: 86},
            stretch=1,
        )
        for idx, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "song"
            data["_index"] = idx
            self._items.append(data)
            self._add_song_row(idx, data, idx)
        if playlist:
            playlist = dict(playlist)
            playlist["itemCount"] = playlist.get("itemCount") or len(self._items)
            self._current_playlist = playlist
            self._set_header(self._playlist_title(playlist), self._playlist_meta(playlist, len(self._items)), "Lista", playlist)
        is_playlist = bool(playlist) and table_kind == "playlist_tracks" and bool(self._items)
        self.shuffle_btn.setVisible(is_playlist)
        self.status.setVisible(False)
        self._prefetch_thumbnails()
        self._prefetch_audio_streams(0, 4)
        self._restore_playing_selection()

    def _show_playlists(self, items: list[dict]):
        self._show_browse_content()
        self._table_kind = "playlists"
        self._current_playlist = None
        self._items = []
        self._configure_table(["Playlist", "Autor", "Canciones"], widths={1: 220, 2: 110}, stretch=0)
        for row, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "playlist"
            self._items.append(data)
            self.table.insertRow(row)
            self.table.setRowHeight(row, 62)
            self.table.setItem(row, 0, self._item(self._playlist_title(data), data))
            self.table.setItem(row, 1, self._item(self._safe_text(data.get("author")), data))
            self.table.setItem(row, 2, self._item(self._safe_text(data.get("itemCount")), data, Qt.AlignmentFlag.AlignCenter))
            self._set_row_icon(row, data)
        self._set_header("Playlists", "Tu biblioteca de YouTube Music", "Playlists", {})
        self.shuffle_btn.setVisible(False)
        self.status.setVisible(False)
        self._prefetch_thumbnails()

    def _show_artists(self, items: list[dict]):
        self._show_browse_content()
        self._table_kind = "artists"
        self._current_playlist = None
        self._items = []
        self._configure_table(["Artista", "Info"], widths={1: 220}, stretch=0)
        for row, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "artist"
            self._items.append(data)
            self.table.insertRow(row)
            self.table.setRowHeight(row, 62)
            self.table.setItem(row, 0, self._item(self._safe_text(data.get("name") or data.get("title")), data))
            self.table.setItem(row, 1, self._item(self._safe_text(data.get("subscribers") or data.get("description")), data))
            self._set_row_icon(row, data)
        self._set_header("Artistas", "Resultados de la busqueda", "Busqueda", {})
        self.shuffle_btn.setVisible(False)
        self.status.setVisible(False)
        self._prefetch_thumbnails()

    # ------------------------------------------------------------------
    # Header "⋯" settings menu
    # ------------------------------------------------------------------

    _MENU_STYLE = """
        QMenu {
            background: #0d1117;
            color: #e6f0f8;
            border: 1px solid rgba(182, 196, 255, 0.20);
            border-radius: 10px;
            padding: 5px 4px;
        }
        QMenu::item {
            padding: 7px 20px 7px 14px;
            border-radius: 6px;
            font-size: 12px;
        }
        QMenu::item:selected { background: rgba(94, 130, 255, 0.16); }
        QMenu::item:checked  { color: #B6C4FF; }
        QMenu::separator {
            height: 1px;
            background: rgba(182, 196, 255, 0.12);
            margin: 4px 10px;
        }
    """

    def _show_header_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._MENU_STYLE)

        in_playlist = self._table_kind == "playlist_tracks"
        in_playlists_list = self._table_kind == "playlists"

        if in_playlists_list:
            act_import = menu.addAction("Importar playlist...")
            act_import.triggered.connect(self._import_playlist_dialog)

        if in_playlist:
            act_export_cur = menu.addAction("Exportar esta lista...")
            act_export_cur.triggered.connect(self._do_export_current)
            act_export_liked = menu.addAction("Exportar Me Gusta...")
            act_export_liked.triggered.connect(self._do_export_liked)
            menu.addSeparator()

            cf_label = f"{'✓' if self._cf_enabled else '   '}  Crossfade  ({self._cf_secs} s)"
            act_cf = menu.addAction(cf_label)
            act_cf.setCheckable(True)
            act_cf.setChecked(self._cf_enabled)
            act_cf.triggered.connect(self._toggle_crossfade)

            act_cf_dur = menu.addAction("  Cambiar duración del crossfade...")
            act_cf_dur.triggered.connect(self._change_crossfade_duration)

        if not in_playlists_list and not in_playlist:
            menu.addAction("Sin opciones disponibles").setEnabled(False)

        btn = self._hdr_menu_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomRight()))

    def _toggle_crossfade(self, checked: bool):
        self._cf_enabled = checked
        app_settings.set("crossfade_enabled", checked)
        self._send_playback("set_crossfade", {"seconds": self._cf_secs, "enabled": checked})

    def _change_crossfade_duration(self):
        val, ok = QInputDialog.getInt(
            self, "Duración del crossfade",
            "Segundos de fundido entre canciones (1-15):",
            self._cf_secs, 1, 15, 1,
        )
        if ok:
            self._cf_secs = val
            app_settings.set("crossfade_seconds", val)
            if self._cf_enabled:
                self._send_playback("set_crossfade", {"seconds": val, "enabled": True})

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def _export_done(self, result: dict):
        QMessageBox.information(
            self, "Exportación completada",
            f"Se exportaron {result['count']} canciones de '{result['name']}' a:\n{result['path']}",
        )

    def _export_failed(self, message: str):
        QMessageBox.warning(self, "Error al exportar", message or "No se pudo exportar la lista.")

    def _do_export_liked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Me Gusta",
            str(Path.home() / "Downloads" / "jarvis_me_gusta.json"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import export_liked_to_file
                result = export_liked_to_file(path)
                QTimer.singleShot(0, lambda r=result: self._export_done(r))
            except Exception as e:
                msg = str(e) or repr(e)
                QTimer.singleShot(0, lambda m=msg: self._export_failed(m))

        threading.Thread(target=_work, daemon=True).start()

    def _do_export_current(self):
        pl = self._current_playlist or {}
        # Export the tracks currently loaded in the table — reliable for any list
        # (server playlists, liked, or imported) and avoids a re-fetch that can
        # fail or come back empty.
        tracks = [
            {"videoId": it.get("videoId", ""), "title": it.get("title", ""),
             "artists": it.get("artists", "")}
            for it in (self._items or [])
            if it.get("videoId")
        ]
        if not tracks:
            QMessageBox.information(self, "Lista vacía", "No hay canciones cargadas para exportar.")
            return
        name = (pl.get("title") or pl.get("name") or "playlist").replace("/", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar lista actual",
            str(Path.home() / "Downloads" / f"jarvis_{name}.json"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import export_tracks_to_file
                result = export_tracks_to_file(tracks, name, path)
                QTimer.singleShot(0, lambda r=result: self._export_done(r))
            except Exception as e:
                msg = str(e) or repr(e)
                QTimer.singleShot(0, lambda m=msg: self._export_failed(m))

        threading.Thread(target=_work, daemon=True).start()

    def _import_playlist_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar playlist Jarvis",
            str(Path.home() / "Downloads"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import import_playlist_from_file
                tracks = import_playlist_from_file(path)
                if not tracks:
                    QTimer.singleShot(0, lambda: QMessageBox.warning(
                        self, "Playlist vacía", "No se encontraron pistas con videoId."
                    ))
                    return
                import json as _json
                data = _json.loads(Path(path).read_text(encoding="utf-8"))
                playlist_name = data.get("name", Path(path).stem)
                self._send_playback("play_tracks", {"tracks": tracks, "start_index": 0, "shuffle": False})
                QTimer.singleShot(0, lambda nm=playlist_name, tr=tracks: (
                    self._set_header(
                        nm,
                        f"Importada • {len(tr)} canciones",
                        "Importada",
                        {},
                    ),
                    self._show_imported_tracks(tr),
                ))
            except Exception as e:
                msg = str(e) or repr(e)
                QTimer.singleShot(0, lambda m=msg: QMessageBox.warning(
                    self, "Error al importar", m
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _show_imported_tracks(self, tracks: list):
        """Display imported tracks in the table (runs on UI thread)."""
        self._table_kind = "playlist_tracks"
        self._items = [
            {
                "videoId": t.get("videoId", ""),
                "title": t.get("title", ""),
                "artists": t.get("artists", ""),
                "_kind": "song",
                "_index": i,
            }
            for i, t in enumerate(tracks)
        ]
        self._show_songs(self._items, table_kind="playlist_tracks")
        self.shuffle_btn.setVisible(True)

    def _set_active_section(self, key: str):
        for name, btn in getattr(self, "_section_buttons", {}).items():
            btn.blockSignals(True)
            btn.setChecked(name == key)
            btn.blockSignals(False)
        self.section_row.setVisible(True)

    def _show_section(self, key: str):
        if key == "playlists":
            self.load_playlists(force=True)
        else:
            self.load_recommendations(force=True)

    def load_recommendations(self, force: bool = False):
        if self._artist_page_open and not force:
            return
        self._show_browse_content()
        self.filter_row.setVisible(False)
        self._set_active_section("recommendations")
        self._set_header("Recomendaciones", "Hechas para ti según tu actividad", "Recomendaciones", {})
        self._run("recommendations", lambda: __import__("actions.ytmusic", fromlist=["get_recommendations"]).get_recommendations(limit=60))

    def load_playlists(self, force: bool = False):
        if self._artist_page_open and not force:
            return
        self._show_browse_content()
        self.filter_row.setVisible(False)
        self._set_active_section("playlists")
        self._run("library_playlists", lambda: __import__("actions.ytmusic", fromlist=["list_playlists"]).list_playlists(limit=None))

    def search(self):
        self._show_browse_content()
        query = self.query_input.text().strip()
        if not query:
            self.load_playlists(force=True)
            return
        self.section_row.setVisible(False)

        def _load():
            ytmod = __import__(
                "actions.ytmusic",
                fromlist=["search_songs", "search_playlists", "search_artists"],
            )
            return {
                "songs": ytmod.search_songs(query, limit=40),
                "playlists": ytmod.search_playlists(query, limit=40),
                "artists": ytmod.search_artists(query, limit=30),
            }

        self._set_header(f"Buscar: {query}", "Filtra por canciones, playlists o artistas", "Busqueda", {})
        self._run("search_all", _load)

    def _show_search_filter(self, key: str):
        if key not in self._filter_buttons:
            return
        for name, btn in self._filter_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name == key)
            btn.blockSignals(False)
        if key == "playlists":
            self._show_playlists(self._search_results.get("playlists", []))
            self._set_header("Playlists", "Resultados de la busqueda", "Busqueda", {})
        elif key == "artists":
            self._show_artists(self._search_results.get("artists", []))
        else:
            self._current_playlist = None
            self._show_songs(self._search_results.get("songs", []), table_kind="search_songs")
            self._set_header("Canciones", "Resultados de la busqueda", "Busqueda", {})

    def _select_row(self, row: int, _col: int = 0):
        if row >= 0:
            if self._table_kind == "artists":
                self.table.selectRow(row)
                data = self._row_data(row)
                if data:
                    self._open_artist_page(data)
                return
            if self._table_kind == "playlists":
                self.table.selectRow(row)
            else:
                QTimer.singleShot(0, self._restore_playing_selection)
            self._prefetch_audio_streams(row, 4)

    def _restore_playing_selection(self):
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks", "recommendations"
        }:
            return
        selected_row = -1
        for row in range(self.table.rowCount()):
            if self._row_data(row).get("_playing"):
                selected_row = row
                break
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return
        if selected_row >= 0:
            index = self.table.model().index(selected_row, 1)
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            selection_model.clearSelection()

    def _audio_tracks_from_items(self, start: int = 0, count: int | None = None) -> list[dict]:
        try:
            start_i = max(0, int(start or 0))
        except Exception:
            start_i = 0
        items = self._items[start_i:] if count is None else self._items[start_i:start_i + max(1, int(count))]
        return [
            {
                "videoId": item.get("videoId", ""),
                "title": item.get("title", ""),
                "artists": item.get("artists", ""),
            }
            for item in items
            if item.get("videoId")
        ]

    def _prefetch_audio_streams(self, start: int = 0, count: int = 4):
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks", "recommendations"
        }:
            return
        tracks = self._audio_tracks_from_items(start, count)
        if tracks:
            self._send_playback("prefetch_tracks", {"tracks": tracks, "start_index": 0, "count": count})

    def _activate_row(self, row: int, _col: int = 0):
        # cellDoubleClicked and cellActivated can both fire for a single gesture
        # (platform dependent). Debounce so we don't trigger playback twice.
        import time as _t
        now = _t.monotonic()
        last = getattr(self, "_last_activate", (None, 0.0))
        if last[0] == row and (now - last[1]) < 0.4:
            return
        self._last_activate = (row, now)

        data = self._row_data(row)
        if not data:
            return
        kind = data.get("_kind", "song")
        if kind == "playlist":
            self.open_playlist(data)
            return
        if kind == "artist":
            self._open_artist_page(data)
            return
        if kind == "song":
            self._play_song(data)

    def open_playlist(self, data: dict):
        pid = data.get("playlistId") or data.get("browseId") or ""
        if not pid:
            return
        self._current_playlist = dict(data)
        self._table_kind = "playlist_tracks"
        self._set_header(self._playlist_title(data), self._playlist_meta(data), "Lista", data)

        def _load():
            return __import__("actions.ytmusic", fromlist=["list_playlist_tracks"]).list_playlist_tracks(
                query_or_id=pid,
                limit=None,
                shuffle=False,
            )

        self._run("playlist_tracks", _load)

    def _play_song(self, data: dict):
        self._mark_playing_row(
            self._safe_text(data.get("title")),
            self._safe_text(data.get("artists")),
        )
        if self._table_kind in {"playlist_tracks", "album_tracks", "artist_tracks", "recommendations"}:
            tracks = self._audio_tracks_from_items(0, None)
            if tracks:
                self._send_playback("play_tracks", {
                    "tracks": tracks,
                    "start_index": int(data.get("_index", 0) or 0),
                    "shuffle": False,
                })
                return
        if self._current_playlist and self._table_kind == "playlist_tracks":
            playlist_id = self._current_playlist.get("playlistId") or self._current_playlist.get("browseId") or ""
            if playlist_id:
                self._send_playback("play_playlist", {
                    "playlist_id": playlist_id,
                    "limit": 1000,
                    "start_index": int(data.get("_index", 0) or 0),
                    "shuffle": False,
                })
                return
        if data.get("videoId"):
            self._send_playback("play_track", {
                "videoId": data.get("videoId", ""),
                "title": data.get("title", ""),
                "artists": data.get("artists", ""),
            })
            return
        query = f"{data.get('title', '')} {data.get('artists', '')}".strip()
        if query:
            self._send_playback("play", {"query": query, "type": "song"})

    def _play_current_playlist_shuffled(self):
        if self._table_kind != "playlist_tracks":
            return
        tracks = self._audio_tracks_from_items(0, None)
        if not tracks:
            return
        random.shuffle(tracks)
        first = tracks[0]
        self._mark_playing_row(
            self._safe_text(first.get("title")),
            self._safe_text(first.get("artists")),
        )
        self._send_playback("play_tracks", {
            "tracks": tracks,
            "start_index": 0,
            "shuffle": False,
        })

    def _send_playback(self, action: str, params: dict | None = None):
        win = self.window()
        cb = getattr(win, "on_playback_command", None)
        if cb:
            threading.Thread(target=cb, args=(action, params or {}), daemon=True).start()

    _DETAILS_MIN_WIDTH = 760
    _HEADER_NARROW_WIDTH = 560

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_details_visibility()
        self._apply_header_responsive()

    def _details_should_show(self) -> bool:
        data = self._now_playing_data if isinstance(self._now_playing_data, dict) else {}
        return bool(data.get("title")) and self.width() >= self._DETAILS_MIN_WIDTH

    def _update_details_visibility(self):
        try:
            self.details_panel.setVisible(self._details_should_show())
        except RuntimeError:
            pass

    def _apply_header_responsive(self):
        narrow = self.width() < self._HEADER_NARROW_WIDTH
        if narrow == getattr(self, "_header_narrow_state", None):
            return
        self._header_narrow_state = narrow
        self.title_label.setStyleSheet(
            "color: white; background: transparent; font-weight: 900;"
            f" font-size: {26 if narrow else 34}px;"
        )

    def update_now_playing(self, title: str, artists: str, playing: bool = True):
        title = self._safe_text(title).strip()
        artists = self._safe_text(artists).strip()
        if not title:
            self._now_playing_data = {}
            self._now_playing_key = ("", "")
            self._detail_render_fingerprint = ()
            self._detail_render_track_key = ("", "")
            self._details_loading_key = ("", "", "")
            self.details_panel.setVisible(False)
            self._details_visible_once = False
            return

        key = (title.lower(), artists.lower())
        track_changed = key != self._now_playing_key
        matched = self._find_matching_track(title, artists)
        data = dict(matched or {"title": title, "artists": artists, "_kind": "song"})
        data["_playing"] = bool(playing)
        if key == self._now_playing_key:
            kept = {k: v for k, v in self._now_playing_data.items() if v not in ("", None, [])}
            self._now_playing_data = {**data, **kept}
        else:
            self._now_playing_data = data
        self._now_playing_key = key
        self._update_details_visibility()
        self._details_visible_once = True
        if track_changed or self._playing_mark_revision != self._table_revision:
            self._mark_playing_row(title, artists)
        self._ensure_thumb_async(self._now_playing_data)
        self._render_now_playing()
        detail_key = self._details_key(self._now_playing_data)
        if not self._now_playing_data.get("_details_loaded") and self._details_loading_key != detail_key:
            self._load_now_playing_details(self._now_playing_data)

    def _find_matching_track(self, title: str, artists: str) -> dict:
        title_n = title.strip().lower()
        artists_n = artists.strip().lower()
        for item in self._items:
            if self._safe_text(item.get("title")).strip().lower() != title_n:
                continue
            if artists_n and artists_n not in self._safe_text(item.get("artists")).strip().lower():
                continue
            return item
        return {}

    def _details_key(self, data: dict) -> tuple[str, str, str]:
        return (
            self._safe_text(data.get("videoId")).strip(),
            self._safe_text(data.get("title")).strip().lower(),
            self._safe_text(data.get("artists")).strip().lower(),
        )

    def _mark_playing_row(self, title: str, artists: str):
        title_n = title.strip().lower()
        artists_n = artists.strip().lower()
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        }:
            return
        self._playing_mark_revision = self._table_revision
        selected_row = -1
        for row in range(self.table.rowCount()):
            data = self._row_data(row)
            is_playing = self._safe_text(data.get("title")).strip().lower() == title_n
            if is_playing and artists_n:
                is_playing = artists_n in self._safe_text(data.get("artists")).strip().lower()
            data["_playing"] = is_playing
            self._set_row_data(row, data)
            number_item = self.table.item(row, 0)
            if number_item:
                number_item.setText("▶" if is_playing else str(int(data.get("_index", row) or row) + 1))
            if is_playing and selected_row < 0:
                selected_row = row
        self.table.clearSelection()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
            self.table.setCurrentCell(selected_row, 1)

    def _load_now_playing_details(self, data: dict):
        self._detail_request += 1
        token = self._detail_request
        request_key = self._details_key(data)
        self._details_loading_key = request_key

        def worker():
            try:
                ytmod = __import__("actions.ytmusic", fromlist=["get_song_details"])
                result = ytmod.get_song_details(
                    video_id=data.get("videoId", ""),
                    title=data.get("title", ""),
                    artists=data.get("artists", ""),
                    album_id=data.get("albumId", ""),
                    artist_id=data.get("artistId", ""),
                )
                self._result_sig.emit("now_playing_details", {"token": token, "key": request_key, "details": result})
                images = {}
                if result.get("thumbnail"):
                    raw_url = result.get("thumbnail", "")
                    images["thumbnail"] = raw_url
                    images["thumb_b64"] = self._fetch_thumb_b64(raw_url)
                    cached = self._thumb_cache.get(str(raw_url or "").strip())
                    if cached:
                        images["thumb_src"] = self._data_uri(cached)
                        images["thumb_src_url"] = raw_url
                if result.get("artistThumbnail"):
                    raw_url = result.get("artistThumbnail", "")
                    images["artistThumbnail"] = raw_url
                    images["artist_thumb_b64"] = self._fetch_thumb_b64(raw_url)
                    cached = self._thumb_cache.get(str(raw_url or "").strip())
                    if cached:
                        images["artist_thumb_src"] = self._data_uri(cached)
                        images["artist_thumb_src_url"] = raw_url
                if images:
                    self._result_sig.emit("now_playing_images", {"token": token, "key": request_key, "details": images})
            except Exception as exc:
                self._result_sig.emit("now_playing_details", {"token": token, "key": request_key, "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()

    def _render_now_playing(self):
        data = self._now_playing_data or {}
        if not data:
            return
        fp = self._detail_fingerprint(data)
        if fp == self._detail_render_fingerprint:
            return
        scroll = self.details_scroll.verticalScrollBar()
        same_track = self._detail_render_track_key == self._now_playing_key
        previous_scroll = scroll.value() if same_track else 0
        self._set_detail_cover(data)
        self.details.setText(self._render_details_html(data))
        self._detail_render_fingerprint = fp
        self._detail_render_track_key = self._now_playing_key
        if same_track:
            QTimer.singleShot(0, lambda: scroll.setValue(min(previous_scroll, scroll.maximum())))
        else:
            QTimer.singleShot(0, lambda: scroll.setValue(0))

    def _detail_fingerprint(self, data: dict) -> tuple:
        keys = (
            "videoId",
            "title",
            "artists",
            "album",
            "year",
            "duration",
            "thumbnail",
            "thumb_src_url",
            "artistName",
            "artistDescription",
            "artistThumbnail",
            "artist_thumb_src_url",
            "_details_loaded",
        )
        return tuple(self._safe_text(data.get(k)) for k in keys) + (str(self._details_image_width()),)

    def _meta_row(self, label: str, value, href: str = "") -> str:
        shown = self._safe_text(value) or "-"
        rendered = self._esc(shown)
        if href and shown != "-":
            rendered = (
                f"<a href='{html_lib.escape(href, quote=True)}' "
                "style='color:#b6c4ff; text-decoration:none; font-weight:800;'>"
                f"{rendered}</a>"
            )
        return (
            "<tr>"
            f"<td style='color:#7f8ea3; font-size:11px; font-weight:700; padding:7px 16px 7px 0; white-space:nowrap; vertical-align:middle;'>{self._esc(str(label).upper())}</td>"
            f"<td style='color:#f1f5f9; font-size:13px; font-weight:700; padding:7px 0; vertical-align:middle;'>{rendered}</td>"
            "</tr>"
        )

    def _render_details_html(self, data: dict) -> str:
        artist_src = self._image_src(data, "artist_thumb_b64", "artist_thumb_src", "artistThumbnail", "artist_thumb_src_url")
        image_width = self._details_image_width()
        artist_img = self._image_html(artist_src, image_width, "0 0 14px 0")
        artist_name = data.get("artistName") or data.get("artists") or ""
        artist_desc = data.get("artistDescription") or "Cargando información del artista..."
        artist_block = ""
        if artist_name or artist_desc or artist_img:
            artist_block = (
                "<div style='margin-top:26px;'>"
                "<div style='color:#7f8ea3; font-size:11px; font-weight:800; letter-spacing:1px; margin:0 0 12px 0;'>INFORMACIÓN DEL ARTISTA</div>"
                f"{artist_img}"
                "<div style='font-size:17px; font-weight:900; margin:0 0 8px 0;'>"
                f"<a href='music:artist' style='color:#f8fafc; text-decoration:none;'>{self._esc(artist_name)}</a>"
                "</div>"
                f"<p style='color:#b9c0c9; font-size:14px; line-height:1.5; margin:0;'>{self._esc(artist_desc)}</p>"
                "</div>"
            )
        return (
            "<div style='color:#f8fafc;'>"
            f"<h2 style='margin:0 0 3px 0; font-size:22px; line-height:1.15;'>{self._esc(data.get('title'))}</h2>"
            "<div style='font-size:13px; font-weight:700; margin-bottom:16px;'>"
            f"<a href='music:artist' style='color:#9aa7b4; text-decoration:none;'>{self._esc(data.get('artists'))}</a>"
            "</div>"
            "<table cellspacing='0' cellpadding='0' style='margin:2px 0 6px 0;'>"
            f"{self._meta_row('Álbum', data.get('album'), 'music:album')}"
            f"{self._meta_row('Año', data.get('year'))}"
            f"{self._meta_row('Artista', artist_name, 'music:artist')}"
            f"{self._meta_row('Duración', data.get('duration'))}"
            "</table>"
            f"{artist_block}"
            "</div>"
        )

    def _on_details_link(self, link: str):
        target = str(link or "").strip().lower()
        data = dict(self._now_playing_data or {})
        if target == "music:album":
            self._open_album_page(data)
        elif target == "music:artist":
            self._open_artist_page(data)

    def _open_album_page(self, data: dict):
        album_id = self._safe_text(data.get("albumId")).strip()
        album_name = self._safe_text(data.get("album") or data.get("title")).strip()
        if not album_id and not album_name:
            return
        self._show_browse_content()
        self.filter_row.setVisible(False)
        self._set_header(album_name or "Album", "Cargando album...", "Album", data)

        def _load():
            ytmod = __import__("actions.ytmusic", fromlist=["get_album_details"])
            return ytmod.get_album_details(query=album_name, browse_id=album_id)

        self._run("album_page", _load)

    def _open_artist_page(self, data: dict):
        artist_id = self._safe_text(data.get("artistBrowseId") or data.get("artistId")).strip()
        artist_name = self._safe_text(data.get("artistName") or data.get("artists")).strip()
        if not artist_id and not artist_name:
            return
        self.filter_row.setVisible(False)
        self._show_artist_loading(artist_name or "Artista", data)

        def _load():
            ytmod = __import__("actions.ytmusic", fromlist=["get_artist_details"])
            return ytmod.get_artist_details(query=artist_name, browse_id=artist_id)

        self._run("artist_page", _load)

    def _merge_now_playing_details(self, payload: dict, mark_loaded: bool = True):
        if not isinstance(payload, dict):
            return
        payload_key = payload.get("key")
        current_key = self._details_key(self._now_playing_data)
        if payload_key and payload_key != current_key:
            return
        if not payload_key and payload.get("token") != self._detail_request:
            return
        self._details_loading_key = ("", "", "")
        if payload.get("error") and mark_loaded:
            return
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            return
        self._now_playing_data.update({k: v for k, v in details.items() if v not in ("", None, [])})
        if mark_loaded:
            self._now_playing_data["_details_loaded"] = True
        self._render_now_playing()

    def _handle_result(self, op: str, result):
        if op == "now_playing_details":
            self._merge_now_playing_details(result)
            return
        if op == "now_playing_images":
            self._merge_now_playing_details(result, mark_loaded=False)
            return
        if self._artist_page_open and op in {
            "library_playlists", "playlist_tracks", "search_all", "recommendations"
        }:
            return
        if isinstance(result, Exception):
            self.status.setVisible(True)
            self.status.setText("Error")
            self.table.setRowCount(0)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels(["Error"])
            self.table.insertRow(0)
            self.table.setItem(0, 0, QTableWidgetItem(str(result)))
            return
        if op == "album_page":
            data = dict(result or {}) if isinstance(result, dict) else {}
            tracks = list(data.get("tracks") or [])
            self._current_playlist = None
            self._show_songs(tracks, table_kind="album_tracks")
            title = self._safe_text(data.get("title")) or "Album"
            artists = self._safe_text(data.get("artists"))
            year = self._safe_text(data.get("year"))
            subtitle = " - ".join(part for part in (artists, year, f"{len(tracks)} canciones") if part)
            self._set_header(title, subtitle, "Album", data)
            return
        if op == "artist_page":
            data = dict(result or {}) if isinstance(result, dict) else {}
            self._current_playlist = None
            self._render_artist_page(data)
            return
        if op == "recommendations":
            self.filter_row.setVisible(False)
            recs = list(result or [])
            self._current_playlist = None
            self._show_songs(recs, table_kind="recommendations")
            self._set_header("Recomendaciones", "Hechas para ti según tu actividad", "Recomendaciones", {})
            if not recs:
                self.status.setVisible(True)
                self.status.setText("No hay recomendaciones disponibles. Inicia sesión en YouTube Music para verlas.")
            return
        if op == "library_playlists":
            self.filter_row.setVisible(False)
            self._show_playlists(list(result or []))
            return
        if op == "playlist_tracks":
            self.filter_row.setVisible(False)
            self._show_songs(list(result or []), table_kind="playlist_tracks", playlist=self._current_playlist or {})
            return
        if op == "search_all":
            if not isinstance(result, dict):
                result = {}
            self._search_results = {
                "songs": list(result.get("songs") or []),
                "playlists": list(result.get("playlists") or []),
                "artists": list(result.get("artists") or []),
            }
            self.filter_row.setVisible(True)
            if self._search_results["songs"]:
                self._show_search_filter("songs")
            elif self._search_results["playlists"]:
                self._show_search_filter("playlists")
            else:
                self._show_search_filter("artists")


_FILE_ICONS = {
    "image":   ("image", C.ACC), "video":   ("video", C.ACC2),
    "audio":   ("audio", C.ACC), "pdf":     ("file", C.RED),
    "word":    ("file", C.ACC2), "excel":   ("chart", C.ACC),
    "code":    ("code", C.ACC2), "archive": ("archive", C.ACC2),
    "pptx":    ("chart", C.RED), "text":     ("file", C.TEXT_DIM),
    "data":    ("chart", C.ACC), "unknown":  ("file", C.TEXT_DIM),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0, qcol("#FFFFFF", 34 if z._hovering or z._drag_over else 22))
        bg.setColorAt(1, qcol("#FFFFFF", 12))
        p.setBrush(QBrush(bg)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 16, 16)

        if z._current_file:   border_col = qcol(C.ACC, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.3, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 16, 16)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here or click to browse")
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        icon_pm = _line_icon("upload", C.PRI, 24).pixmap(24, 24)
        p.drawPixmap(int(cx - 12), int(cy - 24), icon_pm)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        icon_pm = _line_icon(icon, icon_col, 30).pixmap(30, 30)
        p.drawPixmap(
            int(block_x + (block_w - 30) / 2),
            int((H - 30) / 2),
            icon_pm,
        )

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont(FONT_UI, 6))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        close_pm = _line_icon("close", C.RED, 18).pixmap(18, 18)
        p.drawPixmap(W - 29, int((H - 18) / 2), close_pm)

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


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


class _CommandInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._draft: str = ""

    def add_history(self, text: str):
        text = str(text or "").strip()
        if not text:
            return
        if self._history and self._history[-1] == text:
            self._history_idx = None
            self._draft = ""
            return
        self._history.append(text)
        self._history_idx = None
        self._draft = ""

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._history:
            if self._history_idx is None:
                if key == Qt.Key.Key_Up:
                    self._draft = self.text()
                    self._history_idx = len(self._history) - 1
                else:
                    return
            else:
                if key == Qt.Key.Key_Up and self._history_idx > 0:
                    self._history_idx -= 1
                elif key == Qt.Key.Key_Down:
                    if self._history_idx < len(self._history) - 1:
                        self._history_idx += 1
                    else:
                        self._history_idx = None
                        self.setText(self._draft)
                        self.setCursorPosition(len(self.text()))
                        return
            if self._history_idx is not None:
                value = self._history[self._history_idx]
                self.setText(value)
                self.setCursorPosition(len(value))
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._history_idx = None
            self._draft = ""

        super().keyPressEvent(event)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(15, 23, 42, 235);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 22px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(FONT_UI, font_size,
                            QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("Initialisation Required", 15, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont(FONT_UI, 10))
        self._key_input.setFixedHeight(38)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.035); color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.080); border-radius: 14px; padding: 6px 12px;
            }}
            QLineEdit:focus {{ border: 1px solid rgba(182, 196, 255, 0.42); }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("Initialise Systems")
        init_btn.setFont(QFont(FONT_UI, 10, QFont.Weight.DemiBold))
        init_btn.setFixedHeight(40)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(182, 196, 255, 0.16); color: {C.PRI};
                border: 1px solid rgba(182, 196, 255, 0.32); border-radius: 15px;
            }}
            QPushButton:hover {{
                background: rgba(182, 196, 255, 0.25); border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#161c38"),"mac":(C.ACC,"#161c38"),"linux":(C.ACC,"#161c38")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 14px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(255, 255, 255, 0.06); color: {C.TEXT_DIM};
                        border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 14px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid rgba(182, 196, 255, 0.34); }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


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


class FlowLayout(QLayout):
    """Reflowing layout: items wrap to the next row based on available width."""

    def __init__(self, parent=None, margin=0, hspacing=16, vspacing=18):
        super().__init__(parent)
        self._items: list = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        right = rect.right() - margins.right()
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._hspace
            if next_x - self._hspace > right and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + self._vspace
                next_x = x + w + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height - rect.y() + margins.bottom()


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



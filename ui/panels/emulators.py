"""Emuladores + Mi biblioteca — the two tabs that sit beside Juegos.

`EmulatorsModePanel` is a ROM storefront *and* the emulator itself: search and
browse a console catalogue (actions/rom_catalog.py), download a ROM, then play
it on a third stack page that renders inside this panel. The game runs on a
libretro core driven by actions/libretro.py and painted by
ui/widgets/retro.py — no second window, no external process.

`LibraryModePanel` is the shared shelf: it lists everything from
actions/game_library.py — downloaded ROMs *and* installed PC games — so both
halves of the Juegos mode end up in one place.

Neither panel derives from MoviesModePanel: that base class carries a whole
VLC playback stack (player page, hover controls, floating window) that has no
meaning here, so these are plain QWidgets that reuse only the poster helpers.

Threading follows the Movies/Games convention — network work runs on a daemon
thread or the shared pool and comes back through a signal, never touching
widgets directly.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from concurrent.futures import ThreadPoolExecutor

from ..theme import *
from ..icons import *
from ..widgets.retro import RetroScreen, keymap_help
from ..widgets.controls import ControlsDialog
from ..widgets.layouts import FlowLayout
from actions.perf_helpers import SharedThreadPool, DiskImageCache
from .movies import _download_image, _round_pixmap

_ART_CACHE = DiskImageCache("rom_art")

# A ROM grid shows far more covers at once than a movie rail does, and these
# fetches are latency-bound rather than CPU-bound. They get their own pool so a
# 90-result search cannot monopolise the app-wide SharedThreadPool that Movies,
# Music and the rest also queue on.
_ART_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="jarvis_rom_art")


def _emit(signal, *args) -> None:
    """Emit from a worker thread, tolerating a panel the user already left.

    Switching mode deletes the panel's C++ object while its network thread is
    still in flight; the plain ``signal.emit`` then raises RuntimeError inside
    the thread instead of quietly going nowhere.
    """
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


# Card geometry. GBA boxart is roughly square, so the art slot is far less
# tall than the 148x200 poster used for films.
_CARD_W, _ART_H = 152, 152


def _panel_qss() -> str:
    return f"""
        QWidget#EmuRoot {{ background:#071115; }}
        QScrollArea {{ background:transparent; border:none; }}
        QScrollBar:vertical {{ background:transparent; width:8px; margin:0; }}
        QScrollBar::handle:vertical {{
            background:#2f3d43; border-radius:4px; min-height:40px;
        }}
        QScrollBar::handle:vertical:hover {{ background:#3a4a52; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background:transparent;
        }}
    """


def _chip_qss() -> str:
    return """
        QPushButton {
            background:#273035; color:#ffffff; border:none; border-radius:18px;
            padding:0 18px; font-size:9pt; font-weight:600; font-family:Inter;
        }
        QPushButton:checked { background:#ffffff; color:#111820; }
        QPushButton:hover:!checked { background:#2f3d43; }
    """


def _primary_btn_qss() -> str:
    return f"""
        QPushButton {{
            background:{C.PRI_DIM}; color:{C.DARK}; border:none;
            border-radius:8px; font-weight:bold;
        }}
        QPushButton:hover {{ background:{C.PRI}; }}
        QPushButton:disabled {{ background:{C.PANEL2}; color:{C.TEXT_MED}; }}
    """


def _ghost_btn_qss() -> str:
    return f"""
        QPushButton {{
            background:{C.GLASS}; color:{C.TEXT}; border:1px solid {C.BORDER_A};
            border-radius:8px; padding:0 16px; font-weight:600;
        }}
        QPushButton:hover {{ border-color:{C.PRI}; color:{C.PRI}; }}
    """


class _ArtCard(QWidget):
    """Clickable cover card whose artwork loads off the UI thread.

    _MovieCard (movies.py) downloads its poster inside __init__, which is fine
    for a 14-card rail but would freeze the UI for the 60-card grid this panel
    shows, so this is a separate, fully async card.
    """

    clicked = pyqtSignal(object)
    context_requested = pyqtSignal(object, QPoint)
    _art_ready = pyqtSignal(QImage)

    def __init__(self, item, subtitle: str = "", badge: str = "", parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedWidth(_CARD_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.context_requested.emit(self.item, self.mapToGlobal(pos))
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(6)

        self._art = QLabel()
        self._art.setFixedSize(_CARD_W, _ART_H)
        self._art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._art.setStyleSheet("background:#1a2226; border-radius:12px;")
        self._art.setText("🎮")
        self._art.setFont(QFont(FONT_UI, 22))
        lay.addWidget(self._art)

        title = QLabel(getattr(item, "title", ""))
        title.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        title.setStyleSheet("color:#f4f4f2; background:transparent;")
        title.setWordWrap(True)
        title.setFixedHeight(32)
        title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        title.setToolTip(getattr(item, "title", ""))
        lay.addWidget(title)

        meta = QLabel(subtitle)
        meta.setFont(QFont("Inter", 8))
        meta.setStyleSheet("color:#9aa6ab; background:transparent;")
        meta.setFixedHeight(14)
        lay.addWidget(meta)

        self._badge = badge
        self._art_ready.connect(self._on_art_ready)
        self._urls = [
            url for url in (
                getattr(item, "poster_url", ""),
                getattr(item, "header_url", ""),
                getattr(item, "backdrop_url", ""),
                getattr(item, "thumb_url", ""),
            ) if url
        ]
        self._art_requested = False

    def ensure_art(self):
        """Start the cover download — called when the card scrolls into view.

        Fetching in __init__ instead would mean a 90-result search downloading
        90 covers, most of them for rows the user never scrolls to.
        """
        if self._art_requested or not self._urls:
            return
        self._art_requested = True
        _ART_POOL.submit(self._fetch_art, self._urls)

    def _fetch_art(self, urls: list[str]):
        for url in urls:
            try:
                data = _download_image(url, timeout=12, cache=_ART_CACHE)
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    _emit(self._art_ready, img)
                    return
            except Exception:
                continue

    def _on_art_ready(self, img: QImage):
        try:
            # Box fronts, title screens and in-game snaps have wildly different
            # aspect ratios, so fit-inside (not crop-to-fill) keeps every piece
            # of art intact instead of slicing the sides off wide shots.
            scaled = QPixmap.fromImage(img).scaled(
                _CARD_W - 8, _ART_H - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            canvas = QPixmap(_CARD_W, _ART_H)
            canvas.fill(QColor("#1a2226"))
            painter = QPainter(canvas)
            painter.drawPixmap(
                (_CARD_W - scaled.width()) // 2,
                (_ART_H - scaled.height()) // 2,
                scaled,
            )
            if self._badge:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(6, 8, 18, 205))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(8, 8, 46, 20), 10, 10)
                painter.setPen(QColor(C.PRI))
                painter.setFont(QFont("Inter", 7, QFont.Weight.Bold))
                painter.drawText(QRectF(8, 8, 46, 20),
                                 Qt.AlignmentFlag.AlignCenter, self._badge)
            painter.end()
            self._art.setText("")
            self._art.setPixmap(_round_pixmap(canvas, 12))
        except RuntimeError:
            pass  # card deleted while its art was downloading

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item)


class _CardGrid(QWidget):
    """Responsive wrapping grid of _ArtCards — column count follows width."""

    clicked = pyqtSignal(object)
    context_requested = pyqtSignal(object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(2, 6, 2, 10)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(10)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._cards: list[_ArtCard] = []
        self._columns = 0
        self._viewport: QWidget | None = None

    def attach_viewport(self, scroll: QScrollArea):
        """Watch a scroll area so covers load as their cards come into view."""
        self._viewport = scroll.viewport()
        scroll.verticalScrollBar().valueChanged.connect(self.load_visible_art)

    def load_visible_art(self):
        if self._viewport is None:
            for card in self._cards:
                card.ensure_art()
            return
        # One screen of lookahead, so scrolling at a normal speed still meets
        # covers that have already arrived.
        try:
            visible = self._viewport.rect().adjusted(
                0, -self._viewport.height(), 0, self._viewport.height())
        except RuntimeError:
            return
        for card in self._cards:
            # A queued call from a previous result set can land after those
            # cards were deleted; one dead widget must not abort the loop and
            # leave the rest of the grid without covers.
            try:
                top_left = card.mapTo(self._viewport, QPoint(0, 0))
                if visible.intersects(QRect(top_left, card.size())):
                    card.ensure_art()
            except RuntimeError:
                continue

    def set_items(self, items, subtitle_of=None, badge_of=None):
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        while self._grid.count():
            self._grid.takeAt(0)

        for item in items:
            card = _ArtCard(
                item,
                subtitle=subtitle_of(item) if subtitle_of else "",
                badge=badge_of(item) if badge_of else "",
            )
            card.clicked.connect(self.clicked)
            card.context_requested.connect(self.context_requested)
            self._cards.append(card)
        self._columns = 0
        self._relayout()
        # Deferred: the cards have no geometry until the layout has run, so a
        # visibility test right here would place every one of them at (0, 0).
        # Checked twice because a single pass that lands before the layout
        # settles would silently leave every cover unloaded, with nothing to
        # retry it until the user happened to scroll or resize.
        QTimer.singleShot(0, self.load_visible_art)
        QTimer.singleShot(150, self.load_visible_art)

    def _column_count(self) -> int:
        usable = max(_CARD_W, self.width() - 8)
        return max(1, usable // (_CARD_W + 14))

    def _relayout(self):
        columns = self._column_count()
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.load_visible_art()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()
        self.load_visible_art()


class _HeaderBar(QWidget):
    """Back button + title pill + search field, matching the Movies header."""

    search_submitted = pyqtSignal(str)
    search_changed = pyqtSignal(str)
    back_clicked = pyqtSignal()

    def __init__(self, title: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.back_btn = QPushButton("←")
        self.back_btn.setFixedSize(40, 40)
        self.back_btn.setVisible(False)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setStyleSheet(
            "background:#273035; border:none; border-radius:12px;"
            "color:#f4f4f2; font-size:13pt;"
        )
        self.back_btn.clicked.connect(self.back_clicked)
        lay.addWidget(self.back_btn)

        self.title = QLabel(title)
        self.title.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        self.title.setFixedHeight(40)
        self.title.setStyleSheet(
            "color:#f4f4f2; background:#273035; border:none;"
            "border-radius:12px; padding:0 16px;"
        )
        lay.addWidget(self.title)

        frame = QFrame()
        frame.setObjectName("EmuSearch")
        frame.setFixedHeight(40)
        frame.setStyleSheet(
            "QFrame#EmuSearch { background:#273035; border:none; border-radius:12px; }"
        )
        frame_lay = QHBoxLayout(frame)
        frame_lay.setContentsMargins(14, 0, 12, 0)
        frame_lay.setSpacing(8)

        glyph = QLabel("⌕")
        glyph.setFont(QFont("Inter", 13))
        glyph.setStyleSheet("color:#9aa6ab; background:transparent;")
        frame_lay.addWidget(glyph)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setFrame(False)
        self.search.setStyleSheet(
            "background:transparent; border:none; color:#f4f4f2;"
            "font-size:10pt; font-family:Inter;"
        )
        palette = self.search.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9aa6ab"))
        self.search.setPalette(palette)
        self.search.returnPressed.connect(
            lambda: self.search_submitted.emit(self.search.text().strip()))
        self.search.textChanged.connect(self.search_changed)
        frame_lay.addWidget(self.search, 1)
        lay.addWidget(frame, 1)


class _ImportPage(QWidget):
    """Fallback page for a console that only supports user-owned local dumps."""

    bios_import_requested = pyqtSignal()
    bios_remove_requested = pyqtSignal()
    rom_import_requested = pyqtSignal()
    play_requested = pyqtSignal(object)  # LibraryEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        self._console_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 8)
        root.setSpacing(16)

        warn = QFrame()
        warn.setStyleSheet(
            "QFrame { background:#2a1414; border:1px solid #6a2a2a;"
            "border-radius:12px; } QLabel { background:transparent; border:none; }"
        )
        warn_lay = QVBoxLayout(warn)
        warn_lay.setContentsMargins(16, 14, 16, 14)
        warn_lay.setSpacing(6)
        warn_title = QLabel("Sin verificar")
        warn_title.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        warn_title.setStyleSheet("color:#ff8a8a;")
        warn_lay.addWidget(warn_title)
        warn_body = QLabel(
            "El core de PS2 es un port reciente y beta de libretro. No he podido "
            "verificar que llegue a arrancar ningún juego — puede quedarse en "
            "pantalla negra o cerrarse. El resto de consolas de este programa sí "
            "están verificadas jugando de verdad; esta no."
        )
        warn_body.setWordWrap(True)
        warn_body.setFont(QFont(FONT_UI, 9))
        warn_body.setStyleSheet("color:#e0b0b0;")
        warn_lay.addWidget(warn_body)
        root.addWidget(warn)

        bios_box = QFrame()
        bios_box.setStyleSheet(
            f"QFrame {{ background:{C.PANEL2}; border-radius:12px; }}"
            "QLabel { background:transparent; border:none; }"
        )
        bios_lay = QVBoxLayout(bios_box)
        bios_lay.setContentsMargins(16, 14, 16, 14)
        bios_lay.setSpacing(8)
        bios_title = QLabel("BIOS")
        bios_title.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        bios_lay.addWidget(bios_title)
        bios_expl = QLabel(
            "PS2 no puede arrancar nada sin el firmware original de Sony. Este "
            "programa no lo trae ni puede descargarlo por ti — es firmware "
            "protegido por copyright. Si tienes tu propia PS2, puedes volcarlo "
            "tú mismo y darlo aquí; el archivo pesa siempre 4 MB exactos."
        )
        bios_expl.setWordWrap(True)
        bios_expl.setFont(QFont(FONT_UI, 9))
        bios_expl.setStyleSheet(f"color:{C.TEXT_MED};")
        bios_lay.addWidget(bios_expl)

        bios_row = QHBoxLayout()
        bios_row.setSpacing(10)
        self._bios_status = QLabel("")
        self._bios_status.setFont(QFont(FONT_UI, 10))
        self._bios_status.setWordWrap(True)
        bios_row.addWidget(self._bios_status, 1)
        self._bios_import_btn = QPushButton("Importar BIOS…")
        self._bios_import_btn.setFixedHeight(36)
        self._bios_import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bios_import_btn.setStyleSheet(_primary_btn_qss())
        self._bios_import_btn.clicked.connect(self.bios_import_requested)
        bios_row.addWidget(self._bios_import_btn)
        self._bios_remove_btn = QPushButton("Quitar")
        self._bios_remove_btn.setFixedHeight(36)
        self._bios_remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bios_remove_btn.setStyleSheet(_ghost_btn_qss())
        self._bios_remove_btn.clicked.connect(self.bios_remove_requested)
        bios_row.addWidget(self._bios_remove_btn)
        bios_lay.addLayout(bios_row)
        root.addWidget(bios_box)

        rom_header = QHBoxLayout()
        rom_title = QLabel("Tus imágenes de disco")
        rom_title.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
        rom_header.addWidget(rom_title)
        rom_header.addStretch(1)
        import_btn = QPushButton("Importar ISO/CHD…")
        import_btn.setFixedHeight(36)
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.setStyleSheet(_primary_btn_qss())
        import_btn.clicked.connect(self.rom_import_requested)
        rom_header.addWidget(import_btn)
        root.addLayout(rom_header)

        rom_hint = QLabel(
            "Solo imágenes que ya tengas — volcadas por ti de discos que posees. "
            "No hay catálogo en línea para PS2: los sets de discos pesan cientos "
            "de GB por región, nada comparable a las otras consolas de aquí."
        )
        rom_hint.setWordWrap(True)
        rom_hint.setFont(QFont(FONT_UI, 9))
        rom_hint.setStyleSheet(f"color:{C.TEXT_MED};")
        root.addWidget(rom_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent; border:none;")
        self._grid = _CardGrid()
        self._grid.clicked.connect(self.play_requested)
        scroll.setWidget(self._grid)
        self._grid.attach_viewport(scroll)
        root.addWidget(scroll, 1)

    def set_console(self, console_id: str):
        self._console_id = console_id
        self.refresh_bios_state()
        self.refresh_library(console_id)

    def refresh_bios_state(self):
        try:
            from actions import bios, emulator_runtime as er
            found = bios.find_ps2_bios(er.system_dir())
        except Exception:
            found = None
        if found is not None:
            self._bios_status.setText(f"✓ BIOS instalada: {found.name}")
            self._bios_status.setStyleSheet(f"color:{C.GREEN};")
            self._bios_remove_btn.setVisible(True)
        else:
            self._bios_status.setText("✗ No hay ninguna BIOS instalada todavía")
            self._bios_status.setStyleSheet(f"color:{C.RED};")
            self._bios_remove_btn.setVisible(False)

    def refresh_library(self, console_id: str):
        try:
            from actions import game_library as gl
            entries = [e for e in gl.list_entries(include_pc=False)
                      if e.kind == "rom" and e.console_id == console_id]
        except Exception:
            entries = []
        self._grid.set_items(
            entries,
            subtitle_of=lambda e: _human_size_label(e.size_bytes),
        )


def _human_size_label(size_bytes: int) -> str:
    size = float(size_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class _FullscreenHost(QWidget):
    """Borderless window that borrows the RetroScreen for fullscreen play.

    The screen widget is *moved* here rather than duplicated: a libretro core
    has one framebuffer, and running a second widget off it would just mean
    painting the same frames twice.
    """

    closed = pyqtSignal()

    def __init__(self, screen: RetroScreen, title: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.setStyleSheet("background:#000000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(screen)
        screen.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class GamePlayerPage(QWidget):
    """The in-panel emulator: screen, transport controls and save handling.

    Save data is split the way the hardware splits it. ``.srm`` is the
    cartridge's battery save — what the game's own "Save" menu writes — and is
    flushed on a timer and on exit, because a core never tells the frontend
    when the game touched it. ``.state`` is a whole-machine snapshot, written
    only when the user asks for one.
    """

    exit_requested = pyqtSignal()
    status = pyqtSignal(str)

    _AUTOSAVE_MS = 30_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        self._core = None
        self._stem = ""
        self._title = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._screen = RetroScreen()
        self._console_id = "gba"
        self._screen.fps_updated.connect(self._on_fps)
        self._screen_slot = QVBoxLayout()
        self._screen_slot.setContentsMargins(0, 0, 0, 0)
        self._screen_slot.addWidget(self._screen)
        root.addLayout(self._screen_slot, 1)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._pause_btn = self._tool("⏸  Pausa", self._toggle_pause)
        controls.addWidget(self._pause_btn)
        controls.addWidget(self._tool("↻  Reiniciar", self._reset))
        controls.addWidget(self._tool("💾  Guardar estado", self._save_state))
        controls.addWidget(self._tool("📂  Cargar estado", self._load_state))

        self._smooth_btn = self._tool("Suavizado", self._toggle_smooth)
        self._smooth_btn.setCheckable(True)
        controls.addWidget(self._smooth_btn)

        controls.addWidget(self._tool("⛶  Pantalla completa", self._go_fullscreen))
        controls.addWidget(self._tool("🎮  Controles", self.open_controls))
        controls.addWidget(self._tool("📱  Mando móvil", self._call_phone_pad))
        controls.addStretch(1)

        self._fps_label = QLabel("")
        self._fps_label.setFont(QFont("Inter", 8))
        self._fps_label.setStyleSheet("color:#5d6b73; background:transparent;")
        controls.addWidget(self._fps_label)

        exit_btn = self._tool("✕  Salir", self._exit)
        exit_btn.setStyleSheet(_primary_btn_qss() + "QPushButton { padding:0 16px; }")
        controls.addWidget(exit_btn)
        root.addLayout(controls)

        self._help_label = QLabel(keymap_help())
        self._help_label.setWordWrap(True)
        self._help_label.setFont(QFont("Inter", 8))
        self._help_label.setStyleSheet("color:#5d6b73; background:transparent;")
        root.addWidget(self._help_label)

        self._fullscreen = None
        self._autosave = QTimer(self)
        self._autosave.setInterval(self._AUTOSAVE_MS)
        self._autosave.timeout.connect(lambda: self._flush_sram(quiet=True))

    def _call_phone_pad(self) -> None:
        """Re-raise the 'use me as a controller' prompt on the paired phone.

        The phone offers itself automatically when a game starts, but once
        dismissed there was no way back to it without restarting the game.
        """
        try:
            from actions import lan_dashboard
            lan_dashboard.bump_gamepad_announce()
            self.status.emit("Aviso enviado al móvil.")
        except Exception as exc:
            self.status.emit(f"No pude avisar al móvil: {exc}")

    def _tool(self, label: str, slot) -> QPushButton:
        button = QPushButton(label)
        button.setFixedHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_ghost_btn_qss())
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def start(self, core, rom_path: str | Path, title: str,
              console_id: str = "gba") -> None:
        self._core = core
        self._console_id = console_id
        self._stem = Path(rom_path).stem
        self._title = title
        self._restore_sram()
        self._screen.attach(core, console_id)
        self._help_label.setText(keymap_help(console_id))
        self._pause_btn.setText("⏸  Pausa")
        self._autosave.start()

    def stop(self) -> None:
        if self._core is None:
            return
        self._autosave.stop()
        self._close_fullscreen()
        self._flush_sram(quiet=True)
        self._screen.detach()
        try:
            from actions import libretro
            libretro.unload()
        except Exception:
            pass
        self._core = None

    def is_active(self) -> bool:
        return self._core is not None

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def pause(self):
        if self._core is None or not self._screen.is_running():
            return
        self._screen.set_paused(True)
        self._pause_btn.setText("▶  Reanudar")
        self._flush_sram(quiet=True)

    def _toggle_pause(self):
        if self._core is None:
            return
        running = self._screen.is_running()
        self._screen.set_paused(running)
        self._pause_btn.setText("▶  Reanudar" if running else "⏸  Pausa")

    def _reset(self):
        if self._core is None:
            return
        self._core.reset()
        self.status.emit(f"«{self._title}» reiniciado")

    def _toggle_smooth(self):
        self._screen.set_smooth(self._smooth_btn.isChecked())

    def _on_fps(self, fps: float):
        self._fps_label.setText(f"{fps:.0f} fps")

    def open_controls(self, console_id: str | None = None):
        """Rebind while the game keeps running — paused so a captured key or
        pad button doesn't also reach the game underneath."""
        was_running = self._screen.is_running()
        if was_running:
            self._screen.set_paused(True)
        profile = console_id or self._console_id
        dialog = ControlsDialog(self, console_id=profile)
        dialog.bindings_changed.connect(self._apply_bindings)
        dialog.exec()
        self._apply_bindings()
        if was_running:
            self._screen.set_paused(False)
            self._pause_btn.setText("⏸  Pausa")

    def _apply_bindings(self):
        self._screen.reload_bindings()
        self._help_label.setText(keymap_help(self._console_id))

    def _go_fullscreen(self):
        if self._core is None or self._fullscreen is not None:
            return
        self._screen_slot.removeWidget(self._screen)
        self._fullscreen = _FullscreenHost(self._screen, self._title, self)
        self._fullscreen.closed.connect(self._close_fullscreen)
        self._fullscreen.showFullScreen()
        self.status.emit("Esc para salir de pantalla completa")

    def _close_fullscreen(self):
        if self._fullscreen is None:
            return
        window, self._fullscreen = self._fullscreen, None
        window.layout().removeWidget(self._screen)
        self._screen.setParent(self)
        self._screen_slot.addWidget(self._screen)
        self._screen.show()
        self._screen.setFocus(Qt.FocusReason.OtherFocusReason)
        window.deleteLater()

    def _exit(self):
        self.stop()
        self.exit_requested.emit()

    # ------------------------------------------------------------------
    # Save data
    # ------------------------------------------------------------------

    def _save_paths(self) -> tuple[Path, Path]:
        from actions import emulator_runtime as er
        folder = er.saves_dir()
        return folder / f"{self._stem}.srm", folder / f"{self._stem}.state"

    def _restore_sram(self):
        if self._core is None:
            return
        srm, _ = self._save_paths()
        try:
            if srm.is_file():
                self._core.write_sram(srm.read_bytes())
        except Exception:
            pass

    def _flush_sram(self, quiet: bool = False):
        if self._core is None:
            return
        srm, _ = self._save_paths()
        try:
            blob = self._core.read_sram()
            if blob and blob.strip(b"\xff") and blob.strip(b"\x00"):
                srm.write_bytes(blob)
                if not quiet:
                    self.status.emit("Partida guardada")
        except Exception as exc:
            if not quiet:
                self.status.emit(f"No pude guardar la partida: {exc}")

    def _save_state(self):
        if self._core is None:
            return
        _, state = self._save_paths()
        try:
            state.write_bytes(self._core.save_state())
            self._flush_sram(quiet=True)
            self.status.emit("Estado guardado")
        except Exception as exc:
            self.status.emit(f"No pude guardar el estado: {exc}")

    def _load_state(self):
        if self._core is None:
            return
        _, state = self._save_paths()
        if not state.is_file():
            self.status.emit("No hay ningún estado guardado para este juego")
            return
        try:
            self._core.load_state(state.read_bytes())
            self.status.emit("Estado cargado")
        except Exception as exc:
            self.status.emit(f"No pude cargar el estado: {exc}")


class EmulatorsModePanel(QWidget):
    """ROM catalogue browser, downloader and embedded player."""

    _results_ready = pyqtSignal(list, str, str)     # (roms, header, error)
    _status_sig = pyqtSignal(str)
    _emu_state_sig = pyqtSignal(dict)
    _dl_progress = pyqtSignal(str, float, str)      # (rom stem, fraction, label)
    _dl_finished = pyqtSignal(str, str, str)        # (rom stem, path, error)
    _detail_art_sig = pyqtSignal(str, QImage)       # (rom stem, artwork)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EmuRoot")
        self._console_id = "gba"
        self._region = ""
        self._detail_rom = None
        self._search_token = 0
        self._active_downloads: set[str] = set()

        self._build_ui()
        self._results_ready.connect(self._on_results)
        self._status_sig.connect(self._set_status)
        self._emu_state_sig.connect(self._apply_emulator_state)
        self._dl_progress.connect(self._on_dl_progress)
        self._dl_finished.connect(self._on_dl_finished)
        self._detail_art_sig.connect(self._apply_detail_art)

        QTimer.singleShot(0, self._refresh_emulator_state)
        QTimer.singleShot(0, self._load_popular)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setStyleSheet(_panel_qss())
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        self._header = _HeaderBar("Emuladores", "Buscar ROMs…")
        self._header.search_submitted.connect(self._do_search)
        self._header.search_changed.connect(self._queue_search)
        self._header.back_clicked.connect(self._on_back)
        root.addWidget(self._header)

        # Emulator status strip — only visible when something needs doing.
        self._emu_bar = QFrame()
        self._emu_bar.setObjectName("EmuBar")
        self._emu_bar.setStyleSheet(
            "QFrame#EmuBar { background:#0b1b27; border:1px solid #1b4a69;"
            "border-radius:12px; } QLabel { background:transparent; border:none; }"
        )
        bar_lay = QHBoxLayout(self._emu_bar)
        bar_lay.setContentsMargins(14, 10, 10, 10)
        bar_lay.setSpacing(10)
        self._emu_label = QLabel("Comprobando emulador…")
        self._emu_label.setFont(QFont(FONT_UI, 10))
        self._emu_label.setStyleSheet(f"color:{C.TEXT_MED};")
        self._emu_label.setWordWrap(True)
        bar_lay.addWidget(self._emu_label, 1)
        self._emu_btn = QPushButton("Instalar emulador")
        self._emu_btn.setFixedHeight(34)
        self._emu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._emu_btn.setStyleSheet(_primary_btn_qss())
        self._emu_btn.setMinimumWidth(170)
        self._emu_btn.clicked.connect(self._install_emulator)
        self._emu_btn.setVisible(False)
        bar_lay.addWidget(self._emu_btn)
        root.addWidget(self._emu_bar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")

        # -- Grid page ---------------------------------------------------
        grid_page = QWidget()
        grid_page.setStyleSheet("background:transparent;")
        grid_lay = QVBoxLayout(grid_page)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(8)

        console_row = QHBoxLayout()
        console_row.setSpacing(8)
        from actions import rom_catalog as rc

        # A wrapping layout, not a plain row: the console list is long enough
        # to overflow the panel on a narrow window, and chips pushed off the
        # right edge would be unreachable.
        chips_holder = QWidget()
        chips_holder.setStyleSheet("background:transparent;")
        chips_flow = FlowLayout(chips_holder, margin=0, hspacing=8, vspacing=8)
        self._console_chips: dict[str, QPushButton] = {}
        for console in rc.CONSOLES.values():
            chip = self._chip(console.short, checked=(console.id == self._console_id))
            chip.clicked.connect(lambda _=False, cid=console.id: self._set_console(cid))
            self._console_chips[console.id] = chip
            chips_flow.addWidget(chip)
        console_row.addWidget(chips_holder, 1)

        controls_btn = QPushButton("🎮  Controles")
        controls_btn.setFixedHeight(34)
        controls_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_btn.setStyleSheet(_ghost_btn_qss())
        controls_btn.clicked.connect(self._open_controls)
        console_row.addWidget(controls_btn, 0, Qt.AlignmentFlag.AlignTop)
        grid_lay.addLayout(console_row)

        region_row = QHBoxLayout()
        region_row.setSpacing(8)
        self._region_chips: dict[str, QPushButton] = {}
        for label, value in (("Todas", ""), ("USA", "USA"), ("Europa", "Europa"),
                             ("Japón", "Japón"), ("España", "España")):
            chip = self._chip(label, checked=(value == ""))
            chip.clicked.connect(lambda _=False, v=value: self._set_region(v))
            self._region_chips[value] = chip
            region_row.addWidget(chip)
        region_row.addStretch(1)
        grid_lay.addLayout(region_row)

        self._results_label = QLabel("")
        self._results_label.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        self._results_label.setStyleSheet("color:#edf6ff; background:transparent;")
        grid_lay.addWidget(self._results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent; border:none;")
        self._grid = _CardGrid()
        self._grid.clicked.connect(self._show_rom_detail)
        scroll.setWidget(self._grid)
        self._grid.attach_viewport(scroll)
        self._grid_scroll = scroll
        grid_lay.addWidget(scroll, 1)
        self._stack.addWidget(grid_page)

        # -- Detail page -------------------------------------------------
        detail_page = QWidget()
        detail_page.setStyleSheet("background:transparent;")
        detail_outer = QVBoxLayout(detail_page)
        detail_outer.setContentsMargins(0, 0, 0, 0)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail_scroll.setStyleSheet("background:transparent; border:none;")
        detail_content = QWidget()
        detail_content.setStyleSheet("background:transparent;")
        self._detail_layout = QVBoxLayout(detail_content)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_scroll.setWidget(detail_content)
        detail_outer.addWidget(detail_scroll)
        self._stack.addWidget(detail_page)

        # -- Player page (the emulator itself) ---------------------------
        self._player = GamePlayerPage()
        self._player.exit_requested.connect(self._on_back)
        self._player.status.connect(self._set_status)
        self._stack.addWidget(self._player)

        # -- Import page (consoles with no online catalogue, e.g. PS2) ---
        # Appended last (index 3) rather than inserted earlier so the
        # existing 0/1/2 = grid/detail/player indices used throughout this
        # class don't have to change.
        self._import_page = _ImportPage()
        self._import_page.bios_import_requested.connect(self._import_bios)
        self._import_page.bios_remove_requested.connect(self._remove_bios)
        self._import_page.rom_import_requested.connect(self._import_local_rom)
        self._import_page.play_requested.connect(
            lambda entry: self.play_file(entry.path, entry.title, entry.console_id))
        self._stack.addWidget(self._import_page)

        root.addWidget(self._stack, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{C.TEXT_MED}; background:transparent; font-size:11px;")
        root.addWidget(self._status)

    def _chip(self, label: str, checked: bool = False) -> QPushButton:
        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(34)
        button.setStyleSheet(_chip_qss())
        return button

    # ------------------------------------------------------------------
    # Emulator state
    # ------------------------------------------------------------------

    def _refresh_emulator_state(self):
        def work():
            try:
                from actions import emulator_runtime as er
                _emit(self._emu_state_sig, er.core_status(self._console_id))
            except Exception as exc:
                _emit(self._emu_state_sig, {"available": False, "error": str(exc)})

        self._run_async(work)

    def _apply_emulator_state(self, state: dict):
        self._emu_state = state
        if state.get("available"):
            self._emu_bar.setVisible(False)
            self._emu_btn.setVisible(False)
        else:
            self._emu_label.setText(
                f"Falta el emulador de {self._console_name()}. Jarvis descarga "
                "el core mGBA (libretro, ~3 MB) y los juegos corren dentro de "
                "esta ventana."
            )
            self._emu_btn.setEnabled(True)
            self._emu_btn.setText("Instalar emulador")
            self._emu_btn.setVisible(True)
            self._emu_bar.setVisible(True)

    def _console_name(self) -> str:
        from actions import rom_catalog as rc
        console = rc.CONSOLES.get(self._console_id)
        return console.name if console else self._console_id.upper()

    def _install_emulator(self):
        self._emu_btn.setEnabled(False)
        self._emu_btn.setText("Descargando…")

        def work():
            try:
                from actions import emulator_runtime as er

                def progress(fraction, done, total):
                    _emit(self._status_sig,
                          f"Descargando emulador… {fraction * 100:.0f}%")

                er.install_core(self._console_id, progress=progress)
                _emit(self._status_sig, "Emulador listo")
                _emit(self._emu_state_sig, er.core_status(self._console_id))
            except Exception as exc:
                _emit(self._status_sig, f"No pude instalar el emulador: {exc}")
                _emit(self._emu_state_sig, {"available": False})

        self._run_async(work)

    # ------------------------------------------------------------------
    # Browsing
    # ------------------------------------------------------------------

    def _run_async(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _set_status(self, text: str):
        self._status.setText(text)

    def _set_console(self, console_id: str):
        for cid, chip in self._console_chips.items():
            chip.setChecked(cid == console_id)
        self._console_id = console_id
        # Otherwise a status line from the console just left behind (its own
        # "Cargando catálogo…", a stale error) lingers on screen under a
        # completely different console's page until something else happens to
        # overwrite it.
        self._set_status("")
        has_catalog = self._console_has_catalog()
        # A search box that does nothing (no online catalogue to search) is a
        # worse experience than one that's visibly not there.
        self._header.search.setEnabled(has_catalog)
        self._header.search.setPlaceholderText(
            "Buscar ROMs…" if has_catalog else "Sin catálogo en línea para esta consola")
        self._refresh_emulator_state()
        self._reload()

    def _set_region(self, region: str):
        for value, chip in self._region_chips.items():
            chip.setChecked(value == region)
        self._region = region
        self._reload()

    def _reload(self):
        if not self._console_has_catalog():
            self._show_grid_view()
            return
        query = self._header.search.text().strip()
        if query:
            self._do_search(query)
        else:
            self._load_popular()

    def _load_popular(self):
        if not self._console_has_catalog():
            # Reachable at construction time via the deferred initial-load
            # timer racing a console switch, not just through _reload()'s own
            # guard — stamping a "loading" status here for a console with
            # nothing to load would outlive the import page taking over.
            return
        self._set_status("Cargando catálogo…")
        self._search_token += 1
        token = self._search_token

        def work():
            try:
                from actions import rom_catalog as rc
                roms = rc.popular(self._console_id, limit=60)
                if self._region:
                    roms = [r for r in roms if rc.matches_region(r, self._region)]
                    if not roms:
                        roms = rc.search("", self._console_id, self._region, limit=60)
                if token != self._search_token:
                    return
                _emit(self._results_ready, roms, f"Destacados de {self._console_name()}", "")
            except Exception as exc:
                _emit(self._results_ready, [], "", str(exc))

        self._run_async(work)

    def _queue_search(self, text: str):
        self._search_token += 1
        token = self._search_token
        query = text.strip()
        # Debounced so a fast typist doesn't rank the 3.5k-entry index once per
        # keystroke; an emptied box falls back to the featured shelf.
        QTimer.singleShot(
            280, lambda: self._do_search(query) if token == self._search_token else None
        )

    def _do_search(self, query: str = ""):
        query = (query or self._header.search.text()).strip()
        if not query:
            self._load_popular()
            return
        self._search_token += 1
        token = self._search_token
        self._set_status(f"Buscando «{query}»…")

        def work():
            try:
                from actions import rom_catalog as rc
                roms = rc.search(query, self._console_id, self._region, limit=90)
                if token != self._search_token:
                    return
                _emit(self._results_ready, roms, f"Resultados para «{query}»", "")
            except Exception as exc:
                _emit(self._results_ready, [], "", str(exc))

        self._run_async(work)

    def _on_results(self, roms: list, header: str, error: str):
        if not self._console_has_catalog():
            # A search/popular-shelf load for a no-catalogue console (only
            # started because it raced the user switching to it — this
            # console always returns [] by design) has nothing useful to say;
            # the import page owns all messaging here, and stamping "Sin
            # resultados" over it would look like a real search failure.
            return
        self._show_grid_view()
        if error:
            self._set_status(error)
            self._results_label.setText("No pude cargar el catálogo")
            self._grid.set_items([])
            return
        self._results_label.setText(f"{header}  ·  {len(roms)}")
        self._set_status("" if roms else "Sin resultados")
        self._grid.set_items(
            roms,
            subtitle_of=lambda r: " · ".join(filter(None, [
                r.region, r.size_label,
                "No disponible" if not getattr(r, "available", True) else "",
            ])),
            badge_of=lambda r: "EN DISCO" if self._is_downloaded(r) else "",
        )
        self._grid_scroll.verticalScrollBar().setValue(0)

    @staticmethod
    def _is_downloaded(rom) -> bool:
        try:
            from actions import rom_catalog as rc
            return rc.local_path(rom) is not None
        except Exception:
            return False

    def _on_back(self):
        """Contextual back: leaving the player returns to the game's ficha."""
        if self._stack.currentIndex() == 2:
            self._close_player()
            if self._detail_rom is not None:
                self._show_rom_detail(self._detail_rom)
                return
        self._show_grid_view()

    def _show_grid_view(self):
        # A running game outranks anything that wants to show the grid. Late
        # catalogue results and background refreshes both land here, and either
        # one yanking the user out mid-game (or killing the core) is never what
        # was meant. Leaving the player is deliberate and goes through
        # _on_back / the Salir button, which close it first.
        if self._player.is_active():
            return
        self._header.back_btn.setVisible(False)
        self._header.title.setText("Emuladores")
        if self._console_has_catalog():
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(3)
            self._import_page.set_console(self._console_id)

    def _console_has_catalog(self) -> bool:
        from actions import rom_catalog as rc
        console = rc.CONSOLES.get(self._console_id)
        return console is None or console.has_catalog

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def _show_rom_detail(self, rom):
        self._detail_rom = rom
        self._header.back_btn.setVisible(True)
        self._header.title.setText(rom.title)
        self._stack.setCurrentIndex(1)
        self._render_detail(rom)

    def _render_detail(self, rom):
        from actions import rom_catalog as rc

        _clear_layout(self._detail_layout)

        page = QWidget()
        page.setStyleSheet("background:transparent;")
        root = QVBoxLayout(page)
        root.setSpacing(16)
        root.setContentsMargins(0, 4, 0, 8)

        columns = QHBoxLayout()
        columns.setSpacing(20)

        art = QLabel()
        art.setFixedSize(260, 260)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setStyleSheet(f"background:{C.PANEL2}; border-radius:14px;")
        art.setText("🎮")
        art.setFont(QFont(FONT_UI, 34))
        self._detail_art = art
        self._load_detail_art(rom)
        columns.addWidget(art, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(8)

        title = QLabel(rom.title)
        title.setWordWrap(True)
        title.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        title.setStyleSheet("color:#f5fbff; background:transparent;")
        info.addWidget(title)

        versions = rc.versions_for(rom)
        version_title = QLabel(
            "Versión del juego" if len(versions) > 1 else "Versión disponible"
        )
        version_title.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        version_title.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
        info.addWidget(version_title)

        self._version_combo = QComboBox()
        self._version_combo.setFixedHeight(38)
        self._version_combo.setMinimumWidth(430)
        self._version_combo.setStyleSheet(f"""
            QComboBox {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER_A}; border-radius:8px;
                padding:0 12px;
            }}
            QComboBox::drop-down {{ border:none; width:28px; }}
            QComboBox QAbstractItemView {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER_A}; selection-background-color:{C.PRI_DIM};
            }}
        """)
        selected_index = 0
        for index, candidate in enumerate(versions):
            self._version_combo.addItem(rc.version_label(candidate), candidate)
            if (candidate.stem == rom.stem
                    and candidate.download_url == rom.download_url):
                selected_index = index
        self._version_combo.setCurrentIndex(selected_index)
        self._version_combo.currentIndexChanged.connect(self._select_detail_version)
        info.addWidget(self._version_combo)

        if not getattr(rom, "available", True):
            warning = QLabel(
                "Esta edición figura en el catálogo, pero su archivo está "
                "restringido en la fuente. Elige otra versión disponible."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "color:#f2c879; background:#261f14; border:1px solid #5b4826;"
                "border-radius:8px; padding:9px 11px;"
            )
            info.addWidget(warning)

        for label, value in (
            ("Consola", self._console_name()),
            ("Región", rom.region),
            ("Idiomas", ", ".join(rom.languages)),
            ("Revisión", rom.revision),
            ("Tamaño", rom.size_label),
            ("Archivo", rom.stem),
        ):
            if not value:
                continue
            row = QLabel(f"<b>{label}:</b> {value}")
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setFont(QFont(FONT_UI, 10))
            row.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
            row.setWordWrap(True)
            info.addWidget(row)

        info.addSpacing(6)

        self._dl_bar = QProgressBar()
        self._dl_bar.setFixedHeight(8)
        self._dl_bar.setTextVisible(False)
        self._dl_bar.setRange(0, 1000)
        self._dl_bar.setVisible(False)
        self._dl_bar.setStyleSheet(f"""
            QProgressBar {{ background:{C.PANEL2}; border:none; border-radius:4px; }}
            QProgressBar::chunk {{ background:{C.PRI_DIM}; border-radius:4px; }}
        """)
        info.addWidget(self._dl_bar)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self._action_btn = QPushButton()
        self._action_btn.setFixedHeight(42)
        self._action_btn.setMinimumWidth(200)
        self._action_btn.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(_primary_btn_qss())
        actions_row.addWidget(self._action_btn)

        self._folder_btn = QPushButton("Abrir carpeta")
        self._folder_btn.setFixedHeight(42)
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.setStyleSheet(_ghost_btn_qss())
        self._folder_btn.clicked.connect(self._open_roms_folder)
        actions_row.addWidget(self._folder_btn)
        actions_row.addStretch(1)
        info.addLayout(actions_row)
        info.addStretch(1)

        columns.addLayout(info, 1)
        root.addLayout(columns)
        root.addStretch(1)
        self._detail_layout.addWidget(page)

        self._sync_action_button()

    def _select_detail_version(self, index: int):
        combo = getattr(self, "_version_combo", None)
        selected = combo.itemData(index) if combo is not None else None
        if selected is None or selected is self._detail_rom:
            return
        self._detail_rom = selected
        self._header.title.setText(selected.title)
        self._render_detail(selected)

    def _load_detail_art(self, rom):
        urls = [u for u in (rom.poster_url, rom.header_url, rom.backdrop_url) if u]
        stem = rom.stem

        def work():
            for url in urls:
                try:
                    data = _download_image(url, timeout=12, cache=_ART_CACHE)
                    img = QImage()
                    img.loadFromData(data)
                    if not img.isNull():
                        # QImage decodes fine off-thread; QPixmap does not, so
                        # the conversion happens in the slot on the UI thread.
                        _emit(self._detail_art_sig, stem, img)
                        return
                except Exception:
                    continue

        SharedThreadPool().submit(work)

    def _apply_detail_art(self, stem: str, img: QImage):
        if self._detail_rom is None or self._detail_rom.stem != stem:
            return  # a different ROM's detail is on screen now
        try:
            scaled = QPixmap.fromImage(img).scaled(
                252, 252, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._detail_art.setText("")
            self._detail_art.setPixmap(scaled)
        except RuntimeError:
            pass

    def _sync_action_button(self):
        rom = self._detail_rom
        if rom is None:
            return
        try:
            from actions import rom_catalog as rc
            path = rc.local_path(rom)
        except Exception:
            path = None

        button = self._action_btn
        # The button switches roles (download <-> play) in place, so the old
        # handler has to go or a second click would re-run the previous action.
        try:
            button.clicked.disconnect()
        except TypeError:
            pass

        if rom.stem in self._active_downloads:
            button.setText("Descargando…")
            button.setEnabled(False)
            return

        if not getattr(rom, "available", True):
            button.setText("No disponible en esta fuente")
            button.setToolTip(getattr(rom, "unavailable_reason", ""))
            button.setEnabled(False)
            return

        button.setEnabled(True)
        button.setToolTip("")
        if path is not None:
            button.setText("▶  Jugar")
            button.clicked.connect(lambda: self._play_rom(rom))
        else:
            button.setText("⬇  Descargar ROM")
            button.clicked.connect(lambda: self._download_rom(rom))

    def _open_roms_folder(self):
        try:
            from actions import rom_catalog as rc
            from actions import game_library as gl
            folder = rc.roms_dir(self._console_id)
            gl.open_location(gl.LibraryEntry(entry_id="", title="", kind="rom",
                                             path=str(folder)))
        except Exception as exc:
            self._set_status(f"No pude abrir la carpeta: {exc}")

    # ------------------------------------------------------------------
    # Download / play
    # ------------------------------------------------------------------

    def _download_rom(self, rom):
        if rom.stem in self._active_downloads:
            return
        self._active_downloads.add(rom.stem)
        self._sync_action_button()
        self._dl_bar.setVisible(True)
        self._dl_bar.setValue(0)

        def work():
            try:
                from actions import rom_catalog as rc
                from actions import game_library as gl

                def progress(fraction, done, total):
                    _emit(
                        self._dl_progress, rom.stem, fraction,
                        f"{done / 1048576:.1f} / {total / 1048576:.1f} MB"
                        if total else f"{done / 1048576:.1f} MB",
                    )

                path = rc.download(rom, progress=progress)
                gl.add_rom(rom, path)
                _emit(self._dl_finished, rom.stem, str(path), "")
            except Exception as exc:
                _emit(self._dl_finished, rom.stem, "", str(exc))

        self._run_async(work)

    def _on_dl_progress(self, stem: str, fraction: float, label: str):
        if self._detail_rom is None or self._detail_rom.stem != stem:
            return
        try:
            self._dl_bar.setValue(int(fraction * 1000))
        except RuntimeError:
            return
        self._set_status(f"Descargando «{stem}» — {fraction * 100:.0f}%  ({label})")

    def _on_dl_finished(self, stem: str, path: str, error: str):
        self._active_downloads.discard(stem)
        if error:
            self._set_status(f"Error descargando «{stem}»: {error}")
        else:
            self._set_status(f"ROM lista: {path}")
        if self._detail_rom is not None and self._detail_rom.stem == stem:
            try:
                self._dl_bar.setVisible(bool(error))
            except RuntimeError:
                pass
            self._sync_action_button()

    def _play_rom(self, rom):
        from actions import rom_catalog as rc

        path = rc.local_path(rom)
        if path is None:
            self._download_rom(rom)
            return
        self.play_file(path, rom.title, self._console_id)

    def play_file(self, rom_path, title: str, console_id: str = "gba"):
        """Boot a ROM on the embedded core. Entry point for the library tab too."""
        from actions import emulator_runtime as er
        from actions import game_library as gl
        from actions import libretro

        core_path = er.find_core(console_id)
        if core_path is None:
            self._set_status("Falta el emulador — pulsa «Instalar emulador» arriba")
            self._refresh_emulator_state()
            return

        if console_id == "ps2":
            from actions import bios

            if not bios.has_ps2_bios(er.system_dir()):
                self._set_status(
                    "PS2 necesita una BIOS de tu propia consola antes de poder jugar"
                )
                self._import_bios()
                if not bios.has_ps2_bios(er.system_dir()):
                    return

        self._close_player()
        try:
            spec = er.CORES.get(console_id)
            core = libretro.load(core_path, er.system_dir(), er.saves_dir(),
                                 options=spec.options if spec else None)
            core.load_game(str(rom_path))
        except Exception as exc:
            self._set_status(f"No pude arrancar el juego: {exc}")
            return

        self._player.start(core, rom_path, title, console_id)
        self._header.back_btn.setVisible(True)
        self._header.title.setText(title)
        self._stack.setCurrentIndex(2)
        self._set_status(f"▶ {title}")
        try:
            gl.mark_played(f"rom:{Path(rom_path).stem}")
        except Exception:
            pass

    def _close_player(self):
        if self._player.is_active():
            self._player.stop()

    def pause_playback(self):
        """Freeze a running game (mode switch) without losing the session."""
        self._player.pause()

    def _import_bios(self):
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Selecciona el volcado de tu BIOS de PS2",
            "", "Volcados de BIOS (*.bin *.rom);;Todos los archivos (*)")
        if not path:
            return
        try:
            from actions import bios, emulator_runtime as er
            target = bios.import_ps2_bios(path, er.system_dir())
            self._set_status(f"BIOS importada: {target.name}")
        except Exception as exc:
            self._set_status(str(exc))
        self._import_page.refresh_bios_state()

    def _remove_bios(self):
        from actions import bios, emulator_runtime as er
        if bios.remove_ps2_bios(er.system_dir()):
            self._set_status("BIOS quitada")
        self._import_page.refresh_bios_state()

    def _import_local_rom(self):
        from PyQt6.QtWidgets import QFileDialog

        console_id = self._console_id
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecciona una imagen de disco",
            "", "Imágenes de disco (*.iso *.chd *.cso *.zso);;Todos los archivos (*)")
        if not path:
            return
        try:
            from actions import game_library as gl
            entry = gl.add_local_rom(path, console_id)
            self._set_status(f"Añadido: {entry.title}")
        except Exception as exc:
            self._set_status(f"No pude importar el archivo: {exc}")
        self._import_page.refresh_library(console_id)

    def _open_controls(self):
        """Controls are reachable without a game running, so the grid page has
        its own entry point into the player's dialog."""
        self._player.open_controls(self._console_id)

    def shutdown(self):
        """Release the core when the panel goes away (mode switch, app exit)."""
        self._close_player()


class LibraryModePanel(QWidget):
    """«Mi biblioteca» — downloaded ROMs and installed PC games in one grid."""

    _entries_ready = pyqtSignal(list, str)
    # ROMs are handed to the Emuladores tab instead of being launched here:
    # the embedded core lives there, and two panels owning one core would be a
    # second source of truth for who has to unload it.
    play_rom_requested = pyqtSignal(str, str, str)  # (path, title, console_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EmuRoot")
        self._entries: list = []
        self._filter_kind = ""
        self._query = ""

        self._build_ui()
        self._entries_ready.connect(self._on_entries)
        QTimer.singleShot(0, self.refresh)

    def _build_ui(self):
        self.setStyleSheet(_panel_qss())
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        self._header = _HeaderBar("Mi biblioteca", "Filtrar mi biblioteca…")
        self._header.search_changed.connect(self._on_filter_text)
        root.addWidget(self._header)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._chips: dict[str, QPushButton] = {}
        for label, value in (("Todo", ""), ("ROMs", "rom"), ("PC", "pc")):
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setChecked(value == "")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(34)
            chip.setStyleSheet(_chip_qss())
            chip.clicked.connect(lambda _=False, v=value: self._set_filter(v))
            self._chips[value] = chip
            chips_row.addWidget(chip)
        chips_row.addStretch(1)

        refresh_btn = QPushButton("Actualizar")
        refresh_btn.setFixedHeight(34)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(_ghost_btn_qss())
        refresh_btn.clicked.connect(self.refresh)
        chips_row.addWidget(refresh_btn)
        root.addLayout(chips_row)

        self._results_label = QLabel("")
        self._results_label.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        self._results_label.setStyleSheet("color:#edf6ff; background:transparent;")
        root.addWidget(self._results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent; border:none;")
        self._grid = _CardGrid()
        self._grid.clicked.connect(self._launch)
        self._grid.context_requested.connect(self._show_menu)
        scroll.setWidget(self._grid)
        self._grid.attach_viewport(scroll)
        root.addWidget(scroll, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{C.TEXT_MED}; background:transparent; font-size:11px;")
        root.addWidget(self._status)

    # ------------------------------------------------------------------

    def refresh(self):
        self._status.setText("Leyendo biblioteca…")

        def work():
            try:
                from actions import game_library as gl
                entries = gl.list_entries()
                _emit(self._entries_ready, entries, "")
            except Exception as exc:
                _emit(self._entries_ready, [], str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_entries(self, entries: list, error: str):
        if error:
            self._status.setText(error)
            return
        self._entries = entries
        self._status.setText("")
        self._apply_filter()

    def _set_filter(self, kind: str):
        for value, chip in self._chips.items():
            chip.setChecked(value == kind)
        self._filter_kind = kind
        self._apply_filter()

    def _on_filter_text(self, text: str):
        self._query = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self):
        items = self._entries
        if self._filter_kind == "rom":
            items = [e for e in items if e.kind == "rom"]
        elif self._filter_kind == "pc":
            items = [e for e in items if e.kind in ("steam", "epic", "game")]
        if self._query:
            items = [e for e in items if self._query in e.title.lower()]

        self._results_label.setText(
            f"{len(items)} en tu biblioteca" if items else "Tu biblioteca está vacía"
        )
        self._grid.set_items(
            items,
            subtitle_of=lambda e: e.platform or e.kind.upper(),
            badge_of=lambda e: "ROM" if e.kind == "rom" else "",
        )
        if not items and not self._query and not self._filter_kind:
            self._status.setText(
                "Descarga una ROM en la pestaña Emuladores o un juego en Juegos "
                "y aparecerá aquí."
            )

    def _launch(self, entry):
        if entry.kind == "rom":
            self.play_rom_requested.emit(entry.path, entry.title,
                                         entry.console_id or "gba")
            return
        try:
            from actions import game_library as gl
            gl.launch(entry)
            self._status.setText(f"▶ {entry.title}")
        except Exception as exc:
            self._status.setText(f"No pude lanzarlo: {exc}")

    def _show_menu(self, entry, global_pos: QPoint):
        menu = QMenu(self)
        menu.addAction("Jugar", lambda: self._launch(entry))
        menu.addAction("Abrir carpeta", lambda: self._open_location(entry))
        if entry.removable:
            menu.addSeparator()
            menu.addAction("Quitar de la biblioteca",
                           lambda: self._remove(entry, delete_files=False))
            menu.addAction("Eliminar del disco",
                           lambda: self._remove(entry, delete_files=True))
        menu.exec(global_pos)

    def _open_location(self, entry):
        from actions import game_library as gl
        gl.open_location(entry)

    def _remove(self, entry, delete_files: bool):
        if delete_files:
            confirm = QMessageBox.question(
                self, "Eliminar del disco",
                f"¿Borrar «{entry.title}» del disco? Esta acción no se puede deshacer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        try:
            from actions import game_library as gl
            gl.remove(entry.entry_id, delete_files=delete_files)
            self._status.setText(f"«{entry.title}» quitado")
            self.refresh()
        except Exception as exc:
            self._status.setText(f"No pude quitarlo: {exc}")


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                _clear_layout(sub)

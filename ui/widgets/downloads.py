"""Right-panel downloads manager UI.

Renders the persistent download list owned by actions.download_manager: one row
per download with a percentage bar and a three-dot menu (pause/resume, open
folder, delete). Pure view — it emits intent signals and is driven by render();
the MainWindow wires those to the DownloadManager.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..theme import C, FONT_UI


def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    mb = bps / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


_STATUS_LABEL = {
    "downloading": "Descargando",
    "paused": "En pausa",
    "done": "Completada",
    "error": "Error",
}


class DownloadRow(QWidget):
    pause_requested = pyqtSignal(str)
    resume_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    open_requested = pyqtSignal(str)

    def __init__(self, download, parent=None):
        super().__init__(parent)
        self._id = download.id
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; }}
            QProgressBar {{
                background: rgba(255,255,255,0.05); border: none;
                border-radius: 5px; height: 10px; text-align: center;
                color: {C.TEXT}; font-size: 8px;
            }}
            QProgressBar::chunk {{ background: {C.PRI}; border-radius: 5px; }}
            QPushButton#Menu {{
                background: transparent; color: {C.TEXT_DIM};
                border: none; font-size: 14px; font-weight: bold;
                padding: 0 4px;
            }}
            QPushButton#Menu:hover {{ color: {C.ACC}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        self._title = QLabel()
        self._title.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color: {C.TEXT};")
        top.addWidget(self._title, stretch=1)

        self._menu_btn = QPushButton("⋮")
        self._menu_btn.setObjectName("Menu")
        self._menu_btn.setFixedWidth(20)
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.clicked.connect(self._show_menu)
        top.addWidget(self._menu_btn)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(True)
        lay.addWidget(self._bar)

        self._sub = QLabel()
        self._sub.setFont(QFont(FONT_UI, 7))
        self._sub.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(self._sub)

        self.update_data(download)

    def update_data(self, download):
        self._id = download.id
        self._status = download.status
        self._title.setText(download.title)
        self._title.setToolTip(download.title)
        pct = int(round((download.progress or 0) * 100))
        self._bar.setValue(max(0, min(100, pct)))
        self._bar.setFormat(f"{pct}%")

        status_txt = _STATUS_LABEL.get(download.status, download.status)
        if download.status == "downloading":
            spd = _fmt_speed(download.speed)
            self._sub.setText(f"{status_txt}  ·  {spd}" if spd else status_txt)
        elif download.status == "error":
            self._sub.setText(download.error or status_txt)
        else:
            self._sub.setText(status_txt)

    def _show_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;
            }}
            QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {C.PANEL2}; }}
        """)
        if self._status == "downloading":
            menu.addAction("Pausar", lambda: self.pause_requested.emit(self._id))
        elif self._status == "paused":
            menu.addAction("Reanudar", lambda: self.resume_requested.emit(self._id))
        elif self._status == "error":
            menu.addAction("Reintentar", lambda: self.resume_requested.emit(self._id))
        menu.addAction("Abrir carpeta", lambda: self.open_requested.emit(self._id))
        menu.addAction("Borrar", lambda: self.remove_requested.emit(self._id))
        menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomRight()))


class DownloadsListWidget(QWidget):
    """Scrollable list of DownloadRow widgets, updated in place by render()."""

    pause_requested = pyqtSignal(str)
    resume_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    open_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, DownloadRow] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._header = QLabel("DESCARGAS")
        self._header.setObjectName("SideEyebrow")
        self._header.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        self._header.setStyleSheet("color: #7F91A8;")
        lay.addWidget(self._header)

        self._empty = QLabel("Sin descargas")
        self._empty.setFont(QFont(FONT_UI, 8))
        self._empty.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(self._empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.setMaximumHeight(260)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(container)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(2)
        self._list_lay.addStretch(1)
        scroll.setWidget(container)
        lay.addWidget(scroll)

    def render(self, downloads: list):
        """Reconcile the row set with the manager's snapshot (called on the UI
        thread). Rows are updated in place so an open menu / progress tick
        doesn't tear down and rebuild the whole list."""
        seen = set()
        for i, d in enumerate(downloads):
            seen.add(d.id)
            row = self._rows.get(d.id)
            if row is None:
                row = DownloadRow(d)
                row.pause_requested.connect(self.pause_requested.emit)
                row.resume_requested.connect(self.resume_requested.emit)
                row.remove_requested.connect(self.remove_requested.emit)
                row.open_requested.connect(self.open_requested.emit)
                self._rows[d.id] = row
                # Insert before the trailing stretch, in snapshot order (newest first).
                self._list_lay.insertWidget(i, row)
            else:
                row.update_data(d)

        # Drop rows for downloads that no longer exist.
        for did in list(self._rows):
            if did not in seen:
                row = self._rows.pop(did)
                row.setParent(None)
                row.deleteLater()

        has = bool(downloads)
        self._empty.setVisible(not has)


__all__ = ["DownloadsListWidget", "DownloadRow"]

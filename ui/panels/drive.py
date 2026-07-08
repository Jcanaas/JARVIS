from __future__ import annotations
from pathlib import Path
from PyQt6.QtPdf import QPdfDocument
import html as html_lib
import tempfile
import mimetypes
import json
import threading

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from ..theme import *
from ..icons import *
from ..widgets import *

class DriveModePanel(QWidget):
    _result_sig = pyqtSignal(str, object)
    _preview_sig = pyqtSignal(int, object)

    def __init__(self, progress_hook=None, parent=None):
        super().__init__(parent)
        self.progress_hook = progress_hook
        self._items: list[dict] = []
        self._preview_request = 0
        self._preview_cache: dict[str, dict] = {}
        self._audio_temp_files: dict[str, str] = {}
        self._folder_stack: list[tuple[str, str]] = []
        self._current_folder_id = "root"
        self._current_folder_name = "Mi unidad"
        self.setStyleSheet(self._panel_style())
        self._result_sig.connect(self._handle_result)
        self._preview_sig.connect(self._apply_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        heading = QHBoxLayout()
        heading.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Drive")
        title.setObjectName("DriveTitle")
        subtitle = QLabel("Archivos y documentos de tu cuenta")
        subtitle.setObjectName("DriveSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.status = QLabel("Listo")
        self.status.setObjectName("DriveStatus")
        heading.addWidget(self.status)
        root.addLayout(heading)

        toolbar = QFrame()
        toolbar.setObjectName("DriveToolbar")
        search_row = QHBoxLayout(toolbar)
        search_row.setContentsMargins(10, 8, 10, 8)
        search_row.setSpacing(8)
        self.folder_back = QPushButton()
        self.folder_back.setObjectName("DriveToolButton")
        self.folder_back.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 17))
        self.folder_back.setToolTip("Volver a la carpeta anterior")
        self.folder_back.setEnabled(False)
        self.folder_back.clicked.connect(self.go_back_folder)
        search_row.addWidget(self.folder_back)
        self.search_input = SearchGlowInput("Buscar archivos en Drive...")
        self.search_input.returnPressed.connect(self.search_files)
        search_row.addWidget(self.search_input, stretch=1)
        recent = QPushButton("Mi unidad")
        recent.setObjectName("DriveToolButton")
        recent.setIcon(_line_icon("refresh", C.TEXT_DIM, 17))
        recent.clicked.connect(self.load_recent)
        upload = QPushButton("Subir")
        upload.setObjectName("DrivePrimaryButton")
        upload.setIcon(_line_icon("upload", C.PRI, 17))
        upload.clicked.connect(self.upload_selected_file)
        search_row.addWidget(recent)
        search_row.addWidget(upload)
        root.addWidget(toolbar)

        self.folder_path = QLabel("Mi unidad")
        self.folder_path.setObjectName("DrivePath")
        root.addWidget(self.folder_path)

        body = QHBoxLayout()
        body.setSpacing(10)
        list_panel = QFrame()
        list_panel.setObjectName("DriveListPanel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        list_header = QLabel("ARCHIVOS")
        list_header.setObjectName("DriveSectionLabel")
        list_layout.addWidget(list_header)
        self.file_list = QListWidget()
        self.file_list.setObjectName("DriveFileList")
        self.file_list.itemSelectionChanged.connect(self._show_selected_details)
        self.file_list.itemDoubleClicked.connect(self._activate_drive_item)
        list_layout.addWidget(self.file_list, stretch=1)
        body.addWidget(list_panel, stretch=7)

        details_panel = QFrame()
        details_panel.setObjectName("DriveDetailsPanel")
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(16, 14, 16, 14)
        details_layout.setSpacing(8)
        details_header = QLabel("DETALLES")
        details_header.setObjectName("DriveSectionLabel")
        details_layout.addWidget(details_header)
        self.preview_stack = AnimatedStack()
        self.preview_stack.setObjectName("DrivePreviewStack")
        self.preview_image = QLabel("Selecciona un archivo")
        self.preview_image.setObjectName("DrivePreviewImage")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumHeight(250)
        self.preview_image.setWordWrap(True)
        self.preview_text = QTextBrowser()
        self.preview_text.setObjectName("DrivePreviewText")
        self.preview_text.setReadOnly(True)
        self.preview_audio = QWidget()
        audio_layout = QVBoxLayout(self.preview_audio)
        audio_layout.setContentsMargins(28, 28, 28, 28)
        audio_layout.setSpacing(14)
        audio_layout.addStretch()
        self.audio_title = QLabel("Audio")
        self.audio_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_title.setStyleSheet(
            f"color:{C.TEXT};font-size:16px;font-weight:750;background:transparent;"
        )
        audio_layout.addWidget(self.audio_title)
        audio_controls = QHBoxLayout()
        audio_controls.setSpacing(10)
        self.audio_play = QPushButton()
        self.audio_play.setFixedSize(42, 42)
        self.audio_play.setIcon(_line_icon("play", C.PRI, 19))
        self.audio_play.setIconSize(QSize(19, 19))
        self.audio_play.setToolTip("Reproducir o pausar")
        self.audio_play.clicked.connect(self._toggle_drive_audio)
        self.audio_seek = QSlider(Qt.Orientation.Horizontal)
        self.audio_seek.setRange(0, 0)
        self.audio_seek.sliderMoved.connect(self._seek_drive_audio)
        self.audio_time = QLabel("0:00 / 0:00")
        self.audio_time.setStyleSheet(f"color:{C.TEXT_MED};background:transparent;")
        audio_controls.addWidget(self.audio_play)
        audio_controls.addWidget(self.audio_seek, stretch=1)
        audio_controls.addWidget(self.audio_time)
        audio_layout.addLayout(audio_controls)
        audio_layout.addStretch()

        self._drive_audio_output = QAudioOutput(self)
        self._drive_audio_output.setVolume(0.75)
        self._drive_audio_player = QMediaPlayer(self)
        self._drive_audio_player.setAudioOutput(self._drive_audio_output)
        self._drive_audio_player.positionChanged.connect(self._update_drive_audio_position)
        self._drive_audio_player.durationChanged.connect(self._update_drive_audio_duration)
        self._drive_audio_player.playbackStateChanged.connect(self._update_drive_audio_state)
        self._drive_audio_player.errorOccurred.connect(self._drive_audio_error)
        self.preview_stack.addWidget(self.preview_image)
        self.preview_stack.addWidget(self.preview_text)
        self.preview_stack.addWidget(self.preview_audio)
        details_layout.addWidget(self.preview_stack, stretch=4)

        self.details = QTextBrowser()
        self.details.setObjectName("DriveDetails")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Selecciona un archivo para ver detalles.")
        self.details.setOpenExternalLinks(True)
        self.details.setMaximumHeight(150)
        details_layout.addWidget(self.details, stretch=1)
        body.addWidget(details_panel, stretch=5)
        root.addLayout(body, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for text, icon, handler, destructive in [
            ("Descargar", "download", self.download_selected_file, False),
            ("Compartir", "share", self.share_selected_file, False),
            ("Renombrar", "edit", self.rename_selected_file, False),
            ("Borrar", "trash", self.delete_selected_file, True),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("DriveDangerButton" if destructive else "DriveActionButton")
            btn.setIcon(_line_icon(icon, "#fda4af" if destructive else C.TEXT_DIM, 16))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(handler)
            actions.addWidget(btn)
        actions.addStretch()
        root.addLayout(actions)
        QTimer.singleShot(200, self.load_recent)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QLabel#DriveTitle {{
                color: #f8fafc;
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#DriveSubtitle {{
                color: rgba(188, 198, 238, 0.58);
                font-size: 11px;
            }}
            QLabel#DriveStatus {{
                background: rgba(52, 211, 153, 0.10);
                color: #a7f3d0;
                border: 1px solid rgba(52, 211, 153, 0.22);
                border-radius: 6px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 800;
            }}
            QFrame#DriveToolbar, QFrame#DriveListPanel, QFrame#DriveDetailsPanel {{
                background: rgba(7, 15, 26, 0.90);
                border: 1px solid rgba(182, 196, 255, 0.11);
                border-radius: 10px;
            }}
            QLineEdit#DriveSearch {{
                min-height: 34px;
                background: rgba(2, 8, 15, 0.72);
                color: {C.TEXT};
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 7px;
                padding: 0 11px;
                selection-background-color: {C.PRI};
            }}
            QLineEdit#DriveSearch:focus {{
                border-color: rgba(182, 196, 255, 0.52);
            }}
            QLabel#DriveSectionLabel {{
                color: rgba(182, 196, 255, 0.72);
                padding: 13px 15px 9px 15px;
                font-size: 10px;
                font-weight: 900;
            }}
            QLabel#DrivePath {{
                color: rgba(188, 198, 238, 0.72);
                padding: 0 4px;
                font-size: 11px;
                font-weight: 700;
            }}
            QListWidget#DriveFileList {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                outline: none;
                padding: 0 8px 8px 8px;
            }}
            QListWidget#DriveFileList::item {{
                min-height: 42px;
                border: none;
                border-bottom: 1px solid rgba(182, 196, 255, 0.08);
                padding: 7px 9px;
            }}
            QListWidget#DriveFileList::item:hover {{
                background: rgba(182, 196, 255, 0.06);
            }}
            QListWidget#DriveFileList::item:selected {{
                background: rgba(94, 130, 255, 0.13);
                color: #f8fafc;
                border-left: 2px solid #5e82ff;
            }}
            QStackedWidget#DrivePreviewStack {{
                background: rgba(2, 8, 15, 0.46);
                border: 1px solid rgba(182, 196, 255, 0.08);
                border-radius: 8px;
            }}
            QLabel#DrivePreviewImage {{
                color: rgba(188, 198, 238, 0.58);
                background: transparent;
                border: none;
                padding: 14px;
                font-size: 12px;
            }}
            QTextBrowser#DrivePreviewText {{
                background: transparent;
                color: #d7dbee;
                border: none;
                padding: 12px;
                font-family: "Cascadia Mono", "Consolas";
                font-size: 11px;
            }}
            QTextBrowser#DriveDetails {{
                background: transparent;
                color: #d7dbee;
                border: none;
                padding: 4px 0 0 0;
                selection-background-color: rgba(94, 130, 255, 0.40);
            }}
            QPushButton#DriveToolButton, QPushButton#DriveActionButton {{
                min-height: 32px;
                background: rgba(255, 255, 255, 0.035);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-radius: 7px;
                padding: 0 11px;
                font-weight: 700;
            }}
            QPushButton#DrivePrimaryButton {{
                min-height: 32px;
                background: rgba(94, 130, 255, 0.16);
                color: #dce1ff;
                border: 1px solid rgba(182, 196, 255, 0.28);
                border-radius: 7px;
                padding: 0 12px;
                font-weight: 800;
            }}
            QPushButton#DriveDangerButton {{
                min-height: 32px;
                background: rgba(244, 63, 94, 0.07);
                color: #fda4af;
                border: 1px solid rgba(244, 63, 94, 0.16);
                border-radius: 7px;
                padding: 0 11px;
                font-weight: 700;
            }}
            QPushButton#DriveToolButton:hover, QPushButton#DriveActionButton:hover,
            QPushButton#DrivePrimaryButton:hover {{
                background: rgba(94, 130, 255, 0.18);
                color: {C.TEXT};
                border-color: rgba(182, 196, 255, 0.34);
            }}
            QPushButton#DriveDangerButton:hover {{
                background: rgba(244, 63, 94, 0.14);
                border-color: rgba(251, 113, 133, 0.32);
            }}
        """ + _scrollbar_qss()

    def _run(self, op: str, fn):
        self.status.setText("Trabajando...")

        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            self._result_sig.emit(op, result)

        threading.Thread(target=worker, daemon=True).start()

    def _selected_file(self) -> dict:
        item = self.file_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else {}

    def load_recent(self):
        self._folder_stack.clear()
        self._current_folder_id = "root"
        self._current_folder_name = "Mi unidad"
        self.search_input.clear()
        self._update_folder_navigation()
        self._load_current_folder()

    def _load_current_folder(self):
        folder_id = self._current_folder_id
        self._run(
            "list",
            lambda: __import__("actions.gdrive", fromlist=["list_files"]).list_files(
                count=200,
                folder_id=folder_id,
            ),
        )

    def _activate_drive_item(self, item):
        file = item.data(Qt.ItemDataRole.UserRole) if item else {}
        if file.get("mimeType") == "application/vnd.google-apps.folder":
            self._folder_stack.append((self._current_folder_id, self._current_folder_name))
            self._current_folder_id = str(file.get("id") or "")
            self._current_folder_name = str(file.get("name") or "Carpeta")
            self.search_input.clear()
            self._update_folder_navigation()
            self._load_current_folder()
            return
        self._show_selected_details()

    def go_back_folder(self):
        if not self._folder_stack:
            return
        self._current_folder_id, self._current_folder_name = self._folder_stack.pop()
        self.search_input.clear()
        self._update_folder_navigation()
        self._load_current_folder()

    def _update_folder_navigation(self):
        names = [name for _folder_id, name in self._folder_stack]
        names.append(self._current_folder_name)
        self.folder_path.setText("  /  ".join(names))
        self.folder_back.setEnabled(bool(self._folder_stack))

    def search_files(self):
        query = self.search_input.text().strip()
        if not query:
            self._load_current_folder()
            return
        folder_id = self._current_folder_id
        self._run(
            "list",
            lambda: __import__("actions.gdrive", fromlist=["search_files"]).search_files(
                query,
                count=200,
                folder_id=folder_id,
            ),
        )

    def upload_selected_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecciona un archivo para subir")
        if not path:
            return
        self._run(
            "uploaded",
            lambda: __import__("actions.gdrive", fromlist=["upload_file"]).upload_file(
                path,
                progress_hook=self.progress_hook,
            ),
        )

    def download_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        self._run(
            "downloaded",
            lambda: __import__("actions.gdrive", fromlist=["download_file"]).download_file(
                file_id=file.get("id", ""),
                progress_hook=self.progress_hook,
            ),
        )

    def share_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        email, ok = QInputDialog.getText(self, "Compartir archivo", "Email o vacio para enlace publico:")
        if not ok:
            return
        self._run(
            "shared",
            lambda: __import__("actions.gdrive", fromlist=["share_file"]).share_file(
                file_id=file.get("id", ""),
                email=email.strip(),
                anyone=not bool(email.strip()),
                role="reader",
            ),
        )

    def rename_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        new_name, ok = QInputDialog.getText(self, "Renombrar archivo", "Nuevo nombre:", text=file.get("name", ""))
        if not ok or not new_name.strip():
            return
        self._run(
            "renamed",
            lambda: __import__("actions.gdrive", fromlist=["rename_file"]).rename_file(
                file_id=file.get("id", ""),
                new_name=new_name.strip(),
            ),
        )

    def delete_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        answer = QMessageBox.question(
            self,
            "Borrar archivo",
            f"Enviar a la papelera: {file.get('name', '')}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run(
            "deleted",
            lambda: __import__("actions.gdrive", fromlist=["delete_file"]).delete_file(file_id=file.get("id", "")),
        )

    def _show_selected_details(self):
        file = self._selected_file()
        if not file:
            return
        if self._drive_audio_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self._drive_audio_player.stop()
        self._render_drive_metadata(file)
        file_id = str(file.get("id") or "")
        if not file_id:
            return
        self._preview_request += 1
        request_id = self._preview_request
        audio_path = self._audio_temp_files.get(file_id)
        if audio_path and Path(audio_path).exists():
            self._apply_preview(
                request_id,
                {
                    "kind": "audio",
                    "data": b"",
                    "mimeType": file.get("mimeType") or "",
                    "info": file,
                },
            )
            return
        cached = self._preview_cache.get(file_id)
        if cached is not None:
            self._apply_preview(request_id, cached)
            return
        self.preview_stack.setCurrentWidget(self.preview_image)
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText("Cargando previsualización...")
        self.status.setText("Cargando vista previa...")

        def worker():
            try:
                result = __import__(
                    "actions.gdrive",
                    fromlist=["get_file_preview"],
                ).get_file_preview(file_id)
            except Exception as exc:
                result = exc
            self._preview_sig.emit(request_id, result)

        threading.Thread(target=worker, daemon=True).start()

    def _render_drive_metadata(self, file: dict):
        name = html_lib.escape(str(file.get("name") or "(sin nombre)"))
        mime = html_lib.escape(str(file.get("mimeType") or "Tipo desconocido"))
        modified = html_lib.escape(str(file.get("modifiedTime") or "")[:19].replace("T", " "))
        description = html_lib.escape(str(file.get("description") or ""))
        try:
            size = int(file.get("size") or 0)
            units = ["B", "KB", "MB", "GB"]
            value = float(size)
            unit = units[0]
            for candidate in units:
                unit = candidate
                if value < 1024 or candidate == units[-1]:
                    break
                value /= 1024
            size_text = f"{value:.1f} {unit}" if size else ""
        except (TypeError, ValueError):
            size_text = ""
        rows = [
            f"<div style='font-size:15px;font-weight:700;color:#f8fafc'>{name}</div>",
            f"<div style='margin-top:5px;color:#91a9bd'>{mime}</div>",
        ]
        facts = " · ".join(part for part in (size_text, modified) if part)
        if facts:
            rows.append(f"<div style='margin-top:5px;color:#7890a5'>{html_lib.escape(facts)}</div>")
        if description:
            rows.append(f"<div style='margin-top:8px;color:#cbd5e1'>{description}</div>")
        self.details.setHtml("".join(rows))

    def _apply_preview(self, request_id: int, result):
        if request_id != self._preview_request:
            return
        if isinstance(result, Exception):
            self.preview_stack.setCurrentWidget(self.preview_image)
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText(f"No se pudo cargar la vista previa.\n{result}")
            self.status.setText("Vista previa no disponible")
            return
        if not isinstance(result, dict):
            return
        file_id = str((result.get("info") or {}).get("id") or "")
        kind = str(result.get("kind") or "unsupported")
        raw = bytes(result.get("data") or b"")
        if file_id and kind != "audio":
            self._preview_cache[file_id] = result
        self.status.setText("Listo")

        if kind == "text":
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 120_000:
                text = text[:120_000] + "\n\n[Vista previa recortada]"
            self.preview_text.setPlainText(text)
            self.preview_stack.setCurrentWidget(self.preview_text)
            return

        if kind == "audio":
            info = result.get("info") or {}
            file_id = str(info.get("id") or "")
            name = str(info.get("name") or result.get("name") or "Audio")
            path = self._audio_temp_files.get(file_id)
            if not path or not Path(path).exists():
                suffix = Path(name).suffix
                if not suffix:
                    suffix = mimetypes.guess_extension(str(result.get("mimeType") or "")) or ".audio"
                handle = tempfile.NamedTemporaryFile(
                    prefix="jarvis-drive-",
                    suffix=suffix,
                    delete=False,
                )
                try:
                    handle.write(raw)
                    path = handle.name
                finally:
                    handle.close()
                if file_id:
                    self._audio_temp_files[file_id] = path
            self.audio_title.setText(name)
            self.audio_seek.setValue(0)
            self.audio_time.setText("0:00 / 0:00")
            self._drive_audio_player.setSource(QUrl.fromLocalFile(path))
            self.preview_stack.setCurrentWidget(self.preview_audio)
            return

        pixmap = QPixmap()
        if kind == "image":
            pixmap.loadFromData(raw)
        elif kind == "pdf":
            buffer = QBuffer()
            buffer.setData(QByteArray(raw))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document = QPdfDocument(self)
            document.load(buffer)
            if document.pageCount() > 0:
                pixmap = QPixmap.fromImage(document.render(0, QSize(900, 1180)))
            document.deleteLater()

        self.preview_stack.setCurrentWidget(self.preview_image)
        if not pixmap.isNull():
            target = self.preview_image.size()
            if target.width() < 100 or target.height() < 100:
                target = QSize(620, 360)
            self.preview_image.setText("")
            self.preview_image.setPixmap(
                pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            return

        labels = {
            "folder": "Carpeta de Google Drive",
            "too_large": "Archivo demasiado grande para previsualizar",
            "unsupported": "Este formato no admite previsualizacion",
        }
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText(labels.get(kind, "Vista previa no disponible"))

    def _toggle_drive_audio(self):
        if self._drive_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._drive_audio_player.pause()
        else:
            self._drive_audio_player.play()

    def _seek_drive_audio(self, position: int):
        self._drive_audio_player.setPosition(int(position))

    def _update_drive_audio_duration(self, duration: int):
        self.audio_seek.setRange(0, max(0, int(duration)))
        self._update_drive_audio_position(self._drive_audio_player.position())

    def _update_drive_audio_position(self, position: int):
        if not self.audio_seek.isSliderDown():
            self.audio_seek.setValue(max(0, int(position)))
        duration = max(0, int(self._drive_audio_player.duration()))
        self.audio_time.setText(
            f"{self._format_drive_media_time(position)} / {self._format_drive_media_time(duration)}"
        )

    def _update_drive_audio_state(self, state):
        icon = "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play"
        self.audio_play.setIcon(_line_icon(icon, C.PRI, 19))

    def _drive_audio_error(self, _error, message: str):
        if message:
            self.status.setText("No se pudo reproducir el audio")
            self.audio_title.setToolTip(message)

    @staticmethod
    def _format_drive_media_time(milliseconds: int) -> str:
        seconds = max(0, int(milliseconds) // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def closeEvent(self, event):
        self._drive_audio_player.stop()
        for path in self._audio_temp_files.values():
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self._audio_temp_files.clear()
        super().closeEvent(event)

    def _handle_result(self, op: str, result):
        if isinstance(result, Exception):
            self.status.setText("Error")
            self.details.setPlainText(str(result))
            return
        if op == "list":
            self._items = list(result or [])
            self.file_list.clear()
            for file in self._items:
                item = QListWidgetItem(f"{file.get('name') or '(sin nombre)'}\n{file.get('modifiedTime', '')[:10]}")
                mime = str(file.get("mimeType") or "").lower()
                icon_name = "folder" if "folder" in mime else "image" if "image" in mime else "video" if "video" in mime else "audio" if "audio" in mime else "archive" if any(value in mime for value in ("zip", "rar", "compressed")) else "file"
                item.setIcon(_line_icon(icon_name, C.TEXT_MED, 18))
                item.setData(Qt.ItemDataRole.UserRole, file)
                self.file_list.addItem(item)
            self.status.setText(f"{len(self._items)} archivo(s)")
            if not self._items:
                self.details.setPlainText("No hay resultados.")
                self.preview_image.setText("No hay archivos para mostrar")
            else:
                self.file_list.setCurrentRow(0)
            return
        if isinstance(result, dict):
            self.status.setText(result.get("name") or op)
            self.details.setPlainText(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            self.status.setText(op)
            self.details.setPlainText(str(result))
        if op in {"uploaded", "renamed", "deleted"}:
            QTimer.singleShot(500, self.load_recent)



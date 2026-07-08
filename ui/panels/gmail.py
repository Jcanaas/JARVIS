from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *

class GmailComposeDialog(QDialog):
    """Compositor de correo con redacción asistida por IA y envío en segundo plano."""

    _ai_sig = pyqtSignal(object)
    _send_sig = pyqtSignal(object)

    def __init__(self, parent=None, prefill: dict | None = None):
        super().__init__(parent)
        prefill = prefill or {}
        self.setWindowTitle("Redactar correo")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(self._style())
        self._attachments: list[str] = []
        self._ai_sig.connect(self._on_ai_result)
        self._send_sig.connect(self._on_send_result)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        title = QLabel("Nuevo correo")
        title.setObjectName("ComposeTitle")
        lay.addWidget(title)

        self.to_input = QLineEdit(str(prefill.get("to") or ""))
        self.to_input.setObjectName("ComposeField")
        self.to_input.setPlaceholderText("Para (correo del destinatario)")
        lay.addWidget(self.to_input)

        self.subject_input = QLineEdit(str(prefill.get("subject") or ""))
        self.subject_input.setObjectName("ComposeField")
        self.subject_input.setPlaceholderText("Asunto")
        lay.addWidget(self.subject_input)

        # ── Fila de IA ────────────────────────────────────────────────
        ai_row = QHBoxLayout()
        ai_row.setSpacing(6)
        self.ai_input = QLineEdit()
        self.ai_input.setObjectName("ComposeField")
        self.ai_input.setPlaceholderText("Dile a la IA qué escribir y pulsa Autocompletar…")
        self.ai_input.returnPressed.connect(self._run_ai)
        ai_row.addWidget(self.ai_input, stretch=1)
        self.ai_btn = QPushButton("✨ Autocompletar")
        self.ai_btn.setObjectName("ComposeAi")
        self.ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_btn.clicked.connect(self._run_ai)
        ai_row.addWidget(self.ai_btn)
        lay.addLayout(ai_row)

        self.body_input = _ComposeTextEdit()
        self.body_input.setPlainText(str(prefill.get("body") or ""))
        self.body_input.setObjectName("ComposeBody")
        self.body_input.setPlaceholderText(
            "Escribe tu mensaje, o genéralo con la IA. Puedes pegar capturas (Ctrl+V) o arrastrar archivos."
        )
        self.body_input.setMinimumHeight(200)
        self.body_input.files_pasted.connect(self._add_attachments)
        lay.addWidget(self.body_input, stretch=1)

        # ── Fila de adjuntos ──────────────────────────────────────────
        attach_row = QHBoxLayout()
        attach_row.setSpacing(8)
        self.attach_btn = QPushButton("  Adjuntar")
        self.attach_btn.setObjectName("ComposeAttach")
        self.attach_btn.setIcon(_line_icon("upload", C.PRI, 15))
        self.attach_btn.setIconSize(QSize(15, 15))
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.clicked.connect(self._pick_attachments)
        attach_row.addWidget(self.attach_btn)
        self.attach_label = QLabel("Sin adjuntos")
        self.attach_label.setObjectName("ComposeAttachLabel")
        self.attach_label.setWordWrap(True)
        attach_row.addWidget(self.attach_label, stretch=1)
        self.attach_clear = QPushButton("Quitar")
        self.attach_clear.setObjectName("ComposeCancel")
        self.attach_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_clear.clicked.connect(self._clear_attachments)
        self.attach_clear.setVisible(False)
        attach_row.addWidget(self.attach_clear)
        lay.addLayout(attach_row)

        self.feedback = QLabel("")
        self.feedback.setObjectName("ComposeFeedback")
        self.feedback.setWordWrap(True)
        lay.addWidget(self.feedback)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setObjectName("ComposeCancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.send_btn = QPushButton("Enviar")
        self.send_btn.setObjectName("ComposeSend")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setIcon(_line_icon("send", "#0a0e26", 15))
        self.send_btn.setIconSize(QSize(15, 15))
        self.send_btn.clicked.connect(self._send)
        buttons.addWidget(self.send_btn)
        lay.addLayout(buttons)

    # -- IA ---------------------------------------------------------------
    def _run_ai(self):
        instruction = self.ai_input.text().strip()
        if not instruction:
            self._set_feedback("Escribe una instrucción para la IA.", error=True)
            return
        to = self.to_input.text().strip()
        subject = self.subject_input.text().strip()
        self.ai_btn.setEnabled(False)
        self._set_feedback("Generando con IA…")

        def worker():
            try:
                result = __import__(
                    "actions.gmail_ai", fromlist=["draft_email"]
                ).draft_email(instruction, to=to, subject=subject)
            except Exception as exc:
                result = exc
            self._ai_sig.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_result(self, result):
        self.ai_btn.setEnabled(True)
        if isinstance(result, Exception):
            self._set_feedback(f"IA: {result}", error=True)
            return
        subject = str(result.get("subject") or "").strip()
        body = str(result.get("body") or "").strip()
        if subject and not self.subject_input.text().strip():
            self.subject_input.setText(subject)
        if body:
            self.body_input.setPlainText(body)
        self._set_feedback("Borrador generado. Revísalo antes de enviar.")

    # -- Adjuntos ---------------------------------------------------------
    def _pick_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Adjuntar archivos", "", "Todos los archivos (*.*)"
        )
        if paths:
            self._add_attachments(paths)

    def _add_attachments(self, paths):
        added = 0
        for path in paths or []:
            path = str(path or "").strip()
            if path and path not in self._attachments and Path(path).is_file():
                self._attachments.append(path)
                added += 1
        self._refresh_attach_label()
        if added:
            self._set_feedback(f"{added} adjunto(s) añadido(s).")

    def _clear_attachments(self):
        self._attachments.clear()
        self._refresh_attach_label()

    def _refresh_attach_label(self):
        if not self._attachments:
            self.attach_label.setText("Sin adjuntos")
            self.attach_clear.setVisible(False)
            return
        names = ", ".join(Path(p).name for p in self._attachments)
        self.attach_label.setText(f"{len(self._attachments)} archivo(s): {names}")
        self.attach_clear.setVisible(True)

    # -- Envío ------------------------------------------------------------
    def _send(self):
        to = self.to_input.text().strip()
        subject = self.subject_input.text().strip() or "(sin asunto)"
        body = self.body_input.toPlainText().strip()
        if not to:
            self._set_feedback("Indica el destinatario.", error=True)
            return
        if not body and not self._attachments:
            self._set_feedback("El mensaje está vacío.", error=True)
            return
        attachments = list(self._attachments)
        self.send_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._set_feedback("Enviando…")

        def worker():
            try:
                result = __import__(
                    "actions.gmail", fromlist=["send_email"]
                ).send_email(to, subject, body, attachments=attachments)
            except Exception as exc:
                result = exc
            self._send_sig.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_send_result(self, result):
        if isinstance(result, Exception):
            self.send_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self._set_feedback(f"No se pudo enviar: {result}", error=True)
            return
        self.accept()

    def _set_feedback(self, text: str, error: bool = False):
        self.feedback.setText(text)
        self.feedback.setStyleSheet(
            f"color: {'#fca5a5' if error else 'rgba(188,198,238,0.75)'};"
            " background: transparent; font-size: 12px;"
        )

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
            QLineEdit#ComposeField, QTextEdit#ComposeBody {{
                background: rgba(3, 9, 17, 0.72);
                color: #e8ebff;
                border: 1px solid rgba(182, 196, 255, 0.16);
                border-radius: 7px;
                padding: 8px 11px;
                font-size: 13px;
                selection-background-color: #5e82ff;
            }}
            QLineEdit#ComposeField:focus, QTextEdit#ComposeBody:focus {{
                border-color: rgba(182, 196, 255, 0.55);
            }}
            QPushButton#ComposeAi {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border: 1px solid rgba(182, 196, 255, 0.45);
                border-radius: 7px;
                padding: 0 14px;
                min-height: 34px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#ComposeAi:hover {{
                background: rgba(94, 130, 255, 0.18);
            }}
            QPushButton#ComposeAi:disabled {{
                color: rgba(188, 198, 238, 0.35);
                border-color: rgba(182, 196, 255, 0.14);
            }}
            QPushButton#ComposeAttach {{
                background: rgba(255, 255, 255, 0.05);
                color: #dce1ff;
                border: 1px solid rgba(182, 196, 255, 0.25);
                border-radius: 7px;
                padding: 0 12px 0 8px;
                min-height: 30px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#ComposeAttach:hover {{
                background: rgba(94, 130, 255, 0.14);
                border-color: rgba(182, 196, 255, 0.45);
            }}
            QLabel#ComposeAttachLabel {{
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
            QPushButton#ComposeSend:disabled {{ background: rgba(182,196,255,0.25); color: rgba(4,18,31,0.5); }}
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


class GmailModePanel(QWidget):
    _result_sig = pyqtSignal(str, object)

    # (texto del chip, label de Gmail, solo_no_leídos)
    _FOLDERS = [
        ("Entrada",     "INBOX",     False),
        ("No leídos",   "INBOX",     True),
        ("Destacados",  "STARRED",   False),
        ("Importantes", "IMPORTANT", False),
        ("Enviados",    "SENT",      False),
        ("Borradores",  "DRAFT",     False),
        ("Spam",        "SPAM",      False),
        ("Papelera",    "TRASH",     False),
        ("Todo",        "ALL",       False),
    ]
    # carpetas en las que importa el destinatario, no el remitente
    _OUTGOING = {"SENT", "DRAFT"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._current_email: dict | None = None
        self._compact_reader = False
        self._page = 1
        self._pages = 1
        self._total_emails = 0
        self._page_size = 50
        self._list_label = "ALL"
        self._list_unread = False
        self._list_query = ""
        self._folder_name = "Bandeja"
        self._rendered_email: dict | None = None
        self._enriched_email_html: dict[str, str] = {}
        self.setStyleSheet(self._panel_style())
        self._result_sig.connect(self._handle_result)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        self.reader_page = QFrame()
        self.reader_page.setObjectName("GmailReader")
        reader_lay = QVBoxLayout(self.reader_page)
        reader_lay.setContentsMargins(24, 18, 24, 20)
        reader_lay.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.back_btn = QPushButton()
        self.back_btn.setObjectName("GmailIconButton")
        self.back_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 18))
        self.back_btn.setIconSize(QSize(18, 18))
        self.back_btn.setToolTip("Volver a la bandeja")
        self.back_btn.setFixedSize(34, 30)
        self.back_btn.clicked.connect(self._show_inbox)
        toolbar.addWidget(self.back_btn)
        self.reader_folder = QLabel("BANDEJA")
        self.reader_folder.setObjectName("GmailEyebrow")
        toolbar.addWidget(self.reader_folder)
        toolbar.addStretch()
        self.reader_date = QLabel("")
        self.reader_date.setObjectName("GmailDate")
        toolbar.addWidget(self.reader_date)
        reader_lay.addLayout(toolbar)

        self.reader_subject = QLabel("Selecciona un correo")
        self.reader_subject.setObjectName("GmailSubject")
        self.reader_subject.setWordWrap(True)
        reader_lay.addWidget(self.reader_subject)

        sender_row = QHBoxLayout()
        sender_row.setContentsMargins(0, 10, 0, 13)
        sender_row.setSpacing(10)
        self.sender_avatar = QLabel("@")
        self.sender_avatar.setObjectName("GmailAvatar")
        self.sender_avatar.setFixedSize(38, 38)
        self.sender_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sender_row.addWidget(self.sender_avatar)
        sender_text = QVBoxLayout()
        sender_text.setSpacing(1)
        self.reader_sender = QLabel("Ningún mensaje abierto")
        self.reader_sender.setObjectName("GmailSender")
        self.reader_sender.setWordWrap(True)
        self.reader_recipient = QLabel("")
        self.reader_recipient.setObjectName("GmailRecipient")
        self.reader_recipient.setWordWrap(True)
        sender_text.addWidget(self.reader_sender)
        sender_text.addWidget(self.reader_recipient)
        sender_row.addLayout(sender_text, stretch=1)
        reader_lay.addLayout(sender_row)

        divider = QFrame()
        divider.setObjectName("GmailDivider")
        divider.setFixedHeight(1)
        reader_lay.addWidget(divider)

        self.preview = QTextBrowser()
        self.preview.setObjectName("GmailPreview")
        self.preview.setReadOnly(True)
        self.preview.setOpenExternalLinks(True)
        self.preview.setPlaceholderText("Selecciona un correo de la bandeja.")
        reader_lay.addWidget(self.preview, stretch=1)

        self.inbox_page = QFrame()
        self.inbox_page.setObjectName("GmailInbox")
        self.inbox_page.setMinimumWidth(300)
        self.inbox_page.setMaximumWidth(380)
        inbox_lay = QVBoxLayout(self.inbox_page)
        inbox_lay.setContentsMargins(12, 14, 12, 12)
        inbox_lay.setSpacing(9)

        heading_row = QHBoxLayout()
        inbox_heading = QLabel("Bandeja")
        inbox_heading.setObjectName("GmailInboxTitle")
        heading_row.addWidget(inbox_heading)
        heading_row.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("GmailCount")
        heading_row.addWidget(self.status)
        self.compose_btn = QPushButton("  Redactar")
        self.compose_btn.setObjectName("GmailCompose")
        self.compose_btn.setIcon(_line_icon("edit", "#0a0e26", 16))
        self.compose_btn.setIconSize(QSize(16, 16))
        self.compose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compose_btn.setToolTip("Redactar un correo nuevo")
        self.compose_btn.clicked.connect(self._open_compose)
        heading_row.addWidget(self.compose_btn)
        inbox_lay.addLayout(heading_row)

        self.search_input = SearchGlowInput("Buscar correo")
        self.search_input.returnPressed.connect(self.search_emails)
        inbox_lay.addWidget(self.search_input)

        filter_host = QWidget()
        filter_host.setStyleSheet("QWidget { background: transparent; }")
        filters = FlowLayout(filter_host, margin=0, hspacing=5, vspacing=5)
        self._folder_group = QButtonGroup(self)
        self._folder_group.setExclusive(True)
        self._folder_buttons: dict[tuple[str, bool], QPushButton] = {}
        for idx, (text, label, unread) in enumerate(self._FOLDERS):
            btn = QPushButton(text)
            btn.setObjectName("GmailFilter")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, lb=label, un=unread, nm=text: self._select_folder(lb, un, nm)
            )
            self._folder_group.addButton(btn, idx)
            self._folder_buttons[(label, unread)] = btn
            filters.addWidget(btn)
        inbox_lay.addWidget(filter_host)

        self.email_list = QListWidget()
        self.email_list.setObjectName("GmailList")
        self.email_list.itemSelectionChanged.connect(self._on_email_selected)
        self.email_list.setSpacing(0)
        self.email_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inbox_lay.addWidget(self.email_list, stretch=1)

        self.pagination = PaginationBar(total_pages=1, current_page=1, max_visible=5, parent=self)
        self.pagination.page_changed.connect(self._load_page)
        pagination = QHBoxLayout()
        pagination.addStretch()
        pagination.addWidget(self.pagination)
        pagination.addStretch()
        inbox_lay.addLayout(pagination)

        root.addWidget(self.reader_page, stretch=7)
        root.addWidget(self.inbox_page, stretch=3)
        self.back_btn.setVisible(False)
        QTimer.singleShot(200, self.load_recent)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QFrame#GmailReader {{
                background: rgba(5, 11, 20, 0.88);
                border: 1px solid rgba(182, 196, 255, 0.11);
                border-radius: 10px;
            }}
            QFrame#GmailInbox {{
                background: rgba(8, 17, 29, 0.92);
                border: 1px solid rgba(182, 196, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel#GmailInboxTitle {{
                color: #f8fafc;
                font-size: 17px;
                font-weight: 900;
            }}
            QLabel#GmailCount, QLabel#GmailDate, QLabel#GmailRecipient {{
                color: rgba(188, 198, 238, 0.58);
                font-size: 11px;
            }}
            QLabel#GmailEyebrow {{
                color: #b6c4ff;
                font-size: 11px;
                font-weight: 900;
            }}
            QLabel#GmailSubject {{
                color: #f8fafc;
                font-size: 23px;
                font-weight: 900;
                padding: 16px 0 4px 0;
            }}
            QLabel#GmailSender {{
                color: #e7f3ff;
                font-size: 13px;
                font-weight: 800;
            }}
            QLabel#GmailAvatar {{
                color: #dce1ff;
                background: rgba(94, 130, 255, 0.17);
                border: 1px solid rgba(182, 196, 255, 0.26);
                border-radius: 19px;
                font-size: 14px;
                font-weight: 900;
            }}
            QFrame#GmailDivider {{
                background: rgba(182, 196, 255, 0.12);
                border: none;
            }}
            QLineEdit#GmailSearch {{
                min-height: 34px;
                background: rgba(3, 9, 17, 0.72);
                color: #e8ebff;
                border: 1px solid rgba(182, 196, 255, 0.14);
                border-radius: 6px;
                padding: 0 11px;
                selection-background-color: #5e82ff;
            }}
            QLineEdit#GmailSearch:focus {{
                border-color: rgba(182, 196, 255, 0.55);
            }}
            QPushButton#GmailFilter {{
                min-height: 27px;
                background: rgba(255, 255, 255, 0.04);
                color: rgba(220, 237, 250, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 5px;
                padding: 0 9px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#GmailFilter:hover {{
                color: #f8fafc;
                background: rgba(94, 130, 255, 0.12);
                border-color: rgba(182, 196, 255, 0.24);
            }}
            QPushButton#GmailFilter:checked,
            QPushButton#GmailFilter:checked:hover,
            QPushButton#GmailFilter:checked:focus {{
                color: {C.PRI};
                background: {C.PRI_GHO};
                border-color: rgba(182, 196, 255, 0.55);
                font-weight: 800;
            }}
            QPushButton#GmailCompose {{
                min-height: 28px;
                background: {C.PRI};
                color: #0a0e26;
                border: 1px solid {C.PRI};
                border-radius: 6px;
                padding: 0 12px 0 8px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#GmailCompose:hover {{
                background: #a7afff;
                border-color: #a7afff;
            }}
            QPushButton#GmailIconButton {{
                background: rgba(255, 255, 255, 0.045);
                color: #dce1ff;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                font-size: 20px;
                padding: 0;
            }}
            QPushButton#GmailIconButton:hover {{
                background: rgba(94, 130, 255, 0.14);
                border-color: rgba(182, 196, 255, 0.32);
            }}
            QListWidget#GmailList {{
                background: transparent;
                border: none;
                outline: none;
                padding: 0;
            }}
            QListWidget#GmailList::item {{
                border: none;
                border-bottom: 1px solid rgba(182, 196, 255, 0.09);
                padding: 0;
                margin: 0;
            }}
            QListWidget#GmailList::item:hover {{
                background: rgba(182, 196, 255, 0.055);
            }}
            QListWidget#GmailList::item:selected {{
                background: rgba(94, 130, 255, 0.14);
                border-left: 2px solid #5e82ff;
            }}
            QTextBrowser#GmailPreview {{
                background: transparent;
                color: #d7dbee;
                border: none;
                padding: 18px 3px 8px 3px;
                selection-background-color: rgba(94, 130, 255, 0.45);
            }}
        """ + _scrollbar_qss()

    def _run(self, op: str, fn):
        if op == "list":
            self.status.setText("Cargando…")
            self.pagination.setEnabled(False)

        def worker():
            try:
                result = fn()
                if op == "read_images" and isinstance(result, dict):
                    prepared = dict(result)
                    current = self._rendered_email or {}
                    html_body = str(current.get("html") or "").strip()
                    if html_body:
                        prepared["_prepared_html"] = self._inject_email_images(
                            html_body,
                            prepared.get("inline_images") or [],
                        )
                    result = prepared
            except Exception as exc:
                result = exc
            try:
                self._result_sig.emit(op, result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _sender_parts(self, value: str) -> tuple[str, str]:
        from email.utils import parseaddr

        name, address = parseaddr(str(value or ""))
        name = name.strip().strip('"') or address.split("@", 1)[0] or "Remitente"
        return name, address

    def _short_date(self, value: str, include_time: bool = False) -> str:
        from datetime import datetime
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(str(value or ""))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            now = datetime.now(dt.tzinfo)
            if dt.date() == now.date():
                return dt.strftime("%H:%M")
            if dt.year == now.year:
                return dt.strftime("%d %b" + (", %H:%M" if include_time else ""))
            return dt.strftime("%d %b %Y")
        except Exception:
            text = str(value or "").strip()
            return text[:22]

    def _email_row_widget(self, email: dict) -> QWidget:
        row = QWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(10, 8, 9, 8)
        layout.setSpacing(2)

        if self._list_label in self._OUTGOING:
            name, _address = self._sender_parts(email.get("to", "") or email.get("from", ""))
            sender_name = f"Para: {name}"
        else:
            sender_name, _address = self._sender_parts(email.get("from", ""))
        top = QHBoxLayout()
        top.setSpacing(6)
        sender = QLabel(sender_name)
        sender.setStyleSheet(
            "color:#f3f8fc; background:transparent; font-size:12px; font-weight:800;"
            if email.get("unread")
            else "color:#d0dde7; background:transparent; font-size:12px; font-weight:650;"
        )
        date = QLabel(self._short_date(email.get("date", "")))
        date.setStyleSheet("color:rgba(188,198,238,0.50); background:transparent; font-size:10px;")
        date.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(sender, stretch=1)
        top.addWidget(date)
        layout.addLayout(top)

        subject_text = str(email.get("subject") or "(sin asunto)").strip()
        if len(subject_text) > 54:
            subject_text = subject_text[:53].rstrip() + "…"
        subject = QLabel(("●  " if email.get("unread") else "") + subject_text)
        subject.setStyleSheet(
            "color:#8edbff; background:transparent; font-size:11px; font-weight:750;"
            if email.get("unread")
            else "color:#aebdca; background:transparent; font-size:11px;"
        )
        layout.addWidget(subject)

        snippet_text = re.sub(r"\s+", " ", str(email.get("snippet") or "")).strip()
        if len(snippet_text) > 76:
            snippet_text = snippet_text[:75].rstrip() + "…"
        snippet = QLabel(snippet_text)
        snippet.setStyleSheet("color:rgba(192,199,226,0.48); background:transparent; font-size:10px;")
        layout.addWidget(snippet)
        return row

    def _set_reader_header(self, email: dict):
        sender_name, sender_address = self._sender_parts(email.get("from", ""))
        self.reader_subject.setText(email.get("subject") or "(sin asunto)")
        self.reader_sender.setText(sender_name)
        self.reader_recipient.setText(sender_address)
        self.reader_date.setText(self._short_date(email.get("date", ""), include_time=True))
        initials = "".join(part[:1] for part in sender_name.split()[:2]).upper() or "@"
        self.sender_avatar.setText(initials)

    def _apply_responsive_layout(self):
        narrow = self.width() < 760
        if not narrow:
            self.reader_page.setVisible(True)
            self.inbox_page.setVisible(True)
            self.back_btn.setVisible(False)
            self.inbox_page.setMaximumWidth(360)
            self.inbox_page.setMinimumWidth(285)
            return

        self.inbox_page.setMaximumWidth(16777215)
        self.inbox_page.setMinimumWidth(0)
        if self._compact_reader and self._current_email:
            self.reader_page.setVisible(True)
            self.inbox_page.setVisible(False)
            self.back_btn.setVisible(True)
        else:
            self.reader_page.setVisible(False)
            self.inbox_page.setVisible(True)
            self.back_btn.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _folder_title(self, label: str, unread: bool) -> str:
        for text, lb, un in self._FOLDERS:
            if lb == label and un == unread:
                return text
        return "Bandeja"

    def _select_folder(self, label: str, unread: bool = False, name: str = ""):
        self._show_inbox()
        self._list_label = label
        self._list_unread = unread
        self._list_query = ""
        self.search_input.clear()
        self._folder_name = name or self._folder_title(label, unread)
        btn = self._folder_buttons.get((label, unread))
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._load_page(1)

    def load_inbox(self):
        self._select_folder("INBOX", False)

    def load_recent(self):
        self._select_folder("ALL", False)

    def load_unread(self):
        self._select_folder("INBOX", True)

    def search_emails(self):
        query = self.search_input.text().strip()
        if not query:
            self.load_recent()
            return
        self._show_inbox()
        self._list_label = "ALL"
        self._list_unread = False
        self._list_query = query
        self._folder_name = f"Búsqueda: {query}"
        checked = self._folder_group.checkedButton()
        if checked is not None:
            self._folder_group.setExclusive(False)
            checked.setChecked(False)
            self._folder_group.setExclusive(True)
        self._load_page(1)

    def _open_compose(self, prefill: dict | None = None):
        dialog = GmailComposeDialog(self, prefill=prefill or {})
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Si se envió desde la carpeta Enviados, refrescamos la vista.
            if self._list_label == "SENT":
                self._load_page(1)

    def _load_page(self, page: int):
        if page < 1 or page > self._pages:
            return
        self._show_inbox()
        label = self._list_label
        unread = self._list_unread
        query = self._list_query
        page_size = self._page_size
        self._run(
            "list",
            lambda: __import__(
                "actions.gmail",
                fromlist=["get_email_page"],
            ).get_email_page(
                page=page,
                page_size=page_size,
                label=label,
                unread_only=unread,
                query=query,
            ),
        )

    def _on_email_selected(self):
        item = self.email_list.currentItem()
        if not item:
            return
        email = item.data(Qt.ItemDataRole.UserRole) or {}
        email_id = email.get("id")
        if email_id:
            self._current_email = email
            self._rendered_email = None
            self.reader_folder.setText((self._folder_name or "Bandeja").upper())
            self._set_reader_header(email)
            snippet = html_lib.escape(str(email.get("snippet") or "").strip())
            loading_text = snippet or "Cargando mensaje…"
            self.preview.setHtml(
                f"<p style='color:rgba(188,198,238,.58); margin:24px 0; line-height:1.6;'>{loading_text}</p>"
            )
            self._compact_reader = True
            self._apply_responsive_layout()
            self._run("read", lambda: __import__("actions.gmail", fromlist=["read_email"]).read_email(email_id))

    def _is_complex_email(self, html_body: str) -> bool:
        html_body = str(html_body or "")
        return (
            len(html_body) > 30000
            and (
                "@media" in html_body.lower()
                or len(re.findall(r"<table\b", html_body, flags=re.I)) >= 12
                or "mso-" in html_body.lower()
            )
        )

    def _show_inbox(self):
        self._compact_reader = False
        self._apply_responsive_layout()

    def _handle_result(self, op: str, result):
        if isinstance(result, Exception):
            if op == "list":
                self.status.setText("Error")
                self.pagination.setEnabled(True)
            elif op == "read":
                self.preview.setPlainText(str(result))
            return
        if op == "list":
            page_data = result if isinstance(result, dict) else {"emails": result or []}
            self._items = list(page_data.get("emails") or [])
            self._page = int(page_data.get("page") or 1)
            self._pages = max(1, int(page_data.get("pages") or 1))
            self._total_emails = max(0, int(page_data.get("total") or len(self._items)))
            self.email_list.clear()
            for email in self._items:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, email)
                item.setSizeHint(QSize(0, 76))
                self.email_list.addItem(item)
                self.email_list.setItemWidget(item, self._email_row_widget(email))
            self.status.setText(f"{self._total_emails} correos")
            self.pagination.setEnabled(True)
            self.pagination.set_pages(self._pages, self._page)
            if not self._items:
                self.status.setText("No hay resultados.")
            return
        if op == "read":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            self._rendered_email = dict(result)
            self._set_reader_header(result)
            to_value = str(result.get("to") or "").strip()
            if to_value:
                self.reader_recipient.setText(f"{self.reader_recipient.text()}  ·  para {to_value}")
            cached_html = self._enriched_email_html.get(str(result.get("id") or ""))
            if cached_html:
                enriched = dict(result)
                enriched["_prepared_html"] = cached_html
                self._render_email_body(enriched)
            else:
                self._render_email_body(result)
            if self._is_complex_email(result.get("html", "")):
                email_id = result.get("id")
                width = max(680, self.preview.viewport().width())
                self._run(
                    "render_mail",
                    lambda: __import__(
                        "actions.gmail",
                        fromlist=["render_email_preview"],
                    ).render_email_preview(email_id, result.get("html", ""), width),
                )
                return
            if result.get("html") and not cached_html:
                email_id = result.get("id")
                html_body = str(result.get("html") or "")
                if re.search(r'<img\b[^>]*\bsrc=["\']cid:', html_body, flags=re.I):
                    self._run(
                        "read_images",
                        lambda: __import__(
                            "actions.gmail",
                            fromlist=["read_email_images"],
                        ).read_email_images(email_id),
                    )
                else:
                    self._run(
                        "read_images",
                        lambda: {"id": email_id, "inline_images": []},
                    )
            return
        if op == "read_images":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            enriched = dict(self._rendered_email or {})
            prepared_html = result.get("_prepared_html")
            if prepared_html:
                self._enriched_email_html[str(result.get("id") or "")] = prepared_html
                enriched["_prepared_html"] = prepared_html
                self._render_email_body(enriched)
            return
        if op == "render_mail":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            image_path = str(result.get("image_path") or "")
            if not image_path or not Path(image_path).exists():
                return
            image_url = QUrl.fromLocalFile(image_path).toString()
            display_width = max(320, self.preview.viewport().width() - 18)
            self.preview.setHtml(
                "<html><body style='margin:0;background:#050a12;text-align:center;'>"
                f"<img src='{image_url}' width='{display_width}' style='display:block;margin:0 auto;'>"
                "</body></html>"
            )

    def _render_email_body(self, result: dict):
        html_body = (result.get("_prepared_html") or result.get("html") or "").strip()
        plain_body = (result.get("body") or "").strip()
        if html_body:
            self.preview.setHtml(
                f"""
                <html>
                  <head>
                    <style>
                      body {{
                        font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                        color: {C.TEXT};
                        background: transparent;
                        font-size: 13px;
                        line-height: 1.62;
                        margin: 0;
                        padding: 10px 8px 24px 8px;
                        overflow-wrap: anywhere;
                      }}
                      .mail-content {{ max-width: 780px; margin: 0 auto; }}
                      p {{ margin: 0 0 14px 0; }}
                      a {{ color: #b6c4ff; text-decoration: none; }}
                      a:hover {{ text-decoration: underline; }}
                      img, video {{ max-width: 100%; height: auto; }}
                      table {{ border-collapse: collapse; max-width: 100%; margin: 8px 0; }}
                      td, th {{ border-color: rgba(255,255,255,0.12); padding: 4px; }}
                      blockquote {{
                        margin: 14px 0;
                        padding-left: 14px;
                        border-left: 2px solid rgba(182, 196, 255, 0.35);
                        color: rgba(248, 250, 252, 0.82);
                      }}
                    </style>
                  </head>
                  <body><div class="mail-content">{html_body}</div></body>
                </html>
                """
            )
        else:
            self.preview.setHtml(
                f"""
                <html>
                  <body style="font-family:'{FONT_UI}', '{FONT_UI_FALLBACK}'; color:{C.TEXT}; font-size:13px; white-space:pre-wrap; line-height:1.62; margin:0; padding:10px 8px 24px 8px;">
                    <div style="max-width:780px; margin:0 auto;">{html_lib.escape(plain_body).replace("\n", "<br>")}</div>
                  </body>
                </html>
                """
            )

    def _inject_email_images(self, html_body: str, inline_images: list[dict]) -> str:
        transparent_pixel = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        cid_map = {}
        for img in inline_images:
            cid = str(img.get("cid") or "").strip().lower()
            data_url = str(img.get("data_url") or "").strip()
            if cid and data_url:
                cid_map[cid] = data_url

        def _displayable_data_url(raw: bytes, mime: str) -> str:
            byte_array = QByteArray(raw)
            buffer = QBuffer(byte_array)
            if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
                return ""
            reader = QImageReader(buffer)
            size = reader.size()
            if size.isValid() and size.width() <= 2 and size.height() <= 2:
                return ""
            if mime in {"image/gif", "image/webp"}:
                image = reader.read()
                if image.isNull():
                    return ""
                png_bytes = QByteArray()
                png_buffer = QBuffer(png_bytes)
                png_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if image.save(png_buffer, "PNG"):
                    raw = bytes(png_bytes)
                    mime = "image/png"
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

        def _normalize_data_url(src: str) -> str:
            match = re.match(
                r"data:(image/[^;,]+)(?:;[^,]*)?;base64,(.+)",
                src,
                flags=re.I | re.S,
            )
            if not match:
                return src
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=False)
            except Exception:
                return src
            return _displayable_data_url(raw, match.group(1).lower())

        def _fetch_remote_image(src: str) -> str:
            src = html_lib.unescape(str(src or "").strip())
            if src.startswith("//"):
                src = "https:" + src
            if not src.startswith(("http://", "https://")):
                return src
            cache = getattr(self, "_email_img_cache", None)
            if cache is None:
                cache = {}
                self._email_img_cache = cache
            if src in cache:
                return cache[src]

            cache_dir = MEMORY_DIR / "gmail_images"
            cache_key = hashlib.sha256(src.encode("utf-8", errors="ignore")).hexdigest()
            cached_files = list(cache_dir.glob(f"{cache_key}.*")) if cache_dir.exists() else []
            if cached_files:
                try:
                    cached_file = cached_files[0]
                    raw = cached_file.read_bytes()
                    suffix = cached_file.suffix.lower().lstrip(".") or "png"
                    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
                    data_url = _displayable_data_url(raw, mime)
                    if not data_url:
                        cache[src] = ""
                        return ""
                    cache[src] = data_url
                    return data_url
                except OSError:
                    pass
            try:
                resp = requests.get(
                    src,
                    timeout=12,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"
                        ),
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                    },
                )
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
                if not ctype.startswith("image/") or not resp.content:
                    cache[src] = src
                    return src
                data_url = _displayable_data_url(resp.content, ctype)
                if not data_url:
                    cache[src] = ""
                    return ""
                cache[src] = data_url
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    extension = {
                        "image/jpeg": "jpg",
                        "image/png": "png",
                        "image/gif": "gif",
                        "image/webp": "webp",
                        "image/svg+xml": "svg",
                    }.get(ctype, "img")
                    (cache_dir / f"{cache_key}.{extension}").write_bytes(resp.content)
                except OSError:
                    pass
                return data_url
            except Exception:
                cache[src] = src
                return src

        def _repl(match: re.Match) -> str:
            prefix, src, suffix = match.group(1), match.group(2), match.group(3)
            key = src.strip().lower()
            if key.startswith("cid:"):
                cid = key[4:].strip("<>")
                data_url = cid_map.get(cid)
                if data_url:
                    return f"{prefix}{_normalize_data_url(data_url) or transparent_pixel}{suffix}"
                return match.group(0)
            if key.startswith("data:image/"):
                return f"{prefix}{_normalize_data_url(src) or transparent_pixel}{suffix}"
            if key.startswith(("http://", "https://", "//")):
                return f"{prefix}{_fetch_remote_image(src) or transparent_pixel}{suffix}"
            return match.group(0)

        prepared = re.sub(
            r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])',
            _repl,
            html_body,
            flags=re.I,
        )

        def _css_repl(match: re.Match) -> str:
            prefix, quote, src, suffix = (
                match.group(1),
                match.group(2) or "",
                match.group(3),
                match.group(4),
            )
            key = html_lib.unescape(src.strip()).lower()
            if key.startswith(("http://", "https://", "//")):
                return f"{prefix}{quote}{_fetch_remote_image(src)}{quote}{suffix}"
            return match.group(0)

        return re.sub(
            r'((?:background|background-image)\s*:[^;]*?\burl\(\s*)(["\']?)([^)"\']+)(\s*\))',
            _css_repl,
            prepared,
            flags=re.I,
        )



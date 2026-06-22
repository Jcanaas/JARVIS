from __future__ import annotations

import sys
import time
import re
import threading
from typing import Optional

import requests

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QFontMetrics, QIcon, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QLabel, QMessageBox, QSizePolicy, QScrollArea, QFrame,
    QAbstractItemView, QLineEdit, QFileDialog, QStackedWidget,
)

from .whatsapp import (
    list_recent_chats, get_contact_name, get_conversation, get_message_acks,
    get_profile_picture_url,
    mark_chat_read, media_url, resolve_contact, send_whatsapp, send_whatsapp_media,
    _bridge_qr_status,
)
from .whatsapp_manager import WhatsAppManager
from pathlib import Path
import sounddevice as sd
import numpy as np
import wave
import tempfile
from actions.file_processor import _process_audio

BG = "#080B12"
PANEL = "rgba(255, 255, 255, 0.050)"
PANEL2 = "rgba(255, 255, 255, 0.085)"
BORDER = "rgba(255, 255, 255, 0.080)"
PRI = "#7DD3FC"
ACC = "#A7F3D0"
TEXT = "#F8FAFC"
TEXT_DIM = "#CBD5E1"
TEXT_MED = "#94A3B8"


def _wa_icon(name: str, color: str = TEXT_DIM, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    if name == "search":
        painter.drawEllipse(QRectF(4, 4, 11, 11)); line(14, 14, 20, 20)
    elif name == "refresh":
        painter.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 285 * 16)
        line(17, 4, 20, 4); line(20, 4, 20, 7)
    elif name == "more":
        painter.setBrush(QBrush(QColor(color)))
        for y in (6, 12, 18):
            painter.drawEllipse(QRectF(11, y - 1, 2, 2))
    elif name == "mic":
        painter.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        painter.drawArc(QRectF(6, 8, 12, 10), 180 * 16, 180 * 16)
        line(12, 18, 12, 21); line(9, 21, 15, 21)
    elif name == "send":
        path = QPainterPath()
        path.moveTo(4, 5); path.lineTo(21, 12); path.lineTo(4, 19)
        path.lineTo(7, 12); path.closeSubpath()
        painter.drawPath(path); line(7, 12, 21, 12)
    elif name == "attach":
        painter.save()
        painter.translate(12, 12); painter.rotate(45); painter.translate(-12, -12)
        painter.drawRoundedRect(QRectF(9, 2.5, 6, 19), 3, 3)
        line(12, 6.5, 12, 16)
        painter.restore()
    painter.end()
    return QIcon(pm)


def _wa_icon_button(name: str, tooltip: str, size: int = 38, accent: bool = False) -> QPushButton:
    button = QPushButton()
    button.setFixedSize(size, size)
    button.setIcon(_wa_icon(name, PRI if accent else TEXT_DIM, 19))
    button.setIconSize(QSize(19, 19))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(f"""
        QPushButton {{
            background: {"rgba(56,189,248,0.16)" if accent else "rgba(255,255,255,0.045)"};
            border: 1px solid {"rgba(125,211,252,0.30)" if accent else "rgba(255,255,255,0.09)"};
            border-radius: 10px;
            padding: 0;
        }}
        QPushButton:hover {{
            background: rgba(125,211,252,0.14);
            border-color: rgba(125,211,252,0.34);
        }}
        QPushButton:focus {{ border: 2px solid rgba(125,211,252,0.58); }}
    """)
    return button


class _SendTextEdit(QTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        multiline = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if enter and not multiline:
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _AckIndicator(QWidget):
    def __init__(self, ack: int = -2, parent=None):
        super().__init__(parent)
        self._ack = ack
        self.setFixedSize(19, 12)
        self.setToolTip("Estado de entrega")

    def set_ack(self, ack: int):
        try:
            ack = int(ack)
        except (TypeError, ValueError):
            ack = -2
        if ack != self._ack:
            self._ack = ack
            self.update()

    def paintEvent(self, _event):
        if self._ack < 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#59C7FF" if self._ack >= 3 else "#B8C7D6")
        pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        def draw_check(offset: int):
            path = QPainterPath()
            path.moveTo(offset + 1.5, 6.0)
            path.lineTo(offset + 4.2, 8.7)
            path.lineTo(offset + 9.4, 2.7)
            painter.drawPath(path)

        draw_check(0)
        if self._ack >= 2:
            draw_check(6)


class WhatsAppWindow(QWidget):
    close_requested = pyqtSignal()
    avatar_loaded = pyqtSignal(str, str)
    avatar_image_loaded = pyqtSignal(str, object)
    chats_loaded = pyqtSignal(object, str)
    conversation_loaded = pyqtSignal(str, object, str)
    chat_read_marked = pyqtSignal(str, bool)
    message_send_finished = pyqtSignal(str, str, object, str)
    message_acks_loaded = pyqtSignal(str, object)
    media_preview_loaded = pyqtSignal(str, object, str)
    suggestion_ready = pyqtSignal(str, str, str)
    incoming_message = pyqtSignal(dict)
    bridge_state = pyqtSignal(dict)

    def __init__(self, manager=None, contact: str = "", embedded: bool = False, parent=None):
        super().__init__(parent)
        self.contact = (contact or "").strip()
        self.chat_id = ""
        self.embedded = embedded
        self.chat_mode = bool(self.contact or self.embedded)
        self.current_chat_id = ""
        self.current_chat_name = ""
        self._current_is_group = False
        self._chat_index: dict[str, dict] = {}
        self._chat_items: dict[str, QListWidgetItem] = {}
        self._chat_avatar_labels: dict[str, QLabel] = {}
        self._avatar_url_labels: dict[str, list[QLabel]] = {}
        self._avatar_cache: dict[str, bytes] = {}
        self._avatar_url_loading: set[str] = set()
        self._avatar_image_loading: set[str] = set()
        self._name_cache: dict[str, str] = {}
        self._all_chats: list[dict] = []
        self._conversation_cache: dict[str, tuple[list[dict], str]] = {}
        self._render_queue: list[tuple[dict, str, bool]] = []
        self._render_total_count = 0
        # --- Conversation pagination (load older messages on scroll-up) ---
        self._chat_page_size = 150
        self._chat_fetch_limit: dict[str, int] = {}
        self._chat_has_more: dict[str, bool] = {}
        self._loading_more: set[str] = set()
        self._more_prev_max = 0
        self._render_scroll_mode = "bottom"
        self._message_bubbles: list[QFrame] = []
        self._message_text_labels: list[QLabel] = []
        self._message_meta_labels: dict[str, QLabel] = {}
        self._message_ack_indicators: dict[str, _AckIndicator] = {}
        self._media_preview_labels: dict[str, list[QLabel]] = {}
        self._media_preview_cache: dict[str, bytes] = {}
        self._media_preview_loading: set[str] = set()
        self._chat_filter = "all"
        self._loading_chats = False
        self._loading_chat_ids: set[str] = set()
        self._marking_read_chat_ids: set[str] = set()
        self._loading_acks = False
        self.setWindowTitle('WhatsApp - Chat' if self.chat_mode else 'WhatsApp - Gestor de Pendientes')
        self.resize(800, 420)
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                color: {TEXT};
                font-family: "Segoe UI Variable", "Segoe UI";
                letter-spacing: 0;
            }}
            QLabel {{
                background: transparent;
                color: {TEXT_MED};
                font-size: 12px;
                font-weight: 600;
            }}
            QListWidget, QTextEdit {{
                background: transparent;
                color: {TEXT};
                border: none;
                selection-background-color: transparent;
            }}
            QListWidget::item {{
                border: none;
                padding: 0;
            }}
            QListWidget::item:selected {{
                background: transparent;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QPushButton {{
                background: rgba(255, 255, 255, 0.045);
                color: {TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(125, 211, 252, 0.16);
                border-color: rgba(125, 211, 252, 0.40);
                color: {TEXT};
            }}
            QLineEdit {{
                background: rgba(3, 10, 18, 0.72);
                color: {TEXT};
                border: 1px solid rgba(125, 211, 252, 0.13);
                border-radius: 8px;
                padding: 8px 11px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: rgba(125, 211, 252, 0.45);
                background: rgba(7, 19, 31, 0.92);
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 8px 3px 8px 3px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(125, 211, 252, 0.24);
                border: none;
                border-radius: 3px;
                min-height: 34px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(125, 211, 252, 0.42);
                border-color: rgba(125, 211, 252, 0.45);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
                border: none;
                height: 0px;
            }}
        """)

        # use provided manager or create own
        self.mgr = manager or WhatsAppManager()
        self.avatar_loaded.connect(self._apply_avatar_url)
        self.avatar_image_loaded.connect(self._apply_avatar_image)
        self.chats_loaded.connect(self._apply_loaded_chats)
        self.conversation_loaded.connect(self._apply_loaded_conversation)
        self.chat_read_marked.connect(self._apply_chat_read)
        self.message_send_finished.connect(self._apply_message_send_result)
        self.message_acks_loaded.connect(self._apply_message_acks)
        self.media_preview_loaded.connect(self._apply_media_preview)
        self.suggestion_ready.connect(self._apply_suggested_reply)
        self.incoming_message.connect(self._handle_incoming_message)
        self.bridge_state.connect(self._apply_bridge_state)
        # Polls the bridge while WhatsApp is unlinked so the in-panel QR stays
        # fresh and we auto-advance to the chat list once the phone connects.
        self._qr_poll_timer = QTimer(self)
        self._qr_poll_timer.setInterval(2500)
        self._qr_poll_timer.timeout.connect(self.load_chats)
        self._message_render_timer = QTimer(self)
        self._message_render_timer.setInterval(0)
        self._message_render_timer.timeout.connect(self._render_message_batch)
        self._ack_timer = QTimer(self)
        self._ack_timer.setInterval(2500)
        self._ack_timer.timeout.connect(self._poll_message_acks)
        self._ack_timer.start()
        # Debounce timer to coalesce chat-list refreshes triggered by bursts
        # of incoming messages into a single bridge round-trip.
        self._incoming_refresh_timer = QTimer(self)
        self._incoming_refresh_timer.setSingleShot(True)
        self._incoming_refresh_timer.timeout.connect(self.load_chats)
        # Listen for new incoming messages from the manager (called from its
        # polling thread → marshalled to the GUI thread via the Qt signal).
        self._closing = False
        self._mgr_listener = None
        if self.chat_mode and hasattr(self.mgr, "add_message_listener"):
            def _emit_incoming(entry):
                if getattr(self, "_closing", False):
                    return
                try:
                    self.incoming_message.emit(dict(entry or {}))
                except RuntimeError:
                    pass  # widget already destroyed
            self._mgr_listener = _emit_incoming
            self.mgr.add_message_listener(self._mgr_listener)
            # The panel is recreated with deleteLater() (no closeEvent), so also
            # detach on destruction. The closure captures only the manager and
            # the listener — never the dying widget.
            _mgr_ref = self.mgr
            _listener_ref = self._mgr_listener
            self.destroyed.connect(
                lambda *_: _mgr_ref.remove_message_listener(_listener_ref)
            )

        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_select)

        if self.chat_mode:
            self.detail_label = None
            self.chat_list = QListWidget()
            self.chat_list.setObjectName("WaChatList")
            self.chat_list.setSpacing(0)
            self.chat_list.verticalScrollBar().valueChanged.connect(
                lambda _value: self._load_visible_chat_avatars()
            )
            self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.chat_list.itemSelectionChanged.connect(self._on_chat_select)
            self.chat_list.setStyleSheet("""
                QListWidget#WaChatList {
                    background: transparent;
                    border: none;
                    outline: none;
                    padding: 0;
                }
                QListWidget#WaChatList::item {
                    background: transparent;
                    border: none;
                    border-bottom: 1px solid rgba(125, 211, 252, 0.07);
                    padding: 0;
                }
                QListWidget#WaChatList::item:hover {
                    background: rgba(125, 211, 252, 0.055);
                }
                QListWidget#WaChatList::item:selected {
                    background: rgba(56, 189, 248, 0.12);
                    border-left: 2px solid #38BDF8;
                }
            """)

            self.chat_scroll = QScrollArea()
            self.chat_scroll.setWidgetResizable(True)
            self.chat_scroll.setMinimumHeight(300)
            self.chat_scroll.viewport().setStyleSheet("background: transparent;")
            self.chat_host = QWidget()
            self.chat_host.setObjectName("WaChatHost")
            self.chat_host.setStyleSheet("""
                QWidget#WaChatHost {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #06111B, stop:0.55 #081722, stop:1 #07131D);
                }
            """)
            self.chat_layout = QVBoxLayout(self.chat_host)
            self.chat_layout.setContentsMargins(28, 20, 28, 20)
            self.chat_layout.setSpacing(8)
            self.chat_scroll.setWidget(self.chat_host)
            self.chat_scroll.verticalScrollBar().valueChanged.connect(self._on_chat_scroll)

            self.chat_header = QLabel("Selecciona un chat")
            self.chat_header.setStyleSheet(f"font-size: 15px; font-weight: 750; color: {TEXT};")
            self.chat_subheader = QLabel("Chats recientes")
            self.chat_subheader.setStyleSheet(f"font-size: 11px; color: {TEXT_MED};")
            self.chat_subheader.hide()
        else:
            self.detail_label = QTextEdit()
            self.detail_label.setReadOnly(True)
            self.detail_label.setPlaceholderText('Selecciona un mensaje para ver detalles')
            self.detail_label.setMinimumHeight(190)

        self.reply_edit = _SendTextEdit()
        self.reply_edit.send_requested.connect(self.send_reply)
        self.reply_edit.setPlaceholderText('Escribe un mensaje' if self.chat_mode else 'Escribe la respuesta aqui...')
        if self.chat_mode:
            self.reply_edit.setMinimumHeight(42)
            self.reply_edit.setMaximumHeight(50)
            self.reply_edit.setStyleSheet(f"""
                QTextEdit {{
                    background: rgba(3, 11, 19, 0.76);
                    color: {TEXT};
                    border: 1px solid rgba(125, 211, 252, 0.13);
                    border-radius: 10px;
                    padding: 9px 12px;
                    font-size: 13px;
                }}
                QTextEdit:focus {{
                    background: rgba(5, 16, 27, 0.94);
                    border-color: rgba(125, 211, 252, 0.42);
                }}
            """)

        self.refresh_btn = QPushButton('Refrescar')
        self.refresh_btn.clicked.connect(self.load_pending)
        self.refresh_btn.setVisible(not self.embedded)
        self.close_btn = QPushButton('Cerrar modo')
        self.close_btn.clicked.connect(self.close_requested.emit)
        self.close_btn.setVisible(self.embedded and bool(self.contact))
        self.prepare_btn = QPushButton('Guardar Borrador')
        self.prepare_btn.clicked.connect(self.prepare_draft)
        self.send_btn = QPushButton('Enviar mensaje' if self.contact else 'Enviar (autoriza)')
        self.send_btn.clicked.connect(self.send_reply)
        self.voice_btn = QPushButton('Voz')
        self.voice_btn.clicked.connect(self._toggle_record)
        self.attach_btn = QPushButton('Adjuntar')
        self.attach_btn.setToolTip("Adjuntar archivo")
        self.attach_btn.clicked.connect(self._attach_file)
        self.suggest_btn = QPushButton("Sugerir")
        self.suggest_btn.setToolTip("Generar una respuesta sin enviarla")
        self.suggest_btn.clicked.connect(self.suggest_reply)
        self._recording = False
        self._rec_sr = 16000
        self._rec_buf = None

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.close_btn)
        if not self.contact:
            btn_row.addWidget(self.prepare_btn)
        btn_row.addWidget(self.voice_btn)
        btn_row.addWidget(self.send_btn)

        if self.chat_mode:
            root = QFrame()
            root.setObjectName("WaRoot")
            root.setStyleSheet("""
                QFrame#WaRoot {
                    background: #07131D;
                    border: none;
                    border-radius: 10px;
                }
            """)
            root_lay = QHBoxLayout(root)
            root_lay.setContentsMargins(0, 0, 0, 0)
            root_lay.setSpacing(0)

            chats_panel = QFrame()
            chats_panel.setObjectName("WaChatsPanel")
            chats_panel.setMinimumWidth(310)
            chats_panel.setMaximumWidth(410)
            chats_panel.setStyleSheet("""
                QFrame#WaChatsPanel {
                    background: rgba(8, 20, 30, 0.98);
                    border-left: 1px solid rgba(125, 211, 252, 0.12);
                }
            """)
            chats_col = QVBoxLayout(chats_panel)
            chats_col.setContentsMargins(14, 14, 10, 10)
            chats_col.setSpacing(10)
            chats_head = QHBoxLayout()
            chats_title = QLabel("Chats")
            chats_title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {TEXT};")
            self.chats_refresh = _wa_icon_button("refresh", "Refrescar chats", size=34)
            self.chats_refresh.setToolTip("Refrescar chats")
            self.chats_refresh.clicked.connect(self.load_chats)
            chats_head.addWidget(chats_title)
            chats_head.addStretch()
            chats_head.addWidget(self.chats_refresh)
            chats_col.addLayout(chats_head)

            self.chat_search = QLineEdit()
            self.chat_search.setPlaceholderText("Buscar chats")
            self.chat_search.addAction(_wa_icon("search", TEXT_MED, 16), QLineEdit.ActionPosition.LeadingPosition)
            self.chat_search.textChanged.connect(self._render_chat_list)
            chats_col.addWidget(self.chat_search)

            chips = QHBoxLayout()
            chips.setSpacing(6)
            self.filter_all = self._make_filter_button("Todo", "all")
            self.filter_unread = self._make_filter_button("Sin leer", "unread")
            self.filter_groups = self._make_filter_button("Grupos", "groups")
            chips.addWidget(self.filter_all)
            chips.addWidget(self.filter_unread)
            chips.addWidget(self.filter_groups)
            chips.addStretch()
            chats_col.addLayout(chips)
            chats_col.addWidget(self.chat_list, 1)

            chat_panel = QFrame()
            chat_panel.setObjectName("WaChatPanel")
            chat_panel.setStyleSheet("""
                QFrame#WaChatPanel {
                    background: #07131D;
                    border: none;
                }
            """)
            chat_col = QVBoxLayout(chat_panel)
            chat_col.setContentsMargins(0, 0, 0, 0)
            chat_col.setSpacing(0)

            top_bar = QFrame()
            top_bar.setObjectName("WaTopBar")
            top_bar.setFixedHeight(64)
            top_bar.setStyleSheet("""
                QFrame#WaTopBar {
                    background: rgba(9, 24, 35, 0.98);
                    border-bottom: 1px solid rgba(125, 211, 252, 0.12);
                }
            """)
            top_lay = QHBoxLayout(top_bar)
            top_lay.setContentsMargins(16, 9, 16, 9)
            self.header_avatar = QLabel("?")
            self.header_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.header_avatar.setFixedSize(38, 38)
            self.header_avatar.setScaledContents(True)
            self.header_avatar.setStyleSheet("background: rgba(125,211,252,0.20); border-radius: 19px; color: white; font-weight: 800;")
            title_box = QVBoxLayout()
            title_box.setSpacing(1)
            title_box.addWidget(self.chat_header)
            title_box.addWidget(self.chat_subheader)
            top_lay.addWidget(self.header_avatar)
            top_lay.addLayout(title_box, 1)
            top_lay.addStretch()
            privacy = QLabel("CIFRADO")
            privacy.setStyleSheet("""
                color: rgba(167, 243, 208, 0.82);
                background: rgba(52, 211, 153, 0.08);
                border: 1px solid rgba(52, 211, 153, 0.16);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 9px;
                font-weight: 800;
            """)
            top_lay.addWidget(privacy)
            chat_col.addWidget(top_bar)
            chat_col.addWidget(self.chat_scroll, 1)

            input_bar = QFrame()
            input_bar.setObjectName("WaInputBar")
            input_bar.setFixedHeight(70)
            input_bar.setStyleSheet("""
                QFrame#WaInputBar {
                    background: rgba(9, 24, 35, 0.98);
                    border-top: 1px solid rgba(125, 211, 252, 0.12);
                }
            """)
            input_lay = QHBoxLayout(input_bar)
            input_lay.setContentsMargins(16, 10, 16, 10)
            input_lay.setSpacing(10)
            self.voice_btn.setText("")
            self.voice_btn.setIcon(_wa_icon("mic", TEXT_DIM, 19))
            self.voice_btn.setIconSize(QSize(19, 19))
            self.voice_btn.setToolTip("Grabar mensaje de voz")
            self.voice_btn.setAccessibleName("Grabar mensaje de voz")
            self.voice_btn.setFixedSize(38, 38)
            self.attach_btn.setText("")
            self.attach_btn.setIcon(_wa_icon("attach", TEXT_DIM, 19))
            self.attach_btn.setIconSize(QSize(19, 19))
            self.attach_btn.setToolTip("Adjuntar archivo")
            self.attach_btn.setAccessibleName("Adjuntar archivo")
            self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.attach_btn.setFixedSize(38, 38)
            self.attach_btn.setStyleSheet(self.voice_btn.styleSheet())
            self.send_btn.setText("")
            self.send_btn.setIcon(_wa_icon("send", PRI, 19))
            self.send_btn.setIconSize(QSize(19, 19))
            self.send_btn.setToolTip("Enviar mensaje")
            self.send_btn.setAccessibleName("Enviar mensaje")
            self.send_btn.setFixedSize(42, 38)
            input_lay.addWidget(self.attach_btn)
            input_lay.addWidget(self.voice_btn)
            input_lay.addWidget(self.reply_edit, 1)
            input_lay.addWidget(self.suggest_btn)
            input_lay.addWidget(self.send_btn)
            chat_col.addWidget(input_bar)

            root_lay.addWidget(chat_panel, 1)
            root_lay.addWidget(chats_panel)

            # Stacked: page 0 = normal chat UI, page 1 = in-panel QR linking view
            # shown whenever the WhatsApp bridge has no active session.
            self._wa_stack = QStackedWidget()
            self._wa_stack.addWidget(root)
            self._qr_page = self._build_qr_page()
            self._wa_stack.addWidget(self._qr_page)

            main = QHBoxLayout()
            main.setContentsMargins(0, 0, 0, 0)
            main.addWidget(self._wa_stack)
        else:
            left_col = QVBoxLayout()
            self.left_title = QLabel('Mensajes pendientes')
            left_col.addWidget(self.left_title)
            left_col.addWidget(self.list_widget)

            right_col = QVBoxLayout()
            right_col.addWidget(QLabel('Detalles'))
            right_col.addWidget(self.detail_label)
            right_col.addWidget(QLabel('Borrador de respuesta'))
            right_col.addWidget(self.reply_edit)
            right_col.addLayout(btn_row)

            main = QHBoxLayout()
            main.addLayout(left_col, 2)
            main.addLayout(right_col, 3)

        self.setLayout(main)

        self._last_refresh = 0
        self.load_pending()

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.load_pending)
        self._timer.start(30000 if self.chat_mode else 5000)

    # ------------------------------------------------------------------
    # In-panel WhatsApp linking (QR) — shown when the bridge has no session
    # ------------------------------------------------------------------
    def _build_qr_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("WaQrPage")
        page.setStyleSheet("""
            QFrame#WaQrPage {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #06111B, stop:0.55 #081722, stop:1 #07131D);
                border-radius: 10px;
            }
        """)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 36, 40, 36)
        lay.setSpacing(14)
        lay.addStretch()

        title = QLabel("Vincula WhatsApp")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {TEXT};")
        lay.addWidget(title)

        subtitle = QLabel(
            "Abre WhatsApp en tu telefono → Dispositivos vinculados →\n"
            "Vincular un dispositivo, y escanea este codigo."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_MED};")
        lay.addWidget(subtitle)

        self._qr_image = QLabel()
        self._qr_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_image.setFixedSize(280, 280)
        self._qr_image.setStyleSheet("background: #FFFFFF; border-radius: 10px;")
        lay.addWidget(self._qr_image, 0, Qt.AlignmentFlag.AlignHCenter)

        self._qr_status = QLabel("Conectando con el bridge…")
        self._qr_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_status.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_DIM};")
        lay.addWidget(self._qr_status)

        lay.addStretch()
        return page

    def _render_qr(self, payload: dict):
        """Paint the QR from a /qr bridge payload onto the in-panel view."""
        qr_raw = (payload or {}).get("qr")
        if not payload:
            self._qr_image.clear()
            self._qr_status.setText("Bridge de WhatsApp no disponible. Reintentando…")
            return
        if not qr_raw:
            self._qr_image.clear()
            self._qr_status.setText("Esperando codigo QR…")
            return
        try:
            import io
            import qrcode
            img = qrcode.make(qr_raw)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            pix = QPixmap()
            pix.loadFromData(buf.read())
            pix = pix.scaled(
                264, 264,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._qr_image.setPixmap(pix)
            self._qr_status.setText("Escanea el codigo con tu telefono")
        except Exception as exc:
            self._qr_image.clear()
            self._qr_status.setText(f"No se pudo generar el QR — {exc}")

    def _apply_bridge_state(self, status: dict):
        """React to bridge readiness: show chats or the in-panel QR linker."""
        stack = getattr(self, "_wa_stack", None)
        if stack is None:
            return
        if status.get("ready"):
            # Connected → show the normal chat UI and stop QR polling.
            # (_loading_chats is left set: the worker is still fetching chats and
            #  will reset it via chats_loaded → _apply_loaded_chats.)
            if self._qr_poll_timer.isActive():
                self._qr_poll_timer.stop()
            if stack.currentIndex() != 0:
                stack.setCurrentIndex(0)
        else:
            # Not linked → show QR and keep polling until the phone connects.
            self._loading_chats = False
            stack.setCurrentIndex(1)
            self._render_qr(status)
            if not self._qr_poll_timer.isActive():
                self._qr_poll_timer.start()

    def load_pending(self):
        if self.chat_mode:
            self.load_chats()
            return
        try:
            pend = self.mgr.list_pending()
            self.list_widget.clear()
            for p in pend:
                text = f"{p['from']} — {p['body'][:60].replace('\n',' ')}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, p['id'])
                self.list_widget.addItem(item)
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'No se pudieron cargar pendientes: {e}')

    def load_chats(self):
        if self._loading_chats:
            return
        self._loading_chats = True
        keep_chat_id = self.current_chat_id or self.chat_id or (self.contact if self.contact and '@' in self.contact else "")

        def worker():
            # First confirm the bridge has an active WhatsApp session; if not,
            # surface the in-panel QR linker instead of silently failing.
            try:
                status = _bridge_qr_status()
            except Exception:
                status = {}
            if not status or not status.get("ready"):
                self.bridge_state.emit(status or {})
                return
            self.bridge_state.emit({"ready": True})

            try:
                chats = list_recent_chats(
                    300,
                    timeout=12,
                    include_pictures=False,
                    raise_on_unready=True,
                )
                # Chats that still have locally-tracked pending (un-announced) messages.
                pending_ids = {
                    str(p.get("from") or "").strip()
                    for p in self.mgr.list_pending()
                    if str(p.get("from") or "").strip()
                }

                if not chats and keep_chat_id:
                    chats = [{
                        "chatId": keep_chat_id,
                        "name": self.contact or keep_chat_id,
                        "preview": "",
                        "timestamp": 0,
                        "fromMe": False,
                        "unread": 0,
                    }]

                # The unread count comes straight from WhatsApp (chat.unreadCount),
                # which already reflects messages read on the phone or other devices.
                # We do NOT inflate it from local pending state — that produced
                # false "unread" badges for chats already read elsewhere. Instead,
                # when WhatsApp reports a chat as read we drop our stale pending
                # entries so they stop being counted/announced.
                stale_read_ids = []
                for chat in chats:
                    chat_id = str(chat.get("chatId") or "").strip()
                    if not chat_id:
                        continue
                    bridge_unread = int(chat.get("unread") or 0)
                    chat["unread"] = bridge_unread
                    if bridge_unread == 0 and chat_id in pending_ids:
                        stale_read_ids.append(chat_id)
                if stale_read_ids and hasattr(self.mgr, "mark_chat_read"):
                    for chat_id in stale_read_ids:
                        try:
                            self.mgr.mark_chat_read(chat_id)
                        except Exception:
                            pass
                result = [dict(c) for c in chats if str(c.get("chatId") or "").strip()]
            except Exception:
                result = None
            self.chats_loaded.emit(result, keep_chat_id)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_loaded_chats(self, chats, keep_chat_id: str):
        self._loading_chats = False
        if chats is None:
            return
        if not isinstance(chats, list):
            chats = []
        previous_ids = [str(c.get("chatId") or "") for c in self._all_chats]
        next_ids = [str(c.get("chatId") or "") for c in chats]
        if previous_ids == next_ids:
            changed = False
            for old, new in zip(self._all_chats, chats):
                if (
                    old.get("preview") != new.get("preview")
                    or old.get("timestamp") != new.get("timestamp")
                    or old.get("unread") != new.get("unread")
                    or old.get("name") != new.get("name")
                ):
                    changed = True
                    break
            if not changed:
                return
        self._all_chats = [dict(c) for c in chats]
        for chat in self._all_chats:
            chat_id = str(chat.get("chatId") or "")
            name = str(chat.get("name") or "")
            if chat_id and name:
                self._name_cache[chat_id] = name
        self._render_chat_list()
        if keep_chat_id and keep_chat_id in self._chat_items:
            self.chat_list.setCurrentItem(self._chat_items[keep_chat_id])
        elif self.chat_list.count() and not self.current_chat_id:
            self.chat_list.setCurrentRow(0)
            first = self.chat_list.item(0)
            if first:
                self._open_chat(str(first.data(Qt.ItemDataRole.UserRole) or ""), refresh=False)
        elif not self.chat_list.count():
            self._render_empty("No hay chats recientes para mostrar.")

    def _make_filter_button(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(key == self._chat_filter)
        btn.clicked.connect(lambda _=False, k=key: self._set_chat_filter(k))
        btn.setStyleSheet(self._filter_button_style(key == self._chat_filter))
        return btn

    def _filter_button_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: rgba(56, 189, 248, 0.16);
                    color: {TEXT};
                    border: 1px solid rgba(125, 211, 252, 0.28);
                    border-radius: 7px;
                    padding: 6px 11px;
                    font-size: 11px;
                    font-weight: 700;
                }}
            """
        return f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.035);
                color: {TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.065);
                border-radius: 7px;
                padding: 6px 11px;
                font-size: 11px;
                font-weight: 650;
            }}
            QPushButton:hover {{
                background: rgba(125, 211, 252, 0.13);
                color: {TEXT};
            }}
        """

    def _set_chat_filter(self, key: str):
        self._chat_filter = key
        for btn, btn_key in (
            (getattr(self, "filter_all", None), "all"),
            (getattr(self, "filter_unread", None), "unread"),
            (getattr(self, "filter_groups", None), "groups"),
        ):
            if btn is None:
                continue
            active = btn_key == key
            btn.setChecked(active)
            btn.setStyleSheet(self._filter_button_style(active))
        self._render_chat_list()

    def _render_chat_list(self, *_):
        if not self.chat_mode:
            return
        query = ""
        if hasattr(self, "chat_search"):
            query = self.chat_search.text().strip().lower()
        current = self.current_chat_id
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        self._chat_index.clear()
        self._chat_items.clear()
        self._chat_avatar_labels.clear()
        self._avatar_url_labels.clear()

        rendered = 0
        for chat in self._all_chats:
            chat_id = str(chat.get("chatId") or "").strip()
            name = str(chat.get("name") or chat_id)
            preview = str(chat.get("preview") or "")
            if self._chat_filter == "unread" and not int(chat.get("unread") or 0):
                continue
            if self._chat_filter == "groups" and not bool(chat.get("isGroup")):
                continue
            if query and query not in name.lower() and query not in preview.lower():
                continue
            self._chat_index[chat_id] = dict(chat)
            widget = self._chat_row_widget(chat, load_avatar=rendered < 45)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, chat_id)
            item.setSizeHint(widget.sizeHint())
            self.chat_list.addItem(item)
            self.chat_list.setItemWidget(item, widget)
            self._chat_items[chat_id] = item
            rendered += 1

        self.chat_list.blockSignals(False)
        if current and current in self._chat_items:
            self.chat_list.setCurrentItem(self._chat_items[current])
        QTimer.singleShot(0, self._load_visible_chat_avatars)

    def _load_visible_chat_avatars(self):
        if not self.chat_mode or not hasattr(self, "chat_list"):
            return
        viewport_rect = self.chat_list.viewport().rect()
        for row in range(self.chat_list.count()):
            item = self.chat_list.item(row)
            if item is None or not self.chat_list.visualItemRect(item).intersects(viewport_rect):
                continue
            chat_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            chat = self._chat_index.get(chat_id) or {}
            picture_url = str(chat.get("pictureUrl") or "").strip()
            if picture_url:
                label = self._chat_avatar_labels.get(chat_id)
                if label is not None:
                    labels = self._avatar_url_labels.setdefault(picture_url, [])
                    if label not in labels:
                        labels.append(label)
                self._ensure_avatar_image_async(picture_url)
            elif chat_id:
                self._ensure_avatar_url_async(chat_id)

    def load_conversation(self, chat_id: str | None = None):
        chat_id = (chat_id or self.current_chat_id or self.chat_id or self.contact or "").strip()
        if not chat_id:
            self._render_empty("Selecciona un chat para ver la conversacion.")
            return
        if chat_id in self._conversation_cache:
            cached_msgs, cached_name = self._conversation_cache[chat_id]
            self._apply_loaded_conversation(chat_id, {"messages": cached_msgs, "error": ""}, cached_name)
            return
        if chat_id in self._loading_chat_ids:
            return
        self._loading_chat_ids.add(chat_id)
        limit = self._chat_fetch_limit.setdefault(chat_id, self._chat_page_size)
        self.chat_subheader.setText("Cargando mensajes...")

        def worker():
            msgs = []
            error = ""
            display_name = self.current_chat_name or self.contact or chat_id
            try:
                msgs = get_conversation(chat_id, limit=limit, timeout=30, strict=True)
                display_name = get_contact_name(chat_id) or display_name
            except Exception as exc:
                error = str(exc)
                msgs = []
            self.conversation_loaded.emit(chat_id, {"messages": msgs, "error": error}, display_name)

        threading.Thread(target=worker, daemon=True).start()

    def _on_chat_scroll(self, value: int):
        """Trigger loading older messages when the user scrolls near the top."""
        if not self.chat_mode:
            return
        chat_id = self.current_chat_id
        if not chat_id or chat_id in self._loading_more:
            return
        if chat_id in self._loading_chat_ids or self._message_render_timer.isActive():
            return
        if not self._chat_has_more.get(chat_id):
            return
        bar = self.chat_scroll.verticalScrollBar()
        if bar.maximum() <= 0 or value > 48:
            return
        self._load_more_messages(chat_id)

    def _load_more_messages(self, chat_id: str):
        chat_id = str(chat_id or "").strip()
        if not chat_id or chat_id in self._loading_more:
            return
        if not self._chat_has_more.get(chat_id):
            return
        self._loading_more.add(chat_id)
        self._more_prev_max = self.chat_scroll.verticalScrollBar().maximum()
        # Cap at the render limit (800) — older messages are not rendered anyway.
        new_limit = min(800, self._chat_fetch_limit.get(chat_id, self._chat_page_size) + self._chat_page_size)
        self._chat_fetch_limit[chat_id] = new_limit

        def worker():
            msgs = []
            error = ""
            display_name = self.current_chat_name or self.contact or chat_id
            try:
                msgs = get_conversation(chat_id, limit=new_limit, timeout=30, strict=True)
            except Exception as exc:
                error = str(exc)
                msgs = []
            self.conversation_loaded.emit(chat_id, {"messages": msgs, "error": error}, display_name)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_loaded_conversation(self, chat_id: str, msgs, display_name: str):
        chat_id = str(chat_id or "")
        self._loading_chat_ids.discard(chat_id)
        is_more = chat_id in self._loading_more
        self._loading_more.discard(chat_id)
        if chat_id != self.current_chat_id:
            return
        error = ""
        if isinstance(msgs, dict):
            error = str(msgs.get("error") or "")
            msgs = msgs.get("messages") or []
        msgs = msgs if isinstance(msgs, list) else []
        # If the bridge returned as many messages as we asked for, older ones
        # likely still exist, so keep pagination enabled for this chat.
        requested = self._chat_fetch_limit.get(chat_id, self._chat_page_size)
        self._chat_has_more[chat_id] = bool(msgs) and len(msgs) >= requested and requested < 800
        display_name = str(display_name or self.current_chat_name or self.contact or chat_id)
        self.current_chat_name = display_name
        self.setWindowTitle(f'WhatsApp - {display_name}')
        self.chat_header.setText(display_name)
        self._conversation_cache[chat_id] = ([dict(m) for m in msgs], display_name)
        if not msgs:
            if error:
                self._render_empty(f"No se pudieron cargar mensajes para {display_name}.\n\n{error}")
            else:
                self._render_empty(f"No hay mensajes cargados para {display_name}.")
            return
        self._begin_message_render(msgs, display_name, preserve_scroll=is_more)

    def _begin_message_render(self, msgs: list[dict], display_name: str, preserve_scroll: bool = False):
        self._render_scroll_mode = "preserve" if preserve_scroll else "bottom"
        self._clear_chat()
        self._render_total_count = len(msgs)
        render_msgs = msgs[-800:] if len(msgs) > 800 else msgs
        if self._render_total_count > len(render_msgs):
            notice = QLabel(f"Mostrando los ultimos {len(render_msgs)} de {self._render_total_count} mensajes")
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            notice.setStyleSheet(f"color: {TEXT_MED}; padding: 8px; background: transparent;")
            self.chat_layout.addWidget(notice)
        self._render_queue = []
        last_day = None
        for m in render_msgs:
            day = self._day_key(m.get('timestamp'))
            if day and day != last_day:
                self._render_queue.append(({"_sep": self._day_label(m.get('timestamp'))}, "", False))
                last_day = day
            direction = str(m.get('direction') or '').lower()
            from_me = bool(m.get('fromMe')) or direction == 'out'
            who = 'Yo' if from_me else (m.get('authorName') or m.get('senderName') or display_name)
            self._render_queue.append((m, who, from_me))
        self._render_message_batch()

    def _render_message_batch(self):
        if not self._render_queue:
            if self._message_render_timer.isActive():
                self._message_render_timer.stop()
            self.chat_layout.addStretch()
            if self._render_scroll_mode == "preserve":
                QTimer.singleShot(20, self._restore_scroll_after_more)
            else:
                QTimer.singleShot(20, self._scroll_bottom)
            return
        for _ in range(min(35, len(self._render_queue))):
            m, who, from_me = self._render_queue.pop(0)
            self._add_message_bubble(m, who, from_me)
        if self._render_queue:
            if not self._message_render_timer.isActive():
                self._message_render_timer.start()
        else:
            self._render_message_batch()

    def _format_chat_time(self, raw) -> str:
        try:
            val = int(raw or 0)
            if val > 10_000_000_000:
                val = val // 1000
            return time.strftime("%H:%M", time.localtime(val))
        except Exception:
            return ""

    def _chat_row_widget(self, chat: dict, load_avatar: bool = True) -> QWidget:
        row = QWidget()
        row.setObjectName("WaChatRow")
        row.setStyleSheet("QWidget#WaChatRow { background: transparent; border: none; }")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 10, 9, 10)
        lay.setSpacing(11)

        name = str(chat.get("name") or chat.get("chatId") or "Chat")
        preview = str(chat.get("preview") or "")
        ts = self._format_chat_time(chat.get("timestamp"))
        unread = int(chat.get("unread") or 0)

        avatar = QLabel((name[:1] or "?").upper())
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(44, 44)
        avatar.setScaledContents(True)
        avatar.setStyleSheet(f"""
            QLabel {{
                background: rgba(125, 211, 252, 0.16);
                color: {TEXT};
                border: 1px solid rgba(125, 211, 252, 0.16);
                border-radius: 22px;
                font-weight: 700;
            }}
        """)
        self._chat_avatar_labels[str(chat.get("chatId") or "")] = avatar
        picture_url = str(chat.get("pictureUrl") or "")
        if picture_url:
            self._avatar_url_labels.setdefault(picture_url, []).append(avatar)
        pix = self._avatar_pixmap(picture_url, 44) if load_avatar else None
        if pix is not None:
            avatar.setText("")
            avatar.setPixmap(pix)
        elif load_avatar and chat.get("chatId") and not chat.get("pictureUrl"):
            self._ensure_avatar_url_async(str(chat.get("chatId")))

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; font-size: 13px;")
        name_lbl.setMaximumWidth(205)
        name_lbl.setToolTip(name)
        preview_lbl = QLabel(preview or "Sin mensajes")
        preview_lbl.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px;")
        preview_lbl.setWordWrap(False)
        preview_lbl.setMaximumWidth(220)
        preview_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text_col.addWidget(name_lbl)
        text_col.addWidget(preview_lbl)

        right_col = QVBoxLayout()
        right_col.setSpacing(2)
        time_lbl = QLabel(ts)
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_lbl.setStyleSheet(f"color: {TEXT_MED}; font-size: 10px;")
        right_col.addWidget(time_lbl)
        if unread:
            badge = QLabel("99+" if unread > 99 else str(unread))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedHeight(18)
            # Grow horizontally for 2-3 digit counts so the number is never clipped.
            badge.setMinimumWidth(18)
            badge.setMaximumWidth(34)
            badge.setToolTip(
                "1 mensaje sin leer" if unread == 1 else f"{unread} mensajes sin leer"
            )
            badge.setStyleSheet("""
                QLabel {
                    background: #38BDF8;
                    color: white;
                    border-radius: 9px;
                    font-size: 10px;
                    font-weight: 700;
                    padding: 0 5px;
                }
            """)
            right_col.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight)
        else:
            spacer = QLabel("")
            spacer.setFixedHeight(18)
            right_col.addWidget(spacer)

        lay.addWidget(avatar)
        lay.addLayout(text_col, 1)
        lay.addLayout(right_col)
        return row

    def _avatar_pixmap(self, url: str | None, size: int) -> QPixmap | None:
        url = str(url or "").strip()
        if not url:
            return None
        raw = self._avatar_cache.get(url)
        if raw is None:
            self._ensure_avatar_image_async(url)
            return None
        pix = QPixmap()
        pix.loadFromData(raw)
        if pix.isNull():
            return None
        return self._circular_pixmap(pix, size)

    def _circular_pixmap(self, src: QPixmap, size: int) -> QPixmap:
        """Center-crop to a square, scale, and clip to a circle (no distortion)."""
        size = max(1, int(size))
        side = min(src.width(), src.height())
        if side <= 0:
            return src
        cropped = src.copy(
            (src.width() - side) // 2,
            (src.height() - side) // 2,
            side, side,
        ).scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addEllipse(0.0, 0.0, float(size), float(size))
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        return out

    def _ensure_avatar_image_async(self, url: str):
        url = str(url or "").strip()
        if not url or url in self._avatar_cache or url in self._avatar_image_loading:
            return
        self._avatar_image_loading.add(url)

        def worker():
            raw = b""
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                raw = resp.content
            except Exception:
                raw = b""
            self.avatar_image_loaded.emit(url, raw)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_avatar_image(self, url: str, raw):
        url = str(url or "").strip()
        self._avatar_image_loading.discard(url)
        if not url or not raw:
            return
        self._avatar_cache[url] = bytes(raw)
        for label in list(self._avatar_url_labels.get(url, [])):
            try:
                pix = self._avatar_pixmap(url, 44)
                if pix is not None:
                    label.setText("")
                    label.setPixmap(pix)
            except RuntimeError:
                try:
                    self._avatar_url_labels[url].remove(label)
                except (KeyError, ValueError):
                    pass
        current = self.current_chat_id
        for chat_id, chat in self._chat_index.items():
            if str(chat.get("pictureUrl") or "") != url:
                continue
            label = self._chat_avatar_labels.get(chat_id)
            if label is not None:
                pix = self._avatar_pixmap(url, 44)
                if pix is not None:
                    label.setText("")
                    label.setPixmap(pix)
        if hasattr(self, "header_avatar"):
            chat = self._chat_index.get(current, {})
            if str(chat.get("pictureUrl") or "") == url:
                pix = self._avatar_pixmap(url, 38)
                if pix is not None:
                    self.header_avatar.setText("")
                    self.header_avatar.setPixmap(pix)

    def _ensure_avatar_url_async(self, chat_id: str):
        chat_id = str(chat_id or "").strip()
        if not chat_id or chat_id in self._avatar_url_loading:
            return
        chat = self._chat_index.get(chat_id) or {}
        if chat.get("pictureUrl"):
            return
        self._avatar_url_loading.add(chat_id)

        def worker():
            url = ""
            try:
                url = get_profile_picture_url(chat_id) or ""
            except Exception:
                url = ""
            self.avatar_loaded.emit(chat_id, url)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_avatar_url(self, chat_id: str, url: str):
        chat_id = str(chat_id or "").strip()
        self._avatar_url_loading.discard(chat_id)
        if not chat_id or not url:
            return
        changed = False
        for chat in self._all_chats:
            if str(chat.get("chatId") or "") == chat_id:
                chat["pictureUrl"] = url
                changed = True
                break
        if not changed:
            return
        current = self.current_chat_id
        label = self._chat_avatar_labels.get(chat_id)
        if label is not None:
            self._avatar_url_labels.setdefault(url, []).append(label)
            pix = self._avatar_pixmap(url, 44)
            if pix is not None:
                label.setText("")
                label.setPixmap(pix)
            else:
                self._ensure_avatar_image_async(url)
        if current == chat_id and hasattr(self, "header_avatar"):
            self._avatar_url_labels.setdefault(url, []).append(self.header_avatar)
            pix = self._avatar_pixmap(url, 38)
            if pix is not None:
                self.header_avatar.setText("")
                self.header_avatar.setPixmap(pix)
            else:
                self._ensure_avatar_image_async(url)

    def _update_chat_preview(self, chat_id: str, preview: str):
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return
        now = int(time.time())
        for chat in self._all_chats:
            if str(chat.get("chatId") or "") == chat_id:
                chat["preview"] = preview
                chat["timestamp"] = now
                chat["fromMe"] = True
                break
        else:
            self._all_chats.insert(0, {
                "chatId": chat_id,
                "name": self.current_chat_name or chat_id,
                "preview": preview,
                "timestamp": now,
                "fromMe": True,
                "unread": 0,
            })
        self._all_chats.sort(key=lambda x: int(x.get("timestamp") or 0), reverse=True)
        self._render_chat_list()
        if chat_id in self._chat_items:
            self.chat_list.setCurrentItem(self._chat_items[chat_id])

    def _handle_incoming_message(self, entry: dict):
        """React to a new incoming message detected by the manager (GUI thread)."""
        if not self.chat_mode:
            return
        chat_id = str((entry or {}).get("from") or "").strip()
        if not chat_id:
            return
        # If the affected chat is open, show the message right away.
        if chat_id == self.current_chat_id:
            self._append_incoming_message(chat_id, entry)
            self._mark_open_chat_read(chat_id)
        # Reconcile the chat list (preview, order, unread) with WhatsApp's real
        # state, coalescing bursts into a single refresh.
        self._incoming_refresh_timer.start(800)

    def _append_incoming_message(self, chat_id: str, entry: dict):
        msg = {
            "id": entry.get("id"),
            "from": chat_id,
            "to": "me",
            "chatId": chat_id,
            "author": entry.get("author"),
            "senderName": entry.get("senderName"),
            "body": entry.get("body") or "",
            "type": entry.get("type") or "chat",
            "fromMe": False,
            "direction": "in",
            "timestamp": entry.get("timestamp"),
        }
        cached, name = self._conversation_cache.get(chat_id, (None, self.current_chat_name or chat_id))
        if cached is None:
            return  # conversation not loaded yet; the upcoming load will include it
        mid = str(msg.get("id") or "").strip()
        if mid and any(str(m.get("id") or "") == mid for m in cached):
            return  # already present (dedup against poll/refresh)
        cached = [dict(m) for m in cached] + [msg]
        self._conversation_cache[chat_id] = (cached, name)
        # Render the bubble only if this chat is on screen and idle.
        if (
            chat_id == self.current_chat_id
            and chat_id not in self._loading_chat_ids
            and not self._message_render_timer.isActive()
        ):
            who = msg.get("senderName") or name or chat_id
            self._add_message_bubble(msg, who, False)
            QTimer.singleShot(20, self._scroll_bottom)

    def _mark_open_chat_read(self, chat_id: str):
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return

        def worker():
            try:
                mark_chat_read(chat_id)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
        if self.mgr is not None and hasattr(self.mgr, "mark_chat_read"):
            try:
                self.mgr.mark_chat_read(chat_id)
            except Exception:
                pass

    def closeEvent(self, event):
        # Detach from the manager so it stops emitting into a dead widget.
        self._closing = True
        if self._mgr_listener is not None and hasattr(self.mgr, "remove_message_listener"):
            try:
                self.mgr.remove_message_listener(self._mgr_listener)
            except Exception:
                pass
            self._mgr_listener = None
        super().closeEvent(event)

    def _open_chat(self, chat_id: str, refresh: bool = True):
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return
        already_current = chat_id == self.current_chat_id
        if self._message_render_timer.isActive():
            self._message_render_timer.stop()
        self._render_queue = []
        self.current_chat_id = chat_id
        self.chat_id = chat_id
        chat = self._chat_index.get(chat_id, {})
        self.current_chat_name = str(chat.get("name") or self._name_cache.get(chat_id) or chat_id)
        self._current_is_group = bool(chat.get("isGroup")) or chat_id.endswith("@g.us")
        self.chat_header.setText(self.current_chat_name)
        self._mark_chat_read(chat_id)
        if chat_id not in self._conversation_cache:
            self.chat_subheader.setText("Cargando conversacion...")
            self._render_empty("Cargando mensajes...")
        if hasattr(self, "header_avatar"):
            self.header_avatar.setText((self.current_chat_name[:1] or "?").upper())
            self.header_avatar.setPixmap(QPixmap())
            picture_url = str(chat.get("pictureUrl") or "")
            if picture_url:
                self._avatar_url_labels.setdefault(picture_url, []).append(self.header_avatar)
            pix = self._avatar_pixmap(picture_url, 38)
            if pix is not None:
                self.header_avatar.setText("")
                self.header_avatar.setPixmap(pix)
            elif chat_id:
                self._ensure_avatar_url_async(chat_id)
        if already_current and chat_id in self._conversation_cache:
            return
        if refresh:
            self.load_conversation(chat_id)
        else:
            self.load_conversation(chat_id)

    def _mark_chat_read(self, chat_id: str):
        chat_id = str(chat_id or "").strip()
        if not chat_id or chat_id in self._marking_read_chat_ids:
            return
        chat = self._chat_index.get(chat_id, {})
        if not int(chat.get("unread") or 0):
            return
        self._marking_read_chat_ids.add(chat_id)

        def worker():
            success = mark_chat_read(chat_id)
            self.chat_read_marked.emit(chat_id, success)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_chat_read(self, chat_id: str, success: bool):
        chat_id = str(chat_id or "").strip()
        self._marking_read_chat_ids.discard(chat_id)
        if not success:
            return
        for chat in self._all_chats:
            if str(chat.get("chatId") or "") == chat_id:
                chat["unread"] = 0
                break
        if self.mgr is not None and hasattr(self.mgr, "mark_chat_read"):
            self.mgr.mark_chat_read(chat_id)
        self._render_chat_list()

    def _clear_chat(self):
        self._message_bubbles.clear()
        self._message_text_labels.clear()
        self._message_meta_labels.clear()
        self._message_ack_indicators.clear()
        self._media_preview_labels.clear()
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_empty(self, text: str):
        self._clear_chat()
        self.chat_layout.addStretch()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMaximumWidth(430)
        label.setStyleSheet(f"""
            color: {TEXT_MED};
            background: rgba(125, 211, 252, 0.035);
            border: 1px solid rgba(125, 211, 252, 0.08);
            border-radius: 10px;
            padding: 22px 26px;
            font-size: 12px;
        """)
        self.chat_layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.chat_layout.addStretch()

    def _fmt_ts(self, raw) -> str:
        try:
            val = int(raw or 0)
            if val > 10_000_000_000:
                val = val // 1000
            return time.strftime("%H:%M", time.localtime(val))
        except Exception:
            return ""

    def _day_key(self, raw) -> str:
        try:
            val = int(raw or 0)
            if val > 10_000_000_000:
                val = val // 1000
            if val <= 0:
                return ""
            return time.strftime("%Y-%m-%d", time.localtime(val))
        except Exception:
            return ""

    def _day_label(self, raw) -> str:
        key = self._day_key(raw)
        if not key:
            return ""
        if key == time.strftime("%Y-%m-%d", time.localtime()):
            return "Hoy"
        if key == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
            return "Ayer"
        try:
            val = int(raw or 0)
            if val > 10_000_000_000:
                val = val // 1000
            return time.strftime("%d %b %Y", time.localtime(val))
        except Exception:
            return key

    def _add_day_separator(self, text: str):
        if not text:
            return
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        row.addStretch()
        pill = QLabel(text)
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            "color: rgba(203, 213, 225, 0.78);"
            "background: rgba(9, 24, 35, 0.92);"
            "border: 1px solid rgba(125, 211, 252, 0.12);"
            "border-radius: 10px;"
            "padding: 4px 12px;"
            "font-size: 10px; font-weight: 700;"
        )
        row.addWidget(pill)
        row.addStretch()
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setLayout(row)
        self.chat_layout.addWidget(wrapper)

    def _outgoing_meta_text(self, msg: dict) -> tuple[str, bool]:
        timestamp = self._fmt_ts(msg.get("timestamp"))
        if msg.get("_failed"):
            suffix, read = "no enviado", False
        elif msg.get("_pending"):
            suffix, read = "enviando", False
        else:
            try:
                ack = int(msg.get("ack"))
            except (TypeError, ValueError):
                ack = -2
            if ack >= 1:
                suffix, read = "", ack >= 3
            elif ack == 0:
                suffix, read = "pendiente", False
            else:
                suffix, read = "", False
        return (f"{timestamp} · {suffix}" if timestamp and suffix else timestamp or suffix), read

    def _poll_message_acks(self):
        chat_id = self.current_chat_id
        if not chat_id or chat_id in self._loading_chat_ids or self._loading_acks:
            return
        cached, _name = self._conversation_cache.get(chat_id, ([], ""))
        ids = [
            str(msg.get("id") or "")
            for msg in cached[-500:]
            if (msg.get("fromMe") or str(msg.get("direction") or "").lower() == "out")
            and msg.get("id")
            and not str(msg.get("id")).startswith("local-")
        ]
        if not ids:
            return
        self._loading_acks = True

        def worker():
            try:
                acks = get_message_acks(ids)
            except Exception:
                acks = {}
            self.message_acks_loaded.emit(chat_id, acks)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_message_acks(self, chat_id: str, acks):
        self._loading_acks = False
        if chat_id != self.current_chat_id or not isinstance(acks, dict):
            return
        cached, name = self._conversation_cache.get(chat_id, ([], self.current_chat_name))
        changed = False
        updated = []
        for raw in cached:
            message = dict(raw)
            message_id = str(message.get("id") or "")
            if message_id in acks and message.get("ack") != acks[message_id]:
                message["ack"] = acks[message_id]
                changed = True
            updated.append(message)
            label = self._message_meta_labels.get(message_id)
            if label is not None and message_id in acks:
                text, _read = self._outgoing_meta_text(message)
                label.setText(text)
                label.setStyleSheet(
                    "color: rgba(248, 250, 252, 0.68); font-size: 10px;"
                    "background: transparent; padding-top: 1px;"
                )
            indicator = self._message_ack_indicators.get(message_id)
            if indicator is not None and message_id in acks:
                indicator.set_ack(acks[message_id])
        if changed:
            self._conversation_cache[chat_id] = (updated, name)

    def _add_message_bubble(self, msg: dict, who: str, from_me: bool):
        if msg.get("_sep"):
            self._add_day_separator(str(msg.get("_sep")))
            return
        row = QHBoxLayout()
        row.setContentsMargins(10, 2, 10, 2)
        row.setSpacing(10)
        if from_me:
            row.addStretch()

        bubble = QFrame()
        bubble.setObjectName("msgOut" if from_me else "msgIn")
        bubble_w = self._bubble_max_width()
        bubble.setMinimumWidth(0)
        bubble.setMaximumWidth(bubble_w)
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        bubble.setStyleSheet(f"""
            QFrame#msgOut {{
                background: rgba(22, 91, 151, 0.76);
                border: 1px solid rgba(125, 211, 252, 0.13);
                border-radius: 10px;
            }}
            QFrame#msgIn {{
                background: rgba(16, 31, 43, 0.98);
                border: 1px solid rgba(255, 255, 255, 0.055);
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(bubble)
        lay.setContentsMargins(13, 9, 11, 7)
        lay.setSpacing(6)

        body = self._format_mentions((msg.get("body") or "").strip(), msg)
        if body:
            text = QLabel(body)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setAlignment(Qt.AlignmentFlag.AlignRight if from_me else Qt.AlignmentFlag.AlignLeft)
            text.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 430; background: transparent;")
            text_w = self._message_text_width(text, body, bubble_w)
            text.setMinimumWidth(text_w)
            text.setMaximumWidth(text_w)
            lay.addWidget(text)
            self._message_text_labels.append(text)

        if msg.get("hasMedia") or msg.get("mediaUrl"):
            self._add_media_widget(lay, msg)

        if not body and not (msg.get("hasMedia") or msg.get("mediaUrl")):
            text = QLabel(f"[{msg.get('type', 'mensaje')}]")
            text.setStyleSheet(f"color: {TEXT_DIM};")
            lay.addWidget(text)

        failed = bool(msg.get("_failed"))
        if from_me:
            meta_text, _read = self._outgoing_meta_text(msg)
        else:
            meta_text = self._fmt_ts(msg.get('timestamp'))
        if not from_me and who and getattr(self, "_current_is_group", False):
            meta_text = f"{who} · {meta_text}" if meta_text else who
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(4)
        meta_row.addStretch()
        meta = QLabel(meta_text)
        meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta.setStyleSheet(f"""
            QLabel {{
                color: {'#fca5a5' if failed else ('rgba(248, 250, 252, 0.68)' if from_me else 'rgba(203, 213, 225, 0.62)')};
                font-size: 10px;
                background: transparent;
                padding-top: 1px;
            }}
        """)
        meta_row.addWidget(meta)
        message_id = str(msg.get("id") or "").strip()
        if from_me and message_id:
            self._message_meta_labels[message_id] = meta
            try:
                ack_value = int(msg.get("ack"))
            except (TypeError, ValueError):
                ack_value = -2
            indicator = _AckIndicator(ack_value)
            meta_row.addWidget(indicator)
            self._message_ack_indicators[message_id] = indicator
        lay.addLayout(meta_row)

        row.addWidget(bubble)
        if not from_me:
            row.addStretch()
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setLayout(row)
        self.chat_layout.addWidget(wrapper)
        self._message_bubbles.append(bubble)

    def _bubble_max_width(self) -> int:
        try:
            viewport_w = int(self.chat_scroll.viewport().width() or 0)
        except Exception:
            viewport_w = 900
        return max(300, int(viewport_w * 0.58))

    def _message_text_width(self, label: QLabel, body: str, bubble_w: int) -> int:
        max_w = max(80, int(bubble_w) - 22)
        try:
            metrics = QFontMetrics(label.font())
            longest = max((metrics.horizontalAdvance(part) for part in str(body or "").splitlines()), default=0)
        except Exception:
            longest = len(str(body or "")) * 7
        natural = longest + 4
        return max(24, min(max_w, natural))

    def _update_bubble_widths(self):
        width = self._bubble_max_width()
        for bubble in list(self._message_bubbles):
            try:
                bubble.setMaximumWidth(width)
                bubble.setMinimumWidth(0)
            except RuntimeError:
                try:
                    self._message_bubbles.remove(bubble)
                except ValueError:
                    pass
        for label in list(self._message_text_labels):
            try:
                text_w = self._message_text_width(label, label.text(), width)
                label.setMinimumWidth(text_w)
                label.setMaximumWidth(text_w)
            except RuntimeError:
                try:
                    self._message_text_labels.remove(label)
                except ValueError:
                    pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.chat_mode:
            QTimer.singleShot(0, self._update_bubble_widths)

    def _format_mentions(self, body: str, msg: dict) -> str:
        if not body:
            return ""
        mentions = msg.get("mentions") or {}
        if not isinstance(mentions, dict):
            mentions = {}
        mentioned_ids = msg.get("mentionedIds") or []
        if isinstance(mentioned_ids, list):
            for raw_id in mentioned_ids:
                raw_id = str(raw_id or "").strip()
                if not raw_id or mentions.get(raw_id):
                    continue
                name = self._name_cache.get(raw_id, "")
                if name:
                    mentions[raw_id] = name
        for phone in re.findall(r"@(\d{6,16})", body):
            raw_id = f"{phone}@c.us"
            if mentions.get(raw_id):
                continue
            name = self._name_cache.get(raw_id, "")
            if name:
                mentions[raw_id] = name
        out = body
        for raw_id, name in mentions.items():
            if not name:
                continue
            phone = str(raw_id).split("@", 1)[0]
            out = out.replace(f"@{phone}", f"@{name}")
            out = out.replace(f"@{raw_id}", f"@{name}")
        return out

    def _cached_contact_name(self, raw_id: str) -> str:
        raw_id = str(raw_id or "").strip()
        if not raw_id:
            return ""
        if raw_id in self._name_cache:
            return self._name_cache[raw_id]
        return ""

    def _add_media_widget(self, lay: QVBoxLayout, msg: dict):
        url = media_url(msg.get("mediaUrl") or "")
        msg_type = str(msg.get("type") or "media").lower()
        if not url:
            label = QLabel(f"[{msg_type}] media no disponible")
            label.setStyleSheet(f"color: {TEXT_DIM};")
            lay.addWidget(label)
            return

        visual_types = {"image", "sticker", "video"}
        if msg_type in visual_types:
            preview = QLabel("Cargando vista previa...")
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedSize(300, 180 if msg_type == "video" else 230)
            preview.setStyleSheet("""
                QLabel {
                    color: rgba(203, 213, 225, 0.72);
                    background: rgba(2, 8, 15, 0.52);
                    border: 1px solid rgba(255, 255, 255, 0.07);
                    border-radius: 8px;
                    font-size: 11px;
                }
            """)
            lay.addWidget(preview)
            self._media_preview_labels.setdefault(url, []).append(preview)
            cached = self._media_preview_cache.get(url)
            if cached:
                self._set_media_preview(preview, cached, msg_type)
            else:
                self._load_media_preview(url, msg_type)
        else:
            title = QLabel({
                "audio": "Audio",
                "ptt": "Nota de voz",
                "document": "Documento",
            }.get(msg_type, "Archivo multimedia"))
            title.setStyleSheet(
                f"color: {TEXT}; background: rgba(2, 8, 15, 0.35);"
                "border: 1px solid rgba(255,255,255,0.07); border-radius: 8px;"
                "padding: 12px 14px; font-size: 12px; font-weight: 650;"
            )
            lay.addWidget(title)

        btn = QPushButton("Abrir")
        btn.setToolTip("Abrir archivo multimedia")
        btn.setAccessibleName("Abrir archivo multimedia")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                color: rgba(226, 232, 240, 0.88);
                background: transparent;
                border: none;
                border-radius: 7px;
                padding: 5px 8px;
                text-align: right;
                font-size: 10px;
                font-weight: 650;
            }
            QPushButton:hover {
                color: #7DD3FC;
                background: rgba(125, 211, 252, 0.08);
            }
        """)
        btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _load_media_preview(self, url: str, msg_type: str):
        if not url or url in self._media_preview_loading:
            return
        self._media_preview_loading.add(url)

        def worker():
            raw = b""
            error = ""
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                raw = response.content
                if msg_type == "video" and raw:
                    import cv2
                    suffix = ".mp4"
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                        handle.write(raw)
                        temp_path = handle.name
                    try:
                        capture = cv2.VideoCapture(temp_path)
                        ok, frame = capture.read()
                        capture.release()
                        if not ok or frame is None:
                            raise RuntimeError("No se pudo extraer una miniatura")
                        ok, encoded = cv2.imencode(".jpg", frame)
                        if not ok:
                            raise RuntimeError("No se pudo codificar la miniatura")
                        raw = encoded.tobytes()
                    finally:
                        try:
                            Path(temp_path).unlink()
                        except OSError:
                            pass
            except Exception as exc:
                error = str(exc)
                raw = b""
            self.media_preview_loaded.emit(url, raw, f"{msg_type}|{error}")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_media_preview(self, url: str, raw, result: str):
        self._media_preview_loading.discard(url)
        msg_type, _, error = str(result or "").partition("|")
        if raw:
            self._media_preview_cache[url] = bytes(raw)
        for label in list(self._media_preview_labels.get(url, [])):
            try:
                if raw:
                    self._set_media_preview(label, bytes(raw), msg_type)
                else:
                    label.setText("Vista previa no disponible")
                    label.setToolTip(error)
            except RuntimeError:
                self._media_preview_labels[url].remove(label)

    def _set_media_preview(self, label: QLabel, raw: bytes, msg_type: str):
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw):
            label.setText("Vista previa no disponible")
            return
        target = label.size()
        scaled = pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if msg_type == "video":
            canvas = QPixmap(target)
            canvas.fill(QColor("#07131D"))
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            x = (target.width() - scaled.width()) // 2
            y = (target.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            center = QPointF(target.width() / 2, target.height() / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(3, 12, 22, 205))
            painter.drawEllipse(center, 27, 27)
            painter.setBrush(QColor("#F8FAFC"))
            play = QPainterPath()
            play.moveTo(center.x() - 6, center.y() - 10)
            play.lineTo(center.x() + 11, center.y())
            play.lineTo(center.x() - 6, center.y() + 10)
            play.closeSubpath()
            painter.drawPath(play)
            painter.end()
            scaled = canvas
        label.setText("")
        label.setPixmap(scaled)

    def _scroll_bottom(self):
        try:
            bar = self.chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _restore_scroll_after_more(self):
        """Keep the previously visible message in place after prepending older ones."""
        try:
            bar = self.chat_scroll.verticalScrollBar()
            delta = bar.maximum() - self._more_prev_max
            bar.setValue(max(0, delta))
        except Exception:
            pass

    def _on_select(self):
        if self.chat_mode:
            return
        items = self.list_widget.selectedItems()
        if not items:
            self.detail_label.setText('Selecciona un mensaje para ver detalles')
            self.reply_edit.clear()
            return
        item = items[0]
        mid = item.data(Qt.ItemDataRole.UserRole)
        entry = self.mgr.get(mid)
        if not entry:
            self.detail_label.setText('Mensaje no encontrado')
            return
        body = entry.get('body', '')
        from_ = entry.get('from')
        ts = entry.get('timestamp')
        draft = entry.get('draft') or ''
        self.detail_label.setText(f"De: {from_}\nTimestamp: {ts}\n\n{body}")
        self.reply_edit.setPlainText(draft)

    def _on_chat_select(self):
        if not self.chat_mode:
            return
        items = self.chat_list.selectedItems()
        if not items:
            return
        chat_id = str(items[0].data(Qt.ItemDataRole.UserRole) or "").strip()
        if chat_id and chat_id != self.current_chat_id:
            self._open_chat(chat_id, refresh=True)

    def prepare_draft(self):
        items = self.list_widget.selectedItems()
        if not items:
            QMessageBox.information(self, 'Info', 'Selecciona primero un mensaje')
            return
        item = items[0]
        mid = item.data(Qt.ItemDataRole.UserRole)
        text = self.reply_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, 'Info', 'Escribe un borrador antes de guardar')
            return
        try:
            self.mgr.prepare_reply(mid, text)
            QMessageBox.information(self, 'OK', 'Borrador guardado')
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'No se pudo guardar borrador: {e}')

    def _toggle_record(self):
        if not self._recording:
            # start recording
            self._recording = True
            self.voice_btn.setText('Parar')
            self._rec_buf = []
            self._rec_stream = sd.InputStream(samplerate=self._rec_sr, channels=1, callback=self._rec_callback)
            self._rec_stream.start()
        else:
            # stop
            self._rec_stream.stop()
            self._rec_stream.close()
            self._recording = False
            self.voice_btn.setText('Voz')
            # write temp wav
            try:
                tmp = tempfile.mktemp(suffix='.wav')
                data = np.concatenate(self._rec_buf, axis=0)
                # normalize to int16
                data_i16 = np.int16(data * 32767)
                with wave.open(tmp, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self._rec_sr)
                    wf.writeframes(data_i16.tobytes())
                # transcribe using existing helper
                res = _process_audio(Path(tmp), 'transcribe', {}, speak=None)
                # put into reply edit
                if isinstance(res, str):
                    # if transcription saved, _process_audio returns message; try to read saved file
                    if 'Transcription saved:' in res:
                        # extract filename
                        txt = res.split('Preview:')[-1].strip()
                        self.reply_edit.setPlainText(txt)
                    else:
                        self.reply_edit.setPlainText(res)
                else:
                    self.reply_edit.setPlainText(str(res))
            except Exception as e:
                QMessageBox.warning(self, 'Error', f'Transcription failed: {e}')

    def _rec_callback(self, indata, frames, time_info, status):
        # indata is float32 numpy array
        try:
            self._rec_buf.append(indata.copy())
        except Exception:
            pass

    def suggest_reply(self):
        if not self.chat_mode:
            return
        chat_id = (self.current_chat_id or self.chat_id or self.contact or "").strip()
        if not chat_id or "@" not in chat_id:
            QMessageBox.information(self, "Info", "Selecciona un chat primero.")
            return
        cached, _name = self._conversation_cache.get(chat_id, ([], ""))
        messages = [dict(message) for message in cached[-24:]]
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.setText("Pensando...")

        def worker():
            text = ""
            error = ""
            try:
                from .whatsapp_ai import generate_whatsapp_reply

                text = generate_whatsapp_reply(chat_id, messages=messages)
            except Exception as exc:
                error = str(exc)
            self.suggestion_ready.emit(chat_id, text, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_suggested_reply(self, chat_id: str, text: str, error: str):
        self.suggest_btn.setEnabled(True)
        self.suggest_btn.setText("Sugerir")
        if error:
            QMessageBox.warning(self, "Respuesta sugerida", error)
            return
        if chat_id != self.current_chat_id:
            return
        self.reply_edit.setPlainText(text)
        self.reply_edit.setFocus()
        cursor = self.reply_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.reply_edit.setTextCursor(cursor)

    def _attach_file(self):
        if not self.chat_mode:
            return
        target = self.current_chat_id or self.chat_id or self.contact
        if not target or "@" not in target:
            QMessageBox.information(self, 'Info', 'Selecciona un chat primero')
            return
        path, _ = QFileDialog.getOpenFileName(self, "Adjuntar archivo", "", "Todos los archivos (*.*)")
        if not path:
            return
        filename = Path(path).name
        caption = self.reply_edit.toPlainText().strip()
        self.reply_edit.clear()
        to_id = target
        label = caption or filename
        optimistic = {
            "id": f"local-{int(time.time() * 1000)}",
            "from": "me",
            "to": to_id,
            "chatId": to_id,
            "body": f"\U0001F4CE {label}",
            "type": "document",
            "fromMe": True,
            "direction": "out",
            "timestamp": int(time.time()),
            "_pending": True,
        }
        cached, name = self._conversation_cache.get(target, ([], self.current_chat_name or target))
        cached = [dict(m) for m in cached] + [optimistic]
        self._conversation_cache[target] = (cached, name)
        if target == self.current_chat_id:
            self._add_message_bubble(optimistic, "Yo", True)
            QTimer.singleShot(20, self._scroll_bottom)
        self._update_chat_preview(target, f"\U0001F4CE {label}")

        def worker():
            response = {}
            error = ""
            try:
                response = send_whatsapp_media(to=to_id, file_path=path, caption=caption)
            except Exception as exc:
                error = str(exc)
            self.message_send_finished.emit(optimistic["id"], target, response, error)

        threading.Thread(target=worker, daemon=True).start()

    def send_reply(self):
        if self.chat_mode:
            text = self.reply_edit.toPlainText().strip()
            if not text:
                return
            target = self.current_chat_id or self.chat_id or self.contact
            if not target:
                QMessageBox.information(self, 'Info', 'Selecciona un chat primero')
                return
            if "@" not in target:
                QMessageBox.warning(self, "Error", "El chat no tiene un identificador de WhatsApp válido.")
                return
            to_id = target
            self.reply_edit.clear()
            optimistic = {
                "id": f"local-{int(time.time() * 1000)}",
                "from": "me",
                "to": to_id,
                "chatId": to_id,
                "body": text,
                "type": "chat",
                "fromMe": True,
                "direction": "out",
                "timestamp": int(time.time()),
                "_pending": True,
            }
            cached, name = self._conversation_cache.get(target, ([], self.current_chat_name or target))
            cached = [dict(m) for m in cached] + [optimistic]
            self._conversation_cache[target] = (cached, name)
            if target == self.current_chat_id:
                self._add_message_bubble(optimistic, "Yo", True)
                QTimer.singleShot(20, self._scroll_bottom)
            self._update_chat_preview(target, text)

            def worker():
                response = {}
                error = ""
                try:
                    response = send_whatsapp(to=to_id, body=text)
                except Exception as exc:
                    error = str(exc)
                self.message_send_finished.emit(optimistic["id"], target, response, error)

            threading.Thread(target=worker, daemon=True).start()
            return

        items = self.list_widget.selectedItems()
        if not items:
            return
        item = items[0]
        mid = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.mgr.send_reply(mid)
            self.load_pending()
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'No se pudo enviar: {e}')

    def _apply_message_send_result(self, local_id: str, chat_id: str, response, error: str):
        cached, name = self._conversation_cache.get(chat_id, ([], self.current_chat_name or chat_id))
        updated = []
        found = False
        for raw in cached:
            message = dict(raw)
            if str(message.get("id") or "") == local_id:
                found = True
                message.pop("_pending", None)
                if error:
                    message["_failed"] = True
                else:
                    message.pop("_failed", None)
                    if isinstance(response, dict) and response.get("id"):
                        message["id"] = response["id"]
                    if isinstance(response, dict) and response.get("ack") is not None:
                        message["ack"] = response["ack"]
            updated.append(message)
        if not found:
            return
        self._conversation_cache[chat_id] = (updated, name)
        if chat_id == self.current_chat_id:
            self._apply_loaded_conversation(
                chat_id,
                {"messages": updated, "error": ""},
                name,
            )


def main():
    app = QApplication(sys.argv)
    w = WhatsAppWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

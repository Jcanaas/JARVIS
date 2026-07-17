from __future__ import annotations

import sys
import time
import re
import threading
from typing import Optional

import requests

from PyQt6.QtCore import (
    QBuffer, QByteArray, QEasingCurve, QIODevice, QPointF, QPropertyAnimation,
    QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QFontMetrics, QIcon, QMovie, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QLabel, QMessageBox, QSizePolicy, QScrollArea, QFrame,
    QAbstractItemView, QLineEdit, QFileDialog, QStackedWidget, QSlider,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QMenu, QDialog,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from .whatsapp import (
    list_recent_chats, get_contact_name, get_conversation, get_message_acks,
    get_profile_picture_url,
    mark_chat_read, media_url, resolve_contact, send_whatsapp, send_whatsapp_media,
    edit_whatsapp_message,
    _bridge_qr_status,
)
from .whatsapp_manager import WhatsAppManager
from pathlib import Path
import sounddevice as sd
import numpy as np
import wave
import tempfile
from actions.file_processor import _process_audio
from actions.perf_helpers import DiskImageCache

BG = "#0A0C16"
PANEL = "rgba(255, 255, 255, 0.050)"
PANEL2 = "rgba(255, 255, 255, 0.085)"
BORDER = "rgba(255, 255, 255, 0.080)"
PRI = "#B6C4FF"
ACC = "#4ADE80"
TEXT = "#F8FAFC"
TEXT_DIM = "#CBD5E1"
TEXT_MED = "#94A3B8"


class _ElidedLabel(QLabel):
    """QLabel que recorta con '…' en vez de forzar el ancho de su fila.

    Un QLabel normal nunca se encoge por debajo de su texto, así que en
    paneles estrechos empuja la columna de hora/contador fuera del área
    visible. Este acepta cualquier ancho y pinta el texto elidido.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        elided = QFontMetrics(self.font()).elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.width()
        )
        p.drawText(self.rect(), int(self.alignment()), elided)


def _fade_in(w: QWidget, duration: int = 200):
    """Aparición suave de un widget nuevo (burbujas de mensaje)."""
    from ui.widgets.anim import HiFpsAnimation  # local: evita import circular actions<->ui
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    anim = HiFpsAnimation(w, setter=eff.setOpacity)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: w.setGraphicsEffect(None))
    anim.start(delete_when_stopped=True)


def _pulse_glow(w: QWidget, color: str = "#7C9AFF", radius: int = 70):
    """Destello azul puntual en un control (feedback al enviar)."""
    eff = w.graphicsEffect()
    if not isinstance(eff, QGraphicsDropShadowEffect):
        eff = QGraphicsDropShadowEffect(w)
        eff.setOffset(0, 0)
        eff.setBlurRadius(0.0)
        w.setGraphicsEffect(eff)
    col = QColor(color)
    col.setAlpha(255)
    eff.setColor(col)
    from ui.widgets.anim import HiFpsAnimation  # local: evita import circular actions<->ui
    anim = HiFpsAnimation(w, setter=eff.setBlurRadius)
    anim.setDuration(550)
    anim.setKeyValueAt(0.0, eff.blurRadius())
    anim.setKeyValueAt(0.25, float(radius))
    anim.setKeyValueAt(1.0, 0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(delete_when_stopped=True)


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
    elif name == "play":
        painter.setBrush(QBrush(QColor(color)))
        path = QPainterPath()
        path.moveTo(8, 5); path.lineTo(19, 12); path.lineTo(8, 19); path.closeSubpath()
        painter.drawPath(path)
    elif name == "pause":
        painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(QRectF(7, 5, 3.6, 14), 1.4, 1.4)
        painter.drawRoundedRect(QRectF(13.4, 5, 3.6, 14), 1.4, 1.4)
    elif name == "transcribe":
        line(5, 7, 19, 7); line(5, 11, 16, 11); line(5, 15, 19, 15); line(5, 19, 13, 19)
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
            background: {"rgba(94,130,255,0.16)" if accent else "rgba(255,255,255,0.045)"};
            border: 1px solid {"rgba(182,196,255,0.30)" if accent else "rgba(255,255,255,0.09)"};
            border-radius: 10px;
            padding: 0;
        }}
        QPushButton:hover {{
            background: rgba(182,196,255,0.14);
            border-color: rgba(182,196,255,0.34);
        }}
        QPushButton:focus {{ border: 2px solid rgba(182,196,255,0.58); }}
    """)
    return button


class _SendTextEdit(QTextEdit):
    send_requested = pyqtSignal()
    files_pasted = pyqtSignal(list)
    escape_requested = pyqtSignal()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        multiline = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if enter and not multiline:
            self.send_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.escape_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source) -> bool:
        # Por defecto QTextEdit rechaza un portapapeles que solo tenga imagen,
        # así que insertFromMimeData nunca se llamaría al pegar una captura.
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        want = source.hasUrls() or (
            source.hasImage() and not source.hasText() and not source.hasHtml()
        )
        if want:
            from actions.clipboard_files import mime_to_files

            paths = mime_to_files(source)
            if paths:
                self.files_pasted.emit(paths)
                return
        super().insertFromMimeData(source)


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
        color = QColor("#7C9AFF" if self._ack >= 3 else "#BCC3E2")
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


class _WaSpinner(QWidget):
    """Lightweight rotating-arc spinner for the WhatsApp loading screen."""

    def __init__(self, size: int = 64, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._angle = 0
        self._wanted = False
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._advance)

    def start(self):
        self._wanted = True
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._wanted = False
        self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._wanted and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _advance(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = 5
        rect = QRectF(m, m, self._size - 2 * m, self._size - 2 * m)
        # Faint track ring…
        p.setPen(QPen(QColor(182, 196, 255, 40), 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        # …with a bright rotating sweep on top.
        p.setPen(QPen(QColor("#7C9AFF"), 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, -self._angle * 16, 100 * 16)


class _ImageViewer(QDialog):
    """Visor de imágenes in-app: overlay oscuro sobre la ventana, sin navegador.

    Escala la imagen para caber en la ventana anfitriona conservando la
    proporción. Se cierra con Esc o con un clic en cualquier punto.
    """

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("QDialog { background: rgba(4, 6, 12, 0.96); }")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        host = parent.window() if parent is not None else None
        if host is not None:
            geo = host.geometry()
        else:
            geo = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(geo)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 16)
        lay.setSpacing(8)

        img = QLabel()
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setStyleSheet("background: transparent;")
        avail_w = max(100, geo.width() - 56)
        avail_h = max(100, geo.height() - 72)
        if pixmap.width() > avail_w or pixmap.height() > avail_h:
            pixmap = pixmap.scaled(
                avail_w, avail_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        img.setPixmap(pixmap)
        lay.addWidget(img, 1)

        hint = QLabel("Clic o Esc para cerrar")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color: {TEXT_MED}; font-size: 11px; background: transparent;"
        )
        lay.addWidget(hint)

    def mousePressEvent(self, _event):
        self.close()


def _fmt_audio_time(ms: int) -> str:
    total = max(0, int(ms)) // 1000
    return f"{total // 60}:{total % 60:02d}"


class _SeekSlider(QSlider):
    """Horizontal slider that jumps to the clicked position (click-to-seek).

    A plain QSlider only pages towards a groove click; here a click moves the
    handle straight to where the user pressed, then emits pressed/released so the
    owning player seeks immediately.
    """

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.maximum() > self.minimum()
            and self.width() > 0
        ):
            ratio = min(1.0, max(0.0, event.position().x() / self.width()))
            span = self.maximum() - self.minimum()
            self.setValue(int(self.minimum() + round(span * ratio)))
            self.sliderPressed.emit()
            self.sliderReleased.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _AudioPlayer(QWidget):
    """Inline voice-note / audio player: play-pause, seek bar and time label.

    The QMediaPlayer is created lazily on first play so rendering a chat full of
    audios stays cheap. Only one player sounds at a time — starting playback
    pauses any other audio in the same window.
    """

    def __init__(self, url: str, window: "WhatsAppWindow", is_ptt: bool = False, parent=None):
        super().__init__(parent)
        self._url = url
        self._window = window
        self._player: QMediaPlayer | None = None
        self._output: QAudioOutput | None = None
        self._duration = 0
        self._seeking = False
        self._rate = 1.0
        self._rates = (1.0, 1.5, 2.0)

        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(9)

        self._play_btn = QPushButton()
        self._play_btn.setIcon(_wa_icon("play", PRI, 18))
        self._play_btn.setIconSize(QSize(18, 18))
        self._play_btn.setFixedSize(34, 34)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setToolTip("Reproducir nota de voz" if is_ptt else "Reproducir audio")
        self._play_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(182,196,255,0.14);
                border: 1px solid rgba(182,196,255,0.30);
                border-radius: 17px; padding: 0;
            }}
            QPushButton:hover {{ background: rgba(182,196,255,0.22); }}
        """)
        self._play_btn.clicked.connect(self._toggle)
        row.addWidget(self._play_btn)

        self._slider = _SeekSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.setFixedWidth(140)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; border-radius: 2px; background: rgba(255,255,255,0.14);
            }}
            QSlider::sub-page:horizontal {{ background: {PRI}; border-radius: 2px; }}
            QSlider::handle:horizontal {{
                width: 12px; margin: -5px 0; border-radius: 6px;
                background: {PRI}; border: none;
            }}
        """)
        self._slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self._slider.sliderReleased.connect(self._on_seek_release)
        row.addWidget(self._slider)

        self._time = QLabel("0:00")
        self._time.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px; background: transparent;")
        row.addWidget(self._time)

        self._speed_btn = QPushButton("1x")
        self._speed_btn.setFixedSize(38, 26)
        self._speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speed_btn.setToolTip("Velocidad de reproducción")
        self._speed_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(182,196,255,0.10);
                border: 1px solid rgba(182,196,255,0.24);
                border-radius: 9px; padding: 0;
                color: {PRI}; font-size: 11px; font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(182,196,255,0.20); }}
        """)
        self._speed_btn.clicked.connect(self._cycle_speed)
        row.addWidget(self._speed_btn)
        row.addStretch()

    def _ensure_player(self):
        if self._player is not None:
            return
        self._output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._output)
        self._player.setSource(QUrl(self._url))
        self._player.setPlaybackRate(self._rate)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)

    def _toggle(self):
        self._ensure_player()
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._window._stop_other_audio(self)
            self._player.play()

    def pause(self):
        if self._player is not None:
            self._player.pause()

    def _cycle_speed(self):
        idx = (self._rates.index(self._rate) + 1) % len(self._rates) if self._rate in self._rates else 0
        self._rate = self._rates[idx]
        label = f"{self._rate:g}x"
        self._speed_btn.setText(label)
        if self._player is not None:
            self._player.setPlaybackRate(self._rate)

    def _on_duration(self, ms: int):
        self._duration = ms
        self._slider.setRange(0, ms)
        self._time.setText(_fmt_audio_time(ms))

    def _on_position(self, ms: int):
        if not self._seeking:
            self._slider.setValue(ms)
        remaining = self._duration - ms if self._duration else 0
        self._time.setText(_fmt_audio_time(remaining if self._duration else ms))

    def _on_seek_release(self):
        self._seeking = False
        if self._player is not None:
            self._player.setPosition(self._slider.value())

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setIcon(_wa_icon("pause" if playing else "play", PRI, 18))
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._slider.setValue(0)
            self._time.setText(_fmt_audio_time(self._duration))


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
    transcription_ready = pyqtSignal(str, str, str)
    incoming_message = pyqtSignal(dict)
    incoming_edit = pyqtSignal(dict)
    edit_finished = pyqtSignal(str, str, str, str)
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
        self._avatar_disk_cache = DiskImageCache("wa_avatars")
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
        self._message_bubbles_by_id: dict[str, QFrame] = {}
        self._message_text_labels: list[QLabel] = []
        self._message_text_labels_by_id: dict[str, QLabel] = {}
        self._message_meta_labels: dict[str, QLabel] = {}
        self._message_ack_indicators: dict[str, _AckIndicator] = {}
        # Composer state: reply-to (quote) or edit-in-place. Mutually exclusive.
        self._reply_to: dict | None = None
        self._edit_target_id: str | None = None
        self._media_preview_labels: dict[str, list[QLabel]] = {}
        self._media_preview_cache: dict[str, bytes] = {}
        self._media_preview_loading: set[str] = set()
        # Inline audio players (only one sounds at a time) + transcription targets.
        self._audio_players: list = []
        self._transcribe_widgets: dict[str, tuple] = {}
        self._transcribing: set[str] = set()
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
                background: rgba(182, 196, 255, 0.16);
                border-color: rgba(182, 196, 255, 0.40);
                color: {TEXT};
            }}
            QLineEdit {{
                background: rgba(3, 10, 18, 0.72);
                color: {TEXT};
                border: 1px solid rgba(182, 196, 255, 0.13);
                border-radius: 8px;
                padding: 8px 11px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: rgba(182, 196, 255, 0.45);
                background: rgba(7, 19, 31, 0.92);
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 8px 3px 8px 3px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(182, 196, 255, 0.24);
                border: none;
                border-radius: 3px;
                min-height: 34px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(182, 196, 255, 0.42);
                border-color: rgba(182, 196, 255, 0.45);
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
        self.transcription_ready.connect(self._apply_transcription)
        self.incoming_message.connect(self._handle_incoming_message)
        self.incoming_edit.connect(self._handle_incoming_edit)
        self.edit_finished.connect(self._apply_edit_result)
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
        # Debounce search input (250ms) to avoid re-rendering on every keystroke.
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(250)
        self._search_debounce_timer.timeout.connect(self._render_chat_list)
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

        self._mgr_edit_listener = None
        if self.chat_mode and hasattr(self.mgr, "add_edit_listener"):
            def _emit_edit(entry):
                if getattr(self, "_closing", False):
                    return
                try:
                    self.incoming_edit.emit(dict(entry or {}))
                except RuntimeError:
                    pass  # widget already destroyed
            self._mgr_edit_listener = _emit_edit
            self.mgr.add_edit_listener(self._mgr_edit_listener)
            _mgr_ref2 = self.mgr
            _edit_listener_ref = self._mgr_edit_listener
            self.destroyed.connect(
                lambda *_: _mgr_ref2.remove_edit_listener(_edit_listener_ref)
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
                    border-bottom: 1px solid rgba(182, 196, 255, 0.07);
                    padding: 0;
                }
                QListWidget#WaChatList::item:hover {
                    background: rgba(182, 196, 255, 0.055);
                }
                QListWidget#WaChatList::item:selected {
                    background: rgba(94, 130, 255, 0.12);
                    border-left: 2px solid #5E82FF;
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
                        stop:0 #080D20, stop:0.55 #0B1128, stop:1 #0A0F26);
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
        self.reply_edit.files_pasted.connect(self._send_pasted_files)
        self.reply_edit.escape_requested.connect(self._cancel_composer_context)
        self.reply_edit.setPlaceholderText('Escribe un mensaje' if self.chat_mode else 'Escribe la respuesta aqui...')

        # Banner shown above the composer when replying to (quoting) a message
        # or editing one in place. Hidden by default.
        self.composer_banner = QFrame()
        self.composer_banner.setVisible(False)
        self.composer_banner.setStyleSheet("""
            QFrame {
                background: rgba(182, 196, 255, 0.08);
                border-top: 1px solid rgba(182, 196, 255, 0.14);
            }
        """)
        banner_lay = QHBoxLayout(self.composer_banner)
        banner_lay.setContentsMargins(16, 6, 16, 6)
        banner_lay.setSpacing(8)
        self.composer_banner_bar = QFrame()
        self.composer_banner_bar.setFixedWidth(3)
        self.composer_banner_bar.setStyleSheet(f"background: {PRI}; border-radius: 1px;")
        banner_lay.addWidget(self.composer_banner_bar)
        banner_text_box = QVBoxLayout()
        banner_text_box.setSpacing(0)
        self.composer_banner_title = QLabel("")
        self.composer_banner_title.setStyleSheet(f"color: {PRI}; font-size: 11px; font-weight: 700; background: transparent;")
        self.composer_banner_body = QLabel("")
        self.composer_banner_body.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px; background: transparent;")
        banner_text_box.addWidget(self.composer_banner_title)
        banner_text_box.addWidget(self.composer_banner_body)
        banner_lay.addLayout(banner_text_box, 1)
        self.composer_banner_close = QPushButton("✕")
        self.composer_banner_close.setFixedSize(22, 22)
        self.composer_banner_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.composer_banner_close.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #94A3B8; font-weight: 700; }
            QPushButton:hover { color: #F8FAFC; }
        """)
        self.composer_banner_close.clicked.connect(self._cancel_composer_context)
        banner_lay.addWidget(self.composer_banner_close)
        if self.chat_mode:
            self.reply_edit.setMinimumHeight(42)
            self.reply_edit.setMaximumHeight(50)
            self.reply_edit.setStyleSheet(f"""
                QTextEdit {{
                    background: rgba(3, 11, 19, 0.76);
                    color: {TEXT};
                    border: 1px solid rgba(182, 196, 255, 0.13);
                    border-radius: 10px;
                    padding: 9px 12px;
                    font-size: 13px;
                }}
                QTextEdit:focus {{
                    background: rgba(5, 16, 27, 0.94);
                    border-color: rgba(182, 196, 255, 0.42);
                }}
            """)

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
                    background: #0A0F26;
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
                    border-left: 1px solid rgba(182, 196, 255, 0.12);
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
            self.chat_search.textChanged.connect(self._on_search_changed)
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
                    background: #0A0F26;
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
                    border-bottom: 1px solid rgba(182, 196, 255, 0.12);
                }
            """)
            top_lay = QHBoxLayout(top_bar)
            top_lay.setContentsMargins(16, 9, 16, 9)
            self.header_avatar = QLabel("?")
            self.header_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.header_avatar.setFixedSize(38, 38)
            self.header_avatar.setScaledContents(True)
            self.header_avatar.setStyleSheet("background: rgba(182,196,255,0.20); border-radius: 19px; color: white; font-weight: 800;")
            title_box = QVBoxLayout()
            title_box.setSpacing(1)
            title_box.addWidget(self.chat_header)
            title_box.addWidget(self.chat_subheader)
            top_lay.addWidget(self.header_avatar)
            top_lay.addLayout(title_box, 1)
            top_lay.addStretch()
            chat_col.addWidget(top_bar)
            chat_col.addWidget(self.chat_scroll, 1)

            input_bar = QFrame()
            input_bar.setObjectName("WaInputBar")
            input_bar.setMinimumHeight(70)
            input_bar.setStyleSheet("""
                QFrame#WaInputBar {
                    background: rgba(9, 24, 35, 0.98);
                    border-top: 1px solid rgba(182, 196, 255, 0.12);
                }
            """)
            input_bar_col = QVBoxLayout(input_bar)
            input_bar_col.setContentsMargins(0, 0, 0, 0)
            input_bar_col.setSpacing(0)
            input_bar_col.addWidget(self.composer_banner)
            input_row = QFrame()
            input_lay = QHBoxLayout(input_row)
            input_lay.setContentsMargins(16, 10, 16, 10)
            input_lay.setSpacing(10)
            input_bar_col.addWidget(input_row)
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
                    stop:0 #080D20, stop:0.55 #0B1128, stop:1 #0A0F26);
                border-radius: 10px;
            }
        """)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 36, 40, 36)
        lay.setSpacing(14)
        lay.addStretch()

        self._qr_title = QLabel("Conectando WhatsApp")
        self._qr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {TEXT};")
        lay.addWidget(self._qr_title)

        self._qr_subtitle = QLabel(
            "Abre WhatsApp en tu telefono → Dispositivos vinculados →\n"
            "Vincular un dispositivo, y escanea este codigo."
        )
        self._qr_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_subtitle.setWordWrap(True)
        self._qr_subtitle.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_MED};")
        lay.addWidget(self._qr_subtitle)

        # Loading state: an animated spinner shown while the bridge starts up or
        # syncs, so the user sees clear progress instead of a stuck "waiting".
        self._qr_spinner = _WaSpinner(64)
        lay.addWidget(self._qr_spinner, 0, Qt.AlignmentFlag.AlignHCenter)

        # Linking state: the actual QR image (hidden until a code is available).
        self._qr_image = QLabel()
        self._qr_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_image.setFixedSize(280, 280)
        self._qr_image.setStyleSheet("background: #FFFFFF; border-radius: 10px;")
        lay.addWidget(self._qr_image, 0, Qt.AlignmentFlag.AlignHCenter)

        self._qr_status = QLabel("Iniciando el bridge…")
        self._qr_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_status.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_DIM};")
        lay.addWidget(self._qr_status)

        # Escape hatch: appears only when a link/loading has stalled, forcing a
        # fresh connection attempt on the bridge.
        self._qr_retry = QPushButton("Reintentar")
        self._qr_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._qr_retry.setFixedHeight(34)
        self._qr_retry.setStyleSheet(
            "QPushButton { background: rgba(124,154,255,0.16); color: #B6C4FF;"
            " border: 1px solid rgba(124,154,255,0.36); border-radius: 8px;"
            " padding: 0 22px; font-size: 11px; font-weight: 700; }"
            " QPushButton:hover { background: rgba(124,154,255,0.26); }"
            " QPushButton:disabled { color: #7F91A8; border-color: rgba(255,255,255,0.10); }"
        )
        self._qr_retry.clicked.connect(self._on_qr_retry)
        self._qr_retry.hide()
        lay.addWidget(self._qr_retry, 0, Qt.AlignmentFlag.AlignHCenter)

        lay.addStretch()

        # How many consecutive polls we've waited without a QR/ready; used to
        # reveal the retry button only once things are actually stuck.
        self._qr_wait_polls = 0
        self._set_qr_mode("loading")
        return page

    def _set_qr_mode(self, mode: str):
        """Toggle between the 'loading' spinner view and the 'qr' image view."""
        loading = mode == "loading"
        self._qr_spinner.setVisible(loading)
        if loading:
            self._qr_spinner.start()
        else:
            self._qr_spinner.stop()
        self._qr_image.setVisible(not loading)
        self._qr_subtitle.setVisible(not loading)
        self._qr_title.setText("Vincula WhatsApp" if not loading else "Conectando WhatsApp")

    def _on_qr_retry(self):
        self._qr_retry.setEnabled(False)
        self._qr_retry.setText("Reintentando…")
        self._qr_wait_polls = 0

        def worker():
            try:
                from actions.whatsapp import bridge_reconnect
                bridge_reconnect()
            except Exception:
                pass
            # Re-enable shortly after so a stuck retry can be tried again.
            QTimer.singleShot(4000, self._reset_qr_retry)

        threading.Thread(target=worker, daemon=True).start()

    def _reset_qr_retry(self):
        self._qr_retry.setEnabled(True)
        self._qr_retry.setText("Reintentar")

    def _render_qr(self, payload: dict):
        """Drive the in-panel linking view from a /qr bridge payload.

        Shows an animated loading screen while the bridge is starting or the
        phone is syncing, and only swaps to the QR image once a code exists.
        """
        payload = payload or {}
        qr_raw = payload.get("qr")
        # `state` is authoritative on newer bridges; fall back to inferring it
        # from the ready/qr fields so an older bridge still behaves sanely.
        state = payload.get("state")
        detail = payload.get("detail") or ""
        if not state:
            if not payload:
                state = "starting"
            elif payload.get("ready"):
                state = "ready"
            elif qr_raw:
                state = "qr"
            else:
                state = "starting"

        if state == "qr" and qr_raw:
            self._qr_wait_polls = 0
            self._qr_retry.hide()
            self._set_qr_mode("qr")
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
            return

        # Any non-QR state → loading screen with the bridge's own detail text.
        self._set_qr_mode("loading")
        if not payload:
            msg = "Iniciando el bridge de WhatsApp…"
        elif state in ("auth_failure", "disconnected"):
            msg = detail or "Reconectando con WhatsApp…"
        elif state == "loading":
            msg = detail or "Sincronizando WhatsApp…"
        else:  # starting / unknown
            msg = detail or "Preparando la vinculación…"
        self._qr_status.setText(msg)

        # Reveal the retry button once we've been stuck for a while (~20s at a
        # 2.5s poll). Syncing (loading) legitimately takes longer than a QR
        # wait, so give it a bigger grace period (~75s) instead of hiding the
        # button outright — a hung sync must still be manually recoverable.
        self._qr_wait_polls += 1
        threshold = 30 if state == "loading" else 8
        if self._qr_wait_polls >= threshold:
            self._qr_retry.show()

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
                # Readiness was already confirmed above (bridge_qr_status). Do
                # NOT raise on unready here: with raise_on_unready=True a 503 from
                # /chats (e.g. getChats throwing on a detached WhatsApp Web frame)
                # raises before list_recent_chats can fall back to deriving the
                # chat list from the /messages buffer — leaving the panel empty
                # even though messages are arriving. False lets that fallback run.
                chats = list_recent_chats(
                    300,
                    timeout=12,
                    include_pictures=False,
                    raise_on_unready=False,
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
                    or old.get("ack") != new.get("ack")
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
                    background: rgba(94, 130, 255, 0.16);
                    color: {TEXT};
                    border: 1px solid rgba(182, 196, 255, 0.28);
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
                background: rgba(182, 196, 255, 0.13);
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

    def _on_search_changed(self):
        """Debounce search input: restart timer to delay render."""
        self._search_debounce_timer.stop()
        self._search_debounce_timer.start()

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

    def _merge_pending_messages(self, chat_id: str, bridge_msgs: list) -> list:
        """Merge pending messages from whatsapp_pending.json with bridge messages."""
        try:
            from actions.paths import DATA_DIR
            import json
            pending_file = DATA_DIR / "whatsapp_pending.json"
            if not pending_file.exists():
                return bridge_msgs

            pending_data = json.loads(pending_file.read_text(encoding="utf-8"))
            bridge_ids = {m.get("id") for m in bridge_msgs}

            # Add pending messages that aren't already in bridge messages
            for msg in pending_data.values():
                if isinstance(msg, dict):
                    msg_from = str(msg.get("from") or "").strip()
                    msg_id = msg.get("id")
                    # Only include if it's from this chat and not already loaded
                    if msg_from == chat_id and msg_id not in bridge_ids:
                        bridge_msgs.append(msg)

            # Sort by timestamp
            bridge_msgs.sort(key=lambda m: int(m.get("timestamp") or 0))
            return bridge_msgs
        except Exception:
            return bridge_msgs

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
        # Merge pending messages from whatsapp_pending.json
        msgs = self._merge_pending_messages(chat_id, msgs)
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
        # Anchor messages to the bottom (WhatsApp-style): a single stretch at the
        # TOP absorbs spare space, so the newest message always sits at the
        # bottom and later addWidget() calls (optimistic sends, live messages)
        # append below every existing bubble instead of after a trailing stretch.
        self.chat_layout.addStretch()
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
            # Bottom anchoring is handled by the leading stretch added in
            # _begin_message_render; no trailing stretch here.
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
                background: rgba(182, 196, 255, 0.16);
                color: {TEXT};
                border: 1px solid rgba(182, 196, 255, 0.16);
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
        name_lbl = _ElidedLabel(name)
        name_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; font-size: 13px;")
        name_lbl.setToolTip(name)
        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(4)
        # Checkmarks only make sense for our own last message — WhatsApp never
        # reports delivery/read state for messages we received.
        if bool(chat.get("fromMe")) and chat.get("ack") is not None:
            ack_dot = _AckIndicator(chat.get("ack"))
            preview_row.addWidget(ack_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        preview_lbl = _ElidedLabel(preview or "Sin mensajes")
        preview_lbl.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px;")
        if preview:
            preview_lbl.setToolTip(preview)
        preview_row.addWidget(preview_lbl, 1)
        text_col.addWidget(name_lbl)
        text_col.addLayout(preview_row)

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
                    background: #5E82FF;
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
            raw = self._avatar_disk_cache.get(url)
            if raw:
                self._avatar_cache[url] = raw
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
                if raw:
                    self._avatar_disk_cache.put(url, raw)
            except Exception:
                raw = b""
            self.avatar_image_loaded.emit(url, raw)

        from actions.perf_helpers import SharedThreadPool
        SharedThreadPool().submit(worker)

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
        now = int(time.time() * 1000)  # ms, to match bridge chat timestamps for sorting
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
        """React to a new message detected by the manager (GUI thread) —
        incoming, sent from the linked phone, or sent by Jarvis via voice."""
        if not self.chat_mode:
            return
        # 'from' is the chat id here (the manager normalizes it that way — see
        # whatsapp_manager.py), regardless of who actually sent the message.
        chat_id = str((entry or {}).get("from") or "").strip()
        if not chat_id:
            return
        if chat_id == self.current_chat_id:
            # Append straight from the entry we already have. Calling
            # load_conversation() here would look like a refresh but does
            # nothing: this chat is always in _conversation_cache while open,
            # so it hits the cache branch and just re-renders the stale list.
            self._append_incoming_message(chat_id, entry)
        # Always refresh chat list (preview, order, unread count).
        self._incoming_refresh_timer.start(800)

    def _append_incoming_message(self, chat_id: str, entry: dict):
        from_me = bool(entry.get("fromMe"))
        body = entry.get("body") or ""
        timestamp = entry.get("timestamp")
        cached, name = self._conversation_cache.get(chat_id, (None, self.current_chat_name or chat_id))
        if cached is None:
            return  # conversation not loaded yet; the upcoming load will include it
        mid = str(entry.get("id") or "").strip()
        if mid and any(str(m.get("id") or "") == mid for m in cached):
            return  # already present (dedup against poll/refresh)
        if from_me:
            # A message sent from *this* panel already exists as an optimistic
            # "local-…" bubble; _apply_message_send_result() swaps in the real
            # id once the bridge confirms. Skip it here so it doesn't flash as
            # a second, briefly-duplicated bubble while that swap is pending.
            for m in reversed(cached):
                if (
                    m.get("_pending")
                    and bool(m.get("fromMe"))
                    and (m.get("body") or "") == body
                    and abs(int(m.get("timestamp") or 0) - int(timestamp or 0)) < 15000
                ):
                    return
        msg = {
            "id": entry.get("id"),
            "from": chat_id,
            "to": chat_id if from_me else "me",
            "chatId": chat_id,
            "author": entry.get("author"),
            "senderName": entry.get("senderName"),
            "body": body,
            "type": entry.get("type") or "chat",
            "fromMe": from_me,
            "direction": "out" if from_me else "in",
            "timestamp": timestamp,
            "hasMedia": bool(entry.get("hasMedia") or entry.get("mediaUrl")),
            "mediaUrl": entry.get("mediaUrl"),
            "mentionedIds": entry.get("mentionedIds") or [],
            "mentions": entry.get("mentions") or {},
            "ack": entry.get("ack"),
        }
        cached = [dict(m) for m in cached] + [msg]
        self._conversation_cache[chat_id] = (cached, name)
        # Render the bubble only if this chat is on screen and idle.
        if (
            chat_id == self.current_chat_id
            and chat_id not in self._loading_chat_ids
            and not self._message_render_timer.isActive()
        ):
            who = "Yo" if from_me else (entry.get("senderName") or name or chat_id)
            self._add_message_bubble(msg, who, from_me, animate=True)
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
        if self._mgr_edit_listener is not None and hasattr(self.mgr, "remove_edit_listener"):
            try:
                self.mgr.remove_edit_listener(self._mgr_edit_listener)
            except Exception:
                pass
            self._mgr_edit_listener = None
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
        for player in self._audio_players:
            try:
                player.pause()
            except RuntimeError:
                pass
        self._audio_players.clear()
        self._transcribe_widgets.clear()
        self._message_bubbles.clear()
        self._message_bubbles_by_id.clear()
        self._message_text_labels.clear()
        self._message_text_labels_by_id.clear()
        self._message_meta_labels.clear()
        self._message_ack_indicators.clear()
        self._media_preview_labels.clear()
        self._cancel_composer_context()
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
            background: rgba(182, 196, 255, 0.035);
            border: 1px solid rgba(182, 196, 255, 0.08);
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
            "border: 1px solid rgba(182, 196, 255, 0.12);"
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

    def _add_message_bubble(self, msg: dict, who: str, from_me: bool,
                            animate: bool = False):
        if msg.get("_sep"):
            self._add_day_separator(str(msg.get("_sep")))
            return

        msg_type_raw = str(msg.get("type") or "").lower()
        is_system = msg_type_raw in ("gp2",)

        row = QHBoxLayout()
        row.setContentsMargins(10, 2, 10, 2)
        row.setSpacing(10)
        if from_me and not is_system:
            row.addStretch()

        bubble = QFrame()
        if is_system:
            bubble.setObjectName("msgSystem")
            bubble_w = self._bubble_max_width() * 0.7
            bubble.setMaximumWidth(int(bubble_w))
            bubble.setStyleSheet(f"""
                QFrame#msgSystem {{
                    background: rgba(100, 116, 139, 0.20);
                    border: none;
                    border-radius: 6px;
                }}
            """)
        else:
            bubble.setObjectName("msgOut" if from_me else "msgIn")
            bubble_w = self._bubble_max_width()
            bubble.setStyleSheet(f"""
                QFrame#msgOut {{
                    background: rgba(22, 91, 151, 0.76);
                    border: 1px solid rgba(182, 196, 255, 0.13);
                    border-radius: 10px;
                }}
                QFrame#msgIn {{
                    background: rgba(16, 31, 43, 0.98);
                    border: 1px solid rgba(255, 255, 255, 0.055);
                    border-radius: 10px;
                }}
            """)
        bubble.setMinimumWidth(0)
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(bubble)
        lay.setContentsMargins(13, 9, 11, 7)
        lay.setSpacing(6)

        quoted = msg.get("quoted")
        if isinstance(quoted, dict) and quoted.get("id"):
            quoted_id = str(quoted["id"])
            quote_frame = QFrame()
            quote_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            quote_frame.setStyleSheet("""
                QFrame { background: rgba(255, 255, 255, 0.06); border-radius: 4px; }
            """)
            quote_outer = QHBoxLayout(quote_frame)
            quote_outer.setContentsMargins(0, 0, 8, 0)
            quote_outer.setSpacing(8)
            # A single dedicated bar widget (not a CSS border-left) — Qt renders
            # border-left on a QFrame containing wrapped multi-line QLabels
            # unreliably (it can appear to split per row). A fixed-width filled
            # widget always renders as one continuous bar.
            quote_bar = QFrame()
            quote_bar.setFixedWidth(3)
            quote_bar.setStyleSheet(f"background: {PRI}; border-radius: 0px;")
            quote_outer.addWidget(quote_bar)
            quote_lay = QVBoxLayout()
            quote_lay.setContentsMargins(0, 4, 0, 4)
            quote_lay.setSpacing(1)
            quote_who = "Tú" if quoted.get("fromMe") else (quoted.get("senderName") or who)
            quote_title = QLabel(str(quote_who or ""))
            quote_title.setStyleSheet(f"color: {PRI}; font-size: 11px; font-weight: 700; background: transparent;")
            quote_lay.addWidget(quote_title)
            quote_body_text = quoted.get("body") or f"[{quoted.get('type') or 'mensaje'}]"
            quote_body = QLabel(quote_body_text[:150])
            quote_body.setWordWrap(True)
            quote_body.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px; background: transparent;")
            quote_lay.addWidget(quote_body)
            quote_outer.addLayout(quote_lay, 1)
            quote_frame.mousePressEvent = lambda _ev, mid=quoted_id: self._scroll_to_message(mid)
            lay.addWidget(quote_frame)

        has_media = bool(msg.get("hasMedia") or msg.get("mediaUrl"))
        body = self._format_mentions((msg.get("body") or "").strip(), msg)
        # The bridge fills media messages with a placeholder body like "[nota de
        # voz]" / "[imagen]". When we render the actual media widget below, hide
        # that placeholder so it isn't shown next to the player (real captions,
        # which aren't fully bracketed, are kept).
        if has_media and re.fullmatch(r"\[[^\[\]]+\]", body or ""):
            body = ""

        # Stickers: fully transparent bubble (no background, no border).
        is_sticker = msg_type_raw == "sticker"
        if is_sticker:
            bubble.setStyleSheet("QFrame { background: transparent; border: none; }")

        # Pure-media messages (no caption text): remove excess padding so the
        # image fills the bubble edge-to-edge.
        transcription = msg.get("transcription", "").strip()
        is_pure_media = has_media and not body and not transcription
        if is_sticker:
            lay.setContentsMargins(0, 0, 0, 0)
        elif is_pure_media:
            lay.setContentsMargins(4, 4, 4, 4)
        # else keep default (13, 9, 11, 7) set above

        if body:
            text = QLabel(body)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            if is_system:
                text.setAlignment(Qt.AlignmentFlag.AlignCenter)
                text.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px; font-weight: 400; background: transparent;")
            else:
                text.setAlignment(Qt.AlignmentFlag.AlignRight if from_me else Qt.AlignmentFlag.AlignLeft)
                text.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 430; background: transparent;")
            text_w = self._message_text_width(text, body, bubble_w)
            text.setMinimumWidth(text_w)
            text.setMaximumWidth(text_w)
            lay.addWidget(text)
            self._message_text_labels.append(text)
            message_id_for_text = str(msg.get("id") or "").strip()
            if message_id_for_text:
                self._message_text_labels_by_id[message_id_for_text] = text

        # Show audio transcription if available (transcription already set above)
        if transcription:
            transcript_label = QLabel(f"📝 {transcription}")
            transcript_label.setWordWrap(True)
            transcript_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            transcript_label.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            transcript_label.setAlignment(Qt.AlignmentFlag.AlignRight if from_me else Qt.AlignmentFlag.AlignLeft)
            transcript_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; font-style: italic; background: transparent; margin-top: 4px;")
            transcript_w = self._message_text_width(transcript_label, transcription, bubble_w)
            transcript_label.setMinimumWidth(transcript_w)
            transcript_label.setMaximumWidth(transcript_w)
            lay.addWidget(transcript_label)
            # Regístralo para que _update_bubble_widths lo re-mida en resize,
            # igual que los cuerpos de texto.
            self._message_text_labels.append(transcript_label)

        if msg.get("hasMedia") or msg.get("mediaUrl"):
            self._add_media_widget(lay, msg)

        if not body and not (msg.get("hasMedia") or msg.get("mediaUrl")):
            type_label = msg.get('type', 'mensaje')
            if type_label == 'gp2':
                type_label = 'Actualización del grupo'
            text = QLabel(f"[{type_label}]")
            if is_system:
                text.setAlignment(Qt.AlignmentFlag.AlignCenter)
                text.setStyleSheet(f"color: {TEXT_MED}; font-size: 11px;")
            else:
                text.setStyleSheet(f"color: {TEXT_DIM};")
            lay.addWidget(text)

        failed = bool(msg.get("_failed"))
        if from_me:
            meta_text, _read = self._outgoing_meta_text(msg)
        else:
            meta_text = self._fmt_ts(msg.get('timestamp'))
        if not from_me and who and getattr(self, "_current_is_group", False):
            meta_text = f"{who} · {meta_text}" if meta_text else who
        if msg.get("edited"):
            meta_text = f"{meta_text} · editado" if meta_text else "editado"
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
        if message_id:
            self._message_meta_labels[message_id] = meta
        if from_me and message_id:
            try:
                ack_value = int(msg.get("ack"))
            except (TypeError, ValueError):
                ack_value = -2
            indicator = _AckIndicator(ack_value)
            meta_row.addWidget(indicator)
            self._message_ack_indicators[message_id] = indicator
        lay.addLayout(meta_row)

        row.addWidget(bubble)
        if is_system:
            row.addStretch()
        elif not from_me:
            row.addStretch()
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setLayout(row)
        self.chat_layout.addWidget(wrapper)
        if animate:
            _fade_in(wrapper)
        self._message_bubbles.append(bubble)
        if message_id:
            self._message_bubbles_by_id[message_id] = bubble

        bubble.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        msg_snapshot = dict(msg)
        bubble.customContextMenuRequested.connect(
            lambda local_pos, m=msg_snapshot, b=bubble: self._show_message_context_menu(m, b.mapToGlobal(local_pos))
        )

    def _bubble_max_width(self) -> int:
        try:
            viewport_w = int(self.chat_scroll.viewport().width() or 0)
        except Exception:
            viewport_w = 0
        if viewport_w <= 0:
            viewport_w = 900
        # 58% del viewport con mínimo de 300 (las miniaturas miden 260), pero
        # nunca más ancho que el viewport menos los márgenes de fila (10+10):
        # antes, en ventanas estrechas el mínimo desbordaba y cortaba burbujas.
        return min(max(300, int(viewport_w * 0.58)), max(200, viewport_w - 40))

    def _message_text_width(self, label: QLabel, body: str, bubble_w: int) -> int:
        # Los márgenes internos de la burbuja suman 24px (13 izq + 11 der).
        max_w = max(80, int(bubble_w) - 26)
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
        # Mensajes salientes optimistas (imagen pegada/adjunta): aún no hay
        # mediaUrl del bridge, pero sí el archivo local — renderízalo directo.
        local_path = str(msg.get("_localPath") or "")
        if not url and local_path:
            try:
                local = Path(local_path)
                if local.is_file():
                    url = local.as_uri()
                    if url not in self._media_preview_cache:
                        self._media_preview_cache[url] = local.read_bytes()
            except OSError:
                url = ""
        if not url:
            label = QLabel(f"[{msg_type}] media no disponible")
            label.setStyleSheet(f"color: {TEXT_DIM};")
            lay.addWidget(label)
            return

        visual_types = {"image", "sticker", "video", "gif"}
        if msg_type in visual_types:
            max_w = 160 if msg_type == "sticker" else 260
            placeholder_h = max_w if msg_type == "sticker" else 140
            preview = QLabel()
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedSize(max_w, placeholder_h)
            # No background, no border — the image IS the content.
            # For video only, keep a subtle dark background behind the thumbnail.
            if msg_type == "video":
                preview.setStyleSheet(
                    "QLabel { background: rgba(2, 8, 15, 0.52); border-radius: 8px; }"
                )
            else:
                preview.setStyleSheet("QLabel { background: transparent; }")
            lay.addWidget(preview)
            self._media_preview_labels.setdefault(url, []).append(preview)
            cached = self._media_preview_cache.get(url)
            if cached:
                self._set_media_preview(preview, cached, msg_type)
            else:
                self._load_media_preview(url, msg_type)

            # Clic en la miniatura: imágenes se abren en el visor in-app;
            # los vídeos siguen abriéndose fuera (no hay reproductor embebido).
            preview.setCursor(Qt.CursorShape.PointingHandCursor)
            if msg_type == "video":
                preview.mousePressEvent = (
                    lambda _ev, u=url: QDesktopServices.openUrl(QUrl(u))
                )
            else:
                preview.mousePressEvent = (
                    lambda _ev, u=url: self._open_media_viewer(u)
                )

            # "Abrir" button — skip for stickers (they have no useful external URL)
            if msg_type != "sticker":
                btn = QPushButton("Abrir")
                btn.setToolTip("Abrir archivo multimedia")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet("""
                    QPushButton {
                        color: rgba(226, 232, 240, 0.78);
                        background: transparent; border: none;
                        border-radius: 7px; padding: 3px 6px;
                        text-align: right; font-size: 10px; font-weight: 650;
                    }
                    QPushButton:hover { color: #B6C4FF; background: rgba(182,196,255,0.08); }
                """)
                if msg_type == "video":
                    btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                else:
                    btn.setToolTip("Ver imagen")
                    btn.clicked.connect(lambda _=False, u=url: self._open_media_viewer(u))
                lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

        elif msg_type in {"audio", "ptt"}:
            self._add_audio_widget(lay, msg, url)
            return
        else:
            title = QLabel({
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
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    color: rgba(226, 232, 240, 0.88); background: transparent;
                    border: none; border-radius: 7px; padding: 5px 8px;
                    text-align: right; font-size: 10px; font-weight: 650;
                }
                QPushButton:hover { color: #B6C4FF; background: rgba(182,196,255,0.08); }
            """)
            btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _open_media_viewer(self, url: str):
        """Abre una imagen en el visor in-app; si no hay bytes, cae al navegador."""
        raw = self._media_preview_cache.get(url)
        pixmap = QPixmap()
        if not raw or not pixmap.loadFromData(raw):
            QDesktopServices.openUrl(QUrl(url))
            return
        _ImageViewer(pixmap, self).show()

    def _add_audio_widget(self, lay: QVBoxLayout, msg: dict, url: str):
        is_ptt = str(msg.get("type") or "").lower() == "ptt"
        player = _AudioPlayer(url, self, is_ptt=is_ptt)
        self._audio_players.append(player)
        lay.addWidget(player)

        # If a transcription already exists, the bubble shows it above already.
        if str(msg.get("transcription") or "").strip():
            return

        key = str(msg.get("id") or url)
        btn = QPushButton("  Transcribir")
        btn.setIcon(_wa_icon("transcribe", TEXT_MED, 15))
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                color: rgba(226, 232, 240, 0.85); background: transparent;
                border: none; border-radius: 7px; padding: 4px 6px;
                text-align: left; font-size: 11px; font-weight: 650;
            }
            QPushButton:hover { color: #B6C4FF; background: rgba(182, 196, 255, 0.08); }
            QPushButton:disabled { color: rgba(148, 163, 184, 0.6); }
        """)
        result_label = QLabel()
        result_label.setWordWrap(True)
        result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 12px; font-style: italic;"
            " background: transparent; margin-top: 2px;"
        )
        result_label.hide()
        btn.clicked.connect(lambda _=False, m=dict(msg), k=key: self._transcribe_audio(m, k))
        self._transcribe_widgets[key] = (result_label, btn)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(result_label)

    def _stop_other_audio(self, current: "_AudioPlayer"):
        for player in self._audio_players:
            if player is not current:
                try:
                    player.pause()
                except RuntimeError:
                    pass

    def _transcribe_audio(self, msg: dict, key: str):
        if key in self._transcribing or key not in self._transcribe_widgets:
            return
        _label, btn = self._transcribe_widgets[key]
        self._transcribing.add(key)
        btn.setEnabled(False)
        btn.setText("  Transcribiendo...")

        def worker():
            text = ""
            error = ""
            try:
                from .whatsapp import transcribe_message_audio

                text = transcribe_message_audio(msg) or ""
                if not text:
                    error = "No se pudo transcribir el audio."
            except Exception as exc:
                error = str(exc)
            self.transcription_ready.emit(key, text, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_transcription(self, key: str, text: str, error: str):
        self._transcribing.discard(key)
        target = self._transcribe_widgets.get(key)
        if not target:
            return
        label, btn = target
        try:
            if error:
                btn.setEnabled(True)
                btn.setText("  Reintentar transcripción")
                QMessageBox.warning(self, "Transcripción", error)
                return
            label.setText(f"📝 {text}")
            label.show()
            btn.hide()
        except RuntimeError:
            pass

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
        max_w = 160 if msg_type == "sticker" else 260

        # ── Animated content (GIF magic bytes, or sticker/gif type) ──────────
        is_gif_bytes = len(raw) >= 6 and raw[:6] in (b"GIF87a", b"GIF89a")
        try_animate = is_gif_bytes or msg_type in {"gif", "sticker"}
        if try_animate:
            ba = QByteArray(bytes(raw))
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            movie = QMovie()
            movie.setDevice(buf)
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            if movie.isValid():
                movie.jumpToFrame(0)
                src_sz = movie.currentPixmap().size()
                if src_sz.width() > 0 and movie.frameCount() != 1:
                    w = min(max_w, src_sz.width())
                    h = max(1, int(src_sz.height() * w / src_sz.width()))
                    movie.setScaledSize(QSize(w, h))
                    label.setFixedSize(w, h)
                    label.setMovie(movie)
                    label._movie = movie  # prevent GC
                    label._buf   = buf
                    label._ba    = ba
                    movie.setSpeed(100)
                    movie.start()
                    label.setText("")
                    return
            # fall through to static path if movie invalid or single-frame

        # ── Static image ──────────────────────────────────────────────────────
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw):
            label.setText("Vista previa no disponible")
            return

        src_w, src_h = pixmap.width(), pixmap.height()
        if src_w <= 0 or src_h <= 0:
            label.setText("Vista previa no disponible")
            return

        w = min(max_w, src_w)
        h = max(1, int(src_h * w / src_w))
        scaled = pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

        if msg_type == "video":
            canvas = QPixmap(QSize(w, h))
            canvas.fill(QColor("#0A0F26"))
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, scaled)
            cx, cy = w / 2, h / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(3, 12, 22, 205))
            painter.drawEllipse(QPointF(cx, cy), 24.0, 24.0)
            painter.setBrush(QColor("#F8FAFC"))
            play = QPainterPath()
            play.moveTo(cx - 5, cy - 9)
            play.lineTo(cx + 10, cy)
            play.lineTo(cx - 5, cy + 9)
            play.closeSubpath()
            painter.drawPath(play)
            painter.end()
            scaled = canvas

        label.setFixedSize(w, h)
        label.setText("")
        label.setPixmap(scaled)

    def _scroll_bottom(self):
        try:
            bar = self.chat_scroll.verticalScrollBar()
            target = bar.maximum()
            distance = target - bar.value()
            # Saltos grandes (abrir un chat) van directos; los pequeños
            # (mensaje nuevo) se animan para que el chat no dé tirones.
            if distance <= 0 or distance > bar.pageStep() * 2 or not self.isVisible():
                bar.setValue(target)
                return
            anim = getattr(self, "_scroll_anim", None)
            if anim is not None:
                anim.stop()
            from ui.widgets.anim import HiFpsAnimation  # local: evita import circular actions<->ui
            anim = HiFpsAnimation(self, setter=lambda v: bar.setValue(round(v)))
            anim.setDuration(240)
            anim.setStartValue(float(bar.value()))
            anim.setEndValue(float(target))
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._scroll_anim = anim
            anim.start()
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
        caption = self.reply_edit.toPlainText().strip()
        self.reply_edit.clear()
        self._send_media_file(path, target, caption)

    def _send_pasted_files(self, paths):
        if not self.chat_mode:
            return
        target = self.current_chat_id or self.chat_id or self.contact
        if not target or "@" not in target:
            QMessageBox.information(self, 'Info', 'Selecciona un chat primero')
            return
        caption = self.reply_edit.toPlainText().strip()
        self.reply_edit.clear()
        for index, path in enumerate(paths or []):
            if path and Path(str(path)).is_file():
                # Solo el primer archivo lleva el texto como pie de foto.
                self._send_media_file(str(path), target, caption if index == 0 else "")

    def _send_media_file(self, path: str, to_id: str, caption: str = ""):
        filename = Path(path).name
        is_image = Path(path).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
        }
        # Las imágenes (p. ej. capturas pegadas) se muestran como imagen desde
        # el primer momento — antes salían como "📎 jarvis-paste-....png".
        label = caption or ("Imagen" if is_image else filename)
        optimistic = {
            "id": f"local-{int(time.time() * 1000)}",
            "from": "me",
            "to": to_id,
            "chatId": to_id,
            "body": caption if is_image else f"\U0001F4CE {label}",
            "type": "image" if is_image else "document",
            "fromMe": True,
            "direction": "out",
            "timestamp": int(time.time() * 1000),
            "_pending": True,
        }
        if is_image:
            optimistic["hasMedia"] = True
            optimistic["_localPath"] = path
        cached, name = self._conversation_cache.get(to_id, ([], self.current_chat_name or to_id))
        cached = [dict(m) for m in cached] + [optimistic]
        self._conversation_cache[to_id] = (cached, name)
        if to_id == self.current_chat_id:
            self._add_message_bubble(optimistic, "Yo", True, animate=True)
            QTimer.singleShot(20, self._scroll_bottom)
        try:
            _pulse_glow(self.send_btn)
        except Exception:
            pass
        preview_text = f"\U0001F5BC️ {label}" if is_image else f"\U0001F4CE {label}"
        self._update_chat_preview(to_id, preview_text)

        def worker():
            response = {}
            error = ""
            try:
                response = send_whatsapp_media(to=to_id, file_path=path, caption=caption)
            except Exception as exc:
                error = str(exc)
            self.message_send_finished.emit(optimistic["id"], to_id, response, error)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Reply-to (quote) and edit-in-place composer state
    # ------------------------------------------------------------------
    def _cancel_composer_context(self):
        had_state = bool(self._reply_to or self._edit_target_id)
        self._reply_to = None
        if self._edit_target_id:
            self._edit_target_id = None
            self.reply_edit.clear()
        self.composer_banner.setVisible(False)
        if had_state:
            self.reply_edit.setFocus()

    def _show_composer_banner(self, title: str, body: str):
        self.composer_banner_title.setText(title)
        self.composer_banner_body.setText(body)
        self.composer_banner.setVisible(True)

    def _start_reply(self, msg: dict):
        message_id = str(msg.get("id") or "").strip()
        if not message_id or message_id.startswith("local-"):
            return  # can't quote a message that hasn't been confirmed by the bridge yet
        self._edit_target_id = None
        from_me = bool(msg.get("fromMe"))
        sender_name = "Tú" if from_me else (msg.get("senderName") or msg.get("authorName") or self.current_chat_name or "")
        self._reply_to = {
            "id": message_id,
            "body": (msg.get("body") or "")[:200],
            "fromMe": from_me,
            "senderName": None if from_me else sender_name,
            "type": msg.get("type") or "chat",
        }
        preview = self._reply_to["body"] or f"[{self._reply_to['type']}]"
        self._show_composer_banner(f"Respondiendo a {sender_name or 'mensaje'}", preview)
        self.reply_edit.setFocus()

    def _start_edit(self, msg: dict):
        message_id = str(msg.get("id") or "").strip()
        if not message_id or message_id.startswith("local-") or not msg.get("fromMe"):
            return
        self._reply_to = None
        self._edit_target_id = message_id
        self.reply_edit.setPlainText(msg.get("body") or "")
        cursor = self.reply_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.reply_edit.setTextCursor(cursor)
        self._show_composer_banner("Editando mensaje", "Enter para guardar, Esc para cancelar")
        self.reply_edit.setFocus()

    def _confirm_edit(self):
        message_id = self._edit_target_id
        chat_id = self.current_chat_id
        if not message_id:
            return
        text = self.reply_edit.toPlainText().strip()
        if not text:
            return

        def worker():
            error = ""
            try:
                edit_whatsapp_message(message_id, text)
            except Exception as exc:
                error = str(exc)
            self.edit_finished.emit(chat_id, message_id, text, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_edit_result(self, chat_id: str, message_id: str, new_body: str, error: str):
        if error:
            QMessageBox.warning(self, "No se pudo editar", error)
            return  # keep the edit banner + text so the user can retry or cancel
        self._edit_target_id = None
        self.reply_edit.clear()
        self.composer_banner.setVisible(False)
        self._apply_edit(chat_id, message_id, new_body)

    def _handle_incoming_edit(self, entry: dict):
        chat_id = str(entry.get("from") or "").strip()
        message_id = str(entry.get("id") or "").strip()
        if not chat_id or not message_id:
            return
        self._apply_edit(chat_id, message_id, entry.get("body") or "")

    def _apply_edit(self, chat_id: str, message_id: str, new_body: str):
        cached, name = self._conversation_cache.get(chat_id, (None, self.current_chat_name or chat_id))
        if cached is not None:
            updated = []
            for raw in cached:
                message = dict(raw)
                if str(message.get("id") or "") == message_id:
                    message["body"] = new_body
                    message["edited"] = True
                updated.append(message)
            self._conversation_cache[chat_id] = (updated, name)
        if chat_id != self.current_chat_id:
            return
        text_label = self._message_text_labels_by_id.get(message_id)
        if text_label is not None:
            try:
                text_label.setText(self._format_mentions(new_body, {}))
            except RuntimeError:
                text_label = None
        meta_label = self._message_meta_labels.get(message_id)
        if meta_label is not None:
            try:
                current = meta_label.text()
                if "editado" not in current:
                    meta_label.setText(f"{current} · editado" if current else "editado")
            except RuntimeError:
                pass
        if text_label is None:
            # Bubble not tracked (older render) — fall back to a full reload.
            self._apply_loaded_conversation(chat_id, {"messages": cached or [], "error": ""}, name)

    def _show_message_context_menu(self, msg: dict, global_pos):
        menu = QMenu(self)
        message_id = str(msg.get("id") or "").strip()
        can_act = bool(message_id) and not message_id.startswith("local-")
        reply_action = menu.addAction("Responder")
        reply_action.setEnabled(can_act)
        edit_action = None
        if msg.get("fromMe"):
            edit_action = menu.addAction("Editar")
            edit_action.setEnabled(can_act)
        menu.addAction("Copiar texto")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is reply_action:
            self._start_reply(msg)
        elif edit_action is not None and chosen is edit_action:
            self._start_edit(msg)
        elif chosen.text() == "Copiar texto":
            QApplication.clipboard().setText(msg.get("body") or "")

    def _scroll_to_message(self, message_id: str):
        bubble = self._message_bubbles_by_id.get(message_id)
        if bubble is None:
            return
        try:
            self.chat_scroll.ensureWidgetVisible(bubble)
        except RuntimeError:
            pass

    def send_reply(self):
        if self.chat_mode:
            if self._edit_target_id:
                self._confirm_edit()
                return
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
            quoted = self._reply_to
            quoted_id = str(quoted.get("id") or "").strip() if quoted else ""
            self.reply_edit.clear()
            self._cancel_composer_context()
            optimistic = {
                "id": f"local-{int(time.time() * 1000)}",
                "from": "me",
                "to": to_id,
                "chatId": to_id,
                "body": text,
                "type": "chat",
                "fromMe": True,
                "direction": "out",
                "timestamp": int(time.time() * 1000),
                "quoted": quoted,
                "_pending": True,
            }
            cached, name = self._conversation_cache.get(target, ([], self.current_chat_name or target))
            cached = [dict(m) for m in cached] + [optimistic]
            self._conversation_cache[target] = (cached, name)
            if target == self.current_chat_id:
                self._add_message_bubble(optimistic, "Yo", True, animate=True)
                QTimer.singleShot(20, self._scroll_bottom)
            try:
                _pulse_glow(self.send_btn)
            except Exception:
                pass
            self._update_chat_preview(target, text)

            def worker():
                response = {}
                error = ""
                try:
                    response = send_whatsapp(to=to_id, body=text, quoted_message_id=quoted_id or None)
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
        final_msg: dict | None = None
        for raw in cached:
            message = dict(raw)
            if str(message.get("id") or "") == local_id:
                message.pop("_pending", None)
                if error:
                    message["_failed"] = True
                else:
                    message.pop("_failed", None)
                    if isinstance(response, dict) and response.get("id"):
                        message["id"] = response["id"]
                    if isinstance(response, dict) and response.get("ack") is not None:
                        message["ack"] = response["ack"]
                final_msg = message
            updated.append(message)
        if final_msg is None:
            return
        self._conversation_cache[chat_id] = (updated, name)
        if chat_id != self.current_chat_id:
            return
        # Actualiza la burbuja optimista in situ: re-renderizar toda la
        # conversación aquí hacía saltar el scroll arriba y abajo y cortaba
        # la animación de aparición del mensaje recién enviado.
        meta = self._message_meta_labels.pop(local_id, None)
        indicator = self._message_ack_indicators.pop(local_id, None)
        if meta is None and indicator is None:
            self._apply_loaded_conversation(
                chat_id, {"messages": updated, "error": ""}, name,
            )
            return
        new_id = str(final_msg.get("id") or local_id)
        if meta is not None:
            self._message_meta_labels[new_id] = meta
            meta_text, _read = self._outgoing_meta_text(final_msg)
            meta.setText(meta_text)
            if error:
                meta.setStyleSheet("""
                    QLabel {
                        color: #fca5a5;
                        font-size: 10px;
                        background: transparent;
                        padding-top: 1px;
                    }
                """)
        if indicator is not None:
            self._message_ack_indicators[new_id] = indicator
            try:
                indicator.set_ack(int(final_msg.get("ack")))
            except (TypeError, ValueError):
                pass

    def showEvent(self, event):
        super().showEvent(event)
        if self.chat_mode and hasattr(self, "_ack_timer"):
            self._ack_timer.start()
        if hasattr(self, "_qr_poll_timer"):
            self._qr_poll_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "_ack_timer"):
            self._ack_timer.stop()
        if hasattr(self, "_qr_poll_timer"):
            self._qr_poll_timer.stop()


def main():
    app = QApplication(sys.argv)
    w = WhatsAppWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

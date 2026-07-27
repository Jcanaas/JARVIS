from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

__all__ = ["_TranslateOverlayWindow"]


class _TranslateOverlayWindow(QWidget):
    """Floating always-on-top OCR+translation overlay — "OCR + traducción
    en tiempo real sobre cualquier ventana". Draggable, closable, shows
    whatever translated text the last screen check found via update_text().
    """

    def __init__(self, on_close):
        super().__init__()
        self._on_close = on_close
        self._drag: QPoint | None = None
        self._origin: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 220)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Traducción en vivo")
        title.setStyleSheet(
            "color: rgba(182,196,255,0.65); font-size: 11px; font-weight: 700; background: transparent;"
        )
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(title, stretch=1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda: self._on_close())
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: rgba(175,200,230,0.40); font-size: 13px; padding: 0; }"
            "QPushButton:hover { color: #B6C4FF; }"
            "QPushButton:pressed { color: #5E82FF; }"
        )
        top.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)

        self._text_lbl = QLabel("Buscando texto en pantalla…")
        self._text_lbl.setWordWrap(True)
        self._text_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._text_lbl.setStyleSheet(
            "color: #DCE1FF; font-size: 13px; font-weight: 500; background: transparent;"
        )
        lay.addWidget(self._text_lbl, stretch=1)

        for w in (title, self._text_lbl):
            w.installEventFilter(self)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(182, 196, 255, 45), 1))
        p.setBrush(QColor(5, 10, 20, 225))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)

    # ------------------------------------------------------------------ drag
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        t = event.type()
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint()
            self._origin = self.pos()
        elif t == QEvent.Type.MouseMove and self._drag is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self.move(self._origin + (event.globalPosition().toPoint() - self._drag))
        elif t == QEvent.Type.MouseButtonRelease:
            self._drag = None
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint()
            self._origin = self.pos()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(self._origin + (event.globalPosition().toPoint() - self._drag))
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None

    def update_text(self, text: str):
        self._text_lbl.setText(text or "(sin texto detectado)")

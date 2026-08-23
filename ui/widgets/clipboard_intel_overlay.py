from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

__all__ = ["_ClipboardIntelOverlayWindow"]

_CHIP_QSS = """
    QPushButton {
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.075);
        border-radius: 10px;
        color: #DCE1FF;
        font-size: 12px;
        padding: 6px 4px;
    }
    QPushButton:hover {
        background: rgba(182,196,255,0.10);
        border-color: rgba(182,196,255,0.28);
    }
    QPushButton:focus {
        border: 2px solid rgba(182,196,255,0.56);
    }
    QPushButton:disabled {
        color: rgba(220,225,255,0.35);
    }
"""


class _ClipboardIntelOverlayWindow(QWidget):
    """Floating always-on-top popup for the clipboard-intelligence hotkey
    (Ctrl+Alt+C) — shows the copied text and 4 quick LLM actions
    (Translate/Summarise/Explain/Fix). Draggable, closable. The caller wires
    each button's clicked signal to actually run the action (this widget
    only owns presentation state)."""

    def __init__(self, on_close, preview_text: str = ""):
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
        self.setFixedSize(460, 260)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Portapapeles")
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

        self._preview_lbl = QLabel(preview_text or "(portapapeles vacío)")
        self._preview_lbl.setWordWrap(True)
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._preview_lbl.setMaximumHeight(60)
        self._preview_lbl.setStyleSheet(
            "color: rgba(220,225,255,0.55); font-size: 11px; font-style: italic; background: transparent;"
        )
        lay.addWidget(self._preview_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._buttons: dict[str, QPushButton] = {}
        for action, label in (
            ("translate", "Traducir"),
            ("summarize", "Resumir"),
            ("explain", "Explicar"),
            ("fix", "Corregir"),
        ):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_CHIP_QSS)
            self._buttons[action] = btn
            btn_row.addWidget(btn)
        lay.addLayout(btn_row)

        self._result_lbl = QLabel("")
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._result_lbl.setStyleSheet(
            "color: #DCE1FF; font-size: 13px; font-weight: 500; background: transparent;"
        )
        lay.addWidget(self._result_lbl, stretch=1)

        for w in (title, self._preview_lbl):
            w.installEventFilter(self)

    def button(self, action: str) -> QPushButton:
        return self._buttons[action]

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

    def set_loading(self) -> None:
        self._result_lbl.setText("Procesando…")
        for btn in self._buttons.values():
            btn.setEnabled(False)

    def update_result(self, text: str) -> None:
        self._result_lbl.setText(text or "(sin resultado)")
        for btn in self._buttons.values():
            btn.setEnabled(True)

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..theme import C, FONT_UI, qcol, _scrollbar_qss


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(FONT_UI, 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                border-radius: 0px;
                padding: 2px 4px;
                selection-background-color: {C.PRI_GHO};
            }}
        """ + _scrollbar_qss())
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.ACC),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


class DownloadWidget(QWidget):
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hide_tmr = QTimer(self)
        self._hide_tmr.setSingleShot(True)
        self._hide_tmr.timeout.connect(self.hide)
        self.setStyleSheet(f"""
            QWidget {{
                background: rgba(255, 255, 255, 0.030);
                border: 1px solid rgba(255, 255, 255, 0.070);
                border-radius: 16px;
            }}
            QLabel {{
                color: {C.TEXT};
                background: transparent;
            }}
            QProgressBar {{
                background: rgba(255, 255, 255, 0.040);
                border: none;
                border-radius: 6px;
                text-align: center;
                color: {C.TEXT};
                height: 14px;
            }}
            QProgressBar::chunk {{
                background: {C.PRI};
                border-radius: 6px;
            }}
            QPushButton {{
                background: rgba(255, 255, 255, 0.030);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.080);
                border-radius: 10px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                color: {C.ACC};
                border-color: {C.ACC};
            }}
            QPushButton:disabled {{
                color: {C.MUTED_C};
                border-color: {C.BORDER};
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self._title = QLabel("DESCARGA")
        self._title.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color: {C.TEXT_MED};")
        top.addWidget(self._title)
        top.addStretch()
        self._status = QLabel("Inactivo")
        self._status.setFont(QFont(FONT_UI, 7))
        self._status.setStyleSheet(f"color: {C.TEXT_DIM};")
        top.addWidget(self._status)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        lay.addWidget(self._bar)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        self._detail = QLabel("Sin descargas activas")
        self._detail.setFont(QFont(FONT_UI, 7))
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color: {C.TEXT_DIM};")
        bottom.addWidget(self._detail, stretch=1)

        self._cancel = QPushButton("Cancelar")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self.cancel_requested.emit)
        bottom.addWidget(self._cancel)
        lay.addLayout(bottom)
        self.hide()

    def set_state(self, state: dict):
        active = bool(state.get("active", False))
        percent = float(state.get("percent", 0) or 0)
        label = str(state.get("label", "Idle") or "Idle")
        detail = str(state.get("detail", "") or "")
        percent_txt = state.get("percent_text")
        title = str(state.get("title") or state.get("task") or "PROGRESO")

        if not active and percent <= 0 and not detail and label.lower() in ("idle", "none", ""):
            self._hide_tmr.stop()
            self.hide()
            return

        self._hide_tmr.stop()
        self.setVisible(True)
        self._title.setText(title.upper()[:32])
        self._status.setText(label)
        self._detail.setText(detail or "Sin tareas activas")
        self._bar.setValue(max(0, min(100, int(round(percent)))))
        if percent_txt:
            self._bar.setFormat(str(percent_txt))
        else:
            self._bar.setFormat(f"{int(round(percent))}%")
        self._cancel.setEnabled(active and bool(state.get("can_cancel", True)))
        if not active:
            self._hide_tmr.start(15000)


__all__ = [
    'LogWidget',
    'DownloadWidget',
]

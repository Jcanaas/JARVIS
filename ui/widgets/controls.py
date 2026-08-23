"""Controls dialog — rebind the emulator's keyboard and gamepad inputs.

Bindings are captured, never chosen from a list: click a cell, press the key or
the pad button, done. That is not just nicer than a dropdown — it is the only
approach that works for a controller nobody has enumerated in advance, since
actions/gamepad.py deliberately reports opaque signal ids rather than pretending
to know what "button 7" is called on a given pad.

Pad capture polls the shared GamepadManager instead of waiting on an event: the
manager already runs its own thread, and a short poll here keeps all the Qt work
on the UI thread.
"""
from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from actions import input_config
from actions.input_config import Bindings

_CAPTURE_TIMEOUT_MS = 6000


class _BindButton(QPushButton):
    """One cell: shows the current binding, captures a new one when clicked."""

    capture_requested = pyqtSignal(str, str)  # (button id, kind)

    def __init__(self, button_id: str, kind: str, parent=None):
        super().__init__(parent)
        self.button_id = button_id
        self.kind = kind
        self.setFixedHeight(34)
        self.setMinimumWidth(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._qss(False))
        self.clicked.connect(lambda: self.capture_requested.emit(self.button_id,
                                                                 self.kind))

    @staticmethod
    def _qss(capturing: bool) -> str:
        border = C.PRI if capturing else C.BORDER_A
        color = C.PRI if capturing else C.TEXT
        return f"""
            QPushButton {{
                background:{C.GLASS}; color:{color};
                border:1px solid {border}; border-radius:8px;
                padding:0 12px; font-family:Inter; font-size:9pt;
                text-align:left;
            }}
            QPushButton:hover {{ border-color:{C.PRI}; }}
        """

    def show_capturing(self):
        self.setText("Pulsa…")
        self.setStyleSheet(self._qss(True))

    def show_value(self, text: str):
        self.setText(text or "— sin asignar —")
        self.setStyleSheet(self._qss(False))


class ControlsDialog(QDialog):
    """Keyboard + gamepad bindings for the emulator."""

    bindings_changed = pyqtSignal()

    def __init__(self, parent=None, console_id: str = "gba"):
        super().__init__(parent)
        self._console_id = console_id
        self.setWindowTitle("Controles del emulador")
        self.setMinimumWidth(660)
        self.setStyleSheet(f"""
            QDialog {{ background:{C.PANEL}; }}
            QLabel {{ color:{C.TEXT}; background:transparent; }}
        """)

        self._bindings: Bindings = input_config.load(console_id)
        self._capturing: tuple[str, str] | None = None
        self._capture_cell: _BindButton | None = None
        self._cells: dict[tuple[str, str], _BindButton] = {}

        self._gamepad = None
        try:
            from actions import gamepad
            self._gamepad = gamepad.get_manager()
        except Exception:
            pass
        # Signals already held when capture starts must not be taken as the
        # answer — a resting stick that reads slightly off-centre would bind
        # itself to the first button the user clicks.
        self._ignored_signals: set[str] = set()

        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._poll_pad)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(_CAPTURE_TIMEOUT_MS)
        self._timeout.timeout.connect(lambda: self._end_capture(None))

        self._build_ui()
        self._refresh_cells()
        self._refresh_pads()

    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("Controles")
        title.setFont(QFont(FONT_UI, 15, QFont.Weight.Bold))
        root.addWidget(title)

        hint = QLabel(
            "Pulsa una casilla y luego la tecla o el botón del mando que quieras "
            "asignar. Clic derecho sobre una casilla para dejarla sin asignar."
        )
        hint.setWordWrap(True)
        hint.setFont(QFont(FONT_UI, 9))
        hint.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
        root.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for column, heading in enumerate(("Botón", "Teclado", "Mando")):
            label = QLabel(heading)
            label.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            label.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
            grid.addWidget(label, 0, column)

        for row, (button_id, label_text) in enumerate(
                input_config.button_order(self._console_id),
                                                      start=1):
            name = QLabel(label_text)
            name.setFont(QFont(FONT_UI, 10, QFont.Weight.DemiBold))
            grid.addWidget(name, row, 0)
            for column, kind in ((1, "keyboard"), (2, "pad")):
                cell = _BindButton(button_id, kind)
                cell.capture_requested.connect(self._start_capture)
                cell.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                cell.customContextMenuRequested.connect(
                    lambda _pos, b=button_id, k=kind: self._clear(b, k))
                self._cells[(button_id, kind)] = cell
                grid.addWidget(cell, row, column)
        root.addLayout(grid)

        self._pads_label = QLabel("")
        self._pads_label.setWordWrap(True)
        self._pads_label.setFont(QFont(FONT_UI, 9))
        self._pads_label.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
        root.addWidget(self._pads_label)

        footer = QHBoxLayout()
        footer.setSpacing(10)

        rescan = QPushButton("Buscar mandos")
        rescan.setFixedHeight(36)
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setStyleSheet(self._ghost_qss())
        rescan.clicked.connect(self._refresh_pads)
        footer.addWidget(rescan)

        reset = QPushButton("Restaurar por defecto")
        reset.setFixedHeight(36)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setStyleSheet(self._ghost_qss())
        reset.clicked.connect(self._reset)
        footer.addWidget(reset)
        footer.addStretch(1)

        close = QPushButton("Hecho")
        close.setFixedHeight(36)
        close.setMinimumWidth(120)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{
                background:{C.PRI_DIM}; color:{C.DARK}; border:none;
                border-radius:8px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{C.PRI}; }}
        """)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)

    @staticmethod
    def _ghost_qss() -> str:
        return f"""
            QPushButton {{
                background:{C.GLASS}; color:{C.TEXT};
                border:1px solid {C.BORDER_A}; border-radius:8px;
                padding:0 16px; font-weight:600;
            }}
            QPushButton:hover {{ border-color:{C.PRI}; color:{C.PRI}; }}
        """

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _refresh_cells(self):
        from actions.gamepad import signal_label
        for (button_id, kind), cell in self._cells.items():
            if kind == "keyboard":
                keys = self._bindings.keyboard.get(button_id) or []
                text = ", ".join(input_config.key_label(k) for k in keys)
            else:
                signals = self._bindings.pad.get(button_id) or []
                text = ", ".join(signal_label(s) for s in signals)
            cell.show_value(text)

    def _refresh_pads(self):
        if self._gamepad is None:
            self._pads_label.setText("Entrada de mando no disponible en este sistema.")
            return
        pads = self._gamepad.pads()
        if not pads:
            self._pads_label.setText(
                "No hay ningún mando conectado. Conéctalo y pulsa «Buscar mandos» "
                "(la detección tarda un par de segundos)."
            )
            return
        names = "  ·  ".join(f"{p.name} ({p.kind.upper()})" for p in pads)
        self._pads_label.setText(f"Mandos detectados: {names}")

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _start_capture(self, button_id: str, kind: str):
        self._end_capture(None)
        self._capturing = (button_id, kind)
        self._capture_cell = self._cells[(button_id, kind)]
        self._capture_cell.show_capturing()
        self._timeout.start()
        if kind == "pad":
            if self._gamepad is not None:
                self._ignored_signals = self._gamepad.signals()
                self._poll.start()
            else:
                self._end_capture(None)
        else:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _poll_pad(self):
        if self._capturing is None or self._gamepad is None:
            return
        signals = self._gamepad.signals()
        self._ignored_signals &= signals  # released, so it can be bound again
        fresh = signals - self._ignored_signals
        if fresh:
            self._end_capture(sorted(fresh)[0])

    def _end_capture(self, value):
        self._poll.stop()
        self._timeout.stop()
        capturing, self._capturing = self._capturing, None
        cell, self._capture_cell = self._capture_cell, None
        if capturing is None:
            return
        button_id, kind = capturing
        if value is not None:
            if kind == "keyboard":
                self._bindings.bind_key(button_id, int(value))
            else:
                self._bindings.bind_signal(button_id, str(value))
            input_config.save(self._bindings, self._console_id)
            self.bindings_changed.emit()
        if cell is not None:
            # Every cell, not just this one: binding an input steals it from
            # whichever button held it before.
            self._refresh_cells()

    def keyPressEvent(self, event):
        if self._capturing is not None and self._capturing[1] == "keyboard":
            if event.key() == Qt.Key.Key_Escape:
                self._end_capture(None)
            else:
                self._end_capture(event.key())
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(event)

    def _clear(self, button_id: str, kind: str):
        self._bindings.clear(button_id, kind)
        input_config.save(self._bindings, self._console_id)
        self._refresh_cells()
        self.bindings_changed.emit()

    def _reset(self):
        self._bindings = input_config.reset(self._console_id)
        self._refresh_cells()
        self.bindings_changed.emit()

    def closeEvent(self, event):
        self._poll.stop()
        self._timeout.stop()
        super().closeEvent(event)

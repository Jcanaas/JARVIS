from __future__ import annotations

import threading
import time
from datetime import datetime as _datetime, timedelta as _timedelta

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from actions import app_settings
from .theme import *
from .icons import *
from .widgets import *

__all__ = ["WhatsAppToast", "WhatsAppModePicker", "WhatsAppRuleDialog", "_wa_local_tz_name",
           "_WA_COMMON_TZS", "_WA_DAYS"]

_WA_DAYS = ["L", "M", "X", "J", "V", "S", "D"]

# Curated timezone list for the rule editor (IANA names resolvable via dateutil).
_WA_COMMON_TZS = [
    "Europe/Madrid", "Europe/London", "Europe/Lisbon", "Europe/Paris",
    "Europe/Berlin", "Europe/Rome", "Atlantic/Canary", "UTC",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Mexico_City", "America/Bogota", "America/Lima", "America/Santiago",
    "America/Argentina/Buenos_Aires", "America/Sao_Paulo",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
]


def _wa_local_tz_name() -> str:
    """Best-effort IANA name for the system timezone; defaults to Europe/Madrid."""
    try:
        from tzlocal import get_localzone_name
        name = get_localzone_name()
        if name:
            return name
    except Exception:
        pass
    return "Europe/Madrid"


class WhatsAppRuleDialog(QDialog):
    """Create/edit an auto-reply rule: contacts, schedule, timezone and prompt."""

    _contacts_loaded = pyqtSignal(list)

    def __init__(self, parent=None, rule: dict | None = None):
        super().__init__(parent)
        self._rule = dict(rule or {})
        self._owner = parent
        # chat_id -> name, accumulated from the rule + loaded WhatsApp chats.
        self._contact_names: dict[str, str] = {
            c.get("chat_id"): (c.get("name") or c.get("chat_id"))
            for c in (self._rule.get("contacts") or [])
            if c.get("chat_id")
        }
        self._selected: set[str] = set(self._contact_names)

        self.setWindowTitle("Regla de respuesta automática")
        self.setModal(True)
        self.resize(560, 720)
        self.setStyleSheet(f"""
            QDialog {{ background: {C.BG}; }}
            QLabel {{ color: {C.TEXT}; background: transparent; font-size: 12px; }}
            QLineEdit, QTextEdit, QTimeEdit, QComboBox {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                padding: 5px 10px; font-size: 13px;
            }}
            QLineEdit:focus, QTextEdit:focus, QTimeEdit:focus, QComboBox:focus {{
                border-color: rgba(182,196,255,0.55);
            }}
            QComboBox QAbstractItemView {{
                background: #0E1226; color: {C.TEXT};
                selection-background-color: rgba(94,130,255,0.25);
            }}
            QListWidget {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                font-size: 12px;
            }}
            QListWidget::item {{ padding: 4px 6px; }}
            QCheckBox {{ color: {C.TEXT}; font-size: 12px; font-weight: 700; }}
        """)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        scroll.setWidget(body)
        form = QVBoxLayout(body)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(8)

        def _heading(text: str):
            h = QLabel(text)
            h.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-weight: 800; font-size: 11px; letter-spacing: 1px;")
            form.addSpacing(6)
            form.addWidget(h)

        # Name + enabled
        _heading("NOMBRE")
        self._name_edit = QLineEdit(self._rule.get("name", ""))
        self._name_edit.setPlaceholderText("Ej. Fuera de oficina")
        form.addWidget(self._name_edit)
        en_row = QHBoxLayout()
        en_lbl = QLabel("Regla activa")
        en_row.addWidget(en_lbl, 1)
        self._enabled_sw = ToggleSwitch(bool(self._rule.get("enabled", True)))
        en_row.addWidget(self._enabled_sw, 0)
        form.addSpacing(4)
        form.addLayout(en_row)

        # Contacts
        _heading("CONTACTOS")
        self._contact_search = SearchGlowInput("Buscar contacto…")
        self._contact_search.textChanged.connect(self._filter_contacts)
        form.addWidget(self._contact_search)
        self._contact_list = QListWidget()
        self._contact_list.setMinimumHeight(150)
        self._contact_list.itemChanged.connect(self._on_contact_item_changed)
        form.addWidget(self._contact_list)
        self._contacts_status = QLabel("Cargando contactos de WhatsApp…")
        self._contacts_status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 11px;")
        form.addWidget(self._contacts_status)
        # Seed with the rule's own contacts immediately (checked).
        for chat_id, nm in self._contact_names.items():
            self._add_contact_item(chat_id, nm, checked=True)

        # Schedule
        _heading("HORARIO")
        self._always_chk = QCheckBox("Siempre activa (ignorar días y horas)")
        self._always_chk.setChecked(bool(self._rule.get("always", False)))
        self._always_chk.toggled.connect(self._on_always_toggled)
        form.addWidget(self._always_chk)

        self._days_row = QHBoxLayout()
        self._days_row.setSpacing(6)
        self._day_btns: list[QPushButton] = []
        rule_days = set(self._rule.get("days") or [0, 1, 2, 3, 4])
        for i, label in enumerate(_WA_DAYS):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(i in rule_days)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(38, 34)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.03); color: {C.TEXT_MED};
                    border: 1px solid rgba(182,196,255,0.12); border-radius: 8px;
                    font-size: 12px; font-weight: 700;
                }}
                QPushButton:checked {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: rgba(182,196,255,0.55); }}
            """)
            self._day_btns.append(b)
            self._days_row.addWidget(b)
        self._days_row.addStretch(1)
        form.addSpacing(4)
        form.addLayout(self._days_row)

        time_row = QHBoxLayout()
        time_row.setSpacing(10)
        self._start_edit = QTimeEdit(self._parse_time(self._rule.get("start", "09:00")))
        self._start_edit.setDisplayFormat("HH:mm")
        self._end_edit = QTimeEdit(self._parse_time(self._rule.get("end", "18:00")))
        self._end_edit.setDisplayFormat("HH:mm")
        time_row.addWidget(QLabel("Desde"), 0)
        time_row.addWidget(self._start_edit, 0)
        time_row.addWidget(QLabel("hasta"), 0)
        time_row.addWidget(self._end_edit, 0)
        time_row.addStretch(1)
        form.addSpacing(6)
        form.addLayout(time_row)

        # Timezone
        _heading("ZONA HORARIA")
        self._tz_combo = QComboBox()
        tz_options = list(_WA_COMMON_TZS)
        current_tz = self._rule.get("timezone") or _wa_local_tz_name()
        if current_tz not in tz_options:
            tz_options.insert(0, current_tz)
        self._tz_combo.addItems(tz_options)
        self._tz_combo.setCurrentText(current_tz)
        form.addWidget(self._tz_combo)

        # Prompt
        _heading("INSTRUCCIONES PARA LA IA")
        self._prompt_edit = QTextEdit(self._rule.get("prompt", ""))
        self._prompt_edit.setPlaceholderText(
            "Ej. Responde con educación que ahora no puedo atender el móvil, que "
            "devolveré el mensaje por la tarde. Si preguntan por mi disponibilidad, "
            "consulta mi calendario."
        )
        self._prompt_edit.setMinimumHeight(110)
        form.addWidget(self._prompt_edit)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                padding: 7px 18px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ border-color: rgba(182,196,255,0.5); }}
        """)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        brow = QHBoxLayout()
        brow.setContentsMargins(20, 8, 20, 14)
        brow.addStretch(1)
        brow.addWidget(buttons)
        outer.addLayout(brow)

        self._on_always_toggled(self._always_chk.isChecked())
        self._contacts_loaded.connect(self._populate_contacts)
        threading.Thread(target=self._load_contacts, daemon=True).start()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _parse_time(value: str) -> QTime:
        try:
            hh, mm = str(value).split(":")[:2]
            return QTime(int(hh), int(mm))
        except Exception:
            return QTime(9, 0)

    def _add_contact_item(self, chat_id: str, name: str, checked: bool) -> None:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, chat_id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._contact_list.addItem(item)

    def _load_contacts(self):
        try:
            from actions.whatsapp import list_recent_chats

            chats = list_recent_chats(limit=500, timeout=8)
        except Exception:
            chats = []
        items = [
            (str(c.get("chatId")), str(c.get("name") or c.get("chatId")))
            for c in chats
            if c.get("chatId") and not c.get("isGroup")
        ]
        self._contacts_loaded.emit(items)

    def _populate_contacts(self, items: list):
        existing = {
            self._contact_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._contact_list.count())
        }
        self._contact_list.blockSignals(True)
        for chat_id, name in items:
            if chat_id in existing:
                continue
            self._contact_names.setdefault(chat_id, name)
            self._add_contact_item(chat_id, name, checked=chat_id in self._selected)
        self._contact_list.blockSignals(False)
        if items:
            self._contacts_status.setText(f"{len(items)} contactos disponibles. Marca los que quieras.")
        else:
            self._contacts_status.setText(
                "No se pudieron cargar contactos (¿WhatsApp conectado?). "
                "Las reglas existentes se conservan."
            )

    def _filter_contacts(self, text: str):
        needle = text.strip().lower()
        for i in range(self._contact_list.count()):
            item = self._contact_list.item(i)
            item.setHidden(needle not in item.text().lower())

    def _on_contact_item_changed(self, item: QListWidgetItem):
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(chat_id)
            self._contact_names[chat_id] = item.text()
        else:
            self._selected.discard(chat_id)

    def _on_always_toggled(self, always: bool):
        for b in self._day_btns:
            b.setEnabled(not always)
        self._start_edit.setEnabled(not always)
        self._end_edit.setEnabled(not always)

    def _on_accept(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Falta el nombre", "Ponle un nombre a la regla.")
            return
        if not self._selected:
            QMessageBox.warning(self, "Sin contactos", "Selecciona al menos un contacto.")
            return
        if not self._prompt_edit.toPlainText().strip():
            QMessageBox.warning(self, "Sin instrucciones", "Escribe las instrucciones para la IA.")
            return
        if not self._always_chk.isChecked() and not any(b.isChecked() for b in self._day_btns):
            QMessageBox.warning(self, "Sin días", "Elige al menos un día o marca «Siempre activa».")
            return
        self.accept()

    def result_rule(self) -> dict:
        days = [i for i, b in enumerate(self._day_btns) if b.isChecked()]
        return {
            "id": self._rule.get("id"),
            "name": self._name_edit.text().strip(),
            "enabled": self._enabled_sw.isChecked(),
            "contacts": [
                {"chat_id": cid, "name": self._contact_names.get(cid, cid)}
                for cid in self._selected
            ],
            "always": self._always_chk.isChecked(),
            "days": days,
            "start": self._start_edit.time().toString("HH:mm"),
            "end": self._end_edit.time().toString("HH:mm"),
            "timezone": self._tz_combo.currentText().strip(),
            "prompt": self._prompt_edit.toPlainText().strip(),
        }


class WhatsAppToast(QWidget):
    """Floating desktop notification shown when a WhatsApp message arrives.

    A frameless, always-on-top window that does not steal focus, pinned to the
    bottom-right of the screen. Clicking it opens the corresponding chat; it
    fades out automatically after ``duration_ms`` (paused while hovered).
    """

    _WIDTH = 360

    def __init__(self, title: str, body: str, chat_id: str = "",
                 on_open=None, on_closed=None, duration_ms: int = 7000):
        super().__init__(None)
        self._chat_id  = chat_id
        self._on_open  = on_open
        self._on_closed = on_closed
        self._closing  = False
        self._anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(self._WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(15, 12, 12, 13)
        outer.setSpacing(11)

        # Circular avatar — initial as fallback, replaced by the contact photo
        # once it has been fetched (see MainWindow._fetch_wa_toast_avatar).
        self._av = QLabel()
        self._av.setFixedSize(42, 42)
        self._av.setPixmap(self._avatar_pixmap(title))
        outer.addWidget(self._av, alignment=Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)
        col.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(col, stretch=1)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.setContentsMargins(0, 0, 0, 0)
        head_lbl = QLabel("WHATSAPP")
        head_lbl.setStyleSheet(
            "color: #25D366; font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.6px; background: transparent;"
        )
        head.addWidget(head_lbl, stretch=1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._dismiss)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: rgba(175,200,230,0.45); font-size: 12px; padding: 0; }"
            "QPushButton:hover { color: #FF5E82; }"
        )
        head.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)

        title_lbl = QLabel()
        tf = QFont(); tf.setPixelSize(13); tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setStyleSheet("color: #F2FBFF; background: transparent;")
        title_lbl.setText(
            title_lbl.fontMetrics().elidedText(
                str(title or "WhatsApp"), Qt.TextElideMode.ElideRight, self._WIDTH - 112
            )
        )
        col.addWidget(title_lbl)

        body_lbl = QLabel(str(body or ""))
        body_lbl.setWordWrap(True)
        body_lbl.setMaximumHeight(42)
        body_lbl.setStyleSheet(
            "color: rgba(214,228,242,0.92); font-size: 12px; background: transparent;"
        )
        col.addWidget(body_lbl)

        self.adjustSize()

        # duration_ms <= 0 → stay on screen until the user dismisses it.
        self._persistent = int(duration_ms) <= 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        if not self._persistent:
            self._timer.start(max(2500, int(duration_ms)))

    # ── appearance ──────────────────────────────────────────────────────────
    @staticmethod
    def _avatar_pixmap(title: str) -> QPixmap:
        size = 42
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QColor(0x25, 0xD3, 0x66))
        grad.setColorAt(1.0, QColor(0x10, 0x8C, 0x7E))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, size, size)
        initial = next((ch.upper() for ch in str(title or "") if ch.isalnum()), "·")
        p.setPen(QColor("#06231A"))
        f = QFont(); f.setPixelSize(18); f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, initial)
        p.end()
        return pm

    @staticmethod
    def _circular(src: QPixmap, size: int) -> QPixmap:
        """Center-crop to a square, scale, and clip to a circle (no distortion)."""
        size = max(1, int(size))
        side = min(src.width(), src.height())
        if side <= 0:
            return src
        cropped = src.copy(
            (src.width() - side) // 2, (src.height() - side) // 2, side, side,
        ).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
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

    def set_avatar_bytes(self, raw):
        """Replace the initial-avatar with the contact's profile photo (raw bytes)."""
        try:
            if not raw or self._closing:
                return
            pm = QPixmap()
            if not pm.loadFromData(raw) or pm.isNull():
                return
            self._av.setPixmap(self._circular(pm, 42))
        except Exception:
            pass

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor(13, 21, 34, 247))
        # Subtle WhatsApp-green accent strip down the left edge.
        accent = QPainterPath()
        accent.addRoundedRect(QRectF(r.left(), r.top() + 4, 4.0, r.height() - 8), 2, 2)
        p.fillPath(accent, QColor(0x25, 0xD3, 0x66))
        pen = QPen(QColor(37, 211, 102, 95)); pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawPath(path)
        p.end()

    # ── lifecycle / animation ───────────────────────────────────────────────
    def show_animated(self):
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(220)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _dismiss(self):
        if self._closing:
            return
        self._closing = True
        try:
            self._timer.stop()
        except Exception:
            pass
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(220)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._finish_close)
        self._anim.start()

    def _finish_close(self):
        try:
            if callable(self._on_closed):
                self._on_closed(self)
        except Exception:
            pass
        self.close()
        self.deleteLater()

    # ── interaction ─────────────────────────────────────────────────────────
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                if callable(self._on_open):
                    self._on_open(self._chat_id)
            except Exception:
                pass
            self._dismiss()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        try:
            if not self._closing and not self._persistent:
                self._timer.start(2500)
        except Exception:
            pass
        super().leaveEvent(event)


class WhatsAppModePicker(QWidget):
    open_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; color: {C.TEXT}; font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}"; }}
            QLabel {{ background: transparent; color: {C.TEXT_DIM}; }}
            QLineEdit {{
                background: rgba(255, 255, 255, 0.035);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.080);
                border-radius: 14px;
                padding: 10px 12px;
            }}
            QPushButton {{
                background: rgba(182, 196, 255, 0.16);
                color: {C.TEXT};
                border: 1px solid rgba(182, 196, 255, 0.28);
                border-radius: 14px;
                padding: 10px 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(182, 196, 255, 0.24); border-color: {C.PRI}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 34, 34, 34)
        root.setSpacing(10)
        root.addStretch()

        title = QLabel("MODO WHATSAPP")
        title.setFont(QFont(FONT_UI, 18, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {C.ACC};")
        root.addWidget(title)

        hint = QLabel("Escribe el contacto, número o nombre del chat que quieres abrir.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        self.contact_input = QLineEdit()
        self.contact_input.setPlaceholderText("Ej: Mama, +34..., Juan")
        self.contact_input.returnPressed.connect(self._emit_open)
        row.addWidget(self.contact_input, stretch=1)
        btn = QPushButton("Abrir chat")
        btn.clicked.connect(self._emit_open)
        row.addWidget(btn)
        root.addLayout(row)
        root.addStretch()

    def _emit_open(self):
        contact = self.contact_input.text().strip()
        if contact:
            self.open_requested.emit(contact)

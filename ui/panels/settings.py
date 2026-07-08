from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from actions import app_settings
from actions.paths import config_path

from ..theme import *
from ..icons import *
from ..widgets import *

class SettingsModePanel(QWidget):
    """App settings space, organised as a strip of top tabs over a content stack.

    Per-mode pages (Música holds the real crossfade controls) sit alongside the
    general pages (Apariencia, Inicio, Acerca de). Most pages are tidy empty
    states for now; the panel is built to grow one tab at a time.
    """

    # (label, page-builder name). Order = tab order. Música first since it has
    # real settings; "·" entries render a thin separator between mode/general.
    _TABS = [
        ("Música",     "music"),
        ("Ecualizador", "equalizer"),
        ("YouTube",    "youtube"),
        ("WhatsApp",   "whatsapp"),
        ("Gmail",      "empty"),
        ("Drive",      "empty"),
        ("General",    "profile"),
        ("Apariencia", "appearance"),
        ("Inicio",     "startup"),
        ("Acerca de",  "about"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPanel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Keep the scroll area + its viewport transparent so the dark workspace
        # background shows through (otherwise the viewport paints its default,
        # light base colour over everything — see the YouTube panels at L6255).
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(30, 24, 30, 26)
        lay.setSpacing(6)

        title = QLabel("Configuración")
        title.setStyleSheet(
            "color: white; background: transparent; font-weight: 900; font-size: 28px;"
        )
        lay.addWidget(title)
        subtitle = QLabel("Ajusta el comportamiento de cada modo y las opciones generales.")
        subtitle.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px;")
        lay.addWidget(subtitle)

        # ── Tab strip (wraps on narrow widths) ───────────────────────────
        tab_host = QWidget()
        tab_host.setStyleSheet("QWidget { background: transparent; }")
        self._tab_flow = FlowLayout(tab_host, hspacing=8, vspacing=8)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        lay.addSpacing(8)
        lay.addWidget(tab_host)

        # thin divider under the tabs
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: rgba(182,196,255,0.12); border: none;")
        lay.addSpacing(12)
        lay.addWidget(rule)
        lay.addSpacing(14)

        # ── Content stack ────────────────────────────────────────────────
        self._stack = QStackedWidget()
        lay.addWidget(self._stack)
        lay.addStretch(1)

        builders = {
            "music": self._build_music_page,
            "equalizer": self._build_equalizer_page,
            "youtube": self._build_youtube_page,
            "whatsapp": self._build_whatsapp_page,
            "appearance": self._build_appearance_page,
            "startup": self._build_startup_page,
            "profile": self._build_profile_page,
            "about": self._build_about_page,
        }
        for idx, (label, kind) in enumerate(self._TABS):
            page = builders.get(kind, lambda l=label: self._build_empty_page(l))()
            self._stack.addWidget(page)
            btn = self._make_tab(label)
            btn.setChecked(idx == 0)
            self._tab_group.addButton(btn, idx)
            self._tab_flow.addWidget(btn)
        self._tab_group.idClicked.connect(self._stack.setCurrentIndex)

    # -- builders ---------------------------------------------------------
    def _make_tab(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.03);
                color: {C.TEXT_MED};
                border: 1px solid rgba(182,196,255,0.10);
                border-radius: 17px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                color: {C.TEXT};
                border-color: rgba(182,196,255,0.30);
            }}
            QPushButton:checked {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border-color: rgba(182,196,255,0.55);
            }}
        """)
        return btn

    def _spin_qss(self) -> str:
        return f"""
            QSpinBox {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                padding: 2px 10px; font-size: 13px; font-weight: 700;
            }}
            QSpinBox:disabled {{ color: {C.TEXT_DIM}; border-color: rgba(182,196,255,0.08); }}
        """

    def _combo_qss(self) -> str:
        return f"""
            QComboBox {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                padding: 2px 12px; font-size: 12px; font-weight: 600; min-width: 140px;
            }}
            QComboBox:hover {{ border-color: rgba(182,196,255,0.45); }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox QAbstractItemView {{
                background: #0E1226; color: {C.TEXT};
                selection-background-color: rgba(94,130,255,0.25);
                border: 1px solid rgba(182,196,255,0.22); outline: none;
            }}
        """

    def _make_card(self, heading: str | None = None) -> tuple[QWidget, QVBoxLayout]:
        card = QWidget()
        card.setStyleSheet(
            "QWidget { background: rgba(255,255,255,0.025);"
            " border: 1px solid rgba(182,196,255,0.10); border-radius: 14px; }"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 6, 18, 6)
        cl.setSpacing(0)
        if heading:
            hd = QLabel(heading)
            hd.setStyleSheet(
                f"color: {C.TEXT_MED}; background: transparent; font-weight: 800;"
                " font-size: 11px; letter-spacing: 1px; border: none;"
            )
            cl.setContentsMargins(18, 14, 18, 8)
            cl.addWidget(hd)
        return card, cl

    def _add_row(self, body: QVBoxLayout, title: str, desc: str, control: QWidget,
                 *, first: bool = False) -> QWidget:
        """Append a label/description + control row, with a hairline separator."""
        if not first:
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: rgba(255,255,255,0.05); border: none;")
            body.addWidget(sep)

        row = QWidget()
        row.setStyleSheet("QWidget { background: transparent; border: none; }")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 14, 0, 14)
        rl.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color: {C.TEXT}; background: transparent; font-size: 13px; font-weight: 600; border: none;")
        text.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 11px; border: none;")
            text.addWidget(d)
        rl.addLayout(text, 1)
        rl.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(row)
        return row

    def _build_empty_page(self, name: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 30, 0, 30)
        l.setSpacing(8)
        l.addStretch(1)

        glyph = QLabel("⚙")
        glyph.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        glyph.setStyleSheet(f"color: {C.BORDER_B}; background: transparent; font-size: 40px; border: none;")
        l.addWidget(glyph)

        head = QLabel(f"{name}")
        head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        head.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 14px; font-weight: 700; border: none;")
        l.addWidget(head)

        msg = QLabel("Sin ajustes disponibles todavía.")
        msg.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        msg.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px; border: none;")
        l.addWidget(msg)
        l.addStretch(1)
        return w

    # -- WhatsApp auto-reply rules ----------------------------------------
    _WA_DAYS = ["L", "M", "X", "J", "V", "S", "D"]
    _WA_DAY_NAMES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    def _build_whatsapp_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        intro = QLabel(
            "Crea reglas para que Jarvis responda automáticamente por ti según el "
            "contacto y el horario. La IA lee la conversación previa y puede consultar "
            "tu calendario si hace falta."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px; border: none;")
        l.addWidget(intro)

        notif_card, notif_body = self._make_card("NOTIFICACIONES")
        self._wa_notif_switch = ToggleSwitch(bool(app_settings.get("whatsapp_notifications", True)))
        self._wa_notif_switch.toggled.connect(self._on_wa_notifications)
        self._add_row(
            notif_body, "Ventanas flotantes",
            "Muestra una notificación emergente al recibir un mensaje. "
            "Haz clic en ella para abrir el chat.",
            self._wa_notif_switch, first=True,
        )

        self._wa_notif_dur_combo = QComboBox()
        for label, secs in (
            ("3 segundos", 3), ("5 segundos", 5), ("7 segundos", 7),
            ("10 segundos", 10), ("15 segundos", 15), ("Hasta cerrarla", 0),
        ):
            self._wa_notif_dur_combo.addItem(label, secs)
        cur_dur = int(app_settings.get("whatsapp_notification_duration_s", 7))
        di = self._wa_notif_dur_combo.findData(cur_dur)
        self._wa_notif_dur_combo.setCurrentIndex(di if di >= 0 else 2)
        self._wa_notif_dur_combo.setFixedHeight(32)
        self._wa_notif_dur_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wa_notif_dur_combo.setStyleSheet(self._combo_qss())
        self._wa_notif_dur_combo.currentIndexChanged.connect(self._on_wa_notif_duration)
        self._add_row(
            notif_body, "Duración en pantalla",
            "Cuánto tiempo permanece visible cada notificación.",
            self._wa_notif_dur_combo,
        )
        l.addWidget(notif_card)

        rules_title = QLabel("Reglas de respuesta automática")
        rules_title.setStyleSheet(
            f"color: {C.TEXT}; background: transparent; font-size: 13px; font-weight: 700; border: none;"
        )
        l.addWidget(rules_title)

        add_btn = QPushButton("  Añadir regla")
        add_btn.setIcon(_line_icon("plus", C.PRI, 16))
        add_btn.setIconSize(QSize(15, 15))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedHeight(36)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid rgba(182,196,255,0.45); border-radius: 18px;
                padding: 0 18px; font-size: 13px; font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(94,130,255,0.18); }}
        """)
        add_btn.clicked.connect(self._wa_add_rule)
        row = QHBoxLayout()
        row.addWidget(add_btn, 0)
        row.addStretch(1)
        l.addLayout(row)

        self._wa_rules_box = QVBoxLayout()
        self._wa_rules_box.setSpacing(10)
        l.addLayout(self._wa_rules_box)
        l.addStretch(1)

        self._refresh_wa_rules()
        return w

    def _wa_schedule_summary(self, rule: dict) -> str:
        if rule.get("always"):
            sched = "Siempre activa"
        else:
            days = rule.get("days") or []
            if not days or len(days) == 7:
                day_txt = "Todos los días"
            elif days == [0, 1, 2, 3, 4]:
                day_txt = "L-V"
            else:
                day_txt = " ".join(self._WA_DAY_NAMES[d] for d in days)
            sched = f"{day_txt} · {rule.get('start', '00:00')}-{rule.get('end', '23:59')}"
        tz = rule.get("timezone")
        return f"{sched}  ({tz})" if tz else sched

    def _refresh_wa_rules(self):
        from actions import whatsapp_rules

        while self._wa_rules_box.count():
            item = self._wa_rules_box.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.deleteLater()

        rules = whatsapp_rules.load_rules()
        if not rules:
            empty = QLabel("Todavía no has creado ninguna regla.")
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 12px; border: none; padding: 8px 2px;")
            self._wa_rules_box.addWidget(empty)
            return

        for idx, rule in enumerate(rules):
            self._wa_rules_box.addWidget(self._wa_rule_card(rule, idx, len(rules)))

    def _wa_rule_card(self, rule: dict, idx: int, total: int) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            "QWidget { background: rgba(255,255,255,0.025);"
            " border: 1px solid rgba(182,196,255,0.10); border-radius: 14px; }"
        )
        cl = QHBoxLayout(card)
        cl.setContentsMargins(16, 12, 14, 12)
        cl.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(3)
        name = QLabel(rule.get("name") or "Regla")
        name.setStyleSheet(f"color: {C.TEXT}; background: transparent; font-size: 14px; font-weight: 700; border: none;")
        info.addWidget(name)
        contacts = rule.get("contacts") or []
        names = ", ".join(c.get("name") or c.get("chat_id") for c in contacts[:3])
        if len(contacts) > 3:
            names += f" +{len(contacts) - 3}"
        meta = QLabel(f"{self._wa_schedule_summary(rule)}\n{names or 'Sin contactos'}")
        meta.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 11px; border: none;")
        info.addWidget(meta)
        cl.addLayout(info, 1)

        sw = ToggleSwitch(bool(rule.get("enabled", True)))
        sw.toggled.connect(lambda v, rid=rule.get("id"): self._wa_toggle_rule(rid, v))
        cl.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)

        def _mini_btn(icon_name: str, tip: str, danger: bool = False) -> QPushButton:
            color = C.RED if danger else C.TEXT_MED
            b = QPushButton()
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(30, 30)
            b.setIcon(_line_icon(icon_name, color))
            b.setIconSize(QSize(16, 16))
            hover_border = "rgba(251,113,133,0.5)" if danger else "rgba(182,196,255,0.4)"
            hover_bg = "rgba(251,113,133,0.10)" if danger else "rgba(182,196,255,0.10)"
            b.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.04);
                    border: 1px solid rgba(182,196,255,0.12); border-radius: 8px;
                }}
                QPushButton:hover {{ background: {hover_bg}; border-color: {hover_border}; }}
                QPushButton:disabled {{ background: rgba(255,255,255,0.015); border-color: rgba(182,196,255,0.05); }}
            """)
            return b

        up = _mini_btn("up", "Subir prioridad")
        up.setEnabled(idx > 0)
        up.clicked.connect(lambda _=False, rid=rule.get("id"): self._wa_move_rule(rid, -1))
        down = _mini_btn("down", "Bajar prioridad")
        down.setEnabled(idx < total - 1)
        down.clicked.connect(lambda _=False, rid=rule.get("id"): self._wa_move_rule(rid, 1))
        edit = _mini_btn("edit", "Editar")
        edit.clicked.connect(lambda _=False, r=rule: self._wa_edit_rule(r))
        delete = _mini_btn("trash", "Eliminar", danger=True)
        delete.clicked.connect(lambda _=False, r=rule: self._wa_delete_rule(r))
        for b in (up, down, edit, delete):
            cl.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _wa_toggle_rule(self, rule_id: str, enabled: bool):
        from actions import whatsapp_rules

        for r in whatsapp_rules.load_rules():
            if r.get("id") == rule_id:
                r["enabled"] = bool(enabled)
                whatsapp_rules.update_rule(rule_id, r)
                break

    def _wa_move_rule(self, rule_id: str, delta: int):
        from actions import whatsapp_rules

        whatsapp_rules.move_rule(rule_id, delta)
        self._refresh_wa_rules()

    def _wa_add_rule(self):
        dlg = WhatsAppRuleDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            from actions import whatsapp_rules

            whatsapp_rules.add_rule(dlg.result_rule())
            self._refresh_wa_rules()

    def _wa_edit_rule(self, rule: dict):
        dlg = WhatsAppRuleDialog(parent=self, rule=rule)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            from actions import whatsapp_rules

            whatsapp_rules.update_rule(rule.get("id"), dlg.result_rule())
            self._refresh_wa_rules()

    def _wa_delete_rule(self, rule: dict):
        resp = QMessageBox.question(
            self, "Eliminar regla",
            f"¿Eliminar la regla «{rule.get('name')}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            from actions import whatsapp_rules

            whatsapp_rules.delete_rule(rule.get("id"))
            self._refresh_wa_rules()

    def _build_about_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        hero = QWidget()
        hero.setStyleSheet("QWidget { background: transparent; border: none; }")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(2, 2, 2, 2)
        hl.setSpacing(2)
        name = QLabel("J.A.R.V.I.S")
        name.setStyleSheet("color: white; background: transparent; font-size: 26px; font-weight: 900; letter-spacing: 3px; border: none;")
        hl.addWidget(name)
        mark = QLabel("MARK XXXIX")
        mark.setStyleSheet(f"color: {C.PRI}; background: transparent; font-size: 12px; font-weight: 800; letter-spacing: 4px; border: none;")
        hl.addWidget(mark)
        tag = QLabel("Asistente personal de escritorio.")
        tag.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px; border: none;")
        hl.addWidget(tag)
        l.addWidget(hero)

        card, body = self._make_card()
        self._add_about_row(body, "Versión", "Mark XXXIX", first=True)
        self._add_about_row(body, "Interfaz", "PyQt6")
        self._add_about_row(body, "Ajustes guardados en", str(config_path("app_settings.json")))
        l.addWidget(card)
        l.addStretch(1)
        return w

    def _add_about_row(self, body: QVBoxLayout, key: str, value: str, *, first: bool = False) -> None:
        if not first:
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: rgba(255,255,255,0.05); border: none;")
            body.addWidget(sep)
        row = QWidget()
        row.setStyleSheet("QWidget { background: transparent; border: none; }")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 12, 0, 12)
        rl.setSpacing(14)
        k = QLabel(key)
        k.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px; border: none;")
        rl.addWidget(k, 0)
        v = QLabel(value)
        v.setWordWrap(True)
        v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.setStyleSheet(f"color: {C.TEXT}; background: transparent; font-size: 12px; font-weight: 600; border: none;")
        rl.addWidget(v, 1)
        body.addWidget(row)

    def _build_music_page(self) -> QWidget:
        # Seed from the live music panel; fall back to the persisted settings.
        self._cf_enabled = bool(app_settings.get("crossfade_enabled", False))
        self._cf_secs = int(app_settings.get("crossfade_seconds", 3))
        self._cf_on_skip = bool(app_settings.get("crossfade_on_skip", False))
        mp = getattr(self.window(), "_music_panel", None)
        if mp is not None:
            self._cf_enabled = bool(getattr(mp, "_cf_enabled", self._cf_enabled))
            self._cf_secs = int(getattr(mp, "_cf_secs", self._cf_secs))

        self._autoplay = bool(app_settings.get("music_autoplay", True))
        self._def_volume = int(app_settings.get("music_default_volume", 100))
        self._audio_quality = str(app_settings.get("music_audio_quality", "m4a"))
        self._disable_ducking = bool(app_settings.get("music_disable_ducking", True))

        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("REPRODUCCIÓN")

        self._cf_switch = ToggleSwitch(self._cf_enabled)
        self._cf_switch.toggled.connect(self._on_cf_toggled)
        self._add_row(
            body, "Crossfade entre canciones",
            "Funde el final de una canción con el principio de la siguiente.",
            self._cf_switch, first=True,
        )

        self._cf_spin = QSpinBox()
        self._cf_spin.setRange(1, 15)
        self._cf_spin.setValue(self._cf_secs)
        self._cf_spin.setSuffix(" s")
        self._cf_spin.setFixedSize(78, 32)
        self._cf_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._cf_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cf_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cf_spin.setStyleSheet(f"""
            QSpinBox {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 9px;
                padding: 2px 10px; font-size: 13px; font-weight: 700;
            }}
            QSpinBox:disabled {{ color: {C.TEXT_DIM}; border-color: rgba(182,196,255,0.08); }}
        """)
        self._cf_spin.valueChanged.connect(self._on_cf_secs)
        self._dur_row = self._add_row(
            body, "Duración del fundido",
            "Cuántos segundos dura la transición.",
            self._cf_spin,
        )

        self._cf_skip_switch = ToggleSwitch(self._cf_on_skip)
        self._cf_skip_switch.toggled.connect(self._on_cf_skip)
        self._skip_row = self._add_row(
            body, "Aplicar al saltar de canción",
            "Usa el fundido también al pulsar siguiente o anterior.",
            self._cf_skip_switch,
        )

        l.addWidget(card)

        # ── Audio card ───────────────────────────────────────────────────
        card2, body2 = self._make_card("AUDIO")

        self._autoplay_switch = ToggleSwitch(self._autoplay)
        self._autoplay_switch.toggled.connect(self._on_autoplay)
        self._add_row(
            body2, "Reproducción automática",
            "Continúa con la siguiente canción al terminar.",
            self._autoplay_switch, first=True,
        )

        self._vol_spin = QSpinBox()
        self._vol_spin.setRange(0, 100)
        self._vol_spin.setValue(self._def_volume)
        self._vol_spin.setSuffix(" %")
        self._vol_spin.setFixedSize(86, 32)
        self._vol_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._vol_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vol_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        self._vol_spin.setStyleSheet(self._spin_qss())
        self._vol_spin.valueChanged.connect(self._on_def_volume)
        self._add_row(
            body2, "Volumen por defecto",
            "Volumen con el que arranca la reproducción.",
            self._vol_spin,
        )

        self._quality_combo = QComboBox()
        for label, val in (
            ("Máxima  ≈160–256 kbps inestable", "best"),
            ("Opus ~160 kbps (WebM) recomendado", "opus"),
            ("AAC-LC ~128 kbps (M4A)", "m4a"),
            ("AAC ~48 kbps (M4A) · ahorro", "low"),
        ):
            self._quality_combo.addItem(label, val)
        qi = self._quality_combo.findData(self._audio_quality)
        self._quality_combo.setCurrentIndex(qi if qi >= 0 else 0)
        self._quality_combo.setFixedHeight(32)
        self._quality_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality_combo.setStyleSheet(self._combo_qss())
        self._quality_combo.currentIndexChanged.connect(self._on_quality)
        self._add_row(
            body2, "Calidad de audio",
            "Códec y bitrate del stream. «Máxima» deja que se elija el mejor "
            "audio disponible; Opus rinde igual con menos datos; «ahorro» usa "
            "el bitrate más bajo.",
            self._quality_combo,
        )

        self._duck_switch = ToggleSwitch(self._disable_ducking)
        self._duck_switch.toggled.connect(self._on_ducking)
        self._add_row(
            body2, "Evitar que Windows baje el volumen",
            "Desactiva la atenuación automática del audio al usar el micrófono.",
            self._duck_switch,
        )

        l.addWidget(card2)

        # ── Playlists card ───────────────────────────────────────────────
        card3, body3 = self._make_card("LISTAS DE REPRODUCCIÓN")

        self._pl_export_btn = QPushButton("Exportar «Me Gusta»…")
        self._pl_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pl_export_btn.setFixedHeight(32)
        self._pl_export_btn.setStyleSheet(self._settings_button_qss())
        self._pl_export_btn.clicked.connect(self._settings_export_liked)
        self._add_row(
            body3, "Exportar tus «Me Gusta»",
            "Guarda tus canciones marcadas con me gusta en un archivo .json.",
            self._pl_export_btn, first=True,
        )

        self._pl_import_btn = QPushButton("Importar lista…")
        self._pl_import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pl_import_btn.setFixedHeight(32)
        self._pl_import_btn.setStyleSheet(self._settings_button_qss())
        self._pl_import_btn.clicked.connect(self._settings_import_playlist)
        self._add_row(
            body3, "Importar una lista",
            "Carga un .json exportado por Jarvis y empieza a reproducirlo.",
            self._pl_import_btn,
        )
        l.addWidget(card3)

        l.addStretch(1)
        self._sync_cf_enabled_state()
        return w

    def _settings_button_qss(self) -> str:
        return f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid rgba(182,196,255,0.45); border-radius: 9px;
                padding: 0 16px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(94,130,255,0.18); }}
        """

    def _settings_export_liked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Me Gusta",
            str(Path.home() / "Downloads" / "jarvis_me_gusta.json"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import export_liked_to_file
                result = export_liked_to_file(path)
                QTimer.singleShot(0, lambda r=result: QMessageBox.information(
                    self, "Exportación completada",
                    f"Se exportaron {r['count']} canciones a:\n{r['path']}",
                ))
            except Exception as e:
                msg = str(e) or repr(e)
                QTimer.singleShot(0, lambda m=msg: QMessageBox.warning(
                    self, "Error al exportar", m,
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _settings_import_playlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar playlist Jarvis",
            str(Path.home() / "Downloads"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import import_playlist_from_file
                tracks = import_playlist_from_file(path)
                if not tracks:
                    QTimer.singleShot(0, lambda: QMessageBox.warning(
                        self, "Playlist vacía", "No se encontraron pistas con videoId."
                    ))
                    return
                import json as _json
                data = _json.loads(Path(path).read_text(encoding="utf-8"))
                name = data.get("name", Path(path).stem)
                QTimer.singleShot(0, lambda tr=tracks, nm=name: self._settings_play_imported(tr, nm))
            except Exception as e:
                msg = str(e) or repr(e)
                QTimer.singleShot(0, lambda m=msg: QMessageBox.warning(
                    self, "Error al importar", m,
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _settings_play_imported(self, tracks: list, name: str = ""):
        win = self.window()
        # Switch to the Music space (creates the panel if needed) so the user
        # sees the imported list and playback.
        if hasattr(win, "_show_music_mode"):
            try:
                win._show_music_mode()
            except Exception:
                pass
        panel = getattr(win, "_music_panel", None)
        if panel is not None and hasattr(panel, "_show_imported_tracks"):
            try:
                panel._set_header(
                    name or "Lista importada",
                    f"Importada • {len(tracks)} canciones", "Importada", {},
                )
                panel._show_imported_tracks(tracks)
            except Exception:
                pass
        cb = getattr(win, "on_playback_command", None)
        if callable(cb):
            threading.Thread(
                target=cb,
                args=("play_tracks", {"tracks": tracks, "start_index": 0, "shuffle": False}),
                daemon=True,
            ).start()

    # ── Equalizer ────────────────────────────────────────────────────────
    _EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    _EQ_PRESETS = {
        "Plano":        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "Graves":       [6, 5, 4, 2, 0, 0, 0, 0, 1, 2],
        "Agudos":       [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
        "Vocal":        [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
        "Rock":         [4, 3, 2, 0, -1, -1, 0, 2, 3, 4],
        "Electrónica":  [5, 4, 2, 0, -1, 0, 1, 2, 4, 5],
    }

    @staticmethod
    def _fmt_freq(f: int) -> str:
        return f"{f//1000}k" if f >= 1000 else str(f)

    def _build_equalizer_page(self) -> QWidget:
        gains = app_settings.get("eq_gains", None)
        if not isinstance(gains, list) or len(gains) != len(self._EQ_BANDS):
            gains = [0] * len(self._EQ_BANDS)
        self._eq_enabled = bool(app_settings.get("eq_enabled", False))

        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("ECUALIZADOR")

        # Top controls: enable toggle + preset + reset
        self._eq_switch = ToggleSwitch(self._eq_enabled)
        self._eq_switch.toggled.connect(self._on_eq_toggle)
        self._add_row(
            body, "Activar ecualizador",
            "Ajusta el tono de la música por bandas de frecuencia.",
            self._eq_switch, first=True,
        )

        preset_row = QWidget()
        preset_row.setStyleSheet("QWidget { background: transparent; border: none; }")
        pr = QHBoxLayout(preset_row)
        pr.setContentsMargins(0, 8, 0, 8)
        pr.setSpacing(10)
        pr_lbl = QLabel("Preajuste")
        pr_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent; font-size: 13px; font-weight: 600; border: none;")
        pr.addWidget(pr_lbl)
        pr.addStretch(1)
        self._eq_preset = QComboBox()
        self._eq_preset.addItem("Personalizado", "")
        for name in self._EQ_PRESETS:
            self._eq_preset.addItem(name, name)
        self._eq_preset.setFixedHeight(30)
        self._eq_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eq_preset.setStyleSheet(self._combo_qss())
        self._eq_preset.currentIndexChanged.connect(self._on_eq_preset)
        pr.addWidget(self._eq_preset)
        reset_btn = QPushButton("Reiniciar")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setFixedHeight(30)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04); color: {C.TEXT_MED};
                border: 1px solid rgba(182,196,255,0.18); border-radius: 8px;
                padding: 0 14px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: rgba(182,196,255,0.45); }}
        """)
        reset_btn.clicked.connect(self._on_eq_reset)
        pr.addWidget(reset_btn)
        body.addWidget(preset_row)

        # Band sliders (vertical)
        bands_row = QWidget()
        bands_row.setStyleSheet("QWidget { background: transparent; border: none; }")
        br = QHBoxLayout(bands_row)
        br.setContentsMargins(0, 10, 0, 6)
        br.setSpacing(6)
        self._eq_sliders = []
        self._eq_gain_labels = []
        for i, freq in enumerate(self._EQ_BANDS):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            gain_lbl = QLabel(self._fmt_gain(gains[i]))
            gain_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            gain_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 10px; border: none;")
            col.addWidget(gain_lbl)
            sld = QSlider(Qt.Orientation.Vertical)
            sld.setRange(-12, 12)
            sld.setValue(int(gains[i]))
            sld.setFixedHeight(150)
            sld.setCursor(Qt.CursorShape.PointingHandCursor)
            sld.setStyleSheet(self._eq_slider_qss())
            sld.valueChanged.connect(lambda v, idx=i: self._on_eq_slider(idx, v))
            col.addWidget(sld, alignment=Qt.AlignmentFlag.AlignHCenter)
            f_lbl = QLabel(self._fmt_freq(freq))
            f_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            f_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 10px; border: none;")
            col.addWidget(f_lbl)
            self._eq_sliders.append(sld)
            self._eq_gain_labels.append(gain_lbl)
            br.addLayout(col)
        body.addWidget(bands_row)

        # Debounce so dragging a slider doesn't spam the IPC
        self._eq_save_timer = QTimer(self)
        self._eq_save_timer.setSingleShot(True)
        self._eq_save_timer.setInterval(180)
        self._eq_save_timer.timeout.connect(self._eq_commit)

        l.addWidget(card)
        l.addStretch(1)
        self._eq_sync_enabled()
        return w

    @staticmethod
    def _fmt_gain(g: int) -> str:
        g = int(g)
        return f"+{g}" if g > 0 else str(g)

    def _eq_slider_qss(self) -> str:
        return f"""
            QSlider::groove:vertical {{
                width: 4px; background: rgba(255,255,255,0.10); border-radius: 2px;
            }}
            QSlider::handle:vertical {{
                height: 14px; margin: 0 -6px; border-radius: 7px;
                background: {C.PRI}; border: 1px solid rgba(255,255,255,0.4);
            }}
            QSlider::sub-page:vertical {{ background: rgba(255,255,255,0.10); border-radius: 2px; }}
            QSlider::add-page:vertical {{ background: {C.PRI_DIM}; border-radius: 2px; }}
            QSlider:disabled::handle:vertical {{ background: #475569; }}
            QSlider:disabled::add-page:vertical {{ background: rgba(255,255,255,0.06); }}
        """

    def _eq_sync_enabled(self) -> None:
        on = self._eq_switch.isChecked()
        for s in self._eq_sliders:
            s.setEnabled(on)
        self._eq_preset.setEnabled(on)

    def _on_eq_toggle(self, checked: bool) -> None:
        self._eq_enabled = checked
        self._eq_sync_enabled()
        app_settings.set("eq_enabled", checked)
        self._send_playback("set_equalizer", {"enabled": checked, "gains": self._eq_current_gains()})

    def _eq_current_gains(self) -> list:
        return [int(s.value()) for s in self._eq_sliders]

    def _on_eq_slider(self, idx: int, val: int) -> None:
        self._eq_gain_labels[idx].setText(self._fmt_gain(val))
        # Slider moved by hand → mark preset as custom (without recursing)
        if self._eq_preset.currentIndex() != 0:
            self._eq_preset.blockSignals(True)
            self._eq_preset.setCurrentIndex(0)
            self._eq_preset.blockSignals(False)
        self._eq_save_timer.start()

    def _eq_commit(self) -> None:
        gains = self._eq_current_gains()
        app_settings.set("eq_gains", gains)
        self._send_playback("set_equalizer", {"enabled": self._eq_enabled, "gains": gains})

    def _on_eq_preset(self, _idx: int) -> None:
        name = self._eq_preset.currentData() or ""
        preset = self._EQ_PRESETS.get(name)
        if not preset:
            return
        for s, g in zip(self._eq_sliders, preset):
            s.blockSignals(True)
            s.setValue(int(g))
            s.blockSignals(False)
        for lbl, g in zip(self._eq_gain_labels, preset):
            lbl.setText(self._fmt_gain(g))
        self._eq_commit()

    def _on_eq_reset(self) -> None:
        self._eq_preset.blockSignals(True)
        self._eq_preset.setCurrentIndex(0)
        self._eq_preset.blockSignals(False)
        for s in self._eq_sliders:
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        for lbl in self._eq_gain_labels:
            lbl.setText("0")
        self._eq_commit()

    def _build_youtube_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("REPRODUCTOR FLOTANTE (PIP)")
        self._pip_remember_switch = ToggleSwitch(bool(app_settings.get("youtube_remember_pip", True)))
        self._pip_remember_switch.toggled.connect(
            lambda c: app_settings.set("youtube_remember_pip", bool(c))
        )
        self._add_row(
            body, "Recordar tamaño y posición",
            "Vuelve a abrir el mini-reproductor donde lo dejaste la última vez.",
            self._pip_remember_switch, first=True,
        )
        l.addWidget(card)
        l.addStretch(1)
        return w

    def _build_profile_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("TU PERFIL")

        # Seed from the existing long-term memory (identity.*), not a separate store
        name0, about0 = self._load_profile_from_memory()

        # Name row
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Nombre")
        self._name_edit.setText(name0 or "")
        self._name_edit.setMinimumWidth(220)
        self._name_edit.textChanged.connect(self._on_profile_changed)
        self._add_row(
            body, "Tu nombre",
            "JARVIS te llamará por este nombre.",
            self._name_edit, first=True,
        )

        # Context block (full width, multiline)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.05); border: none;")
        body.addWidget(sep)

        ctx_wrap = QWidget()
        ctx_wrap.setStyleSheet("QWidget { background: transparent; border: none; }")
        cw = QVBoxLayout(ctx_wrap)
        cw.setContentsMargins(0, 14, 0, 14)
        cw.setSpacing(6)
        ctx_title = QLabel("Sobre ti")
        ctx_title.setStyleSheet(f"color: {C.TEXT}; background: transparent; font-size: 13px; font-weight: 600; border: none;")
        cw.addWidget(ctx_title)
        ctx_desc = QLabel("Pronombres, edad, gustos, profesión, cómo prefieres que te hable… lo que quieras que JARVIS tenga en cuenta.")
        ctx_desc.setWordWrap(True)
        ctx_desc.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 11px; border: none;")
        cw.addWidget(ctx_desc)

        self._ctx_edit = QTextEdit()
        self._ctx_edit.setPlainText(about0)
        self._ctx_edit.setPlaceholderText(
            "Ej.: Me llamo Jordi, pronombres él/elle, 17 años. Me gusta la música electrónica "
            "y la programación. Háblame en español y de forma directa."
        )
        self._ctx_edit.setMinimumHeight(130)
        self._ctx_edit.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(10,12,26,0.85); color: {C.TEXT};
                border: 1px solid rgba(182,196,255,0.22); border-radius: 10px;
                padding: 8px 10px; font-size: 13px;
            }}
            QTextEdit:focus {{ border-color: rgba(182,196,255,0.55); }}
        """)
        self._ctx_edit.textChanged.connect(self._on_profile_changed)
        cw.addWidget(self._ctx_edit)
        body.addWidget(ctx_wrap)

        hint = QLabel("Se aplica en la próxima conversación con JARVIS.")
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 11px; border: none;")
        cw.addWidget(hint)

        # Debounced auto-save so we don't write the file on every keystroke
        self._profile_save_timer = QTimer(self)
        self._profile_save_timer.setSingleShot(True)
        self._profile_save_timer.setInterval(600)
        self._profile_save_timer.timeout.connect(self._save_profile)

        l.addWidget(card)

        # ── Audio devices card ───────────────────────────────────────────
        dev_card, dev_body = self._make_card("DISPOSITIVOS DE AUDIO")

        # Microphone (input) — used by the voice assistant
        self._mic_combo = QComboBox()
        self._mic_combo.addItem("Automático (evita Bluetooth)", "")
        for d in self._list_input_devices():
            self._mic_combo.addItem(d, d)
        mi = self._mic_combo.findData(str(app_settings.get("input_device_name", "")))
        self._mic_combo.setCurrentIndex(mi if mi >= 0 else 0)
        self._mic_combo.setFixedHeight(32)
        self._mic_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_combo.setStyleSheet(self._combo_qss())
        self._mic_combo.currentIndexChanged.connect(self._on_input_device)
        self._add_row(
            dev_body, "Micrófono (entrada)",
            "Elige un micro que no sea Bluetooth para que los auriculares no se "
            "pongan en modo manos libres y la música no se corte."
            "Si aun asi experimentas problemas con el microfono te recomndamos"
            "que desde el panel de control desahabilites el microfono de tu auriculares bluetooth",
            self._mic_combo, first=True,
        )

        # Output device — used for music playback
        self._out_combo = QComboBox()
        for d in self._list_output_devices():
            self._out_combo.addItem(d.get("description", d.get("name", "")), d.get("name", ""))
        oi = self._out_combo.findData(str(app_settings.get("audio_output_device", "")))
        self._out_combo.setCurrentIndex(oi if oi >= 0 else 0)
        self._out_combo.setFixedHeight(32)
        self._out_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._out_combo.setStyleSheet(self._combo_qss())
        self._out_combo.currentIndexChanged.connect(self._on_output_device)
        self._add_row(
            dev_body, "Salida de audio",
            "Dónde se reproduce la música.",
            self._out_combo,
        )

        dev_hint = QLabel("El micrófono se aplica al reiniciar la escucha; la salida, al instante.")
        dev_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; font-size: 11px; border: none;")
        dev_body.addWidget(dev_hint)

        l.addWidget(dev_card)

        # ── Danger zone: wipe everything and restart ─────────────────────
        danger_card, danger_body = self._make_card("ZONA DE PELIGRO")
        self._reset_btn = QPushButton("Borrar todos los datos y reiniciar")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setFixedHeight(34)
        self._reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(248,113,113,0.14); color: #FFD9D9;
                border: 1px solid rgba(248,113,113,0.40); border-radius: 9px;
                padding: 0 16px; font-size: 12px; font-weight: 800;
            }}
            QPushButton:hover {{
                background: rgba(248,113,113,0.24);
                border-color: rgba(248,113,113,0.70); color: #FFECEC;
            }}
            QPushButton:pressed {{ background: rgba(248,113,113,0.32); }}
        """)
        self._reset_btn.clicked.connect(self._confirm_reset_all_data)
        self._add_row(
            danger_body, "Restablecer JARVIS",
            "Borra TODOS los datos: ajustes, perfil, memoria, sesiones de Google "
            "y WhatsApp, descargas en cola y registros. La app se reiniciará desde "
            "cero. Esta acción no se puede deshacer.",
            self._reset_btn, first=True,
        )
        l.addWidget(danger_card)

        l.addStretch(1)
        return w

    def _confirm_reset_all_data(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Borrar todos los datos")
        box.setText("¿Seguro que quieres borrar TODOS los datos de JARVIS?")
        box.setInformativeText(
            "Se eliminarán tus ajustes, tu perfil, la memoria, las sesiones de "
            "Google y WhatsApp y los registros. La app se reiniciará vacía.\n\n"
            "Esta acción no se puede deshacer."
        )
        yes = box.addButton("Borrar y reiniciar", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not yes:
            return
        try:
            from actions.app_reset import schedule_reset_and_restart
            schedule_reset_and_restart()
        except Exception as exc:
            QMessageBox.critical(
                self, "No se pudo reiniciar",
                f"Ocurrió un error al preparar el reinicio:\n{exc}",
            )
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # -- audio device helpers --------------------------------------------
    @staticmethod
    def _list_input_devices() -> list:
        try:
            import sounddevice as _sd
            seen, out = set(), []
            for d in _sd.query_devices():
                if d.get("max_input_channels", 0) > 0:
                    nm = d.get("name", "")
                    if nm and nm not in seen:
                        seen.add(nm)
                        out.append(nm)
            return out
        except Exception:
            return []

    @staticmethod
    def _list_output_devices() -> list:
        try:
            from actions import ytmusic_headless as _hl
            return _hl.list_audio_output_devices()
        except Exception:
            return [{"name": "", "description": "Automático (predeterminado del sistema)"}]

    def _on_input_device(self, _idx: int) -> None:
        app_settings.set("input_device_name", self._mic_combo.currentData() or "")

    def _on_output_device(self, _idx: int) -> None:
        name = self._out_combo.currentData() or ""
        app_settings.set("audio_output_device", name)
        self._send_playback("set_audio_output_device", {"name": name})

    def _build_appearance_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("INTERFAZ")

        self._viz_switch = ToggleSwitch(bool(app_settings.get("ui_show_visualizer", True)))
        self._viz_switch.toggled.connect(self._on_visualizer)
        self._add_row(
            body, "Visualizador de audio",
            "Muestra las barras reactivas al sonido en el núcleo.",
            self._viz_switch, first=True,
        )

        self._ontop_switch = ToggleSwitch(bool(app_settings.get("ui_always_on_top", False)))
        self._ontop_switch.toggled.connect(self._on_always_on_top)
        self._add_row(
            body, "Ventana siempre encima",
            "Mantiene la ventana de JARVIS por encima del resto.",
            self._ontop_switch,
        )

        l.addWidget(card)
        l.addStretch(1)
        return w

    def _build_startup_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { border: none; background: transparent; }")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(14)

        card, body = self._make_card("AL INICIAR")

        self._autostart_switch = ToggleSwitch(bool(app_settings.get("start_with_windows", False)))
        self._autostart_switch.toggled.connect(self._on_autostart)
        self._add_row(
            body, "Arrancar con Windows",
            "Inicia JARVIS automáticamente al encender el equipo.",
            self._autostart_switch, first=True,
        )

        self._space_combo = QComboBox()
        for label, val in (
            ("Último usado", "last"),
            ("Inicio", "Normal"),
            ("WhatsApp", "WhatsApp"),
            ("Correo", "Gmail"),
            ("Drive", "Drive"),
            ("Música", "Music"),
            ("YouTube", "YouTube"),
            ("Calendario", "Calendar"),
        ):
            self._space_combo.addItem(label, val)
        si = self._space_combo.findData(str(app_settings.get("startup_space", "last")))
        self._space_combo.setCurrentIndex(si if si >= 0 else 0)
        self._space_combo.setFixedHeight(32)
        self._space_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._space_combo.setStyleSheet(self._combo_qss())
        self._space_combo.currentIndexChanged.connect(
            lambda _i: app_settings.set("startup_space", self._space_combo.currentData() or "last")
        )
        self._add_row(
            body, "Espacio inicial",
            "Qué pantalla se abre al arrancar la app.",
            self._space_combo,
        )

        l.addWidget(card)
        l.addStretch(1)
        return w

    # -- handlers ---------------------------------------------------------
    @staticmethod
    def _mem_value(entry) -> str:
        if isinstance(entry, dict):
            return str(entry.get("value", "") or "")
        return str(entry or "")

    def _load_profile_from_memory(self) -> tuple[str, str]:
        """Read name + free-form 'about' from the shared long-term memory."""
        try:
            from memory.memory_manager import load_memory
            ident = load_memory().get("identity", {}) or {}
            return self._mem_value(ident.get("name")), self._mem_value(ident.get("about"))
        except Exception:
            return "", ""

    def _on_profile_changed(self) -> None:
        # Restart the debounce; actual write happens in _save_profile
        if hasattr(self, "_profile_save_timer"):
            self._profile_save_timer.start()

    def _save_profile(self) -> None:
        # Persist into the existing long-term memory (identity.*) so it flows
        # through the normal [WHAT YOU KNOW ABOUT THIS PERSON] prompt block and
        # isn't duplicated in a separate store.
        try:
            from memory.memory_manager import update_memory, forget
        except Exception:
            return
        name = self._name_edit.text().strip()
        about = self._ctx_edit.toPlainText().strip()
        for key, val in (("name", name), ("about", about)):
            try:
                if val:
                    update_memory({"identity": {key: {"value": val}}})
                else:
                    forget(key, "identity")
            except Exception:
                pass

    def _on_wa_notifications(self, checked: bool) -> None:
        app_settings.set("whatsapp_notifications", bool(checked))

    def _on_wa_notif_duration(self, _index: int) -> None:
        try:
            secs = int(self._wa_notif_dur_combo.currentData())
        except Exception:
            secs = 7
        app_settings.set("whatsapp_notification_duration_s", secs)

    def _on_visualizer(self, checked: bool) -> None:
        app_settings.set("ui_show_visualizer", bool(checked))

    def _on_always_on_top(self, checked: bool) -> None:
        app_settings.set("ui_always_on_top", bool(checked))
        win = self.window()
        if hasattr(win, "set_always_on_top"):
            win.set_always_on_top(bool(checked))

    def _on_autostart(self, checked: bool) -> None:
        ok = app_settings.set_windows_autostart(bool(checked))
        app_settings.set("start_with_windows", bool(checked) if ok else False)
        if not ok and checked:
            self._autostart_switch.setChecked(False)
            QMessageBox.warning(
                self, "Arranque con Windows",
                "No se pudo configurar el arranque automático.",
            )

    def _sync_cf_enabled_state(self) -> None:
        on = self._cf_switch.isChecked()
        self._cf_spin.setEnabled(on)
        self._cf_skip_switch.setEnabled(on)
        self._dur_row.setEnabled(on)
        self._skip_row.setEnabled(on)

    def _on_cf_toggled(self, checked: bool) -> None:
        self._cf_enabled = checked
        self._sync_cf_enabled_state()
        app_settings.set("crossfade_enabled", checked)
        self._push_music_panel_state()
        self._send_playback("set_crossfade", {"seconds": self._cf_secs, "enabled": checked})

    def _on_cf_secs(self, val: int) -> None:
        self._cf_secs = int(val)
        app_settings.set("crossfade_seconds", self._cf_secs)
        self._push_music_panel_state()
        if self._cf_enabled:
            self._send_playback("set_crossfade", {"seconds": self._cf_secs, "enabled": True})

    def _on_cf_skip(self, checked: bool) -> None:
        self._cf_on_skip = checked
        app_settings.set("crossfade_on_skip", checked)
        self._send_playback("set_crossfade_on_skip", {"enabled": checked})

    def _on_autoplay(self, checked: bool) -> None:
        app_settings.set("music_autoplay", checked)
        self._send_playback("set_autoplay", {"enabled": checked})

    def _on_def_volume(self, val: int) -> None:
        app_settings.set("music_default_volume", int(val))
        self._send_playback("volume", {"level": int(val)})

    def _on_quality(self, _idx: int) -> None:
        q = self._quality_combo.currentData() or "m4a"
        app_settings.set("music_audio_quality", q)
        self._send_playback("set_audio_quality", {"quality": q})

    def _on_ducking(self, checked: bool) -> None:
        app_settings.set("music_disable_ducking", checked)
        self._send_playback("set_ducking", {"enabled": checked})

    def _push_music_panel_state(self) -> None:
        """Keep the music panel's context-menu crossfade state in sync."""
        mp = getattr(self.window(), "_music_panel", None)
        if mp is not None:
            try:
                mp._cf_enabled = self._cf_enabled
                mp._cf_secs = self._cf_secs
            except Exception:
                pass

    def _send_playback(self, action: str, params: dict | None = None) -> None:
        win = self.window()
        cb = getattr(win, "on_playback_command", None)
        if cb:
            threading.Thread(target=cb, args=(action, params or {}), daemon=True).start()


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






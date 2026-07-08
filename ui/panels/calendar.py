from __future__ import annotations

import calendar as _calendar_mod
from datetime import date as _date, datetime as _datetime

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ..theme import *
from ..icons import *
from ..widgets import *

_MONTH_NAMES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                   "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
_WEEKDAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

class _CalendarDayCell(QFrame):
    """Celda de día del grid mensual: número + puntos de evento como widgets
    reales (no texto multilínea embebido en un botón), para que la altura de
    la celda sea estable sin depender de cuántos eventos tenga ese día."""

    clicked = pyqtSignal(object)

    def __init__(self, day: _date, parent=None):
        super().__init__(parent)
        self.day = day
        self.setObjectName("CalDayCell")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(44)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(3)

        self.number_label = QLabel(str(day.day))
        self.number_label.setObjectName("CalDayNumber")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.number_label)

        self.dots_row = QHBoxLayout()
        self.dots_row.setSpacing(3)
        self.dots_row.setContentsMargins(0, 0, 0, 0)
        dots_host = QWidget()
        dots_host.setLayout(self.dots_row)
        dots_host.setStyleSheet("background: transparent;")
        lay.addWidget(dots_host, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def set_event_count(self, n: int):
        while self.dots_row.count():
            item = self.dots_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        shown = min(n, 3)
        for _ in range(shown):
            dot = QLabel()
            dot.setFixedSize(5, 5)
            dot.setStyleSheet(f"background: {C.PRI_DIM}; border-radius: 2px;")
            self.dots_row.addWidget(dot)
        if n > shown:
            more = QLabel(f"+{n - shown}")
            more.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 8px; font-weight: 700; background: transparent;")
            self.dots_row.addWidget(more)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.day)





class CalendarModePanel(QWidget):
    """Modo Calendario: vista mensual respaldada por Google Calendar."""

    _month_sig = pyqtSignal(list, str)
    _action_sig = pyqtSignal(bool, str)
    _search_sig = pyqtSignal(list, str)

    _WEEKDAYS = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
    _MAX_GRID_ROWS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self._panel_style())
        today = _date.today()
        self._year = today.year
        self._month = today.month
        self._selected_date: _date = today
        self._events_by_day: dict[str, list[dict]] = {}
        self._day_cells: dict[str, _CalendarDayCell] = {}
        self._loading = False
        self._search_active = False
        self._search_query = ""
        self._search_results: list[dict] = []
        self._searching = False

        self._month_sig.connect(self._on_month_loaded)
        self._action_sig.connect(self._on_action_done)
        self._search_sig.connect(self._on_search_result)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.month_label = QLabel("")
        self.month_label.setObjectName("CalMonthLabel")
        header.addWidget(self.month_label)

        self.today_btn = QPushButton("Hoy")
        self.today_btn.setObjectName("CalTodayChip")
        self.today_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.today_btn.clicked.connect(self._go_today)
        header.addWidget(self.today_btn)

        self.prev_btn = QPushButton()
        self.prev_btn.setObjectName("CalNavButton")
        self.prev_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 18))
        self.prev_btn.setIconSize(QSize(18, 18))
        self.prev_btn.setFixedSize(30, 30)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setToolTip("Mes anterior")
        self.prev_btn.clicked.connect(lambda: self._shift_month(-1))
        header.addWidget(self.prev_btn)

        self.next_btn = QPushButton()
        self.next_btn.setObjectName("CalNavButton")
        self.next_btn.setIcon(_line_icon("chevron_right", C.TEXT_DIM, 18))
        self.next_btn.setIconSize(QSize(18, 18))
        self.next_btn.setFixedSize(30, 30)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setToolTip("Mes siguiente")
        self.next_btn.clicked.connect(lambda: self._shift_month(1))
        header.addWidget(self.next_btn)

        header.addStretch()

        self.status_label = QLabel("")
        self.status_label.setObjectName("CalStatus")
        header.addWidget(self.status_label)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("CalIconButton")
        self.refresh_btn.setIcon(_line_icon("refresh", C.TEXT_DIM, 17))
        self.refresh_btn.setIconSize(QSize(17, 17))
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.setToolTip("Actualizar")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._load_month)
        header.addWidget(self.refresh_btn)

        self.add_btn = QPushButton()
        self.add_btn.setObjectName("CalAddButton")
        self.add_btn.setIcon(_line_icon("plus", "#0a0e26", 16))
        self.add_btn.setIconSize(QSize(16, 16))
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setToolTip("Nuevo evento")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self._open_add_dialog)
        header.addWidget(self.add_btn)

        root.addLayout(header)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        search_icon = QLabel()
        search_icon.setPixmap(_line_icon("search", C.TEXT_MED, 14).pixmap(14, 14))
        search_row.addWidget(search_icon)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("CalSearchInput")
        self.search_input.setPlaceholderText("Buscar eventos (título, invitado, ubicación…)")
        self.search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self.search_input, stretch=1)
        self.search_clear_btn = QPushButton()
        self.search_clear_btn.setObjectName("CalIconButton")
        self.search_clear_btn.setIcon(_line_icon("close", C.TEXT_DIM, 13))
        self.search_clear_btn.setIconSize(QSize(13, 13))
        self.search_clear_btn.setFixedSize(28, 28)
        self.search_clear_btn.setToolTip("Cerrar búsqueda")
        self.search_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_clear_btn.clicked.connect(self._clear_search)
        self.search_clear_btn.setVisible(False)
        search_row.addWidget(self.search_clear_btn)
        root.addLayout(search_row)

        grid_wrap = QFrame()
        grid_wrap.setObjectName("CalGridWrap")
        grid_lay = QVBoxLayout(grid_wrap)
        grid_lay.setContentsMargins(14, 14, 14, 14)
        grid_lay.setSpacing(8)

        weekday_row = QGridLayout()
        weekday_row.setSpacing(6)
        for i, wd in enumerate(self._WEEKDAYS):
            lbl = QLabel(wd)
            lbl.setObjectName("CalWeekday")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            weekday_row.addWidget(lbl, 0, i)
            weekday_row.setColumnStretch(i, 1)
        grid_lay.addLayout(weekday_row)

        self.days_grid = QGridLayout()
        self.days_grid.setSpacing(6)
        for i in range(7):
            self.days_grid.setColumnStretch(i, 1)
        grid_lay.addLayout(self.days_grid)

        root.addWidget(grid_wrap, stretch=3)

        events_wrap = QFrame()
        events_wrap.setObjectName("CalEventsWrap")
        events_lay = QVBoxLayout(events_wrap)
        events_lay.setContentsMargins(14, 12, 14, 12)
        events_lay.setSpacing(8)

        events_header = QHBoxLayout()
        self.selected_day_label = QLabel("")
        self.selected_day_label.setObjectName("CalSelectedDayLabel")
        events_header.addWidget(self.selected_day_label)
        events_header.addStretch()
        self.new_event_btn = QPushButton("  Nuevo evento")
        self.new_event_btn.setObjectName("CalNewEventButton")
        self.new_event_btn.setIcon(_line_icon("plus", "#0a0e26", 14))
        self.new_event_btn.setIconSize(QSize(14, 14))
        self.new_event_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_event_btn.clicked.connect(self._open_add_dialog)
        events_header.addWidget(self.new_event_btn)
        events_lay.addLayout(events_header)

        self.events_list = QListWidget()
        self.events_list.setObjectName("CalEventsList")
        self.events_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        events_lay.addWidget(self.events_list, stretch=1)

        root.addWidget(events_wrap, stretch=2)

        self._rebuild_grid()
        self._refresh_events_list()
        QTimer.singleShot(150, self._load_month)

    # -- estilos ------------------------------------------------------------
    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
            }}
            QLabel#CalMonthLabel {{
                color: #f8fafc; font-size: 17px; font-weight: 900;
            }}
            QPushButton#CalTodayChip {{
                background: rgba(255,255,255,0.05);
                color: {C.TEXT_MED};
                border: 1px solid rgba(182,196,255,0.22);
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 10px; font-weight: 700;
            }}
            QPushButton#CalTodayChip:hover {{ color: {C.TEXT}; background: rgba(255,255,255,0.09); }}
            QPushButton#CalNavButton, QPushButton#CalIconButton {{
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 8px;
            }}
            QPushButton#CalNavButton:hover, QPushButton#CalIconButton:hover {{
                background: rgba(255,255,255,0.09);
                border-color: rgba(182,196,255,0.30);
            }}
            QPushButton#CalAddButton {{
                background: {C.PRI};
                border-radius: 16px;
            }}
            QPushButton#CalAddButton:hover {{ background: #a7afff; }}
            QLabel#CalStatus {{ color: rgba(188,198,238,0.55); font-size: 11px; }}
            QLineEdit#CalSearchInput {{
                background: rgba(255,255,255,0.045);
                color: {C.TEXT};
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QLineEdit#CalSearchInput:focus {{ border-color: rgba(182,196,255,0.45); }}
            QFrame#CalGridWrap, QFrame#CalEventsWrap {{
                background: rgba(8, 14, 26, 0.82);
                border: 1px solid rgba(182, 196, 255, 0.11);
                border-radius: 12px;
            }}
            QLabel#CalWeekday {{
                color: {C.TEXT_MED};
                background: rgba(255,255,255,0.04);
                border-radius: 10px;
                padding: 5px 0;
                font-size: 9px; font-weight: 800; letter-spacing: 0.6px;
            }}
            QLabel#CalSelectedDayLabel {{ color: {C.TEXT}; font-size: 13px; font-weight: 800; }}
            QPushButton#CalNewEventButton {{
                background: {C.PRI};
                color: #0a0e26;
                border: none;
                border-radius: 7px;
                padding: 0 12px;
                min-height: 28px;
                font-size: 11px; font-weight: 800;
            }}
            QPushButton#CalNewEventButton:hover {{ background: #a7afff; }}
            QListWidget#CalEventsList {{
                background: transparent;
                border: none;
            }}
            QListWidget#CalEventsList::item {{
                border-radius: 8px;
                padding: 2px;
                margin-bottom: 2px;
            }}
        """ + _scrollbar_qss()

    def _style_day_cell(self, cell: "_CalendarDayCell", muted: bool, is_today: bool, is_selected: bool):
        if is_selected:
            bg, border, color = "rgba(94,130,255,0.18)", C.PRI_DIM, C.TEXT
        elif is_today:
            bg, border, color = "rgba(255,255,255,0.05)", "rgba(182,196,255,0.55)", C.TEXT
        elif muted:
            bg, border, color = "rgba(255,255,255,0.015)", "rgba(255,255,255,0.05)", "rgba(154,163,192,0.35)"
        else:
            bg, border, color = "rgba(255,255,255,0.03)", "rgba(255,255,255,0.07)", C.TEXT_DIM
        cell.setStyleSheet(f"""
            QFrame#CalDayCell {{
                background: {bg};
                border: 1.4px solid {border};
                border-radius: 10px;
            }}
            QFrame#CalDayCell:hover {{ border-color: rgba(182,196,255,0.4); }}
        """)
        cell.number_label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 700; background: transparent;"
        )

    # -- carga de datos -------------------------------------------------
    def _load_month(self):
        if self._loading:
            return
        self._loading = True
        self.status_label.setText("Cargando…")
        cal = _calendar_mod.Calendar(firstweekday=0)
        weeks = list(cal.monthdatescalendar(self._year, self._month))
        grid_start = weeks[0][0]
        grid_end = weeks[-1][-1]
        time_min = _datetime.combine(grid_start, _datetime.min.time()).astimezone().isoformat()
        time_max = _datetime.combine(grid_end + _timedelta(days=1), _datetime.min.time()).astimezone().isoformat()

        def work():
            try:
                from actions import google_calendar as gcal
                events = gcal.list_events_range(time_min, time_max)
                self._month_sig.emit(events, "")
            except Exception as e:
                self._month_sig.emit([], str(e))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _event_date(ev: dict) -> _date | None:
        raw = str(ev.get("start") or "")
        if not raw:
            return None
        try:
            from dateutil import parser as dtparser
            return dtparser.parse(raw).date()
        except Exception:
            return None

    def _on_month_loaded(self, events: list, err: str):
        self._loading = False
        if err:
            self.status_label.setText("Sin datos de Google Calendar")
            self._events_by_day = {}
        else:
            by_day: dict[str, list[dict]] = {}
            for ev in events:
                d = self._event_date(ev)
                if d:
                    by_day.setdefault(d.isoformat(), []).append(ev)
            self._events_by_day = by_day
            total = len(events)
            self.status_label.setText(f"{total} evento(s) este mes" if total else "Sin eventos este mes")
        self._rebuild_grid()
        self._refresh_events_list()

    # -- grid -------------------------------------------------------------
    def _rebuild_grid(self):
        while self.days_grid.count():
            item = self.days_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._day_cells = {}
        # Reset every possible row's stretch first — otherwise a leftover
        # stretch factor from a previous 6-week month keeps competing for
        # space against a following 5-week month's rows, so day cells look
        # like they change size when switching months.
        for r in range(self._MAX_GRID_ROWS):
            self.days_grid.setRowStretch(r, 0)

        cal = _calendar_mod.Calendar(firstweekday=0)
        weeks = list(cal.monthdatescalendar(self._year, self._month))
        today = _date.today()

        for row, week in enumerate(weeks):
            self.days_grid.setRowStretch(row, 1)
            for col, day in enumerate(week):
                key = day.isoformat()
                n_events = len(self._events_by_day.get(key, []))
                cell = _CalendarDayCell(day)
                cell.set_event_count(n_events)
                muted = day.month != self._month
                is_today = day == today
                is_selected = day == self._selected_date
                self._style_day_cell(cell, muted, is_today, is_selected)
                cell.clicked.connect(self._select_date)
                self.days_grid.addWidget(cell, row, col)
                self._day_cells[key] = cell

        self.month_label.setText(f"{_MONTH_NAMES_ES[self._month - 1]} {self._year}")

    def _select_date(self, d: _date):
        self._selected_date = d
        if self._search_active:
            self.search_input.clear()
            self._search_active = False
            self._search_query = ""
            self._search_results = []
            self.search_clear_btn.setVisible(False)
        self._rebuild_grid()
        self._refresh_events_list()

    def _shift_month(self, delta: int):
        m = self._month - 1 + delta
        self._year += m // 12
        self._month = m % 12 + 1
        self._load_month()

    def _go_today(self):
        today = _date.today()
        self._year, self._month = today.year, today.month
        self._selected_date = today
        self._load_month()

    # -- panel de eventos del día ----------------------------------------
    def _format_day_es(self, d: _date) -> str:
        return f"{_WEEKDAY_NAMES_ES[d.weekday()]}, {d.day} de {_MONTH_NAMES_ES[d.month - 1]}"

    def _format_event_time(self, ev: dict) -> str:
        if ev.get("all_day"):
            return "Todo el día"
        try:
            from dateutil import parser as dtparser
            dt = dtparser.parse(str(ev.get("start") or ""))
            end_raw = str(ev.get("end") or "")
            if end_raw:
                end_dt = dtparser.parse(end_raw)
                return f"{dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}"
            return dt.strftime("%H:%M")
        except Exception:
            return str(ev.get("start") or "")

    def _refresh_events_list(self):
        self.events_list.clear()

        if self._search_active:
            self.selected_day_label.setText(
                f"Resultados para «{self._search_query}»" if self._search_query else "Resultados"
            )
            if self._searching:
                item = QListWidgetItem("Buscando…")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.events_list.addItem(item)
                return
            if not self._search_results:
                item = QListWidgetItem("Sin resultados.")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.events_list.addItem(item)
                return
            for ev in self._search_results:
                self._add_event_row(ev, show_date=True)
            return

        d = self._selected_date
        self.selected_day_label.setText(self._format_day_es(d))
        events = sorted(
            self._events_by_day.get(d.isoformat(), []),
            key=lambda e: str(e.get("start") or ""),
        )
        if not events:
            item = QListWidgetItem("Sin eventos este día.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.events_list.addItem(item)
            return
        for ev in events:
            self._add_event_row(ev)

    def _add_event_row(self, ev: dict, show_date: bool = False):
        item = QListWidgetItem()
        self.events_list.addItem(item)
        row = _EventRow()
        row.setStyleSheet("background: rgba(255,255,255,0.03); border-radius: 8px;")
        row.clicked.connect(lambda e=ev: self._open_event_dialog(e))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {C.PRI_DIM}; font-size: 12px; background: transparent;")
        lay.addWidget(dot)

        text = QVBoxLayout()
        text.setSpacing(1)
        title_lbl = QLabel(str(ev.get("summary") or "(sin título)"))
        title_lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 700; background: transparent;")
        title_lbl.setWordWrap(True)
        meta_bits = [self._format_event_time(ev)]
        if show_date:
            d = self._event_date(ev)
            if d:
                meta_bits.insert(0, f"{d.day} {_MONTH_NAMES_ES[d.month - 1][:3]} {d.year}")
        n_attendees = len(ev.get("attendees") or [])
        if n_attendees:
            meta_bits.append(f"{n_attendees} invitado(s)")
        time_lbl = QLabel("  ·  ".join(meta_bits))
        time_lbl.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 10px; background: transparent;")
        text.addWidget(title_lbl)
        text.addWidget(time_lbl)
        lay.addLayout(text, stretch=1)

        del_btn = QPushButton()
        del_btn.setIcon(_line_icon("trash", C.TEXT_DIM, 15))
        del_btn.setIconSize(QSize(15, 15))
        del_btn.setFixedSize(26, 26)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("Eliminar evento")
        del_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; }
            QPushButton:hover { background: rgba(255,94,130,0.14); border-radius: 6px; }
        """)
        del_btn.clicked.connect(lambda _=False, eid=ev.get("id"): self._delete_event(eid))
        lay.addWidget(del_btn)

        item.setSizeHint(row.sizeHint())
        self.events_list.setItemWidget(item, row)

    # -- búsqueda ------------------------------------------------------------
    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            self._clear_search()
            return
        self._search_active = True
        self._search_query = query
        self._searching = True
        self.search_clear_btn.setVisible(True)
        self._refresh_events_list()

        def work():
            try:
                from actions import google_calendar as gcal
                results = gcal.search_events(query, max_results=30)
                self._search_sig.emit(results, "")
            except Exception as e:
                self._search_sig.emit([], str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_search_result(self, results: list, err: str):
        self._searching = False
        if err:
            self.status_label.setText("Error buscando eventos")
            self._search_results = []
        else:
            self._search_results = results
            self.status_label.setText(f"{len(results)} resultado(s)" if results else "Sin resultados")
        self._refresh_events_list()

    def _clear_search(self):
        self.search_input.clear()
        self._search_active = False
        self._search_query = ""
        self._search_results = []
        self._searching = False
        self.search_clear_btn.setVisible(False)
        total = sum(len(v) for v in self._events_by_day.values())
        self.status_label.setText(f"{total} evento(s) este mes" if total else "Sin eventos este mes")
        self._refresh_events_list()

    # -- acciones ----------------------------------------------------------
    def _open_add_dialog(self):
        dialog = CalendarEventDialog(self, default_date=self._selected_date)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload:
            return
        self.status_label.setText("Creando evento…")

        def work():
            try:
                from actions import google_calendar as gcal
                gcal.create_event(
                    summary=payload["summary"],
                    start=payload["start"],
                    end=payload["end"],
                    description=payload.get("description", ""),
                    location=payload.get("location", ""),
                    attendees=payload.get("attendees") or None,
                )
                self._action_sig.emit(True, "Evento creado.")
            except Exception as e:
                self._action_sig.emit(False, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _open_event_dialog(self, ev: dict):
        dialog = CalendarEventDialog(self, event=ev)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.delete_requested():
            self._delete_event(dialog.event_id())
            return
        payload = dialog.payload()
        event_id = dialog.event_id()
        if not payload or not event_id:
            return
        self._update_event(event_id, payload)

    def _update_event(self, event_id: str, payload: dict):
        self.status_label.setText("Guardando cambios…")

        def work():
            try:
                from actions import google_calendar as gcal
                gcal.update_event(
                    event_id,
                    summary=payload["summary"],
                    start=payload["start"],
                    end=payload["end"],
                    description=payload.get("description", ""),
                    location=payload.get("location", ""),
                    attendees=payload.get("attendees") or [],
                )
                self._action_sig.emit(True, "Evento actualizado.")
            except Exception as e:
                self._action_sig.emit(False, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _delete_event(self, event_id: str | None):
        if not event_id:
            return
        self.status_label.setText("Eliminando evento…")

        def work():
            try:
                from actions import google_calendar as gcal
                gcal.delete_event(event_id)
                self._action_sig.emit(True, "Evento eliminado.")
            except Exception as e:
                self._action_sig.emit(False, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_action_done(self, success: bool, message: str):
        self.status_label.setText(message)
        if not success:
            return
        if self._search_active and self._search_query:
            self._do_search()
        else:
            self._load_month()




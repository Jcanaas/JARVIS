from __future__ import annotations

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from actions.perf_helpers import SharedThreadPool
from actions.cardtrader_optimizer import FAST_SEARCH_CAP
from ..theme import *
from ..icons import *
from ..widgets import *


def _fmt_price(cents: int, currency: str) -> str:
    amount = cents / 100
    if currency == "EUR":
        return f"{amount:.2f} €"
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{amount:.2f} {currency}"


class _Badge(QLabel):
    def __init__(self, text: str, kind: str = "zero", parent=None):
        super().__init__(text, parent)
        colors = {
            "zero": (C.GREEN, "rgba(74, 222, 128, 0.14)", "rgba(74, 222, 128, 0.35)"),
            "oneday": (C.PRI, "rgba(182, 196, 255, 0.16)", "rgba(182, 196, 255, 0.38)"),
            "direct": (C.TEXT_MED, "rgba(255, 255, 255, 0.06)", "rgba(255, 255, 255, 0.12)"),
            "warn": (C.RED, "rgba(255, 94, 130, 0.14)", "rgba(255, 94, 130, 0.35)"),
        }
        fg, bg, border = colors.get(kind, colors["direct"])
        self.setStyleSheet(f"""
            QLabel {{
                color: {fg};
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 650;
            }}
        """)


class _PillTab(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setObjectName("CTTab")


class _OfferRow(QFrame):
    def __init__(self, title: str, subtitle: str, price_text: str, badges: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("CTOfferRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name_lbl = QLabel(title)
        name_lbl.setObjectName("CTOfferTitle")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("CTOfferSubtitle")
        text_box.addWidget(name_lbl)
        text_box.addWidget(sub_lbl)
        lay.addLayout(text_box, stretch=1)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        for label, kind in badges:
            badge_row.addWidget(_Badge(label, kind))
        lay.addLayout(badge_row)

        price_lbl = QLabel(price_text)
        price_lbl.setObjectName("CTOfferPrice")
        lay.addWidget(price_lbl)


class CardTraderModePanel(QWidget):
    _result_sig = pyqtSignal(str, object)
    _quote_item_sig = pyqtSignal(object, int, int)
    _search_progress_sig = pyqtSignal(int, int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_quote = None
        self._quote_resolved = 0
        self.setStyleSheet(self._panel_style())
        self._result_sig.connect(self._handle_result)
        self._quote_item_sig.connect(self._on_quote_item)
        self._search_progress_sig.connect(self._on_search_progress)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # -- header ------------------------------------------------
        heading = QHBoxLayout()
        heading.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Cartas Magic")
        title.setObjectName("CTTitle")
        subtitle = QLabel("CardTrader — precios y CardTrader Zero")
        subtitle.setObjectName("CTSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()

        self.catalog_label = QLabel("Catálogo: —")
        self.catalog_label.setObjectName("CTCatalogLabel")
        heading.addWidget(self.catalog_label)

        sync_btn = QPushButton()
        sync_btn.setObjectName("CTIconButton")
        sync_btn.setIcon(_line_icon("refresh", C.TEXT_DIM, 16))
        sync_btn.setToolTip("Sincronizar catálogo (sets nuevos)")
        sync_btn.setFixedSize(32, 32)
        sync_btn.clicked.connect(self._sync_catalog)
        heading.addWidget(sync_btn)
        root.addLayout(heading)

        # -- tabs ----------------------------------------------------
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)
        self.tab_search = _PillTab("Buscar")
        self.tab_deck = _PillTab("Mazo")
        self.tab_cart = _PillTab("Carrito")
        self.tab_search.setChecked(True)
        self._tab_group = QButtonGroup(self)
        for i, btn in enumerate((self.tab_search, self.tab_deck, self.tab_cart)):
            self._tab_group.addButton(btn, i)
            tabs_row.addWidget(btn)
        tabs_row.addStretch()
        self._tab_group.idClicked.connect(self._on_tab_changed)
        root.addLayout(tabs_row)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_view())
        self.stack.addWidget(self._build_deck_view())
        self.stack.addWidget(self._build_cart_view())
        root.addWidget(self.stack, stretch=1)

        self.status = QLabel("")
        self.status.setObjectName("CTStatus")
        root.addWidget(self.status)

        self._refresh_catalog_label()

    # -- tab switching --------------------------------------------------

    def _on_tab_changed(self, idx: int):
        self.stack.setCurrentIndex(idx)
        if idx == 2:
            self.view_cart()

    # -- search view ------------------------------------------------

    def _build_search_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = SearchGlowInput("Buscar una carta de Magic...")
        self.search_input.returnPressed.connect(self.search_card)
        search_row.addWidget(self.search_input, stretch=1)
        search_btn = QPushButton("Buscar")
        search_btn.setObjectName("CTPrimaryButton")
        search_btn.clicked.connect(self.search_card)
        search_row.addWidget(search_btn)
        lay.addLayout(search_row)

        filters = QFrame()
        filters.setObjectName("CTFilterBar")
        f_lay = QHBoxLayout(filters)
        f_lay.setContentsMargins(12, 8, 12, 8)
        f_lay.setSpacing(16)

        zero_box = QHBoxLayout()
        zero_box.setSpacing(8)
        zero_box.addWidget(QLabel("Solo CT Zero"))
        self.zero_toggle = ToggleSwitch(checked=True)
        zero_box.addWidget(self.zero_toggle)
        f_lay.addLayout(zero_box)

        f_lay.addWidget(self._filter_label("Condición mínima"))
        self.condition_combo = QComboBox()
        self.condition_combo.setObjectName("CTCombo")
        self.condition_combo.addItems([
            "Poor", "Heavily Played", "Played", "Moderately Played", "Slightly Played", "Near Mint", "Mint",
        ])
        self.condition_combo.setCurrentText("Moderately Played")
        f_lay.addWidget(self.condition_combo)

        f_lay.addWidget(self._filter_label("Idioma"))
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("CTCombo")
        self.language_combo.addItems(["Cualquiera", "en", "es", "de", "fr", "it", "jp", "pt"])
        f_lay.addWidget(self.language_combo)

        f_lay.addWidget(self._filter_label("Foil"))
        self.foil_combo = QComboBox()
        self.foil_combo.setObjectName("CTCombo")
        self.foil_combo.addItems(["Indiferente", "Solo foil", "Solo normal"])
        f_lay.addWidget(self.foil_combo)

        f_lay.addStretch()
        self.all_versions_check = QCheckBox("Comparar todas las versiones")
        self.all_versions_check.setObjectName("CTCheck")
        f_lay.addWidget(self.all_versions_check)
        self.fast_check = QCheckBox("Modo rápido")
        self.fast_check.setObjectName("CTCheck")
        self.fast_check.setToolTip(
            f"Por defecto se revisan TODAS las ediciones de la carta (precio mínimo real garantizado, "
            "puede tardar en cartas muy reeditadas). Marca esto para revisar solo un tope de "
            f"{FAST_SEARCH_CAP} ediciones y acabar antes, a costa de no garantizar el mínimo absoluto."
        )
        f_lay.addWidget(self.fast_check)
        lay.addWidget(filters)

        self.search_progress = QProgressBar()
        self.search_progress.setObjectName("CTProgress")
        self.search_progress.setTextVisible(True)
        self.search_progress.setFixedHeight(18)
        self.search_progress.setVisible(False)
        lay.addWidget(self.search_progress)

        results_label = QLabel("RESULTADOS")
        results_label.setObjectName("CTSectionLabel")
        lay.addWidget(results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("CTScroll")
        self.search_results_container = QWidget()
        self.search_results_layout = QVBoxLayout(self.search_results_container)
        self.search_results_layout.setContentsMargins(0, 0, 0, 0)
        self.search_results_layout.setSpacing(6)
        self.search_results_layout.addStretch()
        scroll.setWidget(self.search_results_container)
        lay.addWidget(scroll, stretch=1)

        return w

    def _filter_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("CTFilterLabel")
        return lbl

    def search_card(self):
        name = self.search_input.text().strip()
        if not name:
            return
        self._clear_layout(self.search_results_layout)
        self.search_progress.setRange(0, 0)
        self.search_progress.setFormat("Buscando ediciones...")
        self.search_progress.setVisible(True)
        self.status.setText(f"Buscando '{name}'...")

        filters = self._current_filters()
        all_versions = self.all_versions_check.isChecked()

        def work():
            from actions.cardtrader_catalog import find_blueprints
            from actions.cardtrader_optimizer import best_offer, compare_versions
            from actions.cardtrader_api import CardTraderClient
            client = CardTraderClient()
            if all_versions:
                return ("versions", compare_versions(
                    name, filters, client=client,
                    progress_cb=lambda i, n, best: self._search_progress_sig.emit(i, n, best),
                ))
            blueprints = find_blueprints(name)
            if not blueprints:
                return ("not_found", name)
            result = best_offer(
                blueprints, qty=1, filters=filters, client=client,
                progress_cb=lambda i, n, best: self._search_progress_sig.emit(i, n, best),
            )
            return ("offer", result)

        self._run("search", work)

    def _current_filters(self):
        from actions.cardtrader_optimizer import OfferFilters
        lang = self.language_combo.currentText()
        foil_txt = self.foil_combo.currentText()
        foil = None if foil_txt == "Indiferente" else (foil_txt == "Solo foil")
        fast = self.fast_check.isChecked()
        return OfferFilters(
            zero_only=self.zero_toggle.isChecked(),
            min_condition=self.condition_combo.currentText(),
            languages=None if lang == "Cualquiera" else [lang],
            foil=foil,
            max_printings=FAST_SEARCH_CAP if fast else None,
        )

    # -- deck view --------------------------------------------------

    def _build_deck_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        self.deck_text = QTextEdit()
        self.deck_text.setObjectName("CTDeckText")
        self.deck_text.setPlaceholderText(
            "Pega aquí tu mazo de Moxfield en texto plano...\n\n"
            "4 Lightning Bolt\n1 Sol Ring (C21) 263\n1 Fire // Ice (MH2) 290"
        )
        self.deck_text.setMinimumHeight(140)
        self.deck_text.setMaximumHeight(180)
        lay.addWidget(self.deck_text)

        filters = QFrame()
        filters.setObjectName("CTFilterBar")
        f_lay = QHBoxLayout(filters)
        f_lay.setContentsMargins(12, 8, 12, 8)
        f_lay.setSpacing(16)

        zero_box = QHBoxLayout()
        zero_box.setSpacing(8)
        zero_box.addWidget(QLabel("Solo CT Zero"))
        self.deck_zero_toggle = ToggleSwitch(checked=True)
        zero_box.addWidget(self.deck_zero_toggle)
        f_lay.addLayout(zero_box)

        f_lay.addWidget(self._filter_label("Condición mínima"))
        self.deck_condition_combo = QComboBox()
        self.deck_condition_combo.setObjectName("CTCombo")
        self.deck_condition_combo.addItems([
            "Poor", "Heavily Played", "Played", "Moderately Played", "Slightly Played", "Near Mint", "Mint",
        ])
        self.deck_condition_combo.setCurrentText("Moderately Played")
        f_lay.addWidget(self.deck_condition_combo)

        f_lay.addWidget(self._filter_label("Idioma"))
        self.deck_language_combo = QComboBox()
        self.deck_language_combo.setObjectName("CTCombo")
        self.deck_language_combo.addItems(["Cualquiera", "en", "es", "de", "fr", "it", "jp", "pt"])
        f_lay.addWidget(self.deck_language_combo)

        f_lay.addStretch()
        self.respect_printings_check = QCheckBox("Respetar edición exacta del export")
        self.respect_printings_check.setObjectName("CTCheck")
        f_lay.addWidget(self.respect_printings_check)
        lay.addWidget(filters)

        quote_row = QHBoxLayout()
        quote_btn = QPushButton("Cotizar mazo")
        quote_btn.setObjectName("CTPrimaryButton")
        quote_btn.clicked.connect(self.quote_deck)
        quote_row.addWidget(quote_btn)
        quote_row.addStretch()
        self.deck_total_label = QLabel("")
        self.deck_total_label.setObjectName("CTTotalLabel")
        self.deck_total_label.setMinimumWidth(260)
        quote_row.addWidget(self.deck_total_label)
        add_cart_btn = QPushButton("Añadir mazo al carrito")
        add_cart_btn.setObjectName("CTSecondaryButton")
        add_cart_btn.clicked.connect(self.add_deck_to_cart)
        quote_row.addWidget(add_cart_btn)
        lay.addLayout(quote_row)

        self.deck_progress = QProgressBar()
        self.deck_progress.setObjectName("CTProgress")
        self.deck_progress.setTextVisible(True)
        self.deck_progress.setFixedHeight(18)
        self.deck_progress.setVisible(False)
        lay.addWidget(self.deck_progress)

        table_label = QLabel("DESGLOSE")
        table_label.setObjectName("CTSectionLabel")
        lay.addWidget(table_label)

        self.deck_table = QTableWidget(0, 5)
        self.deck_table.setObjectName("CTTable")
        self.deck_table.setHorizontalHeaderLabels(["Carta", "Cant.", "Mejor precio", "Edición", "CT Zero"])
        self.deck_table.horizontalHeader().setStretchLastSection(False)
        self.deck_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            self.deck_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.deck_table.verticalHeader().setVisible(False)
        self.deck_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.deck_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.deck_table, stretch=1)

        self.deck_not_found_label = QLabel("")
        self.deck_not_found_label.setObjectName("CTWarnLabel")
        self.deck_not_found_label.setWordWrap(True)
        lay.addWidget(self.deck_not_found_label)

        return w

    def quote_deck(self):
        text = self.deck_text.toPlainText().strip()
        if not text:
            return

        from actions.deck_parser import parse_deck_text
        parsed = parse_deck_text(text)
        if not parsed.entries:
            self.status.setText("No pude interpretar ninguna linea del mazo.")
            return

        self.deck_table.setRowCount(0)
        self.deck_total_label.setText("")
        self.deck_not_found_label.setText("")
        self._quote_resolved = 0
        total = len(parsed.entries)
        self.deck_progress.setRange(0, total)
        self.deck_progress.setValue(0)
        self.deck_progress.setFormat(f"Cotizando 0/{total}...")
        self.deck_progress.setVisible(True)
        self.status.setText("Cotizando mazo (puede tardar por el limite de CardTrader)...")

        from actions.cardtrader_optimizer import OfferFilters
        lang = self.deck_language_combo.currentText()
        filters = OfferFilters(
            zero_only=self.deck_zero_toggle.isChecked(),
            min_condition=self.deck_condition_combo.currentText(),
            languages=None if lang == "Cualquiera" else [lang],
        )
        respect = self.respect_printings_check.isChecked()
        entries = parsed.entries

        def work():
            from actions.cardtrader_optimizer import quote_deck
            quote = quote_deck(
                entries, filters, respect_printings=respect,
                item_cb=lambda item, i, n: self._quote_item_sig.emit(item, i, n),
            )
            return quote

        self._run("quote", work)

    def add_deck_to_cart(self):
        if self._last_quote is None:
            self.status.setText("Cotiza el mazo primero.")
            return
        self.status.setText("Añadiendo mazo al carrito...")

        quote = self._last_quote

        def work():
            from actions.cardtrader_api import CardTraderClient, CardTraderError
            client = CardTraderClient()
            added, failed = 0, []
            for item in quote.items:
                if not item.result:
                    continue
                for offer, qty in item.result.offers_used:
                    try:
                        client.cart_add(offer.product_id, qty, via_zero=offer.can_sell_via_hub)
                        added += qty
                    except CardTraderError:
                        failed.append(item.entry.name)
            return {"added": added, "failed": failed}

        self._run("add_deck", work)

    # -- cart view --------------------------------------------------

    def _build_cart_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Actualizar")
        refresh_btn.setObjectName("CTSecondaryButton")
        refresh_btn.clicked.connect(self.view_cart)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        clear_btn = QPushButton("Vaciar carrito")
        clear_btn.setObjectName("CTDangerButton")
        clear_btn.clicked.connect(self.clear_cart)
        toolbar.addWidget(clear_btn)
        lay.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("CTScroll")
        self.cart_items_container = QWidget()
        self.cart_items_layout = QVBoxLayout(self.cart_items_container)
        self.cart_items_layout.setContentsMargins(0, 0, 0, 0)
        self.cart_items_layout.setSpacing(6)
        self.cart_items_layout.addStretch()
        scroll.setWidget(self.cart_items_container)
        lay.addWidget(scroll, stretch=1)

        totals = QFrame()
        totals.setObjectName("CTTotalsBar")
        t_lay = QVBoxLayout(totals)
        t_lay.setContentsMargins(14, 10, 14, 10)
        t_lay.setSpacing(4)
        self.cart_subtotal_label = QLabel("Subtotal: —")
        self.cart_fees_label = QLabel("Fees / envío: —")
        self.cart_total_label = QLabel("Total: —")
        self.cart_total_label.setObjectName("CTTotalLabel")
        t_lay.addWidget(self.cart_subtotal_label)
        t_lay.addWidget(self.cart_fees_label)
        t_lay.addWidget(self.cart_total_label)
        lay.addWidget(totals)

        note = QLabel("La compra final se confirma en la web o app de CardTrader.")
        note.setObjectName("CTNote")
        lay.addWidget(note)

        return w

    def view_cart(self):
        self.status.setText("Cargando carrito...")

        def work():
            from actions.cardtrader_api import CardTraderClient
            return CardTraderClient().cart()

        self._run("cart", work)

    def clear_cart(self):
        self.status.setText("Vaciando carrito...")

        def work():
            from actions.cardtrader_api import CardTraderClient, CardTraderError
            client = CardTraderClient()
            cart = client.cart()
            removed = 0
            for sub in cart.get("subcarts", []):
                for item in sub.get("cart_items", []):
                    try:
                        client.cart_remove(item["product"]["id"], item["quantity"])
                        removed += 1
                    except CardTraderError:
                        pass
            return removed

        self._run("clear_cart", work)

    def _remove_cart_item(self, product_id: int, quantity: int):
        self.status.setText("Quitando artículo...")

        def work():
            from actions.cardtrader_api import CardTraderClient
            CardTraderClient().cart_remove(product_id, quantity)
            return CardTraderClient().cart()

        self._run("cart", work)

    # -- catalog ------------------------------------------------

    def _refresh_catalog_label(self):
        try:
            from actions.cardtrader_catalog import catalog_status
            s = catalog_status()
            self.catalog_label.setText(f"Catálogo: {s['blueprints']:,} cartas".replace(",", "."))
        except Exception:
            self.catalog_label.setText("Catálogo: —")

    def _sync_catalog(self):
        self.status.setText("Sincronizando catálogo...")

        def work():
            from actions.cardtrader_catalog import ensure_catalog
            return ensure_catalog()

        self._run("sync", work)

    # -- threading helper --------------------------------------------------

    def _run(self, op: str, fn):
        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            self._result_sig.emit(op, result)
        SharedThreadPool().submit(worker)

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # -- result handling --------------------------------------------------

    def _handle_result(self, op: str, result):
        from actions.cardtrader_api import CardTraderError

        if isinstance(result, Exception):
            msg = str(result) if isinstance(result, CardTraderError) else f"Error: {result}"
            self.status.setText(msg)
            return

        if op == "sync":
            self.status.setText(
                f"Catálogo sincronizado: {result.get('blueprints_synced', 0)} cartas nuevas "
                f"en {result.get('seconds', 0)}s."
            )
            self._refresh_catalog_label()
            return

        if op == "search":
            self._render_search_result(result)
            return

        if op == "quote":
            self._render_deck_quote(result)
            return

        if op == "add_deck":
            added, failed = result["added"], result["failed"]
            msg = f"Añadidas {added} cartas al carrito."
            if failed:
                msg += f" Fallaron: {', '.join(failed[:5])}."
            self.status.setText(msg)
            return

        if op == "cart":
            self._render_cart(result)
            return

        if op == "clear_cart":
            self.status.setText(f"Carrito vaciado: {result} líneas eliminadas.")
            self.view_cart()
            return

    def _on_search_progress(self, i: int, total: int, best):
        if self.search_progress.maximum() == 0:
            self.search_progress.setRange(0, total)
        self.search_progress.setValue(i)
        if best is not None:
            self.search_progress.setFormat(f"Edición {i}/{total} — mejor hasta ahora: {_fmt_price(best.price_cents, best.price_currency)}")
        else:
            self.search_progress.setFormat(f"Edición {i}/{total}...")

    def _render_search_result(self, result):
        self._clear_layout(self.search_results_layout)
        self.search_progress.setFormat("Búsqueda completa")
        QTimer.singleShot(900, lambda: self.search_progress.setVisible(False))
        kind, payload = result

        if kind == "not_found":
            self.status.setText(f"No encontré '{payload}' en el catálogo.")
            return

        if kind == "offer":
            if not payload or payload.covered_qty == 0:
                self.status.setText("No hay ofertas que cumplan los filtros.")
                return
            rows = [(o, q) for o, q in payload.offers_used] + [(o, 0) for o in payload.runner_ups]
            for offer, _qty in rows:
                badges = []
                badges.append(("CT Zero", "zero") if offer.can_sell_via_hub else ("Directa", "direct"))
                if offer.one_day_ready:
                    badges.append(("1 día", "oneday"))
                subtitle = f"{offer.expansion_name} ({offer.expansion_code.upper()}) · {offer.condition} · {offer.language}{' · foil' if offer.foil else ''} · {offer.seller_username}"
                row = _OfferRow(offer.name, subtitle, _fmt_price(offer.price_cents, offer.price_currency), badges)
                self.search_results_layout.insertWidget(self.search_results_layout.count() - 1, row)
            self.status.setText(f"{len(rows)} oferta(s) encontradas.")
            return

        if kind == "versions":
            summaries = [s for s in payload if s.best_offer is not None]
            if not summaries:
                self.status.setText("No hay versiones que cumplan los filtros.")
                return
            for s in summaries:
                offer = s.best_offer
                badges = []
                badges.append(("CT Zero", "zero") if offer.can_sell_via_hub else ("Directa", "direct"))
                if offer.one_day_ready:
                    badges.append(("1 día", "oneday"))
                subtitle = f"{s.expansion_name} ({s.expansion_code.upper()}) · {offer.condition} · {s.offers_count} oferta(s)"
                row = _OfferRow(offer.name, subtitle, _fmt_price(offer.price_cents, offer.price_currency), badges)
                self.search_results_layout.insertWidget(self.search_results_layout.count() - 1, row)
            self.status.setText(f"{len(summaries)} versión(es) encontradas.")
            return

    def _on_quote_item(self, item, i: int, total: int):
        self._quote_resolved = i
        self.deck_progress.setValue(i)
        self.deck_progress.setFormat(f"Cotizando {i}/{total}: {item.entry.name}")
        if item.result:
            self._add_deck_row(item)

    def _add_deck_row(self, item):
        row = self.deck_table.rowCount()
        self.deck_table.insertRow(row)
        offer, _qty = item.result.offers_used[0]
        total_cents = sum(o.price_cents * q for o, q in item.result.offers_used)

        self.deck_table.setItem(row, 0, QTableWidgetItem(item.entry.name))
        qty_item = QTableWidgetItem(str(item.entry.qty))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.deck_table.setItem(row, 1, qty_item)
        self.deck_table.setItem(row, 2, QTableWidgetItem(_fmt_price(total_cents, offer.price_currency)))
        self.deck_table.setItem(row, 3, QTableWidgetItem(offer.expansion_code.upper()))
        zero_item = QTableWidgetItem("✓" if offer.can_sell_via_hub else "—")
        zero_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.deck_table.setItem(row, 4, zero_item)
        self.deck_table.scrollToBottom()

    def _render_deck_quote(self, quote):
        # Rows were already inserted incrementally via _on_quote_item as each
        # card resolved; this just finalises totals/summary once the full
        # deck is done.
        self._last_quote = quote

        resolved = [it for it in quote.items if it.result]
        unresolved = [it for it in quote.items if not it.result]

        totals_str = ", ".join(_fmt_price(c, cur) for cur, c in quote.total_by_currency.items())
        self.deck_total_label.setText(f"Total: {totals_str or '0'}  ·  {len(resolved)}/{len(quote.items)} resueltas")

        if unresolved:
            names = ", ".join(it.entry.name for it in unresolved[:10])
            more = "…" if len(unresolved) > 10 else ""
            self.deck_not_found_label.setText(f"No resueltas: {names}{more}")
        else:
            self.deck_not_found_label.setText("")

        self.deck_progress.setFormat("Cotización lista")
        QTimer.singleShot(1200, lambda: self.deck_progress.setVisible(False))
        self.status.setText("Cotización lista.")

    def _render_cart(self, cart: dict):
        self._clear_layout(self.cart_items_layout)

        total = cart.get("total", {})
        subtotal = cart.get("subtotal", {})
        shipping = cart.get("shipping_cost", {})
        ct_fee = cart.get("ct_zero_fee_amount", {})

        self.cart_subtotal_label.setText(f"Subtotal: {_fmt_price(subtotal.get('cents', 0), subtotal.get('currency', 'EUR'))}")
        fees_parts = []
        if shipping.get("cents"):
            fees_parts.append(f"envío {_fmt_price(shipping['cents'], shipping.get('currency', 'EUR'))}")
        if ct_fee.get("cents"):
            fees_parts.append(f"CT Zero {_fmt_price(ct_fee['cents'], ct_fee.get('currency', 'EUR'))}")
        self.cart_fees_label.setText("Fees / envío: " + (", ".join(fees_parts) if fees_parts else "—"))
        self.cart_total_label.setText(f"Total: {_fmt_price(total.get('cents', 0), total.get('currency', 'EUR'))}")

        subcarts = cart.get("subcarts", [])
        if not subcarts:
            empty = QLabel("El carrito está vacío.")
            empty.setObjectName("CTNote")
            self.cart_items_layout.insertWidget(0, empty)
        else:
            for sub in subcarts:
                seller = sub.get("seller", {}).get("username", "?")
                for item in sub.get("cart_items", []):
                    product = item.get("product", {})
                    row = self._build_cart_row(
                        name=product.get("name_en", "?"),
                        seller=seller,
                        qty=item.get("quantity", 1),
                        price_cents=item.get("price_cents", 0),
                        currency=item.get("price_currency", "EUR"),
                        product_id=product.get("id"),
                    )
                    self.cart_items_layout.insertWidget(self.cart_items_layout.count() - 1, row)

        self.status.setText(f"Carrito: {len(subcarts)} vendedor(es).")

    def _build_cart_row(self, name, seller, qty, price_cents, currency, product_id) -> QWidget:
        row = QFrame()
        row.setObjectName("CTOfferRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name_lbl = QLabel(f"{name}  ×{qty}")
        name_lbl.setObjectName("CTOfferTitle")
        sub_lbl = QLabel(f"Vendedor: {seller}")
        sub_lbl.setObjectName("CTOfferSubtitle")
        text_box.addWidget(name_lbl)
        text_box.addWidget(sub_lbl)
        lay.addLayout(text_box, stretch=1)

        price_lbl = QLabel(_fmt_price(price_cents * qty, currency))
        price_lbl.setObjectName("CTOfferPrice")
        lay.addWidget(price_lbl)

        remove_btn = QPushButton()
        remove_btn.setObjectName("CTIconButton")
        remove_btn.setIcon(_line_icon("trash", C.RED, 15))
        remove_btn.setFixedSize(30, 30)
        remove_btn.setToolTip("Quitar del carrito")
        if product_id:
            remove_btn.clicked.connect(lambda: self._remove_cart_item(product_id, qty))
        lay.addWidget(remove_btn)

        return row

    # -- style --------------------------------------------------

    def _panel_style(self) -> str:
        return f"""
            QLabel#CTTitle {{
                color: {C.TEXT};
                font-size: 22px;
                font-weight: 750;
                background: transparent;
            }}
            QLabel#CTSubtitle {{
                color: {C.TEXT_MED};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#CTCatalogLabel {{
                color: {C.TEXT_MED};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#CTStatus {{
                color: {C.TEXT_MED};
                font-size: 12px;
                background: transparent;
                padding: 2px 4px;
            }}
            QLabel#CTSectionLabel {{
                color: {C.TEXT_MED};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                background: transparent;
                padding: 4px 2px;
            }}
            QLabel#CTFilterLabel {{
                color: {C.TEXT_MED};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#CTTotalLabel {{
                color: {C.PRI};
                font-size: 15px;
                font-weight: 750;
                background: transparent;
            }}
            QLabel#CTWarnLabel {{
                color: {C.RED};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#CTNote {{
                color: {C.TEXT_MED};
                font-size: 11px;
                background: transparent;
                padding: 4px;
            }}

            QPushButton#CTTab {{
                background: transparent;
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER_A};
                border-radius: 17px;
                padding: 0 18px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#CTTab:hover {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT};
            }}
            QPushButton#CTTab:checked {{
                background: rgba(94, 130, 255, 0.18);
                color: {C.PRI};
                border-color: rgba(182, 196, 255, 0.40);
            }}

            QPushButton#CTPrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C.PRI_DIM}, stop:1 {C.ACC2});
                color: #0A0C16;
                border: none;
                border-radius: 9px;
                padding: 9px 18px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#CTPrimaryButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C.ACC2}, stop:1 {C.PRI});
            }}
            QPushButton#CTSecondaryButton {{
                background: rgba(255, 255, 255, 0.045);
                color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER_A};
                border-radius: 9px;
                padding: 9px 16px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#CTSecondaryButton:hover {{
                background: rgba(255, 255, 255, 0.09);
                border-color: rgba(182, 196, 255, 0.30);
            }}
            QPushButton#CTDangerButton {{
                background: rgba(255, 94, 130, 0.10);
                color: {C.RED};
                border: 1px solid rgba(255, 94, 130, 0.28);
                border-radius: 9px;
                padding: 9px 16px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#CTDangerButton:hover {{
                background: rgba(255, 94, 130, 0.18);
            }}
            QPushButton#CTIconButton {{
                background: rgba(255, 255, 255, 0.045);
                border: 1px solid {C.BORDER_A};
                border-radius: 8px;
            }}
            QPushButton#CTIconButton:hover {{
                background: rgba(255, 255, 255, 0.09);
                border-color: rgba(182, 196, 255, 0.30);
            }}

            QFrame#CTFilterBar {{
                background: {C.GLASS};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}
            QFrame#CTOfferRow {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}
            QFrame#CTOfferRow:hover {{
                border-color: rgba(182, 196, 255, 0.28);
            }}
            QLabel#CTOfferTitle {{
                color: {C.TEXT};
                font-size: 14px;
                font-weight: 650;
                background: transparent;
            }}
            QLabel#CTOfferSubtitle {{
                color: {C.TEXT_MED};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#CTOfferPrice {{
                color: {C.PRI};
                font-size: 16px;
                font-weight: 750;
                background: transparent;
                min-width: 80px;
            }}
            QFrame#CTTotalsBar {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}

            QComboBox#CTCombo {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 7px;
                padding: 4px 10px;
                font-size: 12px;
                min-width: 90px;
            }}
            QComboBox#CTCombo::drop-down {{ border: none; width: 20px; }}
            QComboBox#CTCombo QAbstractItemView {{
                background: {C.PANEL2};
                color: {C.TEXT};
                selection-background-color: rgba(94, 130, 255, 0.28);
                border: 1px solid {C.BORDER_A};
            }}
            QCheckBox#CTCheck {{
                color: {C.TEXT_DIM};
                font-size: 12px;
                background: transparent;
            }}

            QTextEdit#CTDeckText {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
                padding: 10px;
                font-family: {FONT_MONO};
                font-size: 12px;
            }}
            QTextEdit#CTDeckText:focus {{
                border-color: rgba(182, 196, 255, 0.40);
            }}

            QTableWidget#CTTable {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
                gridline-color: {C.BORDER_A};
                font-size: 12px;
            }}
            QTableWidget#CTTable::item {{
                padding: 6px;
            }}
            QTableWidget#CTTable::item:selected {{
                background: rgba(94, 130, 255, 0.18);
            }}
            QHeaderView::section {{
                background: {C.PANEL2};
                color: {C.TEXT_MED};
                border: none;
                border-bottom: 1px solid {C.BORDER_A};
                padding: 6px;
                font-size: 11px;
                font-weight: 700;
            }}

            QScrollArea#CTScroll {{
                background: transparent;
                border: none;
            }}

            QProgressBar#CTProgress {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER_A};
                border-radius: 9px;
                color: {C.TEXT_DIM};
                font-size: 11px;
                text-align: center;
            }}
            QProgressBar#CTProgress::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C.PRI_DIM}, stop:1 {C.ACC2});
                border-radius: 8px;
            }}
        """ + _scrollbar_qss()

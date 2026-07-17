#cardtrader.py
"""Jarvis-facing tools for CardTrader: search, deck pricing, cart.

Follows the project convention (see flight_finder.py): sync functions
def tool(parameters: dict | None = None, speak=None) -> str, dispatched
by main.py via run_in_executor. Purchase is intentionally NOT exposed —
the cart is prepared, final checkout happens on the CardTrader site/app.
"""
from __future__ import annotations

import json

from actions import event_bus
from actions.cardtrader_api import CardTraderClient, AuthError, CardTraderError
from actions.cardtrader_catalog import ensure_catalog, catalog_status, full_resync, refresh_new_expansions
from actions.cardtrader_optimizer import (
    OfferFilters, Offer, OfferResult, DeckQuote, best_offer, compare_versions, quote_deck,
    FAST_SEARCH_CAP,
)
from actions.cardtrader_catalog import find_blueprints
from actions.deck_parser import parse_deck_text
from actions.paths import config_path

_LAST_QUOTE_PATH = config_path("cardtrader_last_quote.json")
_last_quote: DeckQuote | None = None


def _fmt_price(cents: int, currency: str) -> str:
    symbol = {"EUR": "€", "USD": "$"}.get(currency, currency + " ")
    amount = cents / 100
    return f"{amount:.2f}{symbol}" if currency == "EUR" else f"{symbol}{amount:.2f}"


def _fmt_offer(o: Offer) -> str:
    zero = "CT Zero" + (" 1 dia" if o.one_day_ready else "") if o.can_sell_via_hub else "venta directa"
    foil = " foil" if o.foil else ""
    return (
        f"{_fmt_price(o.price_cents, o.price_currency)} "
        f"({o.expansion_code.upper()}, {o.condition}, {o.language}{foil}, {zero}, vendedor {o.seller_username})"
    )


def _ensure_catalog_ready() -> str | None:
    """Returns a spoken status message if a sync just happened, else None."""
    status = catalog_status()
    if status["expansions"] == 0 or status["pending_expansions"] > 0:
        event_bus.log("CardTrader", "Sincronizando catalogo local...")
        result = ensure_catalog()
        return (
            f"He sincronizado el catalogo de CardTrader ({result.get('blueprints_synced', 0)} cartas "
            f"nuevas en {result.get('seconds', 0)}s). "
        )
    return None


def cardtrader_search_card(parameters: dict | None = None, speak=None) -> str:
    params = parameters or {}
    name = str(params.get("name", "")).strip()
    if not name:
        return "Necesito el nombre de la carta para buscarla."

    try:
        prefix = _ensure_catalog_ready() or ""

        fast = bool(params.get("fast", False))
        filters = OfferFilters(
            zero_only=bool(params.get("zero_only", True)),
            foil=params.get("foil"),
            languages=[params["language"]] if params.get("language") else None,
            max_printings=FAST_SEARCH_CAP if fast else None,  # default: check every printing
        )
        set_code = params.get("set_code")
        all_versions = bool(params.get("all_versions", False))

        client = CardTraderClient()

        if all_versions:
            summaries = compare_versions(name, filters, client=client)
            summaries = [s for s in summaries if s.best_offer is not None]
            if not summaries:
                return f"{prefix}No encontre ofertas de '{name}' que cumplan los filtros."
            lines = [f"{prefix}Versiones de '{name}' ordenadas por precio:"]
            for s in summaries[:10]:
                lines.append(f"- {s.expansion_name} ({s.expansion_code.upper()}): {_fmt_offer(s.best_offer)}")
            event_bus.log("CardTrader", f"compare_versions({name}) -> {len(summaries)} versiones")
            return "\n".join(lines)

        blueprints = find_blueprints(name, set_code=set_code)
        if not blueprints:
            return f"{prefix}No encontre '{name}' en el catalogo de CardTrader."

        result = best_offer(blueprints, qty=1, filters=filters, client=client)
        if not result or result.covered_qty == 0:
            return f"{prefix}No hay ofertas de '{name}' que cumplan los filtros pedidos."

        offer, _ = result.offers_used[0]
        lines = [f"{prefix}{name} — mejor precio: {_fmt_offer(offer)}."]
        if result.runner_ups:
            lines.append("Otras opciones: " + "; ".join(_fmt_offer(o) for o in result.runner_ups))
        event_bus.log("CardTrader", f"search_card({name}) -> {offer.product_id}")
        return "\n".join(lines)

    except AuthError as e:
        return str(e)
    except CardTraderError as e:
        event_bus.error("CardTrader", str(e))
        return f"Error consultando CardTrader: {e}"


def cardtrader_quote_deck(parameters: dict | None = None, speak=None) -> str:
    global _last_quote
    params = parameters or {}
    deck_text = str(params.get("deck_text", "")).strip()
    if not deck_text:
        return "Necesito el texto del mazo para cotizarlo."

    try:
        prefix = _ensure_catalog_ready() or ""

        parsed = parse_deck_text(deck_text)
        if not parsed.entries:
            return f"{prefix}No pude interpretar ninguna linea del mazo."

        filters = OfferFilters(
            zero_only=bool(params.get("zero_only", True)),
            min_condition=str(params.get("min_condition", "Moderately Played")),
            languages=[params["language"]] if params.get("language") else None,
        )

        quote = quote_deck(
            parsed.entries, filters,
            respect_printings=bool(params.get("respect_printings", False)),
        )
        _last_quote = quote
        _save_last_quote(quote)

        resolved = [it for it in quote.items if it.result]
        unresolved = [it for it in quote.items if not it.result]

        totals_str = ", ".join(_fmt_price(c, cur) for cur, c in quote.total_by_currency.items())
        lines = [
            f"{prefix}Cotizacion del mazo: {len(resolved)} de {len(quote.items)} cartas resueltas.",
            f"Total: {totals_str or '0'}.",
        ]
        if unresolved:
            names = ", ".join(it.entry.name for it in unresolved[:8])
            more = "..." if len(unresolved) > 8 else ""
            lines.append(f"No resueltas ({len(unresolved)}): {names}{more}.")
        if parsed.unparsed:
            lines.append(f"{len(parsed.unparsed)} lineas del texto no se pudieron interpretar.")

        expensive = sorted(
            (it for it in resolved),
            key=lambda it: max(o.price_cents * q for o, q in it.result.offers_used),
            reverse=True,
        )[:3]
        if expensive:
            lines.append("Mas caras: " + "; ".join(
                f"{it.entry.name} ({_fmt_price(sum(o.price_cents*q for o,q in it.result.offers_used), it.result.offers_used[0][0].price_currency)})"
                for it in expensive
            ))

        event_bus.log("CardTrader", f"quote_deck: {len(resolved)}/{len(quote.items)} resueltas, total {totals_str}")
        return "\n".join(lines)

    except AuthError as e:
        return str(e)
    except CardTraderError as e:
        event_bus.error("CardTrader", str(e))
        return f"Error consultando CardTrader: {e}"


def _save_last_quote(quote: DeckQuote) -> None:
    try:
        data = {
            "items": [
                {
                    "name": it.entry.name,
                    "qty": it.entry.qty,
                    "found": it.result is not None,
                    "products": (
                        [{"product_id": o.product_id, "qty": q, "price_cents": o.price_cents,
                          "currency": o.price_currency, "can_sell_via_hub": o.can_sell_via_hub}
                         for o, q in it.result.offers_used]
                        if it.result else []
                    ),
                }
                for it in quote.items
            ],
        }
        with open(_LAST_QUOTE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        event_bus.log("CardTrader", f"No se pudo guardar la ultima cotizacion: {e}")


def _load_last_quote_raw() -> dict | None:
    try:
        with open(_LAST_QUOTE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cardtrader_add_to_cart(parameters: dict | None = None, speak=None) -> str:
    params = parameters or {}
    scope = str(params.get("scope", "")).strip().lower()
    quantity = int(params.get("quantity", 1) or 1)

    try:
        client = CardTraderClient()

        if scope == "product":
            product_id = params.get("product_id")
            if not product_id:
                return "Necesito el product_id para anadir un producto concreto."
            client.cart_add(int(product_id), quantity, via_zero=True)
            event_bus.log("CardTrader", f"cart_add product {product_id} x{quantity}")
            return f"Anadido al carrito: producto {product_id} x{quantity}."

        if scope == "last_quote":
            data = _load_last_quote_raw()
            if not data or not data.get("items"):
                return "No tengo ninguna cotizacion reciente guardada. Cotiza el mazo primero."

            card_name = str(params.get("card_name", "")).strip().lower()
            items = data["items"]
            if card_name:
                items = [it for it in items if card_name in it["name"].lower()]
                if not items:
                    return f"No encontre '{card_name}' en la ultima cotizacion."

            added, failed = 0, []
            for it in items:
                if not it["found"]:
                    continue
                for prod in it["products"]:
                    try:
                        client.cart_add(prod["product_id"], prod["qty"], via_zero=prod["can_sell_via_hub"])
                        added += prod["qty"]
                    except CardTraderError:
                        failed.append(it["name"])

            msg = f"Anadidas {added} cartas al carrito via CT Zero."
            if failed:
                msg += f" Fallaron (agotadas o error): {', '.join(failed[:5])}."
            event_bus.log("CardTrader", msg)
            return msg

        return "scope debe ser 'last_quote' o 'product'."

    except AuthError as e:
        return str(e)
    except CardTraderError as e:
        event_bus.error("CardTrader", str(e))
        return f"Error anadiendo al carrito: {e}"


def cardtrader_cart(parameters: dict | None = None, speak=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "view")).strip().lower()

    try:
        client = CardTraderClient()

        if action == "view":
            cart = client.cart()
            total = cart.get("total", {})
            lines = [f"Carrito CardTrader: {_fmt_price(total.get('cents', 0), total.get('currency', 'EUR'))} total."]
            for sub in cart.get("subcarts", []):
                seller = sub.get("seller", {}).get("username", "?")
                n_items = len(sub.get("cart_items", []))
                lines.append(f"- {seller}: {n_items} articulos, envio {_fmt_price(sub.get('shipping_cost', {}).get('cents', 0), sub.get('shipping_cost', {}).get('currency', 'EUR'))}")
            fee = cart.get("ct_zero_fee_amount", {})
            if fee.get("cents"):
                lines.append(f"Fee CT Zero: {_fmt_price(fee['cents'], fee.get('currency', 'EUR'))}")
            event_bus.log("CardTrader", "cart view")
            return "\n".join(lines)

        if action == "remove":
            product_id = params.get("product_id")
            if not product_id:
                return "Necesito el product_id a quitar."
            quantity = int(params.get("quantity", 1) or 1)
            client.cart_remove(int(product_id), quantity)
            return f"Quitado del carrito: producto {product_id} x{quantity}."

        if action == "clear":
            cart = client.cart()
            removed = 0
            for sub in cart.get("subcarts", []):
                for item in sub.get("cart_items", []):
                    pid = item["product"]["id"]
                    qty = item["quantity"]
                    try:
                        client.cart_remove(pid, qty)
                        removed += 1
                    except CardTraderError:
                        pass
            return f"Carrito vaciado: {removed} lineas eliminadas."

        return "action debe ser view, remove o clear."

    except AuthError as e:
        return str(e)
    except CardTraderError as e:
        event_bus.error("CardTrader", str(e))
        return f"Error con el carrito: {e}"


def cardtrader_catalog(parameters: dict | None = None, speak=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "status")).strip().lower()

    try:
        if action == "status":
            s = catalog_status()
            return (
                f"Catalogo CardTrader: {s['expansions']} expansiones, {s['blueprints']} cartas indexadas, "
                f"{s['pending_expansions']} expansiones pendientes de sincronizar."
            )
        if action == "sync":
            r = refresh_new_expansions()
            return f"Sincronizadas {r['expansions_synced']} expansiones, {r['blueprints_synced']} cartas, en {r['seconds']}s."
        if action == "full_resync":
            r = full_resync()
            return f"Catalogo resincronizado desde cero: {r['expansions_synced']} expansiones, {r['blueprints_synced']} cartas, en {r['seconds']}s."
        return "action debe ser status, sync o full_resync."
    except AuthError as e:
        return str(e)
    except CardTraderError as e:
        event_bus.error("CardTrader", str(e))
        return f"Error gestionando el catalogo: {e}"

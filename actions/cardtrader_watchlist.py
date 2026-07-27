"""Price watchlist for CardTrader cards — "avísame si sube de precio".

Persists a flat JSON {card_name: {price_cents, currency, checked}} and, on
demand (wired into the morning briefing), re-checks each watched card's best
offer and reports ones that moved since the last check. No background
polling — CardTrader offers don't change fast enough to need it, and it
keeps this from adding another always-on thread.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from actions.cardtrader_api import CardTraderClient, AuthError, CardTraderError
from actions.cardtrader_catalog import find_blueprints, ensure_catalog, catalog_status
from actions.cardtrader_optimizer import OfferFilters, best_offer
from actions.paths import config_path

_WATCHLIST_PATH = config_path("cardtrader_watchlist.json")


def _load() -> dict[str, Any]:
    try:
        return json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _current_price(name: str) -> tuple[int, str] | None:
    """Best CT Zero offer price for `name`, or None if unavailable."""
    blueprints = find_blueprints(name)
    if not blueprints:
        return None
    result = best_offer(blueprints, qty=1, filters=OfferFilters(zero_only=True), client=CardTraderClient())
    if not result or result.covered_qty == 0:
        return None
    offer, _ = result.offers_used[0]
    return offer.price_cents, offer.price_currency


def watchlist_add(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return "Necesito el nombre de la carta para vigilarla."
    watchlist = _load()
    key = name.lower()
    if key in watchlist:
        return f"'{name}' ya está en la lista de vigilancia."
    price = _current_price(name)
    watchlist[key] = {
        "name": name,
        "price_cents": price[0] if price else None,
        "currency": price[1] if price else "EUR",
        "checked": datetime.now().strftime("%Y-%m-%d"),
    }
    _save(watchlist)
    if price:
        cents, currency = price
        return f"Vigilando '{name}' — precio actual: {cents / 100:.2f}{currency}."
    return f"Vigilando '{name}' — no encontré oferta ahora mismo, lo comprobaré más adelante."


def watchlist_remove(name: str) -> str:
    watchlist = _load()
    key = str(name or "").strip().lower()
    if key not in watchlist:
        return f"'{name}' no estaba en la lista de vigilancia."
    del watchlist[key]
    _save(watchlist)
    return f"'{name}' quitada de la lista de vigilancia."


def watchlist_list() -> list[dict]:
    return list(_load().values())


def check_price_changes(min_change_pct: float = 3.0) -> list[dict]:
    """Re-check every watched card; return the ones whose price moved by at
    least min_change_pct since the last check. Updates the stored price for
    all of them (moved or not) so the next check compares against this run."""
    watchlist = _load()
    if not watchlist:
        return []

    try:
        status = catalog_status()
        if status["expansions"] == 0 or status["pending_expansions"] > 0:
            ensure_catalog()
    except (AuthError, CardTraderError):
        return []

    changes = []
    for key, entry in watchlist.items():
        name = entry.get("name", key)
        try:
            price = _current_price(name)
        except (AuthError, CardTraderError):
            continue
        if not price:
            continue
        new_cents, currency = price
        old_cents = entry.get("price_cents")
        entry["price_cents"] = new_cents
        entry["currency"] = currency
        entry["checked"] = datetime.now().strftime("%Y-%m-%d")
        if old_cents:
            pct = (new_cents - old_cents) / old_cents * 100
            if abs(pct) >= min_change_pct:
                changes.append({
                    "name": name,
                    "old_cents": old_cents,
                    "new_cents": new_cents,
                    "currency": currency,
                    "pct": round(pct, 1),
                })
    _save(watchlist)
    return changes

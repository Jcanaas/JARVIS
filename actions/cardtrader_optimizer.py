#cardtrader_optimizer.py
"""Offer selection and deck pricing on top of CardTrader marketplace data.

Field names below match the *real* API response (verified against a live
token, not just the docs): product.price_cents/price_currency,
product.properties_hash.{condition,mtg_language,mtg_foil,collector_number},
product.user.{can_sell_via_hub,one_day_ready,max_sellable_in24h_quantity},
product.on_vacation, product.quantity.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from actions.cardtrader_api import CardTraderClient
from actions.cardtrader_catalog import Blueprint, find_blueprints
from actions.deck_parser import DeckEntry

_CONDITION_RANK = {
    "Mint": 6, "Near Mint": 5, "Slightly Played": 4,
    "Moderately Played": 3, "Played": 2, "Heavily Played": 1, "Poor": 0,
}

# Max distinct printings queried per card name when the caller doesn't
# restrict to a single set (keeps marketplace calls bounded at ~1 req/s).
DEFAULT_MAX_PRINTINGS = 5

# Fast single-card search: check a generous chunk of printings instead of
# every last reprint (a staple can have 100+). Cuts a worst-case ~127s scan
# down to ~25s while still catching virtually every cheap reprint. Full
# certainty (guaranteed global minimum) is one flag away: max_printings=None.
FAST_SEARCH_CAP = 25


@dataclass
class OfferFilters:
    zero_only: bool = True
    min_condition: str = "Moderately Played"
    languages: list[str] | None = None
    foil: bool | None = None
    prefer_one_day: bool = True
    # None = check every printing of the card (real optimization: the
    # single-card search does this). Deck quoting caps this to bound the
    # number of ~1 req/s marketplace calls across dozens of cards.
    max_printings: int | None = DEFAULT_MAX_PRINTINGS


@dataclass
class Offer:
    product_id: int
    blueprint_id: int
    name: str
    price_cents: int
    price_currency: str
    condition: str
    language: str
    foil: bool
    quantity_available: int
    seller_username: str
    can_sell_via_hub: bool
    one_day_ready: bool
    expansion_code: str
    expansion_name: str


@dataclass
class VersionSummary:
    blueprint_id: int
    expansion_code: str
    expansion_name: str
    best_offer: Offer | None
    offers_count: int


@dataclass
class OfferResult:
    covered_qty: int
    requested_qty: int
    offers_used: list[tuple[Offer, int]]  # (offer, qty taken from it)
    runner_ups: list[Offer]
    total_by_currency: dict[str, int]
    warning: str | None = None


@dataclass
class QuoteItem:
    entry: DeckEntry
    result: OfferResult | None
    reason: str | None = None  # set when not found / no offer


@dataclass
class DeckQuote:
    items: list[QuoteItem] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    total_by_currency: dict[str, int] = field(default_factory=dict)


def _min_condition_rank(min_condition: str) -> int:
    return _CONDITION_RANK.get(min_condition, 0)


def _product_to_offer(p: dict, blueprint: Blueprint) -> Offer:
    props = p.get("properties_hash", {}) or {}
    user = p.get("user", {}) or {}
    return Offer(
        product_id=p["id"],
        blueprint_id=p["blueprint_id"],
        name=p.get("name_en", blueprint.name),
        price_cents=p.get("price_cents", (p.get("price") or {}).get("cents", 0)),
        price_currency=p.get("price_currency", (p.get("price") or {}).get("currency", "EUR")),
        condition=props.get("condition", "Unknown"),
        language=props.get("mtg_language", "en"),
        foil=bool(props.get("mtg_foil", False)),
        quantity_available=p.get("quantity", 1),
        seller_username=user.get("username", ""),
        can_sell_via_hub=bool(user.get("can_sell_via_hub", False)),
        one_day_ready=bool(user.get("one_day_ready", False)),
        expansion_code=blueprint.expansion_code,
        expansion_name=blueprint.expansion_name,
    )


def _fetch_offers(
    client: CardTraderClient, blueprints: list[Blueprint], filters: OfferFilters,
    progress_cb=None,
) -> list[Offer]:
    """progress_cb(i, total, best_offer_so_far) fires after each printing is
    checked, so a slow full scan (many reprints = many 1 req/s calls) can be
    shown live instead of looking frozen."""
    offers: list[Offer] = []
    scoped = blueprints[: filters.max_printings]
    total = len(scoped)
    for i, bp in enumerate(scoped, start=1):
        products = client.marketplace_by_blueprint(
            bp.id,
            foil=filters.foil,
            language=filters.languages[0] if filters.languages and len(filters.languages) == 1 else None,
        )
        for p in products:
            if p.get("on_vacation") or p.get("graded"):
                continue
            offer = _product_to_offer(p, bp)
            if filters.zero_only and not offer.can_sell_via_hub:
                continue
            if _CONDITION_RANK.get(offer.condition, -1) < _min_condition_rank(filters.min_condition):
                continue
            if filters.languages and offer.language not in filters.languages:
                continue
            if filters.foil is not None and offer.foil != filters.foil:
                continue
            offers.append(offer)
        if progress_cb:
            best_so_far = min(offers, key=lambda o: _sort_key(o, filters.prefer_one_day)) if offers else None
            progress_cb(i, total, best_so_far)
    return offers


def _sort_key(offer: Offer, prefer_one_day: bool):
    return (
        not (prefer_one_day and offer.one_day_ready),  # False sorts first
        offer.price_cents,
        -_CONDITION_RANK.get(offer.condition, 0),
    )


def best_offer(
    blueprints: list[Blueprint], qty: int, filters: OfferFilters,
    client: CardTraderClient | None = None,
    progress_cb=None,
) -> OfferResult | None:
    if not blueprints:
        return None
    client = client or CardTraderClient()
    offers = _fetch_offers(client, blueprints, filters, progress_cb=progress_cb)
    if not offers:
        return None

    offers.sort(key=lambda o: _sort_key(o, filters.prefer_one_day))

    remaining = qty
    used: list[tuple[Offer, int]] = []
    totals: dict[str, int] = {}
    for offer in offers:
        if remaining <= 0:
            break
        take = min(remaining, offer.quantity_available)
        if take <= 0:
            continue
        used.append((offer, take))
        totals[offer.price_currency] = totals.get(offer.price_currency, 0) + offer.price_cents * take
        remaining -= take

    warning = None
    if remaining > 0:
        warning = f"Solo se cubrieron {qty - remaining} de {qty} copias con los filtros actuales."

    runner_ups = [o for o in offers if o not in [u[0] for u in used]][:3]

    return OfferResult(
        covered_qty=qty - remaining,
        requested_qty=qty,
        offers_used=used,
        runner_ups=runner_ups,
        total_by_currency=totals,
        warning=warning,
    )


def compare_versions(
    name: str, filters: OfferFilters, client: CardTraderClient | None = None,
    progress_cb=None,
) -> list[VersionSummary]:
    client = client or CardTraderClient()
    blueprints = find_blueprints(name)
    summaries: list[VersionSummary] = []
    total = len(blueprints)
    for i, bp in enumerate(blueprints, start=1):
        offers = _fetch_offers(client, [bp], filters)
        offers.sort(key=lambda o: _sort_key(o, filters.prefer_one_day))
        if progress_cb:
            best = offers[0] if offers else None
            progress_cb(i, total, best)
        summaries.append(VersionSummary(
            blueprint_id=bp.id,
            expansion_code=bp.expansion_code,
            expansion_name=bp.expansion_name,
            best_offer=offers[0] if offers else None,
            offers_count=len(offers),
        ))
    summaries.sort(key=lambda s: s.best_offer.price_cents if s.best_offer else float("inf"))
    return summaries


def quote_deck(
    entries: list[DeckEntry], filters: OfferFilters,
    respect_printings: bool = False,
    progress_cb=None,
    item_cb=None,
) -> DeckQuote:
    """item_cb(item: QuoteItem, index: int, total: int) fires right after
    each card is resolved, so callers (e.g. the UI) can render results
    incrementally instead of waiting for the whole deck."""
    client = CardTraderClient()
    quote = DeckQuote()
    total = len(entries)

    for i, entry in enumerate(entries, start=1):
        if progress_cb:
            progress_cb(i, total, entry.name)

        set_code = entry.set_code if respect_printings else None
        blueprints = find_blueprints(entry.name, set_code=set_code)
        if not blueprints:
            quote.not_found.append(entry.raw_line)
            item = QuoteItem(entry=entry, result=None, reason="carta no encontrada en el catalogo")
            quote.items.append(item)
            if item_cb:
                item_cb(item, i, total)
            continue

        item_filters = OfferFilters(
            zero_only=filters.zero_only,
            min_condition=filters.min_condition,
            languages=filters.languages,
            foil=entry.foil or filters.foil,
            prefer_one_day=filters.prefer_one_day,
            max_printings=1 if set_code else filters.max_printings,
        )
        result = best_offer(blueprints, entry.qty, item_filters, client=client)
        if not result or result.covered_qty == 0:
            item = QuoteItem(entry=entry, result=None, reason="sin stock que cumpla los filtros")
            quote.items.append(item)
            if item_cb:
                item_cb(item, i, total)
            continue

        item = QuoteItem(entry=entry, result=result)
        quote.items.append(item)
        for currency, cents in result.total_by_currency.items():
            quote.total_by_currency[currency] = quote.total_by_currency.get(currency, 0) + cents
        if item_cb:
            item_cb(item, i, total)

    return quote

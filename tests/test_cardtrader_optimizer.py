import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions.cardtrader_catalog import Blueprint
from actions.cardtrader_optimizer import OfferFilters, best_offer, _product_to_offer, _fetch_offers


def _bp(id=1, name="Test Card", expansion_code="tst", expansion_name="Test Set"):
    return Blueprint(
        id=id, name=name, version=None, expansion_id=1,
        expansion_code=expansion_code, expansion_name=expansion_name,
        scryfall_id=None, collector_number="1",
    )


def _product(
    id, blueprint_id=1, price_cents=100, currency="EUR", qty=1,
    condition="Near Mint", language="en", foil=False,
    can_sell_via_hub=True, one_day_ready=False, on_vacation=False, graded=False,
    username="seller",
):
    return {
        "id": id,
        "blueprint_id": blueprint_id,
        "name_en": "Test Card",
        "price_cents": price_cents,
        "price_currency": currency,
        "quantity": qty,
        "on_vacation": on_vacation,
        "graded": graded,
        "properties_hash": {
            "condition": condition,
            "mtg_language": language,
            "mtg_foil": foil,
        },
        "user": {
            "username": username,
            "can_sell_via_hub": can_sell_via_hub,
            "one_day_ready": one_day_ready,
            "max_sellable_in24h_quantity": None,
        },
    }


def _mock_client(products_by_blueprint: dict[int, list[dict]]):
    client = MagicMock()
    client.marketplace_by_blueprint.side_effect = lambda bp_id, **kw: products_by_blueprint.get(bp_id, [])
    return client


def test_picks_cheapest_offer():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=500),
        _product(2, price_cents=200),
        _product(3, price_cents=300),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(), client=client)
    assert result.covered_qty == 1
    offer, qty = result.offers_used[0]
    assert offer.price_cents == 200
    assert qty == 1


def test_zero_only_filters_non_hub_sellers():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=100, can_sell_via_hub=False),
        _product(2, price_cents=200, can_sell_via_hub=True),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(zero_only=True), client=client)
    offer, _ = result.offers_used[0]
    assert offer.price_cents == 200


def test_zero_only_false_allows_direct_sellers():
    bp = _bp()
    products = {1: [_product(1, price_cents=100, can_sell_via_hub=False)]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(zero_only=False), client=client)
    assert result.covered_qty == 1


def test_min_condition_excludes_worse_condition():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=50, condition="Heavily Played"),
        _product(2, price_cents=300, condition="Near Mint"),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(min_condition="Moderately Played"), client=client)
    offer, _ = result.offers_used[0]
    assert offer.price_cents == 300


def test_prefer_one_day_ranks_before_cheaper_non_one_day():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=100, one_day_ready=False),
        _product(2, price_cents=150, one_day_ready=True),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(prefer_one_day=True), client=client)
    offer, _ = result.offers_used[0]
    assert offer.one_day_ready is True
    assert offer.price_cents == 150


def test_covers_quantity_across_multiple_sellers():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=100, qty=2),
        _product(2, price_cents=150, qty=3),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=4, filters=OfferFilters(), client=client)
    assert result.covered_qty == 4
    assert len(result.offers_used) == 2
    assert result.offers_used[0][1] == 2  # cheapest exhausted first
    assert result.offers_used[1][1] == 2  # remaining 2 from second seller


def test_insufficient_stock_sets_warning():
    bp = _bp()
    products = {1: [_product(1, price_cents=100, qty=1)]}
    client = _mock_client(products)
    result = best_offer([bp], qty=5, filters=OfferFilters(), client=client)
    assert result.covered_qty == 1
    assert result.warning is not None


def test_totals_grouped_by_currency():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=100, currency="EUR", qty=1),
        _product(2, price_cents=200, currency="USD", qty=1),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=2, filters=OfferFilters(), client=client)
    assert result.total_by_currency == {"EUR": 100, "USD": 200}


def test_on_vacation_sellers_excluded():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=50, on_vacation=True),
        _product(2, price_cents=300, on_vacation=False),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(), client=client)
    offer, _ = result.offers_used[0]
    assert offer.price_cents == 300


def test_graded_products_excluded():
    bp = _bp()
    products = {1: [
        _product(1, price_cents=50, graded=True),
        _product(2, price_cents=300, graded=False),
    ]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(), client=client)
    offer, _ = result.offers_used[0]
    assert offer.price_cents == 300


def test_no_offers_returns_none():
    bp = _bp()
    client = _mock_client({1: []})
    result = best_offer([bp], qty=1, filters=OfferFilters(), client=client)
    assert result is None


def test_runner_ups_present():
    bp = _bp()
    products = {1: [_product(i, price_cents=100 + i * 10) for i in range(5)]}
    client = _mock_client(products)
    result = best_offer([bp], qty=1, filters=OfferFilters(), client=client)
    assert len(result.runner_ups) == 3
    assert result.runner_ups[0].price_cents < result.runner_ups[1].price_cents


if __name__ == "__main__":
    import inspect
    mod = sys.modules[__name__]
    tests = [f for name, f in inspect.getmembers(mod) if name.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

#manual_ct_probe.py — Fase 0: sonda manual contra la API real de CardTrader.
"""Ejecutar suelto: py tests/manual_ct_probe.py
No commitear la salida si contiene datos personales (username, país...).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions.cardtrader_api import CardTraderClient  # noqa: E402


def dump(label: str, obj) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:4000])


def main() -> None:
    client = CardTraderClient()

    info = client.info()
    dump("GET /info", info)

    games = client.games()
    dump("GET /games", games)
    mtg = [g for g in games if "magic" in str(g.get("name", "")).lower()]
    dump("MTG game entries", mtg)

    expansions = client.expansions()
    print(f"\nTotal expansions: {len(expansions)}")
    mtg_id = mtg[0]["id"] if mtg else 1
    mtg_expansions = [e for e in expansions if e.get("game_id") == mtg_id]
    print(f"MTG expansions (game_id={mtg_id}): {len(mtg_expansions)}")
    dump("First 3 MTG expansions", mtg_expansions[:3])

    if mtg_expansions:
        exp = mtg_expansions[0]
        blueprints = client.blueprints_export(exp["id"])
        dump(f"blueprints_export({exp['id']} - {exp.get('name')})", blueprints[:3])
        print(f"Blueprint count for this expansion: {len(blueprints)}")

        if blueprints:
            bp_id = blueprints[0]["id"]
            products = client.marketplace_by_blueprint(bp_id)
            dump(f"marketplace_by_blueprint({bp_id})", products[:3])
            if products:
                print("\nKeys in first product:", sorted(products[0].keys()))
                print("Keys in product.user:", sorted(products[0].get("user", {}).keys()))

    # Rate limit probe: 15 calls back to back against marketplace endpoint.
    if mtg_expansions and blueprints:
        print("\n=== Rate limit probe (marketplace) ===")
        t0 = time.monotonic()
        for i in range(15):
            client.marketplace_by_blueprint(blueprints[0]["id"])
            print(f"call {i+1} at {time.monotonic()-t0:.2f}s")
        print(f"Total: {time.monotonic()-t0:.2f}s for 15 calls")

    # Cart cycle with a cheap product (manual: replace product_id below).
    cart = client.cart()
    dump("GET /cart (initial)", cart)

    print("\nDone. Cart add/remove skipped — run manually with a real cheap product_id if needed.")


if __name__ == "__main__":
    main()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions.deck_parser import parse_deck_text


def test_basic_quantity_and_name():
    r = parse_deck_text("4 Lightning Bolt")
    assert len(r.entries) == 1
    e = r.entries[0]
    assert e.qty == 4
    assert e.name == "Lightning Bolt"
    assert e.set_code is None
    assert not e.foil
    assert not e.etched


def test_x_suffix_quantity():
    r = parse_deck_text("4x Lightning Bolt")
    assert r.entries[0].qty == 4
    assert r.entries[0].name == "Lightning Bolt"


def test_set_and_collector_number():
    r = parse_deck_text("1 Arcane Signet (CMR) 297")
    e = r.entries[0]
    assert e.name == "Arcane Signet"
    assert e.set_code == "CMR"
    assert e.collector_number == "297"


def test_foil_marker():
    r = parse_deck_text("1 Sol Ring (C21) 263 *F*")
    e = r.entries[0]
    assert e.name == "Sol Ring"
    assert e.set_code == "C21"
    assert e.collector_number == "263"
    assert e.foil
    assert not e.etched


def test_etched_marker():
    r = parse_deck_text("1 Sol Ring (C21) 263 *E*")
    assert r.entries[0].etched
    assert not r.entries[0].foil


def test_split_card_name_preserved():
    r = parse_deck_text("1 Fire // Ice (MH2) 290")
    e = r.entries[0]
    assert e.name == "Fire // Ice"
    assert e.set_code == "MH2"


def test_mdfc_name_preserved():
    r = parse_deck_text("1 Malakir Rebirth // Malakir Mire (ZNR) 111")
    assert r.entries[0].name == "Malakir Rebirth // Malakir Mire"


def test_section_headers_and_sideboard():
    text = "\n".join([
        "Deck",
        "4 Lightning Bolt",
        "SIDEBOARD:",
        "1 Alpine Moon (MH1) 235",
    ])
    r = parse_deck_text(text)
    assert len(r.entries) == 2
    assert r.entries[0].section == "deck"
    assert r.entries[1].section == "sideboard"


def test_empty_lines_ignored():
    r = parse_deck_text("4 Lightning Bolt\n\n\n1 Sol Ring")
    assert len(r.entries) == 2


def test_unparsed_lines_reported_not_dropped():
    r = parse_deck_text("this is not a valid deck line at all!!\n4 Lightning Bolt")
    assert len(r.entries) == 1
    assert len(r.unparsed) == 1
    assert "not a valid" in r.unparsed[0]


def test_commander_section():
    text = "Commander\n1 Atraxa, Praetors' Voice\nDeck\n4 Lightning Bolt"
    r = parse_deck_text(text)
    assert r.entries[0].section == "commander"
    assert r.entries[0].name == "Atraxa, Praetors' Voice"
    assert r.entries[1].section == "deck"


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
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

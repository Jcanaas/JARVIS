#deck_parser.py
"""Parser for Moxfield plain-text deck exports.

No network calls. Handles: quantity+name, optional (SET) collector#,
optional *F*/*E* foil/etched marker, split/MDFC card names with '//',
and section headers (Deck/Sideboard/Commander/Considering/...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINE_RE = re.compile(
    r"^(?P<qty>\d+)x?\s+"
    r"(?P<name>.+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)\s+(?P<collector>[\w\-★]+))?"
    r"(?:\s+\*(?P<mark>F|E)\*)?"
    r"\s*$"
)

_SECTION_RE = re.compile(
    r"^(deck|sideboard|commander|companion|considering|maybeboard)\s*:?\s*$",
    re.IGNORECASE,
)


@dataclass
class DeckEntry:
    qty: int
    name: str
    set_code: str | None
    collector_number: str | None
    foil: bool
    etched: bool
    section: str
    raw_line: str


@dataclass
class ParseResult:
    entries: list[DeckEntry] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)


def parse_deck_text(text: str) -> ParseResult:
    result = ParseResult()
    section = "deck"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1).lower()
            continue

        match = _LINE_RE.match(line)
        if not match:
            result.unparsed.append(raw_line)
            continue

        mark = match.group("mark")
        result.entries.append(DeckEntry(
            qty=int(match.group("qty")),
            name=match.group("name").strip(),
            set_code=match.group("set"),
            collector_number=match.group("collector"),
            foil=(mark == "F"),
            etched=(mark == "E"),
            section=section,
            raw_line=raw_line,
        ))

    return result

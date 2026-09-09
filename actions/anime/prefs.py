"""Anime user preferences.

Lifted out of the old actions/anime_search.py (a Jikan/MyAnimeList client whose
only surviving caller was this pair of functions — the catalog itself moved to
the Kitsu addon long ago). Same prefs.json key, so existing settings carry over
untouched.
"""
from __future__ import annotations

import json

from actions.paths import config_path

_PREFS_KEY = "anime_sfw"
_AUTOPLAY_KEY = "anime_autoplay"
_AUTOCOMPLETE_KEY = "anime_autocomplete"

_DEFAULTS = {
    _PREFS_KEY: True,
    _AUTOPLAY_KEY: True,
    _AUTOCOMPLETE_KEY: True,
}


def _read_all() -> dict:
    try:
        p = config_path("prefs.json")
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {}


def _write_key(key: str, value: bool) -> None:
    try:
        p = config_path("prefs.json")
        data = _read_all()
        data[key] = bool(value)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_sfw() -> bool:
    """True when adult/hentai content should be hidden (default: True)."""
    return bool(_read_all().get(_PREFS_KEY, _DEFAULTS[_PREFS_KEY]))


def set_sfw(value: bool) -> None:
    """Persist the SFW preference, leaving the rest of prefs.json alone."""
    _write_key(_PREFS_KEY, value)


def get_autoplay() -> bool:
    """True when the next episode should auto-play after a countdown."""
    return bool(_read_all().get(_AUTOPLAY_KEY, _DEFAULTS[_AUTOPLAY_KEY]))


def set_autoplay(value: bool) -> None:
    _write_key(_AUTOPLAY_KEY, value)


def get_autocomplete() -> bool:
    """True when watching past 85% of an episode marks it seen and syncs MAL."""
    return bool(_read_all().get(_AUTOCOMPLETE_KEY, _DEFAULTS[_AUTOCOMPLETE_KEY]))


def set_autocomplete(value: bool) -> None:
    _write_key(_AUTOCOMPLETE_KEY, value)

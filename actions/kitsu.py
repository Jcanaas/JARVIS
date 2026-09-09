"""Deprecated shim — the Kitsu anime catalog moved to actions.anime.catalog.

Kept so existing `from actions import kitsu` call sites keep working while they
are migrated. New code should import actions.anime.catalog directly.
"""
from __future__ import annotations

from actions.anime.catalog import (  # noqa: F401
    GENRES,
    KitsuError,
    get_airing_anime,
    get_anime_by_genre,
    get_episodes,
    get_meta,
    get_trending_anime,
    search_anime,
)

__all__ = [
    "GENRES",
    "KitsuError",
    "get_airing_anime",
    "get_anime_by_genre",
    "get_episodes",
    "get_meta",
    "get_trending_anime",
    "search_anime",
]

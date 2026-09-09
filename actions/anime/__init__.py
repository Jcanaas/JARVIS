"""Anime domain layer: catalog, id mappings, episode data and preferences.

Everything anime-specific lives here so the Qt panel (ui/panels/movies.py's
AnimeModePanel) and the web UI (actions/webui_server.py's /api/anime/*) consume
one implementation instead of the two divergent ones this package replaced —
the Qt side went through the Kitsu Stremio addon while the web side went
through TMDB→IMDb, which silently breaks for any anime with more than one
season. See NOTE_anime_shiru_plan.md, phase 0.

Module map:
    catalog.py   metadata + search + genre/trending/airing catalogs (Kitsu addon)
    mappings.py  cross-site ids and episode lists (api.ani.zip)
    cache.py     TTL disk cache shared by the two above
    prefs.py     user preferences (SFW filter)
"""

"""MyAnimeList API v2 wrapper — watchlist access and status updates.

Requires a logged-in session via actions.mal_auth.
"""
from __future__ import annotations

from typing import Optional

import requests

from actions.mal_auth import MALAuthError, get_access_token

_API = "https://api.myanimelist.net/v2"
_TIMEOUT = 12

STATUS_LABELS: dict[str, str] = {
    "watching": "Viendo",
    "completed": "Completado",
    "on_hold": "En pausa",
    "dropped": "Abandonado",
    "plan_to_watch": "Planificado",
}
STATUS_KEYS = list(STATUS_LABELS.keys())


class MALApiError(RuntimeError):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def get_watchlist(status: str = "watching", limit: int = 50) -> list[dict]:
    """Fetch the user's anime list filtered by status.

    Returns raw dicts with keys: mal_id, title, poster_url, rating,
    mal_status, mal_score, mal_watched, mal_total.
    """
    fields = "list_status,num_episodes,mean,main_picture"
    params = {
        "status": status, "limit": limit,
        "fields": fields, "sort": "list_updated_at",
    }
    try:
        r = requests.get(f"{_API}/users/@me/animelist",
                         headers=_headers(), params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise MALApiError(f"MAL watchlist request failed: {exc}") from exc

    results = []
    for item in data.get("data", []):
        node = item.get("node", {})
        ls = item.get("list_status", {})
        if not node:
            continue
        pic = node.get("main_picture", {})
        results.append({
            "mal_id": node.get("id", 0),
            "title": node.get("title", ""),
            "poster_url": pic.get("large") or pic.get("medium") or "",
            "rating": float(node.get("mean") or 0),
            "mal_status": ls.get("status", ""),
            "mal_score": int(ls.get("score") or 0),
            "mal_watched": int(ls.get("num_episodes_watched") or 0),
            "mal_total": int(node.get("num_episodes") or 0),
        })
    return results


# ---------------------------------------------------------------------------
# Single anime status
# ---------------------------------------------------------------------------

def get_anime_status(mal_id: int) -> dict:
    """Return the user's list_status for a specific anime (or {} if not listed)."""
    fields = "list_status,num_episodes"
    try:
        r = requests.get(f"{_API}/anime/{mal_id}",
                         headers=_headers(), params={"fields": fields},
                         timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return {
            "list_status": data.get("list_status", {}),
            "total_episodes": int(data.get("num_episodes") or 0),
        }
    except requests.RequestException:
        return {}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def update_status(mal_id: int, status: Optional[str] = None,
                  num_watched: Optional[int] = None,
                  score: Optional[int] = None) -> dict:
    """Update an anime entry in the user's list.

    Pass only the fields you want to change.  Returns the updated list_status.
    """
    payload = {}
    if status is not None:
        payload["status"] = status
    if num_watched is not None:
        payload["num_watched_episodes"] = str(num_watched)
    if score is not None:
        payload["score"] = str(score)
    if not payload:
        return {}

    try:
        r = requests.patch(f"{_API}/anime/{mal_id}/my_list_status",
                           headers=_headers(), data=payload, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise MALApiError(f"MAL update failed: {exc}") from exc

"""Real BPM + musical key for the "now playing" info panel.

A first version of this estimated both locally from the audio itself
(librosa beat-tracking + chroma/Krumhansl-Schmuckler key correlation). Real
testing showed the key guess in particular was unreliable enough to be
actively misleading — YouTube Music carries no audio-feature metadata, and
neither does Spotify anymore (its `audio-features` endpoint was locked down
for most third-party apps in late 2024), so instead of guessing this pulls
real catalog data from two free sources:

- BPM: Deezer's public search/track API — no key needed. Deezer stores a
  measured BPM on a large slice of its catalog; when a track has none the
  field is present but 0 ("not available", not literally zero).
- Key + time signature: GetSongBPM.com's API (https://api.getsong.co) —
  needs a free API key from https://getsongbpm.com/api, configured under
  Ajustes > API Keys ("GetSongBPM" / config/api_keys.json's
  getsongbpm_api_key). Their terms require attributing the data back to
  getsongbpm.com wherever it's shown — see NowPlayingPanel.svelte's credit
  line. Without a key configured, the key/time-signature fields are simply
  left empty instead of guessed.

Everything here is matched by title+artist text search against a third-party
catalog, so a wrong or missing match is expected sometimes — this is
best-effort enrichment, not authoritative metadata.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import requests

from actions.paths import config_path, memory_path

_CACHE_DIR = memory_path("audio_analysis")
_TIMEOUT = 8


def _cache_path(video_id: str) -> Path:
    safe = "".join(c for c in video_id if c.isalnum() or c in "-_")[:64] or "unknown"
    return _CACHE_DIR / f"{safe}.json"


def _cached(video_id: str) -> Optional[dict]:
    try:
        return json.loads(_cache_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _store(video_id: str, value: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(video_id).write_text(json.dumps(value), encoding="utf-8")
    except OSError:
        pass


def _get_api_key(name: str) -> str:
    try:
        cfg = config_path("api_keys.json")
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                return str(json.load(f).get(name, "") or "").strip()
    except Exception:
        pass
    return ""


def _deezer_bpm(title: str, artists: str) -> Optional[float]:
    q = f"{title} {artists}".strip()
    if not q:
        return None
    try:
        r = requests.get("https://api.deezer.com/search", params={"q": q}, timeout=_TIMEOUT)
        r.raise_for_status()
        results = (r.json() or {}).get("data") or []
        track_id = results[0].get("id") if results else None
        if not track_id:
            return None
        r2 = requests.get(f"https://api.deezer.com/track/{track_id}", timeout=_TIMEOUT)
        r2.raise_for_status()
        bpm = float((r2.json() or {}).get("bpm") or 0)
        return bpm if bpm > 0 else None
    except Exception:
        return None


def _getsongbpm_lookup(title: str, artists: str) -> tuple[bool, Optional[dict]]:
    """Returns (tried_cleanly, data).

    tried_cleanly is False for anything that should NOT be baked into the
    cache — no key configured, a network error, or the API rejecting the key
    (`{"error": "Invalid API Key, or inactive."}` — this is the actual shape
    a freshly-registered, not-yet-activated key returns). Those are worth
    retrying on the next play once the key starts working, not frozen into
    the disk cache forever the first time they're seen.

    data is None when the request went through fine but turned up no match —
    still `tried_cleanly = True`, since that's a real, stable answer.
    """
    api_key = _get_api_key("getsongbpm_api_key")
    if not api_key or not title:
        return False, None
    try:
        r = requests.get(
            "https://api.getsong.co/search/",
            params={
                "api_key": api_key,
                "type": "both",
                "lookup": f"song:{title} artist:{artists}".strip(),
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json() or {}
        if "error" in payload:
            # e.g. an inactive/unverified key — an account-level problem,
            # not "this track has no key data".
            return False, None
        results = payload.get("search") or []
        if not isinstance(results, list) or not results:
            return True, None
        top = results[0]
        # Field names per GetSongBPM's public docs at the time this was
        # written — checked defensively since this wasn't tested against a
        # live key. If these stop matching what the API actually returns,
        # that's the first place to look.
        key_of = str(top.get("key_of") or top.get("key") or "").strip()
        camelot = str(top.get("camelot") or "").strip()
        time_sig = str(top.get("time_sig") or top.get("time_signature") or "").strip()
        tempo = top.get("tempo") or top.get("bpm")
        if not key_of and not camelot:
            return True, None
        return True, {"key": key_of, "camelot": camelot, "time_sig": time_sig, "tempo": tempo}
    except Exception:
        return False, None


def analyze_track(video_id: str, title: str = "", artists: str = "", duration_s: float = 0) -> dict:
    """{"bpm": 116, "bpm_source": "deezer", "key": "Am", "camelot": "8A",
    "time_sig": "4/4", "key_source": "getsongbpm"} — any subset of these
    keys, whichever sources actually found a match. {} means nothing found
    anywhere; {"error": ...} means the request itself was malformed.

    Cached on disk once there's either real data to keep, or a clean pass
    across every source that's actually configured came back empty. A
    GetSongBPM key that exists but isn't working yet (see
    `_getsongbpm_lookup`) is deliberately excluded from that — otherwise the
    first play after registering a key would permanently cache a
    Deezer-only answer, and activating the key later would never enrich it.
    """
    if not video_id:
        return {"error": "missing_video_id"}
    cached = _cached(video_id)
    if cached is not None:
        return cached

    result: dict = {}

    bpm = _deezer_bpm(title, artists)
    if bpm:
        result["bpm"] = round(bpm)
        result["bpm_source"] = "deezer"

    gsb_tried, gsb = _getsongbpm_lookup(title, artists)
    if gsb:
        if gsb.get("key"):
            result["key"] = gsb["key"]
        if gsb.get("camelot"):
            result["camelot"] = gsb["camelot"]
        if gsb.get("time_sig"):
            result["time_sig"] = gsb["time_sig"]
        if result.get("key") or result.get("camelot"):
            result["key_source"] = "getsongbpm"
        if "bpm" not in result and gsb.get("tempo"):
            try:
                result["bpm"] = round(float(gsb["tempo"]))
                result["bpm_source"] = "getsongbpm"
            except (TypeError, ValueError):
                pass

    has_getsongbpm_key = bool(_get_api_key("getsongbpm_api_key"))
    if result or not has_getsongbpm_key or gsb_tried:
        _store(video_id, result)
    return result

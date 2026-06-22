from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


PARTIAL_EXTENSIONS = {".part", ".tmp", ".crdownload", ".ytdl"}


def _downloads_dir() -> Path:
    return Path.home() / "Downloads"


def _download_target(kind: str = "") -> Path:
    key = str(kind or "").strip().lower()
    base = _downloads_dir()
    mapping = {
        "audio": base / "JARVIS_Audio",
        "music": base / "JARVIS_Audio",
        "ytmusic": base / "JARVIS_Audio",
        "video": base / "JARVIS_Videos",
        "videos": base / "JARVIS_Videos",
        "youtube": base / "JARVIS_Videos",
        "downloads": base,
        "download": base,
        "all": base,
        "": base,
    }
    return mapping.get(key, Path(kind).expanduser())


def _is_safe_download_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = _downloads_dir().resolve()
        return resolved == root or resolved.is_relative_to(root)
    except Exception:
        return False


def download_open_folder(kind: str = "downloads") -> str:
    target = _download_target(kind)
    if not _is_safe_download_path(target):
        return f"No puedo abrir esa carpeta fuera de Downloads: {target}"
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(target)])
        elif os.name == "posix" and os.uname().sysname == "Darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return f"Carpeta abierta: {target}"
    except Exception as e:
        return f"No se pudo abrir la carpeta: {e}"


def download_cleanup(kind: str = "all", dry_run: bool = False, limit: int = 500) -> dict:
    target = _download_target(kind)
    if not _is_safe_download_path(target) or not target.exists():
        return {"removed": [], "count": 0, "error": f"Ruta no valida: {target}"}

    removed = []
    scanned = 0
    for path in target.rglob("*"):
        if scanned >= max(1, int(limit or 500)):
            break
        if not path.is_file():
            continue
        scanned += 1
        if path.suffix.lower() not in PARTIAL_EXTENSIONS:
            continue
        try:
            removed.append(str(path))
            if not dry_run:
                path.unlink()
        except Exception:
            continue
    return {
        "folder": str(target),
        "dry_run": bool(dry_run),
        "count": len(removed),
        "removed": removed,
    }


def _compact_search_text(text: str, max_items: int = 5) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {"summary": "No se encontraron resultados.", "items": []}

    items = []
    current: dict[str, str] | None = None
    for line in raw.splitlines():
        clean = line.strip()
        if not clean:
            continue
        match = re.match(r"^\d+\.\s+(.+)$", clean)
        if match:
            if current:
                items.append(current)
            current = {"title": match.group(1), "snippet": "", "url": ""}
            continue
        if current is None:
            continue
        if clean.startswith("http://") or clean.startswith("https://"):
            current["url"] = clean
        elif not current["snippet"]:
            current["snippet"] = clean
    if current:
        items.append(current)

    items = items[:max(1, int(max_items or 5))]
    if not items:
        return {"summary": raw[:1200], "items": []}

    summary_bits = []
    for item in items[:3]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        if title and snippet:
            summary_bits.append(f"{title}: {snippet}")
        elif title:
            summary_bits.append(title)
    return {
        "summary": " ".join(summary_bits)[:1200],
        "items": items,
    }


def web_search_summary(query: str, max_results: int = 5) -> dict:
    if not str(query or "").strip():
        return {"summary": "Necesito una busqueda.", "items": []}
    from actions.web_search import web_search

    result = web_search({"query": query, "mode": "search"})
    compact = _compact_search_text(result, max_items=max_results)
    compact["query"] = query
    return compact


def utility_tools(parameters: dict, player=None, speak=None):
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()
    if player:
        player.write_log(f"[Utility] {action}")

    if action == "download_open_folder":
        return download_open_folder(params.get("kind") or params.get("folder") or "downloads")
    if action == "download_cleanup":
        return download_cleanup(
            kind=params.get("kind") or params.get("folder") or "all",
            dry_run=str(params.get("dry_run", False)).lower() in {"1", "true", "yes", "y", "on", "si", "sí"},
            limit=int(params.get("limit") or 500),
        )
    if action == "web_search_summary":
        return web_search_summary(
            query=params.get("query", ""),
            max_results=int(params.get("limit") or params.get("max_results") or 5),
        )

    return "Accion desconocida. Usa download_open_folder, download_cleanup o web_search_summary."

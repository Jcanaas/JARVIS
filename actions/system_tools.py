from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


try:
    import psutil
    _PSUTIL = True
except Exception:
    psutil = None
    _PSUTIL = False


_SYSTEM = platform.system()


def _known_dirs() -> dict[str, Path]:
    home = Path.home()
    return {
        "home": home,
        "desktop": home / "Desktop",
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
    }


def _resolve_base(raw: str = "") -> Path:
    dirs = _known_dirs()
    key = str(raw or "").strip().lower()
    if not key:
        return dirs["home"]
    return dirs.get(key, Path(raw).expanduser())


def _safe_user_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
        return resolved == home or resolved.is_relative_to(home)
    except Exception:
        return False


def _format_size(bytes_value: int | float) -> str:
    size = float(bytes_value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def system_status(limit: int = 5) -> dict:
    status = {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "psutil_available": _PSUTIL,
    }

    disk_target = Path(Path.home().anchor or str(Path.home()))
    try:
        usage = shutil.disk_usage(disk_target)
    except Exception:
        usage = shutil.disk_usage(Path.cwd().anchor or Path.cwd())
    status["home_disk"] = {
        "total": _format_size(usage.total),
        "used": _format_size(usage.used),
        "free": _format_size(usage.free),
        "percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }

    if not _PSUTIL:
        return status

    status["cpu_percent"] = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    status["memory"] = {
        "total": _format_size(mem.total),
        "used": _format_size(mem.used),
        "available": _format_size(mem.available),
        "percent": mem.percent,
    }
    try:
        battery = psutil.sensors_battery()
        if battery:
            status["battery"] = {
                "percent": battery.percent,
                "plugged": battery.power_plugged,
            }
    except Exception:
        pass

    procs = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            mem_bytes = getattr(info.get("memory_info"), "rss", 0)
            procs.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "",
                "memory": _format_size(mem_bytes),
                "memory_bytes": mem_bytes,
            })
        except Exception:
            continue
    procs.sort(key=lambda p: p.get("memory_bytes", 0), reverse=True)
    status["top_processes"] = [
        {k: v for k, v in p.items() if k != "memory_bytes"}
        for p in procs[:max(1, int(limit or 5))]
    ]
    return status


def app_launch(app_name: str) -> str:
    from actions.open_app import open_app

    return open_app(parameters={"app_name": app_name})


def app_focus(title: str) -> str:
    from actions.computer_control import computer_control

    return computer_control(parameters={"action": "focus_window", "title": title})


def _iter_files(base: Path, max_scan: int = 25000):
    scanned = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"AppData", "node_modules", ".git", "__pycache__"}]
        for name in files:
            if name.startswith("."):
                continue
            scanned += 1
            if scanned > max_scan:
                return
            yield Path(root) / name


def _file_info(path: Path) -> dict:
    st = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "folder": str(path.parent),
        "size": _format_size(st.st_size),
        "modified": st.st_mtime,
    }


def file_find(name: str = "", extension: str = "", path: str = "", limit: int = 20) -> list[dict]:
    base = _resolve_base(path or "home")
    if not _safe_user_path(base) or not base.exists():
        return []
    needle = str(name or "").lower().strip()
    ext = str(extension or "").lower().strip()
    if ext and not ext.startswith("."):
        ext = f".{ext}"

    results = []
    for item in _iter_files(base):
        try:
            item_name = item.name.lower()
            if needle and needle not in item_name:
                continue
            if ext and item.suffix.lower() != ext:
                continue
            results.append(_file_info(item))
            if len(results) >= max(1, int(limit or 20)):
                break
        except Exception:
            continue
    return results


def file_recent(path: str = "downloads", limit: int = 15) -> list[dict]:
    base = _resolve_base(path or "downloads")
    if not _safe_user_path(base) or not base.exists():
        return []
    items = []
    for item in _iter_files(base, max_scan=15000):
        try:
            items.append(_file_info(item))
        except Exception:
            continue
    items.sort(key=lambda i: i.get("modified", 0), reverse=True)
    return items[:max(1, int(limit or 15))]


def file_reveal(path: str) -> str:
    target = Path(path).expanduser()
    if not _safe_user_path(target) or not target.exists():
        return f"No puedo revelar esa ruta: {target}"
    try:
        if _SYSTEM == "Windows":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(target)])
        elif _SYSTEM == "Darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            folder = target.parent if target.is_file() else target
            subprocess.Popen(["xdg-open", str(folder)])
        return f"Abierto en el explorador: {target}"
    except Exception as e:
        return f"No se pudo abrir en el explorador: {e}"


def system_tools(parameters: dict, player=None, speak=None):
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()
    if player:
        player.write_log(f"[System] {action}")

    if action == "system_status":
        return system_status(limit=int(params.get("limit") or 5))
    if action == "app_launch":
        return app_launch(params.get("app_name") or params.get("name") or params.get("query") or "")
    if action == "app_focus":
        return app_focus(params.get("title") or params.get("app_name") or params.get("name") or "")
    if action == "file_find":
        return file_find(
            name=params.get("name") or params.get("query") or "",
            extension=params.get("extension") or "",
            path=params.get("path") or "",
            limit=int(params.get("limit") or 20),
        )
    if action == "file_recent":
        return file_recent(path=params.get("path") or "downloads", limit=int(params.get("limit") or 15))
    if action == "file_reveal":
        return file_reveal(params.get("path") or "")

    return "Accion desconocida. Usa system_status, app_launch, app_focus, file_find, file_recent o file_reveal."

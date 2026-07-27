""""Vigila esta pantalla y avísame cuando pase X" — a lightweight continuous
screen-condition watcher, separate from screen_processor's live vision chat.

Polls a screenshot every few seconds and asks a cheap single-shot vision model
a strict yes/no question ("has this happened yet?"). Deliberately NOT the live
audio vision session in screen_processor.py — that's built for an interactive
back-and-forth conversation about what's on screen, expensive to keep hot for
a background poll running every few seconds for up to half an hour.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from actions.screen_processor import _capture_screen

_VISION_MODEL = "gemini-2.5-flash-lite"


@dataclass
class _Watch:
    id: str
    condition: str
    interval_secs: float
    deadline: float
    on_trigger: Callable[[str, bytes], None]
    stop_flag: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    status: str = "watching"  # watching | triggered | stopped | timed_out | error


_watches: dict[str, _Watch] = {}
_lock = threading.Lock()


def _check_condition(condition: str, image_bytes: bytes) -> bool:
    import io
    import PIL.Image
    from actions.genai_client import get_model

    model = get_model(_VISION_MODEL)
    prompt = (
        "You are watching a screenshot for one specific condition. "
        f"Condition: \"{condition}\"\n"
        "Has this condition become true RIGHT NOW, based on this screenshot? "
        "Reply with ONLY the single word YES or NO — nothing else."
    )
    try:
        img = PIL.Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        text = str(getattr(response, "text", "") or "").strip().upper()
        return text.startswith("YES")
    except Exception as e:
        print(f"[ScreenWatch] ⚠️ check error: {e}")
        return False


def _run(watch: _Watch) -> None:
    while not watch.stop_flag.is_set():
        if time.monotonic() >= watch.deadline:
            watch.status = "timed_out"
            return
        try:
            image_bytes, _mime_type = _capture_screen()
            if _check_condition(watch.condition, image_bytes):
                watch.status = "triggered"
                try:
                    watch.on_trigger(watch.condition, image_bytes)
                except Exception as e:
                    print(f"[ScreenWatch] ⚠️ on_trigger error: {e}")
                return
        except Exception as e:
            print(f"[ScreenWatch] ⚠️ capture/check error: {e}")
        watch.stop_flag.wait(timeout=watch.interval_secs)
    watch.status = "stopped"


def start_watch(
    condition: str,
    on_trigger: Callable[[str, bytes], None],
    interval_secs: float = 5.0,
    max_minutes: float = 30.0,
) -> str:
    """Start watching the screen for `condition`. Returns a watch id.

    Polls every interval_secs (min 2s — don't hammer the vision model) up to
    max_minutes, then gives up automatically. Only one active watch at a
    time — a second call to a busy watcher would just compete for the same
    screen anyway, and stacking API calls silently is the kind of thing that
    burns quota without the user noticing.
    """
    condition = str(condition or "").strip()
    if not condition:
        raise ValueError("condition is required")
    interval_secs = max(2.0, float(interval_secs or 5.0))
    max_minutes = max(0.5, min(180.0, float(max_minutes or 30.0)))

    with _lock:
        active = [w for w in _watches.values() if w.status == "watching"]
        if active:
            raise RuntimeError(
                f"Ya hay una vigilancia activa: '{active[0].condition}'. "
                "Detenla antes de iniciar otra."
            )
        watch_id = uuid.uuid4().hex[:8]
        watch = _Watch(
            id=watch_id,
            condition=condition,
            interval_secs=interval_secs,
            deadline=time.monotonic() + max_minutes * 60,
            on_trigger=on_trigger,
        )
        watch.thread = threading.Thread(target=_run, args=(watch,), daemon=True)
        _watches[watch_id] = watch
        watch.thread.start()
    return watch_id


def stop_watch(watch_id: str = "") -> str:
    with _lock:
        targets = (
            [_watches[watch_id]] if watch_id and watch_id in _watches
            else [w for w in _watches.values() if w.status == "watching"]
        )
    if not targets:
        return "No hay ninguna vigilancia activa."
    for w in targets:
        w.stop_flag.set()
    return f"Vigilancia detenida: '{targets[0].condition}'."


def get_status() -> dict:
    with _lock:
        active = [w for w in _watches.values() if w.status == "watching"]
    if not active:
        return {"watching": False}
    w = active[0]
    remaining_s = max(0, int(w.deadline - time.monotonic()))
    return {"watching": True, "condition": w.condition, "remaining_seconds": remaining_s}

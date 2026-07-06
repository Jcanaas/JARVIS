"""Decoupled pub/sub bus between actions/ and the UI.

Actions emit events instead of calling UI methods directly (e.g. via a
`player` object). Subscribers (typically the UI) register handlers per
event type. If nobody is subscribed, emit() is a safe no-op — this is what
lets actions run standalone (agent path, tests, CLI) without a live UI.
"""

from enum import Enum
from typing import Callable


class ActionEvent(Enum):
    LOG = "action:log"
    PROGRESS = "action:progress"
    TOAST = "action:toast"
    ERROR = "action:error"


_subscribers: dict[ActionEvent, list[Callable]] = {e: [] for e in ActionEvent}


def subscribe(event: ActionEvent, handler: Callable[[dict], None]):
    """Register handler for event type."""
    _subscribers[event].append(handler)


def unsubscribe(event: ActionEvent, handler: Callable[[dict], None]):
    try:
        _subscribers[event].remove(handler)
    except ValueError:
        pass


def emit(event: ActionEvent, data: dict):
    """Publish event to all subscribers. Swallows handler errors so a
    broken UI callback never breaks the action that emitted the event."""
    for handler in _subscribers[event]:
        try:
            handler(data)
        except Exception as e:
            print(f"[EventBus] Handler error for {event.value}: {e}")


def log(source: str, message: str):
    emit(ActionEvent.LOG, {"source": source, "message": message})


def progress(total: int, current: int, label: str = ""):
    emit(ActionEvent.PROGRESS, {"total": total, "current": current, "label": label})


def toast(text: str, duration: int = 2500):
    emit(ActionEvent.TOAST, {"text": text, "duration": duration})


def error(source: str, message: str):
    emit(ActionEvent.ERROR, {"source": source, "message": message})

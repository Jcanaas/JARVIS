"""Serialized command boundary for music playback.

All callers share one worker so stateful backends such as mpv never receive
overlapping mutations.  The controller also turns the backend's mixed return
types into a small, explicit result contract.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class PlaybackCommandResult:
    action: str
    ok: bool
    message: str = ""
    value: Any = None


@dataclass(frozen=True)
class _QueuedCommand:
    action: str
    params: dict[str, Any]
    future: Future


_STOP = object()
_FAILURE_PREFIXES = (
    "aplicación cerrándose",
    "esa lista ",
    "error",
    "falló",
    "la cola está vacía",
    "la lista está vacía",
    "mpv no pudo",
    "no ",
)


def parameter_value(params: Mapping[str, Any], *names: str) -> Any:
    """Return the first present, non-None value without discarding zero."""
    for name in names:
        if name in params and params[name] is not None:
            return params[name]
    return None


def command_result(action: str, value: Any) -> PlaybackCommandResult:
    if isinstance(value, PlaybackCommandResult):
        return value
    message = value if isinstance(value, str) else ""
    normalized = message.strip().casefold()
    ok = value is not False and not any(
        normalized.startswith(prefix) for prefix in _FAILURE_PREFIXES
    )
    return PlaybackCommandResult(action=action, ok=ok, message=message, value=value)


class PlaybackController:
    """Execute playback mutations sequentially on one background worker."""

    def __init__(
        self,
        dispatch: Callable[[str, dict[str, Any]], Any],
        on_result: Callable[[PlaybackCommandResult], None] | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._on_result = on_result
        self._queue: queue.Queue[_QueuedCommand | object] = queue.Queue()
        self._closed = False
        self._state_lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._run,
            name="jarvis-playback-controller",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self, action: str, params: Mapping[str, Any] | None = None
    ) -> Future:
        future: Future = Future()
        command = _QueuedCommand(
            action=str(action or "").strip().lower(),
            params=dict(params or {}),
            future=future,
        )
        with self._state_lock:
            if self._closed:
                raise RuntimeError("PlaybackController is closed")
            self._queue.put(command)
        return future

    def execute(
        self,
        action: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> PlaybackCommandResult:
        return self.submit(action, params).result(timeout)

    def close(self, wait: bool = True) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(_STOP)
        if wait and threading.current_thread() is not self._worker:
            self._worker.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            queued = self._queue.get()
            try:
                if queued is _STOP:
                    return
                command = queued
                if command.future.set_running_or_notify_cancel():
                    result = self._execute_one(command)
                    self._notify(result)
                    command.future.set_result(result)
            finally:
                self._queue.task_done()

    def _execute_one(self, command: _QueuedCommand) -> PlaybackCommandResult:
        try:
            value = self._dispatch(command.action, command.params)
        except Exception as exc:
            return PlaybackCommandResult(
                action=command.action,
                ok=False,
                message=f"{type(exc).__name__}: {exc}",
            )
        return command_result(command.action, value)

    def _notify(self, result: PlaybackCommandResult) -> None:
        if self._on_result is None:
            return
        try:
            self._on_result(result)
        except Exception:
            pass

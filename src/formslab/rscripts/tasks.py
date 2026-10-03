"""Named background threads for rScripts.

For work that must not stall the host loop (an exposure, a long transfer). A
task is ``func(stop_event, *args, **kwargs)`` and should return soon after
``stop_event`` is set.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

_logger: Callable[[str], None] = print


def set_logger(func: Callable[[str], None]) -> None:
    global _logger
    _logger = func


class Tasker:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[..., Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, name: str, func: Callable[..., Any]) -> None:
        with self._lock:
            self._registry[name] = func
        _logger(f"[Tasker] Registered task '{name}'")

    def start(self, name: str, *args: Any, **kwargs: Any) -> bool:
        """Start a registered task; False if it is already running."""
        with self._lock:
            if name not in self._registry:
                raise KeyError(f"Task '{name}' not registered")
            thread = self._threads.get(name)
            if thread is not None and thread.is_alive():
                return False
            stop_event = threading.Event()

            def wrapper() -> None:
                _logger(f"[Tasker] Task '{name}' started")
                try:
                    self._registry[name](stop_event, *args, **kwargs)
                finally:
                    _logger(f"[Tasker] Task '{name}' finished")
                    with self._lock:
                        self._threads.pop(name, None)
                        self._events.pop(name, None)

            thread = threading.Thread(target=wrapper, daemon=True)
            self._threads[name] = thread
            self._events[name] = stop_event
        thread.start()
        return True

    def stop(self, name: str, timeout: float | None = None) -> bool:
        """Signal a task to stop and wait for it; False if it was not running."""
        with self._lock:
            event = self._events.get(name)
            thread = self._threads.get(name)
        if thread is None:
            return False
        if event is not None:
            event.set()
        thread.join(timeout)
        _logger(f"[Tasker] Task '{name}' stopped")
        return True

    def is_running(self, name: str) -> bool:
        with self._lock:
            thread = self._threads.get(name)
        return thread is not None and thread.is_alive()


_default = Tasker()


def rTaskRegister(name: str, func: Callable[..., Any]) -> None:
    _default.register(name, func)


def rTaskStart(name: str, *args: Any, **kwargs: Any) -> bool:
    return _default.start(name, *args, **kwargs)


def rTaskStop(name: str, timeout: float | None = None) -> bool:
    return _default.stop(name, timeout)


def rTaskRunning(name: str) -> bool:
    return _default.is_running(name)

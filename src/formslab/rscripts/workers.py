"""One thread per rScript, so a slow instrument cannot hold up another.

Each loaded rScript is called in its own loop at the host's rate. A chamber
read that takes seconds no longer delays the PSU, the thermocouples, a cast
command to another instrument, or the plan's own steps. Each script's state
(`rg`, its gates, its hardware handles) is touched only by its own thread; what
they share is CAST (locked in `castutils`) and the `forms` variables.

    workers = Workers(forms, hz=10)
    workers.start()
    ...                                   # the plan runs on the main thread
    stuck = workers.stop()                # then rscripts.shutdown(forms)

`stop` waits for each thread to finish its current call, so a script's
rShutdown never runs while its rScript is still talking to the instrument --
unless a call is stuck past the timeout, which `stop` reports.
"""
from __future__ import annotations

import threading
import time

from formslab.rscripts import loader


class Workers:
    def __init__(self, forms, hz: float = 10.0) -> None:
        self.forms = forms
        self.period = 1.0 / hz
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}

    def start(self) -> None:
        for name, func in loader.scripts():
            t = threading.Thread(target=self._run, args=(name, func),
                                 name=f"rScript-{name}", daemon=True)
            self._threads[name] = t
            t.start()

    def _run(self, name: str, func) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            loader.call(self.forms, name, func)
            self._stop.wait(max(0.0, self.period - (time.monotonic() - started)))

    def alive(self) -> list[str]:
        return [n for n, t in self._threads.items() if t.is_alive()]

    def stop(self, timeout: float = 15.0) -> list[str]:
        """Signal every thread and wait for each to finish its current call.
        Returns the names still running after `timeout` seconds in total."""
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads.values():
            t.join(max(0.0, deadline - time.monotonic()))
        return self.alive()

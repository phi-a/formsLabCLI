"""The chamber monitor: formsLAB reads the chamber while no run is going.

During a run, rLACO owns the HVC-3500 and publishes it on CAST "hvc". Between
runs nothing did, so the Chamber tab went stale just when someone wants to watch
a chamber warm up or hold its vacuum after End. The monitor fills that gap. It
runs in the formsLAB server (`labcli gui`) or in a labcli watch screen, only
reads, and gives the controller up to a run, since the controller takes one
client at a time:

* it reads only while no host is running, and holds `monitor.lock` (its pid)
  for as long as it is connected; a second process whose monitor finds the lock
  held by a live process waits its turn (`start_if_free` does not start one);
* a host takes its own lock first, then waits for `monitor.lock` to go
  (`wait_released`) before its routines start;
* a read in progress stops between two queries once a host's lock appears.

Commands still need a run: nothing here sends one.
"""
from __future__ import annotations

import os
import threading
import time

import psutil

from formslab.config import run_dir
from formslab.state.castfile import UpdateStatus

LABEL = "hvc"
RETRY_S = 30.0          # between connect attempts while the chamber is unreachable
RELEASE_WAIT_S = 15.0   # how long a host waits for the monitor to let go


def lock_path():
    return run_dir() / "monitor.lock"


def holder() -> int | None:
    """The pid of the process whose monitor holds the controller, or None. A lock
    left by a process that has gone is removed."""
    path = lock_path()
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if pid != os.getpid() and not psutil.pid_exists(pid):
        path.unlink(missing_ok=True)
        return None
    return pid


def wait_released(emit, limit_s: float = RELEASE_WAIT_S) -> None:
    """For a host that has just taken its lock: wait until no monitor holds the
    controller. A monitor that does not let go in `limit_s` is left behind; the
    host's rLACO then retries its connection."""
    if holder() is None:
        return
    emit("formsLAB's chamber monitor is letting go of the controller for this run")
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if holder() is None:
            return
        time.sleep(0.1)
    emit(f"formsLAB's chamber monitor did not let go within {limit_s:g} s; going on")


def run_going() -> bool:
    """A live host holds the sequence lock. Cheap when there is no lock at all."""
    from formslab.host import sequence

    lock = sequence.read_lock()
    return lock is not None and sequence.is_host(lock["pid"])


def start_if_free() -> "ChamberMonitor | None":
    """A monitor of this process's own, started, unless another process already
    reads the chamber (formsLAB, or a labcli watch screen): then None, and the
    readings it publishes are shared through CAST anyway."""
    if holder() not in (None, os.getpid()):
        return None
    monitor = ChamberMonitor()
    monitor.start()
    return monitor


class ChamberMonitor(threading.Thread):
    """Reads the chamber every poll interval (tvac_bench.json) while no run is
    going, and publishes it as rLACO would. `stop()` lets go and ends."""

    def __init__(self) -> None:
        super().__init__(name="chamber-monitor", daemon=True)
        self._stopping = threading.Event()
        self.laco = None
        self.retry_at = 0.0
        self.next_read = 0.0

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self.join(timeout)

    def run(self) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    self._once()
                except Exception:                    # never take the server down
                    self._release()
                    self._stopping.wait(5.0)
                self._stopping.wait(0.5)
        finally:
            self._release()

    def _interrupt(self) -> bool:
        return self._stopping.is_set() or run_going()

    def _once(self) -> None:
        if run_going():
            self._release()
            return
        if time.monotonic() < max(self.retry_at, self.next_read):
            return
        if holder() not in (None, os.getpid()):      # another window reads it: one reader at a time
            return
        self._claim()
        if run_going():                              # a host took its lock meanwhile
            self._release()
            return
        if self.laco is None:
            from formslab.devices.hvc3500.laco import LACO
            self.laco = LACO()
        if not self.laco.connected:
            try:
                self.laco.connect()
            except OSError as e:
                self.retry_at = time.monotonic() + RETRY_S
                self._release()
                UpdateStatus(LABEL, {"connected": False, "error": str(e)})
                return
        try:
            status = self.laco.status(interrupt=self._interrupt)
        except (OSError, ValueError) as e:
            self._release()
            UpdateStatus(LABEL, {"connected": False, "error": str(e)})
            self.retry_at = time.monotonic() + RETRY_S
            return
        if status is None:                           # a run is starting: it is the run's now
            self._release()
            return
        from formslab.devices.hvc3500 import turbo
        UpdateStatus(LABEL, turbo.annotate(status.as_cast(), self.laco.profile.limits))
        self.next_read = time.monotonic() + self.laco.profile.poll_interval_s

    def _claim(self) -> None:
        lock_path().write_text(f"{os.getpid()}\n", encoding="utf-8")

    def _release(self) -> None:
        """Close the connection, then say so: a host waits for the lock to go."""
        if self.laco is not None and self.laco.connected:
            try:
                self.laco.close()
            except Exception:
                pass
        try:
            if holder() == os.getpid():
                lock_path().unlink(missing_ok=True)
        except OSError:
            pass

# --- rSMTC08: thermocouples from the Sequent SMTC08 boards ---
#
# Reads every board in BOARDS that usbmap.json configures and publishes one
# kelvin scalar per channel: SMTC08_A -> TC01..TC08, SMTC08_B -> TC09..TC16, and
# the same readings in C on the CAST "tc" block for the console's cast panel
# (read-only: no commands). A board missing from usbmap is skipped once
# with a log line; a board that fails to open or read is retried every
# RETRY_INTERVAL s, and its channels read NaN meanwhile so the CSV shows the gap.
#
# On Windows the board's serial port comes from "resource_windows" (e.g. "COM7")
# in its usbmap entry, on Linux from "resource" or the hub "port".
import math
import os
import time

from formslab.console.cast.castutils import UpdateStatus
from formslab.devices.smtc08.driver import SMTC08
from formslab.rscripts import C2K, RScriptControl

name = os.path.splitext(os.path.basename(__file__))[0]

BOARDS = {"SMTC08_A": 1, "SMTC08_B": 9}    # usbmap label -> first TC number
CHANNELS = 8
POLL_INTERVAL = 2.0
RETRY_INTERVAL = 30.0

CAST_LABELS = ("tc",)          # read-only: no commands


def STATUS_LABELS(label, key):
    """`TC01 C` -> `TC01 (C)` on the status page."""
    return f"{key[:-2]} (C)" if key.endswith(" C") else None
COMMANDS = []
VARIABLES = [(f"TC{first + i:02d}", "K") for first in BOARDS.values() for i in range(CHANNELS)]


class rGlobal:
    boards = {}          # label -> SMTC08, while open
    absent = set()       # labels not in usbmap: never retried
    retry_at = {}        # label -> time.monotonic() of the next open attempt
    last_error = {}      # label -> last error text, so each is logged once
    status = {}          # the CAST "tc" block: "TC01 C" -> value or None


rg = rGlobal


def _publish(run, first, temps_c):
    for idx, t in enumerate(temps_c, first):
        var_name = f"TC{idx:02d}"
        run.publish(var_name, math.nan if t is None else C2K(t), "K")
        rg.status[f"{var_name} C"] = t


def _fail(run, label, first, exc):
    msg = f"{type(exc).__name__}: {exc}"
    if rg.last_error.get(label) != msg:
        run.log(f"{label} {msg}; retrying every {RETRY_INTERVAL:g} s",
                  level="ERROR", component=name)
        rg.last_error[label] = msg
    board = rg.boards.pop(label, None)
    if board is not None:
        try:
            board.close()
        except Exception:
            pass
    rg.retry_at[label] = time.monotonic() + RETRY_INTERVAL
    _publish(run, first, [None] * CHANNELS)


def _board(run, label, first):
    """The open board for `label`, or None (absent, or waiting to retry)."""
    if label in rg.boards:
        return rg.boards[label]
    if label in rg.absent or time.monotonic() < rg.retry_at.get(label, 0.0):
        return None
    try:
        board = SMTC08(label=label)
    except ValueError as exc:            # not in usbmap.json
        rg.absent.add(label)
        run.log(f"{label} not configured, skipped ({exc})", component=name)
        return None
    except Exception as exc:
        _fail(run, label, first, exc)
        return None
    rg.boards[label] = board
    run.log(f"{label} open on {board.port}", component=name)
    return board


def rScript(run):
    if RScriptControl(run, name).tick(seconds=POLL_INTERVAL):
        return
    for label, first in BOARDS.items():
        board = _board(run, label, first)
        if board is None:
            continue
        try:
            temps = board.read_all()
        except Exception as exc:
            _fail(run, label, first, exc)
            continue
        if rg.last_error.pop(label, None) is not None:
            run.log(f"{label} reading again", component=name)
        _publish(run, first, temps)
    if rg.status:
        UpdateStatus("tc", dict(sorted(rg.status.items())))


def rShutdown(run):
    for label, board in list(rg.boards.items()):
        try:
            board.close()
        except Exception:
            pass
    rg.boards.clear()

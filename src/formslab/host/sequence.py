"""The sequence host: the process that runs rScripts against lab hardware,
optionally inside a FORMS mission.

Launched by the console's `ctrl` tab (`run <mission|tvac|laco>`), or directly:

    python -m formslab.host.sequence --mode laco

Two kinds of run, chosen by mode:

* **Lab** (`laco`) -- no FORMS. A `formslab.rscripts.LabForms` handle and a
  loop paced in real time at `LAB_LOOP_HZ`. Needs only formsLabCLI.
* **FORMS** (`mission`, `zen`, `tvac`, a mission file) -- FORMS builds the
  handle and its `SequenceRunner` drives the steps, with the rScripts as its
  per-step tick. Needs the `[forms]` extra; every FORMS import is made here, at
  call time, so a lab machine without FORMS can still import this module.

For FORMS runs the host is also the bridge in the other direction: FORMS
inverts control via `forms.zen.hosthooks`, so portable routine code can reach
the telemetry stream and CAST state without importing anything from this
package.
"""

import os
import time
import traceback
from argparse import ArgumentParser
import signal
from pathlib import Path
import re

from formslab import rscripts
from formslab.console.ctrl.ctrlutils import ReadCommand,ResetCtrlState
from formslab.console.cast.castutils import UpdateStatus, ResetJson, WriteCommand
from formslab.console.cmd.cmdutils import read_cmd, write_response, reset_cmd_state

from formslab.host.modes import LAB_MODES

# Lab loop rate. Scripts gate their own hardware cadence (rLACO polls every
# few seconds); this only bounds how quickly ctrl/cmd requests are seen.
LAB_LOOP_HZ = 10.0

# GUI streaming is an optional host capability provided by the host-runtime
# module stream.py (it writes data/streamfile.json for the Zenith frontend).
# When it is absent the streaming calls degrade to no-ops so sequence.py still
# runs end-to-end (headless runs, tests).
try:
    from formslab.host.stream import cleanup_shared_memory, write, flush as stream_flush, set_stream_hz
except ImportError:
    def cleanup_shared_memory(*_args, **_kwargs):
        pass

    def write(*_args, **_kwargs):
        pass

    def stream_flush(*_args, **_kwargs):
        pass

    def set_stream_hz(*_args, **_kwargs):
        pass

# Register host capabilities with the .zen runtime. The routines layer
# (forms.zen) never imports app/ or cli/ directly (the forms-handle
# contract); the host injects GUI streaming + CAST device I/O here.
try:
    from types import SimpleNamespace as _HostNS
    from formslab.host.stream import set_stream_hz as _set_stream_hz
    from formslab.console.cast.castutils import (
        ReadCommand as _cast_read,
        WriteCommand as _cast_write,
        UpdateStatus as _cast_status,
        ReadStatus as _cast_read_status,
    )
    from forms.zen.hosthooks import register_stream_hook, register_cast_provider
    register_stream_hook(_set_stream_hz)
    register_cast_provider(_HostNS(
        read_command=_cast_read,
        write_command=_cast_write,
        update_status=_cast_status,
        read_status=_cast_read_status,
    ))
except Exception:
    pass

forms = None
paused = False
_zen_runtime = None  # ZenRuntime instance when running .zen files

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# IPC paths live under the shared run dir (FORMS' ``run_dir`` when installed,
# else ``<output>/.run``), so a dev host and an attached Zenith agree on where
# events/session/lock go instead of hard-coding the source tree's data/ dir.
from formslab.host.paths import run_dir as _run_dir

SEQUENCE_LOCK_PATH = _run_dir() / "sequence.lock"
SEQUENCE_SESSION_PATH = _run_dir() / "sequence.session.json"
# Sequence runtime-event channel (JSONL). Parallel to streamfile.json (state
# telemetry); carries the structure manifest + per-segment progress so the agent
# and the console can observe how the run was built and what it is doing.
SEQUENCE_EVENTS_PATH = _run_dir() / "sequence.events.jsonl"
_sequence_lock_fd = None


def _reset_events_file() -> None:
    """Truncate the Sequence event log at run start (parallel to ResetJson)."""
    try:
        SEQUENCE_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEQUENCE_EVENTS_PATH.write_text("", encoding="utf-8")
    except Exception:
        pass


def _write_session(mode: str, config_path=None) -> None:
    """Write session metadata so GUI/CLI can discover and attach to this process."""
    import json
    session = {
        "pid": os.getpid(),
        "mode": mode,
        "config_path": str(Path(config_path).resolve()) if config_path else None,
        "started_at": time.time(),
        "source": "sequence",
    }
    try:
        SEQUENCE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEQUENCE_SESSION_PATH.write_text(json.dumps(session, indent=2), encoding="utf-8")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    # On Windows, os.kill(pid, 0) can raise inconsistent low-level errors
    # (including SystemError for stale/invalid lock pids). Use Win32 APIs
    # directly for robust process liveness checks.
    if os.name == "nt":
        if pid > 0x7FFFFFFF:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE

            get_exit_code_process = kernel32.GetExitCodeProcess
            get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            get_exit_code_process.restype = wintypes.BOOL

            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL

            handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                # Access denied can still imply a live process.
                last_error = ctypes.get_last_error()
                if last_error == 5:  # ERROR_ACCESS_DENIED
                    return True
                return False

            try:
                exit_code = wintypes.DWORD(0)
                if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                    return True
                return int(exit_code.value) == STILL_ACTIVE
            finally:
                close_handle(handle)
        except Exception:
            return False

    # POSIX and other runtimes.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def _acquire_sequence_lock(relay_func=None) -> bool:
    """
    Ensure only one sequence.py process is active across GUI sessions.

    Returns
    -------
    bool
        True if lock acquired, False if another live process holds it.
    """
    global _sequence_lock_fd
    this_pid = os.getpid()
    SEQUENCE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    def emit(msg: str) -> None:
        if relay_func:
            relay_func(msg)
        else:
            try:
                print(msg)
            except OSError:
                pass

    for _ in range(2):
        try:
            fd = os.open(
                SEQUENCE_LOCK_PATH,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(fd, f"{this_pid}\n{time.time():.6f}\n".encode("utf-8"))
            _sequence_lock_fd = fd
            return True
        except FileExistsError:
            try:
                raw = SEQUENCE_LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()
                owner_pid = int(raw[0]) if raw else -1
                if owner_pid > 0x7FFFFFFF:
                    owner_pid = -1
            except Exception:
                owner_pid = -1

            if owner_pid > 0 and owner_pid != this_pid and _pid_alive(owner_pid):
                emit(f"Another simulation process is already running (pid={owner_pid}).")
                return False

            # Stale lock file.
            try:
                SEQUENCE_LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                emit("Unable to clear stale sequence lock.")
                return False
        except Exception as e:
            emit(f"Failed to acquire sequence lock: {e}")
            return False

    return False


def _release_sequence_lock() -> None:
    """Release sequence lock if owned by this process."""
    global _sequence_lock_fd
    try:
        if _sequence_lock_fd is not None:
            os.close(_sequence_lock_fd)
            _sequence_lock_fd = None
    except Exception:
        pass

    try:
        owner_raw = SEQUENCE_LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()
        owner_pid = int(owner_raw[0]) if owner_raw else -1
    except Exception:
        owner_pid = -1

    if owner_pid == os.getpid():
        try:
            SEQUENCE_LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        try:
            SEQUENCE_SESSION_PATH.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _api_project_root() -> Path:
    """Return the python workspace root for catalog operations."""
    return Path(__file__).resolve().parent


def _build_fallback_api_helpers():
    """
    Build API helper callables when the live FORMS instance does not expose
    catalog helpers (older runtime objects).
    """
    from forms.core.catalog import build_catalog, catalog_status, find_symbols

    project_root = _api_project_root()

    def _api_status():
        return catalog_status(project_root=project_root)

    def _api_rebuild():
        return build_catalog(project_root=project_root)

    def _api_search(query: str = "", limit: int = 25):
        return find_symbols(
            query,
            project_root=project_root,
            limit=max(1, int(limit)),
        )

    def _api(query: str = "", limit: int = 12, raw: bool = False):
        payload = _api_search(query=query, limit=limit)
        if raw:
            return payload
        matches = payload.get("matches", []) or []
        count = int(payload.get("count", 0) or 0)
        total = int(payload.get("symbol_count", 0) or 0)
        query_txt = str(payload.get("query", "") or "").strip()
        if count == 0:
            suffix = f" for '{query_txt}'" if query_txt else ""
            return f"No FORMS API matches{suffix}. Rebuild with api_rebuild() if needed."

        lines = [f"FORMS API matches ({count}/{total})" + (f" for '{query_txt}'" if query_txt else "")]
        for row in matches:
            symbol = str(row.get("symbol", "") or "")
            sig = str(row.get("signature", "") or "")
            summary = str(row.get("summary", "") or "")
            file = str(row.get("file", "") or "")
            line = row.get("line")
            location = f" [{file}:{line}]" if file and line else ""
            lines.append(f"- {symbol}{sig}{location}")
            if summary:
                lines.append(f"  {summary}")
        return "\n".join(lines)

    return _api, _api_search, _api_status, _api_rebuild


def _render_rich_plain(content) -> str:
    """Convert rich renderables (Text/Table/Group/...) into plain console text."""
    try:
        from rich.console import Console
        from rich.text import Text
    except ModuleNotFoundError:
        return "" if content is None else str(content)

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Text):
        return content.plain

    console = Console(record=True, force_terminal=False, color_system=None, width=120)
    console.begin_capture()
    console.print(content)
    text = console.end_capture()
    text = _ANSI_ESCAPE.sub("", text)
    return text.strip()


def _run_tree_browser(raw_or_path: str = "") -> str:
    """
    Run catalog tree browser and return plain text for GUI REPL responses.

    Preferred forms:
      --tree
      --tree forms
      --tree forms coord
      --tree --rebuild
    """
    from formslab.console.cmd_browser import handle_cmd_command

    text = str(raw_or_path or "").strip()
    lower = text.lower()

    if not text:
        raw = "cmd"
    elif lower.startswith("--tree"):
        rest = text[6:].strip()
        raw = "cmd" + (f" {rest}" if rest else "")
    elif lower.startswith("tree"):
        rest = text[4:].strip()
        raw = "cmd" + (f" {rest}" if rest else "")
    elif lower.startswith("--cmd"):
        rest = text[5:].strip()
        raw = "cmd" + (f" {rest}" if rest else "")
    elif lower.startswith("cmd"):
        rest = text[3:].strip()
        raw = "cmd" + (f" {rest}" if rest else "")
    else:
        raw = f"cmd {text}"

    result = handle_cmd_command(raw)
    return _render_rich_plain(result.content)


def _bind_repl_namespace(forms, namespace):
    """
    Keep REPL bindings deterministic for GUI/console commands.

    We always bind live core objects and API helper aliases so commands do
    not depend on mutable user namespace state. A lab handle has no satellite,
    planet or sun, and a lab machine may have neither numpy nor FORMS; those
    names are then simply absent.
    """
    namespace["forms"] = forms
    for attr in ("satellite", "planet", "sun", "time"):
        if hasattr(forms, attr):
            namespace[attr] = getattr(forms, attr)
    try:
        import numpy as _np
        namespace["np"] = _np
    except ImportError:
        pass

    api = getattr(forms, "api", None)
    api_search = getattr(forms, "api_search", None)
    api_status = getattr(forms, "api_status", None)
    api_rebuild = getattr(forms, "api_rebuild", None)

    if not all(callable(fn) for fn in (api, api_search, api_status, api_rebuild)):
        try:
            api, api_search, api_status, api_rebuild = _build_fallback_api_helpers()
        except ImportError:
            return          # no FORMS: no API catalog to browse

        # Best-effort compatibility for commands like forms.api_status().
        try:
            if not callable(getattr(forms, "api", None)):
                setattr(forms, "api", api)
            if not callable(getattr(forms, "api_search", None)):
                setattr(forms, "api_search", api_search)
            if not callable(getattr(forms, "api_status", None)):
                setattr(forms, "api_status", api_status)
            if not callable(getattr(forms, "api_rebuild", None)):
                setattr(forms, "api_rebuild", api_rebuild)
        except Exception:
            pass

    namespace["api"] = api
    namespace["api_search"] = api_search
    namespace["api_status"] = api_status
    namespace["api_rebuild"] = api_rebuild
    namespace["tree"] = _run_tree_browser
    # Legacy alias; preferred command is --tree.
    namespace["cmd"] = _run_tree_browser


def _repl_help_text() -> str:
    return (
        "REPL commands:\n"
        "- query <expr>            Evaluate expression (recommended)\n"
        "- exec <statement>        Execute statement\n"
        "- --tree [path]           Browse FORMS API tree\n"
        "- --tree --help           Show tree-browser help\n"
        "- --tree --rebuild        Rebuild API catalog\n"
        "- query api_status()      API catalog status\n"
        "- query api_rebuild()     Rebuild API catalog\n"
        "- query api('InUmbra')    Search FORMS API\n"
        "- query tree('forms')     Tree node via query"
    )

# --- Signal Handling ---
class GracefulExit(SystemExit):
    """Custom exception used for graceful shutdown."""
    pass

def signal_handler(forms, relay_func=None):
    """Create a signal handler that reports through ``relay_func`` if provided."""

    def emit(msg: str) -> None:
        if relay_func:
            relay_func(msg)
        else:
            try:
                print(msg)
            except OSError:
                pass

    def handler(sig, frame):  # pragma: no cover - signal path
        emit(f"Received signal {sig}, exiting gracefully...")
        raise GracefulExit()

    return handler

# --- Command Poller ---
_CTRL_LABELS = ["end", "reset", "resume", "pause", "stream"]

def _apply_stream_rate(key):
    """Apply a ``stream`` control command. ``key`` is the target rate in Hz
    (``0`` disables streaming entirely — the no-viewer state)."""
    try:
        hz = float(key)
    except (TypeError, ValueError):
        return
    set_stream_hz(hz)

def check_ctrl_commands(forms):
    """
    Poll ctrlfile.json for control commands (end/reset/resume/pause/stream).

    Reads via ctrlutils.ReadCommand — the same ctrlfile.json the TUI's
    ``write_control`` writes to. (This previously read the *cast* file, where
    these labels are invalid, so every pause/resume/reset was silently dropped.)
    """
    global paused
    for label in _CTRL_LABELS:
        block = ReadCommand(label)
        if block is None:
            continue
        key = block.get("key")
        forms.log(f"ctrl command received: {label}={key}", component="FORMS", level="INFO")
        if label == "end":
            forms.log(f"Simulation ending with graceful exit.", component="FORMS", level="INFO")
            raise GracefulExit()
        elif label == "pause":
            paused = True
            forms.log(f"Simulation paused:{paused}", component="FORMS", level="INFO")
        elif label == "resume":
            paused = False
            forms.log(f"Simulation paused:{paused}", component="FORMS", level="INFO")
        elif label == "reset":
            ResetCtrlState()
            ResetJson()
        elif label == "stream":
            _apply_stream_rate(key)

# --- Interactive REPL Command Handler ---
def check_cmd(forms, namespace):
    """
    Poll cmdfile.json for interactive REPL commands.

    Supports:
      - eval: evaluate a Python expression, return repr of result
      - exec: execute a Python statement (no return value)
      - query: reserved for read-only queries
    """
    cmd = read_cmd()
    if cmd is None:
        return

    _bind_repl_namespace(forms, namespace)

    cmd_id = cmd.get("id", "unknown")
    cmd_type = cmd.get("type", "eval")
    payload = cmd.get("payload", "")
    payload_text = str(payload).strip()
    payload_lower = payload_text.lower()

    try:
        if cmd_type == "eval":
            if payload_lower in ("help", "--help"):
                write_response(cmd_id, result=_repl_help_text())
                return
            if payload_lower in ("query", "--query"):
                write_response(cmd_id, error="Use: query <expression>")
                return
            if payload_lower == "--tree" or payload_lower.startswith("--tree "):
                write_response(cmd_id, result=_run_tree_browser(payload_text))
                return
            # Backward compatibility
            if payload_lower == "tree" or payload_lower.startswith("tree "):
                write_response(cmd_id, result=_run_tree_browser(payload_text))
                return
            if payload_lower == "cmd" or payload_lower.startswith("cmd "):
                write_response(cmd_id, result=_run_tree_browser(payload_text))
                return
            if payload_lower == "--cmd" or payload_lower.startswith("--cmd "):
                write_response(cmd_id, result=_run_tree_browser(payload_text))
                return
            if not payload_text:
                write_response(cmd_id, error="Empty eval payload")
                return
            result = eval(payload, namespace)
            write_response(cmd_id, result=repr(result))
        elif cmd_type == "exec":
            if not payload_text:
                write_response(cmd_id, error="Empty exec payload")
                return
            exec(payload, namespace)
            write_response(cmd_id, result=None)
        elif cmd_type == "query":
            if not payload_text:
                write_response(cmd_id, error="Empty query payload. Use: query <expression>")
                return
            result = eval(payload, namespace)
            write_response(cmd_id, result=repr(result))
        else:
            write_response(cmd_id, error=f"Unknown command type: {cmd_type}")
    except Exception as e:
        write_response(cmd_id, error=f"{type(e).__name__}: {e}")


# --- Mode Transition Handler ---
def transition(forms, target):
    """Handle a mode transition requested by an rScript."""
    # The legacy FORMS modes still run their scripts through FORMS' own loader
    # until those scripts are ported; see modes/tvac.py.
    from forms.utils.rScripts import rScripts, eScript

    forms.log(f"Transitioning to {target} mode...", component="sequence")

    if target == "tvac":
        # --- Clean shutdown of mission-mode hardware before switching ---
        # Signal rSLTA to shut down PSU2 (reads shutdown from "slta" label)
        WriteCommand({"shutdown": True}, "slta")
        forms.log("Sent PSU2 shutdown command via rSLTA", component="sequence")
        eScript(forms)  # execute one final cycle so rSLTA processes the shutdown

        # Reset cast file to clear any stale commands
        ResetJson()

        # --- Load TVAC scripts (replaces mission-mode scripts) ---
        rScripts(forms, [
            "rVARIABLES.py", "rFSS.py", "rSTATES.py",
            "rPSU.py", "rCryoBoard.py", "rTVAC.py", "rTVAC_EQCTRL.py"
        ])
        eScript(forms)
        forms.recording = False
        forms.record(value=30, unit="seconds")
        forms.log("TVAC mode active — CSV recording disabled", component="sequence")

# --- Main execution loop ---
def channel(forms=None, mode="mission", config_path=None, relay_func=None):
    global paused, _zen_runtime

    def emit(msg: str) -> None:
        if relay_func:
            relay_func(msg)
        else:
            try:
                print(msg)
            except OSError:
                pass

    if not _acquire_sequence_lock(relay_func=relay_func):
        return []

    # Write shared session file so GUI and CLI can mutually discover the running process
    _write_session(mode=mode, config_path=config_path)

    ResetCtrlState()
    ResetJson()
    reset_cmd_state()
    _reset_events_file()
    lab = mode in LAB_MODES and not config_path
    if forms is None:
        # Auto-detect .zen files
        if config_path and config_path.endswith('.zen'):
            mode = "zen"

        if lab:
            from formslab.host.modes import laco as lacomode
            forms = lacomode.initialize()
        elif mode == "zen" and config_path:
            from forms.zen import ZenRuntime
            _zen_runtime = ZenRuntime(config_path)
            forms = _zen_runtime.initialize()
        elif config_path:
            # Load from configuration file (YAML/JSON)
            from forms.skills.mission_loader import MissionLoader
            loader = MissionLoader(config_path)
            forms = loader.initialize()
        elif mode == "tvac":
            from formslab.host.modes import tvac as tvacmode
            forms = tvacmode.initialize()
        else:
            from formslab.host.modes import darkness
            forms = darkness.initialize()

    # Select tick function: zen.tick() for .zen files, the formslab rScripts
    # runtime on a lab handle, FORMS' legacy loader for the other FORMS modes.
    if _zen_runtime:
        tick_fn = _zen_runtime.tick
    elif getattr(forms, "is_lab_handle", False):
        tick_fn = lambda: rscripts.tick(forms)
    else:
        from forms.utils.rScripts import eScript
        tick_fn = lambda: eScript(forms)

    # Build REPL namespace — for zen mode, use the runtime's namespace (has forms, satellite, etc.)
    # Otherwise a basic namespace with forms and whatever facades it has.
    if _zen_runtime:
        repl_ns = _zen_runtime._namespace
    else:
        repl_ns = {"__builtins__": __builtins__}
        _bind_repl_namespace(forms, repl_ns)

    # The first derive (previously here, unconditionally) now lives in the
    # Sequence's `setup` segment for the mission path, and in the tvac branch
    # below for a pure-tvac start — so each entry primes exactly once.

    signal.signal(signal.SIGTERM, signal_handler(forms, relay_func=relay_func))
    signal.signal(signal.SIGINT,  signal_handler(forms, relay_func=relay_func))

    logs = []

    entered_tvac = False

    try:
        # --- Lab mode (no FORMS): realtime loop over the rScripts ---
        if lab:
            forms.log(f'Sequence mode:{mode} running (lab, {LAB_LOOP_HZ:g} Hz).',
                      level="INFO", component='sequence')
            period = 1.0 / LAB_LOOP_HZ
            while True:
                started = time.monotonic()
                check_ctrl_commands(forms)
                check_cmd(forms, repl_ns)
                while paused:
                    time.sleep(0.1)
                    check_ctrl_commands(forms)
                    check_cmd(forms, repl_ns)
                tick_fn()
                write(forms)
                forms.record()
                time.sleep(max(0.0, period - (time.monotonic() - started)))

        # --- Mission mode (runs as a Sequence) ---
        if mode != "tvac":
            from forms.sequence import compile_sequence, SequenceRunner, JsonlEventSink
            forms.log(f'Sequence mode:{mode} running.',level="INFO",component='sequence')

            _poll_state = {"last_poll": 0.0}

            def _poll():
                # Host loop concerns: control-command polling + interactive REPL,
                # plus the blocking pause wait. The library runner stays headless;
                # these are injected so it never touches cli/ or app/.
                #
                # Poll the two IPC files (ctrlfile/cmdfile) at wall-clock cadence,
                # not every sim step. Pause/stop/eval arrive at human timescale,
                # but a fixed-step run does millions of steps — reading two JSON
                # files off disk each step dominated the loop. ~100 ms keeps the
                # controls responsive while cutting ~99% of the reads.
                now = time.monotonic()
                if now - _poll_state["last_poll"] >= 0.1:
                    _poll_state["last_poll"] = now
                    check_ctrl_commands(forms)
                    check_cmd(forms, repl_ns)
                while paused:
                    check_ctrl_commands(forms)
                    check_cmd(forms, repl_ns)
                    _poll_state["last_poll"] = time.monotonic()
                    if not paused:
                        break
                    time.sleep(0.1)

            seq = compile_sequence(_zen_runtime if _zen_runtime else forms)
            runner = SequenceRunner(
                forms, seq,
                sink=JsonlEventSink(SEQUENCE_EVENTS_PATH),
                tick=tick_fn,
                write=write,
                poll=_poll,
                after_step=lambda _f: forms.transition.consume(),
            )
            result = runner.run()

            # A routine may request a host mode transition (e.g. -> tvac).
            if result.transition:
                transition(forms, result.transition)
                if result.transition == "tvac":
                    entered_tvac = True

        # --- TVAC mode loop (only if explicitly tvac or transitioned) ---
        if mode == "tvac" or entered_tvac:
            forms.log(f'Sequence mode:{mode} running.',level="INFO",component='sequence')
            if mode == "tvac" and not entered_tvac:
                # Pure-tvac start: no mission Sequence ran, so prime the derive
                # phase here (a transitioned entry was already primed by the
                # mission Sequence's setup segment).
                forms.derive()
            while True:
                check_ctrl_commands(forms)
                check_cmd(forms, repl_ns)
                while paused:
                    check_ctrl_commands(forms)
                    check_cmd(forms, repl_ns)
                    if not paused:
                        break
                    time.sleep(0.1)
                forms.derive()
                tick_fn()
                write(forms)
                forms.record()

        forms.log("Mission loop complete.", level="INFO", component="sequence")
        emit("Mission loop complete.")

    except GracefulExit:
        if forms is not None:
            forms.log("Simulation ended gracefully.", level="INFO", component="sequence")

    except Exception:
        if forms is not None:
            forms.log(
                message=f"error: {traceback.format_exc()}",
                level="DEBUG",
                component="sequence",
            )
        raise

    finally:
        if _zen_runtime:
            _zen_runtime.teardown()
        if forms is not None:
            forms.log("FINAL CLEANUP RUNNING", level="INFO", component="sequence")
            # Flush the final state to streamfile.json (bypasses rate limiter)
            # so the GUI receives the terminal position before the process exits.
            try:
                stream_flush(forms)
            except Exception:
                pass
            # Give the GUI watcher (50 ms poll) time to read the final snapshot
            # before we delete the file and exit.
            time.sleep(0.1)
        cleanup_shared_memory()
        _release_sequence_lock()

    return logs

# --- Entry point ---
if __name__ == "__main__":
    parser = ArgumentParser(description="Run the FORMS processing loop")
    parser.add_argument(
        "--mode",
        choices=["tvac", "laco", "mission", "zen"],
        default="mission",
        help="laco = LACO chamber, no FORMS; tvac = legacy Rigol bench; "
             "mission = propagation loop; zen = .zen mission file",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to mission configuration file (YAML or JSON)",
    )
    args = parser.parse_args()

    try:
        for line in channel(forms=None, mode=args.mode, config_path=args.config):
            print(line)
    except GracefulExit:
        pass

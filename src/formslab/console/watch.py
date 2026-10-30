"""The watch screen: a bare `labcli`.

One screen, repainted every second: the run and where it is, the chamber and
the other instruments as formsLAB shows them, the last lines of the host log,
how the last run ended. Capital-letter hotkeys for End, Fast End, Pause and Resume (lowercase always
types), each End confirmed with the steps it will run. A command line at the bottom takes the
same words as `labcli <command>`. Over SSH it is the bench as the GUI sees it.

It reads only. While no run is going and nothing else reads the chamber, it
starts the same read-only monitor formsLAB runs (formslab.host.monitor) and
lets go when it quits or a run starts.

The frame (console/frame.py) and the key reader (console/keys.py) are the
console's; what a key means is decided here. Without a terminal that can hold
a frame (a pipe, a dumb terminal, a tiny window) it prints the status once and
reads commands line by line.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from formslab import cli, lab
from formslab.console import frame, keys, style
from formslab.console.style import DIM, ERROR, HEADER, INFO, SUCCESS, WARNING

ORDER = cli.ORDER
REPAINT_S = 1.0
LOG_ROWS = 8
RESULT_ROWS = 8          # the last command's output, at most
HOTKEYS = {"E": "end", "F": "end fast", "P": "pause", "R": "resume", "Q": "quit"}
LEGEND = "E End   F Fast End   P Pause   R Resume   Q Quit"
PROMPT = "> "
# What is asked before it is done, and the words that do it.
CONFIRM = {"end": "End the run?", "end fast": "Fast End the run?"}   # the rest ask through cli.question
NOT_HERE = ("gui", "watch")      # a second screen or a server has no place inside this one

console = style.console


# --- what is on screen --------------------------------------------------------------

def snapshot() -> dict:
    return lab.status(LOG_ROWS)


def header(s: dict, width: int = 80) -> Text:
    """The run line and the key legend."""
    t = Text()
    host, run, lost = s["host"], s.get("run") or {}, s.get("lost")
    step = run.get("step")
    if host:
        t.append("● ", SUCCESS)
        t.append(host["plan"], HEADER)
        t.append(f" running since {cli._clock(host['started'])}")
        if step:
            t.append(f" · step {step['index']} of {step['total']}: {step['text']}")
        if run.get("ending") or s.get("action") == "ending":
            t.append(" · ENDING", WARNING)
        elif run.get("paused"):
            t.append(" · PAUSED", WARNING)
    elif s.get("action") == "starting":
        t.append("● starting the run…", WARNING)
    elif s.get("action") == "ending":
        t.append("● ending…", WARNING)
    elif lost:
        t.append("⚠ ", ERROR)
        t.append(f"{lost['plan']} stopped without its end", ERROR)
    else:
        last = s.get("last_run")
        t.append("No run is going." + (f" Last run: {last['plan']}." if last else ""), DIM)
    if not host:
        m = s.get("monitor")
        who = ("this screen is reading the chamber" if m and m.get("mine") else
               "formsLAB or another labcli is reading the chamber" if m else
               "the chamber is not being read")
        t.append(f"   {who}", DIM)
    t.append("\n")
    t.append(LEGEND, DIM)
    return t


def banner(s: dict) -> Text | None:
    """What needs saying above the cards: a lost run, an End or a start in
    progress, or how the last run ended. None when there is nothing."""
    host, lost, ended, run = s["host"], s.get("lost"), s.get("ended"), s.get("run") or {}
    if not host and lost:
        step = run.get("step")
        text = (f"{lost['plan']}, started {cli._clock(lost['started'])}, stopped without its end: its host "
                "was stopped (a crash, Task Manager or a power cut). Its instruments may be as it left them"
                + (f", at step {step['index']} of {step['total']}: {step['text']}" if step else "")
                + ". Press E, or type end, to run its end from a fresh host.")
        return Text(text, style=ERROR)
    if s.get("action") == "ending":
        return Text("Ending: the end script is running, then each instrument's shutdown…", style=WARNING)
    if s.get("action") == "starting":
        return Text("Starting the run…", style=WARNING)
    if not host and ended:
        return Text(cli._ended_text(ended), style=ERROR if ended.get("warnings") else SUCCESS)
    return None


def card(label: str, block: dict) -> Text:
    """One instrument: its name, live or not and how old, then its readings in
    their owner's groups, each group on one line."""
    t = Text()
    live = block.get("live")
    t.append(label.upper(), HEADER)
    t.append(f"  {'live' if live else 'not live'} · {cli._age(block.get('age_s', 0))}", SUCCESS if live else DIM)
    if not live and block.get("reason"):
        t.append(f"\n{block['reason']}", DIM)
    if block.get("pending"):
        t.append("\na command is waiting to be taken", WARNING)
    for g in block.get("groups") or []:
        t.append("\n")
        if g.get("title"):
            t.append(f"{g['title']}: ", INFO)
        t.append(" · ".join(f"{r['name']} {r.get('text', cli._value(r.get('value')))}" for r in g["rows"]))
    return t


def columns(width: int) -> int:
    return 3 if width >= 120 else 2 if width >= 80 else 1


def cards(s: dict, width: int = 80) -> Table:
    """The instruments side by side: three columns on a wide terminal, two on
    an ordinary one, one when narrow."""
    labels = sorted(s["blocks"], key=lambda n: (ORDER.index(n) if n in ORDER else 99, n))
    n = columns(width)
    grid = Table.grid(expand=True, padding=(0, 2))
    for _ in range(n):
        grid.add_column(ratio=1)
    for i in range(0, len(labels), n):
        row = [card(label, s["blocks"][label]) for label in labels[i:i + n]]
        grid.add_row(*row, *([Text()] * (n - len(row))))
    return grid


def log_lines(s: dict) -> Text:
    t = Text("── host.log ──", style=DIM)
    for line in s.get("log") or []:
        t.append("\n" + line, style=DIM)
    t.no_wrap, t.overflow = True, "ellipsis"
    return t


def body(s: dict, width: int = 80, result: Text | None = None) -> Group:
    parts = [result] if result is not None else []
    if s.get("cast_unreadable"):
        parts.append(Text("The CAST file could not be read just now.", style=WARNING))
    parts += [cards(s, width), Text(""), log_lines(s)]
    return Group(*parts)


# --- the terminal ---------------------------------------------------------------------

def clear_screen(*, console_obj=None, output=None, platform_name=None, system_call=None):
    """Clear one interactive screen without emitting controls to captures.

    Rich deliberately suppresses control codes when ``TERM=dumb``. That value
    can be inherited by a Windows launcher even though the attached console is
    interactive, leaving old text in place. Use ``cls`` for that specific
    Windows fallback; redirected output remains append-only.
    """
    console_obj = console_obj or console
    output = output or sys.stdout
    platform_name = platform_name or os.name
    system_call = system_call or os.system

    if not output.isatty():
        return
    if platform_name == "nt" and console_obj.is_dumb_terminal:
        system_call("cls")
        return
    if not console_obj.is_dumb_terminal:
        console_obj.clear()


def _frame_is_available(console_obj=None, output=None) -> bool:
    """Whether this terminal can hold a fixed frame. A redirected stream, a
    dumb terminal or a tiny window takes the line-by-line fallback instead."""
    console_obj = console_obj or console
    output = output or sys.stdout
    try:
        if not output.isatty() or console_obj.is_dumb_terminal:
            return False
        size = console_obj.size
        return size.height >= frame.MIN_HEIGHT and size.width >= frame.MIN_WIDTH
    except Exception:
        return False


def paint(s: dict, *, result: Text | None, scroll: int) -> frame.Frame:
    """Draw the whole screen from the top. The cursor goes home and every row
    is written, so nothing is cleared and nothing flickers."""
    size = console.size
    built = frame.build(console, header=header(s, size.width), banner=banner(s),
                        body=body(s, size.width, result), title="watch",
                        width=size.width, height=size.height, scroll=scroll)
    console.file.write("\x1b[H")
    console.print(built.segments, end="")
    return built


def _echo(prompt: str, buf: keys.LineBuffer) -> None:
    """Rewrite the prompt row in place, cursor included: the frame owns every
    other row, so the line editor is confined to this one."""
    console.file.write("\r\x1b[2K")
    console.print(Text(prompt, style=WARNING if prompt != PROMPT else style.PROMPT), end="", soft_wrap=True)
    console.print(Text(buf.text), end="", soft_wrap=True)
    if buf.trailing:
        console.file.write(f"\x1b[{buf.trailing}D")
    console.file.flush()


# --- doing things -----------------------------------------------------------------------

def dispatch(words: list[str]) -> tuple[str, int]:
    """One `labcli <command>` line, run here with its output captured: what it
    printed, and its exit status."""
    if words and words[0].lower() in NOT_HERE:
        return f"`{words[0]}` is not for this screen; open another terminal for it.", 2
    out = Console(file=io.StringIO(), width=max(40, console.size.width - frame.PANE_CHROME_COLS),
                  force_terminal=False, color_system=None, highlight=False, soft_wrap=True)
    try:
        code = cli.main(words, out)
    except Exception as e:                           # a command's crash must not take the screen down
        return f"✗ {type(e).__name__}: {e}", 1
    return out.file.getvalue().rstrip("\n"), code


class _Worker:
    """One command at a time, off the key loop: a `cast` may wait a minute for
    its result, and the screen must keep moving meanwhile."""

    def __init__(self) -> None:
        self.line: str | None = None
        self.done: tuple[str, int] | None = None

    def busy(self) -> bool:
        return self.line is not None and self.done is None

    def run(self, line: str) -> None:
        self.line, self.done = line, None

        def go() -> None:
            self.done = dispatch(line.split())

        threading.Thread(target=go, name="watch-command", daemon=True).start()

    def take(self) -> Text | None:
        """The finished command's output as a result block, once; None while it runs."""
        if self.line is None:
            return None
        if self.done is None:
            return Text(f"… {self.line}", style=DIM)
        text, code = self.done
        lines = text.splitlines() or ["done" if code == 0 else "failed"]
        shown = lines[-RESULT_ROWS:]
        head = Text(f"{PROMPT}{self.line}\n", style=style.COMMAND)
        return head + Text("\n".join(shown), style=None if code == 0 else ERROR)


class _Quit(Exception):
    pass


def loop(reader) -> int:
    """Keys in, screen out. On every idle tick the size is checked and, once a
    second, the screen is repainted; the typed line survives each repaint. A
    hotkey is a capital letter and acts only while the line is empty, so every
    command can still be typed."""
    buf = keys.LineBuffer()
    worker = _Worker()
    state = {"scroll": 0, "confirm": None, "note": None, "frame": None}

    def prompt() -> str:
        return f"{state['confirm'][0]} y/n " if state["confirm"] else PROMPT

    def result() -> Text | None:
        pending = worker.take()
        return pending if pending is not None else state["note"]

    def repaint() -> None:
        state["frame"] = paint(snapshot(), result=result(), scroll=state["scroll"])
        _echo(prompt(), buf)

    def note(text: str, style_=None) -> None:
        worker.line, worker.done = None, None
        state["note"] = Text(text, style=style_)

    def ask(words: list[str]) -> None:
        """Enter the confirm state for `end` or `end fast`, listing its steps."""
        key = " ".join(words)
        try:
            steps = lab.end_steps()
        except lab.LabError as e:
            note(f"✗ {e}", ERROR)
            return
        what = ("sends, in order, without waiting to confirm each step" if key == "end fast"
                else "runs, in order")
        lines = [f"{CONFIRM[key]} This {what}:"] + [f"  {s}" for s in steps] + ["then each instrument's shutdown."]
        if key == "end fast":
            lines.append("Check the chamber at the HMI afterwards.")
        note("\n".join(lines), WARNING)
        state["confirm"] = (CONFIRM[key], words)

    def do(words: list[str]) -> None:
        """A word the screen handles itself: the hotkeys and their typed forms."""
        key = " ".join(words)
        if key in ("quit", "exit", "q"):
            raise _Quit()
        if key in CONFIRM:
            ask(words)
            return
        if key in ("pause", "resume"):
            try:
                lab.ctrl(key)
            except lab.LabError as e:
                note(f"✗ {e}", ERROR)
                return
            lab.audit(cli._who(), key)
            note(f"✔ {key} sent")
            return
        if worker.busy():
            note(f"still doing: {worker.line}", WARNING)
            return
        question = cli.question(words)
        if question is not None and "yes" not in (w.lower() for w in words):
            note(question, WARNING)
            state["confirm"] = (question, words + ["yes"])
            return
        state["note"] = None
        worker.run(key)

    def confirmed(words: list[str]) -> None:
        if words[0] != "end":                        # kill, supplies off, power: labcli's own, with yes
            state["note"] = None
            worker.run(" ".join(words))
            return
        fast = words == ["end", "fast"]
        try:
            r = lab.end(fast)
        except lab.LabError as e:
            note(f"✗ {e}", ERROR)
            return
        lab.audit(cli._who(), "end fast" if fast else "end")
        note(f"… ending {r['plan']}" + (" with Fast End" if fast else ""), WARNING)

    size = console.size
    clear_screen()
    repaint()
    next_paint = time.monotonic() + REPAINT_S
    try:
        while True:
            token = reader.poll(0.05)
            now = time.monotonic()
            if token is None:
                if console.size != size:
                    size = console.size
                    clear_screen()
                    repaint()
                    next_paint = now + REPAINT_S
                elif now >= next_paint:
                    repaint()
                    next_paint = now + REPAINT_S
                continue
            if token == keys.INTERRUPT or (token == keys.EOF and not buf.text):
                raise _Quit()
            if state["confirm"]:
                question, words = state["confirm"]
                state["confirm"] = None
                if token == "y":
                    confirmed(words)
                else:
                    note("Not done.", DIM)
                repaint()
                continue
            if token == keys.ENTER:
                line, buf = buf.text.strip(), keys.LineBuffer()
                if line:
                    words = line.split()
                    if " ".join(words[:2]).lower() in CONFIRM or words[0].lower() in ("pause", "resume", "quit", "exit"):
                        do([w.lower() for w in words])
                    else:
                        do(words)
                repaint()
                continue
            if token in keys.SCROLL_KEYS:
                built = state["frame"]
                if built is not None and built.paged:
                    page = max(1, built.body_rows - 1)
                    step = {keys.LINE_UP: -1, keys.LINE_DOWN: 1, keys.PAGE_UP: -page, keys.PAGE_DOWN: page}[token]
                    state["scroll"] = max(0, state["scroll"] + step)
                    repaint()
                continue
            if token in keys.EDIT_KEYS:
                buf.apply(token)
                _echo(prompt(), buf)
                continue
            if len(token) == 1 and token.isprintable():
                if not buf.text and token in HOTKEYS:
                    do(HOTKEYS[token].split())
                    repaint()
                    continue
                buf.insert(token)
                _echo(prompt(), buf)
    except _Quit:
        console.file.write("\n")
        console.file.flush()
        return 0


def fallback() -> int:
    """No frame: the status once, then commands line by line, as `labcli <command>`."""
    cli.main(["status"], console)
    console.print("Commands as for `labcli <command>`; `status` again for the latest; q to quit.",
                  style=DIM, markup=False)
    while True:
        try:
            line = console.input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if line in ("q", "quit", "exit"):
            return 0
        if line:
            words = line.split()
            if words[0].lower() in NOT_HERE:
                console.print(f"`{words[0]}` is not for this screen; open another terminal for it.", markup=False)
                continue
            cli.main(words, console)


def _start_monitor():
    """Read the chamber from here while no run is going, unless another
    process (formsLAB, another labcli) already does."""
    from formslab.host.monitor import start_if_free
    return start_if_free()


def _watch(no_monitor: bool) -> int:
    from formslab.state import ensure_runtime_files
    ensure_runtime_files()
    reader = keys.open_reader() if _frame_is_available() else None
    if reader is None:
        return fallback()
    monitor = None if no_monitor else _start_monitor()
    try:
        with reader:
            return loop(reader)
    finally:
        if monitor is not None:
            monitor.stop()


def main(argv: list[str]) -> int:
    """`labcli`, `labcli watch`, with `--demo` (a simulated bench of its own)
    and `--no-monitor` (leave the chamber's controller to scripts/)."""
    flags = {a for a in argv if a != "watch"}
    unknown = flags - {"--demo", "--no-monitor"}
    if unknown:
        console.print(f"✗ unknown option {', '.join(sorted(unknown))}; `labcli help` lists the commands",
                      markup=False)
        return cli.USAGE
    if "--demo" in flags:
        from formslab.demo import bench
        with bench() as b:
            console.print(f"A simulated bench in {b.root}; nothing real is touched. Its files go when you quit.",
                          style=DIM, markup=False)
            return _watch("--no-monitor" in flags)
    return _watch("--no-monitor" in flags)

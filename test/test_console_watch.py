"""The watch screen (formslab.console.watch): what it shows, what its keys do,
and how it degrades without a terminal. Driven with scripted keys and a fake
status; no terminal, no instrument."""
import io
import time

import pytest
from rich.console import Console, ConsoleDimensions
from rich.text import Text

from formslab import lab
from formslab.console import keys, style, watch

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-03T12:00:00+00:00", "output": "/x"}


def block(live, groups=(), age=2.0, **more):
    return {"status": {}, "groups": list(groups), "pending": False, "live": live, "age_s": age,
            "reason": "" if live else "no run is going", **more}


VALVES = {"title": "Valves", "part": "valve",
          "rows": [{"key": "rough", "name": "Vacuum valve", "value": False, "text": "Closed"},
                   {"key": "vent", "name": "Vent valve", "value": False, "text": "Closed"}]}


def status(host=None, **over):
    s = {"now": 0.0, "host": host, "run": None, "last_run": None, "action": None,
         "blocks": {"hvc": block(bool(host), [VALVES]), "psu1": block(False, age=7200.0)},
         "cast_unreadable": False, "log": ["12:00:00 [INFO] [sequence] step 1"],
         "ended": None, "lost": None, "monitor": None}
    s.update(over)
    return s


def rendered(renderable, width=80):
    console = Console(file=io.StringIO(), width=width, height=40, force_terminal=True, color_system=None)
    console.print(renderable)
    return console.file.getvalue()


# --- what it shows -------------------------------------------------------------------------

def test_the_header_says_where_the_run_is():
    s = status(HOST, run={"step": {"index": 3, "total": 7, "text": "hvc rough open"}, "paused": True})
    text = rendered(watch.header(s))
    assert "tvac" in text and "running since" in text and "step 3 of 7: hvc rough open" in text
    assert "PAUSED" in text and "E End" in text and "Q Quit" in text


def test_the_header_with_no_run_says_who_reads_the_chamber():
    assert "the chamber is not being read" in rendered(watch.header(status()))
    assert "this screen is reading the chamber" in rendered(watch.header(status(monitor={"pid": 1, "mine": True})))
    assert "formsLAB or another labcli" in rendered(watch.header(status(monitor={"pid": 2, "mine": False})))
    assert "Last run: tvac." in rendered(watch.header(status(last_run=HOST)))
    assert "stopped without its end" in rendered(watch.header(status(lost=HOST)))


def test_the_banner_says_what_needs_saying():
    assert watch.banner(status()) is None
    lost = watch.banner(status(lost=HOST, run={"step": {"index": 2, "total": 5, "text": "hvc pump on"}}))
    assert "stopped without its end" in lost.plain and "step 2 of 5: hvc pump on" in lost.plain
    assert "Press E" in lost.plain and lost.style == style.ERROR
    ended = {"plan": "tvac", "when": "2026-10-03T12:30:00+00:00", "how": "ended", "fast": False,
             "chamber": "Chamber left: sealed, 700 Torr, pump off, turbo off, zones off", "warnings": []}
    clean = watch.banner(status(ended=ended))
    assert "tvac ended at" in clean.plain and "Chamber left: sealed" in clean.plain and clean.style == style.SUCCESS
    failed = watch.banner(status(ended={**ended, "warnings": ["hvc stop: not confirmed"]}))
    assert "not done: hvc stop" in failed.plain and failed.style == style.ERROR
    assert "Ending:" in watch.banner(status(action="ending")).plain
    assert watch.banner(status(HOST, ended=ended)) is None            # a run going: the old end is history


@pytest.mark.parametrize("width, columns", [(120, 3), (80, 2), (40, 1)])
def test_the_cards_take_the_columns_the_width_allows(width, columns):
    assert watch.columns(width) == columns
    text = rendered(watch.cards(status(HOST), width), width)
    assert "HVC  live" in text and "Valves:" in text and "Vacuum valve Closed" in text
    assert "PSU1  not live" in text and "2.0 h" in text and "no run is going" in text


@pytest.mark.parametrize("width", [120, 80, 40])
def test_the_frame_holds_its_rows_at_every_width(monkeypatch, width):
    console = Console(file=io.StringIO(), width=width, height=24, force_terminal=True, color_system=None)
    monkeypatch.setattr(watch, "console", console)
    built = watch.paint(status(HOST, lost=None), result=Text("✔ hvc ← {}: done"), scroll=0)
    painted = console.file.getvalue()
    assert built.rows == 23 and painted.count("\n") == 23
    assert "host.log" in painted or built.paged


# --- the keys ------------------------------------------------------------------------------

class _Reader:
    def __init__(self, tokens):
        self.tokens = list(tokens)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def poll(self, timeout):
        return self.tokens.pop(0) if self.tokens else keys.INTERRUPT


class _TtyIO(io.StringIO):
    def isatty(self):
        return True


def drive(monkeypatch, tokens, s=None, sizes=None):
    """Run the loop over `tokens` (None is an idle tick); what it painted and printed."""
    class _Console(Console):
        def __init__(self):
            super().__init__(file=_TtyIO(), width=80, height=24, force_terminal=True, color_system=None)
            self.reads = 0

        @property
        def size(self):
            self.reads += 1
            if sizes:
                return ConsoleDimensions(*sizes[min(self.reads - 1, len(sizes) - 1)])
            return ConsoleDimensions(80, 24)

    console = _Console()
    monkeypatch.setattr(watch, "console", console)
    monkeypatch.setattr(watch, "REPAINT_S", 0.0)
    monkeypatch.setattr(watch, "snapshot", lambda: s or status())
    cleared = []
    monkeypatch.setattr(watch, "clear_screen", lambda *a, **k: cleared.append(1))
    painted = []
    real = watch.paint
    monkeypatch.setattr(watch, "paint", lambda st, **k: (painted.append(k), real(st, **k))[1])
    code = watch.loop(_Reader(tokens))
    return code, painted, console.file.getvalue(), cleared


@pytest.fixture
def quiet(monkeypatch):
    """Nothing reaches the lab unless a test says so."""
    calls = {"end": [], "ctrl": [], "audit": [], "lines": []}
    monkeypatch.setattr(lab, "end", lambda fast=False: calls["end"].append(fast) or {"state": "ending", "plan": "tvac"})
    monkeypatch.setattr(lab, "ctrl", lambda c: calls["ctrl"].append(c) or {"sent": c})
    monkeypatch.setattr(lab, "audit", lambda who, what, where="labcli": calls["audit"].append(what))
    monkeypatch.setattr(lab, "end_steps", lambda: ["psu1 ch1 off", "hvc stop"])
    monkeypatch.setattr(watch._Worker, "run", lambda self, line: calls["lines"].append(line))
    return calls


def test_typing_survives_a_repaint_tick(monkeypatch, quiet):
    tokens = ["r", None, "u", None, "n", " ", "t", "v", "a", "c", keys.ENTER]
    code, painted, out, _ = drive(monkeypatch, tokens)
    assert code == 0 and len(painted) >= 3                      # the idle ticks repainted
    assert quiet["lines"] == ["run tvac"]


def test_end_asks_first_and_n_sends_nothing(monkeypatch, quiet):
    _, _, out, _ = drive(monkeypatch, ["E", "n"])
    assert "End the run? y/n" in out and "psu1 ch1 off" in out and "then each instrument's shutdown" in out
    assert quiet["end"] == [] and "Not done." in out


def test_end_then_y_ends_once_and_is_noted(monkeypatch, quiet):
    _, _, out, _ = drive(monkeypatch, ["E", "y"])
    assert quiet["end"] == [False] and quiet["audit"] == ["end"] and "ending tvac" in out


def test_fast_end_then_y_ends_fast(monkeypatch, quiet):
    _, _, out, _ = drive(monkeypatch, ["F", "y"])
    assert quiet["end"] == [True] and quiet["audit"] == ["end fast"]
    assert "Fast End the run? y/n" in out and "Check the chamber at the HMI" in out


def test_typed_end_fast_asks_the_same_way(monkeypatch, quiet):
    drive(monkeypatch, list("end fast") + [keys.ENTER, "y"])
    assert quiet["end"] == [True] and quiet["lines"] == []


def test_pause_and_resume_go_straight_through(monkeypatch, quiet):
    drive(monkeypatch, ["P", "R"])
    assert quiet["ctrl"] == ["pause", "resume"] and quiet["audit"] == ["pause", "resume"]


def test_lowercase_always_types_so_every_command_can_be_typed(monkeypatch, quiet):
    drive(monkeypatch, list("end") + [keys.ENTER, "n", *"plans", keys.ENTER])
    assert quiet["end"] == [] and quiet["lines"] == ["plans"]       # typed end asked and was declined


def test_a_hotkey_letter_with_text_in_the_line_is_text(monkeypatch, quiet):
    drive(monkeypatch, ["x", "E", "P", keys.ENTER])
    assert quiet["lines"] == ["xEP"] and quiet["end"] == [] and quiet["ctrl"] == []


def test_edits_apply_before_the_line_is_sent(monkeypatch, quiet):
    drive(monkeypatch, list("statuss") + [keys.BACKSPACE, keys.HOME, "x", keys.ENTER])
    assert quiet["lines"] == ["xstatus"]


def test_q_ctrl_c_and_ctrl_d_leave(monkeypatch, quiet):
    assert drive(monkeypatch, ["Q"])[0] == 0
    assert drive(monkeypatch, [keys.INTERRUPT])[0] == 0
    assert drive(monkeypatch, [keys.EOF])[0] == 0
    assert drive(monkeypatch, ["a", keys.EOF, keys.INTERRUPT])[0] == 0   # EOF on a line is not quit
    assert quiet["lines"] == []


def test_a_second_command_while_one_runs_is_refused(monkeypatch, quiet):
    monkeypatch.setattr(watch._Worker, "busy", lambda self: True)
    monkeypatch.setattr(watch._Worker, "take", lambda self: None)
    _, _, out, _ = drive(monkeypatch, list("status") + [keys.ENTER])
    assert "still doing" in out and quiet["lines"] == []


def test_scroll_keys_page_the_body_and_never_reach_the_line(monkeypatch, quiet):
    tall = status(log=[f"line {i}" for i in range(100)])
    _, painted, _, _ = drive(monkeypatch, [keys.PAGE_DOWN, keys.LINE_DOWN, "x", keys.ENTER], s=tall)
    assert painted[-1]["scroll"] > 0 and quiet["lines"] == ["x"]


def test_a_resize_clears_and_repaints(monkeypatch, quiet):
    _, _, _, cleared = drive(monkeypatch, [None, None], sizes=[(80, 24), (80, 24), (100, 30), (100, 30)])
    assert len(cleared) >= 2


def test_the_lost_run_banner_and_the_ended_banner_are_painted(monkeypatch, quiet):
    _, _, out, _ = drive(monkeypatch, [None], s=status(lost=HOST))
    assert "stopped without its end" in out


# --- commands typed on the line ------------------------------------------------------------

def test_dispatch_runs_a_labcli_command_and_captures_its_output(monkeypatch):
    monkeypatch.setattr(lab, "status", lambda log_lines=40: status())
    monkeypatch.setattr(watch, "console", Console(file=io.StringIO(), width=80, height=24))
    text, code = watch.dispatch(["status"])
    assert code == 1 and "No run is going" in text
    text, code = watch.dispatch(["gui"])
    assert code == 2 and "not for this screen" in text
    text, code = watch.dispatch(["check", "tvac"])
    assert code == 0 and "sequence" in text                      # the check's own print goes to the screen


def test_the_result_block_shows_the_command_then_its_last_lines():
    w = watch._Worker()
    assert w.take() is None
    w.line, w.done = "status", ("\n".join(f"row {i}" for i in range(20)), 0)
    shown = w.take().plain
    assert shown.startswith("> status\n") and "row 19" in shown and "row 0" not in shown
    w.done = ("", 1)
    assert "failed" in w.take().plain


# --- without a terminal, and the monitor ------------------------------------------------------

def test_without_a_frame_the_status_is_printed_once_and_lines_are_read(monkeypatch):
    monkeypatch.setattr(lab, "status", lambda log_lines=40: status())
    console = Console(file=io.StringIO(), width=80, color_system=None)
    console.input = lambda prompt: next(lines)
    lines = iter(["status", "gui", "q"])
    monkeypatch.setattr(watch, "console", console)
    assert watch.fallback() == 0
    out = console.file.getvalue()
    assert out.count("No run is going") == 2 and "not for this screen" in out


def test_the_screen_reads_the_chamber_unless_asked_not_to(monkeypatch):
    from formslab.host import monitor as monitor_module
    stopped, started = [], []

    class Monitor:
        def stop(self):
            stopped.append(1)

    monkeypatch.setattr(monitor_module, "start_if_free", lambda: started.append(1) or Monitor())
    monkeypatch.setattr(watch, "_frame_is_available", lambda *a, **k: True)
    monkeypatch.setattr(watch.keys, "open_reader", lambda *a, **k: _Reader([keys.INTERRUPT]))
    monkeypatch.setattr(watch, "loop", lambda reader: 0)
    assert watch.main([]) == 0 and started == [1] and stopped == [1]
    assert watch.main(["watch", "--no-monitor"]) == 0 and started == [1]
    assert watch.main(["--bogus"]) == 2


def test_without_a_terminal_main_takes_the_fallback(monkeypatch):
    monkeypatch.setattr(watch, "_frame_is_available", lambda *a, **k: False)
    monkeypatch.setattr(watch, "fallback", lambda: 7)
    assert watch.main([]) == 7


# --- clearing the screen ----------------------------------------------------------------------

class _Output:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


class _Console:
    def __init__(self, is_dumb_terminal):
        self.is_dumb_terminal = is_dumb_terminal
        self.clear_count = 0

    def clear(self):
        self.clear_count += 1


def test_clear_screen_uses_cls_for_an_interactive_dumb_windows_terminal():
    calls, terminal = [], _Console(is_dumb_terminal=True)
    watch.clear_screen(console_obj=terminal, output=_Output(True), platform_name="nt", system_call=calls.append)
    assert calls == ["cls"] and terminal.clear_count == 0


def test_clear_screen_uses_rich_once_for_a_capable_terminal():
    calls, terminal = [], _Console(is_dumb_terminal=False)
    watch.clear_screen(console_obj=terminal, output=_Output(True), platform_name="nt", system_call=calls.append)
    assert calls == [] and terminal.clear_count == 1


def test_clear_screen_does_nothing_for_redirected_output():
    calls, terminal = [], _Console(is_dumb_terminal=False)
    watch.clear_screen(console_obj=terminal, output=_Output(False), platform_name="nt", system_call=calls.append)
    assert calls == [] and terminal.clear_count == 0

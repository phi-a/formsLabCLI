#!/usr/bin/env python3
"""The `labcli` entry point: a tab-switching REPL over the lab hardware.

The screen is a fixed frame, not a transcript: each command repaints the same
regions in the same rows (see `formslab/console/frame.py`) rather than adding to
a scroll. A redirected stream still gets append-only text.

Previously bootstrapped by `setenv.setup_environment()`, which inserted five
directories onto `sys.path` and chdir'd into the FORMS checkout. `formslab` is
an installed package now, so imports resolve on their own and the console runs
from any working directory.
"""
import os
import sys
import io
from contextlib import redirect_stdout

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from rich import print
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from formslab.console import style
from formslab.console import frame
from formslab.console import keys
from formslab.state import ensure_runtime_files
from formslab.console.sessions.base import CLIResult
from formslab.console.resources import handle_resources_command
from formslab.console.cmd_browser import handle_cmd_command
from formslab.console.console_help import build_command_index


def _make_factory(module_name: str, class_name: str):
    def _create():
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
        return cls()
    return _create
# ─── Tab Configuration ─────────────────────────────────────
TAB_FACTORIES = {
    "ctrl": _make_factory("formslab.console.sessions.ctrl", "CtrlSession"),
    "cast": _make_factory("formslab.console.sessions.cast", "CastSession"),
    "log": _make_factory("formslab.console.sessions.log", "LogSession"),
    "psu": _make_factory("formslab.console.sessions.psu", "PSUSession"),
}
TABS = list(TAB_FACTORIES.keys())

active_sessions = {}
current_tab = "ctrl"
console = style.console


def clear_screen(
    *,
    console_obj=None,
    output=None,
    platform_name=None,
    system_call=None,
):
    """Clear one interactive screen without emitting controls to captures.

    Rich deliberately suppresses control codes when ``TERM=dumb``. That value
    can be inherited by a Windows launcher even though the attached console is
    interactive, leaving old dashboard text in place. Use ``cls`` for that
    specific Windows fallback; redirected output remains append-only.
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


def initial_tab_from_argv(argv):
    for arg in argv[1:]:
        if arg.startswith("--"):
            tab = arg.lstrip("-").lower()
            if tab in TABS:
                return tab
    return "ctrl"


def _make_unavailable_session(tab: str, exc: Exception):
    message = f"X {tab} tab unavailable: {exc}"

    class _UnavailableSession:
        def help(self):
            return CLIResult(content=Text(message, style=style.ERROR), suppress_prompt=True)

        def handle(self, _raw):
            return CLIResult(content=Text(message, style=style.ERROR))

        def prompt(self):
            return f"{tab}> "

    return _UnavailableSession()


def get_session(tab):
    if tab not in active_sessions:
        try:
            active_sessions[tab] = TAB_FACTORIES[tab]()
        except Exception as exc:
            active_sessions[tab] = _make_unavailable_session(tab, exc)
    return active_sessions[tab]

def render_tab_bar():
    """The tab strip, with the current tab filled.

    The styles are Style objects, not interpolated strings: a Style dropped into
    an f-string renders its repr, which Rich cannot parse and discards without
    complaint -- that is how the active tab came to be drawn black on black.
    """
    bar = Text()
    for tab in TABS:
        chip = style.TAB_ACTIVE if tab == current_tab else style.TAB_INACTIVE
        bar.append(f" {tab.upper()} ", style=chip)
        bar.append(" ")
    # `style=BG` used to set a black foreground for everything in the panel,
    # border included, which is why both the box and the active tab vanished.
    return Panel(bar, border_style=style.PANEL_BORDER, padding=(0, 1))

def _strip_panel(renderable):
    """Remove Panel borders and replace with header text if present."""
    if isinstance(renderable, Panel):
        content = _strip_panel(renderable.renderable)
        header = None
        if renderable.title:
            title = renderable.title
            if not isinstance(title, Text):
                title = Text(str(title), style=style.HEADER)
            header = title
        if header:
            if isinstance(content, Group):
                return Group(header, *content.renderables)
            return Group(header, content)
        return content
    return renderable

# ─── Frame state ───────────────────────────────────────────
# What is currently on screen, and where the pager sits in it. Repainting at a
# new scroll offset must not re-run the command that produced the output, so the
# renderables are kept rather than the rendered lines.
_last_renderables = ()
_last_frame = None
_scroll = 0

_TYPED_SCROLL = {
    "more": keys.PAGE_DOWN,
    "back": keys.PAGE_UP,
    "top": "top",
    "bottom": "bottom",
}


def _frame_is_available(console_obj=None, output=None):
    """Whether this terminal can hold a fixed frame.

    A redirected stream keeps the append-only path: a pipe or a test capture
    wants the text, not a screenful of padding around it.
    """
    console_obj = console_obj or console
    output = output or sys.stdout
    try:
        if not output.isatty():
            return False
        size = console_obj.size
        return size.height >= frame.MIN_HEIGHT and size.width >= frame.MIN_WIDTH
    except Exception:
        return False


def _session_banner():
    """The current tab's status region, or None when it publishes none.

    `ConsoleSession.banner()` has always existed; the scrolling console never
    drew it. It is the status region now, so CAST's panel stays on screen while
    you work instead of being replaced by the next command's output.
    """
    try:
        session = get_session(current_tab)
        if not getattr(session, "live_status", False):
            return None
        banner = session.banner()
    except Exception:
        return None
    if isinstance(banner, CLIResult):
        banner = banner.content
    return _strip_panel(banner) if banner is not None else None


def _paint():
    """Draw the current content at the current scroll offset."""
    global _last_frame
    valid = [r for r in _last_renderables if r is not None]
    if not valid:
        valid = [Text("Ready.", style=style.TEXT)]
    body = [_strip_panel(r) for r in valid]

    clear_screen()
    if not _frame_is_available():
        _last_frame = None
        console.print(render_tab_bar())
        for r in body:
            console.print(r)
        return

    size = console.size
    _last_frame = frame.build(
        console,
        tab_bar=render_tab_bar(),
        banner=_session_banner(),
        body=Group(*body),
        title=current_tab,
        width=size.width,
        height=size.height,
        scroll=_scroll,
    )
    # `end=""` leaves the cursor on the row the frame deliberately left free,
    # which is where the REPL then writes the prompt.
    console.print(_last_frame.segments, end="")


def render_output(*renderables):
    """Replace the content region, from the top."""
    global _last_renderables, _scroll
    _last_renderables = renderables
    _scroll = 0
    _paint()


def scroll_view(where):
    """Move the window over content already on screen.

    Returns False when nothing is being paged, so a typed word falls through to
    the tab in case it owns a command of the same name.
    """
    global _scroll
    if _last_frame is None or not _last_frame.paged:
        return False
    # A row of overlap between pages keeps your place readable.
    page = max(1, _last_frame.body_rows - 1)
    if where == "top":
        _scroll = 0
    elif where == "bottom":
        _scroll = _last_frame.body_total      # build() clamps it to the last page
    else:
        step = {keys.LINE_UP: -1, keys.LINE_DOWN: 1,
                keys.PAGE_UP: -page, keys.PAGE_DOWN: page}.get(where)
        if step is None:
            return False
        _scroll = max(0, _scroll + step)
    _paint()
    return True


def _echo(prompt, buf):
    """Rewrite the prompt row in place, cursor included.

    The frame owns every other row, so the line editor is confined to this one:
    return to column 0, erase it, redraw, then walk the cursor back to where it
    sits in the text.
    """
    console.file.write("\r\x1b[2K")
    console.print(prompt, end="", soft_wrap=True)
    console.print(Text(buf.text), end="", soft_wrap=True)
    if buf.trailing:
        console.file.write(f"\x1b[{buf.trailing}D")
    console.file.flush()


def read_command(prompt):
    """Read one line, a key at a time, repainting the frame between keys.

    `input()` would block until Enter, which is why the window used to ignore a
    resize and why moving the content pane meant typing a word. Polling for keys
    leaves an idle gap on every tick, and that gap is where the resize check and
    the scroll keys live.

    Falls back to `console.input()` wherever keys cannot be read -- a pipe, a
    capture, a dumb terminal -- so redirected runs behave exactly as before.
    """
    if not _frame_is_available() or console.is_dumb_terminal:
        return console.input(prompt)
    reader = keys.open_reader()
    if reader is None:
        return console.input(prompt)

    buf = keys.LineBuffer()
    size = console.size
    with reader:
        _echo(prompt, buf)
        while True:
            token = reader.poll(0.05)

            if token is None:
                # Idle. Rich re-queries the terminal on every `size` access, so
                # this is the whole of the resize handling: notice, repaint.
                if console.size != size:
                    size = console.size
                    _paint()
                    _echo(prompt, buf)
                continue

            if token == keys.ENTER:
                console.file.write("\n")
                console.file.flush()
                return buf.text
            if token == keys.INTERRUPT:
                raise KeyboardInterrupt
            if token == keys.EOF:
                if not buf.text:
                    raise EOFError
                continue

            if token in keys.SCROLL_KEYS:
                if scroll_view(token):
                    _echo(prompt, buf)      # the repaint wiped the prompt row
                continue

            if token in keys.EDIT_KEYS:
                buf.apply(token)
            elif len(token) == 1 and token.isprintable():
                buf.insert(token)
            else:
                continue
            _echo(prompt, buf)

def capture_stdout(func, *args, **kwargs):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        func(*args, **kwargs)
    return Text(buffer.getvalue(), style=style.TEXT)

def main():
    global current_tab
    # The CTRL command table and CAST device state are regenerated from code
    # defaults when absent. `setenv.setup_environment()` used to do this at
    # import; an installed console does it on the way into the REPL instead.
    ensure_runtime_files()

    current_tab = initial_tab_from_argv(sys.argv)
    session = get_session(current_tab)

    # ─── Initial help ───────────────────────────────────────
    initial_res = session.help()                # returns CLIResult
    render_output(initial_res.content)          # unwrap its .content

    while True:
        try:
            raw = read_command(session.prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        # ─── Exit ──────────────────────────────────────────
        if cmd in ("--resources", "resources"):
            res = handle_resources_command(raw)
            render_output(res.content)
            continue

        if cmd in ("--cmd", "cmd", "--tree", "tree"):
            mapped = raw
            if cmd in ("--tree", "tree"):
                mapped = ("cmd " + " ".join(parts[1:])).strip()
            res = handle_cmd_command(mapped)
            render_output(res.content)
            continue

        if cmd in ("--exit", "exit", "quit"):
            break

        # ─── Pager ─────────────────────────────────────────
        # PgUp/PgDn/arrows do this without Enter; these stay for the fallback
        # path, where there is no key reader. `end` is deliberately absent -- it
        # is a CTRL command that terminates the running sequence.
        if cmd in _TYPED_SCROLL and len(parts) == 1:
            if scroll_view(_TYPED_SCROLL[cmd]):
                continue

        # ─── Help ──────────────────────────────────────────
        if cmd in ("--help", "help"):
            if len(parts) > 1 and parts[1].lower() in ("cmd", "commands", "all"):
                help_res = build_command_index(TAB_FACTORIES, get_session)
                render_output(help_res.content)
                continue
            help_res = session.help()
            render_output(help_res.content)
            continue

        # ─── Tab switch ────────────────────────────────────
        if cmd in tuple(f"--{name}" for name in TABS):
            tab = cmd.lstrip("-")
            if tab in TABS:
                current_tab = tab
                session = get_session(current_tab)
                help_res = session.help()
                render_output(help_res.content)
            else:
                render_output(Text(f"✗ Unknown tab: {tab}", style=style.ERROR))
            continue

        # ─── Actual command dispatch ────────────────────────
        try:
            result = session.handle(raw)
        except Exception as exc:
            result = CLIResult(
                Text(f"✗ {current_tab.upper()} error: {exc}", style=style.ERROR)
            )

        # ─── Unwrap CLIResult ───────────────────────────────
        if isinstance(result, CLIResult):
            render_output(result.content)

        # ─── String ──────────────────────────────────────────
        elif isinstance(result, str):
            render_output(Text(result.strip(), style=style.TEXT))

        # ─── Nothing ─────────────────────────────────────────
        elif result is None:
            render_output(None)

        # ─── Rich renderable (Text/Panel/Group) ────────────
        else:
            render_output(result)

if __name__ == "__main__":
    main()

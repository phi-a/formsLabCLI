"""The console shell: tab routing, screen clearing, and the REPL loop.

Was `test_flatsat_console.py` in the FORMS repository. The name was always a
misnomer -- only two of its cases were about the flatsat tab and the rest cover
the router and `main()`. Those two are replaced by
`test_flatsat_tab_is_not_offered`, which fences the extraction decision: the
flatsat tab wrapped `forms.flatsat`, a subsystem that stayed in FORMS.
"""

from formslab import app
from formslab.app import clear_screen, initial_tab_from_argv
from formslab.console.console_router import ConsoleRouter
from formslab.console.sessions.base import CLIResult


class _StubSession:
    def __init__(self, name):
        self.name = name

    def help(self):
        return CLIResult(f"{self.name} help")

    def handle(self, raw, payload=None):
        return CLIResult(f"{self.name}: {raw}")

    def prompt(self):
        return f"{self.name}> "


def test_flatsat_tab_is_not_offered():
    """It wrapped `forms.flatsat`, which stayed in the FORMS repository."""
    router = ConsoleRouter()

    assert "flatsat" not in router.tabs
    assert "flatsat" not in app.TAB_FACTORIES
    assert initial_tab_from_argv(["labcli", "--flatsat"]) == "ctrl"


def test_analysis_is_not_available_as_console_tab():
    router = ConsoleRouter()

    assert "analysis" not in router.tabs


def test_hardware_tabs_are_offered():
    """The four tabs a bare lab install must be able to reach."""
    router = ConsoleRouter()

    for tab in ("ctrl", "cast", "log", "psu"):
        assert tab in router.tabs


def test_psu_remains_manual_console_tab():
    router = ConsoleRouter(
        tab_factories={
            "ctrl": lambda: _StubSession("ctrl"),
            "psu": lambda: _StubSession("psu"),
        },
        initial_tab="ctrl",
    )

    result = router.route("--psu")

    assert result.tab_changed is True
    assert router.current_tab == "psu"


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
    calls = []
    terminal = _Console(is_dumb_terminal=True)

    clear_screen(
        console_obj=terminal,
        output=_Output(is_tty=True),
        platform_name="nt",
        system_call=calls.append,
    )

    assert calls == ["cls"]
    assert terminal.clear_count == 0


def test_clear_screen_uses_rich_once_for_a_capable_terminal():
    calls = []
    terminal = _Console(is_dumb_terminal=False)

    clear_screen(
        console_obj=terminal,
        output=_Output(is_tty=True),
        platform_name="nt",
        system_call=calls.append,
    )

    assert calls == []
    assert terminal.clear_count == 1


def test_clear_screen_does_nothing_for_redirected_output():
    calls = []
    terminal = _Console(is_dumb_terminal=False)

    clear_screen(
        console_obj=terminal,
        output=_Output(is_tty=False),
        platform_name="nt",
        system_call=calls.append,
    )

    assert calls == []
    assert terminal.clear_count == 0


def test_labcli_does_not_echo_an_entered_command_twice(monkeypatch):
    class Session(_StubSession):
        def handle(self, raw, payload=None):
            return CLIResult(f"result: {raw}")

    class Console:
        def __init__(self):
            self.commands = iter(["status", "exit"])
            self.printed = []

        def input(self, prompt):
            return next(self.commands)

        def print(self, value):
            self.printed.append(str(value))

    terminal = Console()
    rendered = []
    monkeypatch.setattr(app, "console", terminal)
    monkeypatch.setattr(app, "get_session", lambda _tab: Session("ctrl"))
    monkeypatch.setattr(app, "ensure_runtime_files", lambda: None)
    monkeypatch.setattr(app, "render_output", lambda *items: rendered.append(items))

    app.main()

    assert "ctrl> status" not in terminal.printed
    assert rendered[-1] == ("result: status",)


def test_labcli_keeps_prompt_alive_after_session_error(monkeypatch):
    class Session(_StubSession):
        def handle(self, raw, payload=None):
            raise RuntimeError("temporary device failure")

    class Console:
        def __init__(self):
            self.commands = iter(["status", "exit"])
            self.prompts = []

        def input(self, prompt):
            self.prompts.append(prompt)
            return next(self.commands)

        def print(self, _value):
            pass

    terminal = Console()
    rendered = []
    monkeypatch.setattr(app, "console", terminal)
    monkeypatch.setattr(app, "get_session", lambda _tab: Session("ctrl"))
    monkeypatch.setattr(app, "ensure_runtime_files", lambda: None)
    monkeypatch.setattr(app, "render_output", lambda *items: rendered.append(items))

    app.main()

    assert terminal.prompts == ["ctrl> ", "ctrl> "]
    assert "temporary device failure" in rendered[-1][0].plain

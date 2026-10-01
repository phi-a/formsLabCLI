"""The console shell: tab table, screen clearing, and the REPL loop."""

from formslab import app
from formslab.app import clear_screen, initial_tab_from_argv
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


def test_the_hardware_tabs_are_the_tabs():
    """ctrl, cast, log, psu -- and nothing that needed FORMS or Qt."""
    assert set(app.TAB_FACTORIES) == {"ctrl", "cast", "log", "psu"}
    assert initial_tab_from_argv(["labcli", "--flatsat"]) == "ctrl"
    assert initial_tab_from_argv(["labcli", "--psu"]) == "psu"


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

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from rich.text import Text

import formslab.console.style as style
from formslab.console.cmd_browser import handle_cmd_command
from formslab.console.console_help import build_command_index
from formslab.console.resources import handle_resources_command
from formslab.console.sessions.base import CLIResult


SessionFactory = Callable[[], object]


def get_default_tab_factories() -> Dict[str, SessionFactory]:
    """Return lazy session factories for all console tabs.

    Tabs cover bench hardware and sequence operation. FORMS documentation
    remains in FORMS.
    """
    return {
        "ctrl": lambda: __import__("formslab.console.sessions.ctrl", fromlist=["CtrlSession"]).CtrlSession(),
        "cast": lambda: __import__("formslab.console.sessions.cast", fromlist=["CastSession"]).CastSession(),
        "log": lambda: __import__("formslab.console.sessions.log", fromlist=["LogSession"]).LogSession(),
        "psu": lambda: __import__("formslab.console.sessions.psu", fromlist=["PSUSession"]).PSUSession(),
    }


@dataclass
class RouteResult:
    result: Optional[CLIResult] = None
    should_exit: bool = False
    tab_changed: bool = False
    echo_input: bool = False


class ConsoleRouter:
    """Shared command routing engine for terminal and GUI consoles."""

    def __init__(
        self,
        tab_factories: Optional[Dict[str, SessionFactory]] = None,
        initial_tab: str = "ctrl",
    ):
        self.tab_factories = tab_factories or get_default_tab_factories()
        self.tabs = list(self.tab_factories.keys())
        if not self.tabs:
            raise ValueError("No console tabs configured.")

        self.current_tab = initial_tab if initial_tab in self.tab_factories else self.tabs[0]
        self._active_sessions: Dict[str, object] = {}

    def get_session(self, tab: Optional[str] = None):
        key = tab or self.current_tab
        if key not in self._active_sessions:
            try:
                self._active_sessions[key] = self.tab_factories[key]()
            except Exception as exc:
                self._active_sessions[key] = self._make_unavailable_session(key, exc)
        return self._active_sessions[key]

    def initial_result(self) -> CLIResult:
        return self._as_help_result(self.get_session().help())

    def route(self, raw: str, payload: Optional[dict] = None) -> RouteResult:
        text = (raw or "").strip()
        if not text:
            return RouteResult()

        parts = text.split()
        cmd = parts[0].lower()

        if cmd in ("--resources", "resources"):
            return RouteResult(result=self._as_global_result(handle_resources_command(text)))

        if cmd in ("--cmd", "cmd", "--tree", "tree"):
            mapped = text
            if cmd in ("--tree", "tree"):
                parts = text.split(maxsplit=1)
                rest = parts[1] if len(parts) > 1 else ""
                mapped = f"cmd {rest}".strip()
            return RouteResult(result=self._as_global_result(handle_cmd_command(mapped)))

        if cmd in ("--exit", "exit", "quit"):
            return RouteResult(should_exit=True)

        if cmd in ("--help", "help"):
            if len(parts) > 1 and parts[1].lower() in ("cmd", "commands", "all"):
                return RouteResult(
                    result=self._as_global_result(
                        build_command_index(self.tab_factories, self.get_session)
                    )
                )
            return RouteResult(result=self._as_help_result(self.get_session().help()))

        tab_switch_tokens = tuple(f"--{name}" for name in self.tabs)
        if cmd in tab_switch_tokens:
            tab = cmd.lstrip("-")
            if tab not in self.tab_factories:
                err = CLIResult(content=Text(f"✗ Unknown tab: {tab}", style=style.ERROR))
                return RouteResult(result=err)
            self.current_tab = tab
            return RouteResult(
                result=self._as_help_result(self.get_session().help()),
                tab_changed=True,
            )

        result = self._as_cli_result(self.get_session().handle(text, payload))
        return RouteResult(result=result, echo_input=not result.suppress_prompt)

    @staticmethod
    def _as_cli_result(value) -> CLIResult:
        if isinstance(value, CLIResult):
            return value
        return CLIResult(content=value)

    @staticmethod
    def _as_help_result(value) -> CLIResult:
        res = ConsoleRouter._as_cli_result(value)
        return CLIResult(content=res.content, clear=True, suppress_prompt=True, data=res.data)

    @staticmethod
    def _as_global_result(value) -> CLIResult:
        res = ConsoleRouter._as_cli_result(value)
        return CLIResult(content=res.content, clear=True, suppress_prompt=True, data=res.data)

    @staticmethod
    def _make_unavailable_session(tab: str, exc: Exception):
        message = f"X {tab} tab unavailable: {exc}"

        class _UnavailableSession:
            def help(self):
                return CLIResult(content=Text(message, style=style.ERROR), suppress_prompt=True)

            def handle(self, _raw, _payload=None):
                return CLIResult(content=Text(message, style=style.ERROR))

            def prompt(self):
                return f"{tab}> "

        return _UnavailableSession()

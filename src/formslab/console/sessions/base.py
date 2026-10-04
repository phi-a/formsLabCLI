# formslab/console/sessions/base.py
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional
from rich.text import Text
from rich.panel import Panel

@dataclass
class CLIResult:
    content: Any  # Any Rich renderable (Text, Panel, Group, Table, Columns, ...)
    clear: bool = False
    suppress_prompt: bool = False
    # Optional structured data carried alongside the rendered content.
    data: Optional[dict] = None
    # False when the command failed or was refused: `labcli <command>` exits 1.
    ok: bool = True

class ConsoleSession(ABC):
    """Base class for a console tab session."""

    # Whether `banner()` is cheap enough to redraw on every frame.
    #
    # The console paints the banner as a persistent status region, so it is
    # called once per repaint -- i.e. after every command typed on the tab. A
    # banner that only reads a pid file or a JSON state file opts in; PSU's
    # opens a VISA session, queries every channel and closes it again, so it
    # must not, or the supply would be reconnected after each keystroke.
    # Off by default: a new tab has to say its banner is free before the frame
    # will poll it.
    live_status = False

    def __init__(self, name: str, handler: Callable[..., CLIResult]):
        self.name = name
        self._cli_handler = handler
        # Most handlers take only the parsed `parts`. A handler that opts into
        # the structured channel declares a `payload` parameter; we forward it
        # only to those, so existing handlers are untouched.
        self._handler_takes_payload = self._accepts_payload(handler)

    @staticmethod
    def _accepts_payload(handler: Callable[..., Any]) -> bool:
        try:
            return "payload" in inspect.signature(handler).parameters
        except (TypeError, ValueError):
            return False

    def handle(self, raw: str, payload: Optional[dict] = None) -> CLIResult:
        parts = raw.strip().split()
        if not parts:
            return CLIResult(content="")
        try:
            if self._handler_takes_payload:
                return self._cli_handler(parts, payload=payload)
            return self._cli_handler(parts)
        except Exception as e:
            return CLIResult(content=f"✗ {self.name.upper()} error: {e}")

    @abstractmethod
    def help(self):
        pass

    def banner(self):
        return None

    def prompt(self):
        return f"{self.name}> "

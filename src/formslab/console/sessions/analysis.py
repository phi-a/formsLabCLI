# File: cli/sessions/analysis.py

from formslab.console.sessions.base import ConsoleSession
import formslab.console.style as style
from formslab.console.analysis.analysiscli import execute_command, help_panel

class AnalysisSession(ConsoleSession):
    def __init__(self):
        # Initialize with execute_command, which returns CLIResult
        super().__init__("analysis", execute_command)

    def help(self):
        # Return CLIResult from help_panel()
        return help_panel()

    def banner(self):
        # Display a banner when switching to the tab
        from rich.text import Text
        return Text(
            "⚙️ ANALYSIS tab ready. Type --help for commands.",
            style=style.TEXT
        )

    def prompt(self):
        from formslab.console.style import PROMPT_MARKUP, PROMPT_SUFFIX
        return f"[{PROMPT_MARKUP}]analysis{PROMPT_SUFFIX} [/] "

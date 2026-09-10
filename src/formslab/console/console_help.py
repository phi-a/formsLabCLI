from __future__ import annotations

from typing import Callable, Dict

from rich.text import Text

import formslab.console.style as style
from formslab.console.sessions.base import CLIResult


def _content_to_lines(content) -> list[str]:
    if isinstance(content, Text):
        text = content.plain
    else:
        text = str(content)
    return [line.rstrip() for line in text.splitlines()]


def build_command_index(
    tab_factories: Dict[str, Callable[[], object]],
    get_session: Callable[[str], object],
) -> CLIResult:
    lines: list[str] = []
    lines.append("Console Command Index")
    lines.append("")
    lines.append("Global:")
    lines.append("  --help                 Show current tab help")
    lines.append("  --help cmd             Show this full command index")
    lines.append("  resources              Resource inventory")
    lines.append("  resources <key> --check   Status of one resource")
    lines.append("  resources <key> --update  Fetch it if stale")
    lines.append("  resources --update-stale  Fetch everything stale")
    lines.append("  --tree [path]          Browse FORMS API roots/tree")
    lines.append("  --tree forms coord     Browse one level deeper")
    lines.append("  --tree --rebuild       Rebuild API catalog")
    lines.append("  /switch astrid         Switch to Astrid")
    lines.append("  /switch console        Return to the forms console")
    lines.append("  --ctrl --cast --log --psu")
    lines.append("  --exit")
    lines.append("")
    lines.append("Tab Command Sets:")

    for tab in tab_factories.keys():
        lines.append("")
        lines.append(f"[{tab}]")
        try:
            session = get_session(tab)
            help_result = session.help()
            help_lines = _content_to_lines(help_result.content)
            if not help_lines:
                lines.append("  (no help text)")
            else:
                for line in help_lines:
                    lines.append(f"  {line}" if line else "  ")
        except Exception as exc:
            lines.append(f"  (help unavailable: {exc})")

    return CLIResult(content=Text("\n".join(lines), style=style.TEXT), suppress_prompt=True)

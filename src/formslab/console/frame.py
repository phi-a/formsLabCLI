"""Compose one fixed-geometry screen.

The console redraws; it does not scroll. Every command repaints a frame whose
regions occupy the same rows each time, so the prompt never walks down the
terminal and no earlier output survives below the fold. Top to bottom:

    tab bar     the tab strip -- constant height
    status      `session.banner()`, for a tab that publishes one
    content     the last command's output, in a pane of fixed height
    prompt      the terminal's last row, written by the REPL after this frame

The regions are drawn as boxes on purpose. A pane that pads to a constant height
is only *reassuring* if you can see it holding its shape: without a border, a
short command's output and a long one's look like the same printed text, and the
window reads as a transcript again. The border is the part you actually notice.

Only the content region changes between commands, and it changes in *content*,
never in height. Output taller than the pane is windowed in place -- `more`,
`back` and `top`, with the position in the pane's footer -- rather than spilled
into scrollback, which is the thing a static window exists to avoid.

The caller decides whether a frame is possible at all (`MIN_HEIGHT`/`MIN_WIDTH`,
an interactive terminal); on a redirected stream the console falls back to
append-only printing so captures stay diffable.
"""

from dataclasses import dataclass
from itertools import chain
from typing import Any, List, Optional

from rich.console import Group
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.text import Text

from formslab.console import style

# Below this the frame has no room to say anything and the console prints
# append-only instead.
MIN_HEIGHT = 12
MIN_WIDTH = 40

# Visible rows the content pane never drops below. A banner that would force it
# lower is dropped instead (see `build`).
MIN_BODY_ROWS = 3

# What the content pane's own border costs it.
PANE_CHROME_ROWS = 2    # top and bottom edge
PANE_CHROME_COLS = 4    # two edges plus Panel's default (0, 1) padding

# The REPL writes the prompt on the row this frame deliberately leaves free.
PROMPT_ROWS = 1


@dataclass
class Frame:
    """One painted screen, plus what the pager needs to move over it."""

    segments: Segments      # exactly `rows` newline-terminated lines
    rows: int               # total rows this frame occupies
    body_rows: int          # visible rows inside the content pane
    body_total: int         # natural height of the content, before windowing
    scroll: int             # offset actually used, after clamping

    @property
    def paged(self) -> bool:
        """True when the content did not fit and is being windowed."""
        return self.body_total > self.body_rows


def _render(console, renderable, width: int, height: Optional[int] = None) -> List[List[Segment]]:
    """Render to a list of newline-terminated lines, cropped/padded to `height`."""
    options = console.options.update(width=width, height=height)
    return console.render_lines(renderable, options, pad=True, new_lines=True)


def _blank(width: int) -> List[Segment]:
    return [Segment(" " * width), Segment("\n")]


def _fit(lines: List[List[Segment]], rows: int, width: int) -> List[List[Segment]]:
    """Force `lines` to exactly `rows` lines by cropping or padding with blanks."""
    fitted = list(lines[:rows])
    fitted.extend(_blank(width) for _ in range(rows - len(fitted)))
    return fitted


def _footer(scroll: int, shown: int, total: int) -> Text:
    """The pane's bottom edge doubles as the pager readout.

    It names keys, not commands: the reader in `keys.py` moves the window
    without Enter, and the typed `more`/`back`/`top` only exist for the fallback
    path where there is no key reader to bind.
    """
    first, last = scroll + 1, min(scroll + shown, total)
    return Text(f" {first}-{last} of {total} · ↑↓ PgUp/PgDn ", style=style.DIM)


def build(console, *, tab_bar, body, banner=None, title=None,
          width: int, height: int, scroll: int = 0) -> Frame:
    """Lay out one frame of exactly `height - PROMPT_ROWS` rows.

    `banner` is kept only if the content pane can still show `MIN_BODY_ROWS`
    afterwards; otherwise it is dropped whole. Cropping it instead would leave
    half a status panel on screen, which reads as breakage rather than as a
    deliberately windowed view.
    """
    rows = height - PROMPT_ROWS
    inner_width = max(1, width - PANE_CHROME_COLS)

    def chrome_lines(with_banner: bool) -> List[List[Segment]]:
        parts: List[Any] = [tab_bar]
        if with_banner and banner is not None:
            parts.append(Panel(
                banner,
                title=Text("STATUS", style=style.HEADER),
                title_align="left",
                border_style=style.PANEL_BORDER,
            ))
        return _render(console, Group(*parts), width)

    chrome = chrome_lines(with_banner=True)
    if rows - len(chrome) < MIN_BODY_ROWS + PANE_CHROME_ROWS:
        chrome = chrome_lines(with_banner=False)

    pane_rows = max(MIN_BODY_ROWS + PANE_CHROME_ROWS, rows - len(chrome))
    body_rows = pane_rows - PANE_CHROME_ROWS

    natural = _render(console, body, inner_width)
    body_total = len(natural)

    if body_total <= body_rows:
        scroll = 0
        footer = None
    else:
        scroll = max(0, min(scroll, body_total - body_rows))
        footer = _footer(scroll, body_rows, body_total)

    view = _fit(natural[scroll:scroll + body_rows], body_rows, inner_width)
    pane = Panel(
        Segments(chain.from_iterable(view)),
        title=Text(title.upper(), style=style.HEADER) if title else None,
        title_align="left",
        subtitle=footer,
        subtitle_align="right",
        border_style=style.PANEL_BORDER,
        height=pane_rows,
    )

    lines = _fit(chrome + _render(console, pane, width, height=pane_rows), rows, width)
    return Frame(
        segments=Segments(chain.from_iterable(lines)),
        rows=rows,
        body_rows=body_rows,
        body_total=body_total,
        scroll=scroll,
    )

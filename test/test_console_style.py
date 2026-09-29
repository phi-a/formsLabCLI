"""Styling that has to survive contact with Rich.

A `Style` object interpolated into a style or markup string renders its *repr*,
which Rich cannot parse and discards without raising. The result is text in
whatever colour happened to surround it -- which is how the active tab came to
be drawn black on black, invisible, while the code read as though it set a
colour. Nothing about that is catchable by eye in review, so it is pinned here.
"""

import io
import pathlib

import pytest
from rich.console import Console
from rich.style import Style

from formslab import app
from formslab.console import style

ESC = chr(27)
TAB_NAMES = {"CTRL", "CAST", "LOG", "PSU"}


def _tab_segments(active="ctrl"):
    console = Console(file=io.StringIO(), width=60,
                      force_terminal=True, color_system="truecolor")
    app.current_tab = active
    return console, list(console.render(app.render_tab_bar(), console.options))


def _chips(segments):
    return {s.text.strip(): s.style for s in segments if s.text.strip() in TAB_NAMES}


def test_no_source_interpolates_a_style_objects_colour():
    """`f"on {SOME_STYLE.color}"` yields a repr, not a colour, and Rich drops it.

    Use the palette's hex constants or a Style object; `style.PROMPT_MARKUP`
    exists for the markup-string case.
    """
    offenders = [
        str(p) for p in pathlib.Path("src").rglob("*.py")
        if "__pycache__" not in p.parts and ".color}" in p.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_active_tab_is_actually_visible():
    """It was black on black: a foreground with no background to lift it off."""
    _, segments = _tab_segments("ctrl")
    active = _chips(segments)["CTRL"]

    assert active.bgcolor is not None
    assert active.color != active.bgcolor


def test_the_active_tab_is_distinguishable_from_the_inactive_ones():
    _, segments = _tab_segments("ctrl")
    chips = _chips(segments)

    assert chips["CTRL"].bgcolor != chips["CAST"].bgcolor
    assert chips["CAST"].bgcolor == chips["LOG"].bgcolor == chips["PSU"].bgcolor


@pytest.mark.parametrize("tab", ["ctrl", "cast", "log", "psu"])
def test_whichever_tab_is_current_is_the_filled_one(tab):
    _, segments = _tab_segments(tab)
    chips = _chips(segments)
    filled = [name for name, chip in chips.items() if chip.bgcolor == style.TAB_ACTIVE.bgcolor]

    assert filled == [tab.upper()]


def test_the_tab_bar_border_is_not_drawn_in_the_background_colour():
    """`style=BG` painted the border black on black, so the box never appeared."""
    _, segments = _tab_segments()
    border = next(s for s in segments if "┌" in s.text or "│" in s.text)

    assert border.style.color is not None
    assert border.style.color.name != "black"


def test_prompt_markup_parses_as_a_style():
    assert Style.parse(style.PROMPT_MARKUP).color is not None


def test_a_session_prompt_carries_its_styling_through_to_the_terminal():
    """The four tab prompts rendered as plain text for want of a parseable colour."""
    from formslab.console.sessions.log import LogSession

    buf = io.StringIO()
    console = Console(file=buf, width=60, force_terminal=True, color_system="truecolor")
    console.print(LogSession().prompt(), end="")

    assert ESC in buf.getvalue()          # styled, not bare text
    assert "log>" in buf.getvalue()

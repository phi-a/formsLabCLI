"""The fixed frame: the console redraws a stable screen instead of scrolling.

The invariant worth defending is geometric, not cosmetic. Every frame paints
exactly `height - 1` rows whatever the command produced, which is what keeps the
prompt on one row; if that slips, output starts walking down the terminal again
and the tab bar scrolls off, which is the behaviour this replaced.
"""

import io

import pytest
from rich.console import Console
from rich.text import Text

from formslab import app
from formslab.console import frame
from formslab.console.sessions.psu import PSUSession


WIDTH = 60
HEIGHT = 24


def _console():
    return Console(file=io.StringIO(), width=WIDTH, height=HEIGHT,
                   force_terminal=True, color_system=None)


def _build(body_lines, *, banner=None, scroll=0, height=HEIGHT, title=None):
    console = _console()
    body = Text("\n".join(f"body{i:02d}" for i in range(body_lines)))
    built = frame.build(
        console,
        tab_bar=Text("[ CTRL ][ CAST ][ LOG ][ PSU ]"),
        body=body,
        banner=banner,
        title=title,
        width=WIDTH,
        height=height,
        scroll=scroll,
    )
    console.print(built.segments, end="")
    return built, console.file.getvalue()


@pytest.mark.parametrize("body_lines", [0, 1, 5, 23, 24, 200])
def test_every_frame_paints_the_same_number_of_rows(body_lines):
    """Short output pads, long output crops -- the prompt never moves."""
    built, painted = _build(body_lines)

    assert built.rows == HEIGHT - frame.PROMPT_ROWS
    assert painted.count("\n") == HEIGHT - frame.PROMPT_ROWS


def test_the_last_row_is_left_for_the_prompt():
    """The frame stops one row short and does not end in a newline of its own."""
    _, painted = _build(5)

    assert painted.count("\n") == HEIGHT - 1


def test_the_content_pane_is_drawn_as_a_box():
    """The border is what makes a constant height visible; without it the pane
    reads as ordinary printed text and the window looks like a transcript."""
    _, painted = _build(5, title="ctrl")

    assert "CTRL" in painted
    assert "│" in painted          # pane side edges


def test_a_banner_costs_the_content_region_but_not_the_total():
    banner = Text("PSU1 ON 12.0V\nCRYO 58.2 K")
    plain, _ = _build(5)
    with_banner, painted = _build(5, banner=banner)

    assert with_banner.body_rows < plain.body_rows
    assert painted.count("\n") == HEIGHT - frame.PROMPT_ROWS


def test_a_banner_that_would_starve_the_content_is_dropped_whole():
    """Cropping it would leave half a status panel on screen, which reads as breakage."""
    tall = Text("\n".join(f"status{i}" for i in range(HEIGHT)))
    built, painted = _build(10, banner=tall)

    assert built.body_rows >= frame.MIN_BODY_ROWS
    assert "status0" not in painted
    assert painted.count("\n") == HEIGHT - frame.PROMPT_ROWS


def test_overflowing_content_is_paged_rather_than_spilled():
    built, painted = _build(200)

    assert built.paged
    assert "of 200" in painted          # the pane footer carries the position
    assert "PgUp" in painted            # ...and names the keys that move it


def test_scroll_is_clamped_to_the_end_of_the_content():
    built, painted = _build(200, scroll=9999)

    assert built.scroll == 200 - built.body_rows
    assert painted.count("\n") == HEIGHT - frame.PROMPT_ROWS


def test_content_that_fits_is_never_paged():
    built, painted = _build(3, scroll=50)

    assert not built.paged
    assert built.scroll == 0
    assert "PgUp" not in painted        # no footer when nothing is windowed


def test_redirected_output_keeps_the_append_only_path(monkeypatch):
    """A pipe or a test capture wants the text, not a screenful of padding."""
    printed = []

    class _Console:
        is_dumb_terminal = False
        size = type("Size", (), {"width": WIDTH, "height": HEIGHT})()

        def clear(self):
            pass

        def print(self, value, **kwargs):
            printed.append(value)

    monkeypatch.setattr(app, "console", _Console())
    monkeypatch.setattr(app.sys, "stdout", io.StringIO())  # isatty() is False

    app.render_output(Text("hello"))

    assert app._last_frame is None
    assert any("hello" in str(getattr(v, "plain", v)) for v in printed)


def test_psu_does_not_opt_into_the_status_region():
    """`psucli.status_panel` opens a VISA session per supply.

    Drawing it as a persistent region would reconnect the instrument after every
    command typed on the tab. The frame only polls a banner that opts in.
    """
    assert PSUSession.live_status is False

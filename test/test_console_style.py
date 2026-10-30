"""Styling that has to survive contact with Rich.

A `Style` object interpolated into a style or markup string renders its *repr*,
which Rich cannot parse and discards without raising. The result is text in
whatever colour happened to surround it. Nothing about that is catchable by
eye in review, so it is pinned here.
"""
import pathlib


def test_no_source_interpolates_a_style_objects_colour():
    """`f"on {SOME_STYLE.color}"` yields a repr, not a colour, and Rich drops it.
    Use the palette's hex constants or a Style object."""
    offenders = [
        str(p) for p in pathlib.Path("src").rglob("*.py")
        if "__pycache__" not in p.parts and ".color}" in p.read_text(encoding="utf-8")
    ]

    assert offenders == []

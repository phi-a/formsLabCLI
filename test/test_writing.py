"""The application's text against docs/WRITING.md: what a machine can check. Plain
words and sentence case are for review; this catches what slips past review."""
import re

import pytest

from formslab.rscripts.grammar import _Choice, _Slot
from formslab.sequence.plan import _LOAD_CARD, _RECORD, _grammar, available_rscripts

FORBIDDEN = re.compile(r"[()/;<]|![A-Z]")         # parentheses, slashes, semicolons, `<`, controller codes


def _commands():
    """(usage, summary, details, elements) for every command a plan or the console accepts."""
    grammar = _grammar(tuple(available_rscripts()))[0]
    for g in (grammar, _RECORD):
        for c in g._commands:
            yield " ".join(e.usage() for e in c.elements), c.help, c.details, c.elements


COMMANDS = list(_commands())
IDS = [usage for usage, *_ in COMMANDS]


def test_every_kind_of_command_is_checked():
    usages = " ".join(IDS)
    for word in ("hvc", "psu1", "cryo", "slta", "hold", "until", "log", "record"):
        assert word in usages


@pytest.mark.parametrize("usage, summary, details, elements", COMMANDS, ids=IDS)
def test_a_summary_is_one_plain_phrase(usage, summary, details, elements):
    assert summary and len(summary) <= 50, summary
    assert summary[0].isupper() and not summary.endswith("."), summary
    assert not FORBIDDEN.search(summary), summary


@pytest.mark.parametrize("usage, summary, details, elements", COMMANDS, ids=IDS)
def test_every_command_has_details_in_whole_sentences(usage, summary, details, elements):
    assert details, f"{usage}: no details"
    for paragraph in details.split("\n\n"):
        assert paragraph.rstrip().endswith("."), paragraph


@pytest.mark.parametrize("usage, summary, details, elements", COMMANDS, ids=IDS)
def test_inputs_are_named_by_words(usage, summary, details, elements):
    for e in elements:
        name = e.name if isinstance(e, (_Slot, _Choice)) else ""
        if name:
            assert re.fullmatch(r"[a-z][a-z_]{2,}", name), f"{usage}: input named {name!r}"


def test_the_load_card_follows_the_same_rules():
    assert len(_LOAD_CARD["help"]) <= 50 and not FORBIDDEN.search(_LOAD_CARD["help"])
    assert _LOAD_CARD["details"].endswith(".")

"""Parts: which kind of part a command is about (valve, pump, zone, setting), as
rLACO declares it, carried to the page on tokens, options and help cards."""
import pytest

from formslab.gui import api
from formslab.rscripts import cast
from formslab.sequence.plan import describe_step, line_options, tokens


@pytest.mark.parametrize("words, part", [
    ("hvc gate open", "valve"), ("hvc pump on", "pump"), ("hvc platen on", "zone"),
    ("hvc platen 20", "zone"), ("hvc vacuum range 5", "setting"), ("hvc recipe start", "setting"),
    ("hvc stop", None), ("hvc purge", None), ("psu1 ch1 set 1 0.1", None), ("cryo on", None),
    ("hold 5 s", None), ("hvc", None)])
def test_a_command_is_about_the_part_named_after_its_label(words, part):
    assert cast.part_of(words.split()[0], words.split()) == part


def test_plan_tokens_carry_the_part_on_their_keywords_only():
    [line] = tokens("load rLACO\nhvc gate open\n")[1:]
    assert line == [{"text": "hvc", "role": "verb"}, {"text": "gate", "role": "kw", "part": "valve"},
                    {"text": "open", "role": "kw", "part": "valve"}]
    assert all("part" not in t for t in tokens("load rLACO\nhold 5 s\n")[1])


def test_dropdown_options_after_the_label_each_carry_their_own_part():
    r = line_options(["rLACO"], ["hvc", "gate"])
    first = {o["text"]: o["part"] for o in r["positions"][1]}
    assert first["gate"] == "valve" and first["turbo"] == "pump" and first["shroud"] == "zone"
    assert first["vacuum"] == "setting" and first["abort"] is None
    assert {o["part"] for o in r["positions"][2]} == {"valve"}
    assert all(o["part"] is None for o in r["positions"][0])


def test_command_box_hints_carry_parts():
    first = {o["text"]: o["part"] for o in api.complete(["hvc"])}
    assert first["rough"] == "valve" and first["pump"] == "pump" and first["start"] is None
    assert [o["part"] for o in api.complete(["hvc", "turbo"])] == ["pump", "pump"]


def test_a_help_card_knows_its_part_and_its_words():
    [card] = describe_step("load rLACO\nhvc rough open\n", 2)["cards"]
    assert card["part"] == "valve"
    assert [w["role"] for w in card["words"]] == ["word", "choice", "choice"]
    assert card["words"][1]["choices"][0] == "rough"
    [card] = cast.describe(["psu1", "ch1", "on"])["cards"]
    assert card["part"] is None and card["words"][1]["text"] == "channel"

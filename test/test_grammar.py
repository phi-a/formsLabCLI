"""The command grammar: patterns as data, one source for parsing, completion
and help. A toy grammar here; the instruments' real ones are pinned in
test_cast_commands."""
import pytest

from formslab.rscripts.grammar import Grammar, GrammarError, Option

TOY = Grammar([
    ("<zone:platen|shroud> <C:number -180..200 C>", "Zone setpoint", lambda z, c: {z: c}),
    ("<zone:platen|shroud> on|off", "Thermal control", lambda z, s: {f"{z}_control": s == "on"}),
    ("vacuum <P:number 0..>", "Vacuum setpoint", lambda p: {"vacuum": p}),
    ("recipe <n:integer 1..20>", "Select a recipe", lambda n: {"recipe": n}),
    ("imagedir <name:text>", "Image folder", lambda n: {"IMAGEDIR": n}),
    ("log <msg:rest>", "A log line", lambda m: {"log": m}),
    ("until chamberP|platenT <|<=|>|>= <v:number>", "Wait", lambda var, op, v: (var, op, v)),
    ("closeall", "Close every valve", {"close_all": True}),
])


@pytest.mark.parametrize("line, meaning", [
    ("platen 20", {"platen": 20.0}),
    ("SHROUD -40.5", {"shroud": -40.5}),
    ("platen ON", {"platen_control": True}),
    ("vacuum 0", {"vacuum": 0.0}),
    ("recipe 3", {"recipe": 3}),
    ("recipe 3.0", {"recipe": 3}),
    ("imagedir Darks_A", {"IMAGEDIR": "Darks_A"}),            # text keeps its case
    ("log pump on; #2 next", {"log": "pump on; #2 next"}),
    ("until chamberp < 5", ("chamberP", "<", 5.0)),   # a choice keeps its declared spelling
    ("closeall", {"close_all": True}),
])
def test_parse(line, meaning):
    assert TOY.parse(line.split()) == meaning


def test_a_constant_request_is_a_copy():
    TOY.parse(["closeall"])["close_all"] = False
    assert TOY.parse(["closeall"]) == {"close_all": True}


def test_integers_come_back_as_int():
    assert type(TOY.parse(["recipe", "3"])["recipe"]) is int


@pytest.mark.parametrize("line, message", [
    ("platen 500", "platen: 500 is outside -180..200 C"),
    ("vacuum -5", "vacuum: -5 must be >= 0"),                   # a one-sided range (was a TypeError)
    ("recipe 2.5", "2.5 is not a whole number"),
    ("platen hot", "expected <C> (-180..200 C), on or off after 'platen', got 'hot'"),
    ("platen onn", "did you mean 'on'?"),
    ("recipe x", "expected <n> (1..20) after 'recipe', got 'x': not a whole number"),
    ("platen", "incomplete: expected <C> (-180..200 C), on or off after 'platen'"),
    ("closeall now", "unexpected 'now' after 'closeall'"),
    ("teleport", "got 'teleport'"),
    ("vacum 3", "did you mean 'vacuum'?"),
    ("platen 1e999", "got '1e999'"),                         # not finite: not a number
])
def test_errors_say_what_was_expected(line, message):
    with pytest.raises(GrammarError) as e:
        TOY.parse(line.split())
    assert message in str(e.value)


def test_an_error_carries_the_options_a_dropdown_would_show():
    with pytest.raises(GrammarError) as e:
        TOY.parse(["platen", "hot"])
    assert [str(o) for o in e.value.expected] == ["<C> (-180..200 C)", "on", "off"]


def test_complete_cascades():
    first = [o.text for o in TOY.complete([])]
    assert first == ["platen", "shroud", "vacuum", "recipe", "imagedir", "log", "until", "closeall"]
    after_zone = TOY.complete(["platen"])
    assert after_zone[0] == Option("number", "C", "Zone setpoint", -180.0, 200.0, "C")
    assert [o.text for o in after_zone[1:]] == ["on", "off"]
    assert all(o.help == "Thermal control" for o in after_zone[1:])
    assert [o.text for o in TOY.complete(["until", "platenT"])] == ["<", "<=", ">", ">="]
    assert TOY.complete(["closeall"]) == []
    assert TOY.complete(["nonsense"]) == []


def test_an_option_leading_to_one_command_carries_its_help():
    helps = {o.text: o.help for o in TOY.complete([])}
    assert helps["platen"] == ""                     # a setpoint or thermal control: two commands
    assert helps["vacuum"] == "Vacuum setpoint" and helps["closeall"] == "Close every valve"


def test_a_named_choice_may_have_one_member():
    g = Grammar([("until <variable:chamberP> below <v:number>", "", lambda var, v: (var, v))])
    assert g.parse(["until", "CHAMBERP", "below", "5"]) == ("chamberP", 5.0)
    assert g.rows() == [("until <variable> below <v>", "")]


def test_two_commands_matching_one_line_is_an_error():
    g = Grammar([("vent open", "valve", {"vent": "open"}),
                 ("<x:text> open", "anything", lambda x: {x: "open"})])
    with pytest.raises(GrammarError, match="ambiguous"):
        g.parse(["vent", "open"])


def test_rows_are_the_help_table():
    assert TOY.rows()[0] == ("<zone> <C>", "Zone setpoint")
    assert TOY.rows()[6] == ("until chamberP|platenT <|<=|>|>= <v>", "Wait")
    assert ("closeall", "Close every valve") in TOY.rows()


@pytest.mark.parametrize("pattern", [
    "", "<x:number 0..1 V extra>", "<x:number 0..1> <y:rest> z", "<x:text 0..1>", "a|", "<x number>",
])
def test_a_malformed_pattern_is_refused(pattern):
    with pytest.raises(GrammarError):
        Grammar([(pattern, "", {})])

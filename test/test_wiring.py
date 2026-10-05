"""What each supply channel feeds, from the hardware map (devices/dp832a/wiring.py),
and the two scripts that drive a channel taking it from there."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from formslab import config
from formslab.devices.cryocooler import owner
from formslab.devices.cryocooler.config import cryo_supply
from formslab.devices.dp832a import wiring
from formslab.devices.slta import routine


def bench_map(edit):
    """The live map, edited by `edit(data)`."""
    path = config.usbmap_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_shipped_wiring():
    assert wiring.channel_of("rCryoBoard") == ("psu1", 1)
    assert wiring.channel_of("rSLTA") == ("psu2", 1)
    assert wiring.channel("psu1", 1) == {"feeds": "cryocooler board", "owner": "rCryoBoard"}
    assert wiring.channel("PSU1", "2") == {}                      # a free channel
    assert wiring.channel_of("rNobody") is None


def test_a_bench_map_from_before_channels_takes_the_shipped_wiring():
    def strip(data):
        for label in ("psu1", "psu2"):
            data[label].pop("channels", None)
    bench_map(strip)
    assert wiring.channel_of("rCryoBoard") == ("psu1", 1)
    assert wiring.channel("psu2", 1)["feeds"] == "sLTA camera"


def test_the_bench_map_can_rewire_a_channel():
    def rewire(data):
        data["psu1"]["channels"] = {"3": {"feeds": "cryocooler board", "owner": "rCryoBoard"}}
        data["psu2"]["channels"] = {"2": {"feeds": "sLTA camera", "owner": "rSLTA"}}
    bench_map(rewire)
    assert wiring.channel_of("rCryoBoard") == ("psu1", 3)
    assert wiring.channel("psu1", 1) == {}
    assert routine.slta_supply() == ("psu2", 2)


def test_a_damaged_map_falls_back_to_the_defaults():
    config.usbmap_path().write_text("{ not json", encoding="utf-8")
    assert cryo_supply() == ("psu1", 1)
    assert routine.slta_supply() == ("psu2", 1)


def test_the_cryo_board_drives_the_mapped_channel_and_keeps_it_for_the_run():
    bench_map(lambda d: d["psu1"].update(channels={"2": {"feeds": "cryocooler board", "owner": "rCryoBoard"}}))
    rg = SimpleNamespace(_psu2_ready=False, _psu2_request_pending=False, _psu2_status_unknown_reported=False,
                         cryo=None)
    run = SimpleNamespace(log=lambda *a, **k: None)
    with patch.object(owner, "psu_channel_state", return_value="missing"), \
            patch.object(owner, "queue_psu_request") as queue:
        owner._init_psu2(run, rg)
    label, request = queue.call_args[0]
    assert label == "psu1" and set(request) == {"2"}

    bench_map(lambda d: d["psu1"].update(channels={"3": {"feeds": "cryocooler board", "owner": "rCryoBoard"}}))
    assert cryo_supply(rg) == ("psu1", 2)                          # not while the board runs
    with patch.object(owner, "queue_psu_request"):
        owner._shutdown_cryo_subsystem(run, rg, close_transport=False, release_handles=True)
    assert cryo_supply(rg) == ("psu1", 3)                          # the next start reads it again

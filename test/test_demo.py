"""The demo bench (formslab.demo): a simulated chamber and a throwaway folder,
for the GUI demo and `labcli --demo`, gone when it closes."""
import os

from formslab import config


def test_bench_yields_a_live_simulated_chamber_and_cleans_up_after_itself():
    from formslab.demo import bench
    from formslab.devices.hvc3500.laco import LACO
    from formslab.sequence import find_plan

    before = os.environ.get("FORMSLAB_CONFIG_DIR")
    with bench() as b:
        root = b.root
        assert config.config_dir() == root / "config" and os.environ["FORMSLAB_GUI_DEMO"] == "1"
        assert (root / "config" / "tvac_bench.json").is_file()
        assert find_plan("tvac") is not None and find_plan("pump_demo") is not None
        assert set(b.env) == {"FORMSLAB_CONFIG_DIR", "FORMSLAB_OUTPUT_DIR", "FORMSLAB_PLANS_DIR"}
        laco = LACO()
        laco.connect()
        try:
            assert laco.status() is not None
        finally:
            laco.close()
    assert not root.exists()
    assert os.environ.get("FORMSLAB_CONFIG_DIR") == before

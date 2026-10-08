"""Test collection rules and per-test isolation for the lab console.

Hardware tests are excluded from a plain `pytest` run. They open a serial port,
a VISA session or a camera, and hang or fail on a machine with nothing plugged
in. They are the drivers' real coverage and are run deliberately, at the bench,
by naming the file:

    pytest test/test_DP832A.py
"""

from pathlib import Path

import pytest

FIXTURE_PLANS = Path(__file__).resolve().parent / "test" / "fixtures" / "plans"

collect_ignore = [
    # hardware -- needs instruments on the bench
    "test/test_CCboard.py",
    "test/test_DP832A.py",
    "test/test_SMTC08.py",
    "test/test_image.py",
    "test/test_psu_request_resilience.py",
]


@pytest.fixture(autouse=True)
def _isolated_config_and_output(tmp_path_factory, monkeypatch):
    """Give every test its own config and output directories.

    Autouse and not optional. The console's config directory defaults to
    `~/.formslab`, which is an operator's real bench setup: a test that seeds a
    `usbmap.json`, rewrites CAST state, or drops a heater log there would be
    editing live lab configuration. Redirecting both env vars means a test run
    cannot touch anything outside its own tmp dir, and it also exercises the
    override path itself on every single test.
    """
    monkeypatch.setenv("FORMSLAB_CONFIG_DIR",
                       str(tmp_path_factory.mktemp("formslab-config")))
    monkeypatch.setenv("FORMSLAB_OUTPUT_DIR",
                       str(tmp_path_factory.mktemp("formslab-output")))


@pytest.fixture(autouse=True)
def _fixture_plans(tmp_path_factory, monkeypatch):
    """Stand the test plans in for the shipped ones.

    The checkout's `plans/` is the bench's catalog of tests and changes with it.
    The suite reads a frozen set instead, `test/fixtures/plans/`, as if it were
    shipped. The working directory moves to an empty folder, so its `plans/`
    (the checkout's, when pytest runs from the root) is not searched either.
    """
    from formslab.sequence import plan
    monkeypatch.setattr(plan, "shipped_dir", lambda: FIXTURE_PLANS)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))

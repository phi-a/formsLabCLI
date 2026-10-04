import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from formslab.console.cast.castutils import GenerateCleanCast, ReadCommand, WriteCommand
from formslab.devices.dp832a.commands import classify_channel_status
import formslab.devices.cryocooler.owner as cryoutils
import formslab.devices.slta.routine as sltautils


class DummyRun:
    def __init__(self):
        self.logs = []

    def log(self, message, level="INFO", component=None):
        self.logs.append((level, component, message))


class TestCastCommandMerging(unittest.TestCase):
    def test_same_channel_writes_are_deep_merged(self):
        tmp_root = Path(__file__).resolve().parent
        cast_path = tmp_root / f"_cast_test_{uuid4().hex}.json"
        self.addCleanup(lambda: cast_path.unlink(missing_ok=True))
        self.addCleanup(lambda: [q.unlink(missing_ok=True) for q in cast_path.parent.glob(cast_path.name + ".*")])
        GenerateCleanCast(cast_path)

        WriteCommand({"1": {"voltage": 12.0, "current": 2.0}}, "psu2", path=cast_path)
        WriteCommand({"1": {"on": True}}, "psu2", path=cast_path)

        request = ReadCommand("psu2", path=cast_path)
        self.assertEqual(
            request["1"],
            {"voltage": 12.0, "current": 2.0, "on": True},
        )


class TestPsuStatusClassification(unittest.TestCase):
    def test_classify_channel_status_states(self):
        self.assertEqual(classify_channel_status({}, voltage=12.0, current=2.0), "missing")
        self.assertEqual(
            classify_channel_status({"vset": None, "cset": None}, voltage=12.0, current=2.0),
            "unknown",
        )
        self.assertEqual(
            classify_channel_status({"vset": 12.0, "cset": 2.0}, voltage=12.0, current=2.0),
            "match",
        )
        self.assertEqual(
            classify_channel_status({"vset": 5.0, "cset": 2.0}, voltage=12.0, current=2.0),
            "mismatch",
        )


class TestPsuInitGuards(unittest.TestCase):
    def test_slta_unknown_status_does_not_requeue(self):
        run = DummyRun()
        state = SimpleNamespace(
            _psu2_ready=True,
            _psu2_request_pending=False,
            _psu2_status_unknown_reported=False,
        )

        with patch.object(sltautils, "psu_channel_state", return_value="unknown"), patch.object(
            sltautils, "queue_psu_request"
        ) as queue:
            sltautils._init_psu(run, state)

        queue.assert_not_called()
        self.assertTrue(state._psu2_ready)
        self.assertTrue(state._psu2_status_unknown_reported)

    def test_slta_missing_status_still_queues_initial_config(self):
        run = DummyRun()
        state = SimpleNamespace(
            _psu2_ready=False,
            _psu2_request_pending=False,
            _psu2_status_unknown_reported=False,
        )

        with patch.object(sltautils, "psu_channel_state", return_value="missing"), patch.object(
            sltautils, "queue_psu_request"
        ) as queue:
            sltautils._init_psu(run, state)

        queue.assert_called_once()
        self.assertFalse(state._psu2_ready)
        self.assertTrue(state._psu2_request_pending)

    def test_cryo_unknown_status_does_not_requeue(self):
        run = DummyRun()
        state = SimpleNamespace(
            _psu2_ready=True,
            _psu2_request_pending=False,
            _psu2_status_unknown_reported=False,
        )

        with patch.object(cryoutils, "psu_channel_state", return_value="unknown"), patch.object(
            cryoutils, "queue_psu_request"
        ) as queue:
            cryoutils._init_psu2(run, state)

        queue.assert_not_called()
        self.assertTrue(state._psu2_ready)
        self.assertTrue(state._psu2_status_unknown_reported)


if __name__ == "__main__":
    unittest.main()

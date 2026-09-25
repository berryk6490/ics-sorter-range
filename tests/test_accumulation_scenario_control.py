"""Host gates and fixture teardown; no guest or PLC connection."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import accumulation_scenario_control as host
import live_accumulation_fixture as fixture


class HostGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "scenario-control.json"
        self.control = {"version": 1, "run_id": "test_12345678", "scenario": "lane_hold",
                        "pid": 42, "start_ticks": 55, "guest_dir": "/tmp/fake",
                        "local_dir": str(self.root), "monitor_control": str(self.root / "monitor.json")}
        self.path.write_text(json.dumps(self.control))
        self.scada = Mock()
        self.manager = host.ScenarioController(scada=self.scada, drives=Mock(), monitor=Mock())

    def test_release_refuses_missing_independent_evidence(self):
        with self.assertRaisesRegex(RuntimeError, "evidence proof missing"):
            self.manager.action(self.path, "release")
        self.scada.run.assert_not_called()

    def test_begin_requires_bounded_fixture(self):
        with self.assertRaisesRegex(RuntimeError, "fixture not ready"):
            self.manager.action(self.path, "begin")
        self.scada.run.assert_not_called()

    def test_wrong_run_response_is_rejected(self):
        self.control["scenario"] = "smoke"
        self.path.write_text(json.dumps(self.control))
        self.scada.run.return_value = json.dumps({**host.identity(self.control), "run_id": "wrong_run"})
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.manager.action(self.path, "abort")

    def test_monitor_death_prevents_begin(self):
        self.control["scenario"] = "smoke"
        self.path.write_text(json.dumps(self.control))
        monitor = {"version": 1, "run_id": "monitor_12345678", "pid": 77}
        (self.root / "monitor.json").write_text(json.dumps(monitor))
        self.manager.monitor._inspect.return_value = {"alive": False, "matches": False}
        with self.assertRaisesRegex(RuntimeError, "monitor died"):
            self.manager.action(self.path, "begin")
        self.scada.run.assert_not_called()

    def test_evidence_mismatch_cannot_authorize_release(self):
        self.control["scenario"] = "smoke"
        self.path.write_text(json.dumps(self.control))
        (self.root / "evidence-proof.json").write_text(json.dumps({**host.identity(self.control),
                                                                  "start_ticks": 999}))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.manager.action(self.path, "release")
        self.scada.run.assert_not_called()


class FixtureCleanupTest(unittest.TestCase):
    def test_service_restored_when_fixture_start_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            args = Mock(directory=temp, run_id="test_12345678", case="lane_hold", duration=30)
            active = Mock(returncode=0)
            calls = []
            with patch.object(fixture.signal, "signal"), \
                 patch.object(fixture.subprocess, "run", return_value=active), \
                 patch.object(fixture, "systemctl", side_effect=lambda action: calls.append(action)), \
                 patch.object(fixture, "fixture_command", side_effect=RuntimeError("fixture unavailable")), \
                 patch.object(fixture, "count_unflagged", return_value=["normal"]):
                code = fixture.work(args)
            self.assertEqual(code, 1)
            self.assertEqual(calls, ["stop", "start"])
            terminal = json.loads((Path(temp) / "terminal.json").read_text())
            self.assertIn("fixture unavailable", terminal["error"])
            self.assertEqual(terminal["cleanup_errors"], [])
            self.assertTrue(terminal["service_restored"])


if __name__ == "__main__":
    unittest.main()

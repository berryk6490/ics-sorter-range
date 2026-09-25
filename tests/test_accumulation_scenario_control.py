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

    def test_fixture_start_requires_one_run_approval_record(self):
        (self.root / "scenario-ready-proof.json").write_text("{}")
        with patch.object(self.manager, "_file", return_value=host.identity(self.control)):
            with self.assertRaisesRegex(RuntimeError, "authorization missing"):
                self.manager.fixture_start(self.path)
        self.manager.drives.run.assert_not_called()

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

    def test_terminal_outcomes_trigger_one_fixture_restoration(self):
        (self.root / "fixture-control.json").write_text("{}")
        for status in ("complete", "scenario_failure", "timeout", "evidence_failure"):
            with self.subTest(status=status), \
                 patch.object(self.manager, "_inspect", return_value={"alive": False}), \
                 patch.object(self.manager, "_file", return_value={
                     **host.identity(self.control), "status": status}), \
                 patch.object(self.manager, "fixture_stop", return_value={
                     "status": "complete", "service_restored": True}) as restore:
                result = self.manager.wait(self.path, timeout=.2)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["fixture_restoration"]["service_restored"])
                restore.assert_called_once_with(self.path)

    def test_restoration_error_preserves_scenario_failure(self):
        (self.root / "fixture-control.json").write_text("{}")
        with patch.object(self.manager, "_inspect", return_value={"alive": False}), \
             patch.object(self.manager, "_file", return_value={
                 **host.identity(self.control), "status": "scenario_failure",
                 "original_error": "bad checkpoint"}), \
             patch.object(self.manager, "fixture_stop", side_effect=RuntimeError("start denied")):
            result = self.manager.wait(self.path, timeout=.2)
        self.assertEqual(result["status"], "scenario_failure")
        self.assertIn("start denied", result["fixture_restoration_error"])
        self.assertTrue((self.root / "fixture-restoration-failure.json").exists())

    def test_fixture_stop_waits_for_matching_supervisor_exit(self):
        fixture_control = {"run_id": self.control["run_id"], "scenario": "lane_hold",
                           "pid": 77, "start_ticks": 1234, "guest_dir": "/tmp/fake-fixture"}
        (self.root / "fixture-control.json").write_text(json.dumps(fixture_control))
        (self.root / "fixture-authorization.json").write_text(json.dumps({
            "authorization_id": "approval_record123"}))
        terminal = {"run_id": self.control["run_id"], "pid": 77,
                    "authorization_id": "approval_record123", "status": "complete",
                    "service_restored": True}
        self.manager.drives.read_file.return_value = json.dumps(terminal).encode()
        self.manager.drives.run.return_value = "stopped"
        states = [{**fixture_control, "alive": True, "matches": True},
                  {**fixture_control, "alive": False, "matches": False}]
        with patch.object(self.manager, "fixture_inspect", side_effect=states) as inspect, \
             patch.object(host.time, "sleep"):
            result = self.manager.fixture_stop(self.path, timeout=2)
        self.assertTrue(result["service_restored"])
        self.assertEqual(inspect.call_count, 2)


class FixtureCleanupTest(unittest.TestCase):
    def test_service_restored_when_fixture_start_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            args = Mock(directory=temp, run_id="test_12345678", case="lane_hold", duration=30)
            calls = []
            with patch.object(fixture.signal, "signal"), \
                 patch.object(fixture, "service_active", return_value=True), \
                 patch.object(fixture, "systemctl", side_effect=lambda action: calls.append(action)), \
                 patch.object(fixture, "fixture_command", side_effect=RuntimeError("fixture unavailable")), \
                 patch.object(fixture, "count_unflagged", return_value=["normal"]), \
                 patch.object(fixture, "APPROVAL_LEDGER", Path(temp) / "ledger.jsonl"):
                approved = fixture.authorize(temp + "/fixture", args.run_id,
                                             args.case, "approval_12345678", 120)
                args.directory = temp + "/fixture"
                fixture.atomic(Path(args.directory) / "claimed.json",
                               {"run_id": args.run_id, "scenario": args.case,
                                "authorization_id": approved["authorization_id"]})
                code = fixture.work(args)
            self.assertEqual(code, 1)
            self.assertEqual(calls, ["stop", "start"])
            terminal = json.loads((Path(args.directory) / "terminal.json").read_text())
            self.assertIn("fixture unavailable", terminal["error"])
            self.assertEqual(terminal["cleanup_errors"], [])
            self.assertTrue(terminal["service_restored"])


if __name__ == "__main__":
    unittest.main()

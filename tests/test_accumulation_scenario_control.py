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
from test_accumulation_state_snapshot import sample as typed_sample


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

    def _gate_ready(self, plc=None, remaining=40):
        self.control.update(commit="test-commit", runner_sha256="runner-hash")
        self.path.write_text(json.dumps(self.control))
        (self.root / "typed-before.json").write_text(json.dumps({"plc": typed_sample()["plc"]}))
        ready = {**host.identity(self.control), "repository_commit": "test-commit",
                 "mutation_started": False, "preflight": {"runner_sha256": "runner-hash"}}
        self.manager.probe_ready = Mock(return_value={"ready": ready})
        self.manager._inspect = Mock(return_value={"alive": True, "matches": True,
                            "authorization_remaining_seconds": remaining})
        self.manager.monitor._inspect.return_value = {"alive": True, "matches": True}
        current = plc or typed_sample()["plc"]

        def guest(vm, command, label):
            rows = processes(vm)
            return json.dumps({"schema_version": 1, "role": vm,
                "plc": current if vm == "drives" else None,
                "services": {"sorter-plant.service": "active"} if vm == "drives" else
                            {"openplc.service": "active"} if vm == "plc" else
                            {"sorter-hmi.service": "active"}, "processes": rows})

        def processes(vm):
            if vm == "drives":
                return [{"pid": 101, "args": "/home/kevin/venv/bin/python /home/kevin/plant.py"},
                        {"pid": 77, "args": "/home/kevin/venv/bin/python /home/kevin/live_accumulation_monitor.py monitor"}]
            if vm == "plc":
                return []
            return [{"pid": 42, "args": "/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation_detached.py worker"}]

        return (patch.object(host, "load_control", return_value={"run_id": "monitor_12345678", "pid": 77}),
                patch.object(host, "guest_read", side_effect=guest),
                patch.object(host, "service_groups", return_value={
                    "drives": ["sorter-plant.service"], "plc": ["openplc.service"],
                    "scada": ["sorter-hmi.service"]}),
                patch.object(host.subprocess, "run", return_value=Mock(stdout="", returncode=0)))

    def test_post_ready_gate_is_bounded_and_does_not_inventory_hashes(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            reads = [stack.enter_context(p) for p in self._gate_ready()]
            result = self.manager.pre_authorization_gate(self.path)
        self.assertEqual(result["status"], "PASS")
        self.assertGreater(result["authorization_remaining_seconds"], 25)
        self.assertEqual(result["services_checked"], 3)
        self.assertEqual(reads[1].call_count, 3)
        self.assertFalse(any("sha256sum" in call.args[1] for call in reads[1].call_args_list))

    def test_changed_plc_or_expired_window_aborts_without_fixture_stop(self):
        from contextlib import ExitStack
        for changed, remaining in ((True, 40), (False, 1)):
            with self.subTest(changed=changed, remaining=remaining), ExitStack() as stack:
                plc = typed_sample()["plc"]
                if changed:
                    plc["coils_880_920"][0] = True
                for p in self._gate_ready(plc, remaining):
                    stack.enter_context(p)
                cleanup = stack.enter_context(patch.object(self.manager, "_abort_failed_gate",
                                                          return_value={"worker": "stopped", "monitor": "stopped"}))
                fixture_stop = stack.enter_context(patch.object(self.manager, "fixture_stop"))
                with self.assertRaisesRegex(RuntimeError, "post-ready gate failed"):
                    self.manager.pre_authorization_gate(self.path)
                cleanup.assert_called_once()
                fixture_stop.assert_not_called()

    def test_failed_gate_cleanup_stops_only_worker_and_monitor(self):
        self.control["scenario"] = "smoke"
        self.path.write_text(json.dumps(self.control))
        terminal = {**host.identity(self.control), "status": "scenario_failure"}
        with patch.object(self.manager, "action", return_value={"abort_sent": True}) as abort, \
             patch.object(self.manager, "_inspect", return_value={"alive": False}), \
             patch.object(self.manager, "_file", return_value=terminal), \
             patch.object(self.manager, "fixture_stop") as fixture_stop:
            self.manager.monitor.collect.return_value = {"cleanup_errors": [], "orphan": False}
            outcome = self.manager._abort_failed_gate(self.path, "changed state")
        abort.assert_called_once_with(self.path, "abort")
        fixture_stop.assert_not_called()
        self.manager.monitor.collect.assert_called_once()
        self.assertEqual(outcome["errors"], [])
        self.assertFalse(outcome["monitor"]["orphan"])

    def test_authorization_window_expiry_aborts_before_any_fixture(self):
        self.control["scenario"] = "smoke"
        self.path.write_text(json.dumps(self.control))
        (self.root / "post-ready-gate.json").write_text(json.dumps({
            **host.identity(self.control), "status": "PASS"}))
        with patch.object(self.manager, "probe_ready"), \
             patch.object(self.manager, "_inspect", return_value={
                 "alive": True, "matches": True, "authorization_remaining_seconds": 1}), \
             patch.object(self.manager, "_abort_failed_gate", return_value={
                 "worker_terminal": "stopped", "monitor": "stopped"}) as cleanup, \
             patch.object(self.manager, "fixture_stop") as fixture_stop:
            with self.assertRaisesRegex(RuntimeError, "authorization refused"):
                self.manager.action(self.path, "authorize")
        cleanup.assert_called_once()
        fixture_stop.assert_not_called()
        self.scada.run.assert_not_called()


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

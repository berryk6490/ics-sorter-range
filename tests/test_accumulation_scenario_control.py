"""Host gates and fixture teardown; no guest or PLC connection."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
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
        (self.root / "preparation.json").write_text(json.dumps({"run_id": self.control["run_id"],
                                                               "scenario": "lane_hold"}))
        now = time.time_ns()
        (self.root / "operator-approval.json").write_text(json.dumps({
            "version": 1, "run_id": self.control["run_id"], "scenario": "lane_hold",
            "approval_id": "receipt_12345678", "unit": host.FIXTURE_UNIT,
            "stop_argv": host.FIXTURE_STOP, "start_argv": host.FIXTURE_START,
            "initial_service_state": "active",
            "approved_wall_ns": now, "expires_wall_ns": now + 300 * 10**9,
            "scope": "one stop and its restorative start for this run only"}))
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

    def test_fixture_start_refuses_expired_worker_without_service_stop(self):
        (self.root / "scenario-ready-proof.json").write_text("{}")
        row = {"run_id": self.control["run_id"], "scenario": "lane_hold",
               "unit": host.FIXTURE_UNIT, "stop_argv": host.FIXTURE_STOP,
               "start_argv": host.FIXTURE_START, "initial_service_state": "active",
               "guest_directory": f"/tmp/sorter-accumulation-fixture-{self.control['run_id']}"}
        (self.root / "fixture-authorization.json").write_text(json.dumps(row))
        self.manager.drives.read_file.return_value = json.dumps(row)
        with patch.object(self.manager, "_file", return_value=host.identity(self.control)), \
             patch.object(self.manager, "_inspect", return_value={
                 "alive": True, "matches": True, "authorization_remaining_seconds": 1}), \
             patch.object(self.manager, "_abort_failed_gate", return_value={"orphan": False}) as cleanup:
            with self.assertRaisesRegex(RuntimeError, "fixture launch refused"):
                self.manager.fixture_start(self.path)
        cleanup.assert_called_once()
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

    def test_prelaunch_approval_expiring_after_launch_fails_gate(self):
        from contextlib import ExitStack
        row_path = self.root / "operator-approval.json"
        row = json.loads(row_path.read_text())
        row["expires_wall_ns"] = 1
        row_path.write_text(json.dumps(row))
        with ExitStack() as stack:
            for item in self._gate_ready():
                stack.enter_context(item)
            cleanup = stack.enter_context(patch.object(self.manager, "_abort_failed_gate",
                                                       return_value={"orphan": False}))
            with self.assertRaisesRegex(RuntimeError, "post-ready gate failed"):
                self.manager.pre_authorization_gate(self.path)
        cleanup.assert_called_once()
        self.scada.run.assert_not_called()

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


class PrelaunchApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = host.ScenarioController(scada=Mock(), drives=Mock(), monitor=Mock())
        self.baseline = self.root / "baseline.json"
        self.preflight = self.root / "preflight.json"
        self.baseline.write_text("{}")
        self.preflight.write_text("{}")

    def prepared(self, case="lane_hold"):
        with patch.object(host, "validate_typed_launch", return_value=({}, {"status": "PASS"})):
            path = self.manager.prepare(self.root, case, self.baseline, self.preflight)
        return path

    def approved(self, case="lane_hold"):
        path = self.prepared(case)
        with patch.object(host, "validate_typed_launch", return_value=({}, {"status": "PASS"})), \
             patch.object(host, "APPROVAL_LEDGER", self.root / "host-ledger.jsonl"):
            self.manager.record_approval(path, "receipt_12345678")
        return path

    def test_slow_human_approval_precedes_worker_launch(self):
        path = self.prepared()
        self.manager.scada.run.assert_not_called()
        # A 92-second inventory and a simulated 200-second human wait are outside
        # the worker timer; the receipt lifetime begins only at record-approval.
        future = time.time_ns() + 200 * 10**9
        with patch.object(host, "validate_typed_launch", return_value=({}, {"status": "PASS"})), \
             patch.object(host, "APPROVAL_LEDGER", self.root / "host-ledger.jsonl"), \
             patch.object(host.time, "time_ns", return_value=future):
            self.manager.record_approval(path, "receipt_12345678")
        self.manager.scada.run.assert_not_called()
        approval = json.loads((path.parent / "operator-approval.json").read_text())
        self.assertEqual(approval["run_id"], json.loads(path.read_text())["run_id"])
        self.assertEqual(approval["stop_argv"], host.FIXTURE_STOP)
        self.assertEqual(approval["approved_wall_ns"], future)

    def test_expired_or_wrong_run_approval_rejected_before_launch(self):
        path = self.approved()
        row = json.loads((path.parent / "operator-approval.json").read_text())
        for key, value in (("expires_wall_ns", 1), ("run_id", "another_run"),
                           ("scenario", "merge_hold")):
            with self.subTest(key=key):
                changed = dict(row, **{key: value})
                with self.assertRaisesRegex(ValueError, "prelaunch operator approval"):
                    host.valid_preapproval(json.loads(path.read_text()), changed)

    def test_same_receipt_cannot_be_prepared_for_another_run(self):
        first = self.approved()
        second = self.prepared("merge_hold")
        with patch.object(host, "validate_typed_launch", return_value=({}, {"status": "PASS"})), \
             patch.object(host, "APPROVAL_LEDGER", self.root / "host-ledger.jsonl"):
            with self.assertRaisesRegex(ValueError, "already used"):
                self.manager.record_approval(second, "receipt_12345678")
        self.assertFalse((second.parent / "operator-approval.json").exists())
        self.assertTrue((first.parent / "operator-approval.json").exists())

    def test_launch_rejects_expired_approval_and_stops_monitor(self):
        path = self.approved()
        self.manager.monitor.collect.return_value = {"cleanup_errors": [], "orphan": False}
        row_path = path.parent / "operator-approval.json"
        row = json.loads(row_path.read_text())
        row["expires_wall_ns"] = 1
        row_path.write_text(json.dumps(row))
        with patch.object(host, "validate_typed_launch", return_value=(typed_sample(), {"status": "PASS"})), \
             patch.object(self.manager, "_failed_launch", return_value={
                 "cleanup": {"errors": []}, "typed_restoration": {"status": "PASS"}}) as postflight:
            with self.assertRaisesRegex(RuntimeError, "expired"):
                self.manager.launch(self.root, "lane_hold", "monitor", self.baseline,
                                    self.preflight, preparation_path=path)
        postflight.assert_called_once()
        self.manager.scada.run.assert_not_called()

    def test_expiry_after_launch_refuses_fixture_and_aborts(self):
        path = self.approved()
        control = {"run_id": json.loads(path.read_text())["run_id"],
                   "scenario": "lane_hold", "local_dir": str(path.parent),
                   "pid": 42, "start_ticks": 5}
        control_path = path.parent / "scenario-control.json"
        control_path.write_text(json.dumps(control))
        row_path = path.parent / "operator-approval.json"
        row = json.loads(row_path.read_text())
        row["expires_wall_ns"] = 1
        row_path.write_text(json.dumps(row))
        (path.parent / "post-ready-gate.json").write_text(json.dumps({**host.identity(control),
                                                                   "status": "PASS"}))
        with patch.object(self.manager, "_abort_failed_gate", return_value={"orphan": False}) as cleanup, \
             patch.object(self.manager, "fixture_stop") as fixture_stop:
            with self.assertRaisesRegex(RuntimeError, "fixture authorization refused"):
                self.manager.fixture_authorize(control_path, "receipt_12345678")
        cleanup.assert_called_once()
        fixture_stop.assert_not_called()
        self.manager.drives.run.assert_not_called()

    def test_failed_gate_never_reaches_guest_fixture_ledger(self):
        path = self.approved()
        control = {"run_id": json.loads(path.read_text())["run_id"],
                   "scenario": "lane_hold", "local_dir": str(path.parent),
                   "pid": 42, "start_ticks": 5}
        control_path = path.parent / "scenario-control.json"
        control_path.write_text(json.dumps(control))
        with patch.object(self.manager, "_abort_failed_gate", return_value={"orphan": False}):
            with self.assertRaisesRegex(RuntimeError, "fixture authorization refused"):
                self.manager.fixture_authorize(control_path, "receipt_12345678")
        self.manager.drives.run.assert_not_called()

    def test_timely_approval_issues_exact_run_bound_guest_record(self):
        path = self.approved()
        run_id = json.loads(path.read_text())["run_id"]
        control = {"run_id": run_id, "scenario": "lane_hold", "local_dir": str(path.parent),
                   "guest_dir": "/tmp/scenario", "pid": 42, "start_ticks": 5}
        control_path = path.parent / "scenario-control.json"
        control_path.write_text(json.dumps(control))
        (path.parent / "post-ready-gate.json").write_text(json.dumps({**host.identity(control),
                                                                   "status": "PASS"}))
        guest_dir = f"/tmp/sorter-accumulation-fixture-{run_id}"
        expected = {"run_id": run_id, "scenario": "lane_hold", "approval_id": "receipt_12345678",
                    "guest_directory": guest_dir, "unit": host.FIXTURE_UNIT,
                    "stop_argv": host.FIXTURE_STOP, "start_argv": host.FIXTURE_START,
                    "initial_service_state": "active"}
        self.manager._file = Mock(return_value=host.identity(control))
        self.manager.probe_ready = Mock()
        with patch.object(self.manager, "_abort_failed_gate") as abort:
            manifest = json.loads(Path("deploy/deployment_manifest.json").read_text())
            component = next(x for x in manifest["components"] if
                             x["repository_source"] == "tests/live_accumulation_fixture.py")
            self.manager.drives.run.side_effect = [
                component["sha256"] + "  " + component["canonical_destination"], json.dumps(expected)]
            result = self.manager.fixture_authorize(control_path, "receipt_12345678")
        self.assertEqual(result["run_id"], run_id)
        self.assertTrue((path.parent / "fixture-authorization-claimed.json").exists())
        abort.assert_not_called()
        guest_argv = self.manager.drives.run.call_args_list[1].args[0]
        self.assertEqual(guest_argv[guest_argv.index("--run-id") + 1], run_id)
        self.assertEqual(guest_argv[guest_argv.index("--case") + 1], "lane_hold")


class PreparedHandoffTest(unittest.TestCase):
    """Exercise the actual command parser and controller methods in one fake guest run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.gate = self.root / "global-gate.json"
        self.manager = host.ScenarioController(scada=Mock(), drives=Mock(), monitor=Mock())
        self.manager.monitor.collect.return_value = {"cleanup_errors": [], "orphan": False}
        self.manager.state = Mock(return_value={"identity": 24113,
            "coils_880_920": [False] * 41, "slots": [[0] * 12 for _ in range(3)]})
        now = datetime.now(timezone.utc)
        baseline = typed_sample()
        baseline["captured_utc"] = (now - timedelta(seconds=20)).isoformat()
        self.before = self.evidence / "typed-before.json"
        self.before.write_text(json.dumps(baseline))
        manifest = Path("deploy/deployment_manifest.json")
        receipt = {"schema_version": 1, "status": "PASS", "live": True,
            "completed_utc": (now - timedelta(seconds=120)).isoformat(),
            "duration_seconds": 92.0, "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "source_and_guest_hashes": len(json.loads(manifest.read_text())["components"]),
            "program_identity": 24113}
        self.preflight = self.evidence / "deployment-preflight.json"
        self.preflight.write_text(json.dumps(receipt))
        self.monitor_control = self.evidence / "monitor.json"
        self.monitor_control.write_text(json.dumps({
            "launched_utc": (now - timedelta(seconds=10)).isoformat()}))
        self.gate.write_text(json.dumps({"status": "PASS", "run_id": "previous_run"}))

    def command(self, *argv):
        out = io.StringIO()
        with patch.object(host, "ScenarioController", return_value=self.manager), \
             patch.object(host, "RESTORATION_GATE", self.gate), \
             patch.object(host, "APPROVAL_LEDGER", self.root / "ledger.jsonl"), \
             patch.object(host, "load_control", side_effect=lambda path: json.loads(Path(path).read_text())), \
             redirect_stdout(out):
            code = host.main(list(argv))
        self.assertEqual(code, 0)
        return json.loads(out.getvalue().splitlines()[-1])

    def prepare(self, case="lane_hold"):
        return Path(self.command("prepare", "--evidence-dir", str(self.evidence),
                                 "--case", case, "--typed-baseline", str(self.before),
                                 "--preflight-report", str(self.preflight))["preparation"])

    def launch(self, preparation, case="lane_hold"):
        return Path(self.command("launch", "--evidence-dir", str(self.evidence),
                                 "--case", case, "--preparation", str(preparation),
                                 "--monitor-control", str(self.monitor_control),
                                 "--typed-baseline", str(self.before),
                                 "--preflight-report", str(self.preflight))["control"])

    def approve(self, preparation):
        self.command("record-approval", "--preparation", str(preparation),
                     "--approval-id", "new_receipt_12345678")

    def test_canonical_prepare_then_launch_claims_same_directory_once(self):
        preparation = self.prepare()
        self.approve(preparation)

        def worker(argv):
            self.assertEqual(argv[argv.index("--run-id") + 1], preparation.parent.name)
            return json.dumps({"run_id": preparation.parent.name,
                               "scenario": "lane_hold", "pid": 42})

        self.manager.scada.run.side_effect = worker
        control = self.launch(preparation)
        self.assertEqual(control.parent, preparation.parent)
        self.assertEqual(json.loads(control.read_text())["run_id"], preparation.parent.name)
        self.assertEqual(json.loads(self.gate.read_text())["status"], "PENDING")
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], preparation.parent.name)
        self.assertEqual(json.loads((control.parent / "restoration-gate.json").read_text())["status"], "PENDING")
        with self.assertRaisesRegex(ValueError, "already launched"):
            self.launch(preparation)
        self.manager.scada.run.assert_called_once()
        self.assertEqual(json.loads(self.gate.read_text())["status"], "PENDING")

    def test_wrong_scenario_and_unrelated_contents_rejected(self):
        preparation = self.prepare()
        self.approve(preparation)
        with self.assertRaisesRegex(ValueError, "identity"):
            self.launch(preparation, "merge_hold")
        (preparation.parent / "unrelated.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "unexpected contents"):
            self.launch(preparation)
        self.manager.scada.run.assert_not_called()
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], "previous_run")

    def test_prepared_old_schema_and_repeat_prepare_are_rejected(self):
        preparation = self.prepare("smoke")
        row = json.loads(preparation.read_text())
        row["version"] = 1
        preparation.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.launch(preparation, "smoke")
        self.manager.scada.run.assert_not_called()

    def test_prepare_collision_never_replaces_existing_reservation(self):
        fixed_now = datetime.now(timezone.utc)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        with patch.object(host, "datetime", FixedDatetime), \
             patch.object(host.uuid, "uuid4", return_value=Mock(hex="a" * 32)):
            first = self.prepare("smoke")
            original = first.read_bytes()
            with self.assertRaises(FileExistsError):
                self.prepare("smoke")
        self.assertEqual(first.read_bytes(), original)
        self.manager.scada.run.assert_not_called()

    def test_launch_failure_has_current_run_postflight_and_no_prior_pass(self):
        preparation = self.prepare()
        self.approve(preparation)
        self.manager.state.side_effect = RuntimeError("PLC state read failed")
        with patch.object(host, "typed_capture", return_value=json.loads(self.before.read_text())), \
             patch.object(host, "RESTORATION_GATE", self.gate):
            with self.assertRaisesRegex(RuntimeError, "PLC state read failed"):
                self.launch(preparation)
        failure = json.loads((preparation.parent / "launch-failure.json").read_text())
        self.assertEqual(failure["status"], "launch_failed")
        self.assertEqual(failure["typed_restoration"]["status"], "PASS")
        self.assertEqual(failure["cleanup"]["errors"], [])
        self.assertFalse(failure["service_restoration"]["required_start"])
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], preparation.parent.name)
        self.assertEqual(json.loads(self.gate.read_text())["result"], "launch_failed")
        self.manager.monitor.collect.assert_called_once()
        self.manager.scada.run.assert_not_called()

    def test_preflight_failure_spends_approved_attempt_and_runs_postflight(self):
        preparation = self.prepare()
        self.approve(preparation)
        receipt = json.loads(self.preflight.read_text())
        receipt["manifest_sha256"] = "wrong"
        self.preflight.write_text(json.dumps(receipt))
        with patch.object(host, "typed_capture", return_value=json.loads(self.before.read_text())):
            with self.assertRaisesRegex(RuntimeError, "preflight receipt"):
                self.launch(preparation)
        self.assertTrue((preparation.parent / "launch-claimed.json").exists())
        self.assertEqual(json.loads((preparation.parent / "launch-failure.json").read_text())[
            "typed_restoration"]["status"], "PASS")
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], preparation.parent.name)
        self.manager.scada.run.assert_not_called()

    def test_prior_pending_gate_is_never_closed_by_failed_handoff(self):
        preparation = self.prepare()
        self.approve(preparation)
        self.gate.write_text(json.dumps({"status": "PENDING", "run_id": "unresolved_prior_run"}))
        with patch.object(host, "typed_capture", return_value=json.loads(self.before.read_text())):
            with self.assertRaisesRegex(RuntimeError, "previous scenario"):
                self.launch(preparation)
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], "unresolved_prior_run")
        local_gate = json.loads((preparation.parent / "restoration-gate.json").read_text())
        self.assertEqual(local_gate["result"], "launch_blocked_by_prior_run")

    def test_verify_clean_rejects_prior_run_pass(self):
        preparation = self.prepare("smoke")
        self.manager.scada.run.return_value = json.dumps({
            "run_id": preparation.parent.name, "scenario": "smoke", "pid": 42})
        control = self.launch(preparation, "smoke")
        self.gate.write_text(json.dumps({"status": "PASS", "run_id": "previous_run"}))
        with patch.object(host, "RESTORATION_GATE", self.gate), \
             patch.object(host, "typed_capture") as capture:
            with self.assertRaisesRegex(ValueError, "another run"):
                self.manager.verify_clean(control)
        capture.assert_not_called()

    def test_worker_launch_failure_aborts_matching_pid_and_retains_results(self):
        preparation = self.prepare()
        self.approve(preparation)
        self.manager.scada.run.side_effect = RuntimeError("serial response lost")
        self.manager.scada.read_file.return_value = json.dumps({
            "run_id": preparation.parent.name, "scenario": "lane_hold",
            "pid": 42, "start_ticks": 77})
        after = json.loads(self.before.read_text())
        after["plc"]["plant_faults"][0] = 1
        with patch.object(host, "typed_capture", return_value=after), \
             patch.object(self.manager, "_abort_failed_gate", return_value={
                 "worker_terminal": {"status": "scenario_failure"},
                 "monitor": {"cleanup_errors": [], "orphan": False}, "errors": []}) as abort:
            with self.assertRaisesRegex(RuntimeError, "serial response lost"):
                self.launch(preparation)
        abort.assert_called_once()
        failure = json.loads((preparation.parent / "launch-failure.json").read_text())
        self.assertEqual(failure["typed_restoration"]["status"], "FAIL")
        self.assertEqual(failure["cleanup"]["errors"], [])
        self.assertEqual(failure["service_restoration"]["sorter_plant_active"], True)
        self.assertEqual(json.loads(self.gate.read_text())["status"], "FAIL")
        self.assertEqual(json.loads(self.gate.read_text())["run_id"], preparation.parent.name)


if __name__ == "__main__":
    unittest.main()

"""Focused lifecycle and committed-read tests; no live PLC or service calls."""
import json
import hashlib
import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import check_accumulation_views as views
import run_accumulation_attempt as attempt
import accumulation_scenario_control as scenario
from deployment_preflight import PreflightFailure
from test_accumulation_state_snapshot import sample


class Reply:
    def __init__(self, values):
        self.registers = values
        self.bits = values

    def isError(self):
        return False


class RecoveryTests(unittest.TestCase):
    def helper(self):
        pymodbus = ModuleType("pymodbus")
        client_module = ModuleType("pymodbus.client")
        client_module.ModbusTcpClient = object
        xle = ModuleType("xle")
        xle.OutcomeJournal = lambda _path: SimpleNamespace(close=lambda: None)
        xle.ensure_epoch = lambda _client, _journal: 1
        spec = importlib.util.spec_from_file_location(
            "recovery_under_test", Path(__file__).parent / "recover_accumulation_state.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pymodbus": pymodbus,
                                      "pymodbus.client": client_module, "xle": xle}):
            spec.loader.exec_module(module)
        return module

    def test_completed_run_reset_clears_counters_ready_and_temporary_modes(self):
        class PLC:
            nonce = 18
            counters = 3
            ready = 7
            writes = []
            def connect(self): return True
            def close(self): pass
            def read_holding_registers(self, address, count, slave):
                values = {249: 24113, 509: self.nonce, 786: self.ready,
                          591: 0, 561: 0, 788: 0, 255: 0, 256: 0}
                if address == 214:
                    return Reply([self.counters] * count)
                return Reply([values.get(address, 0)] * count)
            def read_coils(self, address, count, slave): return Reply([False] * count)
            def write_coil(self, address, value, slave):
                self.writes.append((address, value))
                if address == 910 and value:
                    self.nonce += 1
                    self.counters = 0
                    self.ready = 0
                return Reply([])
        plc = PLC()
        result = self.helper().recover(plc, "journal", reset_run=True)
        self.assertTrue(result["reset_requested"])
        self.assertTrue(result["counters_zero"])
        self.assertTrue(result["zone_ready_zero"])
        self.assertIn((910, True), plc.writes)
        for address in (914, 915, 916, 917, 918, 919, 920):
            self.assertIn((address, False), plc.writes)


class FakePLC:
    def __init__(self, commits=(10, 10)):
        self.commits = iter(commits)

    def read_holding_registers(self, address, count, slave):
        if address == 785:
            return Reply([next(self.commits)])
        if address == 558:
            return Reply([1, 0])
        if address == 509:
            return Reply([14])
        if address in (644, 659):
            return Reply([1] * count)
        if address in (530, 542, 647):
            return Reply([15, 1, 9, 123, 2, 2, 0] + [0] * 5)
        if address in (620, 630, 670):
            return Reply([1, 0, 14, 15, 1, 1, 116, 2, 0, 1])
        if address in (640, 680):
            return Reply([1] * count)
        if address == 766:
            row = [3, 2, 1, 20, 8]
            return Reply(row * 3 + [1] * 3 + [0, 7, 1, 0, 0])
        return Reply([0] * count)

    def read_coils(self, address, count, slave):
        return Reply([True] * count)


class CommittedReadTests(unittest.TestCase):
    def test_hashes_exclude_active_redirected_stdout(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            active = root / "attempt.log"
            active.write_text("before")
            (root / "proof.json").write_text('{"status":"PASS"}')
            attempt.evidence_hashes(root, stdout_path=active)
            active.write_text("after final JSON")
            sums = (root / "SHA256SUMS").read_text()
            self.assertNotIn("attempt.log", sums)
            self.assertIn("proof.json", sums)

    def test_consistent_identity_snapshot(self):
        result = views.plc_view_once(FakePLC())
        self.assertEqual(result["commit_sequence"], 10)
        self.assertEqual(result["identities"][0], "l1-1-14-15-1")

    def test_inconsistent_commit_is_distinct_and_retryable(self):
        with self.assertRaises(views.InconsistentSnapshot):
            views.plc_view_once(FakePLC((10, 11)))
        fake = FakePLC((10, 11, 12, 12))
        with patch.object(views, "ModbusTcpClient") as factory:
            factory.return_value = fake
            fake.connect = lambda: True
            fake.close = lambda: None
            self.assertEqual(views.plc_view(attempts=2)["commit_sequence"], 12)

    def test_changing_dwell_and_position_are_telemetry(self):
        row = {"slot": 0, "lane": 1, "token": 15, "serial": 1,
               "scanner_sequence": 9, "barcode": 123, "state": 2,
               "destination": 2, "actual": 0, "package_id": "l1-1-14-15-1",
               "zone": 3, "motion": 2, "hold_reason": 1,
               "dwell": 10, "sequence": 8}
        sample = {"epoch": [1, 0], "nonce": 14, "rows": [row]}
        plc = {"epoch": [1, 0], "nonce": 14,
               "faults": {"plant": 0, "zone": 0, "photoeyes": [0, 0, 0]},
               "counters": [0] * 9, "master": True,
               "identities": [row["package_id"]], "lanes": [1],
               "slots": [[15, 1, 9, 123, 2, 2, 0] + [0] * 5],
               "rows": [[3, 2, 1, 45, 41, 1]],
               "plant_rows": [[1, 0, 14, 15, 1, 1, 116, 2, 0, 41]]}
        telemetry = scenario.validate_hold_snapshot(sample, plc, 2)
        self.assertEqual(telemetry[0]["current_dwell"], 45)
        self.assertEqual(telemetry[0]["current_sequence"], 41)
        plc["slots"][0][1] = 2
        with self.assertRaisesRegex(AssertionError, "identity mismatch"):
            scenario.validate_hold_snapshot(sample, plc, 2)


class LifecycleTests(unittest.TestCase):
    def preparation(self, root, case):
        run = root / ("20260925T180000_" + "a" * 32)
        run.mkdir()
        path = run / "preparation.json"
        path.write_text(json.dumps({"run_id": run.name, "scenario": case,
                                    "typed_baseline": str(root / "typed-before.json"),
                                    "preflight_report": str(root / "preflight.json")}))
        return path

    def test_no_package_owns_complete_sequence_and_cleanup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prep = self.preparation(root, "smoke")
            calls = []

            class Monitor:
                def start(self, *_a, **_k):
                    calls.append("monitor_start")
                    return root / "monitor.json"
                def probe(self, *_a, **_k):
                    calls.append("monitor_ready")
                    return {"run_id": "monitor"}
                def collect(self, *_a, **_k):
                    calls.append("monitor_collect")
                    return {"orphan": False, "cleanup_errors": []}

            class Scenario:
                def launch(self, *_a, **_k):
                    calls.append("launch")
                    return root / "scenario.json"
                def probe_ready(self, *_a, **_k):
                    calls.append("worker_ready")
                def pre_authorization_gate(self, *_a, **_k):
                    calls.append("gate")
                    return {"status": "PASS"}
                def action(self, _path, action):
                    calls.append(action)
                def probe_checkpoint(self, *_a, **_k):
                    calls.append("checkpoint")
                    return {"sample": {"smoke": True}}
                def evidence(self, *_a, **_k):
                    calls.append("evidence")
                    return {"api": {}}
                def collect(self, *_a, **_k):
                    calls.append("worker_collect")
                    return {"terminal": {"status": "complete"}, "orphan": False}
                def state(self):
                    return {"slots": [[0] * 12 for _ in range(3)],
                            "coils_880_920": [False] * 41,
                            "plant_faults": [0, 0, 0],
                            "run_identity": {"epoch_fault": 0},
                            "photoeye_faults": [0, 0, 0],
                            "zone_view": [0] * 23}
                def verify_clean(self, *_a):
                    calls.append("postflight")
                    return {"restored": True}

            class Proxy:
                def poll(self):
                    return 0
                def wait(self, **_k):
                    return 0

            result = attempt.run_attempt(prep, scenario=Scenario(), monitor=Monitor(),
                                         proxy_start=lambda _p: Proxy())
            self.assertEqual(result["status"], "PASS")
            self.assertLess(calls.index("monitor_ready"), calls.index("begin"))
            self.assertLess(calls.index("checkpoint"), calls.index("evidence"))
            self.assertLess(calls.index("evidence"), calls.index("release"))
            self.assertLess(calls.index("worker_collect"), calls.index("postflight"))


class DelayedReservationTests(unittest.TestCase):
    def preparation(self, root, case):
        run = root / ("20260925T180000_" + "a" * 32)
        run.mkdir()
        path = run / "preparation.json"
        path.write_text(json.dumps({"run_id": run.name, "scenario": case,
                                    "typed_baseline": str(root / "typed-before.json"),
                                    "preflight_report": str(root / "preflight.json")}))
        return path

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gate = patch.object(scenario, "RESTORATION_GATE", self.root / "gate.json")
        self.gate.start()
        self.addCleanup(self.gate.stop)
        (self.root / "gate.json").write_text('{"status":"PASS"}')
        self.plc = sample()["plc"]
        self.calls = []
        outer = self

        class Scenario(scenario.ScenarioController):
            def state(self): return deepcopy(outer.plc)
            def launch(self, *_a, **_k):
                outer.calls.append("launch")
                return outer.root / "control.json"
            def probe_ready(self, *_a, **_k): pass
            def pre_authorization_gate(self, *_a, **_k): return {"status": "PASS"}
            def action(self, _path, action): outer.calls.append(action)
            def probe_checkpoint(self, *_a, **_k): return {"sample": {"smoke": True}}
            def evidence(self, *_a, **_k): return {"api": {}}
            def collect(self, *_a, **_k):
                return {"terminal": {"status": "complete"}, "orphan": False}
            def verify_clean(self, *_a): return {"restored": True}
        self.sc = Scenario()

        class Monitor:
            def start(self, *_a, **_k):
                outer.calls.append("monitor_start")
                return outer.root / "monitor.json"
            def probe(self, *_a, **_k): return {"run_id": "monitor"}
            def collect(self, *_a, **_k):
                return {"orphan": False, "cleanup_errors": []}
        self.monitor = Monitor()

    def preflight(self, report, _log):
        self.calls.append("fresh_preflight")
        report.write_text(json.dumps({"schema_version": 1, "status": "PASS", "live": True,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": hashlib.sha256(scenario.MANIFEST.read_bytes()).hexdigest(),
            "source_and_guest_hashes": len(json.loads(scenario.MANIFEST.read_text())["components"]),
            "program_identity": 24113}))

    def capture(self):
        self.calls.append("fresh_baseline")
        result = sample()
        result["captured_utc"] = datetime.now(timezone.utc).isoformat()
        return result

    def reserve_aged(self, case="smoke"):
        reservation = self.sc.reserve(self.root / "evidence", case)
        record = json.loads(reservation.read_text())
        record["reserved_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
        reservation.write_text(json.dumps(record))
        return reservation

    def test_late_human_then_fresh_baseline_runs_no_package_lifecycle(self):
        reservation = self.reserve_aged()
        outcome = attempt.run_reserved(
            reservation, datetime.now(timezone.utc).isoformat(), scenario=self.sc,
            monitor=self.monitor, preflight_run=self.preflight, capture=self.capture,
            proxy_start=lambda _p: None)
        self.assertEqual(outcome["status"], "PASS")
        self.assertGreater(outcome["reservation_age_seconds"], 600)
        self.assertLess(self.calls.index("fresh_preflight"), self.calls.index("fresh_baseline"))
        self.assertLess(self.calls.index("fresh_baseline"), self.calls.index("launch"))

    def test_changed_critical_state_fails_before_worker_or_receipt(self):
        reservation = self.reserve_aged("lane_hold")
        def changed():
            value = self.capture()
            value["plc"]["seed"] = 138
            return value
        outcome = attempt.run_reserved(
            reservation, datetime.now(timezone.utc).isoformat(), "unused_receipt_123",
            scenario=self.sc, monitor=self.monitor,
            preflight_run=self.preflight, capture=changed)
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["service_restoration"], "NOT_TOUCHED")
        self.assertIn("configuration changed", outcome["error"])
        self.assertNotIn("monitor_start", self.calls)
        self.assertFalse((self.root / "evidence" / reservation.stem).exists())

    def test_expired_approval_consumes_reservation_without_preflight(self):
        reservation = self.reserve_aged("lane_hold")
        old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        outcome = attempt.run_reserved(
            reservation, old, "unused_receipt_123", scenario=self.sc,
            monitor=self.monitor, preflight_run=self.preflight, capture=self.capture)
        self.assertEqual(outcome["status"], "FAIL")
        self.assertIn("expired", outcome["error"])
        self.assertNotIn("fresh_preflight", self.calls)
        with self.assertRaises(FileExistsError):
            attempt.run_reserved(reservation, datetime.now(timezone.utc).isoformat(),
                                 "another_receipt_123", scenario=self.sc)

    def test_approval_expiring_during_fresh_reads_stops_before_worker(self):
        reservation = self.reserve_aged("lane_hold")
        with patch.object(attempt, "approval_age", side_effect=[1.0, ValueError("operator approval expired")]):
            outcome = attempt.run_reserved(
                reservation, datetime.now(timezone.utc).isoformat(), "unused_receipt_123",
                scenario=self.sc, monitor=self.monitor,
                preflight_run=self.preflight, capture=self.capture)
        self.assertEqual(outcome["status"], "FAIL")
        self.assertIn("expired", outcome["error"])
        self.assertEqual(self.calls, ["fresh_preflight", "fresh_baseline"])
        self.assertEqual(outcome["service_restoration"], "NOT_TOUCHED")

    def test_tampered_reservation_scope_is_rejected(self):
        reservation = self.reserve_aged("lane_hold")
        row = json.loads(reservation.read_text())
        row["start_argv"] = ["sudo", "systemctl", "start", "sorter-plant.service"]
        reservation.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            self.sc.claim_reservation(reservation)
        self.assertNotIn("fresh_preflight", self.calls)

    def test_missing_approval_rejected_before_monitor(self):
        with tempfile.TemporaryDirectory() as name:
            prep = self.preparation(Path(name), "lane_hold")
            with self.assertRaisesRegex(ValueError, "approval"):
                attempt.run_attempt(prep)

    def test_monitor_failure_collects_and_cannot_pass(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prep = self.preparation(root, "smoke")
            calls = []

            class Monitor:
                def start(self, *_a, **_k):
                    calls.append("start")
                    return root / "monitor.json"
                def probe(self, *_a, **_k):
                    raise TimeoutError("no durable initial sample")
                def collect(self, *_a, **_k):
                    calls.append("collect")
                    return {"orphan": False, "cleanup_errors": []}

            result = attempt.run_attempt(prep, scenario=object(), monitor=Monitor())
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["functional_outcome"], "FAIL")
            self.assertEqual(calls, ["start", "collect"])
            self.assertTrue((prep.parent / "attempt-result.json").exists())

    def test_lane_attempt_orders_fixture_browser_release_and_cleanup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prep = self.preparation(root, "lane_hold")
            calls = []

            class Process:
                def poll(self): return 0
                def wait(self, **_k): return 0

            class Monitor:
                def start(self, *_a, **_k):
                    calls.append("monitor_start")
                    return root / "monitor.json"
                def probe(self, *_a, **_k):
                    calls.append("monitor_ready")
                    return {"run_id": "monitor"}
                def collect(self, *_a, **_k):
                    calls.append("monitor_collect")
                    return {"orphan": False, "cleanup_errors": []}

            class Scenario:
                scada = SimpleNamespace(run=lambda argv, **_k:
                    calls.append("operator_reset") or json.dumps({"reset_requested": True,
                                                                   "counters_zero": True,
                                                                   "zone_ready_zero": True}))
                def record_approval(self, *_a):
                    calls.append("approval")
                    return {"run_id": prep.parent.name}
                def launch(self, *_a, **_k):
                    calls.append("launch")
                    return root / "control.json"
                def probe_ready(self, *_a, **_k): calls.append("worker_ready")
                def pre_authorization_gate(self, *_a, **_k):
                    calls.append("gate")
                    return {"status": "PASS"}
                def action(self, _p, action): calls.append(action)
                def fixture_authorize(self, *_a): calls.append("fixture_authorize")
                def fixture_start(self, *_a, **_k): calls.append("fixture_start")
                def fixture_probe(self, *_a): calls.append("fixture_ready")
                def probe_checkpoint(self, *_a, **_k):
                    calls.append("checkpoint")
                    return {"sample": {"rows": []}}
                def evidence(self, *_a, **_k):
                    calls.append("evidence")
                    return {"api": {}}
                def collect(self, *_a, **_k):
                    calls.append("worker_collect")
                    (prep.parent / "stdout.log").write_text(json.dumps({"journal": [], "rows": []}) + "\n")
                    return {"terminal": {"status": "complete",
                            "fixture_restoration": {"service_restored": True}},
                            "orphan": False}
                def state(self):
                    return {"slots": [[0] * 12 for _ in range(3)],
                            "coils_880_920": [False] * 41,
                            "plant_faults": [0, 0, 0],
                            "run_identity": {"epoch_fault": 0},
                            "photoeye_faults": [0, 0, 0],
                            "zone_view": [0] * 23}
                def verify_clean(self, *_a):
                    calls.append("postflight")
                    return {"restored": True}

            def browser(_case, path):
                calls.append("browser")
                screenshot = path / "lane_hold.png"
                screenshot.write_bytes(b"fake test screenshot")
                return {"screenshot": str(screenshot)}

            result = attempt.run_attempt(
                prep, "fresh_receipt_123", scenario=Scenario(), monitor=Monitor(),
                proxy_start=lambda _p: (calls.append("proxy") or Process()),
                driver_start=lambda _p: (calls.append("driver") or Process()),
                browser_run=browser)
            self.assertEqual(result["status"], "PASS")
            self.assertLess(calls.index("approval"), calls.index("monitor_start"))
            self.assertLess(calls.index("gate"), calls.index("fixture_start"))
            self.assertLess(calls.index("checkpoint"), calls.index("browser"))
            self.assertLess(calls.index("browser"), calls.index("release"))
            self.assertLess(calls.index("release"), calls.index("worker_collect"))
            self.assertLess(calls.index("worker_collect"), calls.index("operator_reset"))
            self.assertLess(calls.index("operator_reset"), calls.index("postflight"))
            self.assertTrue((prep.parent / "outcome-before-reset.json").exists())
            self.assertEqual(result["plc_recovery"]["counters_zero"], True)

    def test_postflight_transport_failure_gets_one_fresh_probe_and_retry(self):
        with tempfile.TemporaryDirectory() as name:
            calls = []
            class Scenario:
                def verify_clean(self, _path):
                    calls.append("read")
                    if calls.count("read") == 1:
                        raise PreflightFailure("liveness_wrapper_error", "scada", "processes:scada")
                    return {"restored": True}
            result = attempt.verify_postflight(
                Scenario(), Path(name) / "control", Path(name),
                shell_probe=lambda *_a: calls.append("probe"))
            self.assertTrue(result["restored"])
            self.assertEqual(calls, ["read", "probe", "read"])
            rows = json.loads((Path(name) / "postflight-attempts.json").read_text())
            self.assertEqual([row["status"] for row in rows], ["FAIL", "PASS", "PASS"])

    def test_typed_mismatch_is_not_retried(self):
        with tempfile.TemporaryDirectory() as name:
            calls = []
            class Scenario:
                def verify_clean(self, _path):
                    calls.append("read")
                    raise AssertionError("typed restoration failed: counters remain")
            with self.assertRaisesRegex(AssertionError, "typed restoration failed"):
                attempt.verify_postflight(Scenario(), Path(name) / "control", Path(name),
                                          shell_probe=lambda *_a: calls.append("probe"))
            self.assertEqual(calls, ["read"])

    def test_failed_operator_reset_remains_separate_from_functional_outcome(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prep = self.preparation(root, "lane_hold")
            class Monitor:
                def start(self, *_a, **_k): return root / "monitor.json"
                def probe(self, *_a, **_k): return {"run_id": "monitor"}
                def collect(self, *_a, **_k): return {"orphan": False, "cleanup_errors": []}
            class Process:
                def poll(self): return 0
                def wait(self, **_k): return 0
            class Scenario:
                scada = SimpleNamespace(run=lambda *_a, **_k: (_ for _ in ()).throw(
                    TimeoutError("scanner reset ACK timeout")))
                def record_approval(self, *_a): return {"run_id": prep.parent.name}
                def launch(self, *_a, **_k): return root / "control.json"
                def probe_ready(self, *_a, **_k): pass
                def pre_authorization_gate(self, *_a, **_k): return {"status": "PASS"}
                def action(self, *_a): pass
                def fixture_authorize(self, *_a): pass
                def fixture_start(self, *_a, **_k): pass
                def fixture_probe(self, *_a): pass
                def probe_checkpoint(self, *_a, **_k): return {"sample": {"rows": []}}
                def evidence(self, *_a, **_k): return {"api": {}}
                def collect(self, *_a, **_k):
                    (prep.parent / "stdout.log").write_text(json.dumps({"journal": [], "rows": []}) + "\n")
                    return {"terminal": {"status": "complete",
                            "fixture_restoration": {"service_restored": True}}, "orphan": False}
                def state(self):
                    return {"slots": [[0] * 12 for _ in range(3)],
                            "coils_880_920": [False] * 41,
                            "plant_faults": [0, 0, 0], "run_identity": {"epoch_fault": 0},
                            "photoeye_faults": [0, 0, 0], "zone_view": [0] * 23}
                def verify_clean(self, *_a):
                    raise AssertionError("must not take typed snapshot before reset")
            def browser(_case, path):
                screenshot = path / "lane_hold.png"
                screenshot.write_bytes(b"test")
                return {"screenshot": str(screenshot)}
            result = attempt.run_attempt(
                prep, "receipt", scenario=Scenario(), monitor=Monitor(),
                proxy_start=lambda _p: Process(), driver_start=lambda _p: Process(),
                browser_run=browser)
            self.assertEqual(result["functional_outcome"], "PASS")
            self.assertEqual(result["plc_recovery"], "FAIL")
            self.assertEqual(result["typed_postflight"], "FAIL")
            self.assertIn("scanner reset ACK timeout", result["errors"]["plc_recovery"])
            self.assertTrue((prep.parent / "outcome-before-reset.json").exists())

    def test_interrupted_evidence_aborts_and_never_passes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prep = self.preparation(root, "smoke")
            calls = []

            class Monitor:
                def start(self, *_a, **_k): return root / "monitor.json"
                def probe(self, *_a, **_k): return {"run_id": "monitor"}
                def collect(self, *_a, **_k):
                    calls.append("monitor_collect")
                    return {"orphan": False, "cleanup_errors": []}

            class Scenario:
                def launch(self, *_a, **_k): return root / "control.json"
                def probe_ready(self, *_a, **_k): pass
                def pre_authorization_gate(self, *_a, **_k): return {"status": "PASS"}
                def action(self, _p, name): calls.append(name)
                def probe_checkpoint(self, *_a, **_k): return {"sample": {"smoke": True}}
                def evidence(self, *_a, **_k): raise InterruptedError("host interrupted")
                def collect(self, *_a, **_k):
                    calls.append("worker_collect")
                    return {"terminal": {"status": "scenario_failure"}, "orphan": False}
                def state(self):
                    return {"slots": [[0] * 12 for _ in range(3)],
                            "plant_faults": [0, 0, 0], "run_identity": {"epoch_fault": 0},
                            "photoeye_faults": [0, 0, 0], "zone_view": [0] * 23}
                def verify_clean(self, *_a):
                    calls.append("postflight")
                    return {"restored": True}

            class Proxy:
                def poll(self): return 0
                def wait(self, **_k): return 0

            result = attempt.run_attempt(prep, scenario=Scenario(), monitor=Monitor(),
                                         proxy_start=lambda _p: Proxy())
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["functional_outcome"], "FAIL")
            self.assertEqual(result["typed_postflight"], "PASS")
            self.assertIn("abort", calls)
            self.assertNotIn("release", calls)
            self.assertLess(calls.index("worker_collect"), calls.index("postflight"))


if __name__ == "__main__":
    unittest.main()

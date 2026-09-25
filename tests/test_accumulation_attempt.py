"""Focused lifecycle and committed-read tests; no live PLC or service calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import check_accumulation_views as views
import run_accumulation_attempt as attempt
import accumulation_scenario_control as scenario


class Reply:
    def __init__(self, values):
        self.registers = values
        self.bits = values

    def isError(self):
        return False


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


if __name__ == "__main__":
    unittest.main()

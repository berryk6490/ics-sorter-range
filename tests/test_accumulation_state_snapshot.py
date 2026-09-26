"""Typed Phase 2A restoration contract with no live Modbus traffic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import accumulation_state_snapshot as snap
import read_accumulation_state as reader
import accumulation_scenario_control as scenario


def sample():
    vfds = {name: {"host": "10.10.1.21", "command": 0, "setpoint": 200,
                   "belt_load": 0, "status": 1, "frequency": 0,
                   "feedback_rpm": 0, "fault": 0, "current": 0, "thermal": 0}
            for name in snap.VFD_NAMES}
    return {"schema_version": snap.SCHEMA, "program_identity": 24114,
            "captured_utc": "2026-09-24T00:00:00Z",
            "plc": {"reader_schema": 2, "identity": 24114,
                    "coils_880_920": [False] * 41,
                    "setpoints_200_210": [200] * 11,
                    "photoeye_config_744_747": [2, 2, 120, 400],
                    "seed": 137, "slots": [[0] * 12 for _ in range(3)],
                    "lanes": [0] * 3, "process_214_242": [0] * 29,
                    "scanner_counters_250_254": [0] * 5,
                    "run_identity": {"run_id": 1, "epoch": [1, 0],
                                     "epoch_fault": 0, "scanner_nonce": 1,
                                     "plant_epoch_nonce": [1, 0, 1]},
                    "plant_faults": [0] * 3, "photoeye_faults": [0] * 3,
                    "scanner_state": 0, "scanner_fault_mask": 0,
                    "xle_health": [0, 0], "zone_view": [0] * 23,
                    "zone_raw": [0] * 17,
                    "plant_raw": [[0] * 10 for _ in range(3)],
                    "trailer_counters": [0] * 9, "vfds": vfds},
            "environment": {"services": {
                f"{entry['guest']}:{unit}": "active"
                for entry in json.loads(snap.MANIFEST.read_text())["components"]
                for unit in entry["associated_service"]},
                            "vms": {name: "running" for name in snap.VM_NAMES},
                            "canonical_plant": [{"pid": 101,
                                "args": "/home/kevin/venv/bin/python /home/kevin/plant.py"}],
                            "canonical_xle": [{"pid": 102,
                                "args": "/opt/sorter-xle/venv/bin/python /opt/sorter-xle/xle.py"}],
                            "canonical_asx": [{"pid": 103,
                                "args": "/opt/sorter-xle/venv/bin/python /opt/sorter-xle/asx.py"}],
                            "temporary": {"drives": [], "scada": [], "xle": []},
                            "host_proxy": []}}


class Reply:
    def __init__(self, values):
        self.registers = values
        self.bits = values

    def isError(self):
        return False


class FakePLC:
    def read_holding_registers(self, start, count, slave):
        return Reply([24114 if start == 249 else 0] * count)

    def read_coils(self, start, count, slave):
        return Reply([False] * count)


class FakeVFD:
    def __init__(self, host, port, timeout):
        self.host = host

    def connect(self):
        return True

    def read_holding_registers(self, start, count, slave):
        return Reply([0, 200, 0, 1, 0, 0, 0, 0, 0])

    def close(self):
        pass


class SnapshotTests(unittest.TestCase):
    def check_failure(self, edit, field):
        old = sample()
        new = deepcopy(old)
        edit(new)
        report = snap.compare(old, new)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(field in row["field"] for row in report["differences"]))

    def test_complete_reader_schema(self):
        result = reader.read_state(FakePLC(), FakeVFD, include_vfds=True)
        self.assertEqual(result["reader_schema"], 2)
        self.assertEqual(set(result["vfds"]), set(snap.VFD_NAMES))
        self.assertEqual(len(result["process_214_242"]), 29)
        self.assertEqual(len(result["photoeye_config_744_747"]), 4)

    def test_clean_restoration(self):
        old = sample()
        report = snap.compare(old, deepcopy(old))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual({r["class"] for r in report["fields"]},
                         {"EXACT", "RESET_ZERO", "SAFE_INVARIANT",
                          "DYNAMIC_HEALTH", "INFORMATIONAL"})

    def test_missing_required_field(self):
        self.check_failure(lambda s: s["plc"].pop("vfds"), "schema")

    def test_exact_operator_setting_and_vfd_configuration(self):
        self.check_failure(lambda s: s["plc"]["setpoints_200_210"].__setitem__(0, 300),
                           "setpoints_200_210")
        self.check_failure(lambda s: s["plc"]["vfds"]["induct1"].__setitem__("command", 1),
                           "vfds.induct1.command")
        self.check_failure(lambda s: s["plc"]["vfds"]["induct1"].__setitem__("setpoint", 300),
                           "vfds.induct1.setpoint")

    def test_reset_counters_faults_and_slots(self):
        for edit, field in (
            (lambda s: s["plc"]["trailer_counters"].__setitem__(0, 1), "trailer_counters.0"),
            (lambda s: s["plc"]["process_214_242"].__setitem__(6, 1), "process_214_242.6"),
            (lambda s: s["plc"]["slots"][0].__setitem__(4, 1), "slots.0.4"),
            (lambda s: s["plc"]["photoeye_faults"].__setitem__(0, 1), "photoeye_faults.0"),
            (lambda s: s["plc"]["plant_faults"].__setitem__(0, 1), "plant_faults.0"),
        ):
            with self.subTest(field=field):
                self.check_failure(edit, field)

    def test_safe_invariant_orphan_and_master(self):
        self.check_failure(lambda s: s["plc"]["coils_880_920"].__setitem__(0, True),
                           "coils_880_920.0")
        self.check_failure(lambda s: s["environment"]["temporary"]["drives"].append(
            {"pid": 200, "args": "fixture"}), "temporary.drives")

    def test_dynamic_changes_accepted(self):
        old = sample()
        new = deepcopy(old)
        new["plc"]["vfds"]["induct1"]["feedback_rpm"] = 20
        new["plc"]["run_identity"]["scanner_nonce"] = 99
        new["plc"]["zone_view"][4] = 999
        new["environment"]["canonical_plant"][0]["pid"] = 999
        new["captured_utc"] = "later"
        self.assertEqual(snap.compare(old, new)["status"], "PASS")

    def test_inactive_historical_zone_payload_after_reset(self):
        old = sample()
        new = deepcopy(old)
        new["plc"]["zone_view"][:18] = [2, 2, 1, 284, 444, 1] * 3
        self.assertEqual(snap.compare(old, new)["status"], "PASS")
        new["plc"]["zone_view"][20] = 1  # active validated readiness remains unsafe
        self.assertEqual(snap.compare(old, new)["status"], "FAIL")

    def test_post_ready_gate_ignores_inactive_historical_zone_payload(self):
        old = sample()["plc"]
        current = deepcopy(old)
        current["zone_view"][:18] = [2, 2, 1, 284, 444, 1] * 3
        scenario.validate_unchanged_plc(old, current)
        current["zone_view"][20] = 1
        with self.assertRaisesRegex(ValueError, "safe stopped baseline"):
            scenario.validate_unchanged_plc(old, current)

    def test_unsettled_or_faulted_drive(self):
        self.check_failure(lambda s: s["plc"]["vfds"]["induct1"].__setitem__(
            "feedback_rpm", 100), "feedback_rpm")
        self.check_failure(lambda s: s["plc"]["vfds"]["induct1"].__setitem__(
            "fault", 1), "vfds.induct1.fault")

    def test_schema_and_program_identity(self):
        self.check_failure(lambda s: s.__setitem__("schema_version", 99), "schema")
        self.check_failure(lambda s: (s.__setitem__("program_identity", 42),
                                      s["plc"].__setitem__("identity", 42)), "program_identity")

    def test_next_scenario_gate_and_baseline_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = sample()
            now = datetime.now(timezone.utc)
            baseline["captured_utc"] = (now - timedelta(seconds=20)).isoformat()
            source = root / "before.json"
            source.write_text(json.dumps(baseline))
            preflight = root / "preflight.json"
            preflight.write_text(json.dumps({"schema_version": 1, "status": "PASS", "live": True,
                "completed_utc": (now - timedelta(seconds=125)).isoformat(),
                "duration_seconds": 92,
                "manifest_sha256": hashlib.sha256(snap.MANIFEST.read_bytes()).hexdigest(),
                "source_and_guest_hashes": len(json.loads(snap.MANIFEST.read_text())["components"]),
                "program_identity": 24114}))
            with patch.object(scenario, "RESTORATION_GATE", root / "global-gate.json"), \
                 patch.object(scenario, "load_control", return_value={
                    "launched_utc": (now - timedelta(seconds=10)).isoformat()}):
                self.assertEqual(scenario.validate_typed_launch(root, source, "monitor", preflight)[0],
                                 baseline)
                (root / "global-gate.json").write_text('{"status":"PENDING"}')
                with self.assertRaisesRegex(ValueError, "previous scenario"):
                    scenario.validate_typed_launch(root / "another-run", source, "monitor", preflight)
                (root / "global-gate.json").unlink()
            with patch.object(scenario, "RESTORATION_GATE", root / "global-gate.json"), \
                 patch.object(scenario, "load_control", return_value={
                    "launched_utc": (now - timedelta(seconds=30)).isoformat()}):
                with self.assertRaisesRegex(ValueError, "precede monitor"):
                    scenario.validate_typed_launch(root, source, "monitor", preflight)

    def test_92_second_full_preflight_finishes_before_worker_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(timezone.utc)
            baseline = sample()
            baseline["captured_utc"] = (now - timedelta(seconds=22)).isoformat()
            source = root / "typed-before.json"
            source.write_text(json.dumps(baseline))
            receipt = root / "preflight.json"
            receipt.write_text(json.dumps({"schema_version": 1, "status": "PASS", "live": True,
                "completed_utc": (now - timedelta(seconds=32)).isoformat(),
                "duration_seconds": 92.0,
                "manifest_sha256": hashlib.sha256(snap.MANIFEST.read_bytes()).hexdigest(),
                "source_and_guest_hashes": len(json.loads(snap.MANIFEST.read_text())["components"]),
                "program_identity": 24114}))
            scada = Mock()

            def launch_worker(argv):
                self.assertEqual(json.loads(receipt.read_text())["duration_seconds"], 92.0)
                run_id = argv[argv.index("--run-id") + 1]
                return json.dumps({"run_id": run_id, "scenario": "smoke", "pid": 42})

            scada.run.side_effect = launch_worker
            manager = scenario.ScenarioController(scada=scada, drives=Mock(), monitor=Mock())
            manager.state = Mock(return_value={"identity": 24114,
                         "coils_880_920": [False] * 41,
                         "slots": [[0] * 12 for _ in range(3)]})
            with patch.object(scenario, "RESTORATION_GATE", root / "gate.json"), \
                 patch.object(scenario, "load_control", return_value={
                     "launched_utc": (now - timedelta(seconds=12)).isoformat()}):
                control = manager.launch(root / "evidence", "smoke", "monitor-control",
                                         source, receipt)
            self.assertTrue(control.exists())
            scada.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()

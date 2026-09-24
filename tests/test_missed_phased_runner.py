"""Failure and ordering checks for the missed-tunnel evidence orchestrator."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_missed_phased_host as runner


class MissedPhasedRunner(unittest.TestCase):
    def test_plant_return_and_latch_proof_precede_reset_permission(self):
        actions = []
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            with patch.object(runner, "restore_plant", side_effect=lambda pid: (
                    actions.append("plant_return"), {"normal_pid": 20})[1]), \
                 patch.object(runner, "guest", side_effect=lambda vm, command: (
                     actions.append("recover" if command.endswith("/recover") else "plant_marker"), "")[1]), \
                 patch.object(runner, "wait_file", side_effect=lambda *a: actions.append("wait_latch")), \
                 patch.object(runner, "fetch", side_effect=lambda *a: (
                     actions.append("read_latch"), {"modbus": {}, "views": {}})[1]), \
                 patch.object(runner, "validate_phase", side_effect=lambda *a: actions.append("validate_latch")):
                runner.plant_only_phase("/tmp/sorter-missed-test", evidence, 10, 30, {})
        self.assertEqual(actions, ["plant_return", "plant_marker", "wait_latch",
                                   "read_latch", "validate_latch", "recover"])

    def test_missing_latch_observation_does_not_permit_reset(self):
        actions = []
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(runner, "restore_plant", return_value={"normal_pid": 20}), \
                 patch.object(runner, "guest", side_effect=lambda vm, command: actions.append(command)), \
                 patch.object(runner, "wait_file", return_value=None), \
                 patch.object(runner, "fetch", return_value={"modbus": {}}):
                with self.assertRaises((AssertionError, KeyError)):
                    runner.plant_only_phase("/tmp/sorter-missed-test", Path(temporary), 10, 30, {})
        self.assertFalse(any(command.endswith("/recover") for command in actions))

    def test_timeout_is_failure(self):
        with self.assertRaisesRegex(TimeoutError, "fault.json"):
            runner.wait_file("/tmp/sorter-missed-test", "fault.json", 0)

    def test_empty_process_log_is_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(runner, "guest", return_value="EVIDENCE::END\n"):
            path = Path(temporary) / "asx.jsonl"
            self.assertEqual(runner.fetch("/tmp/sorter-missed-test", "asx.jsonl", path), "")
            self.assertEqual(path.read_bytes(), b"")

    def test_evidence_requires_complete_row_and_agreement(self):
        row = [1, 0, 9, 1, 1, 4, 4, 5]
        modbus = {"photoeye_fault": [1, 2, 1], "validated_row": row,
                  "master": False, "trailers": [0] * 9,
                  "slot": [1, 1, 0, 0, 1] + [0] * 7, "inducted": 1}
        item = {"modbus": modbus, "views": {"opc": {"Photoeyes": row,
                "PhotoeyeFaultMask": 1, "PhotoeyeFaultSensor": 2,
                "PhotoeyeFaultLane": 1}, "hmi": {"row": row, "fault": [1, 2, 1]}},
                "detection_latency_ms": 300}
        runner.validate_phase(item, item)
        broken = copy.deepcopy(item)
        broken["views"]["hmi"]["row"] = row[:4]
        with self.assertRaises(AssertionError):
            runner.validate_phase(item, broken)
        broken = copy.deepcopy(item)
        del broken["detection_latency_ms"]
        with self.assertRaises(KeyError):
            runner.validate_phase(broken, item)

    def test_failure_restores_plant_before_guest_abort(self):
        actions = []
        baseline = {"machine": {"vms": {}, "services": {}},
                    "plc": {"run": False, "slots": [0] * 36, "enables": [True] * 7,
                            "setpoints": [1] * 11, "seed": 137,
                            "photoeye_config": [2, 2, 120, 400],
                            "modes": [False] * 6, "photoeye_fault": [0, 0, 0]},
                    "plant_pid": 10, "head": "test"}
        def guest(vm, command, timeout=30):
            if "FIXTURE_PID" in command:
                return "FIXTURE_PID:11"
            if "RUNNER_PID" in command:
                return "RUNNER_PID:12"
            if "/abort" in command:
                actions.append("abort")
            return "13 /home/kevin/venv/bin/python /home/kevin/plant.py"
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(runner, "preflight", return_value=baseline), \
             patch.object(runner, "guest", side_effect=guest), \
             patch.object(runner, "wait_file", side_effect=[TimeoutError("fault missing"), None]), \
             patch.object(runner, "restore_plant", side_effect=lambda pid: actions.append("plant_return")), \
             patch.object(runner, "state", return_value=baseline["machine"]), \
             patch.object(runner, "plc_state", return_value=baseline["plc"]), \
             patch.object(runner, "fetch", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "fault missing"):
                runner.main(Path(temporary))
        self.assertEqual(actions[:2], ["plant_return", "abort"])


if __name__ == "__main__":
    unittest.main()

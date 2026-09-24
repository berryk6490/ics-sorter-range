"""Live runner failure cleanup, with no guest or Modbus connection."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_runner():
    client = types.ModuleType("pymodbus.client")
    client.ModbusTcpClient = Mock()
    pymodbus = types.ModuleType("pymodbus")
    pymodbus.client = client
    xle = types.ModuleType("live_xle_multi")
    for name in ("holding", "coils", "set_coil", "set_register", "events", "stop"):
        setattr(xle, name, Mock())
    plant = types.ModuleType("live_plant")
    plant.wait = Mock()
    modules = {"pymodbus": pymodbus, "pymodbus.client": client,
               "live_xle_multi": xle, "live_plant": plant}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(
            "live_accumulation_test", Path(__file__).with_name("live_accumulation.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class RunnerCleanupTest(unittest.TestCase):
    def test_failure_stops_processes_resets_occupied_slots_then_restores(self):
        runner = load_runner()
        actions = []
        plc = Mock()
        original = {"enables": [True] * 7, "external": [False, False],
                    "plant": False, "photoeye": False,
                    "accumulation": False, "setpoints": [120] * 11, "seed": 137}
        occupied = [1]

        def holding(_, address):
            if address in (534, 546, 651):
                return occupied
            if address == 243:
                return [100 if occupied[0] else 1]
            return [0]

        def coil(_, address, value):
            actions.append(("coil", address, value))
            if address == 910:
                occupied[0] = 0

        with patch.object(runner, "holding", side_effect=holding), \
             patch.object(runner, "set_coil", side_effect=coil), \
             patch.object(runner, "set_register", side_effect=lambda *a: actions.append(("reg", a[1], a[2]))), \
             patch.object(runner, "stop", side_effect=lambda p: actions.append(("stop", p))), \
             patch.object(runner, "wait", side_effect=lambda predicate, *_: self.assertTrue(predicate())):
            errors = runner.cleanup_run(plc, original, "xle", "asx", Mock())
        self.assertEqual(errors, [])
        self.assertLess(actions.index(("coil", 880, False)), actions.index(("stop", "xle")))
        self.assertLess(actions.index(("stop", "asx")), actions.index(("coil", 910, True)))
        self.assertLess(actions.index(("coil", 910, True)), actions.index(("coil", 920, False)))
        plc.close.assert_called_once()

    def test_cleanup_errors_are_returned_without_hiding_original_failure(self):
        runner = load_runner()
        plc = Mock()
        original = {"enables": [True] * 7, "external": [False, False],
                    "plant": False, "photoeye": False,
                    "accumulation": False, "setpoints": [120] * 11, "seed": 137}
        with patch.object(runner, "holding", side_effect=lambda *_: [0]), \
             patch.object(runner, "set_coil", side_effect=RuntimeError("write failed")), \
             patch.object(runner, "set_register", return_value=None), \
             patch.object(runner, "stop", return_value=None):
            errors = runner.cleanup_run(plc, original, None, None, Mock())
        self.assertTrue(any("write failed" in item for item in errors))
        plc.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""Pure state tests for the deployed scanner's Modbus exchange."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest


# The host test needs no TCP server or installed pymodbus package.
for name in ("pymodbus", "pymodbus.server", "pymodbus.datastore"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["pymodbus.server"].StartTcpServer = object()
for name in ("ModbusSequentialDataBlock", "ModbusSlaveContext", "ModbusServerContext"):
    setattr(sys.modules["pymodbus.datastore"], name, object)
spec = importlib.util.spec_from_file_location(
    "sorter_scanner", Path(__file__).resolve().parents[1] / "devices/scanner.py")
scanner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner_module)


class Registers:
    def __init__(self):
        self.values = [0] * 16

    def getValues(self, function, address, count):
        return self.values[address:address + count]

    def setValues(self, function, address, values):
        self.values[address:address + len(values)] = values


class ScannerResetTest(unittest.TestCase):
    def setUp(self):
        self.reg = Registers()
        self.device = scanner_module.Scanner({0: self.reg}, "test", 1)

    def reset(self, nonce, seed=137):
        self.reg.values[:4] = [scanner_module.PREPARE_SEQ, nonce, seed, 30]
        self.device.step()
        self.assertEqual(self.reg.values[4:11],
                         [scanner_module.PREPARE_SEQ, 0, scanner_module.ST_PREPARED,
                          0, 0, 0, nonce])
        self.reg.values[0] = scanner_module.RESET_SEQ
        self.device.step()
        self.assertEqual(self.reg.values[4:11],
                         [scanner_module.RESET_SEQ, 0, scanner_module.ST_RESET,
                          0, 0, 0, nonce])

    def request(self, seq, serial):
        self.reg.values[:2] = [seq, serial]
        self.device.step()
        return self.reg.values[4:11].copy()

    def test_repeated_sequence_after_reset(self):
        self.reset(1)
        first = self.request(1, 1)
        self.reset(2)
        second = self.request(1, 1)
        self.assertEqual(first[:6], second[:6])
        self.assertEqual(second[6], 2)
        self.assertEqual(self.device.scan_count, 1)

    def test_reset_clears_stale_response_and_history(self):
        self.reset(1)
        self.request(1, 1)
        self.assertTrue(self.device.recent)
        self.reset(2)
        self.assertEqual(self.device.recent, [])
        self.assertEqual(self.device.last_seq, 0)
        self.device.step()  # repeated reset command is idempotent
        self.assertEqual(self.reg.values[4:11],
                         [scanner_module.RESET_SEQ, 0, scanner_module.ST_RESET,
                          0, 0, 0, 2])

    def test_same_seed_replays_first_two_scans(self):
        self.reset(1)
        first = [self.request(1, 1)[:6], self.request(2, 2)[:6]]
        self.reset(2)
        second = [self.request(1, 1)[:6], self.request(2, 2)[:6]]
        self.assertEqual(first, second)

    def test_cold_plc_restart_reuses_nonce(self):
        self.reset(1)
        first = self.request(1, 1)
        self.reset(1)  # new PLC process starts its private nonce at 1
        second = self.request(1, 1)
        self.assertEqual(first, second)
        self.assertEqual(self.device.scan_count, 1)

    def test_reset_without_prepare_does_not_ack(self):
        self.reg.values[:4] = [scanner_module.RESET_SEQ, 1, 137, 30]
        self.device.step()
        self.assertEqual(self.reg.values[4:11], [0] * 7)


if __name__ == "__main__":
    unittest.main()

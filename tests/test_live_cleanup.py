"""Exercise live-test cleanup without changing a running PLC."""

import pathlib
import runpy
import sys
import types
import unittest
from unittest.mock import patch


class Reply:
    def __init__(self, *, bits=None, registers=None):
        self.bits = bits
        self.registers = registers

    def isError(self):
        return False


class Client:
    def __init__(self):
        self.closed = False

    def connect(self):
        return True

    def close(self):
        self.closed = True


class Plc(Client):
    def __init__(self, original_lanes, fail_at=None, initial_seed=137):
        super().__init__()
        self.state = {880: False, 883: original_lanes[0], 884: original_lanes[1],
                      247: initial_seed}
        self.events = []
        self.fail_at = fail_at

    def read_coils(self, address, count, unit):
        self.events.append(("read", address, count))
        return Reply(bits=[self.state[address], self.state[address + 1]])

    def read_holding_registers(self, address, count, unit):
        return Reply(registers=[self.state[247] if address == 247 else 18436])

    def read_input_registers(self, address, count, unit):
        raise RuntimeError("injected measurement failure")

    def write_coil(self, address, value, unit):
        self.events.append(("write", address, value))
        if (address, value) == self.fail_at:
            self.fail_at = None
            raise RuntimeError("injected setup failure")
        self.state[address] = value
        return Reply()

    def write_register(self, address, value, unit):
        self.events.append(("register", address, value))
        self.state[address] = value
        return Reply()


def load_run():
    modules = {name: types.ModuleType(name)
               for name in ("pymodbus", "pymodbus.client", "pymodbus.client.sync")}
    modules["pymodbus.client.sync"].ModbusTcpClient = Client
    path = pathlib.Path(__file__).with_name("live_first_package.py")
    with patch.dict(sys.modules, modules):
        return runpy.run_path(str(path), run_name="live_test")["run"]


class CleanupTest(unittest.TestCase):
    def check_cleanup(self, original_lanes, fail_at, expected_error, initial_seed=137):
        plc, drive, camera = Plc(original_lanes, fail_at, initial_seed), Client(), Client()
        with patch("time.sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, expected_error):
                load_run()(plc, drive, camera)

        self.assertEqual(plc.events[0], ("read", 883, 2))
        coil_writes = [event for event in plc.events if event[0] == "write"]
        self.assertEqual(coil_writes[-3:], [("write", 880, False),
                                            ("write", 883, original_lanes[0]),
                                            ("write", 884, original_lanes[1])])
        self.assertEqual([plc.state[880], plc.state[883], plc.state[884]],
                         [False, *original_lanes])
        self.assertEqual(plc.state[247], initial_seed)
        self.assertTrue(all(client.closed for client in (plc, drive, camera)))

    def test_measurement_failure_restores_mixed_lane_values(self):
        self.check_cleanup((False, True), None, "injected measurement failure")

    def test_partial_setup_failure_restores_mixed_lane_values(self):
        self.check_cleanup((True, False), (884, False), "injected setup failure", 281)


if __name__ == "__main__":
    unittest.main()

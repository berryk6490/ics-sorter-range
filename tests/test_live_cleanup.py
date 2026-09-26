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
        self.state = {880: False, 247: initial_seed, 249: 24114,
                      **{address: True for address in range(881, 888)},
                      **{address: 100 + address for address in range(200, 211)}}
        self.state[883], self.state[884] = original_lanes
        self.initial = self.state.copy()
        self.events = []
        self.fail_at = fail_at

    def read_coils(self, address, count, unit):
        self.events.append(("read", address, count))
        return Reply(bits=[self.state[address + index] for index in range(count)])

    def read_holding_registers(self, address, count, unit):
        return Reply(registers=[self.state.get(address + index, 0)
                                for index in range(count)])

    def read_input_registers(self, address, count, unit):
        if address in (158, 169, 180):
            return Reply(registers=[32767])
        if address in (160, 171, 182):
            return Reply(registers=[6])
        raise RuntimeError("injected measurement failure")

    def write_coil(self, address, value, unit):
        self.events.append(("write", address, value))
        if (address, value) == self.fail_at:
            self.fail_at = None
            raise RuntimeError("injected setup failure")
        self.state[address] = value
        if address == 910 and value:
            for register in range(200, 211):
                self.state[register] = 1
            for coil in range(881, 888):
                self.state[coil] = True
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

        self.assertEqual(plc.events[0], ("read", 881, 7))
        self.assertEqual([plc.state[880], plc.state[883], plc.state[884]],
                         [False, *original_lanes])
        self.assertEqual(plc.state[247], initial_seed)
        self.assertEqual([plc.state[x] for x in range(881, 888)],
                         [plc.initial[x] for x in range(881, 888)])
        self.assertEqual([plc.state[x] for x in range(200, 211)],
                         [plc.initial[x] for x in range(200, 211)])
        self.assertTrue(all(client.closed for client in (plc, drive, camera)))

    def test_measurement_failure_restores_mixed_lane_values(self):
        self.check_cleanup((False, True), None, "injected measurement failure")

    def test_partial_setup_failure_restores_mixed_lane_values(self):
        self.check_cleanup((True, False), (884, False), "injected setup failure", 281)


if __name__ == "__main__":
    unittest.main()

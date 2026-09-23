"""Process-restart behavior at the existing PLC two-slot Modbus boundary."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "recovery_xle", Path(__file__).resolve().parents[1] / "services/xle.py")
xle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xle)


class Reply:
    def __init__(self, values):
        self.registers = values
        self.bits = values

    def isError(self):
        return False


class PLC:
    def __init__(self, state):
        self.nonce = 7
        self.row = [41, 2, 9, 6001, state, 2, 2 if state == 5 else 0,
                    0, 100, 102, 133, 10]
        self.other = [0] * 12
        self.command_id = 10
        self.ack_id = 10
        self.ack = 1
        self.payload = None
        self.releases = 0
        self.routes = 0
        self.route_keys = []
        self.polls = 0
        self.fail_release_once = False

    def connect(self):
        return True

    def read_coils(self, address, count, slave):
        return Reply([True, True])

    def read_holding_registers(self, address, count, slave):
        if address == 528: return Reply([self.command_id])
        if address == 509: return Reply([self.nonce])
        if address == 554: return Reply([self.ack_id])
        if address == 529: return Reply([self.ack])
        if address == 530:
            self.polls += 1
            if self.row[4] == 3 and self.polls >= 3:
                self.row[4] = 5
                self.row[6] = 2
            return Reply(self.row + self.other)
        raise AssertionError((address, count))

    def write_registers(self, address, values, slave):
        assert address == 520
        self.payload = values
        return Reply([])

    def write_register(self, address, value, slave):
        assert address == 528
        self.command_id = self.ack_id = value
        if self.payload[0] == 2:
            if self.fail_release_once:
                self.fail_release_once = False
                raise RuntimeError("simulated process crash before release ACK")
            self.releases += 1
            self.row[0] = self.row[4] = 0
            self.ack = 3
        else:
            valid = (self.payload[2:6] == self.row[:4] and
                     self.payload[7] == self.nonce and self.row[4] == 2)
            if valid:
                self.routes += 1
                self.route_keys.append((self.nonce, *self.row[:4]))
                self.row[4] = 3
                self.row[5] = self.payload[6]
                self.row[11] = value
                self.ack = 1
            else:
                self.ack = 2
        return Reply([])


class Recovery(unittest.TestCase):
    def test_restart_with_already_terminal_slot(self):
        plc = PLC(5)
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "event") as emit:
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=.6,
                                           journal=str(Path(directory) / "outcomes.sqlite3"),
                                           terminal_hold=0), 0)
        self.assertEqual(plc.releases, 1)
        self.assertEqual(plc.routes, 0)
        self.assertEqual(sum(c.args[0] == "plc_outcome" for c in emit.call_args_list), 1)

    def test_restart_with_accepted_route_waits_for_outcome(self):
        plc = PLC(3)
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "event") as emit:
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=.7,
                                           journal=str(Path(directory) / "outcomes.sqlite3"),
                                           terminal_hold=0), 0)
        self.assertEqual(plc.releases, 1)
        self.assertEqual(plc.routes, 0)
        self.assertEqual(sum(c.args[0] == "plc_outcome" for c in emit.call_args_list), 1)

    def test_restart_with_scanned_slot_reissues_identity_bound_lookup(self):
        plc = PLC(2)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(xle, "event") as emit, patch.object(xle, "lookup", return_value=(2, None)):
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=1,
                                           journal=str(Path(directory) / "outcomes.sqlite3"),
                                           terminal_hold=0), 0)
        self.assertEqual(plc.route_keys, [(7, 41, 2, 9, 6001)])
        self.assertEqual(plc.releases, 1)
        self.assertEqual(sum(c.args[0] == "plc_outcome" for c in emit.call_args_list), 1)

    def test_terminal_outcome_is_durable_once_across_restart(self):
        plc = PLC(5)
        plc.fail_release_once = True
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "event") as emit:
            journal = str(Path(directory) / "outcomes.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                xle.run_multi(plc, "unused", packages=1, deadline=.6,
                              journal=journal, terminal_hold=0)
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=.6,
                                           journal=journal, terminal_hold=0), 0)
        self.assertEqual(plc.releases, 1)
        self.assertEqual(sum(c.args[0] == "plc_outcome" for c in emit.call_args_list), 1)

    def test_nonce_change_discards_in_flight_asx_answer(self):
        plc = PLC(2)
        started = threading.Event()
        release_old = threading.Event()
        result = []

        def lookup(request, url):
            if request["scanner_run_nonce"] == 7:
                started.set()
                release_old.wait(2)
            return 2, None

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(xle, "event") as emit, patch.object(xle, "lookup", side_effect=lookup):
            journal = str(Path(directory) / "outcomes.sqlite3")
            def serve():
                try:
                    result.append(xle.run_multi(plc, "unused", packages=1, deadline=2,
                                                journal=journal, terminal_hold=0))
                except Exception as exc:
                    result.append(exc)
            thread = threading.Thread(target=serve)
            thread.start()
            self.assertTrue(started.wait(1))
            plc.nonce = 8
            plc.row = [42, 1, 1, 6001, 2, 0, 0, 0, 5, 0, 0, 0]
            plc.polls = 0
            release_old.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])
        self.assertEqual(plc.route_keys, [(8, 42, 1, 1, 6001)])
        self.assertEqual(plc.releases, 1)
        self.assertTrue(any(c.args[0] == "run_abandoned" for c in emit.call_args_list))


if __name__ == "__main__":
    unittest.main()

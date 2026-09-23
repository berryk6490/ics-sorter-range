"""Process-restart behavior at the existing PLC two-slot Modbus boundary."""
import importlib.util
from pathlib import Path
import sqlite3
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
        self.epoch = 0
        self.offer_id = 0
        self.ack_offer_id = 0
        self.fault_request = 0
        self.command_epoch = 0
        self.stopped = True
        self.heartbeat_sequence = 0
        self.liveness = 0
        self.recovery_sequence = 0
        self.recirc_after = False

    def connect(self):
        return True

    def read_coils(self, address, count, slave):
        return Reply([True, True])

    def write_coil(self, address, value, slave):
        assert address == 880 and value is False
        self.stopped = True
        return Reply([])

    def read_holding_registers(self, address, count, slave):
        if address == 528: return Reply([self.command_id])
        if address == 509: return Reply([self.nonce])
        if address == 554: return Reply([self.ack_id])
        if address == 529: return Reply([self.ack])
        if address == 558: return Reply([self.epoch % 30000, self.epoch // 30000])
        if address == 557: return Reply([self.offer_id])
        if address == 560: return Reply([self.ack_offer_id])
        if address == 564: return Reply([self.fault_request])
        if address == 255: return Reply([0])
        if address == 567: return Reply([self.heartbeat_sequence])
        if address == 569: return Reply([self.liveness])
        if address == 572: return Reply([self.recovery_sequence])
        if address == 573: return Reply([self.heartbeat_sequence])
        if address == 644: return Reply([1, 0])
        if address == 530:
            self.polls += 1
            if self.recirc_after and self.row[4] == 2 and self.polls >= 3:
                self.row[4] = 6
                self.row[7] = 1
            if self.row[4] == 3 and self.polls >= 3:
                self.row[4] = 5
                self.row[6] = 2
            return Reply(self.row + self.other)
        raise AssertionError((address, count))

    def write_registers(self, address, values, slave):
        if address == 555:
            self.offer = values
            return Reply([])
        if address == 562:
            self.command_epoch = values[0] + values[1] * 30000
            return Reply([])
        if address in (565, 570):
            return Reply([])
        assert address == 520
        self.payload = values
        return Reply([])

    def write_register(self, address, value, slave):
        if address == 557:
            self.offer_id = self.ack_offer_id = value
            self.epoch = self.offer[0] + self.offer[1] * 30000
            return Reply([])
        if address == 564:
            self.fault_request = value
            return Reply([])
        if address == 567:
            self.heartbeat_sequence = value
            return Reply([])
        if address == 572:
            self.recovery_sequence = value
            self.liveness = 0
            return Reply([])
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
            valid = (self.command_epoch == self.epoch and
                     self.payload[2:6] == self.row[:4] and
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
    def test_lane_two_identity_and_asx_request(self):
        request = xle.package_request(8, 3,
                                      [41, 2, 9, 5002, 2, 3, 0, 0, 100, 0, 0, 0],
                                      lane=2)
        self.assertEqual(request["package_id"], "l2-8-3-41-2")
        self.assertEqual(request["lane"], 2)

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

    def test_same_seed_cold_restart_keeps_distinct_journal_outcomes(self):
        plc = PLC(5)
        with tempfile.TemporaryDirectory() as directory:
            journal = str(Path(directory) / "outcomes.sqlite3")
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=1,
                                           journal=journal, terminal_hold=0), 0)
            first_epoch = plc.epoch
            # A cold PLC restart repeats nonce, token, serial, and scan sequence.
            plc.epoch = 0
            plc.row = [41, 2, 9, 6001, 5, 5, 5, 0, 100, 102, 133, 10]
            with patch.object(xle, "event") as emit:
                self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=1,
                                               journal=journal, terminal_hold=0), 0)
            self.assertNotEqual(first_epoch, plc.epoch)
            self.assertEqual(sum(c.args[0] == "plc_outcome" for c in emit.call_args_list), 1)
            db = xle.OutcomeJournal(journal)
            self.assertEqual(db.db.execute("SELECT count(*) FROM outcomes").fetchone()[0], 2)
            db.close()

    def test_unknown_active_epoch_fails_closed(self):
        plc = PLC(2)
        plc.epoch = 17
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "event") as emit:
            with self.assertRaisesRegex(RuntimeError, "absent from XLe journal"):
                xle.run_multi(plc, "unused", packages=1, deadline=.5,
                              journal=str(Path(directory) / "outcomes.sqlite3"))
        self.assertTrue(plc.stopped)
        self.assertEqual(plc.fault_request, 2)
        self.assertTrue(any(c.args[0] == "external_sort_fault" for c in emit.call_args_list))

    def test_unreadable_identity_journal_fails_closed(self):
        plc = PLC(2)
        plc.epoch = 17
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(xle.OutcomeJournal, "has_epoch", side_effect=sqlite3.DatabaseError("corrupt")):
            with self.assertRaisesRegex(RuntimeError, "journal identity check failed"):
                xle.run_multi(plc, "unused", packages=1, deadline=.5,
                              journal=str(Path(directory) / "outcomes.sqlite3"))
        self.assertTrue(plc.stopped)
        self.assertEqual(plc.fault_request, 2)

    def test_liveness_fault_recirculates_undecided_slot_without_route(self):
        plc = PLC(2)
        plc.liveness = 1
        plc.recirc_after = True
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "lookup") as lookup:
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=1,
                                           journal=str(Path(directory) / "outcomes.sqlite3"),
                                           terminal_hold=0), 0)
        lookup.assert_not_called()
        self.assertEqual(plc.routes, 0)
        self.assertEqual(plc.liveness, 1)

    def test_retry_state_requires_fresh_journal_proof(self):
        plc = PLC(5)
        plc.liveness = 3
        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "event") as emit:
            self.assertEqual(xle.run_multi(plc, "unused", packages=1, deadline=1,
                                           journal=str(Path(directory) / "outcomes.sqlite3"),
                                           terminal_hold=0), 0)
        self.assertEqual(plc.recovery_sequence, 1)
        self.assertEqual(plc.liveness, 0)
        self.assertEqual(plc.routes, 0)
        self.assertTrue(any(c.args[0] == "xle_recovery_proved" for c in emit.call_args_list))

    def test_old_asx_answer_cannot_route_reused_slot_after_cold_restart(self):
        plc = PLC(2)
        started = threading.Event()
        release_old = threading.Event()
        result = []

        def lookup(request, url):
            if request["run_epoch"] == 1:
                started.set()
                release_old.wait(2)
            return 2, None

        with tempfile.TemporaryDirectory() as directory, patch.object(xle, "lookup", side_effect=lookup):
            journal = str(Path(directory) / "outcomes.sqlite3")
            def serve():
                result.append(xle.run_multi(plc, "unused", packages=1, deadline=2,
                                            journal=journal, terminal_hold=0))
            thread = threading.Thread(target=serve)
            thread.start()
            self.assertTrue(started.wait(1))
            plc.epoch = 0
            plc.row = [41, 2, 9, 6001, 2, 0, 0, 0, 5, 0, 0, 0]
            plc.polls = 0
            release_old.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])
        self.assertEqual(plc.routes, 1)
        self.assertEqual(plc.command_epoch, 2)


if __name__ == "__main__":
    unittest.main()

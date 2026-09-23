import unittest
from unittest.mock import patch

import case_a_attack as attack


class Result:
    def __init__(self, registers=None):
        self.registers = registers

    def isError(self):
        return False


class PLC:
    def __init__(self, value=14, program_hash=24112, fail_attack=False):
        self.value = value
        self.program_hash = program_hash
        self.fail_attack = fail_attack
        self.writes = []

    def read_holding_registers(self, address, count, slave):
        assert slave == 1
        values = {209: self.value, 249: self.program_hash}
        return Result([values.get(address + i, 0) for i in range(count)])

    def write_register(self, address, value, slave):
        self.writes.append((address, value, slave))
        if address != 209:
            raise AssertionError("unexpected write target")
        self.value = value
        if self.fail_attack and value == 32000:
            raise RuntimeError("reply lost after PLC applied write")
        return Result()


class CaseAAttackTest(unittest.TestCase):
    ROUTE = "10.10.1.10 via 10.10.3.1 dev enp7s0 src 10.10.3.10 uid 1000"

    def test_lab_source_and_route_required(self):
        attack.require_lab("analyst", self.ROUTE)
        for host, route in (("workstation", self.ROUTE),
                            ("analyst", self.ROUTE.replace("10.10.1.10", "10.10.1.11")),
                            ("analyst", self.ROUTE.replace("10.10.3.10", "10.10.3.11")),
                            ("analyst", self.ROUTE.replace("10.10.3.1", "10.10.3.254"))):
            with self.subTest(host=host, route=route), self.assertRaises(RuntimeError):
                attack.require_lab(host, route)

    def test_dry_run_makes_no_write(self):
        plc = PLC()
        self.assertTrue(attack.execute(plc, dry_run=True)["dry_run"])
        self.assertEqual(plc.writes, [])

    def test_wrong_identity_or_prevalue_never_writes(self):
        for plc in (PLC(program_hash=0), PLC(value=15)):
            with self.assertRaises(RuntimeError):
                attack.execute(plc, dry_run=False, wait_seconds=0)
            self.assertEqual(plc.writes, [])

    def test_timeout_waiting_for_active_lab_never_writes(self):
        plc = PLC()
        with patch.object(attack, "active_guard", return_value=None):
            with self.assertRaises(TimeoutError):
                attack.execute(plc, wait_seconds=0)
        self.assertEqual(plc.writes, [])

    def test_failed_attack_reply_restores_original(self):
        plc = PLC(fail_attack=True)
        with patch.object(attack, "active_guard", return_value={"epoch": [1, 0],
                                                                 "nonce": 1, "slots": [1, 2, 3]}):
            with self.assertRaisesRegex(RuntimeError, "reply lost"):
                attack.execute(plc, wait_seconds=1)
        self.assertEqual(plc.writes, [(209, 32000, 1), (209, 14, 1)])
        self.assertEqual(plc.value, 14)


if __name__ == "__main__":
    unittest.main()

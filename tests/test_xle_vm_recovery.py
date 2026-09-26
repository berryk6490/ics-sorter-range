"""The dedicated-VM operator recovery contract, with no live PLC writes."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


class Reply:
    def __init__(self, values):
        self.registers = values
        self.bits = values

    def isError(self):
        return False


class PlantRecoveryPLC:
    """One scan after each Modbus operation; modes reproduce the PLC fault gate."""
    def __init__(self, *, recommit=True):
        self.coils = {880: False, 914: False, 915: False, 918: False,
                      919: False, 920: False}
        self.nonce = 38
        self.epoch = 23
        self.plant_epoch = (23, 0, 38)
        self.fault = 0
        self.slot_states = [4, 0, 0]  # undecided package; fallback was in motion
        self.process = {address: 0 for address in range(214, 243)}
        self.process[220] = 1
        self.process[221] = 2
        self.events = [(23, 38, 183)]
        self.acked_event = 182
        self.writes = []
        self.heartbeat = 400
        self.recommit = recommit
        self.ready = 1

    def scan(self):
        if self.coils[918] and self.coils[920]:
            # PLC publishes capacity while accumulation is enabled and keeps
            # its last mask after the temporary modes are dropped.
            self.ready = 7
        if self.coils[918]:
            if self.coils[914] and self.coils[915] and self.epoch:
                if self.recommit:
                    self.plant_epoch = (self.epoch, 0, self.nonce)
                    self.heartbeat += 1
                self.fault = 0 if self.plant_epoch == (self.epoch, 0, self.nonce) else 2
            else:
                self.fault = 2
        # With plant mode off, the PLC retains its last displayed fault.

    def connect(self): return True
    def close(self): pass

    def read_holding_registers(self, address, count, slave):
        self.scan()
        values = {249: 24115, 509: self.nonce, 558: self.epoch, 559: 0,
                  561: 0, 567: 1, 573: 1, 591: self.fault,
                  590: self.heartbeat, 593: 0, 786: self.ready, 788: 0,
                  255: 0, 256: 0, 585: 183, 586: self.acked_event,
                  587: self.plant_epoch[0], 588: self.plant_epoch[1],
                  589: self.plant_epoch[2], 534: self.slot_states[0],
                  546: self.slot_states[1], 651: self.slot_states[2]}
        return Reply([values.get(a, self.process.get(a, 0))
                      for a in range(address, address + count)])

    def read_coils(self, address, count, slave):
        self.scan()
        return Reply([self.coils.get(a, False) for a in range(address, address + count)])

    def write_coil(self, address, value, slave):
        self.writes.append((address, bool(value)))
        if address == 910 and value:
            self.nonce += 1
            self.epoch = 24
            self.slot_states = [0, 0, 0]
            self.process = {a: 0 for a in range(214, 243)}
            self.process[221] = 1
            # The old event is rejected; no new induction or trailer outcome.
            self.events.clear()
            self.acked_event = 183
            self.fault = 1
            self.ready = 0
        else:
            self.coils[address] = bool(value)
        self.scan()
        return Reply([])


def helper():
    pymodbus = ModuleType('pymodbus')
    clients = ModuleType('pymodbus.client')
    clients.ModbusTcpClient = object
    xle = ModuleType('xle')
    xle.OutcomeJournal = object
    xle.ensure_epoch = lambda *_: 24
    spec = importlib.util.spec_from_file_location(
        'xle_vm_recovery_under_test', Path(__file__).with_name('recover_accumulation_state.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pymodbus': pymodbus,
                                  'pymodbus.client': clients, 'xle': xle}):
        spec.loader.exec_module(module)
    return module


class RecoveryOrderTests(unittest.TestCase):
    def test_undecided_reset_recommit_and_safe_mode_teardown(self):
        plc = PlantRecoveryPLC()
        result = helper().recover(plc, 'unused', remote_xle=True, reset_run=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(plc.plant_epoch, (24, 0, 39))
        self.assertEqual(plc.fault, 0)
        self.assertFalse(plc.coils[880])
        self.assertEqual(plc.slot_states, [0, 0, 0])
        self.assertEqual(plc.process[220], 0)
        self.assertEqual(plc.process[222], 0)
        self.assertEqual(plc.acked_event, 183)
        self.assertEqual(plc.events, [])
        self.assertEqual(plc.ready, 0)
        self.assertNotIn((920, True), plc.writes)
        self.assertNotIn((921, True), plc.writes)
        teardown = plc.writes[-8:]
        self.assertEqual(teardown, [(918, False), (919, False),
                         (920, False), (914, False), (915, False),
                         (916, False), (917, False), (880, False)])

    def test_missing_fresh_plant_recommit_fails_closed(self):
        plc = PlantRecoveryPLC(recommit=False)
        with self.assertRaises(TimeoutError) as raised:
            helper().recover(plc, 'unused', remote_xle=True, reset_run=True,
                             timeout=.01)
        self.assertIn('"expected_identity": [24, 0, 39]', str(raised.exception))
        self.assertIn('"observed_identity": [23, 0, 38]', str(raised.exception))
        self.assertFalse(plc.coils[880])
        self.assertNotEqual(plc.fault, 0)

    def test_fault_runner_cleanup_disables_plant_before_external_mode(self):
        class RunnerPLC:
            def __init__(self): self.writes = []
            def connect(self): return True
            def close(self): pass
            def read_coils(self, address, count, slave):
                return Reply([False] * count)
            def read_holding_registers(self, address, count, slave):
                return Reply([0 if address != 249 else 99999] * count)
            def write_coil(self, address, value, slave):
                self.writes.append((address, value)); return Reply([])
            def write_register(self, address, value, slave): return Reply([])

        client = RunnerPLC()
        pymodbus = ModuleType('pymodbus')
        clients = ModuleType('pymodbus.client')
        clients.ModbusTcpClient = lambda *_args, **_kw: client
        spec = importlib.util.spec_from_file_location(
            'xle_vm_liveness_under_test', Path(__file__).with_name('live_xle_vm_liveness.py'))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'pymodbus': pymodbus,
                                      'pymodbus.client': clients}):
            spec.loader.exec_module(module)
        with patch.object(module, 'service', return_value=type('Result', (), {'returncode': 0})()):
            with self.assertRaisesRegex(RuntimeError, 'PLC identity'):
                module.run('before_first')
        self.assertEqual([address for address, _ in client.writes[:6]],
                         [880, 918, 919, 920, 914, 915])


if __name__ == '__main__':
    unittest.main()

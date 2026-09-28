"""Seeded scanner sequences and the register range the tunnel may write."""
import contextlib
import functools
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import types
import unittest
from unittest.mock import patch


SCANNER_PATH = Path(__file__).resolve().parents[1] / "devices/scanner.py"

# The host test needs no TCP server or installed pymodbus package.
for name in ("pymodbus", "pymodbus.server", "pymodbus.datastore"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["pymodbus.server"].StartTcpServer = object()
for name in ("ModbusSequentialDataBlock", "ModbusSlaveContext", "ModbusServerContext"):
    setattr(sys.modules["pymodbus.datastore"], name, object)
spec = importlib.util.spec_from_file_location("sorter_scanner_sequence", SCANNER_PATH)
scanner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner_module)

SEEDS = (137, 47, 29999)
LANES = (1, 2, 3)
RATES = (0, 22, 200, 900)
SCANS = 1500
VALID_BARCODES = range(1000, 10000)


class Registers:
    def __init__(self):
        self.values = [0] * 16
        self.writes = []

    def getValues(self, function, address, count):
        return self.values[address:address + count]

    def setValues(self, function, address, values):
        self.writes.append(list(values))
        self.values[address:address + len(values)] = values


@functools.lru_cache(maxsize=None)
def run(seed, lane, rate, scans=SCANS):
    """Reset one tunnel and return per-scan results with its stream states.

    Cached so the tests share runs; callers must not mutate the result."""
    reg = Registers()
    with patch.dict(os.environ):
        os.environ.pop("SORTER_REPEAT_BARCODE", None)
        device = scanner_module.Scanner({0: reg}, "test", lane)
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):
        reg.values[:4] = [scanner_module.PREPARE_SEQ, 1, seed, rate]
        device.step()
        reg.values[0] = scanner_module.RESET_SEQ
        device.step()
        for seq in range(1, scans + 1):
            reg.values[:2] = [(seq - 1) % 30000 + 1, seq % 30000]
            device.step()
            rows.append((tuple(reg.values[4:11]),
                         device.s_fail.v, device.s_dest.v, device.s_dims.v))
    return rows, reg


class ScannerSequenceTest(unittest.TestCase):
    def test_failure_rate_does_not_shift_destinations_or_dimensions(self):
        seen = set()
        for seed in SEEDS:
            for lane in LANES:
                runs = {rate: run(seed, lane, rate)[0] for rate in RATES}
                base = runs[RATES[0]]
                for rate in RATES[1:]:
                    compared = 0
                    for index, (a, b) in enumerate(zip(base, runs[rate])):
                        (_, bc_a, st_a, len_a, wid_a, hgt_a, _) = a[0]
                        (_, bc_b, st_b, len_b, wid_b, hgt_b, _) = b[0]
                        seen.update((st_a, st_b))
                        where = (seed, lane, rate, index + 1)
                        # The draw invariant: each scan advances these
                        # streams by the same count whatever the outcome.
                        self.assertEqual(a[1:], b[1:], where)
                        if st_a != st_b:
                            continue
                        compared += 1
                        if st_a in (scanner_module.ST_GOOD, scanner_module.ST_OVERSIZE):
                            self.assertEqual(bc_a // 1000, bc_b // 1000, where)
                            self.assertEqual(bc_a, bc_b, where)
                        self.assertEqual((wid_a, hgt_a), (wid_b, hgt_b), where)
                        if st_a != scanner_module.ST_OVERSIZE:
                            self.assertEqual(len_a, len_b, where)
                    self.assertGreater(compared, 0, (seed, lane, rate))
        # The comparison must have crossed the outcomes that used to drift.
        self.assertIn(scanner_module.ST_INVALID, seen)
        self.assertIn(scanner_module.ST_OVERSIZE, seen)

    def test_written_registers_stay_in_signed_int_range(self):
        statuses = set()
        for seed in SEEDS:
            for lane in LANES:
                for rate in (200, 900, 990, 999):
                    rows, reg = run(seed, lane, rate)
                    for values in reg.writes:
                        for value in values:
                            self.assertIsInstance(value, int)
                            self.assertTrue(0 <= value <= 32767,
                                            (seed, lane, rate, values))
                    statuses.update(row[0][2] for row in rows)
        self.assertTrue({scanner_module.ST_INVALID, scanner_module.ST_OVERSIZE,
                         scanner_module.ST_NOREAD}.issubset(statuses), statuses)

    def test_invalid_barcodes_never_look_like_valid_labels(self):
        invalid = []
        for seed in SEEDS:
            for lane in LANES:
                for rate in (0, 900, 990):
                    rows, _ = run(seed, lane, rate)
                    invalid.extend(row[0][1] for row in rows
                                   if row[0][2] == scanner_module.ST_INVALID)
        self.assertGreater(len(invalid), 20)
        for barcode in invalid:
            self.assertNotIn(barcode, VALID_BARCODES)
            self.assertTrue(10000 <= barcode <= 10999, barcode)

    def test_result_block_encodes_with_pymodbus_3_6_9(self):
        # Host Python deliberately has no pymodbus, so the real library runs
        # in a child interpreter. SORTER_PYMODBUS_PYTHON names one that has
        # pymodbus 3.6.9, the version in devices/requirements.txt.
        python = os.environ.get("SORTER_PYMODBUS_PYTHON", sys.executable)
        probe = subprocess.run(
            [python, "-c", "import pymodbus; print(pymodbus.__version__)"],
            capture_output=True, text=True, timeout=60)
        if probe.returncode != 0 or probe.stdout.strip() != "3.6.9":
            self.skipTest("pymodbus 3.6.9 unavailable; set SORTER_PYMODBUS_PYTHON")
        # Allocation tracing only serves this process's ResourceWarning
        # tracebacks and slows the child several fold, so it is not passed on.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONTRACEMALLOC"}
        child = subprocess.run([python, "-c", ENCODE_CHECK, str(SCANNER_PATH)],
                               capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("encoded", child.stdout)


# Builds the same datastore the service does, drives the real scanner, and
# encodes each result block (registers 4..10) and the whole 0..10 read.
ENCODE_CHECK = textwrap.dedent("""
    import contextlib, importlib.util, io, os, sys
    from pymodbus.datastore import (ModbusSequentialDataBlock,
                                    ModbusSlaveContext, ModbusServerContext)
    from pymodbus.register_read_message import ReadHoldingRegistersResponse
    os.environ.pop("SORTER_REPEAT_BARCODE", None)
    spec = importlib.util.spec_from_file_location("scanner", sys.argv[1])
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    statuses, blocks = set(), 0
    for lane in (1, 2, 3):
        for rate in (0, 900, 990):
            block = ModbusSequentialDataBlock(0, [0] * 16)
            ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
            device = scanner.Scanner(ctx, "encode", lane)
            with contextlib.redirect_stdout(io.StringIO()):
                ctx[0].setValues(3, 0, [scanner.PREPARE_SEQ, 1, 137, rate])
                device.step()
                ctx[0].setValues(3, 0, [scanner.RESET_SEQ])
                device.step()
                for seq in range(1, 1501):
                    ctx[0].setValues(3, 0, [seq, seq])
                    device.step()
                    result = ctx[0].getValues(3, 4, count=7)
                    statuses.add(result[2])
                    for values in (result, ctx[0].getValues(3, 0, count=11)):
                        response = ReadHoldingRegistersResponse(values)
                        decoded = ReadHoldingRegistersResponse()
                        decoded.decode(response.encode())
                        assert decoded.registers == values, values
                        blocks += 1
    missing = {scanner.ST_INVALID, scanner.ST_OVERSIZE} - statuses
    assert not missing, missing
    print("encoded", blocks)
""")


if __name__ == "__main__":
    unittest.main()

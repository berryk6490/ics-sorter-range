"""Bounded operator reset and normal plant/XLe handshake after a test run.

Run on SCADA only after the canonical plant service is restored. This writes
documented operator coils, never internal slot, fault, or outcome registers.
"""
import argparse
import json
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from xle import OutcomeJournal, ensure_epoch


def holding(client, address, count=1):
    reply = client.read_holding_registers(address, count, slave=1)
    if reply.isError() or len(reply.registers) != count:
        raise RuntimeError(f"PLC read failed at {address}")
    return list(reply.registers)


def coil(client, address, value):
    reply = client.write_coil(address, value, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC operator coil {address} write failed")


def recover(client, journal_path, *, timeout=30, reset_run=False):
    if not client.connect():
        raise ConnectionError("PLC Modbus unavailable for operator recovery")
    record = {"reset_requested": False, "epoch": None, "faults_before": None,
              "faults_after": None, "master_off": False, "slots_empty": False}
    try:
        if holding(client, 249)[0] != 24113:
            raise ValueError("PLC program identity changed")
        if client.read_coils(880, 1, slave=1).bits[0]:
            raise RuntimeError("master must already be stopped")
        record["faults_before"] = {"plant": holding(client, 591)[0],
                                   "epoch": holding(client, 561)[0],
                                   "zone": holding(client, 788)[0]}
        slots = [holding(client, address + 4)[0] for address in (530, 542, 647)]
        # Completed runs retain counters and the published ready mask until
        # operator reset, despite having no occupied slots or active faults.
        if (reset_run or any(slots) or any(record["faults_before"].values()) or
            any(holding(client, 214, 29)) or holding(client, 786)[0]):
            old_nonce = holding(client, 509)[0]
            coil(client, 910, True)
            record["reset_requested"] = True
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if (holding(client, 509)[0] != old_nonce and
                    holding(client, 255)[0] == 0 and holding(client, 256)[0] == 0):
                    break
                time.sleep(.1)
            else:
                raise TimeoutError("scanner reset ACK timeout")
        for address in (914, 915, 918, 919):
            coil(client, address, True)
        with_journal = OutcomeJournal(str(journal_path))
        try:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                record["epoch"] = ensure_epoch(client, with_journal)
                if record["epoch"] and holding(client, 591)[0] == 0 and holding(client, 561)[0] == 0:
                    break
                time.sleep(.1)
            else:
                raise TimeoutError("plant and journal-backed epoch handshake timeout")
        finally:
            with_journal.close()
    finally:
        for address in (914, 915, 916, 917, 918, 919, 920):
            coil(client, address, False)
        coil(client, 880, False)
        record["faults_after"] = {"plant": holding(client, 591)[0],
                                  "epoch": holding(client, 561)[0],
                                  "zone": holding(client, 788)[0]}
        record["master_off"] = not client.read_coils(880, 1, slave=1).bits[0]
        record["slots_empty"] = not any(holding(client, address + 4)[0]
                                        for address in (530, 542, 647))
        record["counters_zero"] = not any(holding(client, 214, 29))
        record["zone_ready_zero"] = holding(client, 786)[0] == 0
        client.close()
    if (any(record["faults_after"].values()) or not record["slots_empty"] or
        not record["counters_zero"] or not record["zone_ready_zero"]):
        raise RuntimeError(f"operator recovery incomplete: {record}")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path,
                        default=Path.home() / "sorter-services/xle-outcomes.sqlite3")
    parser.add_argument("--reset-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(ModbusTcpClient("10.10.1.10", port=502, timeout=2),
                             args.journal, reset_run=args.reset_run), sort_keys=True), flush=True)

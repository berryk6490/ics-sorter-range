"""Bounded operator reset and normal plant/XLe handshake after a test run.

Run on SCADA only after the canonical plant service is restored. This writes
documented operator coils, never internal slot, fault, or outcome registers.
"""
import argparse
from datetime import datetime, timezone
import json
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient
try:
    from xle import OutcomeJournal, ensure_epoch
except ModuleNotFoundError:
    # Dedicated-VM deployment has no XLe source on SCADA. Only the explicit
    # legacy local-journal path uses these names.
    OutcomeJournal = ensure_epoch = None
from accumulation_register_contract import (RESET_ZERO_PROCESS_COUNTERS,
                                            SERIAL_NEXT_ADDRESS, PROCESS_FIRST_ADDRESS,
                                            PROCESS_WORD_COUNT)


class RecoveryIncomplete(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(f"operator recovery incomplete: {json.dumps(result, sort_keys=True)}")


def holding(client, address, count=1):
    reply = client.read_holding_registers(address, count, slave=1)
    if reply.isError() or len(reply.registers) != count:
        raise RuntimeError(f"PLC read failed at {address}")
    return list(reply.registers)


def coil(client, address, value):
    reply = client.write_coil(address, value, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC operator coil {address} write failed")


def counter_sample(client):
    words = holding(client, PROCESS_FIRST_ADDRESS, PROCESS_WORD_COUNT)
    stamp = datetime.now(timezone.utc).isoformat()
    failures = [{"address": address, "name": name,
                 "observed": words[address - PROCESS_FIRST_ADDRESS],
                 "expected": 0, "sample_time_utc": stamp}
                for address, name in RESET_ZERO_PROCESS_COUNTERS.items()
                if words[address - PROCESS_FIRST_ADDRESS] != 0]
    return {"sample_time_utc": stamp, "sample_monotonic_ns": time.monotonic_ns(),
            "required_counter_failures": failures,
            "serial_next": {"address": SERIAL_NEXT_ADDRESS, "name": "serial_next",
                            "observed": words[SERIAL_NEXT_ADDRESS - PROCESS_FIRST_ADDRESS],
                            "restoration_class": "INFORMATIONAL"}}


def recover(client, journal_path, *, timeout=30, reset_run=False, remote_xle=False):
    if not client.connect():
        raise ConnectionError("PLC Modbus unavailable for operator recovery")
    record = {"reset_requested": False, "epoch": None, "faults_before": None,
              "faults_after": None, "master_off": False, "slots_empty": False,
              "counter_sample": None, "plant_handshake": None,
              "status": "PENDING"}
    try:
        if holding(client, 249)[0] != 24115:
            raise ValueError("PLC program identity changed")
        if client.read_coils(880, 1, slave=1).bits[0]:
            raise RuntimeError("master must already be stopped")
        record["faults_before"] = {"plant": holding(client, 591)[0],
                                   "epoch": holding(client, 561)[0],
                                   "zone": holding(client, 788)[0]}
        previous_plant_identity = holding(client, 587, 3)
        previous_plant_heartbeat = holding(client, 590)[0]
        slots = [holding(client, address + 4)[0] for address in (530, 542, 647)]
        # Completed runs retain counters and the published ready mask until
        # operator reset, despite having no occupied slots or active faults.
        if (reset_run or any(slots) or any(record["faults_before"].values()) or
            counter_sample(client)["required_counter_failures"] or holding(client, 786)[0]):
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
        # Recovery is a stopped-run identity handshake. Accumulation and
        # chute modes stay off after reset; re-enabling accumulation would
        # republish a nonzero ready mask that remains latched when modes drop.
        for address in (914, 915, 918, 919):
            coil(client, address, True)
        if remote_xle:
            # The dedicated XLe service owns the only writable journal and
            # establishes the PLC epoch. SCADA observes the PLC acknowledgement.
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                lo, hi = holding(client, 558, 2)
                record["epoch"] = hi * 30000 + lo
                nonce = holding(client, 509)[0]
                expected_plant_identity = [lo, hi, nonce]
                plant_identity = holding(client, 587, 3)
                plant_heartbeat = holding(client, 590)[0]
                plant_age = holding(client, 593)[0]
                plant_fault = holding(client, 591)[0]
                record["plant_handshake"] = {
                    "expected_identity": expected_plant_identity,
                    "observed_identity": plant_identity,
                    "previous_identity": previous_plant_identity,
                    "heartbeat": plant_heartbeat,
                    "previous_heartbeat": previous_plant_heartbeat,
                    "heartbeat_age": plant_age, "fault": plant_fault,
                    "event_sequence": holding(client, 585)[0],
                    "event_ack": holding(client, 586)[0],
                }
                if (record["epoch"] and
                    plant_identity == expected_plant_identity and
                    plant_identity != previous_plant_identity and
                    plant_heartbeat != previous_plant_heartbeat and
                    plant_age <= 30 and plant_fault == 0 and
                    holding(client, 561)[0] == 0 and holding(client, 569)[0] == 0 and
                    holding(client, 573)[0] > 0 and
                    holding(client, 573)[0] == holding(client, 567)[0]):
                    break
                time.sleep(.1)
            else:
                raise TimeoutError("dedicated XLe/plant fresh identity handshake timeout: " +
                                   json.dumps(record["plant_handshake"], sort_keys=True))
            record["journal_authority"] = "dedicated_sorter_xle_service"
        else:
            if OutcomeJournal is None or ensure_epoch is None:
                raise RuntimeError("legacy local XLe journal helper unavailable")
            with_journal = OutcomeJournal(str(journal_path))
            try:
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    record["epoch"] = ensure_epoch(client, with_journal)
                    if (record["epoch"] and holding(client, 591)[0] == 0 and
                        holding(client, 561)[0] == 0):
                        break
                    time.sleep(.1)
                else:
                    raise TimeoutError("plant and journal-backed epoch handshake timeout")
            finally:
                with_journal.close()
    finally:
        # The PLC evaluates plant identity while plant mode is on. Drop that
        # mode before external/XLe modes, or one scan creates fault 2 and the
        # idle PLC retains it after plant mode turns off.
        for address in (921, 918, 919, 920, 914, 915, 916, 917):
            coil(client, address, False)
        coil(client, 880, False)
        record["faults_after"] = {"plant": holding(client, 591)[0],
                                  "epoch": holding(client, 561)[0],
                                  "zone": holding(client, 788)[0]}
        record["master_off"] = not client.read_coils(880, 1, slave=1).bits[0]
        record["slots_empty"] = not any(holding(client, address + 4)[0]
                                        for address in (530, 542, 647))
        record["counter_sample"] = counter_sample(client)
        record["counters_zero"] = not record["counter_sample"]["required_counter_failures"]
        record["zone_ready_zero"] = holding(client, 786)[0] == 0
        client.close()
    if (any(record["faults_after"].values()) or not record["slots_empty"] or
        not record["counters_zero"] or not record["zone_ready_zero"]):
        record["status"] = "FAIL"
        raise RecoveryIncomplete(record)
    record["status"] = "PASS"
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path,
                        default=Path.home() / "sorter-services/xle-outcomes.sqlite3")
    parser.add_argument("--reset-run", action="store_true")
    parser.add_argument("--remote-xle", action="store_true",
                        help="wait for the dedicated XLe journal and PLC handshake")
    args = parser.parse_args()
    try:
        result = recover(ModbusTcpClient("10.10.1.10", port=502, timeout=2),
                         args.journal, reset_run=args.reset_run,
                         remote_xle=args.remote_xle)
    except RecoveryIncomplete as exc:
        print(json.dumps(exc.result, sort_keys=True), flush=True)
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True), flush=True)

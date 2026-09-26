"""One bounded, serial-console driven trailer-2 chute proof on the XLe guest.

The dedicated XLe and ASX services remain the only route and plan authorities.
This runner uses PLC Modbus only. It stops master on every exit and leaves the
run intact for host evidence collection and the canonical recovery helper.
"""
import argparse
import json
from pathlib import Path
import time

from pymodbus.client import ModbusTcpClient

PLC = "10.10.1.10"
SLOTS = (530, 542, 647)
ACTIVE = (1, 2, 3, 4)
EXPECTED = ((1, 6001), (2, 5002), (3, 2003), (4, 7004))


def regs(client, address, count=1):
    result = client.read_holding_registers(address, count, slave=1)
    if result.isError() or len(result.registers) != count:
        raise RuntimeError(f"read holding {address}/{count}: {result}")
    return list(result.registers)


def coils(client, address, count=1):
    result = client.read_coils(address, count, slave=1)
    if result.isError():
        raise RuntimeError(f"read coil {address}/{count}: {result}")
    return list(result.bits[:count])


def coil(client, address, value):
    result = client.write_coil(address, value, slave=1)
    if result.isError():
        raise RuntimeError(f"write coil {address}: {result}")


def reg(client, address, value):
    result = client.write_register(address, value, slave=1)
    if result.isError():
        raise RuntimeError(f"write holding {address}: {result}")


def enable_plant_modes(client):
    """FC15 makes the plant see all opt-in modes in the same polling sample."""
    result = client.write_coils(918, [True, True, True, True], slave=1)
    if result.isError():
        raise RuntimeError(f"enable plant/stateful/accumulation/chute: {result}")


def wait(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError(label)


def sample(client):
    rows = [regs(client, base, 12) for base in SLOTS]
    zone = [regs(client, base, 5) for base in (751, 756, 761)]
    return {"wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
            "epoch": regs(client, 558, 2), "nonce": regs(client, 509)[0],
            "master": coils(client, 880)[0], "inducted": regs(client, 220)[0],
            "trailer": regs(client, 222, 9), "slots": rows, "zones": zone,
            "lanes": regs(client, 644, 2) + regs(client, 659),
            "plant_event": regs(client, 580, 7),
            "event_ack": regs(client, 586)[0],
            "raw_photoeyes": [regs(client, base, 8) for base in (690, 698, 706)],
            "validated_photoeyes": [regs(client, base, 8) for base in (714, 722, 730)],
            "chute_raw": regs(client, 790, 13) + regs(client, 830, 3),
            "chute_validated": regs(client, 803, 22),
            "chute_accepted": regs(client, 827, 3),
            "plant_fault": regs(client, 591)[0],
            "zone_fault": regs(client, 788)[0],
            "photoeye_faults": regs(client, 748, 3),
            "xle_health": regs(client, 569, 5)}


def assert_safe(state):
    if (state["plant_fault"] or state["zone_fault"] or
            any(state["photoeye_faults"]) or state["xle_health"][0]):
        raise AssertionError(f"PLC fault during chute run: {state}")
    if sum(state["trailer"]) != state["trailer"][1]:
        raise AssertionError(f"wrong trailer loaded: {state['trailer']}")
    if state["inducted"] > 4 or state["trailer"][1] > 4:
        raise AssertionError("more than four packages admitted/confirmed")
    if any(row[5] not in (0, 2) or row[6] not in (0, 2)
           for row in state["slots"]):
        raise AssertionError("wrong route or trailer in PLC slot")


def run(path, duration=210):
    client = ModbusTcpClient(PLC, port=502, timeout=2)
    if not client.connect():
        raise ConnectionError("PLC unavailable")
    started = False
    original = None
    result = {"functional": "FAIL", "cleanup": "NOT_RUN", "error": None}
    rows = {}
    observed_hold = False
    try:
        if regs(client, 249)[0] != 24115 or coils(client, 880)[0]:
            raise RuntimeError("wrong PLC program or master on")
        if any(regs(client, base + 4)[0] for base in SLOTS):
            raise RuntimeError("occupied slot before run")
        if any(coils(client, a)[0] for a in (914, 915, 918, 919, 920, 921)):
            raise RuntimeError("temporary modes already enabled")
        if (regs(client, 591)[0] or regs(client, 788)[0] or
                any(regs(client, 748, 3)) or any(regs(client, 222, 9))):
            raise RuntimeError("fault or trailer count before run")
        original = {"enables": coils(client, 881, 7),
                    "setpoints": regs(client, 200, 11),
                    "seed": regs(client, 247)[0]}
        reg(client, 247, 137)
        nonce_before = regs(client, 509)[0]
        coil(client, 910, True)
        wait(lambda: regs(client, 509)[0] != nonce_before and
             regs(client, 255)[0] == 0 and regs(client, 256)[0] == 0,
             30, "scanner reset ACK")
        for lane in (1, 2, 3):
            coil(client, 881 + lane, lane == 1)
        for address in range(200, 206):
            reg(client, address, 120)
        for address in (207, 208, 209):
            reg(client, address, 14 if address == 207 else 32000)
        coil(client, 914, True)
        coil(client, 915, True)
        wait(lambda: regs(client, 558, 2) != [0, 0] and
             regs(client, 573)[0] > 0 and regs(client, 569)[0] == 0,
             25, "XLe epoch and heartbeat")
        enable_plant_modes(client)
        wait(lambda: regs(client, 591)[0] == 0 and
             regs(client, 587, 3) == regs(client, 558, 2) + regs(client, 509) and
             regs(client, 807)[0] == 1 and regs(client, 786)[0] != 0,
             25, "fresh plant/chute identity")
        epoch_words = regs(client, 558, 2)
        result["epoch"] = epoch_words[0] + epoch_words[1] * 30000
        result["nonce"] = regs(client, 509)[0]
        coil(client, 880, True)
        started = True
        end = time.monotonic() + duration
        with path.open("w", buffering=1) as evidence:
            while time.monotonic() < end:
                state = sample(client)
                assert_safe(state)
                evidence.write(json.dumps(state, sort_keys=True) + "\n")
                if state["inducted"] >= 4:
                    reg(client, 207, 32000)
                epoch = state["epoch"][0] + 30000 * state["epoch"][1]
                for slot, row in enumerate(state["slots"]):
                    if not row[0]:
                        continue
                    key = f"l{state['lanes'][slot]}-{epoch}-{state['nonce']}-{row[0]}-{row[1]}"
                    if row[2] and row[3]:
                        rows[key] = {"slot": slot, "token": row[0], "serial": row[1],
                                     "scanner_sequence": row[2], "barcode": row[3],
                                     "destination": row[5], "actual": row[6],
                                     "state": row[4], "command_id": row[11]}
                    if state["zones"][slot][2] == 5 and row[4] in ACTIVE:
                        observed_hold = True
                if state["trailer"][1] == 4 and len(rows) == 4:
                    break
                time.sleep(.07)
            else:
                raise TimeoutError("fourth accepted trailer confirmation")
        by_serial = {r["serial"]: r for r in rows.values()}
        if [(s, by_serial[s]["barcode"]) for s, _ in EXPECTED] != list(EXPECTED):
            raise AssertionError(f"scanner identity drift: {rows}")
        if (not observed_hold or any(r["destination"] != 2 or r["actual"] != 2
                                     for r in rows.values())):
            raise AssertionError(f"hold/route mismatch: {rows}")
        result.update(functional="PASS", rows=rows)
    except BaseException as exc:
        result["error"] = repr(exc)
    finally:
        cleanup_errors = []
        if original:
            try:
                coil(client, 880, False)
            except BaseException as exc:
                cleanup_errors.append(f"master: {exc!r}")
        if original:
            for address, value in enumerate(original["setpoints"], 200):
                try:
                    reg(client, address, value)
                except BaseException as exc:
                    cleanup_errors.append(f"setpoint {address}: {exc!r}")
            for address, value in enumerate(original["enables"], 881):
                try:
                    coil(client, address, value)
                except BaseException as exc:
                    cleanup_errors.append(f"enable {address}: {exc!r}")
            try:
                reg(client, 247, original["seed"])
            except BaseException as exc:
                cleanup_errors.append(f"seed: {exc!r}")
        if original:
            try:
                if coils(client, 880)[0]:
                    cleanup_errors.append("master remains on")
            except BaseException as exc:
                cleanup_errors.append(f"master verification: {exc!r}")
        result["cleanup"] = {"status": "PASS" if not cleanup_errors else "FAIL",
                             "errors": cleanup_errors, "temporary_modes_retained": True}
        result["started"] = started
        result["observed_hold"] = observed_hold
        result["rows"] = rows
        client.close()
        print(json.dumps(result, sort_keys=True), flush=True)
    if result["functional"] != "PASS" or result["cleanup"]["status"] != "PASS":
        raise RuntimeError(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=210)
    args = parser.parse_args()
    run(args.evidence, args.duration)

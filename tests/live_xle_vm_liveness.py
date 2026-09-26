"""Root-run, bounded XLe service loss and operator recovery probe on xle VM.

This test controls only sorter-xle.service through exact systemctl argv. ASX,
the PLC program, plant service, and journal location are never replaced.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from pymodbus.client import ModbusTcpClient

UNIT = "sorter-xle.service"
JOURNAL = Path("/var/lib/sorter-xle/outcomes.sqlite3")
RESTORE_MODE_ORDER = (918, 919, 920, 914, 915)


def reg(client, address, count=1):
    reply = client.read_holding_registers(address, count, slave=1)
    if reply.isError() or len(reply.registers) != count:
        raise RuntimeError(f"PLC register read {address} failed")
    return list(reply.registers)


def coil(client, address, count=1):
    reply = client.read_coils(address, count, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC coil read {address} failed")
    return [bool(value) for value in reply.bits[:count]]


def write_coil(client, address, value):
    if client.write_coil(address, value, slave=1).isError():
        raise RuntimeError(f"PLC coil write {address} failed")


def write_reg(client, address, value):
    if client.write_register(address, value, slave=1).isError():
        raise RuntimeError(f"PLC register write {address} failed")


def service(action):
    if action not in ("start", "stop", "is-active"):
        raise ValueError(action)
    return subprocess.run(["/usr/bin/systemctl", action, UNIT],
                          capture_output=True, text=True, timeout=15)


def wait(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(.03)
    raise TimeoutError(label)


def rows(client):
    words = reg(client, 530, 24) + reg(client, 647, 12)
    return [words[i:i + 12] for i in (0, 12, 24)]


def journal_rows(epoch, nonce):
    with sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("XLe journal integrity failed")
        return [(key, json.loads(payload)) for key, payload in db.execute(
            "SELECT identity,payload FROM outcomes WHERE identity LIKE ? ORDER BY identity",
            (f"{epoch}:{nonce}:%",))]


def run(case, hold_seconds=8):
    if case not in ("before_first", "occupied", "undecided"):
        raise ValueError(case)
    if service("is-active").returncode:
        raise RuntimeError("canonical XLe service must be active")
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise ConnectionError("PLC Modbus unavailable")
    original = {"enables": coil(client, 881, 7),
                "modes": {a: coil(client, a)[0] for a in (914, 915, 918, 919, 920)},
                "setpoints": reg(client, 200, 11), "seed": reg(client, 247)[0]}
    stopped_service = False
    failure = None
    try:
        if reg(client, 249)[0] != 24114 or coil(client, 880)[0]:
            raise RuntimeError("PLC identity or stopped preflight failed")
        if any(original["modes"].values()):
            raise RuntimeError("test requires all external and plant modes initially off")
        if any(row[4] for row in rows(client)) or reg(client, 591)[0] or reg(client, 788)[0]:
            raise RuntimeError("occupied slot or fault before test")
        write_reg(client, 247, 137)
        old_nonce = reg(client, 509)[0]
        write_coil(client, 910, True)
        wait(lambda: reg(client, 509)[0] != old_nonce and reg(client, 255)[0] == 0,
             25, "scanner reset")
        for lane in (1, 2, 3):
            write_coil(client, 881 + lane, lane in (1, 2) if case == "occupied"
                       else lane == 1 if case == "undecided" else False)
        for address in (914, 915, 918, 919, 920):
            write_coil(client, address, True)
        for address in range(200, 206):
            write_reg(client, address, 120)
        for address in (207, 208, 209):
            write_reg(client, address, 14 if case != "before_first" else 32000)
        wait(lambda: reg(client, 558, 2) != [0, 0] and reg(client, 573)[0] > 0 and
             reg(client, 569)[0] == 0, 20, "fresh run heartbeat")
        lo, hi = reg(client, 558, 2)
        epoch, nonce = hi * 30000 + lo, reg(client, 509)[0]
        write_coil(client, 880, True)
        if case == "before_first":
            time.sleep(1)
            if reg(client, 220)[0] or any(row[4] for row in rows(client)):
                raise RuntimeError("unexpected induction before XLe stop")
            before = rows(client)
        elif case == "undecided":
            before = wait(lambda: rows(client) if reg(client, 220)[0] == 1 and
                          rows(client)[0][4] == 1 else None,
                          30, "one inducted package before scanner decision")
            for address in (207, 208, 209):
                write_reg(client, address, 32000)
        else:
            def checkpoint():
                if reg(client, 220)[0] >= 2:
                    for address in (207, 208, 209):
                        write_reg(client, address, 32000)
                current = rows(client)
                if (current[0][4] in (3, 4) and current[1][4] in (3, 4) and
                    current[0][5] == 2 and current[1][5] == 5):
                    return current
                return None
            before = wait(checkpoint, 90, "two accepted occupied slots")
        if service("stop").returncode:
            raise RuntimeError("failed to stop sorter-xle.service")
        stopped_service = True
        if service("is-active").returncode == 0:
            raise RuntimeError("XLe service remains active after stop")
        wait(lambda: reg(client, 569)[0] == 1, 12, "heartbeat fault")
        at_fault = {"master": coil(client, 880)[0], "slots": rows(client),
                    "inducted": reg(client, 220)[0], "trailers": reg(client, 222, 9),
                    "liveness": reg(client, 569)[0], "heartbeat_age": reg(client, 568)[0]}
        if at_fault["inducted"] != (2 if case == "occupied" else
                                     1 if case == "undecided" else 0):
            raise RuntimeError("heartbeat loss changed the induction count")
        print(json.dumps({"stage": "fault_hold", "case": case, "epoch": epoch,
                          "nonce": nonce, "before": before, "at_fault": at_fault}), flush=True)
        time.sleep(hold_seconds)
        after_hold_inducted = reg(client, 220)[0]
        if after_hold_inducted != at_fault["inducted"]:
            raise RuntimeError("new package inducted during XLe heartbeat fault")
        write_coil(client, 916, True)
        wait(lambda: reg(client, 569)[0] == 2, 3, "fault acknowledgement")
        if case in ("occupied", "undecided"):
            count = 2 if case == "occupied" else 1
            wait(lambda: all(row[4] in (5, 6, 7) for row in rows(client)[:count]),
                 35, "in-motion packages settle without XLe")
        terminal_before_return = rows(client)
        if service("start").returncode:
            raise RuntimeError("failed to restart sorter-xle.service")
        stopped_service = False
        wait(lambda: reg(client, 568)[0] < 15 and reg(client, 573)[0] > 0,
             10, "XLe heartbeat return")
        if reg(client, 569)[0] != 2:
            raise RuntimeError("fault cleared before explicit retry")
        write_coil(client, 917, True)
        wait(lambda: reg(client, 569)[0] == 0, 8, "journal-backed retry proof")
        if case in ("occupied", "undecided"):
            wait(lambda: all(row[0] == 0 for row in rows(client)),
                 20, "terminal slot release")
        outcomes = journal_rows(epoch, nonce)
        if len(outcomes) != (2 if case == "occupied" else
                             1 if case == "undecided" else 0):
            raise RuntimeError(f"journal outcomes mismatch: {outcomes}")
        if case == "occupied":
            for row in terminal_before_return[:2]:
                matches = [payload for _, payload in outcomes
                           if payload["slot_token"] == row[0]]
                if len(matches) != 1 or matches[0]["command_id"] != row[11]:
                    raise RuntimeError("duplicate or changed route command")
            if reg(client, 222, 9)[1] != 1 or reg(client, 222, 9)[4] != 1:
                raise RuntimeError("accepted route trailer outcome mismatch")
        if case == "undecided":
            if (terminal_before_return[0][4] != 6 or
                terminal_before_return[0][11] != 0 or
                outcomes[0][1]["state"] != "recirculated" or
                any(reg(client, 222, 9))):
                raise RuntimeError("undecided package did not recirculate safely")
        result = {"status": "PASS", "case": case, "epoch": epoch,
                  "nonce": nonce, "before": before, "fault": at_fault,
                  "terminal_before_return": terminal_before_return,
                  "journal": outcomes, "final_liveness": reg(client, 569)[0],
                  "final_trailers": reg(client, 222, 9),
                  "final_inducted": reg(client, 220)[0]}
        print(json.dumps(result, sort_keys=True), flush=True)
        return result
    except BaseException as exc:
        failure = exc
        raise
    finally:
        errors = []
        try:
            write_coil(client, 880, False)
        except Exception as exc:
            errors.append(f"master: {exc}")
        if stopped_service or service("is-active").returncode:
            if service("start").returncode:
                errors.append("canonical XLe service restoration failed")
        # A live plant mode with either external mode off sets QW591=2.
        for address in RESTORE_MODE_ORDER:
            try:
                write_coil(client, address, original["modes"][address])
            except Exception as exc:
                errors.append(f"mode {address}: {exc}")
        for index, value in enumerate(original["enables"]):
            try:
                write_coil(client, 881 + index, value)
            except Exception as exc:
                errors.append(f"enable {881 + index}: {exc}")
        for index, value in enumerate(original["setpoints"]):
            try:
                write_reg(client, 200 + index, value)
            except Exception as exc:
                errors.append(f"setpoint {200 + index}: {exc}")
        try:
            write_reg(client, 247, original["seed"])
        except Exception as exc:
            errors.append(f"seed: {exc}")
        client.close()
        if errors:
            print(json.dumps({"cleanup_errors": errors}), flush=True)
            if failure is None:
                raise RuntimeError("cleanup failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("before_first", "occupied", "undecided"))
    parser.add_argument("--hold-seconds", type=float, default=8)
    args = parser.parse_args()
    run(args.case, args.hold_seconds)

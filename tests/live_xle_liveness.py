"""Live SCADA Modbus test: kill XLe, observe latched PLC fault, recover.

`occupied --hmi` waits for HMI Acknowledge and Retry clicks; run in the
background while the serial HMI proxy owns the SCADA console.
"""
import argparse
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from pymodbus.client import ModbusTcpClient
from live_xle_multi import ROOT, coils, events, holding, set_coil, set_register, stop


def wait_for(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError(label)


def rows(client):
    data = holding(client, 530, 24)
    return [data[:12], data[12:]]


def launch_xle(directory, journal, name, terminal_hold=1):
    path = Path(directory) / (name + ".jsonl")
    stream = path.open("w")
    process = subprocess.Popen([sys.executable, str(ROOT / "xle.py"), "--multi",
                                "--packages", "0", "--journal", str(journal),
                                "--terminal-hold", str(terminal_hold)],
                               stdout=stream, stderr=subprocess.STDOUT)
    stream.close()
    return process, path


def run(case, hmi=False):
    occupied_case = case != "before_first"
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert client.connect()
    original = {"enables": coils(client, 881, 7),
                "external": coils(client, 914, 2),
                "setpoints": holding(client, 200, 11),
                "seed": holding(client, 247)[0]}
    processes = []
    asx = None
    with tempfile.TemporaryDirectory(prefix="sorter-liveness-") as directory:
        journal = Path(directory) / "outcomes.sqlite3"
        try:
            set_coil(client, 880, False)
            set_register(client, 247, 137)
            old_tick = holding(client, 243)[0]
            set_coil(client, 910, True)
            seen = False
            def ready():
                nonlocal seen
                tick, state = holding(client, 243)[0], holding(client, 255)[0]
                seen |= tick < old_tick or state in (1, 2)
                return seen and state == 0
            wait_for(ready, 20, "scanner reset")
            asx_command = [sys.executable, str(ROOT / "asx.py")]
            if case == "undecided":
                asx_command += ["--plan", str(ROOT / "liveness_sort_plan.json")]
            asx = subprocess.Popen(asx_command,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            def asx_ready():
                try:
                    socket.create_connection(("127.0.0.1", 8089), .2).close()
                    return True
                except OSError:
                    return False
            wait_for(asx_ready, 5, "ASX listener")
            first, first_path = launch_xle(directory, journal, "before_stop",
                                          30 if occupied_case else 1)
            processes.append(first)
            set_coil(client, 883, False)
            set_coil(client, 884, False)
            set_coil(client, 914, True)
            set_coil(client, 915, True)
            set_register(client, 207, 1000 if not occupied_case else 14)
            wait_for(lambda: holding(client, 558)[0] and holding(client, 573)[0],
                     8, "run epoch and heartbeat")
            epoch = holding(client, 558)[0] + holding(client, 559)[0] * 30000
            nonce = holding(client, 509)[0]
            before = rows(client)
            if occupied_case:
                set_coil(client, 880, True)
                def two_occupied():
                    value = rows(client)
                    if holding(client, 220)[0] >= 2:
                        set_register(client, 207, 32000)
                    return value if all(r[0] for r in value) and value[0][4] == 5 and \
                        (value[1][4] == 2 if case == "undecided" else
                         value[1][4] in (1, 2, 3, 4)) else None
                before = wait_for(two_occupied, 70, "terminal slot and second package in motion")
            first.terminate()
            first.wait(timeout=5)
            first_events = events(first_path.read_text())
            wait_for(lambda: holding(client, 569)[0] == 1, 12, "XLe heartbeat fault")
            assert holding(client, 568)[0] > 50
            assert holding(client, 561)[0] == 0
            assert holding(client, 220)[0] == (2 if occupied_case else 0)
            print(json.dumps({"stage": "fault", "case": case, "epoch": epoch,
                              "before": before, "liveness": holding(client, 569)[0],
                              "inducted": holding(client, 220)[0]}), flush=True)
            if not hmi:
                set_coil(client, 916, True)
            wait_for(lambda: holding(client, 569)[0] == 2, 90 if hmi else 3,
                     "operator acknowledgement")
            terminal = {}
            if occupied_case:
                # With XLe absent the PLC retains both rows. Observe their
                # physical outcomes before a restarted XLe can release them.
                def settled_without_xle():
                    for row in rows(client):
                        if row[0] and row[4] in (5, 6, 7):
                            terminal[row[1]] = row
                    return len(terminal) == 2
                wait_for(settled_without_xle, 30, "both physical slot outcomes")
            second, second_path = launch_xle(directory, journal, "after_return", 8)
            processes.append(second)
            wait_for(lambda: holding(client, 573)[0] != 0 and holding(client, 568)[0] < 15,
                     8, "XLe heartbeat return")
            assert holding(client, 569)[0] == 2, "fault cleared before retry"
            print(json.dumps({"stage": "returned_acknowledged", "case": case,
                              "liveness": holding(client, 569)[0],
                              "heartbeat_age": holding(client, 568)[0]}), flush=True)
            if not hmi:
                set_coil(client, 917, True)
            wait_for(lambda: holding(client, 569)[0] == 0, 90 if hmi else 5,
                     "operator retry and XLe proof")
            if occupied_case:
                def finished():
                    for row in rows(client):
                        if row[0] and row[4] in (5, 6, 7):
                            terminal[row[1]] = row
                    with sqlite3.connect(journal) as db:
                        count = db.execute("SELECT count(*) FROM outcomes").fetchone()[0]
                    return count == 2 and all(r[0] == 0 for r in rows(client))
                wait_for(finished, 30, "two journaled and released outcomes")
                assert len(terminal) == 2, terminal
                assert terminal[1][4] == 5 and terminal[1][6] == 2
                second_routed = before[1][4] in (3, 4)
                assert terminal[2][4] == (5 if second_routed else 6), terminal[2]
                assert terminal[2][6] == (5 if second_routed else 0), terminal[2]
                assert terminal[2][7] == (0 if second_routed else 1), terminal[2]
                assert holding(client, 223)[0] == 1
                assert holding(client, 226)[0] == int(second_routed)
                assert holding(client, 218)[0] == int(not second_routed)
                assert terminal[1][11] == before[0][11], "accepted command changed"
            else:
                assert holding(client, 220)[0] == 0 and all(r[0] == 0 for r in rows(client))
            second_events = events(second_path.read_text())
            with sqlite3.connect(journal) as db:
                journal_rows = dict(db.execute("SELECT identity,payload FROM outcomes"))
            expected_count = 2 if occupied_case else 0
            assert len(journal_rows) == expected_count
            assert holding(client, 220)[0] == expected_count, "unexpected induction during recovery"
            if occupied_case:
                if case == "undecided":
                    assert not any(e["event"] == "safe_fallback" and
                                   e.get("package_id", "").endswith("-2-2")
                                   for e in first_events), "ASX timed out before XLe stopped"
                assert not any(e["event"] == "plc_command" for e in second_events)
                assert len([e for e in first_events + second_events
                            if e["event"] == "plc_outcome"]) == 2
                for serial, row in terminal.items():
                    key = f"{epoch}:{nonce}:{row[0]}:{serial}:{row[2]}"
                    assert json.loads(journal_rows[key])["actual_trailer"] == row[6]
            print(json.dumps({"stage": "complete", "case": case,
                              "epoch": epoch, "nonce": nonce,
                              "liveness": holding(client, 569)[0],
                              "before": before, "terminal": terminal,
                              "journal_rows": journal_rows,
                              "trailers": holding(client, 222, 9),
                              "recirculated": holding(client, 218)[0],
                              "inducted": holding(client, 220)[0],
                              "before_events": first_events,
                              "after_events": second_events}, sort_keys=True), flush=True)
        finally:
            errors = []
            for process in processes:
                stop(process)
            stop(asx)
            try:
                set_coil(client, 880, False)
                assert not coils(client, 880)[0]
                old_tick = holding(client, 243)[0]
                set_coil(client, 910, True)
                reset_seen = False
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    tick, state = holding(client, 243)[0], holding(client, 255)[0]
                    reset_seen |= tick < old_tick or state in (1, 2)
                    if reset_seen and state == 0:
                        break
                    time.sleep(.1)
                assert reset_seen and holding(client, 255)[0] == 0
            except Exception as exc:
                errors.append(f"stopped reset: {exc}")
            actions = [(set_coil, 914, original["external"][0]),
                       (set_coil, 915, original["external"][1]),
                       (set_register, 247, original["seed"])]
            actions += [(set_coil, 881 + i, value) for i, value in enumerate(original["enables"])]
            actions += [(set_register, 200 + i, value) for i, value in enumerate(original["setpoints"])]
            for function, address, value in actions:
                try:
                    function(client, address, value)
                except Exception as exc:
                    errors.append(f"restore {address}: {exc}")
            try:
                set_coil(client, 880, False)
                time.sleep(.3)
                assert not coils(client, 880)[0]
                assert holding(client, 220)[0] == 0
                assert all(row[0] == 0 for row in rows(client))
            except Exception as exc:
                errors.append(f"final safe state: {exc}")
            client.close()
            if errors:
                raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("before_first", "occupied", "undecided"))
    parser.add_argument("--hmi", action="store_true")
    args = parser.parse_args()
    run(args.case, args.hmi)

"""Reproduce XLe process restarts with occupied and terminal PLC slots on SCADA."""
import argparse
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from live_xle_multi import (ROOT, coils, events, holding, set_coil,
                            set_register, stop)
from pymodbus.client import ModbusTcpClient


def slot(c):
    return holding(c, 530, 12)


def wait_for(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError(label)


def launch_xle(directory, journal, name, hold=1):
    path = Path(directory) / (name + ".jsonl")
    output = path.open("w")
    process = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", "1",
                                "--journal", journal, "--terminal-hold", str(hold)],
                               stdout=output, stderr=subprocess.STDOUT)
    output.close()
    return process, path


def finish(process, path, seconds=15):
    code = process.wait(timeout=seconds)
    output = path.read_text()
    assert code == 0, output
    return events(output)


def main(case):
    c = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert c.connect()
    original = {"enables": coils(c, 881, 7),
                "external": coils(c, 914, 2),
                "setpoints": holding(c, 200, 11), "seed": holding(c, 247)[0]}
    processes = []
    asx = None
    with tempfile.TemporaryDirectory(prefix="sorter-recovery-") as directory:
        journal = str(Path(directory) / "outcomes.sqlite3")
        try:
            set_coil(c, 880, False)
            set_register(c, 247, 137)
            old_tick = holding(c, 243)[0]
            set_coil(c, 910, True)
            seen = False
            def reset_ready():
                nonlocal seen
                tick, state = holding(c, 243)[0], holding(c, 255)[0]
                seen |= tick < old_tick or state in (1, 2)
                return seen and state == 0
            wait_for(reset_ready, 20, "scanner reset")
            set_coil(c, 883, False)
            set_coil(c, 884, False)
            set_coil(c, 914, True)
            set_coil(c, 915, True)
            set_register(c, 207, 14)
            if case == "occupied":
                asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py")],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.STDOUT)
                def asx_ready():
                    try:
                        socket.create_connection(("127.0.0.1", 8089), .2).close()
                        return True
                    except OSError:
                        return False
                wait_for(asx_ready, 5, "ASX listener")
            first, first_path = launch_xle(directory, journal, "before_crash")
            processes.append(first)
            set_coil(c, 880, True)
            def occupied():
                if holding(c, 220)[0] >= 1:
                    set_register(c, 207, 1000)
                row = slot(c)
                return row if row[4] in ((3, 4) if case == "occupied" else (2,)) else None
            before = wait_for(occupied, 60, "occupied slot before XLe crash")
            first.terminate()
            first.wait(timeout=5)
            if case == "occupied":
                second, second_path = launch_xle(directory, journal, "after_crash")
                processes.append(second)
                terminal = wait_for(lambda: slot(c) if slot(c)[4] == 5 else None,
                                    60, "loaded terminal slot")
                second_events = finish(second, second_path)
                assert terminal[6] == 2 and terminal[7] == 0
                assert holding(c, 223)[0] == 1 and holding(c, 218)[0] == 0
                assert any(e["event"] == "plc_recovered" for e in second_events)
                assert sum(e["event"] == "plc_outcome" for e in second_events) == 1
                assert not any(e["event"] == "plc_command" for e in second_events)
                assert slot(c)[4] == 0
                records = second_events
            else:
                terminal = wait_for(lambda: slot(c) if slot(c)[4] == 6 else None,
                                    60, "recirculated terminal slot")
                second, second_path = launch_xle(directory, journal, "terminal_before_crash", hold=3)
                processes.append(second)
                wait_for(lambda: any(e["event"] == "plc_outcome"
                                     for e in events(second_path.read_text())),
                         5, "first terminal outcome journal")
                second.terminate()
                second.wait(timeout=5)
                assert slot(c)[4] == 6, "terminal was released before restart"
                third, third_path = launch_xle(directory, journal, "terminal_after_crash", hold=0)
                processes.append(third)
                third_events = finish(third, third_path)
                second_events = events(second_path.read_text())
                assert sum(e["event"] == "plc_outcome" for e in second_events + third_events) == 1
                assert any(e["event"] == "plc_outcome_already_recorded" for e in third_events)
                assert not any(e["event"] == "plc_command" for e in second_events + third_events)
                assert slot(c)[4] == 0
                assert holding(c, 218)[0] == 1 and sum(holding(c, 222, 9)) == 0
                records = second_events + third_events
            with sqlite3.connect(journal) as db:
                assert db.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 1
            print(json.dumps({"case": case, "before_crash": before,
                              "terminal": terminal, "events": records,
                              "journal_outcomes": 1,
                              "inducted": holding(c, 220)[0]}, sort_keys=True), flush=True)
        finally:
            errors = []
            try:
                set_coil(c, 880, False)
                time.sleep(.3)
                assert not coils(c, 880)[0]
            except Exception as exc:
                errors.append(f"stop: {exc}")
            actions = [(set_coil, 914, original["external"][0]),
                       (set_coil, 915, original["external"][1]),
                       (set_register, 247, original["seed"])]
            actions += [(set_coil, 881 + i, value)
                        for i, value in enumerate(original["enables"])]
            actions += [(set_register, 200 + i, value)
                        for i, value in enumerate(original["setpoints"])]
            for fn, address, value in actions:
                try:
                    fn(c, address, value)
                except Exception as exc:
                    errors.append(f"{address}: {exc}")
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            stop(asx)
            c.close()
            if errors:
                raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("occupied", "terminal"))
    main(parser.parse_args().case)

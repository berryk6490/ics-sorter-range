"""Two-package SCADA-to-PLC Modbus verification. Restores settings in finally.

Run on SCADA with only the existing plc, drives, fw, and scada guests.
The repeat case requires the test-only tunnel 1 scanner process described in
MULTI_PACKAGE.md; normal scanner service is restored after that case.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

from pymodbus.client import ModbusTcpClient


ROOT = Path.home() / "sorter-services"


def holding(c, address, count=1):
    result = c.read_holding_registers(address, count, slave=1)
    if result.isError():
        raise RuntimeError(result)
    return result.registers


def coils(c, address, count=1):
    result = c.read_coils(address, count, slave=1)
    if result.isError():
        raise RuntimeError(result)
    return [bool(v) for v in result.bits[:count]]


def set_coil(c, address, value):
    result = c.write_coil(address, value, slave=1)
    if result.isError():
        raise RuntimeError(result)


def set_register(c, address, value):
    result = c.write_register(address, value, slave=1)
    if result.isError():
        raise RuntimeError(result)


def events(output):
    records = []
    for line in output.splitlines():
        try:
            record = json.loads(line)
            if "event" in record:
                records.append(record)
        except ValueError:
            pass
    return records


def stop(process):
    if process is None:
        return ""
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)[0]


def main(case):
    c = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert c.connect(), "SCADA cannot reach PLC through fw"
    original = {"enables": coils(c, 881, 7),
                "external": coils(c, 914, 2),
                "setpoints": holding(c, 200, 11), "seed": holding(c, 247)[0]}
    asx = xle = None
    rows = {}
    try:
        set_coil(c, 880, False)
        set_register(c, 247, 137)
        old_tick = holding(c, 243)[0]
        set_coil(c, 910, True)
        reset_seen = False
        until = time.monotonic() + 20
        while time.monotonic() < until:
            tick, state = holding(c, 243)[0], holding(c, 255)[0]
            reset_seen |= tick < old_tick or state in (1, 2)
            if reset_seen and state == 0:
                break
            time.sleep(.1)
        assert reset_seen and holding(c, 255)[0] == 0, "scanner reset incomplete"
        plan = ROOT / ("timeout_sort_plan.json" if case == "timeout" else "sort_plan.json")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(plan)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                socket.create_connection(("127.0.0.1", 8089), .2).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError("ASX did not listen")
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--packages", "2"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        set_coil(c, 883, False)
        set_coil(c, 884, False)
        set_coil(c, 914, True)
        set_coil(c, 915, True)
        set_register(c, 207, 14)
        assert coils(c, 914, 2) == [True, True]
        set_coil(c, 880, True)
        until = time.monotonic() + 150
        while time.monotonic() < until:
            if holding(c, 220)[0] >= 2:
                set_register(c, 207, 1000)
            data = holding(c, 530, 24)
            for slot in (0, 1):
                row = data[slot * 12:(slot + 1) * 12]
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"slot": slot, "token": row[0],
                                    "serial": row[1], "scanner_sequence": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual_trailer": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10],
                                    "command_id": row[11]}
            if len(rows) == 2 and xle.poll() is not None:
                break
            time.sleep(.1)
        assert len(rows) == 2, f"only {len(rows)} terminal Modbus rows: {rows}"
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        if case == "timeout":
            time.sleep(.5)
        asx_output = stop(asx)
        asx = None
        xe, ae = events(xle_output), events(asx_output)
        by_serial = {row["serial"]: row for row in rows.values()}
        assert set(by_serial) == {1, 2}, by_serial
        expected = {1: 2, 2: 0 if case == "timeout" else 5}
        for serial, row in by_serial.items():
            actual = expected[serial]
            assert row["actual_trailer"] == actual, row
            assert row["state"] == (6 if actual == 0 else 5), row
            assert row["reason"] == (1 if actual == 0 else 0), row
            assert row["scan_tick"] > 0 and row["divert_tick"] > row["scan_tick"], row
            if actual:
                assert row["scan_tick"] <= row["accept_tick"] < row["divert_tick"], row
            package_id = f"l1-{holding(c, 509)[0]}-{row['token']}-{serial}"
            scans = [e for e in xe if e["event"] == "scan" and e["package_id"] == package_id]
            outcomes = [e for e in xe if e["event"] == "plc_outcome" and e["package_id"] == package_id]
            assert len(scans) == len(outcomes) == 1 and scans[0]["barcode"] == row["barcode"]
            assert any(e.get("package_id") == package_id and
                       e.get("request_id") == scans[0]["request_id"] for e in ae)
            if actual:
                assert any(e["event"] == "plc_command" and e["package_id"] == package_id for e in xe)
            else:
                assert any(e["event"] == "safe_fallback" and e["package_id"] == package_id for e in xe)
            row["package_id"] = package_id
            row["scan_to_divert_ms"] = (row["divert_tick"] - row["scan_tick"]) * 100
            row["accept_margin_ms"] = ((row["divert_tick"] - row["accept_tick"]) * 100
                                       if row["accept_tick"] else None)
        assert (by_serial[1]["barcode"] == by_serial[2]["barcode"]) == (case == "repeat")
        trailers = holding(c, 222, 9)
        assert trailers[1] == 1 and trailers[4] == (0 if case == "timeout" else 1)
        assert sum(trailers) == (1 if case == "timeout" else 2)
        assert holding(c, 218)[0] == (1 if case == "timeout" else 0)
        assert holding(c, 220)[0] == 2
        print(json.dumps({"case": case, "rows": by_serial, "trailers": trailers,
                          "recirculated": holding(c, 218)[0],
                          "xle_events": xe, "asx_events": ae}, sort_keys=True), flush=True)
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
        actions += [(set_coil, 881 + i, v) for i, v in enumerate(original["enables"])]
        actions += [(set_register, 200 + i, v) for i, v in enumerate(original["setpoints"])]
        for fn, address, value in actions:
            try:
                fn(c, address, value)
            except Exception as exc:
                errors.append(f"{address}: {exc}")
        stop(xle)
        stop(asx)
        c.close()
        if errors:
            raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("different", "repeat", "timeout"))
    main(parser.parse_args().case)

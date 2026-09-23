"""Finite physical plant verification from SCADA over the existing Modbus path.

Run on scada with the plant service on drives. The failure case requires the
plant service to have --fail-confirm-token 1 for this one test run.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from pymodbus.client import ModbusTcpClient

from live_xle_multi import holding, coils, set_coil, set_register, events, stop

ROOT = Path.home() / "sorter-services"


def wait(predicate, seconds, message):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            value = predicate()
        except (OSError, ConnectionError):
            value = False
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError(message)


def main(case, speed=None, start_file=None, terminal_hold=1.0):
    count = 2 if case == "two" else 1
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7),
                "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0],
                "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-plant-")
    result = {}
    try:
        if start_file:
            marker = Path(start_file)
            marker.unlink(missing_ok=True)
            wait(marker.exists, 180, "browser start marker")
        set_coil(plc, 880, False)
        set_register(plc, 247, 137)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen = False
        def reset_complete():
            nonlocal seen
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen |= tick < old_tick or state in (1, 2)
            return seen and state == 0
        wait(reset_complete, 25, "scanner reset did not complete")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / "sort_plan.json")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX unavailable")
        set_coil(plc, 883, False); set_coil(plc, 884, False)
        set_coil(plc, 914, True); set_coil(plc, 915, True)
        set_coil(plc, 918, True)
        set_register(plc, 207, 14)
        if speed is not None:
            set_register(plc, 200, speed)
            for address in (203, 204, 205):
                set_register(plc, address, speed)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", str(count),
                                "--deadline", "180", "--journal",
                                str(Path(journal.name) / "outcomes.sqlite3"),
                                "--terminal-hold", str(terminal_hold)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0,
             12, "plant/XLe run identity not ready")
        epoch = holding(plc, 558, 2)
        set_coil(plc, 880, True)
        rows = {}
        max_occupied = 0
        sensor_events = []
        telemetry = {0: [], 1: []}
        hmi_two_live = False
        last_hmi_poll = 0.0
        last_sensor_seq = holding(plc, 585)[0]
        until = time.monotonic() + 180
        while time.monotonic() < until:
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_sensor_seq:
                last_sensor_seq = sensor[5]
                sensor_events.append({"type": sensor[0], "token": sensor[1],
                                      "serial": sensor[2], "actual": sensor[3],
                                      "position": sensor[4], "seq": sensor[5]})
            if holding(plc, 220)[0] >= count:
                set_register(plc, 207, 32000)
            block = holding(plc, 530, 24)
            view = holding(plc, 620, 24)
            for slot in (0, 1):
                row = view[slot * 10:(slot + 1) * 10]
                if view[20 + slot] == 1 and row[3]:
                    sample = (row[3], row[4], row[5], row[6], row[7])
                    if not telemetry[slot] or telemetry[slot][-1] != sample:
                        telemetry[slot].append(sample)
            if count == 2 and not hmi_two_live and time.monotonic() - last_hmi_poll > .5:
                last_hmi_poll = time.monotonic()
                try:
                    hmi_live = json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))
                    hmi_two_live = (hmi_live["plant_mode"] and
                                    all(status == 1 for status in hmi_live["plant_status"]) and
                                    all(row[3] for row in hmi_live["plant_rows"]))
                except (OSError, KeyError):
                    pass
            max_occupied = max(max_occupied,
                               sum(block[slot * 12 + 4] in (1, 2, 3, 4)
                                   for slot in (0, 1)))
            for slot in (0, 1):
                row = block[slot * 12:(slot + 1) * 12]
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"slot": slot, "token": row[0], "serial": row[1],
                                    "seq": row[2], "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10]}
            if len(rows) >= count and xle.poll() is not None:
                break
            time.sleep(.1)
        assert len(rows) == count, rows
        if case == "two":
            assert max_occupied == 2, max_occupied
            assert hmi_two_live, "OPC UA/HMI never showed two live plant rows"
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        asx_output = stop(asx); asx = None
        trailers = holding(plc, 222, 9)
        expected = {1: 2, 2: 5}
        if case == "failure":
            expected = {1: 0}
        for row in rows.values():
            actual = expected[row["serial"]]
            assert row["actual"] == actual, row
            assert row["state"] == (7 if case == "failure" else 5), row
            assert row["reason"] == (4 if case == "failure" else 0), row
            assert row["scan_tick"] and row["accept_tick"] >= row["scan_tick"]
            assert row["divert_tick"] > row["accept_tick"]
            sequence = [e["type"] for e in sensor_events if
                        e["token"] == row["token"] and e["serial"] == row["serial"]]
            assert sequence == ([1, 2, 3, 6] if case == "failure" else [1, 2, 3, 4]), (row, sensor_events)
            samples = telemetry[row["slot"]]
            own = [sample for sample in samples if
                   sample[0] == row["token"] and sample[1] == row["serial"]]
            assert any(sample[2] == 1 and sample[3] >= 100 for sample in own), (row, own)
            assert any(sample[2] in (2, 3, 4) and sample[3] >= 20 for sample in own), (row, own)
        assert sum(trailers) == (0 if case == "failure" else count), trailers
        assert holding(plc, 219)[0] == (1 if case == "failure" else 0)
        hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=5))
        assert hmi["plant_mode"] and hmi["plant_fault"] == 0, hmi
        if case == "failure":
            assert hmi["alarms"]["nohome"] and hmi["plant_failed_count"] == 1, hmi
        xe, ae = events(xle_output), events(asx_output)
        for row in rows.values():
            package_id = f"l1-{epoch[0] + epoch[1] * 30000}-{holding(plc, 509)[0]}-{row['token']}-{row['serial']}"
            assert any(e.get("event") == "scan" and e.get("package_id") == package_id
                       for e in xe), package_id
            assert any(e.get("event") == "asx_request" and e.get("package_id") == package_id
                       for e in ae), package_id
            assert any(e.get("event") == "plc_outcome" and e.get("package_id") == package_id
                       for e in xe), package_id
            row["package_id"] = package_id
        result = {"case": case, "epoch": epoch, "rows": rows, "trailers": trailers,
                  "max_occupied_slots": max_occupied,
                  "sensor_events": sensor_events,
                  "telemetry": telemetry, "hmi_two_live": hmi_two_live,
                  "plant_fault": holding(plc, 591)[0], "hmi_nohome": hmi["alarms"]["nohome"],
                  "xle_events": xe, "asx_events": ae}
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        errors = []
        try:
            set_coil(plc, 880, False)
            time.sleep(.3)
            assert not coils(plc, 880)[0]
        except Exception as exc:
            errors.append(f"stop: {exc}")
        actions = [(set_coil, 914, original["external"][0]),
                   (set_coil, 915, original["external"][1]),
                   (set_coil, 918, original["plant"]),
                   (set_register, 247, original["seed"])]
        actions += [(set_coil, 881 + i, v) for i, v in enumerate(original["enables"])]
        actions += [(set_register, 200 + i, v) for i, v in enumerate(original["setpoints"])]
        for fn, address, value in actions:
            try:
                fn(plc, address, value)
            except Exception as exc:
                errors.append(f"restore {address}: {exc}")
        stop(xle); stop(asx); journal.cleanup(); plc.close()
        if errors:
            raise RuntimeError("cleanup failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("one", "two", "failure"))
    parser.add_argument("--speed", type=int)
    parser.add_argument("--start-file")
    parser.add_argument("--terminal-hold", type=float, default=1.0)
    args = parser.parse_args()
    main(args.case, args.speed, args.start_file, args.terminal_hold)

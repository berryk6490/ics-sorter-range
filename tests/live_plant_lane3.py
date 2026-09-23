"""Live three-lane plant proof through PLC Modbus, XLe/ASX and HMI API.

Run on SCADA. The failure case requires a temporary drives plant process with
--fail-confirm-token 1. Each case restores settings and stops master.
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
from urllib.request import urlopen

from pymodbus.client import ModbusTcpClient
from live_xle_multi import holding, coils, set_coil, set_register, events, stop
from live_plant import wait

ROOT = Path.home() / "sorter-services"
CASES = {
    "all": {"lanes": [1, 2, 3], "barcodes": {1: 6001, 2: 5002, 3: 3003},
            "destinations": {1: 2, 2: 5, 3: 8}},
    "shared": {"lanes": [2, 3], "barcodes": {2: 5001, 3: 3002},
               "destinations": {2: 3, 3: 2}},
    "failure": {"lanes": [3], "barcodes": {3: 3001},
                "destinations": {3: 8}},
}


def main(case, start_file=None):
    config = CASES[case]
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0], "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-lane3-")
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
        wait(reset_complete, 25, "scanner reset")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / "lane3_plan.json")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX unavailable")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, lane in config["lanes"])
        set_coil(plc, 914, True); set_coil(plc, 915, True)
        set_coil(plc, 918, True)
        for address in (200, 201, 202, 203, 204, 205):
            set_register(plc, address, 120)
        for address in (207, 208, 209):
            set_register(plc, address, 14)
        count = len(config["lanes"])
        db_path = str(Path(journal.name) / "outcomes.sqlite3")
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", str(count),
                                "--deadline", "200", "--journal", db_path,
                                "--terminal-hold", "4"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        def identity_ready():
            if xle.poll() is not None:
                raise RuntimeError("XLe exited during identity setup: " + xle.communicate()[0])
            return holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0
        try:
            wait(identity_ready, 12, "plant/XLe identity")
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; PLC nonce={holding(plc, 509)[0]} "
                               f"epoch={holding(plc, 558, 2)} "
                               f"plant={holding(plc, 587, 5)} "
                               f"modes={coils(plc, 914, 5)}") from exc
        epoch = holding(plc, 558, 2)
        nonce = holding(plc, 509)[0]
        set_coil(plc, 880, True)
        rows = {}
        sensor_events = []
        telemetry = {lane: [] for lane in config["lanes"]}
        hmi_lanes = set()
        occupied_max = 0
        merge_wait = False
        min_gap = None
        pre_confirmation_counters = None
        last_sensor = holding(plc, 585)[0]
        last_hmi = 0
        until = time.monotonic() + 190
        while time.monotonic() < until:
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_sensor:
                last_sensor = sensor[5]
                sensor_events.append({"type": sensor[0], "token": sensor[1],
                                      "serial": sensor[2], "actual": sensor[3],
                                      "position": sensor[4], "seq": sensor[5],
                                      "lane": holding(plc, 578)[0]})
                if sensor[0] == 3 and not any(e["type"] in (4, 6)
                                              for e in sensor_events):
                    pre_confirmation_counters = holding(plc, 222, 9)
                    assert pre_confirmation_counters == [0] * 9
            if holding(plc, 220)[0] >= count:
                for address in (207, 208, 209):
                    set_register(plc, address, 32000)
            block = holding(plc, 530, 24)
            view = holding(plc, 620, 26)
            occupied = sum(block[i + 4] in (1, 2, 3, 4) for i in (0, 12))
            occupied_max = max(occupied_max, occupied)
            assert occupied <= 2
            on_outbound = []
            for slot in (0, 1):
                row = block[slot * 12:(slot + 1) * 12]
                lane = view[24 + slot]
                tele = view[slot * 10:(slot + 1) * 10]
                if row[0] and lane in telemetry and view[20 + slot] == 1:
                    sample = (row[0], tele[5], tele[6], tele[7])
                    if not telemetry[lane] or telemetry[lane][-1] != sample:
                        telemetry[lane].append(sample)
                    if tele[5] == 2 and tele[7] not in (4, 5, 6) and row[4] in (1, 2, 3, 4):
                        on_outbound.append((tele[6], tele[9]))
                    if case == "shared" and lane == 3 and row[4] == 3 and tele[5] == 6 and tele[6] == 140:
                        merge_wait = True
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"lane": lane, "slot": slot, "token": row[0],
                                    "serial": row[1], "seq": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10]}
            if len(on_outbound) == 2 and on_outbound[0][1] == on_outbound[1][1]:
                gap = abs(on_outbound[0][0] - on_outbound[1][0])
                min_gap = gap if min_gap is None else min(min_gap, gap)
                assert gap >= 32, on_outbound
            if time.monotonic() - last_hmi > .5:
                last_hmi = time.monotonic()
                try:
                    hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))
                    if hmi["plant_mode"]:
                        hmi_lanes.update(lane for lane, status in zip(
                            hmi["plant_lane"], hmi["plant_status"]) if status == 1)
                except (OSError, KeyError):
                    pass
            if len(rows) == count and xle.poll() is not None:
                break
            time.sleep(.1)
        assert len(rows) == count and occupied_max <= 2, (rows, occupied_max)
        assert set(config["lanes"]) <= hmi_lanes, hmi_lanes
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        asx_output = stop(asx); asx = None
        xe, ae = events(xle_output), events(asx_output)
        trailer = holding(plc, 222, 9)
        expected_trailer = [0] * 9
        for row in rows.values():
            lane = row["lane"]
            assert lane in config["lanes"], row
            package_id = f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{row['token']}-{row['serial']}"
            row["package_id"] = package_id
            assert row["barcode"] == config["barcodes"][lane], row
            assert row["destination"] == config["destinations"][lane], row
            failed = case == "failure"
            assert row["state"] == (7 if failed else 5), row
            assert row["actual"] == (0 if failed else row["destination"]), row
            assert row["reason"] == (4 if failed else 0), row
            assert row["scan_tick"] and row["accept_tick"] >= row["scan_tick"]
            assert row["divert_tick"] > row["accept_tick"]
            if not failed:
                expected_trailer[row["destination"] - 1] += 1
            for source, name in ((xe, "scan"), (xe, "plc_command"),
                                 (xe, "plc_outcome"), (ae, "asx_request")):
                assert any(e.get("event") == name and e.get("package_id") == package_id
                           for e in source), (name, package_id)
            samples = telemetry[lane]
            assert any(s[1] == {1: 1, 2: 5, 3: 6}[lane] and s[2] >= 100 for s in samples)
            assert any(s[1] == (row["destination"] - 1) // 3 + 2 for s in samples)
            kinds = [e["type"] for e in sensor_events if
                     e["token"] == row["token"] and e["serial"] == row["serial"]]
            assert kinds == [1, 2, 3, 6 if failed else 4], (package_id, kinds)
        assert trailer == expected_trailer, (trailer, expected_trailer)
        assert pre_confirmation_counters == [0] * 9
        assert holding(plc, 219)[0] == (1 if case == "failure" else 0)
        if case == "failure":
            assert holding(plc, 646)[0] == 3
        if case == "shared":
            assert merge_wait and min_gap is not None, (merge_wait, min_gap)
        if case == "all":
            assert [rows[token]["lane"] for token in sorted(rows)] == [1, 2, 3]
            previous = next(row for token, row in rows.items() if token in (1, 2)
                            and row["slot"] == rows[3]["slot"])
            release = next(e for e in xe if e["event"] == "plc_release" and
                           e["package_id"] == previous["package_id"])
            third_scan = next(e for e in xe if e["event"] == "scan" and
                              e["package_id"] == rows[3]["package_id"])
            assert release["event_ns"] < third_scan["event_ns"]
        with sqlite3.connect(db_path) as db:
            journal_rows = [(k, json.loads(v)) for k, v in
                            db.execute("SELECT identity,payload FROM outcomes ORDER BY identity")]
        assert len(journal_rows) == count
        assert {v["package_id"] for _, v in journal_rows} == {
            row["package_id"] for row in rows.values()}
        print(json.dumps({"case": case, "rows": rows, "trailer": trailer,
                          "sensor_events": sensor_events, "telemetry": telemetry,
                          "hmi_lanes": sorted(hmi_lanes), "merge_wait": merge_wait,
                          "minimum_gap_tenths": min_gap, "max_occupied": occupied_max,
                          "pre_confirmation_counters": pre_confirmation_counters,
                          "journal": journal_rows, "xle_events": xe,
                          "asx_events": ae}, sort_keys=True), flush=True)
    finally:
        errors = []
        try:
            set_coil(plc, 880, False)
        except Exception as exc:
            errors.append(str(exc))
        for fn, address, value in (
            [(set_coil, 914, original["external"][0]),
             (set_coil, 915, original["external"][1]),
             (set_coil, 918, original["plant"]),
             (set_register, 247, original["seed"])] +
            [(set_coil, 881 + i, v) for i, v in enumerate(original["enables"])] +
            [(set_register, 200 + i, v) for i, v in enumerate(original["setpoints"])]):
            try:
                fn(plc, address, value)
            except Exception as exc:
                errors.append(f"restore {address}: {exc}")
        stop(xle); stop(asx); journal.cleanup(); plc.close()
        if errors:
            raise RuntimeError("cleanup failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=CASES)
    parser.add_argument("--start-file")
    args = parser.parse_args()
    main(args.case, args.start_file)

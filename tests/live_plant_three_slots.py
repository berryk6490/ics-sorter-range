"""Live three-slot plant proof over the existing PLC Modbus path."""
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
    "shared_four": {"lanes": [1, 2, 3], "count": 4,
                    "barcodes": {1: 6001, 2: 5002, 3: 3003},
                    "destinations": {1: 1, 2: 2, 3: 3}},
    "late": {"lanes": [1, 2, 3], "count": 3,
             "barcodes": {1: 6001, 2: 5002, 3: 3003},
             "destinations": {1: 1, 2: 2, 3: 0}},
    "failure": {"lanes": [1, 2, 3], "count": 3,
                "barcodes": {1: 6001, 2: 5002, 3: 3003},
                "destinations": {1: 1, 2: 2, 3: 3}},
    "repeat_restart": {"lanes": [1, 2, 3], "count": 4,
                       "barcodes": {1: 6001, 2: 5002, 3: 3003},
                       "destinations": {1: 1, 2: 2, 3: 3}},
}



def main(case, start_file=None):
    config = CASES[case]
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0], "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-three-slot-")
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
                                "--plan", str(ROOT / ("three_slot_late_plan.json"
                                                         if case == "late" else "three_slot_plan.json"))],
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
        count = config["count"]
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
        full_wait_samples = 0
        other_lanes_limited = False
        all_lanes_limited = False
        restart_done = False
        xle_before_restart = ""
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
                                      "lane": holding(plc, 578)[0],
                                      "event_ns": time.monotonic_ns()})
                if sensor[0] == 3 and not any(e["type"] in (4, 6)
                                              for e in sensor_events):
                    pre_confirmation_counters = holding(plc, 222, 9)
                    assert pre_confirmation_counters == [0] * 9
            inducted = holding(plc, 220)[0]
            if inducted >= 3 and not other_lanes_limited:
                set_register(plc, 208, 32000)
                set_register(plc, 209, 32000)
                other_lanes_limited = True
            if inducted >= count and not all_lanes_limited:
                for address in (207, 208, 209):
                    set_register(plc, address, 32000)
                all_lanes_limited = True
            block = holding(plc, 530, 24) + holding(plc, 647, 12)
            view = holding(plc, 620, 26)
            view2 = holding(plc, 670, 12)
            lanes = holding(plc, 644, 2) + holding(plc, 659)
            occupied = sum(block[i + 4] in (1, 2, 3, 4) for i in (0, 12, 24))
            occupied_max = max(occupied_max, occupied)
            assert occupied <= 3
            if occupied == 3 and holding(plc, 220)[0] == 3:
                full_wait_samples += 1
            on_outbound = []
            for slot in (0, 1, 2):
                row = block[slot * 12:(slot + 1) * 12]
                lane = lanes[slot]
                tele = (view[slot * 10:(slot + 1) * 10]
                        if slot < 2 else view2[:10])
                status = view[20 + slot] if slot < 2 else view2[10]
                if row[0] and lane in telemetry and status == 1:
                    sample = (row[0], tele[5], tele[6], tele[7])
                    if not telemetry[lane] or telemetry[lane][-1] != sample:
                        telemetry[lane].append(sample)
                    if tele[5] == 2 and row[4] in (1, 2, 3, 4):
                        on_outbound.append((tele[6], tele[9]))
                    if lane in (2, 3) and row[4] == 3 and tele[5] in (5, 6) and tele[6] == 140:
                        merge_wait = True
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"lane": lane, "slot": slot, "token": row[0],
                                    "serial": row[1], "seq": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10]}
            for a in range(len(on_outbound)):
                for b in range(a + 1, len(on_outbound)):
                    gap = abs(on_outbound[a][0] - on_outbound[b][0])
                    min_gap = gap if min_gap is None else min(min_gap, gap)
                    assert gap >= 32, on_outbound
            if case == "repeat_restart" and not restart_done and rows:
                with sqlite3.connect(db_path) as db:
                    journal_count = db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
                if journal_count and any(row[4] in (5, 6, 7) for row in
                                         (block[i:i + 12] for i in (0, 12, 24))):
                    xle_before_restart = stop(xle)
                    xle = subprocess.Popen(
                        [sys.executable, str(ROOT / "xle.py"), "--multi",
                         "--packages", "0", "--deadline", "200",
                         "--journal", db_path, "--terminal-hold", "4"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    restart_done = True
            if time.monotonic() - last_hmi > .5:
                last_hmi = time.monotonic()
                try:
                    hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))
                    if hmi["plant_mode"]:
                        hmi_lanes.update(lane for lane, status in zip(
                            hmi["plant_lane"], hmi["plant_status"]) if status == 1)
                except (OSError, KeyError):
                    pass
            if len(rows) == count:
                if case == "repeat_restart":
                    with sqlite3.connect(db_path) as db:
                        journal_count = db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
                    if journal_count == count and all(
                            block[i + 4] == 0 for i in (0, 12, 24)):
                        break
                elif xle.poll() is not None:
                    break
            time.sleep(.1)
        assert len(rows) == count and occupied_max == 3 and (case != "shared_four" or full_wait_samples >= 5), \
            (rows, occupied_max, full_wait_samples)
        assert holding(plc, 220)[0] == count
        assert set(config["lanes"]) <= hmi_lanes, hmi_lanes
        if case == "repeat_restart":
            assert restart_done
            xle_output = xle_before_restart + stop(xle)
        else:
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
            if row["serial"] <= 3:
                assert row["barcode"] == config["barcodes"][lane], row
            if row["serial"] <= 3:
                assert row["destination"] == config["destinations"][lane], row
            if case == "repeat_restart" and row["serial"] == 4:
                assert row["barcode"] == 6001 and row["destination"] == 1
            failed = case == "failure" and lane == 3
            late = case == "late" and lane == 3
            assert row["state"] == (7 if failed else 6 if late else 5), row
            assert row["actual"] == (0 if failed or late else row["destination"]), row
            assert row["reason"] == (4 if failed else 1 if late else 0), row
            assert row["scan_tick"]
            if late:
                assert row["accept_tick"] == 0 and row["destination"] == 0
            else:
                assert row["accept_tick"] >= row["scan_tick"]
            assert row["divert_tick"] > row["scan_tick"]
            if not failed and not late:
                expected_trailer[row["destination"] - 1] += 1
            expected_events = [(xe, "scan"), (xe, "plc_outcome"),
                               (ae, "asx_request")]
            if not late:
                expected_events.append((xe, "plc_command"))
            for source, name in expected_events:
                assert any(e.get("event") == name and e.get("package_id") == package_id
                           for e in source), (name, package_id)
            if late:
                assert any(e.get("event") == "safe_fallback" and
                           e.get("package_id") == package_id and
                           e.get("reason", "").startswith("decision_timeout_or_error:")
                           for e in xe)
            samples = telemetry[lane]
            assert any(s[1] == {1: 1, 2: 5, 3: 6}[lane] and s[2] >= 100 for s in samples)
            if not late:
                assert any(s[1] == (row["destination"] - 1) // 3 + 2 for s in samples)
            kinds = [e["type"] for e in sensor_events if
                     e["token"] == row["token"] and e["serial"] == row["serial"]]
            expected_kind = 6 if failed else 5 if late else 4
            assert kinds[-3:] == [2, 3, expected_kind], (package_id, kinds)
        assert trailer == expected_trailer, (trailer, expected_trailer)
        assert pre_confirmation_counters == [0] * 9
        assert holding(plc, 219)[0] == (1 if case == "failure" else 0)
        if case == "failure":
            assert holding(plc, 646)[0] == 3
        if case == "shared_four":
            assert [rows[token]["lane"] for token in (1, 2, 3)] == [1, 2, 3]
            previous = next(row for token, row in rows.items() if token in (1, 2, 3)
                            and row["slot"] == rows[4]["slot"])
            release = next(e for e in xe if e["event"] == "plc_release" and
                           e["package_id"] == previous["package_id"])
            third_scan = next(e for e in xe if e["event"] == "scan" and
                              e["package_id"] == rows[4]["package_id"])
            assert release["event_ns"] < third_scan["event_ns"]
            fourth_induct = next(e for e in sensor_events if
                                 e["type"] == 1 and e["token"] == 4)
            assert release["event_ns"] < fourth_induct["event_ns"]
            assert merge_wait and min_gap is not None, (merge_wait, min_gap)
        if case == "repeat_restart":
            assert rows[1]["barcode"] == rows[4]["barcode"] == 6001
            for row in rows.values():
                assert sum(e.get("event") == "plc_command" and
                           e.get("package_id") == row["package_id"] for e in xe) == 1
                assert sum(e.get("event") == "plc_outcome" and
                           e.get("package_id") == row["package_id"] for e in xe) == 1
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
                          "full_wait_samples": full_wait_samples,
                          "xle_restarted": restart_done,
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

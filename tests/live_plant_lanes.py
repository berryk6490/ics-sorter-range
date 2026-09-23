"""Two-lane physical plant verification over deployed PLC, scanner, XLe and ASX.

Run on SCADA. The failure case needs the drives plant started temporarily with
--fail-confirm-token 2. Every run restores operator settings and stops master.
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
from live_plant import wait

ROOT = Path.home() / "sorter-services"


def main(case, start_file=None):
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0], "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-lanes-")
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
        plan = "sort_plan.json" if case == "distinct" else "lane2_shared_plan.json"
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / plan)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX unavailable")
        set_coil(plc, 883, True); set_coil(plc, 884, False)
        set_coil(plc, 914, True); set_coil(plc, 915, True)
        set_coil(plc, 918, True)
        for address in (200, 201, 203, 204, 205):
            set_register(plc, address, 120)
        set_register(plc, 207, 14); set_register(plc, 208, 14)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", "2", "--deadline", "180",
                                "--journal", str(Path(journal.name) / "outcomes.sqlite3"),
                                "--terminal-hold", "4"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0,
             12, "plant/XLe run identity not ready")
        epoch = holding(plc, 558, 2)
        nonce = holding(plc, 509)[0]
        set_coil(plc, 880, True)
        rows = {}
        sensor_events = []
        telemetry = {1: [], 2: []}
        max_occupied = 0
        merge_wait = False
        hmi_both_live = False
        minimum_same_update_gap = None
        mixed_update_reads = 0
        last_sensor_seq = holding(plc, 585)[0]
        last_hmi_poll = 0.0
        until = time.monotonic() + 180
        while time.monotonic() < until:
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_sensor_seq:
                last_sensor_seq = sensor[5]
                sensor_events.append({"type": sensor[0], "token": sensor[1],
                                      "serial": sensor[2], "actual": sensor[3],
                                      "position": sensor[4], "seq": sensor[5],
                                      "lane": holding(plc, 578)[0]})
            if holding(plc, 220)[0] >= 2:
                set_register(plc, 207, 32000); set_register(plc, 208, 32000)
            block = holding(plc, 530, 24)
            view = holding(plc, 620, 26)
            max_occupied = max(max_occupied, sum(block[i + 4] in (1, 2, 3, 4)
                                                 for i in (0, 12)))
            live_outbound = []
            for slot in (0, 1):
                row = block[slot * 12:(slot + 1) * 12]
                lane = view[24 + slot]
                tele = view[slot * 10:(slot + 1) * 10]
                if row[0] and view[20 + slot] == 1 and lane in (1, 2):
                    sample = (row[0], tele[5], tele[6], tele[7])
                    if not telemetry[lane] or telemetry[lane][-1] != sample:
                        telemetry[lane].append(sample)
                    if tele[5] == 2 and tele[7] not in (4, 5, 6) and row[4] in (1, 2, 3, 4):
                        live_outbound.append((tele[6], tele[9]))
                    if lane == 2 and row[4] == 3 and tele[5] == 5 and tele[6] == 140:
                        merge_wait = True
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"lane": lane, "slot": slot, "token": row[0],
                                    "serial": row[1], "seq": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10]}
            if len(live_outbound) == 2:
                if live_outbound[0][1] == live_outbound[1][1]:
                    gap = abs(live_outbound[0][0] - live_outbound[1][0])
                    minimum_same_update_gap = (gap if minimum_same_update_gap is None else
                                               min(minimum_same_update_gap, gap))
                    assert gap >= 31, live_outbound
                else:
                    mixed_update_reads += 1
            if not hmi_both_live and time.monotonic() - last_hmi_poll > .5:
                last_hmi_poll = time.monotonic()
                try:
                    hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))
                    hmi_both_live = (hmi["plant_mode"] and
                                     hmi["plant_lane"] == [1, 2] and
                                     hmi["plant_status"] == [1, 1])
                except (OSError, KeyError):
                    pass
            if len(rows) == 2 and xle.poll() is not None:
                break
            time.sleep(.1)
        assert len(rows) == 2 and max_occupied == 2, (rows, max_occupied)
        assert hmi_both_live, "HMI never saw both lane identities"
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        asx_output = stop(asx); asx = None
        xe, ae = events(xle_output), events(asx_output)
        actuals = {1: 2, 2: 5 if case == "distinct" else 0 if case == "failure" else 3}
        trailer = holding(plc, 222, 9)
        for row in rows.values():
            lane = row["lane"]
            assert lane in (1, 2), row
            identity = f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{row['token']}-{row['serial']}"
            row["package_id"] = identity
            assert row["barcode"] == (6001 if lane == 1 else 5002), row
            assert row["actual"] == actuals[lane], row
            assert row["state"] == (7 if case == "failure" and lane == 2 else 5), row
            assert row["reason"] == (4 if case == "failure" and lane == 2 else 0), row
            assert row["scan_tick"] and row["accept_tick"] >= row["scan_tick"]
            assert row["divert_tick"] > row["accept_tick"]
            kinds = [e["type"] for e in sensor_events if
                     e["token"] == row["token"] and e["serial"] == row["serial"]]
            assert kinds == [1, 2, 3, 6 if case == "failure" and lane == 2 else 4], (row, kinds)
            assert all(e["lane"] == lane for e in sensor_events if e["token"] == row["token"])
            samples = telemetry[lane]
            assert any(s[1] == (1 if lane == 1 else 5) and s[2] >= 100 for s in samples)
            assert any(s[1] == ((row["destination"] - 1) // 3 + 2) and s[2] >= 20
                       for s in samples)
            for source, name in ((xe, "scan"), (xe, "plc_command"),
                                 (xe, "plc_outcome"), (ae, "asx_request")):
                assert any(e.get("event") == name and e.get("package_id") == identity
                           for e in source), (name, identity)
        if case != "distinct":
            assert merge_wait, "shared outbound never held lane 2 at its merge"
        expected_trailer = [0] * 9
        for value in actuals.values():
            if value: expected_trailer[value - 1] += 1
        assert trailer == expected_trailer, trailer
        assert holding(plc, 219)[0] == (1 if case == "failure" else 0)
        result = {"case": case, "rows": rows, "epoch": epoch, "nonce": nonce,
                  "trailer": trailer, "sensor_events": sensor_events,
                  "telemetry": telemetry, "merge_wait": merge_wait,
                  "max_occupied": max_occupied, "hmi_both_live": hmi_both_live,
                  "minimum_same_update_gap_tenths": minimum_same_update_gap,
                  "mixed_update_reads": mixed_update_reads,
                  "xle_events": xe, "asx_events": ae}
        print(json.dumps(result, sort_keys=True), flush=True)
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
    parser.add_argument("case", choices=("distinct", "shared", "failure"))
    parser.add_argument("--start-file")
    args = parser.parse_args()
    main(args.case, args.start_file)

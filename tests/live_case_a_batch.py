"""SCADA-side four-package plant batch for the bounded analyst Case A write.

Start the analyst runner first. It waits until the first three packages occupy
the global slots; this runner then keeps lanes 1/2 from inducing a fifth and
leaves lane 3 at its normal 14-scan interval. The analyst's sole changed tag
delays the fourth package until restoration. All operator settings are saved
and restored in finally, and master is left stopped.
"""
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


def hmi_snapshot():
    return json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))


def main():
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0], "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="case-a-journal-")
    try:
        set_coil(plc, 880, False)
        set_register(plc, 247, 137)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen_reset = False
        def reset_complete():
            nonlocal seen_reset
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen_reset |= tick < old_tick or state in (1, 2)
            return seen_reset and state == 0
        wait(reset_complete, 25, "scanner reset")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"), "--plan",
                                str(ROOT / "case_a_plan.json")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX listener")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, True)
        set_coil(plc, 914, True); set_coil(plc, 915, True); set_coil(plc, 918, True)
        for address in range(200, 206):
            set_register(plc, address, 120)
        for address in (207, 208, 209):
            set_register(plc, address, 14)
        db_path = str(Path(journal.name) / "outcomes.sqlite3")
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"), "--multi",
                                "--packages", "4", "--deadline", "200",
                                "--journal", db_path, "--terminal-hold", "4"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        def ready():
            if xle.poll() is not None:
                raise RuntimeError("XLe exited before run: " + xle.communicate()[0])
            return holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0
        wait(ready, 12, "plant and XLe identity")
        epoch = holding(plc, 558, 2)
        nonce = holding(plc, 509)[0]
        set_coil(plc, 880, True)
        armed = False
        attack_seen = False
        restore_seen = False
        first_trailer_at = None
        withheld_after_trailer_s = 0.0
        hmi_attack = None
        hmi_restore = None
        plc_attack = None
        rows = {}
        sensor_events = []
        last_seq = holding(plc, 585)[0]
        fourth_induct_at = None
        attack_at = None
        restore_at = None
        last_hmi = 0.0
        deadline = time.monotonic() + 175
        while time.monotonic() < deadline:
            now = time.monotonic()
            inducted = holding(plc, 220)[0]
            if inducted >= 3 and not armed:
                set_register(plc, 207, 32000)
                set_register(plc, 208, 32000)
                armed = True
            interval = holding(plc, 209)[0]
            if armed and interval == 32000 and not attack_seen:
                attack_seen = True
                attack_at = now
                plc_attack = {"inducted": inducted, "interval": interval,
                              "run": coils(plc, 880)[0], "plant_fault": holding(plc, 591)[0]}
            if attack_seen and interval == 14 and not restore_seen:
                restore_seen = True
                restore_at = now
            if inducted >= 4 and fourth_induct_at is None:
                fourth_induct_at = now
                for address in (207, 208, 209):
                    set_register(plc, address, 32000)
            trailer = holding(plc, 222, 9)
            if sum(trailer) and first_trailer_at is None:
                first_trailer_at = now
            if attack_seen and not restore_seen and first_trailer_at is not None and inducted == 3:
                withheld_after_trailer_s = now - first_trailer_at
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_seq:
                last_seq = sensor[5]
                sensor_events.append({"type": sensor[0], "token": sensor[1],
                                      "serial": sensor[2], "actual": sensor[3],
                                      "seq": sensor[5], "lane": holding(plc, 578)[0]})
            block = holding(plc, 530, 24) + holding(plc, 647, 12)
            lanes = holding(plc, 644, 2) + holding(plc, 659)
            for slot in (0, 1, 2):
                row = block[slot * 12:(slot + 1) * 12]
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[0]] = {"lane": lanes[slot], "token": row[0],
                                    "serial": row[1], "barcode": row[3],
                                    "state": row[4], "destination": row[5],
                                    "actual": row[6], "reason": row[7],
                                    "scan_tick": row[8], "accept_tick": row[9],
                                    "divert_tick": row[10]}
            if now - last_hmi > .4:
                last_hmi = now
                try:
                    hmi = hmi_snapshot()
                    sample = {"connected": hmi["connected"],
                              "rate_sp": hmi["rate_sp"], "inducted": hmi["inducted"],
                              "trailer": hmi["trailer"], "alarms": hmi["alarms"],
                              "plant_fault": hmi["plant_fault"],
                              "scanner_fault_mask": hmi["scanner_fault_mask"],
                              "xle_liveness": hmi["xle_liveness"]}
                    if attack_seen and interval == 32000 and sample["rate_sp"][2] == 32000:
                        hmi_attack = sample
                    if restore_seen and interval == 14 and sample["rate_sp"][2] == 14:
                        hmi_restore = sample
                except (OSError, KeyError):
                    pass
            if len(rows) == 4 and xle.poll() is not None:
                break
            time.sleep(.1)
        print(json.dumps({"event": "case_a_batch_timing",
                          "attack_seen": attack_seen, "restore_seen": restore_seen,
                          "first_trailer_after_attack_s": (round(first_trailer_at - attack_at, 2)
                                                           if first_trailer_at and attack_at else None),
                          "withheld_after_trailer_s": round(withheld_after_trailer_s, 2),
                          "inducted": holding(plc, 220)[0], "rows": len(rows)},
                         sort_keys=True), flush=True)
        assert armed and attack_seen and restore_seen, (armed, attack_seen, restore_seen)
        assert withheld_after_trailer_s >= 5, withheld_after_trailer_s
        assert fourth_induct_at is not None and restore_at < fourth_induct_at
        assert hmi_attack is not None and hmi_restore is not None
        assert hmi_attack["connected"] and hmi_attack["inducted"] == 3
        assert hmi_attack["plant_fault"] == 0 and hmi_attack["scanner_fault_mask"] == 0
        assert hmi_attack["xle_liveness"] == 0 and not any(hmi_attack["alarms"].values())
        assert len(rows) == 4 and holding(plc, 220)[0] == 4, rows
        assert [rows[t]["lane"] for t in (1, 2, 3, 4)] == [1, 2, 3, 3], rows
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        asx_output = stop(asx); asx = None
        xe, ae = events(xle_output), events(asx_output)
        with sqlite3.connect(db_path) as db:
            journal_rows = [(k, json.loads(v)) for k, v in
                            db.execute("SELECT identity,payload FROM outcomes ORDER BY identity")]
        assert len(journal_rows) == 4
        for row in rows.values():
            lane = row["lane"]
            package_id = f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{row['token']}-{row['serial']}"
            row["package_id"] = package_id
            assert row["state"] == 5 and row["actual"] == row["destination"] and row["reason"] == 0
            for source, kind in ((xe, "scan"), (xe, "plc_command"),
                                 (xe, "plc_outcome"), (ae, "asx_request")):
                assert any(e.get("package_id") == package_id and e.get("event") == kind
                           for e in source), (package_id, kind)
            assert any(v["package_id"] == package_id for _, v in journal_rows)
        expected = [0] * 9
        for row in rows.values():
            expected[row["actual"] - 1] += 1
        assert holding(plc, 222, 9) == expected
        print(json.dumps({"rows": rows, "trailer": expected, "sensor_events": sensor_events,
                          "hmi_attack": hmi_attack, "hmi_restored": hmi_restore,
                          "plc_attack": plc_attack,
                          "withheld_after_first_trailer_s": round(withheld_after_trailer_s, 2),
                          "attack_duration_s": round(restore_at - attack_at, 2),
                          "restore_to_fourth_induction_s": round(fourth_induct_at - restore_at, 2),
                          "journal": journal_rows, "xle_events": xe, "asx_events": ae},
                         sort_keys=True), flush=True)
    finally:
        errors = []
        try:
            set_coil(plc, 880, False)
        except Exception as exc:
            errors.append(str(exc))
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
    main()

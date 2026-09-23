"""SCADA Modbus verification of same-seed cold restarts and ten slot reuses.

Run `first`, cold restart the PLC process without removing the journal, then
run `second`. For `batch`, start the documented repeated-barcode scanner
fixture and deploy epoch_batch_plan.json to the SCADA service directory.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

from pymodbus.client import ModbusTcpClient
from live_xle_multi import holding, coils, set_coil, set_register, events, stop


ROOT = Path.home() / "sorter-services"
JOURNAL = ROOT / "xle-outcomes.sqlite3"


def run(phase):
    count = 10 if phase == "batch" else 1
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert client.connect()
    original = {"enables": coils(client, 881, 7),
                "external": coils(client, 914, 2),
                "setpoints": holding(client, 200, 11),
                "seed": holding(client, 247)[0]}
    asx = xle = None
    rows = {}
    try:
        set_coil(client, 880, False)
        if phase == "batch":
            set_register(client, 247, 137)
            old_tick = holding(client, 243)[0]
            set_coil(client, 910, True)
            seen = False
            until = time.monotonic() + 20
            while time.monotonic() < until:
                tick, state = holding(client, 243)[0], holding(client, 255)[0]
                seen |= tick < old_tick or state in (1, 2)
                if seen and state == 0:
                    break
                time.sleep(.1)
            assert seen and holding(client, 255)[0] == 0
        else:
            assert holding(client, 255)[0] == 0, "cold PLC scanner reset incomplete"
            assert holding(client, 509)[0] == 1, "expected cold PLC nonce 1"
            assert holding(client, 247)[0] == 137, "expected seed 137"
        assert holding(client, 558, 2) == [0, 0], "cold/reset PLC still has identity"
        assert holding(client, 561)[0] == 1
        before = sqlite3.connect(JOURNAL).execute("SELECT count(*) FROM outcomes").fetchone()[0] if JOURNAL.exists() else 0
        plan = ROOT / ("epoch_batch_plan.json" if phase == "batch" else "sort_plan.json")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"), "--plan", str(plan)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                socket.create_connection(("127.0.0.1", 8089), .2).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError("ASX unavailable")
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"), "--multi",
                                "--packages", str(count), "--deadline", "300",
                                "--journal", str(JOURNAL)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        set_coil(client, 883, False)
        set_coil(client, 884, False)
        set_coil(client, 914, True)
        set_coil(client, 915, True)
        set_register(client, 207, 14)
        until = time.monotonic() + 6
        while time.monotonic() < until:
            epoch_words = holding(client, 558, 2)
            if epoch_words != [0, 0]:
                break
            if xle.poll() is not None:
                raise RuntimeError("XLe exited during identity handshake: " + stop(xle))
            time.sleep(.05)
        epoch = epoch_words[0] + epoch_words[1] * 30000
        assert epoch and holding(client, 561)[0] == 0, (epoch_words, holding(client, 561))
        nonce = holding(client, 509)[0]
        set_coil(client, 880, True)
        until = time.monotonic() + 300
        while time.monotonic() < until:
            if holding(client, 220)[0] >= count:
                set_register(client, 207, 1000)
            data = holding(client, 530, 24)
            for slot in (0, 1):
                row = data[slot * 12:(slot + 1) * 12]
                if row[0] and row[4] in (5, 6, 7):
                    rows[row[1]] = {"slot": slot, "token": row[0],
                                    "serial": row[1], "scanner_sequence": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual_trailer": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10],
                                    "command_id": row[11]}
            if len(rows) == count and xle.poll() is not None:
                break
            time.sleep(.1)
        assert len(rows) == count, f"only {len(rows)}/{count} terminal Modbus rows: {rows}"
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        if phase == "batch":
            time.sleep(.5)  # allow the deliberately late ASX response to log
        asx_output = stop(asx)
        asx = None
        xe, ae = events(xle_output), events(asx_output)
        journal = sqlite3.connect(JOURNAL)
        journal_rows = dict(journal.execute("SELECT identity,payload FROM outcomes"))
        journal.close()
        assert len(journal_rows) == before + count, (before, len(journal_rows), count)
        expected = {i: d for i, d in enumerate([2, 5, 8, 2, 5, 0, 8, 2, 5, 8], 1)} if phase == "batch" else {1: 2}
        for serial, row in sorted(rows.items()):
            dest = expected[serial]
            assert row["actual_trailer"] == dest and row["state"] == (5 if dest else 6), row
            assert row["reason"] == (0 if dest else 1), row
            assert row["scanner_sequence"] and row["scan_tick"] < row["divert_tick"], row
            if dest:
                assert row["scan_tick"] <= row["accept_tick"] < row["divert_tick"], row
            package_id = f"l1-{epoch}-{nonce}-{row['token']}-{serial}"
            key = f"{epoch}:{nonce}:{row['token']}:{serial}:{row['scanner_sequence']}"
            payload = json.loads(journal_rows[key])
            assert payload["package_id"] == package_id and payload["actual_trailer"] == dest
            scans = [e for e in xe if e["event"] == "scan" and e["package_id"] == package_id]
            outcomes = [e for e in xe if e["event"] == "plc_outcome" and e["package_id"] == package_id]
            assert len(scans) == len(outcomes) == 1 and scans[0]["barcode"] == row["barcode"]
            assert any(e.get("event") == "asx_request" and e.get("package_id") == package_id and
                       e.get("request_id") == scans[0]["request_id"] for e in ae)
            if dest:
                assert any(e["event"] == "asx_decision" and e["package_id"] == package_id and
                           e["destination"] == dest for e in xe)
                assert any(e["event"] == "plc_command" and e["package_id"] == package_id and
                           e["command_id"] == row["command_id"] for e in xe)
            else:
                assert any(e["event"] == "safe_fallback" and e["package_id"] == package_id and
                           "timeout" in e["reason"] for e in xe)
                assert not any(e["event"] == "plc_command" and e["package_id"] == package_id for e in xe)
            row["package_id"] = package_id
            row["scanner_to_divert_ms"] = (row["divert_tick"] - row["scan_tick"]) * 100
        if phase == "batch":
            assert all(r["barcode"] == 6001 for r in rows.values()), rows
        trailers = holding(client, 222, 9)
        actual_counts = Counter({i + 1: n for i, n in enumerate(trailers) if n})
        expected_counts = Counter(d for d in expected.values() if d)
        assert actual_counts == expected_counts, (actual_counts, expected_counts)
        assert holding(client, 218)[0] == (1 if phase == "batch" else 0)
        assert holding(client, 220)[0] == count
        assert all(r[0] == 0 for r in (holding(client, 530, 12), holding(client, 542, 12)))
        print(json.dumps({"phase": phase, "run_epoch": epoch, "nonce": nonce,
                          "journal_before": before, "journal_after": len(journal_rows),
                          "rows": rows, "trailers": trailers,
                          "recirculated": holding(client, 218)[0],
                          "xle_events": xe, "asx_events": ae}, sort_keys=True), flush=True)
    finally:
        errors = []
        try:
            set_coil(client, 880, False)
            assert not coils(client, 880)[0]
        except Exception as exc:
            errors.append(f"stop: {exc}")
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
        stop(xle)
        stop(asx)
        client.close()
        if errors:
            raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("first", "second", "batch"))
    run(parser.parse_args().phase)

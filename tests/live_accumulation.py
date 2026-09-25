"""Bounded Phase 2A live scenarios over PLC Modbus and PLC-backed HMI API.

The drives plant fixture is started separately on its existing VM. This
runner never writes plant internals or opens a route around the PLC.
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
from live_xle_multi import holding as raw_holding, coils, set_coil, set_register, events, stop
from live_plant import wait

ROOT = Path.home() / "sorter-services"
CASES = {
    "normal": dict(lanes=(1, 2, 3), count=3, plan="lane3_plan.json",
                   destinations={1: 2, 2: 5, 3: 8}),
    "lane_hold": dict(lanes=(1,), count=3, plan="accumulation_plan.json",
                      destinations={1: 2, 2: 2, 3: 2}),
    "merge_hold": dict(lanes=(1, 2, 3), count=4, plan="three_slot_plan.json",
                       destinations={1: 1, 2: 2, 3: 3, 4: 1}),
    "drive_stop": dict(lanes=(1,), count=1, plan="lane3_plan.json",
                       destinations={1: 2}),
}
ACTIVE = (1, 2, 3, 4)
TERMINAL = (5, 6, 7)


def holding(plc, address, count=1):
    """Bound one lost read under concurrent OPC UA/plant polling."""
    for attempt in range(3):
        try:
            return raw_holding(plc, address, count)
        except (OSError, RuntimeError):
            if attempt == 2:
                raise
            time.sleep(.12)


def snapshot(plc):
    return {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
            "plant": coils(plc, 918)[0], "photoeye": coils(plc, 919)[0],
            "accumulation": coils(plc, 920)[0],
            "setpoints": holding(plc, 200, 11), "seed": holding(plc, 247)[0]}


def restore(plc, original):
    errors = []
    actions = [(set_coil, 880, False),
               (set_coil, 914, original["external"][0]),
               (set_coil, 915, original["external"][1]),
               (set_coil, 918, original["plant"]),
               (set_coil, 919, original["photoeye"]),
               (set_coil, 920, original["accumulation"]),
               (set_register, 247, original["seed"])]
    actions += [(set_coil, 881 + i, value)
                for i, value in enumerate(original["enables"])]
    actions += [(set_register, 200 + i, value)
                for i, value in enumerate(original["setpoints"])]
    for fn, address, value in actions:
        try:
            fn(plc, address, value)
        except Exception as exc:
            errors.append(f"{address}: {exc}")
    return errors


def cleanup_run(plc, original, xle, asx, journal):
    """Stop movement and processes first; reset only if a failed run left slots.

    A reset may leave the established cold-start PlantFault=1 until the next
    valid XLe/plant handshake. That state is reported, never overwritten.
    """
    errors = []
    try:
        set_coil(plc, 880, False)
    except Exception as exc:
        errors.append(f"stop: {exc}")
    for name, process in (("XLe", xle), ("ASX", asx)):
        if process is not None:
            try:
                stop(process)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
    try:
        if (any(holding(plc, base + 4)[0] != 0 for base in (530, 542, 647)) or
            holding(plc, 748)[0] or holding(plc, 788)[0]):
            old_tick = holding(plc, 243)[0]
            set_coil(plc, 910, True)
            wait(lambda: (holding(plc, 243)[0] < old_tick and
                          holding(plc, 255)[0] == 0), 30, "cleanup reset")
            assert all(holding(plc, base + 4)[0] == 0
                       for base in (530, 542, 647))
    except Exception as exc:
        errors.append(f"reset: {exc}")
    errors.extend(restore(plc, original))
    try:
        journal.cleanup()
    except Exception as exc:
        errors.append(f"journal: {exc}")
    plc.close()
    return errors


def checkpoint_sample(plc, case, epoch, nonce, block, lanes, zone, drive_feedback,
                      inducted, full_wait):
    """PLC-validated, identity-bound observation at an evidence hold."""
    rows = []
    for slot in range(3):
        row = block[slot * 12:(slot + 1) * 12]
        z = zone[slot * 5:(slot + 1) * 5] + [zone[15 + slot]]
        lane = lanes[slot]
        rows.append({"slot": slot, "lane": lane, "token": row[0],
                     "serial": row[1], "scanner_sequence": row[2],
                     "barcode": row[3], "state": row[4],
                     "destination": row[5], "actual": row[6],
                     "package_id": (f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-"
                                    f"{row[0]}-{row[1]}") if row[0] else None,
                     "zone": z[0], "motion": z[1], "hold_reason": z[2],
                     "dwell": z[3], "sequence": z[4], "quality": z[5]})
    return {"case": case, "epoch": epoch, "nonce": nonce,
            "observed_wall_ns": time.time_ns(),
            "observed_monotonic_ns": time.monotonic_ns(),
            "rows": rows, "inducted": inducted, "full_wait_samples": full_wait,
            "raw_ready_mask": holding(plc, 784)[0],
            "commit_sequence": holding(plc, 785)[0],
            "validated_ready_mask": holding(plc, 786)[0],
            "zone_age": holding(plc, 787)[0], "zone_fault": holding(plc, 788)[0],
            "plant_fault": holding(plc, 591)[0],
            "photoeye_faults": holding(plc, 748, 3),
            "master": coils(plc, 880)[0],
            "counters": holding(plc, 222, 9),
            "drive_feedback": drive_feedback[-8:]}


def checkpoint_valid(case, sample):
    rows = sample["rows"]
    if sample["zone_fault"] or sample["plant_fault"] or any(sample["photoeye_faults"]):
        return False
    if case == "lane_hold":
        return sum(r["lane"] == 1 and r["state"] in ACTIVE and
                   r["quality"] == 1 and r["motion"] == 2 and
                   r["zone"] in (1, 2, 3) and r["dwell"] >= 10 for r in rows) >= 1
    if case == "merge_hold":
        return (sample["inducted"] == 3 and sample["full_wait_samples"] >= 5 and
                all(r["state"] in ACTIVE and r["quality"] == 1 and
                    r["zone"] == 4 and r["motion"] == 3 for r in rows) and
                {r["lane"] for r in rows} == {1, 2, 3})
    if case == "drive_stop":
        return any(r["state"] in ACTIVE and r["quality"] == 1 and
                   r["motion"] == 4 for r in rows) and bool(
                       sample["drive_feedback"] and
                       sample["drive_feedback"][-1][0] == 0)
    return False


def run(case, hold_marker=None, evidence_hold=None, cleanup_report=None):
    config = CASES[case]
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not plc.connect():
        raise RuntimeError("PLC Modbus unavailable")
    original = snapshot(plc)
    asx = xle = None
    drive = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-accumulation-")
    failure = None
    try:
        assert holding(plc, 249)[0] == 24113, "wrong PLC identity"
        assert not coils(plc, 880)[0], "sorter must be stopped"
        assert all(holding(plc, base + 4)[0] == 0 for base in (530, 542, 647))
        # A cold OpenPLC process initializes PlantFault=1 until a valid
        # plant/XLe run identity arrives. That expected off-mode state is
        # cleared by the documented reset/handshake below.
        assert holding(plc, 591)[0] in (0, 1)
        assert holding(plc, 748, 3) == [0, 0, 0]
        assert holding(plc, 788)[0] == 0
        if case == "drive_stop":
            drive = ModbusTcpClient("10.10.1.21", port=502, timeout=2)
            assert drive.connect(), "induct 1 VFD unavailable"
        set_register(plc, 247, 137)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen_reset = False

        def reset_done():
            nonlocal seen_reset
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen_reset |= tick < old_tick or state in (1, 2)
            return seen_reset and state == 0

        wait(reset_done, 30, "scanner reset")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / config["plan"])],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX startup")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, lane in config["lanes"])
        for address in range(200, 206):
            set_register(plc, address, 120)
        for address in range(207, 210):
            set_register(plc, address, 14)
        for coil in (914, 915, 918, 919, 920):
            set_coil(plc, coil, True)
        db_path = str(Path(journal.name) / "outcomes.sqlite3")
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", str(config["count"]),
                                "--deadline", "200", "--journal", db_path,
                                "--terminal-hold", "4"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)

        def ready():
            if xle.poll() is not None:
                raise RuntimeError("XLe exited: " + xle.communicate()[0])
            return (holding(plc, 558, 2) != [0, 0] and
                    holding(plc, 591)[0] == 0 and holding(plc, 786)[0] != 0)

        wait(ready, 20, "run identity and plant capacity")
        epoch, nonce = holding(plc, 558, 2), holding(plc, 509)[0]
        set_coil(plc, 880, True)
        rows = {}
        zone_samples = {}
        sensor_events = []
        hmi_seen = set()
        held_lanes = set()
        max_held = max_slots = full_wait = 0
        min_spacing = None
        drive_stopped = drive_restarted = False
        drive_feedback = []
        drive_stop_at = 0.0
        checkpoint_done = False
        counters_before_confirmation = None
        last_event_seq = holding(plc, 585)[0]
        last_hmi = 0.0
        deadline = time.monotonic() + 230
        while time.monotonic() < deadline:
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_event_seq:
                last_event_seq = sensor[5]
                sensor_events.append({"type": sensor[0], "token": sensor[1],
                                      "serial": sensor[2], "actual": sensor[3],
                                      "position": sensor[4], "seq": sensor[5],
                                      "lane": holding(plc, 578)[0],
                                      "event_ns": time.monotonic_ns()})
                if sensor[0] == 3 and not any(e["type"] in (4, 6)
                                              for e in sensor_events):
                    counters_before_confirmation = holding(plc, 222, 9)
                    assert counters_before_confirmation == [0] * 9
            inducted = holding(plc, 220)[0]
            if case == "merge_hold" and inducted >= 3:
                set_register(plc, 208, 32000)
                set_register(plc, 209, 32000)
            if inducted >= config["count"]:
                for address in range(207, 210):
                    set_register(plc, address, 32000)
            block = holding(plc, 530, 24) + holding(plc, 647, 12)
            lanes = holding(plc, 644, 2) + holding(plc, 659)
            view = holding(plc, 620, 26)
            view2 = holding(plc, 670, 12)
            zone = holding(plc, 766, 23)
            occupied = sum(block[i + 4] in ACTIVE for i in (0, 12, 24))
            max_slots = max(max_slots, occupied)
            if occupied == 3 and inducted == 3:
                full_wait += 1
            assert occupied <= 3 and zone[22] == 0, (occupied, zone)
            held = 0
            outbound = []
            for slot in range(3):
                row = block[slot * 12:(slot + 1) * 12]
                lane = lanes[slot]
                tele = view[slot * 10:(slot + 1) * 10] if slot < 2 else view2[:10]
                status = view[20 + slot] if slot < 2 else view2[10]
                z = zone[slot * 5:(slot + 1) * 5] + [zone[15 + slot]]
                if row[0] and status == 1 and z[5] == 1:
                    identity = f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{row[0]}-{row[1]}"
                    sample = {"position": tele[6], "belt": tele[5],
                              "zone": z[0], "motion": z[1], "hold": z[2],
                              "dwell": z[3], "seq": z[4]}
                    history = zone_samples.setdefault(identity, [])
                    if not history or history[-1] != sample:
                        history.append(sample)
                    if z[1] in (2, 3):
                        held += 1
                        held_lanes.add(lane)
                    if z[0] == 5 and row[4] in ACTIVE:
                        outbound.append((row[0], tele[5], tele[6]))
                    if case == "drive_stop" and not drive_stopped and z[0] == 2:
                        set_coil(plc, 882, False)
                        drive_stopped = True
                        drive_stop_at = time.monotonic()
                if row[0] and row[4] in TERMINAL:
                    rows[row[0]] = {"lane": lane, "slot": slot, "token": row[0],
                                    "serial": row[1], "seq": row[2],
                                    "barcode": row[3], "state": row[4],
                                    "destination": row[5], "actual": row[6],
                                    "reason": row[7], "scan_tick": row[8],
                                    "accept_tick": row[9], "divert_tick": row[10]}
            max_held = max(max_held, held)
            if drive is not None:
                feedback = drive.read_holding_registers(5, 1, slave=1)
                if feedback.isError():
                    raise RuntimeError("VFD speed feedback unavailable")
                speed = feedback.registers[0]
                motion = next((z["motion"] for history in zone_samples.values()
                               for z in history[-1:] if z["zone"] in (1, 2, 3)), 0)
                sample = (speed, motion)
                if not drive_feedback or drive_feedback[-1] != sample:
                    drive_feedback.append(sample)
            if held and hold_marker and not Path(hold_marker).exists():
                Path(hold_marker).write_text(str(time.time()) + "\n")
            if evidence_hold is not None and not checkpoint_done:
                sample = checkpoint_sample(plc, case, epoch, nonce, block, lanes,
                                           zone, drive_feedback, inducted, full_wait)
                if checkpoint_valid(case, sample):
                    evidence_hold(sample)
                    checkpoint_done = True
            for a in range(len(outbound)):
                for b in range(a + 1, len(outbound)):
                    if outbound[a][1] == outbound[b][1]:
                        gap = abs(outbound[a][2] - outbound[b][2])
                        min_spacing = gap if min_spacing is None else min(min_spacing, gap)
                        assert gap >= 32, outbound  # required 3.2-cell clearance
            if drive_stopped and not drive_restarted and evidence_hold is None and time.monotonic() - drive_stop_at >= 8:
                assert any(s["motion"] == 4 for h in zone_samples.values() for s in h)
                set_coil(plc, 882, True)
                drive_restarted = True
            if drive_stopped and checkpoint_done and not drive_restarted:
                set_coil(plc, 882, True)
                drive_restarted = True
            if time.monotonic() - last_hmi > .5:
                last_hmi = time.monotonic()
                try:
                    hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=1))
                    # OPC UA/HMI can lag the first PLC mode write by one
                    # poll, especially after a cold OpenPLC core restart.
                    # Require the active view below, but do not treat that
                    # initial off-mode snapshot as a process fault.
                    if hmi["accumulation_mode"]:
                        assert hmi["zone_fault"] == 0
                        hmi_seen.update(lane for lane, status in zip(
                            hmi["plant_lane"], hmi["plant_status"]) if status == 1)
                except (OSError, KeyError):
                    pass
            assert holding(plc, 748, 3) == [0, 0, 0]
            if len(rows) == config["count"] and xle.poll() is not None:
                break
            time.sleep(.2)
        assert len(rows) == config["count"], rows
        if evidence_hold is not None:
            assert checkpoint_done, "required evidence checkpoint was never reached"
        assert max_slots <= 3 and (case != "merge_hold" or
                                   (max_slots == 3 and full_wait >= 5 and max_held == 3))
        assert set(config["lanes"]) <= hmi_seen
        if case == "lane_hold":
            assert 1 in held_lanes and max_held >= 2
        if case == "drive_stop":
            assert drive_stopped and drive_restarted
            assert any(speed == 0 and motion == 4 for speed, motion in drive_feedback)
            assert any(speed > 0 and motion == 1 for speed, motion in drive_feedback)
        xle_output = xle.communicate(timeout=5)[0]
        assert xle.returncode == 0, xle_output
        xle = None
        asx_output = stop(asx); asx = None
        xe, ae = events(xle_output), events(asx_output)
        trailer = holding(plc, 222, 9)
        expected = [0] * 9
        for row in rows.values():
            dest = config["destinations"][row["serial"] if case != "normal" else row["lane"]]
            assert row["state"] == 5 and row["actual"] == row["destination"] == dest, row
            assert row["scan_tick"] > 0 and row["accept_tick"] >= row["scan_tick"]
            assert row["divert_tick"] > row["accept_tick"]
            expected[dest - 1] += 1
            package_id = f"l{row['lane']}-{epoch[0]+epoch[1]*30000}-{nonce}-{row['token']}-{row['serial']}"
            row["package_id"] = package_id
            for source, name in ((xe, "scan"), (xe, "plc_command"),
                                 (xe, "plc_outcome"), (ae, "asx_request")):
                assert sum(e.get("event") == name and e.get("package_id") == package_id
                           for e in source) == 1, (name, package_id)
            kinds = [e["type"] for e in sensor_events if e["token"] == row["token"]]
            # The serialized event/ACK pair can complete between supervisory
            # polls. PLC scan/divert/outcome ticks and confirmation counters
            # prove acceptance; the plant's bounded log proves event order.
            assert kinds == sorted(kinds), (package_id, kinds)
        if case == "merge_hold":
            assert [rows[t]["lane"] for t in (1, 2, 3)] == [1, 2, 3]
            assert rows[4]["lane"] == 1
            prior = next(row for token, row in rows.items()
                         if token in (1, 2, 3) and row["slot"] == rows[4]["slot"])
            release = next(e for e in xe if e.get("event") == "plc_release" and
                           e.get("package_id") == prior["package_id"])
            fourth_scan = next(e for e in xe if e.get("event") == "scan" and
                               e.get("package_id") == rows[4]["package_id"])
            assert release["event_ns"] < fourth_scan["event_ns"]
        assert trailer == expected and counters_before_confirmation == [0] * 9
        with sqlite3.connect(db_path) as db:
            journal_rows = [(key, json.loads(value)) for key, value in
                            db.execute("SELECT identity,payload FROM outcomes ORDER BY identity")]
        assert {v["package_id"] for _, v in journal_rows} == {
            row["package_id"] for row in rows.values()}
        result = {"case": case, "rows": rows, "trailer": trailer,
                  "sensor_events": sensor_events, "zone_samples": zone_samples,
                  "hmi_lanes": sorted(hmi_seen),
                  "held_lanes": sorted(held_lanes), "max_held": max_held,
                  "max_slots": max_slots, "full_wait_samples": full_wait,
                  "minimum_spacing_tenths": min_spacing,
                  "pre_confirmation_counters": counters_before_confirmation,
                  "drive_stopped": drive_stopped, "drive_restarted": drive_restarted,
                  "drive_feedback": drive_feedback,
                  "journal": journal_rows, "xle_events": xe, "asx_events": ae}
        print(json.dumps(result, sort_keys=True), flush=True)
        return result
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_errors = cleanup_run(plc, original, xle, asx, journal)
        if cleanup_report is not None:
            cleanup_report.update({"errors": cleanup_errors,
                                   "original_failure": repr(failure)})
        if drive is not None:
            drive.close()
        print(json.dumps({"cleanup": "ok" if not cleanup_errors else "failed",
                          "errors": cleanup_errors, "original_failure": repr(failure)}), flush=True)
        if cleanup_errors and failure is None:
            raise RuntimeError("cleanup failed: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=CASES)
    parser.add_argument("--hold-marker")
    args = parser.parse_args()
    run(args.case, args.hold_marker)

"""SCADA observer for a host-controlled active-run plant service stop.

Launch detached on SCADA. It writes /tmp/plant-stop-ready when three slots are
moving; the host stops sorter-plant.service on drives and this observer records
the resulting PLC/XLe evidence. Deliberately leaves plant mode set for the
separate active-run service restart check. Recovery resets the PLC afterward.
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

from pymodbus.client import ModbusTcpClient
from live_xle_multi import holding, coils, set_coil, set_register, events, stop

ROOT = Path.home() / "sorter-services"
READY = Path("/tmp/plant-stop-ready")
RESULT = Path("/tmp/plant-stop-result.json")
JOURNAL = Path("/tmp/plant-stop-journal.sqlite3")


def wait(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(.1)
    raise TimeoutError(label)


def rows(client):
    data = holding(client, 530, 24) + holding(client, 647, 12)
    return [data[:12], data[12:24], data[24:]]


def main():
    READY.unlink(missing_ok=True)
    RESULT.unlink(missing_ok=True)
    JOURNAL.unlink(missing_ok=True)
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert client.connect()
    original = {"enables": coils(client, 881, 7),
                "external": coils(client, 914, 2),
                "plant": coils(client, 918)[0],
                "setpoints": holding(client, 200, 11),
                "seed": holding(client, 247)[0]}
    Path("/tmp/plant-stop-original.json").write_text(json.dumps(original))
    asx = xle = None
    try:
        set_coil(client, 880, False)
        set_register(client, 247, 137)
        old_tick = holding(client, 243)[0]
        set_coil(client, 910, True)
        seen = False
        def reset_done():
            nonlocal seen
            tick, state = holding(client, 243)[0], holding(client, 255)[0]
            seen |= tick < old_tick or state in (1, 2)
            return seen and state == 0
        wait(reset_done, 25, "scanner reset")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / "three_slot_plan.json")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        def asx_ready():
            try:
                socket.create_connection(("127.0.0.1", 8089), .2).close()
                return True
            except OSError:
                return False
        wait(asx_ready, 8, "ASX unavailable")
        set_coil(client, 882, True); set_coil(client, 883, True)
        set_coil(client, 884, True)
        set_coil(client, 914, True); set_coil(client, 915, True)
        set_coil(client, 918, True)
        for address in (200, 201, 202, 203, 204, 205):
            set_register(client, address, 60)
        for address in (207, 208, 209):
            set_register(client, address, 14)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", "0",
                                "--journal", str(JOURNAL), "--deadline", "90"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: holding(client, 558, 2) != [0, 0] and holding(client, 591)[0] == 0,
             12, "run identity")
        epoch = holding(client, 558, 2)
        nonce = holding(client, 509)[0]
        set_coil(client, 880, True)
        def three_moving():
            active = rows(client)
            return (all(r[0] and r[4] in (2, 3, 4) for r in active) and
                    holding(client, 644, 2) + holding(client, 659) == [1, 2, 3] and
                    holding(client, 222, 9) == [0] * 9)
        wait(three_moving, 70, "three occupied moving slots")
        for address in (207, 208, 209):
            set_register(client, address, 32000)
        before = {"slots": rows(client), "trailers": holding(client, 222, 9),
                  "lanes": holding(client, 644, 2) + holding(client, 659),
                  "telemetry": holding(client, 620, 26) + holding(client, 670, 12),
                  "inducted": holding(client, 220)[0],
                  "request": holding(client, 574)[0],
                  "plant_fault": holding(client, 591)[0],
                  "run": coils(client, 880)[0]}
        READY.write_text(json.dumps(before))
        print("READY_THREE_MOVING " + json.dumps(before), flush=True)
        wait(lambda: holding(client, 591)[0] == 1, 35, "plant heartbeat fault")
        fault_position = holding(client, 620, 26) + holding(client, 670, 12)
        time.sleep(4)
        after = {"slots": rows(client), "trailers": holding(client, 222, 9),
                 "lanes": holding(client, 644, 2) + holding(client, 659),
                 "telemetry": holding(client, 620, 26) + holding(client, 670, 12),
                 "inducted": holding(client, 220)[0],
                 "request": holding(client, 574)[0],
                 "plant_fault": holding(client, 591)[0],
                 "run": coils(client, 880)[0],
                 "heartbeat": holding(client, 590)[0]}
        assert before["inducted"] == after["inducted"] == 3
        assert before["trailers"] == after["trailers"] == [0] * 9
        assert before["request"] == after["request"]
        assert not after["run"] and after["plant_fault"] == 1
        assert all(row[4] in (1, 2, 3, 4) for row in after["slots"])
        assert after["lanes"] == before["lanes"] == [1, 2, 3]
        assert after["telemetry"][20:22] == [2, 2]
        assert after["telemetry"][36] == 2
        assert [after["telemetry"][6], after["telemetry"][16],
                after["telemetry"][32]] == [fault_position[6],
                    fault_position[16], fault_position[32]]
        xle_output = stop(xle); xle = None
        asx_output = stop(asx); asx = None
        xe, ae = events(xle_output), events(asx_output)
        assert not [e for e in xe if e["event"] == "plc_outcome"]
        package_ids = [f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{r[0]}-{r[1]}"
                       for lane, r in zip(before["lanes"], before["slots"])]
        result = {"before": before, "after": after, "epoch": epoch,
                  "nonce": nonce, "package_ids": package_ids,
                  "xle_events": xe, "asx_events": ae}
        RESULT.write_text(json.dumps(result, sort_keys=True))
        print("PLANT_STOP_PASS " + json.dumps(result, sort_keys=True), flush=True)
    finally:
        set_coil(client, 880, False)
        stop(xle); stop(asx); client.close()


if __name__ == "__main__":
    main()

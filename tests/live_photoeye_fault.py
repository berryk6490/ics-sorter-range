"""Live, one-token photoeye fault probe on SCADA; restores every PLC setting."""
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
from live_xle_multi import holding, coils, set_coil, set_register, stop
from live_plant import wait

ROOT = Path.home() / "sorter-services"


def main(kind, start_file=None, hold_seconds=0):
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7),
                "modes": coils(plc, 914, 2) + coils(plc, 918, 2),
                "setpoints": holding(plc, 200, 11),
                "photoeye_config": holding(plc, 744, 4),
                "seed": holding(plc, 247)[0]}
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="photoeye-fault-")
    try:
        if start_file:
            marker = Path(start_file)
            marker.unlink(missing_ok=True)
            wait(marker.exists, 120, "browser start marker")
        set_coil(plc, 880, False)
        set_register(plc, 247, 137)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        observed_reset = False

        def reset_complete():
            nonlocal observed_reset
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            observed_reset |= tick < old_tick or state in (1, 2)
            return observed_reset and state == 0

        wait(reset_complete, 25, "scanner reset")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"),
                                "--plan", str(ROOT / "lane3_plan.json")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX unavailable")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, lane == 1)
        set_coil(plc, 914, True)
        set_coil(plc, 915, True)
        set_coil(plc, 918, True)
        set_coil(plc, 919, True)
        for addr in range(200, 206):
            set_register(plc, addr, 120)
        for addr in (207, 208, 209):
            set_register(plc, addr, 32000)
        set_register(plc, 746, 40)
        set_register(plc, 747, 220)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", "1", "--deadline", "200",
                                "--journal", str(Path(journal.name) / "outcomes.sqlite3")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: holding(plc, 558, 2) != [0, 0] and
             holding(plc, 591)[0] == 0, 15, "run identity")
        set_register(plc, 207, 14)
        set_coil(plc, 880, True)
        wait(lambda: holding(plc, 530)[0] == 1, 10, "occupied slot")
        set_register(plc, 207, 32000)
        samples = []
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            view = holding(plc, 720, 8)
            sample = {"token": view[3], "raw": view[5],
                      "conditioned": view[6], "quality": view[7]}
            if not samples or samples[-1] != sample:
                samples.append(sample)
            if holding(plc, 748)[0]:
                break
            time.sleep(.1)
        fault = holding(plc, 748, 3)
        row = holding(plc, 530, 12)
        trailer = holding(plc, 222, 9)
        hmi = {}
        def hmi_fault_visible():
            nonlocal hmi
            hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=2))
            return hmi["photoeye_mode"] and hmi["photoeye_fault_mask"] == 1
        wait(hmi_fault_visible, 5, "HMI photoeye fault")
        expected_sensor = 1 if kind == "stuck_blocked" else 2
        expected_quality = 3 if kind == "stuck_blocked" else 5
        assert fault == [1, expected_sensor, 1], (fault, samples)
        assert samples[-1]["quality"] == expected_quality, samples[-1]
        assert not coils(plc, 880)[0]
        assert row[0] == 1 and row[4] in (1, 2, 3, 4), row
        assert trailer == [0] * 9, trailer
        assert hmi["photoeye_fault_sensor"] == expected_sensor
        print(json.dumps({"kind": kind, "fault": fault, "slot": row,
                          "trailer": trailer, "samples": samples,
                          "hmi_fault": [hmi["photoeye_fault_mask"],
                                        hmi["photoeye_fault_sensor"],
                                        hmi["photoeye_fault_lane"]]}, sort_keys=True),
              flush=True)
        if hold_seconds:
            time.sleep(hold_seconds)
    finally:
        errors = []
        try:
            set_coil(plc, 880, False)
            for addr in (914, 915, 918, 919):
                set_coil(plc, addr, False)
            old_tick = holding(plc, 243)[0]
            set_coil(plc, 910, True)
            reset_seen = False
            def cleared():
                nonlocal reset_seen
                tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
                reset_seen |= tick < old_tick or state in (1, 2)
                return reset_seen and state == 0 and holding(plc, 748)[0] == 0
            wait(cleared, 25, "photoeye fault reset")
        except Exception as exc:
            errors.append(f"fault reset: {exc}")
        for fn, address, value in (
            [(set_coil, 914, original["modes"][0]),
             (set_coil, 915, original["modes"][1]),
             (set_coil, 918, original["modes"][2]),
             (set_coil, 919, original["modes"][3]),
             (set_register, 247, original["seed"])] +
            [(set_coil, 881 + i, v) for i, v in enumerate(original["enables"])] +
            [(set_register, 200 + i, v) for i, v in enumerate(original["setpoints"])] +
            [(set_register, 744 + i, v) for i, v in enumerate(original["photoeye_config"])]) :
            try:
                fn(plc, address, value)
            except Exception as exc:
                errors.append(f"restore {address}: {exc}")
        try:
            assert not coils(plc, 880)[0]
            assert holding(plc, 748, 3) == [0, 0, 0]
        except Exception as exc:
            errors.append(f"post-cleanup check: {exc}")
        stop(xle)
        stop(asx)
        journal.cleanup()
        plc.close()
        if errors:
            raise RuntimeError("cleanup failed: " + ", ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("stuck_blocked", "missed"))
    parser.add_argument("--start-file")
    parser.add_argument("--hold-seconds", type=float, default=0)
    args = parser.parse_args()
    if not 0 <= args.hold_seconds <= 30:
        parser.error("hold must be between 0 and 30 seconds")
    main(args.kind, args.start_file, args.hold_seconds)

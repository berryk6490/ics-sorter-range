"""Bounded three-lane plant proof with dedicated XLe/ASX services.

Run on SCADA. This script never starts XLe, ASX, or a second journal writer.
The host verifies the dedicated journal and journald events after this run.
"""

import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

from pymodbus.client import ModbusTcpClient

sys.path.insert(0, str(Path.home()))
from live_xle_multi import holding, coils, set_coil, set_register


def wait(predicate, seconds, label):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.1)
    raise TimeoutError(label)


def main():
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not plc.connect():
        raise ConnectionError("SCADA cannot read PLC")
    original = {"enables": coils(plc, 881, 7),
                "modes": {a: coils(plc, a)[0] for a in (914, 915, 918, 919, 920)},
                "setpoints": holding(plc, 200, 11), "seed": holding(plc, 247)[0]}
    failure = None
    try:
        assert holding(plc, 249)[0] == 24115
        assert not coils(plc, 880)[0]
        assert not any(original["modes"].values()), "test requires modes off"
        assert all(holding(plc, a + 4)[0] == 0 for a in (530, 542, 647))
        assert holding(plc, 591)[0] == 0 and holding(plc, 788)[0] == 0
        assert holding(plc, 748, 3) == [0, 0, 0]
        set_register(plc, 247, 137)
        old_nonce = holding(plc, 509)[0]
        set_coil(plc, 910, True)
        wait(lambda: holding(plc, 509)[0] != old_nonce and
             holding(plc, 255)[0] == 0 and holding(plc, 256)[0] == 0,
             30, "scanner reset ACK")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, True)
        for address in (914, 915, 918, 919, 920):
            set_coil(plc, address, True)
        for address in range(200, 206):
            set_register(plc, address, 120)
        for address in (207, 208, 209):
            set_register(plc, address, 14)
        wait(lambda: holding(plc, 558, 2) != [0, 0] and
             holding(plc, 591)[0] == 0 and holding(plc, 569)[0] == 0 and
             holding(plc, 573)[0] != 0,
             20, "XLe run epoch and heartbeat")
        lo, hi = holding(plc, 558, 2)
        epoch = hi * 30000 + lo
        nonce = holding(plc, 509)[0]
        set_coil(plc, 880, True)
        rows = {}
        events = []
        hmi_lanes = set()
        pre_confirmation = None
        last_event = holding(plc, 585)[0]
        last_hmi = 0
        max_slots = 0
        deadline = time.monotonic() + 190
        while time.monotonic() < deadline:
            inducted = holding(plc, 220)[0]
            if inducted >= 3:
                for address in (207, 208, 209):
                    set_register(plc, address, 32000)
            sensor = holding(plc, 580, 6)
            if sensor[5] and sensor[5] != last_event:
                last_event = sensor[5]
                events.append({"type": sensor[0], "token": sensor[1],
                               "serial": sensor[2], "trailer": sensor[3],
                               "position": sensor[4], "sequence": sensor[5],
                               "lane": holding(plc, 578)[0]})
                if sensor[0] == 3 and pre_confirmation is None:
                    pre_confirmation = holding(plc, 222, 9)
            block = holding(plc, 530, 24) + holding(plc, 647, 12)
            lanes = holding(plc, 644, 2) + holding(plc, 659)
            max_slots = max(max_slots, sum(block[i + 4] in (1, 2, 3, 4)
                                           for i in (0, 12, 24)))
            for slot in range(3):
                row = block[slot * 12:(slot + 1) * 12]
                if row[0] and row[4] in (5, 6, 7):
                    lane = lanes[slot]
                    package_id = f"l{lane}-{epoch}-{nonce}-{row[0]}-{row[1]}"
                    rows[package_id] = {"package_id": package_id,
                                        "lane": lane, "slot": slot,
                                        "token": row[0], "serial": row[1],
                                        "scanner_sequence": row[2],
                                        "barcode": row[3], "state": row[4],
                                        "destination": row[5], "actual": row[6],
                                        "reason": row[7], "scan_tick": row[8],
                                        "accept_tick": row[9], "divert_tick": row[10],
                                        "command_id": row[11]}
            if time.monotonic() - last_hmi >= .5:
                last_hmi = time.monotonic()
                try:
                    with urlopen("http://127.0.0.1:8000/api", timeout=1) as response:
                        hmi = json.load(response)
                    if hmi.get("connected") and hmi.get("plant_mode"):
                        hmi_lanes.update(lane for lane, quality in zip(
                            hmi["plant_lane"], hmi["plant_status"]) if quality == 1)
                except (OSError, KeyError, ValueError):
                    pass
            counters = holding(plc, 222, 9)
            if (len(rows) == 3 and counters == [0, 1, 0, 0, 1, 0, 0, 1, 0] and
                all(block[i + 4] == 0 for i in (0, 12, 24))):
                break
            time.sleep(.05)
        expected = {1: (6001, 2), 2: (5002, 5), 3: (3003, 8)}
        assert len(rows) == 3, rows
        assert {row["lane"] for row in rows.values()} == {1, 2, 3}, rows
        assert max_slots <= 3 and set(hmi_lanes) >= {1, 2, 3}, (max_slots, hmi_lanes)
        assert pre_confirmation == [0] * 9, pre_confirmation
        assert holding(plc, 222, 9) == [0, 1, 0, 0, 1, 0, 0, 1, 0]
        assert holding(plc, 748, 3) == [0, 0, 0]
        for row in rows.values():
            barcode, destination = expected[row["lane"]]
            assert (row["barcode"], row["destination"], row["actual"],
                    row["state"], row["reason"]) == (barcode, destination,
                                                      destination, 5, 0), row
            assert row["scanner_sequence"] and row["scan_tick"] > 0
            assert row["scan_tick"] <= row["accept_tick"] < row["divert_tick"], row
        result = {"status": "PASS", "epoch": epoch, "scanner_nonce": nonce,
                  "rows": rows, "sensor_events": events,
                  "hmi_lanes": sorted(hmi_lanes), "maximum_occupied": max_slots,
                  "pre_confirmation_counters": pre_confirmation,
                  "trailer_counters": holding(plc, 222, 9),
                  "inducted": holding(plc, 220)[0]}
        print(json.dumps(result, sort_keys=True), flush=True)
        return result
    except BaseException as exc:
        failure = exc
        raise
    finally:
        errors = []
        try:
            set_coil(plc, 880, False)
        except Exception as exc:
            errors.append(f"master: {exc}")
        # Disable plant mode before either XLe mode to preserve QW591=0.
        for address in (918, 919, 920, 914, 915):
            try:
                set_coil(plc, address, original["modes"][address])
            except Exception as exc:
                errors.append(f"mode {address}: {exc}")
        for offset, value in enumerate(original["enables"]):
            try:
                set_coil(plc, 881 + offset, value)
            except Exception as exc:
                errors.append(f"enable {881 + offset}: {exc}")
        for offset, value in enumerate(original["setpoints"]):
            try:
                set_register(plc, 200 + offset, value)
            except Exception as exc:
                errors.append(f"setpoint {200 + offset}: {exc}")
        try:
            set_register(plc, 247, original["seed"])
        except Exception as exc:
            errors.append(f"seed: {exc}")
        plc.close()
        if errors:
            print(json.dumps({"cleanup_errors": errors}), flush=True)
            if failure is None:
                raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    main()

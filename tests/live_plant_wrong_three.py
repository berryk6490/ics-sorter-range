"""Inject a wrong-lane event while all three global slots are occupied.

Run on SCADA with the normal drives plant service. Restore operator settings
and clear the deliberately latched fault through the scanner/PLC reset.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from pymodbus.client import ModbusTcpClient
from live_xle_multi import holding, coils, set_coil, set_register, stop
from live_plant import wait

ROOT = Path.home() / "sorter-services"


def main():
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "external": coils(plc, 914, 2),
                "plant": coils(plc, 918)[0], "setpoints": holding(plc, 200, 11),
                "seed": holding(plc, 247)[0]}
    xle = None
    journal = tempfile.TemporaryDirectory(prefix="sorter-wrong-lane-")
    try:
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
        set_coil(plc, 882, True); set_coil(plc, 883, True)
        set_coil(plc, 884, True)
        set_coil(plc, 914, True); set_coil(plc, 915, True)
        set_coil(plc, 918, True)
        for address in (200, 201, 202, 203, 204, 205):
            set_register(plc, address, 60)
        for address in (207, 208, 209):
            set_register(plc, address, 14)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"),
                                "--multi", "--packages", "0", "--deadline", "50",
                                "--journal", str(Path(journal.name) / "outcomes.sqlite3")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        wait(lambda: holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0,
             12, "run identity")
        epoch = holding(plc, 558, 2)
        nonce = holding(plc, 509)[0]
        set_coil(plc, 880, True)
        def inducted():
            rows = [holding(plc, 530, 12), holding(plc, 542, 12),
                    holding(plc, 647, 12)]
            return (holding(plc, 220)[0] == 3 and
                    [r[0] for r in rows] == [1, 2, 3] and
                    [r[4] for r in rows] == [1, 1, 1] and
                    holding(plc, 644, 2) + holding(plc, 659) == [1, 2, 3])
        wait(inducted, 25, "three occupied slots")
        for address in (207, 208, 209):
            set_register(plc, address, 32000)
        row = holding(plc, 647, 12)
        assert holding(plc, 222, 9) == [0] * 9
        wait(lambda: holding(plc, 585)[0] == holding(plc, 586)[0],
             3, "prior plant event acknowledgement")
        sequence = holding(plc, 585)[0] % 30000 + 1
        # Payload first, sequence last; the wrong lane is deliberate.
        assert not plc.write_register(578, 2, slave=1).isError()
        assert not plc.write_registers(580, [2, row[0], row[1], 0, 15], slave=1).isError()
        assert not plc.write_register(585, sequence, slave=1).isError()
        wait(lambda: holding(plc, 591)[0] == 3, 4, "wrong-lane fault")
        result = {"package_id": f"l3-{epoch[0]+epoch[1]*30000}-{nonce}-{row[0]}-{row[1]}",
                  "rows": [holding(plc, 530, 12), holding(plc, 542, 12),
                           holding(plc, 647, 12)],
                  "lane": holding(plc, 659)[0],
                  "event_lane": holding(plc, 578)[0], "event_ack": holding(plc, 586)[0],
                  "plant_fault": holding(plc, 591)[0], "run": coils(plc, 880)[0],
                  "inducted": holding(plc, 220)[0],
                  "trailer": holding(plc, 222, 9)}
        assert result["lane"] == 3 and result["event_lane"] == 2
        assert result["event_ack"] == sequence and result["plant_fault"] == 3
        assert not result["run"] and result["inducted"] == 3
        assert result["trailer"] == [0] * 9 and [r[4] for r in result["rows"]] == [1, 1, 1]
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        set_coil(plc, 880, False)
        set_coil(plc, 918, False)
        stop(xle)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen = False
        def reset_done():
            nonlocal seen
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen |= tick < old_tick or state in (1, 2)
            return seen and state == 0
        wait(reset_done, 25, "fault cleanup reset")
        set_coil(plc, 914, original["external"][0])
        set_coil(plc, 915, original["external"][1])
        set_coil(plc, 918, original["plant"])
        set_register(plc, 247, original["seed"])
        for i, value in enumerate(original["enables"]):
            set_coil(plc, 881 + i, value)
        for i, value in enumerate(original["setpoints"]):
            set_register(plc, 200 + i, value)
        assert not coils(plc, 880)[0] and holding(plc, 591)[0] != 3
        journal.cleanup(); plc.close()


if __name__ == "__main__":
    main()

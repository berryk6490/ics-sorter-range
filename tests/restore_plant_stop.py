"""Restore the operator settings saved by live_plant_stop.py on SCADA."""
import json
from pathlib import Path
import time
from pymodbus.client import ModbusTcpClient
from live_xle_multi import coils, holding, set_coil, set_register


def main():
    settings = json.loads(Path("/tmp/plant-stop-original.json").read_text())
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    assert plc.connect()
    try:
        set_coil(plc, 880, False)
        # Clear the injected failed-confirmation alarm through the normal
        # scanner/PLC reset before restoring the operator's mode selections.
        set_coil(plc, 918, False)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen = False
        until = time.monotonic() + 25
        while time.monotonic() < until:
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen |= tick < old_tick or state in (1, 2)
            if seen and state == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("scanner reset did not complete during restoration")
        set_coil(plc, 914, settings["external"][0])
        set_coil(plc, 915, settings["external"][1])
        set_coil(plc, 918, settings["plant"])
        set_register(plc, 247, settings["seed"])
        for i, value in enumerate(settings["enables"]):
            set_coil(plc, 881 + i, value)
        for i, value in enumerate(settings["setpoints"]):
            set_register(plc, 200 + i, value)
        assert not coils(plc, 880)[0]
        assert coils(plc, 918)[0] == settings["plant"]
        assert coils(plc, 881, 7) == settings["enables"]
        assert holding(plc, 200, 11) == settings["setpoints"]
        assert holding(plc, 592)[0] == 0
        print("reset alarm; restored stopped sorter, external/plant modes, seven enables, seed, eleven setpoints")
    finally:
        plc.close()


if __name__ == "__main__":
    main()

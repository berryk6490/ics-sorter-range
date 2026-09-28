"""Run on the PLC guest with XLe already listening on SCADA.

Always stops the sorter and restores initial operator enables, setpoints,
seed, and the external-route enable after the run.
"""
import json
import time
from pymodbus.client import ModbusTcpClient


def read(c, address, count=1):
    r = c.read_holding_registers(address, count, slave=1)
    if r.isError():
        raise RuntimeError(r)
    return r.registers


def coil(c, address, value):
    r = c.write_coil(address, value, slave=1)
    if r.isError():
        raise RuntimeError(r)


def register(c, address, value):
    r = c.write_register(address, value, slave=1)
    if r.isError():
        raise RuntimeError(r)


def main():
    c = ModbusTcpClient("127.0.0.1", port=502, timeout=2)
    assert c.connect()
    enables = tuple(c.read_coils(881, 7, slave=1).bits[:7])
    external = bool(c.read_coils(914, 1, slave=1).bits[0])
    setpoints = read(c, 200, 11)
    seed = read(c, 247)[0]
    try:
        coil(c, 880, False)
        register(c, 247, 137)
        before_reset = read(c, 243)[0]
        coil(c, 910, True)
        deadline = time.monotonic() + 20
        reset_seen = False
        while time.monotonic() < deadline:
            if read(c, 243)[0] < before_reset or read(c, 255)[0] in (1, 2):
                reset_seen = True
            if reset_seen and read(c, 255)[0] == 0:
                break
            time.sleep(.1)
        assert reset_seen and read(c, 255)[0] == 0, "scanner reset incomplete"
        coil(c, 883, False)
        coil(c, 884, False)
        coil(c, 914, True)
        assert c.read_coils(914, 1, slave=1).bits[0], "XLe mode did not latch"
        coil(c, 880, True)
        deadline = time.monotonic() + 90
        saw_scan = False
        while time.monotonic() < deadline:
            if read(c, 220)[0] >= 1:
                register(c, 207, 1000)
            package = read(c, 508)[0]
            if package:
                saw_scan = True
            ack, state, reason, actual, _, nonce = read(c, 504, 6)
            if state in (3, 4, 5):
                result = {"package": package, "command_id": ack,
                          "state": state, "reason": reason,
                          "actual_trailer": actual,
                          "trailer_counts": read(c, 222, 9),
                          "inducted": read(c, 220)[0],
                          "recirc": read(c, 218)[0],
                          "scan_seen": saw_scan,
                          "run_nonce": nonce}
                print(json.dumps(result, sort_keys=True), flush=True)
                assert (state == 3 and actual == 2 and result["trailer_counts"][1] == 1
                        and result["inducted"] == 1)
                return
            time.sleep(.1)
        raise TimeoutError("live package did not reach a terminal PLC outcome")
    finally:
        errors = []
        try:
            coil(c, 880, False)
            time.sleep(.3)  # wait for a PLC scan before disabling one-shot mode
            assert not c.read_coils(880, 1, slave=1).bits[0]
        except Exception as exc:
            errors.append(f"stop: {exc}")
        actions = [(coil, 914, external), (register, 247, seed)]
        actions += [(coil, 881 + i, v) for i, v in enumerate(enables)]
        actions += [(register, 200 + i, v) for i, v in enumerate(setpoints)]
        for fn, address, value in actions:
            try:
                fn(c, address, value)
            except Exception as exc:
                errors.append(f"{address}: {exc}")
        c.close()
        if errors:
            raise RuntimeError("operator restoration failed: " + ", ".join(errors))


if __name__ == "__main__":
    main()

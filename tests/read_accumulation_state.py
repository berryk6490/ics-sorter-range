"""Read-only bounded PLC snapshot for Phase 2A live evidence."""
import json
from pymodbus.client import ModbusTcpClient


def main():
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise RuntimeError("PLC unavailable")
    try:
        def regs(start, count):
            reply = client.read_holding_registers(start, count, slave=1)
            if reply.isError():
                raise RuntimeError(f"holding {start}..{start + count - 1}")
            return list(reply.registers)

        def coils(start, count):
            reply = client.read_coils(start, count, slave=1)
            if reply.isError():
                raise RuntimeError(f"coils {start}..{start + count - 1}")
            return list(reply.bits[:count])

        print(json.dumps({"identity": regs(249, 1)[0],
                          "coils_880_920": coils(880, 41),
                          "setpoints_200_210": regs(200, 11),
                          "seed": regs(247, 1)[0],
                          "slots": [regs(530, 12), regs(542, 12), regs(647, 12)],
                          "lanes": regs(644, 2) + regs(659, 1),
                          "plant_faults": regs(591, 3),
                          "photoeye_faults": regs(748, 3),
                          "scanner_state": regs(255, 1)[0],
                          "scanner_fault_mask": regs(256, 1)[0],
                          "zone_view": regs(766, 23),
                          "zone_raw": regs(751, 15) + regs(784, 2),
                          "plant_raw": [regs(600, 10), regs(610, 10), regs(660, 10)],
                          "trailer_counters": regs(222, 9)}, sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    main()

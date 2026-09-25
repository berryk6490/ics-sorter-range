"""Read-only bounded PLC snapshot for Phase 2A live evidence."""
import argparse
import json
try:
    from pymodbus.client import ModbusTcpClient
except ImportError:  # Host-only contract tests inject fake clients; guest has pymodbus.
    ModbusTcpClient = None

VFD_HOSTS = {f"induct{lane}": f"10.10.1.{20 + lane}" for lane in range(1, 4)}
VFD_HOSTS.update({f"outbnd{lane}": f"10.10.1.{23 + lane}" for lane in range(1, 4)})


def read_state(client, drive_factory=ModbusTcpClient, include_vfds=False):
    def regs(start, count):
        reply = client.read_holding_registers(start, count, slave=1)
        if reply.isError() or len(reply.registers) != count:
            raise RuntimeError(f"holding {start}..{start + count - 1}")
        return list(reply.registers)

    def coils(start, count):
        reply = client.read_coils(start, count, slave=1)
        if reply.isError() or len(reply.bits) < count:
            raise RuntimeError(f"coils {start}..{start + count - 1}")
        return list(reply.bits[:count])

    drives = {}
    for name, host in VFD_HOSTS.items() if include_vfds else ():
        drive = drive_factory(host, port=502, timeout=2)
        if not drive.connect():
            raise RuntimeError(f"VFD unavailable: {name}")
        try:
            response = drive.read_holding_registers(0, 9, slave=1)
            if response.isError() or len(response.registers) != 9:
                raise RuntimeError(f"VFD read failed: {name}")
            values = list(response.registers)
            drives[name] = {"host": host, "command": values[0], "setpoint": values[1],
                            "belt_load": values[2], "status": values[3],
                            "frequency": values[4], "feedback_rpm": values[5],
                            "fault": values[6], "current": values[7],
                            "thermal": values[8]}
        finally:
            drive.close()

    return {"reader_schema": 2, "identity": regs(249, 1)[0],
            "coils_880_920": coils(880, 41),
            "setpoints_200_210": regs(200, 11),
            "photoeye_config_744_747": regs(744, 4),
            "seed": regs(247, 1)[0],
            "slots": [regs(530, 12), regs(542, 12), regs(647, 12)],
            "lanes": regs(644, 2) + regs(659, 1),
            "process_214_242": regs(214, 29),
            "scanner_counters_250_254": regs(250, 5),
            "run_identity": {"run_id": regs(248, 1)[0],
                             "epoch": regs(558, 2), "epoch_fault": regs(561, 1)[0],
                             "scanner_nonce": regs(509, 1)[0],
                             "plant_epoch_nonce": regs(587, 3)},
            "plant_faults": regs(591, 3),
            "photoeye_faults": regs(748, 3),
            "scanner_state": regs(255, 1)[0],
            "scanner_fault_mask": regs(256, 1)[0],
            "xle_health": regs(568, 2),
            "zone_view": regs(766, 23),
            "zone_raw": regs(751, 15) + regs(784, 2),
            "plant_raw": [regs(600, 10), regs(610, 10), regs(660, 10)],
            "trailer_counters": regs(222, 9), "vfds": drives}


def main():
    if ModbusTcpClient is None:
        raise RuntimeError("pymodbus is required on the guest")
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-vfds", action="store_true")
    args = parser.parse_args()
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise RuntimeError("PLC unavailable")
    try:
        print(json.dumps(read_state(client, include_vfds=args.with_vfds), sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    main()

"""Run on the PLC guest to verify the deployed program against live Modbus devices.

Requires pymodbus 2.5.x and the drives guest on the isolated ics-l1 network.
The script leaves the sorter stopped, with its counters available to inspect.
"""

import json
import time

from pymodbus.client.sync import ModbusTcpClient


def registers(client, address, count=1, input_registers=False):
    read = client.read_input_registers if input_registers else client.read_holding_registers
    reply = read(address, count, unit=1)
    if reply.isError():
        raise RuntimeError(f"Modbus read {address}: {reply}")
    return reply.registers


def set_coil(client, address, value):
    reply = client.write_coil(address, value, unit=1)
    if reply.isError():
        raise RuntimeError(f"Modbus coil {address}: {reply}")


def set_register(client, address, value):
    reply = client.write_register(address, value, unit=1)
    if reply.isError():
        raise RuntimeError(f"Modbus register {address}: {reply}")


def coils(client, address, count):
    reply = client.read_coils(address, count, unit=1)
    if reply.isError():
        raise RuntimeError(f"Modbus coils {address}: {reply}")
    return tuple(bool(value) for value in reply.bits[:count])


def run(plc, drive, camera):
    original_options = None
    original_setpoints = None
    original_seed = None
    observed = {"drive_feedback": False, "inducted": False, "belt_cell": False,
                "sensor_trigger": False, "camera_result": False, "barcode": False,
                "trailer_load": False}
    details = {}
    first_barcode = None
    try:
        if not all(client.connect() for client in (plc, drive, camera)):
            raise RuntimeError("PLC, induct drive 1, and camera 1 must answer on port 502")
        original_options = coils(plc, 881, 7)  # auto, induct and outbound enables
        original_setpoints = registers(plc, 200, 11)
        original_seed = registers(plc, 247)[0]
        if registers(plc, 249)[0] != 24111:
            raise RuntimeError("The running PLC program is not the expected sorter generation")

        set_coil(plc, 880, False)  # %QX110.0: operator run
        set_register(plc, 247, 137)
        set_coil(plc, 910, True)   # %QX113.6: reset process state
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (registers(plc, 118)[0] == 0 and
                    all(registers(plc, address, input_registers=True)[0] == 32767
                        for address in (158, 169, 180)) and
                    all(registers(plc, address, input_registers=True)[0] == 6
                        for address in (160, 171, 182))):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Three scanner reset acknowledgements did not arrive")
        set_coil(plc, 883, False)  # disable lane 2 for this one-lane check
        set_coil(plc, 884, False)  # disable lane 3
        set_coil(plc, 880, True)

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            drive_feedback = registers(plc, 105, input_registers=True)[0]
            drive_registers = registers(drive, 0, 6)
            inducted = registers(plc, 220)[0]
            trigger, serial = registers(plc, 118, 2)
            result_seq, barcode, status, _, _, _, response_nonce = registers(camera, 4, 7)
            scan_code = registers(plc, 211)[0]
            belt = registers(plc, 260, 20)
            trailers = registers(plc, 222, 9)

            observed["drive_feedback"] |= (drive_feedback > 0 and drive_registers[5] > 0
                                           and bool(drive_registers[0] & 1))
            observed["inducted"] |= inducted > 0
            observed["belt_cell"] |= 1 in belt or any(cell > 0 and cell % 1000 == 1 for cell in belt)
            observed["sensor_trigger"] |= trigger == 1 and serial == 1
            if (result_seq == 1 and response_nonce > 0 and
                    status in (0, 3, 4) and barcode % 1000 == 1):
                observed["camera_result"] = True
                first_barcode = barcode
            if first_barcode is not None:
                observed["barcode"] |= scan_code == first_barcode
                observed["trailer_load"] |= trailers[first_barcode // 1000 - 1] > 0
            details = {"inducted": inducted, "trigger": trigger, "serial": serial,
                       "result_seq": result_seq, "first_barcode": first_barcode,
                       "barcode": barcode, "status": status,
                       "scan_code": scan_code, "trailers": trailers,
                       "drive_feedback": drive_feedback,
                       "trailer": next((index + 1 for index, count in enumerate(trailers)
                                        if first_barcode and index + 1 == first_barcode // 1000
                                        and count > 0), None),
                       "response_nonce": response_nonce}
            if all(observed.values()):
                break
            time.sleep(0.2)
    finally:
        cleanup_errors = []
        cleanup = [("coil", 880, False)]
        if original_seed is not None:
            cleanup.append(("register", 247, original_seed))
        if original_options is not None:
            cleanup.extend(("coil", 881 + index, value)
                           for index, value in enumerate(original_options))
        if original_setpoints is not None:
            cleanup.extend(("register", 200 + index, value)
                           for index, value in enumerate(original_setpoints))
        for kind, address, value in cleanup:
            try:
                if kind == "coil":
                    set_coil(plc, address, value)
                else:
                    set_register(plc, address, value)
            except Exception as exc:
                cleanup_errors.append(f"{kind} {address}: {exc}")
        for client in (plc, drive, camera):
            client.close()
        if cleanup_errors:
            raise RuntimeError("Cleanup failed: " + "; ".join(cleanup_errors))
    return observed, details


def main():
    runs = []
    for _ in range(2):
        plc = ModbusTcpClient("127.0.0.1", port=502, timeout=2)
        drive = ModbusTcpClient("10.10.1.21", port=502, timeout=2)
        camera = ModbusTcpClient("10.10.1.27", port=502, timeout=2)
        observed, details = run(plc, drive, camera)
        runs.append({"observed": observed, "last": details})
        print(json.dumps(runs[-1], sort_keys=True))
        if not all(observed.values()):
            raise SystemExit("first package did not complete the live path")
    first, second = (item["last"] for item in runs)
    if (first["first_barcode"], first["trailer"]) != (second["first_barcode"], second["trailer"]):
        raise SystemExit("same-seed runs differed: " + json.dumps(runs, sort_keys=True))
    print("same-seed replay: first barcode and trailer match")


if __name__ == "__main__":
    main()

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


plc = ModbusTcpClient("127.0.0.1", port=502, timeout=2)
drive = ModbusTcpClient("10.10.1.21", port=502, timeout=2)
camera = ModbusTcpClient("10.10.1.27", port=502, timeout=2)
if not all(client.connect() for client in (plc, drive, camera)):
    raise SystemExit("PLC, induct drive 1, and camera 1 must all answer on port 502")

observed = {"drive_feedback": False, "inducted": False, "belt_cell": False,
            "sensor_trigger": False, "camera_result": False, "barcode": False,
            "trailer_load": False}
details = {}
first_barcode = None
try:
    if registers(plc, 249)[0] != 18436:
        raise RuntimeError("The running PLC program is not the expected sorter generation")

    set_coil(plc, 880, False)  # %QX110.0: operator run
    set_coil(plc, 910, True)   # %QX113.6: reset process state
    time.sleep(0.3)
    set_coil(plc, 883, False)  # disable lane 2 for this one-lane check
    set_coil(plc, 884, False)  # disable lane 3
    set_coil(plc, 880, True)

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        drive_feedback = registers(plc, 105, input_registers=True)[0]
        drive_registers = registers(drive, 0, 6)
        inducted = registers(plc, 220)[0]
        trigger, serial = registers(plc, 118, 2)
        result_seq, barcode, status = registers(camera, 4, 3)
        scan_code = registers(plc, 211)[0]
        belt = registers(plc, 260, 20)
        trailers = registers(plc, 222, 9)

        observed["drive_feedback"] |= (drive_feedback > 0 and drive_registers[5] > 0
                                       and bool(drive_registers[0] & 1))
        observed["inducted"] |= inducted > 0
        observed["belt_cell"] |= 1 in belt or any(cell > 0 and cell % 1000 == 1 for cell in belt)
        observed["sensor_trigger"] |= trigger == 1 and serial == 1
        if result_seq == 1 and status in (0, 3, 4) and barcode % 1000 == 1:
            observed["camera_result"] = True
            first_barcode = barcode
        if first_barcode is not None:
            observed["barcode"] |= scan_code == first_barcode
            observed["trailer_load"] |= trailers[first_barcode // 1000 - 1] > 0
        details = {"inducted": inducted, "trigger": trigger, "serial": serial,
                   "result_seq": result_seq, "first_barcode": first_barcode,
                   "barcode": barcode, "status": status,
                   "scan_code": scan_code, "trailers": trailers,
                   "drive_feedback": drive_feedback}
        if all(observed.values()):
            break
        time.sleep(0.2)
finally:
    set_coil(plc, 880, False)
    set_coil(plc, 883, True)
    set_coil(plc, 884, True)
    plc.close()
    drive.close()
    camera.close()

print(json.dumps({"observed": observed, "last": details}, sort_keys=True))
if not all(observed.values()):
    raise SystemExit("first package did not complete the live drive-to-scanner-to-trailer path")

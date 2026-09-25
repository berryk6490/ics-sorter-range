"""Read-only agreement probe: PLC validated zone -> OPC UA -> HMI API."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.request import urlopen

from asyncua import Client
from pymodbus.client import ModbusTcpClient


async def read_opc():
    async with Client("opc.tcp://10.10.2.10:4840/sorter/", timeout=5) as client:
        index = await client.get_namespace_index("urn:sorter:level2")
        root = await client.nodes.objects.get_child([f"{index}:Sorter"])
        process = await root.get_child([f"{index}:Process"])
        plant = await process.get_child([f"{index}:Plant"])
        status = await process.get_child([f"{index}:Status"])
        rows = []
        for number in (1, 2, 3):
            slot = await plant.get_child([f"{index}:Slot{number}"])
            rows.append(list(await (await slot.get_child(
                [f"{index}:ValidatedZone"])).read_value()))
        fields = {}
        for name in ("AccumulationMode", "ZoneFault", "ZoneInductReadyMask",
                     "ZoneAgeScans"):
            fields[name] = await (await status.get_child([f"{index}:{name}"])).read_value()
        return {"rows": rows, **fields}


def plc_view():
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise RuntimeError("PLC unavailable")
    try:
        result = client.read_holding_registers(766, 23, slave=1)
        mode = client.read_coils(920, 1, slave=1)
        if result.isError() or mode.isError():
            raise RuntimeError("PLC view read failed")
        values = result.registers
        return {"rows": [values[i * 5:(i + 1) * 5] + [values[15 + i]]
                         for i in range(3)], "mode": bool(mode.bits[0]),
                "ready": values[20], "age": values[21], "fault": values[22]}
    finally:
        client.close()


async def probe(motion, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            modbus = plc_view()
        except (OSError, RuntimeError):
            await asyncio.sleep(.25)
            continue
        matching = [i for i, row in enumerate(modbus["rows"])
                    if row[1] == motion and row[5] == 1]
        if modbus["mode"] and matching:
            slot = matching[0]
            for _ in range(12):
                try:
                    opc = await read_opc()
                    hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=2))
                except (OSError, RuntimeError):
                    await asyncio.sleep(.1)
                    continue
                rows = (modbus["rows"][slot], opc["rows"][slot],
                        hmi["zone_rows"][slot])
                if (all(row[:3] == rows[0][:3] and row[5] == 1 for row in rows) and
                    max(row[3] for row in rows) - min(row[3] for row in rows) <= 15 and
                    opc["AccumulationMode"] == hmi["accumulation_mode"] == modbus["mode"] and
                    opc["ZoneFault"] == hmi["zone_fault"] == modbus["fault"] and
                    opc["ZoneInductReadyMask"] == hmi["zone_ready_mask"] == modbus["ready"]):
                    return {"slot": slot, "modbus": modbus, "opc": opc,
                            "hmi": {"zone_rows": hmi["zone_rows"],
                                    "accumulation_mode": hmi["accumulation_mode"],
                                    "zone_fault": hmi["zone_fault"],
                                    "zone_ready_mask": hmi["zone_ready_mask"]}}
                await asyncio.sleep(.1)
            raise AssertionError("PLC, OPC UA and HMI did not agree on a live zone row")
        await asyncio.sleep(.25)
    raise TimeoutError(f"validated motion {motion} not observed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motion", type=int, choices=(2, 3, 4), required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.motion, args.timeout))
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True), flush=True)

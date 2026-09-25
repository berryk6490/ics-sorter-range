"""Read-only agreement probe: PLC validated zone -> OPC UA -> HMI API."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.request import urlopen

try:
    from asyncua import Client
except ImportError:  # Host contract tests inject or mock guest dependencies.
    Client = None
try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    ModbusTcpClient = None


class InconsistentSnapshot(RuntimeError):
    """Plant changed its committed row while this PLC read was in progress."""


async def read_opc():
    async with Client("opc.tcp://10.10.2.10:4840/sorter/", timeout=5) as client:
        index = await client.get_namespace_index("urn:sorter:level2")
        root = await client.nodes.objects.get_child([f"{index}:Sorter"])
        process = await root.get_child([f"{index}:Process"])
        plant = await process.get_child([f"{index}:Plant"])
        status = await process.get_child([f"{index}:Status"])
        rows, telemetry, lanes, statuses = [], [], [], []
        for number in (1, 2, 3):
            slot = await plant.get_child([f"{index}:Slot{number}"])
            rows.append(list(await (await slot.get_child(
                [f"{index}:ValidatedZone"])).read_value()))
            telemetry.append(list(await (await slot.get_child(
                [f"{index}:Telemetry"])).read_value()))
            lanes.append(await (await slot.get_child([f"{index}:Lane"])).read_value())
            statuses.append(await (await slot.get_child([f"{index}:Status"])).read_value())
        fields = {}
        for name in ("AccumulationMode", "ZoneFault", "ZoneInductReadyMask",
                     "ZoneAgeScans"):
            fields[name] = await (await status.get_child([f"{index}:{name}"])).read_value()
        return {"rows": rows, "telemetry": telemetry, "lanes": lanes,
                "statuses": statuses, **fields}


def plc_view_once(client):
    def regs(address, count):
        reply = client.read_holding_registers(address, count, slave=1)
        if reply.isError() or len(reply.registers) != count:
            raise RuntimeError(f"PLC view read failed at {address}")
        return list(reply.registers)

    first = regs(785, 1)[0]
    if first == 0:
        raise InconsistentSnapshot("plant commit in progress")
    values = regs(766, 23)
    slots = [regs(address, 12) for address in (530, 542, 647)]
    lanes = regs(644, 2) + regs(659, 1)
    epoch, nonce = regs(558, 2), regs(509, 1)[0]
    plant = [regs(address, 10) for address in (620, 630, 670)]
    status = regs(640, 2) + regs(680, 1)
    counters = regs(222, 9)
    faults = {"plant": regs(591, 1)[0], "zone": values[22],
              "photoeyes": regs(748, 3)}
    mode = client.read_coils(920, 1, slave=1)
    master = client.read_coils(880, 1, slave=1)
    if mode.isError() or master.isError():
        raise RuntimeError("PLC coil read failed")
    last = regs(785, 1)[0]
    if first != last or not last:
        raise InconsistentSnapshot(f"plant commit changed {first} -> {last}")
    identities = []
    for lane, row in zip(lanes, slots):
        identities.append((f"l{lane}-{epoch[0]+epoch[1]*30000}-{nonce}-{row[0]}-{row[1]}"
                           if row[0] else None))
    return {"rows": [values[i * 5:(i + 1) * 5] + [values[15 + i]]
                     for i in range(3)], "mode": bool(mode.bits[0]),
            "master": bool(master.bits[0]), "ready": values[20],
            "age": values[21], "fault": values[22], "faults": faults,
            "counters": counters, "slots": slots, "lanes": lanes,
            "identities": identities, "plant_rows": plant,
            "plant_status": status, "epoch": epoch, "nonce": nonce,
            "commit_sequence": last}


def plc_view(attempts=8):
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise RuntimeError("PLC unavailable")
    try:
        for _ in range(attempts):
            try:
                return plc_view_once(client)
            except InconsistentSnapshot:
                time.sleep(.025)
        raise InconsistentSnapshot(f"no consistent PLC commit in {attempts} reads")
    finally:
        client.close()


async def probe(motion, timeout):
    deadline = time.monotonic() + timeout
    inconsistent = 0
    while time.monotonic() < deadline:
        try:
            modbus = plc_view()
        except InconsistentSnapshot:
            inconsistent += 1
            await asyncio.sleep(.1)
            continue
        except (OSError, RuntimeError):
            await asyncio.sleep(.25)
            continue
        matching = [i for i, row in enumerate(modbus["rows"])
                    if row[1] == motion and row[5] == 1]
        if (modbus["mode"] and matching and modbus["master"] and
            modbus["faults"] == {"plant": 0, "zone": 0, "photoeyes": [0, 0, 0]} and
            not any(modbus["counters"])):
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
                identity = modbus["identities"][slot]
                plant = hmi["plant_rows"][slot]
                hmi_id = (f"l{hmi['plant_lane'][slot]}-"
                          f"{plant[0]+plant[1]*30000}-{plant[2]}-{plant[3]}-{plant[4]}")
                opc_plant = opc["telemetry"][slot]
                opc_id = (f"l{opc['lanes'][slot]}-"
                          f"{opc_plant[0]+opc_plant[1]*30000}-{opc_plant[2]}-"
                          f"{opc_plant[3]}-{opc_plant[4]}")
                all_identities_match = True
                for index, package_id in enumerate(modbus["identities"]):
                    if not package_id or modbus["slots"][index][4] == 0:
                        continue
                    h = hmi["plant_rows"][index]
                    o = opc["telemetry"][index]
                    h_id = f"l{hmi['plant_lane'][index]}-{h[0]+h[1]*30000}-{h[2]}-{h[3]}-{h[4]}"
                    o_id = f"l{opc['lanes'][index]}-{o[0]+o[1]*30000}-{o[2]}-{o[3]}-{o[4]}"
                    if package_id != h_id or package_id != o_id:
                        all_identities_match = False
                        break
                if (all(row[:3] == rows[0][:3] and row[5] == 1 for row in rows) and
                    max(row[3] for row in rows) - min(row[3] for row in rows) <= 15 and
                    identity == hmi_id == opc_id and all_identities_match and
                    hmi["plant_status"][slot] == opc["statuses"][slot] == 1 and
                    opc["AccumulationMode"] == hmi["accumulation_mode"] == modbus["mode"] and
                    opc["ZoneFault"] == hmi["zone_fault"] == modbus["fault"] and
                    opc["ZoneInductReadyMask"] == hmi["zone_ready_mask"] == modbus["ready"]):
                    return {"slot": slot, "identity": identity,
                            "inconsistent_read_retries": inconsistent,
                            "modbus": modbus, "opc": opc,
                            "hmi": {"zone_rows": hmi["zone_rows"],
                                    "plant_rows": hmi["plant_rows"],
                                    "plant_lane": hmi["plant_lane"],
                                    "plant_status": hmi["plant_status"],
                                    "accumulation_mode": hmi["accumulation_mode"],
                                    "zone_fault": hmi["zone_fault"],
                                    "zone_ready_mask": hmi["zone_ready_mask"]}}
                await asyncio.sleep(.1)
            raise AssertionError("PLC, OPC UA and HMI did not agree on a live zone row")
        await asyncio.sleep(.25)
    raise TimeoutError(f"validated motion {motion} not observed; inconsistent_reads={inconsistent}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motion", type=int, choices=(2, 3, 4), required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.motion, args.timeout))
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True), flush=True)

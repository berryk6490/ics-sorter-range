"""Direct read-only OPC UA snapshot for the selected trailer-2 chute."""
import asyncio
import base64
import json
import time

from asyncua import Client


async def read():
    async with Client("opc.tcp://10.10.2.10:4840/sorter/", timeout=5) as client:
        index = await client.get_namespace_index("urn:sorter:level2")
        root = await client.nodes.objects.get_child(
            [f"{index}:Sorter", f"{index}:Process", f"{index}:Status"])
        names = ("ChuteMode", "MeasuredChuteTrailer", "MeasuredChuteCapacity",
                 "ChuteState", "ChuteQuality", "ChuteAgeScans",
                 "ChuteOccupied", "ChutePermissive", "ChuteActionAck",
                 "ChuteActionResult", "ChuteEpochLow", "ChuteEpochHigh",
                 "ChuteScannerNonce", "PlantFault", "ZoneFault")
        values = {name: await (await root.get_child([f"{index}:{name}"])).read_value()
                  for name in names}
        trailer = await client.nodes.objects.get_child([
            f"{index}:Sorter", f"{index}:Trailers", f"{index}:Trailer_1_2",
            f"{index}:LoadedCount"])
        values["Trailer2LoadedCount"] = await trailer.read_value()
        values["wall_ns"] = time.time_ns()
        return values


if __name__ == "__main__":
    payload = json.dumps(asyncio.run(read()), sort_keys=True).encode()
    print("__UA__" + base64.b64encode(payload).decode() + "__END__", flush=True)

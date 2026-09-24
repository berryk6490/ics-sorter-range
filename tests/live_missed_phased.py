"""SCADA half of the bounded, token-1 missed-tunnel evidence run.

The host owns the plant fixture. This process never resets between fault.json
and plant_only.json; the host writes marker files after restoring the plant.
"""
import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from asyncua import Client
from pymodbus.client import ModbusTcpClient

from live_xle_multi import coils, events, holding, set_coil, set_register, stop
from live_plant import wait

ROOT = Path.home() / "sorter-services"
PLC = "10.10.1.10"
FAULT = [1, 2, 1]


def save(directory, name, value):
    path = directory / name
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def marker(directory, name, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (directory / "abort").exists():
            raise RuntimeError("host aborted phased run")
        if (directory / name).exists():
            return
        time.sleep(.1)
    raise TimeoutError(f"waiting for host marker {name}")


def snapshot(plc, with_drives=True):
    drives = {}
    for name, addr in ((("induct1", "10.10.1.21"), ("outbnd1", "10.10.1.24"))
                       if with_drives else ()):
        client = ModbusTcpClient(addr, port=502, timeout=2)
        try:
            assert client.connect(), name
            v = holding(client, 0, 9)
            drives[name] = {"command": v[0], "reference": v[1],
                            "status": v[3], "speed_feedback": v[5],
                            "fault": v[6]}
        finally:
            client.close()
    return {"wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
            "tick": holding(plc, 243)[0], "master": coils(plc, 880)[0],
            "slot": holding(plc, 530, 12), "slots": holding(plc, 530, 24) + holding(plc, 647, 12),
            "raw_row": holding(plc, 690, 8), "validated_row": holding(plc, 720, 8),
            "event": holding(plc, 580, 7), "plant_view": holding(plc, 620, 10),
            "plant_status": holding(plc, 640, 1)[0],
            "photoeye_fault": holding(plc, 748, 3),
            "plant_fault": holding(plc, 591)[0],
            "scanner_fault": holding(plc, 255, 4),
            "inducted": holding(plc, 220)[0],
            "trailers": holding(plc, 222, 9), "drives": drives}


async def opc_read():
    async with Client("opc.tcp://10.10.2.10:4840/sorter/", timeout=5) as ua:
        idx = await ua.get_namespace_index("urn:sorter:level2")
        root = await ua.nodes.objects.get_child([f"{idx}:Sorter", f"{idx}:Process"])
        status = await root.get_child([f"{idx}:Status"])
        plant = await root.get_child([f"{idx}:Plant", f"{idx}:Slot1"])
        values = {}
        for name in ("PhotoeyeMode", "PhotoeyeFaultMask", "PhotoeyeFaultSensor",
                     "PhotoeyeFaultLane", "PlantFault"):
            values[name] = await (await status.get_child([f"{idx}:{name}"])).read_value()
        values["Photoeyes"] = await (await plant.get_child([f"{idx}:Photoeyes"])).read_value()
        return values


def views(expected):
    import asyncio
    def match():
        opc = asyncio.run(opc_read())
        hmi = json.load(urlopen("http://127.0.0.1:8000/api", timeout=3))
        triple = [opc["PhotoeyeFaultMask"], opc["PhotoeyeFaultSensor"],
                  opc["PhotoeyeFaultLane"]]
        hmi_triple = [hmi["photoeye_fault_mask"], hmi["photoeye_fault_sensor"],
                      hmi["photoeye_fault_lane"]]
        if triple != expected or hmi_triple != expected:
            return None
        if opc["Photoeyes"] != hmi["photoeye_rows"][0]:
            return None
        return {"opc": opc, "hmi": {"fault": hmi_triple,
                "row": hmi["photoeye_rows"][0], "photoeye_mode": hmi["photoeye_mode"],
                "plant_fault": hmi["plant_fault"]}}
    return wait(match, 8, "OPC UA/HMI fault agreement timeout")


def validate_fault(sample):
    row, raw, slot = sample["validated_row"], sample["raw_row"], sample["slot"]
    assert sample["photoeye_fault"] == FAULT, sample
    assert row[3] == raw[3] == slot[0] == 1 and row[4] == raw[4] == slot[1] == 1, sample
    assert row[5:] == [4, 4, 5], row
    assert not sample["master"] and sample["trailers"] == [0] * 9, sample
    assert slot[2:4] == [0, 0] and slot[5:7] == [0, 0] and slot[11] == 0, slot
    assert slot[4] in (1, 2, 3, 4) and sample["inducted"] == 1, sample


def main(directory):
    directory.mkdir(parents=True, exist_ok=False)
    plc = ModbusTcpClient(PLC, port=502, timeout=2)
    assert plc.connect()
    original = {"enables": coils(plc, 881, 7), "modes": coils(plc, 914, 6),
                "setpoints": holding(plc, 200, 11), "seed": holding(plc, 247)[0],
                "photoeye_config": holding(plc, 744, 4)}
    save(directory, "operator_before.json", original)
    asx = xle = None
    journal = tempfile.TemporaryDirectory(prefix="missed-phased-")
    failure = None
    cleanup = []
    def interrupted(_sig, _frame):
        raise RuntimeError("phased runner interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        assert not coils(plc, 880)[0]
        assert [holding(plc, a)[0] for a in (530, 542, 647)] == [0, 0, 0]
        assert holding(plc, 591)[0] == 0 and holding(plc, 748, 3) == [0, 0, 0]
        assert holding(plc, 255, 4)[0] == 0
        before = snapshot(plc)
        save(directory, "initial.json", before)
        set_register(plc, 247, 137)
        old_tick = holding(plc, 243)[0]
        set_coil(plc, 910, True)
        seen = False
        def reset_done():
            nonlocal seen
            tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
            seen |= tick < old_tick or state in (1, 2)
            return seen and state == 0
        wait(reset_done, 25, "scanner reset timeout")
        asx = subprocess.Popen([sys.executable, str(ROOT / "asx.py"), "--plan",
                                str(ROOT / "lane3_plan.json")], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        wait(lambda: socket.create_connection(("127.0.0.1", 8089), .2).close() or True,
             8, "ASX startup timeout")
        for lane in (1, 2, 3):
            set_coil(plc, 881 + lane, lane == 1)
        for address in (914, 915, 918, 919):
            set_coil(plc, address, True)
        for address in range(200, 206):
            set_register(plc, address, 120)
        for address in (207, 208, 209):
            set_register(plc, address, 32000)
        set_register(plc, 746, 40)
        set_register(plc, 747, 220)
        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py"), "--multi",
                                "--packages", "1", "--deadline", "200", "--journal",
                                str(Path(journal.name) / "outcomes.sqlite3")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        save(directory, "processes.json", {"asx_pid": asx.pid, "xle_pid": xle.pid})
        wait(lambda: holding(plc, 558, 2) != [0, 0] and holding(plc, 591)[0] == 0,
             15, "plant/XLe identity timeout")
        set_register(plc, 207, 14)
        set_coil(plc, 880, True)
        wait(lambda: holding(plc, 530)[0] == 1, 10, "slot induction timeout")
        set_register(plc, 207, 32000)
        moving = snapshot(plc)
        save(directory, "moving.json", moving)
        history = []
        upstream_tick = None
        last_mask = None
        until = time.monotonic() + 50
        while time.monotonic() < until:
            snap = snapshot(plc, with_drives=False)
            mask = snap["validated_row"][6]
            if last_mask is not None and (last_mask & 1) and not (mask & 1):
                upstream_tick = snap["tick"]
            key = (snap["raw_row"][6], snap["validated_row"][5], mask,
                   snap["validated_row"][7], snap["event"][5])
            if not history or history[-1]["key"] != key:
                history.append({"key": key, "sample": snap})
            last_mask = mask
            if snap["photoeye_fault"] == FAULT:
                break
            if snap["photoeye_fault"][0]:
                raise AssertionError(f"unexpected photoeye fault {snap['photoeye_fault']}")
            time.sleep(.08)
        else:
            raise TimeoutError("missed-tunnel fault timeout")
        validate_fault(snap)
        snap["drives"] = snapshot(plc)["drives"]
        assert upstream_tick is not None, "upstream falling edge not observed"
        latency = (snap["tick"] - upstream_tick) * 100
        assert 0 < latency < 50000, latency
        evidence = {"modbus": snap, "views": views(FAULT), "history": history,
                    "last_valid_upstream_tick": upstream_tick,
                    "detection_latency_ms": latency,
                    "package_id": f"l1-{snap['validated_row'][0]}-{snap['validated_row'][2]}-1-1"}
        assert evidence["views"]["opc"]["Photoeyes"] == snap["validated_row"]
        save(directory, "fault.json", evidence)
        marker(directory, "plant_restored", 90)
        after = snapshot(plc)
        validate_fault(after)
        assert after["slot"][:5] == snap["slot"][:5]
        assert after["inducted"] == snap["inducted"]
        assert after["trailers"] == snap["trailers"]
        after_views = views(FAULT)
        assert after_views["opc"]["Photoeyes"] == after["validated_row"]
        save(directory, "plant_only.json", {"modbus": after, "views": after_views})
        marker(directory, "recover", 90)
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        save(directory, "error.json", {"failure": failure})
        try:
            marker(directory, "plant_restored", 90)
        except Exception as second:
            cleanup.append(f"wait for plant restoration: {second}")
    finally:
        # The host restores the normal plant before either marker or abort.
        try:
            set_coil(plc, 880, False)
            for address in (914, 915, 918, 919):
                set_coil(plc, address, False)
            old_tick = holding(plc, 243)[0]
            set_coil(plc, 910, True)
            seen = False
            def reset_clear():
                nonlocal seen
                tick, state = holding(plc, 243)[0], holding(plc, 255)[0]
                seen |= tick < old_tick or state in (1, 2)
                return seen and state == 0 and holding(plc, 748, 3) == [0, 0, 0]
            wait(reset_clear, 25, "fault reset timeout")
        except Exception as exc:
            cleanup.append(f"reset: {exc}")
        for address, value in ((247, original["seed"]),
                               *[(200 + i, v) for i, v in enumerate(original["setpoints"])],
                               *[(744 + i, v) for i, v in enumerate(original["photoeye_config"]) ]):
            try:
                set_register(plc, address, value)
            except Exception as exc:
                cleanup.append(f"register {address}: {exc}")
        for address, value in ([*[(881 + i, v) for i, v in enumerate(original["enables"])],
                                *[(914 + i, v) for i, v in enumerate(original["modes"])] ]):
            try:
                set_coil(plc, address, value)
            except Exception as exc:
                cleanup.append(f"coil {address}: {exc}")
        for name, process in (("xle", xle), ("asx", asx)):
            try:
                output = stop(process)
                (directory / f"{name}.jsonl").write_text(output)
            except Exception as exc:
                cleanup.append(f"{name}: {exc}")
        try:
            journal.cleanup()
        except Exception as exc:
            cleanup.append(f"journal: {exc}")
        try:
            final = snapshot(plc)
            assert not final["master"] and final["photoeye_fault"] == [0, 0, 0]
            assert [final["slots"][i] for i in (0, 12, 24)] == [0, 0, 0]
            save(directory, "recovery.json", {"modbus": final, "operator": original,
                                                "failure": failure, "cleanup_errors": cleanup})
        except Exception as exc:
            cleanup.append(f"final verification: {exc}")
        plc.close()
        save(directory, "result.json", {"failure": failure, "cleanup_errors": cleanup})
    if failure or cleanup:
        raise RuntimeError(f"test failure={failure}; cleanup={cleanup}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("/tmp/sorter-missed-"):
        raise SystemExit("expected one /tmp/sorter-missed-* evidence directory")
    main(Path(sys.argv[1]))

"""Run all five one-package XLe decisions on the SCADA VM.

Uses SCADA's existing L2->L1 Modbus route. Starts temporary ASX/XLe child
processes; each case restores PLC operator settings and stops the sorter.
Requires pymodbus 3.6.9 in ~/opcua.
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from pymodbus.client import ModbusTcpClient


ROOT = Path.home() / "sorter-services"
CASES = ("valid", "unknown", "unavailable", "delay", "mismatch")


def holding(c, address, count=1):
    result = c.read_holding_registers(address, count, slave=1)
    if result.isError():
        raise RuntimeError(f"holding {address}: {result}")
    return result.registers


def inputs(c, address, count=1):
    result = c.read_input_registers(address, count, slave=1)
    if result.isError():
        raise RuntimeError(f"input {address}: {result}")
    return result.registers


def coils(c, address, count=1):
    result = c.read_coils(address, count, slave=1)
    if result.isError():
        raise RuntimeError(f"coils {address}: {result}")
    return [bool(v) for v in result.bits[:count]]


def set_coil(c, address, value):
    result = c.write_coil(address, value, slave=1)
    if result.isError():
        raise RuntimeError(f"coil {address}: {result}")


def set_register(c, address, value):
    result = c.write_register(address, value, slave=1)
    if result.isError():
        raise RuntimeError(f"register {address}: {result}")


def events(output):
    records = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
            if "event" in item:
                records.append(item)
        except ValueError:
            pass
    return records


def stop_process(process):
    if process is None:
        return ""
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)[0]


def wait_asx():
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", 8089), .2):
                return
        except OSError:
            time.sleep(.05)
    raise RuntimeError("ASX did not open loopback port 8089")


def run_case(c, case, empty_plan):
    original = {"enables": coils(c, 881, 7), "external": coils(c, 914)[0],
                "setpoints": holding(c, 200, 11), "seed": holding(c, 247)[0]}
    asx = xle = None
    asx_output = xle_output = ""
    outcome = None
    scanner = None
    try:
        set_coil(c, 880, False)
        set_register(c, 247, 137)
        previous_tick = holding(c, 243)[0]
        set_coil(c, 910, True)
        reset_seen = False
        end = time.monotonic() + 20
        while time.monotonic() < end:
            tick, state = holding(c, 243)[0], holding(c, 255)[0]
            reset_seen |= tick < previous_tick or state in (1, 2)
            if reset_seen and state == 0:
                break
            time.sleep(.1)
        if not reset_seen or holding(c, 255)[0] != 0:
            raise RuntimeError("scanner reset did not finish")

        args = [sys.executable, str(ROOT / "asx.py")]
        if case == "unknown":
            args += ["--plan", empty_plan]
        if case == "delay":
            args += ["--scenario", "delay", "--delay", "1.2"]
        if case == "mismatch":
            args += ["--scenario", "mismatch"]
        if case != "unavailable":
            asx = subprocess.Popen(args, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
            wait_asx()
        else:
            try:
                socket.create_connection(("127.0.0.1", 8089), .2).close()
                raise RuntimeError("ASX unavailable case has a listener")
            except ConnectionRefusedError:
                pass

        xle = subprocess.Popen([sys.executable, str(ROOT / "xle.py")],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        set_coil(c, 883, False)
        set_coil(c, 884, False)
        set_coil(c, 914, True)
        if not coils(c, 914)[0] or coils(c, 883)[0] or coils(c, 884)[0]:
            raise RuntimeError("one-lane settings did not latch")
        set_coil(c, 880, True)
        end = time.monotonic() + 90
        rate_raised = False
        while time.monotonic() < end:
            if not rate_raised and holding(c, 220)[0] >= 1:
                set_register(c, 207, 1000)
                rate_raised = True
            seq, serial = holding(c, 118, 2)
            reply = inputs(c, 158, 7)
            if seq == reply[0] and seq == 1 and reply[1] and reply[6]:
                scanner = {"sequence": seq, "serial": serial,
                           "barcode": reply[1], "status": reply[2],
                           "run_nonce": reply[6]}
            ack, state, reason, actual, barcode, nonce = holding(c, 504, 6)
            if state in (3, 4, 5):
                ticks = holding(c, 510, 3)
                outcome = {"command_id": ack, "state": state,
                           "reason": reason, "actual_trailer": actual,
                           "barcode": barcode, "run_nonce": nonce,
                           "scan_tick": ticks[0], "accept_tick": ticks[1],
                           "divert_tick": ticks[2],
                           "inducted": holding(c, 220)[0],
                           "recirc": holding(c, 218)[0],
                           "trailers": holding(c, 222, 9)}
                break
            time.sleep(.1)
        if outcome is None:
            raise TimeoutError("PLC did not reach a terminal outcome")
        if scanner is None or scanner["barcode"] != outcome["barcode"] or \
           scanner["run_nonce"] != outcome["run_nonce"]:
            raise AssertionError("scanner/PLC package mismatch")
        if outcome["inducted"] != 1 or outcome["scan_tick"] <= 0 or \
           outcome["divert_tick"] <= outcome["scan_tick"]:
            raise AssertionError("one-package timing record incomplete")
        if case == "valid":
            if outcome["state"] != 3 or outcome["actual_trailer"] != 2 or \
               outcome["trailers"] != [0, 1, 0, 0, 0, 0, 0, 0, 0] or \
               not outcome["scan_tick"] <= outcome["accept_tick"] < outcome["divert_tick"]:
                raise AssertionError("valid decision did not load trailer 1-2")
        elif outcome["state"] != 5 or outcome["reason"] != 1 or \
             outcome["actual_trailer"] != 0 or outcome["command_id"] != 0 or \
             outcome["accept_tick"] != 0 or outcome["recirc"] != 1 or \
             any(outcome["trailers"]):
            raise AssertionError("fault case failed to recirculate safely")

        xle_output = xle.communicate(timeout=5)[0]
        xle = None
        if case == "delay":
            time.sleep(.5)  # capture ASX's response after the XLe timeout
        asx_output = stop_process(asx)
        asx = None
        xe, ae = events(xle_output), events(asx_output)
        scan = next((e for e in xe if e["event"] == "scan"), None)
        plc_event = next((e for e in xe if e["event"] == "plc_outcome"), None)
        if not scan or not plc_event or scan["package_id"] != plc_event["package_id"]:
            raise AssertionError("XLe package/outcome correlation missing")
        if scan["barcode"] != scanner["barcode"] or \
           scan["scanner_run_nonce"] != scanner["run_nonce"]:
            raise AssertionError("XLe/scanner identity mismatch")
        for record in ae:
            if record.get("package_id") != scan["package_id"] or \
               record.get("request_id") != scan["request_id"]:
                raise AssertionError("ASX event correlation missing")
        if case != "unavailable" and not ae:
            raise AssertionError("ASX events missing")
        if case == "unavailable" and ae:
            raise AssertionError("ASX was unexpectedly available")
        if case == "valid" and not any(e["event"] == "plc_command" for e in xe):
            raise AssertionError("valid command event missing")
        if case != "valid" and any(e["event"] == "plc_command" for e in xe):
            raise AssertionError("fault case issued a PLC command")
        timing = {"scan_to_divert_ms": 100 * (outcome["divert_tick"] - outcome["scan_tick"])}
        if case == "valid":
            timing["scan_to_accept_ms"] = 100 * (outcome["accept_tick"] - outcome["scan_tick"])
            timing["accept_to_divert_ms"] = 100 * (outcome["divert_tick"] - outcome["accept_tick"])
        lookup = next((e for e in xe if e["event"] == "asx_lookup"), None)
        fallback = next((e for e in xe if e["event"] == "safe_fallback"), None)
        if lookup and fallback:
            timing["lookup_to_fallback_ms"] = round(
                (fallback["event_ns"] - lookup["event_ns"]) / 1_000_000, 1)
        return {"case": case, "scanner": scanner, "plc": outcome,
                "timing": timing, "xle_events": xe, "asx_events": ae}
    finally:
        stop_process(xle)
        stop_process(asx)
        set_coil(c, 880, False)
        time.sleep(.3)
        errors = []
        actions = [(set_coil, 914, original["external"]),
                   (set_register, 247, original["seed"])]
        actions += [(set_coil, 881 + i, value)
                    for i, value in enumerate(original["enables"])]
        actions += [(set_register, 200 + i, value)
                    for i, value in enumerate(original["setpoints"])]
        for fn, address, value in actions:
            try:
                fn(c, address, value)
            except Exception as exc:
                errors.append(f"{address}: {exc}")
        restored = (not coils(c, 880)[0] and
                    coils(c, 881, 7) == original["enables"] and
                    coils(c, 914)[0] == original["external"] and
                    holding(c, 200, 11) == original["setpoints"] and
                    holding(c, 247)[0] == original["seed"])
        if errors or not restored:
            raise RuntimeError("operator restoration failed: " + ", ".join(errors))


def main():
    client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not client.connect():
        raise RuntimeError("SCADA cannot reach PLC via existing firewall rule")
    with tempfile.TemporaryDirectory(prefix="sorter-asx-") as directory:
        empty_plan = str(Path(directory) / "empty.json")
        Path(empty_plan).write_text('{"barcodes": {}}')
        failures = 0
        for case in CASES:
            try:
                print(json.dumps(run_case(client, case, empty_plan), sort_keys=True), flush=True)
            except Exception as exc:
                failures += 1
                print(json.dumps({"case": case, "error": repr(exc)}), flush=True)
        client.close()
        if failures:
            raise SystemExit(f"{failures} live cases failed")


if __name__ == "__main__":
    main()

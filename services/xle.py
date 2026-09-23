"""One-lane XLe slice: correlate a scanner read, ask ASX, command PLC, log outcome.

Run on SCADA with pymodbus 3.6.9. Events are JSON lines on stdout. The
single-package runner exits after one terminal PLC outcome.
"""
import argparse
import json
import time
import urllib.request
import uuid


def event(kind, **fields):
    print(json.dumps({"event": kind, **fields}, sort_keys=True), flush=True)


def validate_decision(request, response):
    if response.get("request_id") != request["request_id"] or \
       response.get("package_id") != request["package_id"]:
        return None, "stale_or_mismatched_response"
    if response.get("decision") == "no_decision":
        return None, "unknown_package"
    destination = response.get("destination")
    if response.get("decision") != "route" or type(destination) is not int or \
       not 1 <= destination <= 9:
        return None, "invalid_decision"
    return destination, None


def lookup(request, url, timeout=0.8):
    body = json.dumps(request).encode()
    call = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(call, timeout=timeout) as result:
            response = json.load(result)
    except (OSError, ValueError, TimeoutError) as exc:
        return None, "decision_timeout_or_error: " + type(exc).__name__
    return validate_decision(request, response)


def read(client, address, count=1, inputs=False):
    method = client.read_input_registers if inputs else client.read_holding_registers
    reply = method(address, count, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC read {address}: {reply}")
    return reply.registers


def run(client, asx_url, deadline=90):
    if not client.connect():
        raise RuntimeError("PLC Modbus unavailable")
    seen = set()
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if not client.read_coils(914, 1, slave=1).bits[0]:
            time.sleep(0.1)
            continue
        seq, serial = read(client, 118, 2)
        result = read(client, 158, 7, inputs=True)
        barcode, nonce = read(client, 508, 2)
        if (seq == 0 or seq > 30000 or result[0] != seq or
                result[1] != barcode or result[6] != nonce or
                result[2] not in (0, 3, 4) or not barcode or
                barcode % 1000 != serial):
            time.sleep(0.05)
            continue
        key = (nonce, seq, serial)
        if key in seen:
            time.sleep(0.05)
            continue
        seen.add(key)
        package_id = f"l1-{nonce}-{serial}-{uuid.uuid4().hex[:8]}"
        request = {"package_id": package_id,
                   "request_id": str(uuid.uuid4()),
                   "barcode": barcode, "scanner_sequence": seq,
                   "scanner_run_nonce": nonce, "lane": 1}
        event("scan", **request)
        destination, reason = lookup(request, asx_url)
        if destination is None:
            event("safe_fallback", package_id=package_id,
                  request_id=request["request_id"], action="recirculate",
                  reason=reason)
            command_id = 0
        else:
            event("asx_decision", package_id=package_id,
                  request_id=request["request_id"], destination=destination)
            command_id = read(client, 500)[0] + 1
            if command_id > 30000:
                command_id = 1
            reply = client.write_registers(500,
                                           [command_id, barcode, destination, nonce], slave=1)
            if reply.isError():
                raise RuntimeError(f"PLC command write: {reply}")
            event("plc_command", package_id=package_id,
                  command_id=command_id, barcode=barcode,
                  destination=destination, run_nonce=nonce)
        while time.monotonic() < end:
            ack, state, fault, actual, plc_barcode, plc_nonce = read(client, 504, 6)
            if plc_barcode != barcode or plc_nonce != nonce:
                raise RuntimeError("PLC changed run or package before outcome")
            if command_id and ack == command_id and state in (4,):
                event("plc_outcome", package_id=package_id, command_id=command_id,
                      state="failed", reason=fault, actual_trailer=actual)
                return 1
            if command_id and ack == command_id and state == 3:
                event("plc_outcome", package_id=package_id, command_id=command_id,
                      state="loaded", reason=fault, actual_trailer=actual)
                return 0
            if not command_id and state == 5:
                event("plc_outcome", package_id=package_id, command_id=0,
                      state="recirculated", reason=fault, actual_trailer=0)
                return 0
            time.sleep(0.1)
        raise TimeoutError("PLC outcome timeout")
    raise TimeoutError("scanner result timeout")


if __name__ == "__main__":
    from pymodbus.client import ModbusTcpClient

    parser = argparse.ArgumentParser()
    parser.add_argument("--plc", default="10.10.1.10")
    parser.add_argument("--asx", default="http://127.0.0.1:8089/sort-plan")
    args = parser.parse_args()
    plc = ModbusTcpClient(args.plc, port=502, timeout=2)
    try:
        raise SystemExit(run(plc, args.asx))
    finally:
        plc.close()

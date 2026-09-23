"""Lane 1 XLe: correlate scanner reads, ask ASX, command PLC, log outcomes.

Run on SCADA with pymodbus 3.6.9. Events are JSON lines on stdout. The
The legacy one-package runner and bounded two-slot multi runner share this file.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor


def event(kind, **fields):
    print(json.dumps({"event": kind, "event_ns": time.monotonic_ns(),
                      **fields}, sort_keys=True), flush=True)


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
    for attempt in range(3):
        reply = method(address, count, slave=1)
        if not reply.isError():
            return reply.registers
        if attempt < 2:
            time.sleep(.1)
    raise RuntimeError(f"PLC read {address}: {reply}")


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
        barcode, nonce, scan_tick = read(client, 508, 3)
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
        event("scan", plc_scan_tick=scan_tick, **request)
        event("asx_lookup", package_id=package_id,
              request_id=request["request_id"], timeout_ms=800)
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
            (ack, state, fault, actual, plc_barcode, plc_nonce,
             result_tick, accept_tick, divert_tick) = read(client, 504, 9)
            if plc_barcode != barcode or plc_nonce != nonce:
                raise RuntimeError("PLC changed run or package before outcome")
            if command_id and ack == command_id and state in (4,):
                event("plc_outcome", package_id=package_id, command_id=command_id,
                      state="failed", reason=fault, actual_trailer=actual,
                      scan_tick=result_tick, accept_tick=accept_tick,
                      divert_tick=divert_tick)
                return 1
            if command_id and ack == command_id and state == 3:
                event("plc_outcome", package_id=package_id, command_id=command_id,
                      state="loaded", reason=fault, actual_trailer=actual,
                      scan_tick=result_tick, accept_tick=accept_tick,
                      divert_tick=divert_tick)
                return 0
            if not command_id and state == 5:
                event("plc_outcome", package_id=package_id, command_id=0,
                      state="recirculated", reason=fault, actual_trailer=0,
                      scan_tick=result_tick, accept_tick=accept_tick,
                      divert_tick=divert_tick)
                return 0
            time.sleep(0.1)
        raise TimeoutError("PLC outcome timeout")
    raise TimeoutError("scanner result timeout")


SLOT_BASE = 530
SLOT_WIDTH = 12
TERMINAL = {5: "loaded", 6: "recirculated", 7: "failed"}


def slot_rows(client):
    words = read(client, SLOT_BASE, 2 * SLOT_WIDTH)
    return [words[i:i + SLOT_WIDTH] for i in (0, SLOT_WIDTH)]


def multi_enabled(client):
    for attempt in range(3):
        reply = client.read_coils(914, 2, slave=1)
        if not reply.isError():
            return bool(reply.bits[0] and reply.bits[1])
        if attempt < 2:
            time.sleep(.1)
    raise RuntimeError(f"PLC mode read: {reply}")


def write_multi(client, command_id, op, slot, row, destination=0, nonce=None):
    token, serial, seq, barcode = row[:4]
    if nonce is None:
        nonce = read(client, 509)[0]
    payload = [op, slot, token, serial, seq, barcode, destination, nonce]
    reply = client.write_registers(520, payload, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC payload write: {reply}")
    reply = client.write_register(528, command_id, slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC command commit: {reply}")
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        if read(client, 554)[0] == command_id:
            return read(client, 529)[0]
        time.sleep(.05)
    raise TimeoutError(f"PLC did not acknowledge command {command_id}")


class OutcomeJournal:
    """Durable canonical outcome log, keyed by the full PLC package identity."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS outcomes "
                        "(identity TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.commit()

    def record(self, identity, payload):
        key = ":".join(map(str, identity))
        result = self.db.execute("INSERT OR IGNORE INTO outcomes VALUES (?, ?)",
                                 (key, json.dumps(payload, sort_keys=True)))
        self.db.commit()
        return result.rowcount == 1

    def close(self):
        self.db.close()


def package_request(nonce, row):
    token, serial, seq, barcode = row[:4]
    return {"package_id": f"l1-{nonce}-{token}-{serial}",
            "request_id": str(uuid.uuid4()), "barcode": barcode,
            "package_serial": serial, "slot_token": token,
            "scanner_sequence": seq, "scanner_run_nonce": nonce, "lane": 1}


def run_multi(client, asx_url, packages=2, deadline=120, journal=None,
              terminal_hold=1.0):
    """Recover occupied PLC slots and serve a finite batch or packages=0 forever."""
    if not client.connect():
        raise RuntimeError("PLC Modbus unavailable")
    journal_path = journal or str(Path.home() / "sorter-services" / "xle-outcomes.sqlite3")
    log = OutcomeJournal(journal_path)
    pending = {}
    completed = 0
    epoch = None
    startup_slots = None
    command_id = read(client, 528)[0]
    end = float("inf") if packages == 0 else time.monotonic() + deadline
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            while time.monotonic() < end:
                if not multi_enabled(client):
                    time.sleep(.05)
                    continue
                nonce = read(client, 509)[0]
                rows = slot_rows(client)
                if read(client, 509)[0] != nonce:
                    continue  # reset raced the row read
                if startup_slots is None:
                    startup_slots = {(nonce, r[0], r[1], r[2])
                                     for r in rows if r[0] and r[4] >= 2}
                if epoch is not None and nonce != epoch:
                    for task in pending.values():
                        if task["future"] is not None:
                            task["future"].cancel()
                        event("run_abandoned", package_id=task["request"]["package_id"],
                              old_nonce=epoch, new_nonce=nonce)
                    pending.clear()
                epoch = nonce
                present = {(nonce, r[0], r[1], r[2]) for r in rows if r[0] and r[4]}
                for key in list(pending):
                    if key not in present:
                        task = pending.pop(key)
                        if task["future"] is not None:
                            task["future"].cancel()
                        event("slot_disappeared", package_id=task["request"]["package_id"])
                for slot, row in enumerate(rows):
                    token, serial, seq, barcode, state, dest, actual, reason, scan_tick, accept_tick, divert_tick, plc_cmd = row
                    if not token or state < 2:
                        continue
                    key = (nonce, token, serial, seq)
                    if key not in pending and len(pending) < 2 and \
                       (packages == 0 or completed + len(pending) < packages):
                        request = package_request(nonce, row)
                        future = pool.submit(lookup, request, asx_url) if state == 2 and barcode else None
                        pending[key] = {"slot": slot, "request": request,
                                        "future": future, "decided": state != 2 or not barcode,
                                        "terminal_at": None}
                        if state == 2:
                            event("scan", plc_scan_tick=scan_tick,
                                  recovered=key in startup_slots, **request)
                            if future is not None:
                                event("asx_lookup", package_id=request["package_id"],
                                      request_id=request["request_id"], timeout_ms=800)
                            else:
                                event("safe_fallback", package_id=request["package_id"],
                                      request_id=request["request_id"],
                                      action="recirculate", reason="scanner_no_read")
                        else:
                            event("plc_recovered", package_id=request["package_id"],
                                  slot=slot, slot_token=token, state=state,
                                  command_id=plc_cmd)
                    task = pending.get(key)
                    if task is None:
                        continue
                    package_id = task["request"]["package_id"]
                    if state == 2 and not task["decided"] and task["future"].done():
                        destination, fault = task["future"].result()
                        task["decided"] = True
                        if destination is None:
                            event("safe_fallback", package_id=package_id,
                                  request_id=task["request"]["request_id"],
                                  action="recirculate", reason=fault)
                        else:
                            event("asx_decision", package_id=package_id,
                                  request_id=task["request"]["request_id"],
                                  destination=destination)
                            # The nonce and slot are re-read immediately before
                            # committing the old ASX answer. The PLC checks them again.
                            fresh_nonce = read(client, 509)[0]
                            fresh_row = slot_rows(client)[slot]
                            if fresh_nonce != nonce or fresh_row[:5] != row[:5]:
                                event("decision_discarded", package_id=package_id,
                                      reason="run_or_slot_changed")
                                continue
                            command_id = command_id % 30000 + 1
                            try:
                                result = write_multi(client, command_id, 1, slot, row,
                                                     destination, nonce=nonce)
                            except TimeoutError:
                                if read(client, 509)[0] != nonce:
                                    event("decision_discarded", package_id=package_id,
                                          reason="run_changed_during_command")
                                    continue
                                raise
                            if result == 1:
                                event("plc_command", package_id=package_id,
                                      command_id=command_id, slot=slot,
                                      slot_token=token, destination=destination,
                                      run_nonce=nonce)
                            else:
                                event("safe_fallback", package_id=package_id,
                                      request_id=task["request"]["request_id"],
                                      action="recirculate", reason="plc_rejected_command")
                    if state in TERMINAL:
                        if task["terminal_at"] is None:
                            task["terminal_at"] = time.monotonic()
                            outcome = {"package_id": package_id, "command_id": plc_cmd,
                                       "state": TERMINAL[state], "reason": reason,
                                       "actual_trailer": actual, "scanner_sequence": seq,
                                       "slot_token": token, "scan_tick": scan_tick,
                                       "accept_tick": accept_tick, "divert_tick": divert_tick,
                                       "scanner_to_divert_ms": (divert_tick - scan_tick) * 100,
                                       "acceptance_margin_ms": (divert_tick - accept_tick) * 100
                                       if accept_tick else None}
                            if log.record(key, outcome):
                                event("plc_outcome", **outcome)
                            else:
                                event("plc_outcome_already_recorded", package_id=package_id)
                        if time.monotonic() - task["terminal_at"] >= terminal_hold:
                            if read(client, 509)[0] != nonce or slot_rows(client)[slot][:5] != row[:5]:
                                continue
                            command_id = command_id % 30000 + 1
                            if write_multi(client, command_id, 2, slot, row, nonce=nonce) != 3:
                                raise RuntimeError(f"PLC rejected terminal release for {package_id}")
                            event("plc_release", package_id=package_id,
                                  command_id=command_id)
                            completed += 1
                            del pending[key]
                if packages and completed >= packages:
                    return 0
                time.sleep(.1)
        raise TimeoutError(f"multi outcome timeout: {completed}/{packages}")
    finally:
        log.close()


if __name__ == "__main__":
    from pymodbus.client import ModbusTcpClient

    parser = argparse.ArgumentParser()
    parser.add_argument("--plc", default="10.10.1.10")
    parser.add_argument("--asx", default="http://127.0.0.1:8089/sort-plan")
    parser.add_argument("--packages", type=int, default=1)
    parser.add_argument("--multi", action="store_true",
                        help="use the two-slot protocol even for one package")
    parser.add_argument("--journal", help="durable multi-package outcome journal")
    parser.add_argument("--terminal-hold", type=float, default=1.0,
                        help="seconds to retain a terminal row before release")
    parser.add_argument("--deadline", type=float, default=180)
    args = parser.parse_args()
    plc = ModbusTcpClient(args.plc, port=502, timeout=2)
    try:
        if args.packages < 0:
            parser.error("--packages must be 0 (continuous) or a positive count")
        if args.terminal_hold < 0:
            parser.error("--terminal-hold must be nonnegative")
        raise SystemExit(run_multi(plc, args.asx, args.packages, args.deadline,
                                   args.journal, args.terminal_hold) if args.multi or args.packages != 1
                         else run(plc, args.asx))
    finally:
        plc.close()

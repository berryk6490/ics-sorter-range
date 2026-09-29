"""XLe: correlate lane 1/2/3 scanner reads, ask ASX, command PLC, log outcomes.

Run on SCADA with pymodbus 3.6.9. Events are JSON lines on stdout. The
The legacy one-package runner and bounded three-slot multi runner share this file.
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
SLOT_ADDRESSES = (530, 542, 647)
SLOT_LANE_ADDRESSES = (644, 645, 659)
SLOT_COUNT = len(SLOT_ADDRESSES)
TERMINAL = {5: "loaded", 6: "recirculated", 7: "failed"}


def slot_rows(client):
    legacy = read(client, SLOT_BASE, 2 * SLOT_WIDTH)
    return [legacy[:SLOT_WIDTH], legacy[SLOT_WIDTH:],
            read(client, SLOT_ADDRESSES[2], SLOT_WIDTH)]


def slot_lanes(client):
    return [*read(client, SLOT_LANE_ADDRESSES[0], 2),
            read(client, SLOT_LANE_ADDRESSES[2])[0]]


def multi_enabled(client):
    for attempt in range(3):
        reply = client.read_coils(914, 2, slave=1)
        if not reply.isError():
            return bool(reply.bits[0] and reply.bits[1])
        if attempt < 2:
            time.sleep(.1)
    raise RuntimeError(f"PLC mode read: {reply}")


def write_multi(client, command_id, op, slot, row, destination=0, nonce=None,
                epoch=None):
    token, serial, seq, barcode = row[:4]
    if nonce is None:
        nonce = read(client, 509)[0]
    if epoch is None:
        epoch = plc_epoch(client)
    reply = client.write_registers(562, [epoch % 30000, epoch // 30000], slave=1)
    if reply.isError():
        raise RuntimeError(f"PLC epoch write: {reply}")
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
        self.db.execute("CREATE TABLE IF NOT EXISTS run_epochs "
                        "(identity INTEGER PRIMARY KEY)")
        self.db.commit()

    def allocate_epoch(self):
        # A monotonically allocated, committed ID survives an XLe restart and
        # cannot alias an earlier journal entry after a cold PLC restart.
        value = self.db.execute("SELECT COALESCE(MAX(identity), 0) + 1 FROM run_epochs").fetchone()[0]
        if value >= 30000 * 30000:
            raise RuntimeError("run identity space exhausted")
        self.db.execute("INSERT INTO run_epochs VALUES (?)", (value,))
        self.db.commit()
        return value

    def has_epoch(self, value):
        return self.db.execute("SELECT 1 FROM run_epochs WHERE identity=?", (value,)).fetchone() is not None

    def record(self, identity, payload):
        key = ":".join(map(str, identity))
        result = self.db.execute("INSERT OR IGNORE INTO outcomes VALUES (?, ?)",
                                 (key, json.dumps(payload, sort_keys=True)))
        self.db.commit()
        return result.rowcount == 1

    def close(self):
        self.db.close()


class PassLedger:
    """Durable, run-scoped recycle budget per barcode group.

    SIGHTING_CONTRACT.md section 6. XLe enforces the bound online from this
    history. It counts sightings of a readable barcode, not physical packages:
    a shared budget can send a duplicate-label package to the exception door
    before its own third pass, and a misread package draws on another group.
    Unreadable sightings go straight to the exception door and never join a
    group. Nothing here knows or asks for a plant physical ID.
    """

    ROUTE, NO_ROUTE, EXCEPTION = "route", "no_route", "exception"
    OUTCOMES = ("recycled", "lost", "confirmed", "exception_entry", "stopped")
    SPENT = ("recycled", "lost")

    def __init__(self, path, limit=3):
        if type(limit) is not int or limit < 0:
            raise ValueError("pass limit must be a non-negative integer")
        self.limit = limit
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS sightings "
                        "(run_epoch INTEGER NOT NULL, sighting TEXT NOT NULL, "
                        "barcode INTEGER, decision TEXT NOT NULL, "
                        "ambiguous INTEGER NOT NULL DEFAULT 0, outcome TEXT, "
                        "PRIMARY KEY (run_epoch, sighting))")
        self.db.execute("CREATE TABLE IF NOT EXISTS groups "
                        "(run_epoch INTEGER NOT NULL, barcode INTEGER NOT NULL, "
                        "used INTEGER NOT NULL, PRIMARY KEY (run_epoch, barcode))")
        self.db.commit()

    def used(self, run_epoch, barcode):
        row = self.db.execute("SELECT used FROM groups WHERE run_epoch=? AND barcode=?",
                              (run_epoch, barcode)).fetchone()
        return row[0] if row else 0

    def open(self, run_epoch, sighting, barcode, readable):
        """Decide an outbound sighting. Replaying a known sighting returns its
        stored decision, so an XLe restart cannot re-spend or re-decide it."""
        row = self.db.execute("SELECT decision, ambiguous FROM sightings "
                              "WHERE run_epoch=? AND sighting=?",
                              (run_epoch, sighting)).fetchone()
        if row:
            return {"decision": row[0], "ambiguous": bool(row[1]), "replayed": True}
        group = barcode if readable and barcode else None
        ambiguous = False
        if group is None or self.used(run_epoch, group) >= self.limit:
            decision = self.EXCEPTION
        else:
            others = self.db.execute("SELECT sighting FROM sightings WHERE run_epoch=? "
                                     "AND barcode=? AND outcome IS NULL",
                                     (run_epoch, group)).fetchall()
            if others:
                # Concurrent sightings of one label: keep any accepted route,
                # route neither new nor old again, never guess who returned.
                ambiguous = True
                self.db.execute("UPDATE sightings SET ambiguous=1 WHERE run_epoch=? "
                                "AND barcode=? AND outcome IS NULL", (run_epoch, group))
                decision = self.NO_ROUTE
            else:
                decision = self.ROUTE
        self.db.execute("INSERT INTO sightings (run_epoch, sighting, barcode, decision, "
                        "ambiguous) VALUES (?, ?, ?, ?, ?)",
                        (run_epoch, sighting, group, decision, int(ambiguous)))
        self.db.commit()
        return {"decision": decision, "ambiguous": ambiguous, "replayed": False}

    def close(self, run_epoch, sighting, outcome):
        """Record a sighting's single outcome. Recycled and lost spend one unit
        of the group budget; nothing refunds it within the run."""
        if outcome not in self.OUTCOMES:
            raise ValueError(f"unknown sighting outcome {outcome!r}")
        row = self.db.execute("SELECT barcode, decision FROM sightings "
                              "WHERE run_epoch=? AND sighting=?",
                              (run_epoch, sighting)).fetchone()
        if row is None:
            raise KeyError(f"unknown sighting {sighting!r}")
        barcode, decision = row
        result = self.db.execute("UPDATE sightings SET outcome=? WHERE run_epoch=? "
                                 "AND sighting=? AND outcome IS NULL",
                                 (outcome, run_epoch, sighting))
        recorded = result.rowcount == 1
        if recorded and outcome in self.SPENT and barcode is not None:
            self.db.execute("INSERT INTO groups VALUES (?, ?, 1) ON CONFLICT"
                            "(run_epoch, barcode) DO UPDATE SET used = used + 1",
                            (run_epoch, barcode))
        self.db.commit()
        return {"recorded": recorded,
                "exception_failed": recorded and decision == self.EXCEPTION and
                outcome in self.SPENT}

    def close_db(self):
        self.db.close()


def package_request(epoch, nonce, row, lane=1):
    token, serial, seq, barcode = row[:4]
    if lane not in (1, 2, 3):
        raise ValueError(f"invalid PLC package lane {lane}")
    return {"package_id": f"l{lane}-{epoch}-{nonce}-{token}-{serial}",
            "request_id": str(uuid.uuid4()), "barcode": barcode,
            "package_serial": serial, "slot_token": token,
            "scanner_sequence": seq, "scanner_run_nonce": nonce,
            "run_epoch": epoch, "lane": lane}


def plc_epoch(client):
    lo, hi = read(client, 558, 2)
    return hi * 30000 + lo


def fail_closed(client, reason):
    event("external_sort_fault", reason=reason, action="sorter_stopped")
    client.write_coil(880, False, slave=1)
    client.write_register(564, 2, slave=1)
    raise RuntimeError(reason)


def ensure_epoch(client, journal):
    value = plc_epoch(client)
    if value:
        if not journal.has_epoch(value):
            fail_closed(client, f"PLC run identity {value} absent from XLe journal")
        if read(client, 564)[0] == 2:
            fail_closed(client, "external sort fault requires operator review")
        return value
    # The PLC inhibits multi-package induction until this handshake completes.
    if read(client, 255)[0] != 0:
        return None
    client.write_coil(880, False, slave=1)
    value = journal.allocate_epoch()
    offer_id = read(client, 557)[0] % 30000 + 1
    reply = client.write_registers(555, [value % 30000, value // 30000], slave=1)
    if reply.isError():
        fail_closed(client, "run identity offer write failed")
    reply = client.write_register(557, offer_id, slave=1)
    if reply.isError():
        fail_closed(client, "run identity offer commit failed")
    until = time.monotonic() + 2
    while time.monotonic() < until:
        if read(client, 560)[0] == offer_id:
            if plc_epoch(client) == value:
                event("run_identity_established", run_epoch=value)
                return value
            break
        time.sleep(.05)
    fail_closed(client, f"PLC rejected run identity {value}")


def run_multi(client, asx_url, packages=2, deadline=120, journal=None,
              terminal_hold=1.0):
    """Recover occupied PLC slots and serve a finite batch or packages=0 forever."""
    if not client.connect():
        raise RuntimeError("PLC Modbus unavailable")
    journal_path = journal or str(Path.home() / "sorter-services" / "xle-outcomes.sqlite3")
    try:
        log = OutcomeJournal(journal_path)
    except (sqlite3.Error, OSError) as exc:
        fail_closed(client, f"XLe journal unavailable: {exc}")
    pending = {}
    completed = 0
    run_identity = None
    startup_slots = None
    heartbeat_sequence = None
    heartbeat_at = 0
    recovery_proved = False
    command_id = read(client, 528)[0]
    end = float("inf") if packages == 0 else time.monotonic() + deadline
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            while time.monotonic() < end:
                if not multi_enabled(client):
                    time.sleep(.05)
                    continue
                try:
                    current_epoch = ensure_epoch(client, log)
                except (sqlite3.Error, OSError) as exc:
                    fail_closed(client, f"XLe journal identity check failed: {exc}")
                if current_epoch is None:
                    time.sleep(.05)
                    continue
                if run_identity is None or current_epoch != run_identity[0]:
                    heartbeat_sequence = read(client, 567)[0]
                    heartbeat_at = 0
                    recovery_proved = False
                if time.monotonic() - heartbeat_at >= .5:
                    heartbeat_sequence = heartbeat_sequence % 30000 + 1
                    reply = client.write_registers(565,
                                                   [current_epoch % 30000,
                                                    current_epoch // 30000], slave=1)
                    if reply.isError():
                        raise RuntimeError("PLC heartbeat identity write failed")
                    reply = client.write_register(567, heartbeat_sequence, slave=1)
                    if reply.isError():
                        raise RuntimeError("PLC heartbeat commit failed")
                    heartbeat_at = time.monotonic()
                heartbeat_accepted = read(client, 573)[0] == heartbeat_sequence
                liveness = read(client, 569)[0]
                if liveness == 3 and heartbeat_accepted and not recovery_proved:
                    # Recovery proof is issued only after the explicit retry.
                    # The PLC ignores any proof already present when retry was pressed.
                    try:
                        integrity = log.db.execute("PRAGMA quick_check").fetchone()[0]
                        if integrity != "ok" or not log.has_epoch(current_epoch):
                            fail_closed(client, "journal or active run identity failed recovery verification")
                    except (sqlite3.Error, OSError) as exc:
                        fail_closed(client, f"journal recovery verification failed: {exc}")
                    proof_id = read(client, 572)[0] % 30000 + 1
                    reply = client.write_registers(570,
                                                   [current_epoch % 30000,
                                                    current_epoch // 30000], slave=1)
                    if reply.isError():
                        raise RuntimeError("PLC recovery identity write failed")
                    reply = client.write_register(572, proof_id, slave=1)
                    if reply.isError():
                        raise RuntimeError("PLC recovery commit failed")
                    event("xle_recovery_proved", run_epoch=current_epoch,
                          recovery_sequence=proof_id)
                    recovery_proved = True
                elif liveness == 0:
                    recovery_proved = False
                nonce = read(client, 509)[0]
                rows = slot_rows(client)
                lanes = slot_lanes(client)
                if read(client, 509)[0] != nonce or plc_epoch(client) != current_epoch:
                    continue  # reset raced the row read
                if any(row[0] and row[4] >= 2 and lane not in (1, 2, 3)
                       for row, lane in zip(rows, lanes)):
                    fail_closed(client, "PLC occupied slot has no valid lane identity")
                if startup_slots is None:
                    startup_slots = {(current_epoch, nonce, r[0], r[1], r[2])
                                     for r in rows if r[0] and r[4] >= 2}
                if run_identity is not None and (current_epoch, nonce) != run_identity:
                    for task in pending.values():
                        if task["future"] is not None:
                            task["future"].cancel()
                        event("run_abandoned", package_id=task["request"]["package_id"],
                              old_run=run_identity, new_run=(current_epoch, nonce))
                    pending.clear()
                    startup_slots = set()
                run_identity = (current_epoch, nonce)
                present = {(current_epoch, nonce, r[0], r[1], r[2]) for r in rows if r[0] and r[4]}
                for key in list(pending):
                    if key not in present:
                        task = pending.pop(key)
                        if task["future"] is not None:
                            task["future"].cancel()
                        event("slot_disappeared", package_id=task["request"]["package_id"])
                for slot, row in enumerate(rows):
                    lane = lanes[slot]
                    token, serial, seq, barcode, state, dest, actual, reason, scan_tick, accept_tick, divert_tick, plc_cmd = row
                    if not token or state < 2 or (state == 2 and not heartbeat_accepted
                                                    and liveness == 0):
                        continue
                    key = (current_epoch, nonce, token, serial, seq)
                    if key not in pending and len(pending) < SLOT_COUNT and \
                       (packages == 0 or completed + len(pending) < packages):
                        request = package_request(current_epoch, nonce, row, lane)
                        future = (pool.submit(lookup, request, asx_url)
                                  if state == 2 and barcode and liveness == 0 else None)
                        pending[key] = {"slot": slot, "request": request,
                                        "future": future,
                                        "decided": state != 2 or not barcode or liveness != 0,
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
                                      action="recirculate",
                                      reason="xle_liveness_fault" if liveness else "scanner_no_read")
                        else:
                            event("plc_recovered", package_id=request["package_id"],
                                  slot=slot, slot_token=token, state=state,
                                  command_id=plc_cmd)
                    task = pending.get(key)
                    if task is None:
                        continue
                    package_id = task["request"]["package_id"]
                    if state == 2 and not task["decided"] and liveness != 0:
                        task["decided"] = True
                        task["future"].cancel()
                        event("safe_fallback", package_id=package_id,
                              request_id=task["request"]["request_id"],
                              action="recirculate", reason="xle_liveness_fault")
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
                            fresh_epoch = plc_epoch(client)
                            fresh_row = slot_rows(client)[slot]
                            if (fresh_nonce != nonce or fresh_epoch != current_epoch or
                                fresh_row[:5] != row[:5] or slot_lanes(client)[slot] != lane):
                                event("decision_discarded", package_id=package_id,
                                      reason="run_or_slot_changed")
                                continue
                            command_id = command_id % 30000 + 1
                            try:
                                result = write_multi(client, command_id, 1, slot, row,
                                                     destination, nonce=nonce,
                                                     epoch=current_epoch)
                            except TimeoutError:
                                if read(client, 509)[0] != nonce or plc_epoch(client) != current_epoch:
                                    event("decision_discarded", package_id=package_id,
                                          reason="run_changed_during_command")
                                    continue
                                raise
                            if result == 1:
                                event("plc_command", package_id=package_id,
                                      command_id=command_id, slot=slot,
                                      slot_token=token, destination=destination,
                                      run_nonce=nonce, run_epoch=current_epoch)
                            else:
                                event("safe_fallback", package_id=package_id,
                                      request_id=task["request"]["request_id"],
                                      action="recirculate", reason="plc_rejected_command")
                    if state in TERMINAL:
                        if task["terminal_at"] is None:
                            task["terminal_at"] = time.monotonic()
                            outcome = {"package_id": package_id, "lane": lane,
                                       "command_id": plc_cmd,
                                       "state": TERMINAL[state], "reason": reason,
                                       "actual_trailer": actual, "scanner_sequence": seq,
                                       "slot_token": token, "scan_tick": scan_tick,
                                       "accept_tick": accept_tick, "divert_tick": divert_tick,
                                       "scanner_to_divert_ms": (divert_tick - scan_tick) * 100,
                                       "acceptance_margin_ms": (divert_tick - accept_tick) * 100
                                       if accept_tick else None}
                            try:
                                recorded = log.record(key, outcome)
                            except (sqlite3.Error, OSError) as exc:
                                fail_closed(client, f"XLe outcome journal write failed: {exc}")
                            if recorded:
                                event("plc_outcome", **outcome)
                            else:
                                event("plc_outcome_already_recorded", package_id=package_id)
                        if time.monotonic() - task["terminal_at"] >= terminal_hold:
                            if (read(client, 509)[0] != nonce or plc_epoch(client) != current_epoch or
                                slot_rows(client)[slot][:5] != row[:5] or
                                slot_lanes(client)[slot] != lane):
                                continue
                            command_id = command_id % 30000 + 1
                            if write_multi(client, command_id, 2, slot, row, nonce=nonce,
                                           epoch=current_epoch) != 3:
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
                        help="use the multi-slot protocol even for one package")
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

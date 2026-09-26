"""Canonical read-only Phase 2A snapshot and typed restoration comparison."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from deployment_preflight import MANIFEST, guest_read, service_read_command
from accumulation_register_contract import (RESET_ZERO_PROCESS_COUNTERS,
                                            SERIAL_NEXT_ADDRESS, PROCESS_FIRST_ADDRESS)

SCHEMA = 1
PLANT = re.compile(r"^/home/kevin/venv/bin/python /home/kevin/plant\.py(?:\s|$)")
TEMPORARY = re.compile(
    r"(?:live_accumulation_monitor\.py|live_accumulation_detached\.py|"
    r"live_accumulation_fixture\.py|/sorter-services/(?:xle|asx)\.py)")
VM_NAMES = ("analyst", "drives", "fw", "plc", "scada")
VFD_NAMES = ("induct1", "induct2", "induct3", "outbnd1", "outbnd2", "outbnd3")
EXACT_COILS = (880, 881, 882, 883, 884, 885, 886, 887, 914, 915, 918, 919, 920)
ZERO_COILS = (910, 912, 913, 916, 917)
PROCESS_COUNTERS = tuple(RESET_ZERO_PROCESS_COUNTERS)


def _guest_json(vm, command, label):
    output = guest_read(vm, command, label)
    try:
        return json.loads(output.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"{label}: missing JSON result") from exc


def _processes(vm):
    output = guest_read(vm, "ps -eo pid=,args=", f"processes:{vm}")
    lines = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if match:
            lines.append({"pid": int(match.group(1)), "args": match.group(2)})
    return lines


def capture():
    """All guest commands pass the canonical completion/prompt/liveness gate."""
    manifest = json.loads(MANIFEST.read_text())
    plc = _guest_json("drives", "/home/kevin/venv/bin/python /home/kevin/read_accumulation_state.py --with-vfds",
                      "typed_plc_and_vfds")
    units = sorted({(entry["guest"], unit) for entry in manifest["components"]
                    for unit in entry["associated_service"]})
    services = {}
    for vm, unit in units:
        output = guest_read(vm, service_read_command(unit), f"service:{unit}")
        state = output.splitlines()[-1]
        if state not in ("active", "inactive", "failed", "activating", "deactivating"):
            raise ValueError(f"invalid service state: {vm}:{unit}: {state}")
        services[f"{vm}:{unit}"] = state
    vms = {}
    for vm in VM_NAMES:
        result = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", vm],
                                capture_output=True, text=True, timeout=10, check=True)
        vms[vm] = result.stdout.strip()
    guest_processes = {vm: _processes(vm) for vm in ("drives", "scada")}
    plant = [row for row in guest_processes["drives"] if PLANT.match(row["args"])]
    temporary = {vm: [row for row in rows if TEMPORARY.search(row["args"])]
                 for vm, rows in guest_processes.items()}
    host = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                          text=True, timeout=10, check=True).stdout
    proxy = [line.strip() for line in host.splitlines()
             if re.match(r"\s*\d+\s+python(?:3(?:\.\d+)?)?\s+tests/serial_hmi_proxy\.py(?:\s|$)", line)]
    return {"schema_version": SCHEMA, "captured_utc": datetime.now(timezone.utc).isoformat(),
            "program_identity": manifest["plc_program_identity"], "plc": plc,
            "environment": {"services": services, "vms": vms,
                            "canonical_plant": plant, "temporary": temporary,
                            "host_proxy": proxy}}


def _value(snapshot, path):
    current = snapshot
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _record(rows, before, after, path, kind, expected=None, predicate=None):
    try:
        old, new = _value(before, path), _value(after, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        rows.append({"field": path, "class": kind, "status": "FAIL",
                     "expected": expected, "observed": "MISSING", "difference": str(exc)})
        return
    target = old if kind == "EXACT" else expected
    if kind == "INFORMATIONAL":
        ok = True
    elif kind == "DYNAMIC_HEALTH" or kind == "SAFE_INVARIANT":
        ok = predicate(new)
    else:
        ok = new == target and type(new) is type(target)
    rows.append({"field": path, "class": kind, "status": "PASS" if ok else "FAIL",
                 "expected": target if kind != "INFORMATIONAL" else "record only",
                 "observed": new,
                 "difference": None if ok else {"before": old, "after": new,
                                                  "expected": target}})


def _structural(snapshot):
    p = snapshot["plc"]
    e = snapshot["environment"]
    if snapshot["schema_version"] != SCHEMA or p["reader_schema"] != 2:
        raise ValueError("snapshot schema mismatch")
    if snapshot["program_identity"] != p["identity"]:
        raise ValueError("manifest/PLC identity mismatch")
    lengths = {"coils_880_920": 41, "setpoints_200_210": 11,
               "photoeye_config_744_747": 4, "slots": 3, "lanes": 3,
               "process_214_242": 29, "scanner_counters_250_254": 5,
               "plant_faults": 3, "photoeye_faults": 3,
               "zone_view": 23, "zone_raw": 17, "plant_raw": 3,
               "trailer_counters": 9, "xle_health": 2}
    for field, length in lengths.items():
        if not isinstance(p[field], list) or len(p[field]) != length:
            raise ValueError(f"incomplete PLC field: {field}")
    if any(len(row) != 12 for row in p["slots"]):
        raise ValueError("incomplete slot row")
    if any(len(row) != 10 for row in p["plant_raw"]):
        raise ValueError("incomplete plant row")
    if set(p["vfds"]) != set(VFD_NAMES):
        raise ValueError("incomplete six-VFD snapshot")
    required_units = {f"{entry['guest']}:{unit}"
                      for entry in json.loads(MANIFEST.read_text())["components"]
                      for unit in entry["associated_service"]}
    if (set(e["vms"]) != set(VM_NAMES) or set(e["services"]) != required_units or
        set(e["temporary"]) != {"drives", "scada"}):
        raise ValueError("incomplete environment snapshot")
    for name in VFD_NAMES:
        for field in ("host", "command", "setpoint", "feedback_rpm", "status",
                      "fault", "current", "thermal", "belt_load", "frequency"):
            p["vfds"][name][field]
    for field in ("run_id", "epoch", "epoch_fault", "scanner_nonce", "plant_epoch_nonce"):
        p["run_identity"][field]
    for field in ("canonical_plant", "temporary", "host_proxy"):
        e[field]


def compare(before, after):
    rows = []
    try:
        _structural(before)
        _structural(after)
    except (KeyError, TypeError, ValueError) as exc:
        return {"status": "FAIL", "schema_version": SCHEMA,
                "program_identity": before.get("program_identity"),
                "fields": [{"field": "schema", "class": "SAFE_INVARIANT",
                            "status": "FAIL", "difference": str(exc)}],
                "differences": [{"field": "schema", "class": "SAFE_INVARIANT",
                                 "status": "FAIL", "difference": str(exc)}],
                "intentionally_ignored": []}
    if before["program_identity"] != after["program_identity"]:
        rows.append({"field": "program_identity", "class": "EXACT", "status": "FAIL",
                     "expected": before["program_identity"],
                     "observed": after["program_identity"], "difference": "program changed"})
    for address in EXACT_COILS:
        _record(rows, before, after, f"plc.coils_880_920.{address - 880}", "EXACT")
    for address in ZERO_COILS:
        _record(rows, before, after, f"plc.coils_880_920.{address - 880}", "RESET_ZERO", False)
    for path in ("plc.seed", "plc.setpoints_200_210", "plc.photoeye_config_744_747"):
        _record(rows, before, after, path, "EXACT")
    for address in PROCESS_COUNTERS:
        _record(rows, before, after,
                f"plc.process_214_242.{address - PROCESS_FIRST_ADDRESS}", "RESET_ZERO", 0)
    for idx in range(9):
        _record(rows, before, after, f"plc.trailer_counters.{idx}", "RESET_ZERO", 0)
    for idx in range(5):
        _record(rows, before, after, f"plc.scanner_counters_250_254.{idx}", "RESET_ZERO", 0)
    for idx in range(3):
        _record(rows, before, after, f"plc.slots.{idx}.4", "RESET_ZERO", 0)
        _record(rows, before, after, f"plc.lanes.{idx}", "RESET_ZERO", 0)
        _record(rows, before, after, f"plc.photoeye_faults.{idx}", "RESET_ZERO", 0)
    for path in ("plc.plant_faults.0", "plc.plant_faults.1", "plc.scanner_state",
                 "plc.scanner_fault_mask", "plc.run_identity.epoch_fault",
                 "plc.xle_health.1", "plc.zone_view.22", "plc.zone_view.20"):
        _record(rows, before, after, path, "RESET_ZERO", 0)
    # The PLC clears its internal zone rows at reset, but copies them to the
    # supervisory registers only while plant and accumulation modes are on.
    # With both modes off these are inactive historical payload, not occupancy.
    for idx in (0, 1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13, 15, 16, 17, 18):
        _record(rows, before, after, f"plc.zone_view.{idx}", "INFORMATIONAL")
    _record(rows, before, after, "plc.coils_880_920.0", "SAFE_INVARIANT", "master off",
            lambda v: v is False)
    for name in VFD_NAMES:
        root = f"plc.vfds.{name}"
        for field in ("command", "setpoint"):
            _record(rows, before, after, f"{root}.{field}", "EXACT")
        _record(rows, before, after, f"{root}.fault", "RESET_ZERO", 0)
        _record(rows, before, after, f"{root}.feedback_rpm", "DYNAMIC_HEALTH",
                "settled 0..40 RPM", lambda v: type(v) is int and 0 <= v <= 40)
        _record(rows, before, after, f"{root}.status", "DYNAMIC_HEALTH",
                "ready, not running or faulted", lambda v: type(v) is int and v & 1 and not v & 6)
        for field in ("belt_load", "frequency", "current", "thermal", "host"):
            _record(rows, before, after, f"{root}.{field}", "INFORMATIONAL")
    _record(rows, before, after, "environment.services", "SAFE_INVARIANT",
            "same required units, all active",
            lambda v: set(v) == set(before["environment"]["services"]) and
                      all(state == "active" for state in v.values()))
    for vm in VM_NAMES:
        _record(rows, before, after, f"environment.vms.{vm}", "EXACT")
    _record(rows, before, after, "environment.canonical_plant", "SAFE_INVARIANT",
            "exactly one unflagged canonical plant process",
            lambda v: len(v) == 1 and not "--block-" in v[0]["args"])
    for vm in ("drives", "scada"):
        _record(rows, before, after, f"environment.temporary.{vm}", "SAFE_INVARIANT",
                "no validation worker, monitor, fixture, XLe or ASX", lambda v: v == [])
    _record(rows, before, after, "environment.host_proxy", "SAFE_INVARIANT",
            "no HMI proxy", lambda v: v == [])
    for path in ("captured_utc", "plc.run_identity.run_id", "plc.run_identity.epoch",
                 "plc.run_identity.scanner_nonce", "plc.run_identity.plant_epoch_nonce",
                 "plc.zone_view.4", "plc.zone_view.9", "plc.zone_view.14",
                 "plc.zone_view.19", "plc.zone_view.21", "plc.xle_health.0",
                 "plc.plant_faults.2",
                 f"plc.process_214_242.{SERIAL_NEXT_ADDRESS - PROCESS_FIRST_ADDRESS}",
                 "plc.zone_raw", "plc.plant_raw"):
        _record(rows, before, after, path, "INFORMATIONAL")
    for slot in range(3):
        for offset in (0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11):
            _record(rows, before, after, f"plc.slots.{slot}.{offset}", "INFORMATIONAL")
    return {"status": "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL",
            "schema_version": SCHEMA, "program_identity": after["program_identity"],
            "fields": rows, "differences": [r for r in rows if r["status"] == "FAIL"],
            "intentionally_ignored": [r["field"] for r in rows if r["class"] == "INFORMATIONAL"]}


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    p = sub.add_parser("capture")
    p.add_argument("--output", required=True)
    p = sub.add_parser("compare")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--output")
    args = cli.parse_args(argv)
    if args.action == "capture":
        result = capture()
        save(args.output, result)
        report = compare(result, result)
        print(json.dumps({"snapshot": args.output, "baseline": report["status"],
                          "program_identity": result["program_identity"]}))
    else:
        result = compare(json.loads(Path(args.before).read_text()),
                         json.loads(Path(args.after).read_text()))
        if args.output:
            save(args.output, result)
        print(json.dumps(result, sort_keys=True))
        report = result
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

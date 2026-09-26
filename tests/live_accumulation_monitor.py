"""Read-only, bounded Phase 2A PLC readiness monitor for the drives guest.

The monitor owns only files in its unique run directory. ``inspect`` and
``signal`` check the recorded Linux process start time and command line before
the host can treat a PID as live or send it SIGTERM.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time


RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
STOP = False


def now():
    return {"monotonic_ns": time.monotonic_ns(),
            "wall_time_ns": time.time_ns(),
            "wall_time_utc": datetime.now(timezone.utc).isoformat()}


def atomic_new_json(path, value):
    """Create once, durably, without replacing a stale marker."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def process_start_ticks(pid):
    data = Path(f"/proc/{pid}/stat").read_text()
    return int(data[data.rfind(")") + 2:].split()[19])


def inspect_process(pid_file, run_id, expected_pid):
    record = json.loads(Path(pid_file).read_text())
    if record.get("run_id") != run_id or record.get("pid") != expected_pid:
        raise ValueError("PID file run ID or PID mismatch")
    pid = record["pid"]
    try:
        proc = Path(f"/proc/{pid}")
        if proc.stat().st_uid != os.getuid():
            raise ValueError("monitor PID belongs to another user")
        if process_start_ticks(pid) != record["start_ticks"]:
            raise ValueError("monitor PID was reused")
        if (proc / "stat").read_text().split(")", 1)[1].split()[0] == "Z":
            return {"run_id": run_id, "pid": pid,
                    "start_ticks": record["start_ticks"],
                    "alive": False, "matches": False}
        argv = [part.decode(errors="replace") for part in
                (proc / "cmdline").read_bytes().split(b"\0") if part]
        script = Path(__file__).resolve()
        if not any(Path(arg).resolve() == script for arg in argv if arg.endswith(".py")):
            raise ValueError("PID does not run the canonical monitor")
        if "--run-id" not in argv or argv[argv.index("--run-id") + 1] != run_id:
            raise ValueError("PID has a different monitor run ID")
        return {"run_id": run_id, "pid": pid, "start_ticks": record["start_ticks"],
                "alive": True, "matches": True}
    except FileNotFoundError:
        return {"run_id": run_id, "pid": pid, "start_ticks": record["start_ticks"],
                "alive": False, "matches": False}


def read_sample(client, run_id):
    def registers(address, count=1):
        reply = client.read_holding_registers(address, count, slave=1)
        if reply.isError() or len(reply.registers) != count:
            raise IOError(f"PLC register read {address}:{count} failed")
        return reply.registers

    def coils(address, count=1):
        reply = client.read_coils(address, count, slave=1)
        if reply.isError() or len(reply.bits) < count:
            raise IOError(f"PLC coil read {address}:{count} failed")
        return [bool(value) for value in reply.bits[:count]]

    identity = registers(249)[0]
    if identity != 24115:
        raise ValueError(f"unexpected PLC identity {identity}")
    zone = registers(751, 38)
    slots = [registers(base, 12) for base in (530, 542, 647)]
    lane_slot = registers(644, 2) + registers(659)
    fields = {
        "run_id": run_id, "plc_identity": identity,
        "plc_epoch": registers(558, 2), "plc_nonce": registers(509)[0],
        "plant_identity": registers(587, 3), "plant_fault": registers(591)[0],
        "master": coils(880)[0], "accumulation_mode": coils(920)[0],
        "plant_mode": coils(918)[0], "photoeye_mode": coils(919)[0],
        "raw_zone_rows": [zone[i:i + 5] for i in (0, 5, 10)],
        "validated_zone_rows": [zone[i:i + 5] for i in (15, 20, 25)],
        "zone_quality": zone[30:33], "raw_ready_mask": zone[33],
        "commit_sequence": zone[34], "validated_ready_mask": zone[35],
        "zone_age_scans": zone[36], "zone_fault": zone[37],
        "slots": [{"slot": i, "token": row[0], "serial": row[1],
                   "scanner_sequence": row[2], "barcode": row[3],
                   "state": row[4], "destination": row[5],
                   "actual_trailer": row[6], "lane": lane_slot[i]}
                  for i, row in enumerate(slots)],
        "lane_enables": coils(882, 3), "outbound_enables": coils(885, 3),
        "block_state": [{"slot": i, "hold_reason": zone[17 + i * 5],
                         "motion": zone[16 + i * 5]}
                        for i in range(3)],
    }
    if not 0 <= fields["raw_ready_mask"] <= 7 or not 0 <= fields["validated_ready_mask"] <= 7:
        raise ValueError("readiness mask out of bounds")
    if any(not 0 <= quality <= 3 for quality in fields["zone_quality"]):
        raise ValueError("zone quality out of bounds")
    return fields


def emit(stream, event, run_id, **fields):
    row = {"event": event, "run_id": run_id, **now(), **fields}
    line = json.dumps(row, sort_keys=True, separators=(",", ":"))
    stream.write(line + "\n")
    stream.flush()
    return line


def _stop(_signal, _frame):
    global STOP
    STOP = True


def run_monitor(args, client_factory=None, install_signals=True):
    global STOP
    STOP = False
    if not RUN_ID.fullmatch(args.run_id):
        raise ValueError("invalid run ID")
    if not 0.2 <= args.duration <= 600 or not 0.05 <= args.interval <= 5:
        raise ValueError("duration or interval outside bounded monitor range")
    paths = [Path(args.output), Path(args.ready), Path(args.pid_file), Path(args.result)]
    if len(set(paths)) != 4 or any(path.exists() for path in paths):
        raise FileExistsError("monitor paths must be distinct and unused")
    if len({path.parent for path in paths}) != 1 or not paths[0].parent.is_dir():
        raise ValueError("monitor paths must share an existing run directory")
    if client_factory is None:
        from pymodbus.client import ModbusTcpClient
        client_factory = ModbusTcpClient
    client = None
    if install_signals:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    pid = os.getpid()
    ticks = process_start_ticks(pid)
    sample_count = 0
    status = "error"
    reason = "startup failure"
    error = None
    output = paths[0]
    with output.open("x", encoding="utf-8", buffering=1) as stream:
        try:
            emit(stream, "monitor_starting", args.run_id, pid=pid, start_ticks=ticks)
            atomic_new_json(paths[2], {"run_id": args.run_id, "pid": pid,
                                       "start_ticks": ticks})
            if STOP:
                raise InterruptedError("monitor stopped before PLC connection")
            client = client_factory(args.plc_host, port=args.plc_port, timeout=2)
            if not client.connect():
                raise ConnectionError("PLC Modbus connection failed")
            initial = read_sample(client, args.run_id)
            if STOP:
                raise InterruptedError("monitor stopped before initial sample")
            initial_line = emit(stream, "initial_sample", args.run_id, sample=initial,
                                sample_number=0)
            stream.flush()
            os.fsync(stream.fileno())
            initial_hash = hashlib.sha256((initial_line + "\n").encode()).hexdigest()
            emit(stream, "monitor_ready", args.run_id, pid=pid, sample_number=0)
            stream.flush()
            os.fsync(stream.fileno())
            atomic_new_json(paths[1], {"run_id": args.run_id, "pid": pid,
                                       "start_ticks": ticks,
                                       "initial_sample_sha256": initial_hash,
                                       "ready_monotonic_ns": time.monotonic_ns(),
                                       "ready_wall_time_ns": time.time_ns()})
            end = time.monotonic() + args.duration
            previous = initial
            while not STOP and time.monotonic() < end:
                time.sleep(min(args.interval, max(0, end - time.monotonic())))
                if STOP or time.monotonic() >= end:
                    break
                current = read_sample(client, args.run_id)
                sample_count += 1
                emit(stream, "change" if current != previous else "sample",
                     args.run_id, sample=current, sample_number=sample_count)
                previous = current
            status = "complete"
            reason = "signal" if STOP else "duration"
            emit(stream, "monitor_stopping", args.run_id, reason=reason,
                 sample_count=sample_count)
            emit(stream, "monitor_complete", args.run_id, reason=reason,
                 sample_count=sample_count)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            reason = "error"
            emit(stream, "monitor_error", args.run_id, error=error,
                 sample_count=sample_count)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    if status == "complete":
                        status = "error"
                        reason = "error"
                        error = f"client close: {exc}"
                        emit(stream, "monitor_error", args.run_id, error=error,
                             sample_count=sample_count)
            stream.flush()
            os.fsync(stream.fileno())
            atomic_new_json(paths[3], {"run_id": args.run_id, "pid": pid,
                                       "start_ticks": ticks, "status": status,
                                       "reason": reason, "error": error,
                                       "sample_count": sample_count, **now()})
    return 0 if status == "complete" else 1


def parser():
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    monitor = sub.add_parser("monitor")
    for option in ("plc-host", "run-id", "output", "ready", "pid-file", "result"):
        monitor.add_argument("--" + option, required=True)
    monitor.add_argument("--plc-port", type=int, required=True)
    monitor.add_argument("--duration", type=float, required=True)
    monitor.add_argument("--interval", type=float, required=True)
    for action in ("inspect", "signal"):
        command = sub.add_parser(action)
        command.add_argument("--run-id", required=True)
        command.add_argument("--pid-file", required=True)
        command.add_argument("--pid", type=int, required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    if args.action == "monitor":
        return run_monitor(args)
    try:
        result = inspect_process(args.pid_file, args.run_id, args.pid)
        if args.action == "signal" and result["alive"]:
            os.kill(args.pid, signal.SIGTERM)
            result["signal_sent"] = "SIGTERM"
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["matches"] else 2
    except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(json.dumps({"run_id": args.run_id, "pid": args.pid,
                          "matches": False, "error": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())

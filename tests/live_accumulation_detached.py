"""Identity-bound detached lifecycle for the existing SCADA accumulation runner.

The launch and control subcommands never write PLC or start a package. Only
``worker`` after an identity-bound authorization can invoke the live runner.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
CASES = ("smoke", "lane_hold", "merge_hold", "drive_stop", "normal")
REPO_RUNNER = Path(__file__).with_name("live_accumulation.py")
STOP = False


def stamp():
    return {"wall_utc": datetime.now(timezone.utc).isoformat(),
            "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def ticks(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw[raw.rfind(")") + 2:].split()[19])


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def paths(directory):
    root = Path(directory)
    return {name: root / (name + ".json") for name in
            ("pid", "ready", "authorize", "begin", "checkpoint", "release", "terminal")}


def identity(run_id, case, pid=None, start_ticks=None):
    return {"run_id": run_id, "scenario": case,
            "pid": pid if pid is not None else os.getpid(),
            "start_ticks": start_ticks if start_ticks is not None else ticks(os.getpid())}


def validate_identity(record, expected):
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("lifecycle identity mismatch")
    return record


def inspect(directory, expected, script=None):
    record = validate_identity(json.loads(paths(directory)["pid"].read_text()), expected)
    pid = record["pid"]
    try:
        proc = Path(f"/proc/{pid}")
        if proc.stat().st_uid != os.getuid() or ticks(pid) != record["start_ticks"]:
            raise ValueError("PID owner or start ticks mismatch")
        if (proc / "stat").read_text().split(")", 1)[1].split()[0] == "Z":
            return {**record, "alive": False, "matches": False}
        argv = [part.decode(errors="replace") for part in
                (proc / "cmdline").read_bytes().split(b"\0") if part]
        if (str(Path(script or __file__).resolve()) not in argv or "worker" not in argv or
            "--run-id" not in argv or argv[argv.index("--run-id") + 1] != expected["run_id"]):
            raise ValueError("PID command line does not match canonical worker")
        return {**record, "alive": True, "matches": True}
    except FileNotFoundError:
        return {**record, "alive": False, "matches": False}


def stop_signal(_signal, _frame):
    global STOP
    STOP = True
    raise InterruptedError("detached scenario aborted")


def wait_marker(path, expected, seconds, label):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if STOP:
            raise InterruptedError(f"{label} aborted")
        if path.exists():
            return validate_identity(json.loads(path.read_text()), expected)
        time.sleep(.1)
    raise TimeoutError(f"{label} timed out")


def preflight(case, runner_hash):
    import live_accumulation as live
    if case != "smoke" and case not in live.CASES:
        raise ValueError("unknown live case")
    actual = hashlib.sha256(REPO_RUNNER.read_bytes()).hexdigest()
    if actual != runner_hash:
        raise ValueError("deployed scenario runner hash mismatch")
    from pymodbus.client import ModbusTcpClient
    plc = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
    if not plc.connect():
        raise ConnectionError("PLC unavailable")
    try:
        if live.holding(plc, 249)[0] != 24115 or live.coils(plc, 880)[0]:
            raise ValueError("PLC identity or stopped-state precondition failed")
        if any(live.holding(plc, base + 4)[0] for base in (530, 542, 647)):
            raise ValueError("occupied PLC slot at startup")
        return {"plc_identity": 24115, "master": False, "slots_empty": True,
                "runner_sha256": actual}
    finally:
        plc.close()


def worker(args):
    global STOP
    STOP = False
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    folder = paths(args.directory)
    own = identity(args.run_id, args.case)
    original_error = None
    cleanup = {"errors": [], "original_failure": None}
    status = "error"
    try:
        atomic(folder["pid"], {**own, "repository_commit": args.commit,
                               "runner_sha256": args.runner_sha256, **stamp()})
        proof = preflight(args.case, args.runner_sha256)
        ready_stamp = stamp()
        atomic(folder["ready"], {**own, "repository_commit": args.commit,
                                 "preflight": proof, "mutation_started": False,
                                 "authorization_deadline_monotonic_ns":
                                 ready_stamp["monotonic_ns"] + int(args.startup_timeout * 1e9),
                                 **ready_stamp})
        wait_marker(folder["authorize"], own, args.startup_timeout, "authorization")
        wait_marker(folder["begin"], own, args.startup_timeout, "scenario begin")
        if args.case == "smoke":
            sample = {"smoke": True, "mutation_started": False,
                      "plc_identity": proof["plc_identity"]}
            atomic(folder["checkpoint"], {**own, "sample": sample, **stamp()})
            wait_marker(folder["release"], own, args.hold_timeout, "evidence release")
        else:
            import live_accumulation as live
            def hold(sample):
                if not live.checkpoint_valid(args.case, sample):
                    raise AssertionError("checkpoint is not PLC validated")
                atomic(folder["checkpoint"], {**own, "sample": sample, **stamp()})
                wait_marker(folder["release"], own, args.hold_timeout, "evidence release")
            live.run(args.case, evidence_hold=hold if args.case != "normal" else None,
                     cleanup_report=cleanup)
        status = "complete"
    except BaseException as exc:
        original_error = f"{type(exc).__name__}: {exc}"
        status = "timeout" if isinstance(exc, TimeoutError) else "scenario_failure"
    finally:
        if cleanup["errors"]:
            status = "cleanup_failure" if status == "complete" else status
        atomic(folder["terminal"], {**own, "status": status,
                                    "original_error": original_error,
                                    "cleanup": cleanup,
                                    "cleanup_failed": bool(cleanup["errors"]),
                                    **stamp()})
    return 0 if status == "complete" else 1


def launch(args):
    if not RUN_ID.fullmatch(args.run_id) or args.case not in CASES:
        raise ValueError("invalid run ID or scenario")
    if not 1 <= args.hold_timeout <= 60 or not 1 <= args.startup_timeout <= 120:
        raise ValueError("unbounded timeout")
    directory = Path(args.directory)
    directory.mkdir(mode=0o700)
    if any(directory.iterdir()):
        raise FileExistsError("run directory is not empty")
    cmd = [sys.executable, str(Path(__file__).resolve()), "worker",
           "--directory", str(directory), "--run-id", args.run_id,
           "--case", args.case, "--commit", args.commit,
           "--runner-sha256", args.runner_sha256,
           "--startup-timeout", str(args.startup_timeout),
           "--hold-timeout", str(args.hold_timeout)]
    with (directory / "stdout.log").open("x") as log:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   close_fds=True)
    print(json.dumps({"run_id": args.run_id, "scenario": args.case,
                      "pid": process.pid, "directory": str(directory)}, sort_keys=True))
    return 0


def marker_action(args):
    expected = identity(args.run_id, args.case, args.pid, args.start_ticks)
    state = inspect(args.directory, expected)
    folder = paths(args.directory)
    if args.action == "inspect":
        if folder["ready"].exists():
            ready = validate_identity(json.loads(folder["ready"].read_text()), expected)
            deadline = ready.get("authorization_deadline_monotonic_ns")
            if deadline is not None:
                state["authorization_remaining_seconds"] = max(
                    0.0, (deadline - time.monotonic_ns()) / 1e9)
        print(json.dumps(state, sort_keys=True))
        return 0
    if args.action == "abort":
        if state["alive"] and state["matches"]:
            os.kill(args.pid, signal.SIGTERM)
        print(json.dumps({**state, "abort_sent": state["alive"]}, sort_keys=True))
        return 0
    if args.action in ("authorize", "begin", "release"):
        if not state["alive"] or not state["matches"]:
            raise RuntimeError("worker is not live")
        if args.action == "authorize" and not folder["ready"].exists():
            raise RuntimeError("runner is not ready")
        if args.action == "begin" and not folder["authorize"].exists():
            raise RuntimeError("runner is not authorized")
        if args.action == "release" and not folder["checkpoint"].exists():
            raise RuntimeError("checkpoint is absent")
        atomic(folder[args.action], {**expected, **stamp()})
        print(json.dumps({**expected, "action": args.action}, sort_keys=True))
        return 0
    raise ValueError(args.action)


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("launch", "worker"):
        p = sub.add_parser(name)
        p.add_argument("--directory", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--case", required=True, choices=CASES)
        p.add_argument("--commit", required=True)
        p.add_argument("--runner-sha256", required=True)
        p.add_argument("--startup-timeout", type=float, default=120)
        p.add_argument("--hold-timeout", type=float, default=30)
    for name in ("inspect", "authorize", "begin", "release", "abort"):
        p = sub.add_parser(name)
        p.add_argument("--directory", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--case", required=True)
        p.add_argument("--pid", type=int, required=True)
        p.add_argument("--start-ticks", type=int, required=True)
    args = parser.parse_args(argv)
    return launch(args) if args.action == "launch" else worker(args) if args.action == "worker" else marker_action(args)


if __name__ == "__main__":
    sys.exit(main())

"""Bounded drives-side plant fixture supervisor for Phase 2A validation.

Only the fixed sorter-plant.service stop/start commands need narrowly scoped
sudo -n authorization. A deadline and finally block restore the service even
if the host controller disconnects. This helper has no PLC write path.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from live_accumulation_detached import RUN_ID, atomic, identity, inspect, stamp, ticks

PLANT = "/home/kevin/plant.py"
PYTHON = "/home/kevin/venv/bin/python"
SERVICE = "sorter-plant.service"
SYSTEMCTL = "/usr/bin/systemctl"
STOP_ARGV = ("sudo", "-n", SYSTEMCTL, "stop", SERVICE)
START_ARGV = ("sudo", "-n", SYSTEMCTL, "start", SERVICE)
MAX_AUTH_SECONDS = 180
APPROVAL_LEDGER = Path.home() / ".local/state/sorter-fixture-approvals.jsonl"
STOP = False


def interrupted(_signum, _frame):
    global STOP
    STOP = True


def systemctl(action):
    if action not in ("stop", "start"):
        raise ValueError("fixture service action must be stop or start")
    argv = STOP_ARGV if action == "stop" else START_ARGV
    return subprocess.run(list(argv),
                          capture_output=True, text=True, timeout=15, check=True)


def service_active():
    return subprocess.run([SYSTEMCTL, "is-active", "--quiet", SERVICE],
                          capture_output=True, timeout=10, check=False).returncode == 0


def authorization_path(directory):
    return Path(directory) / "authorization.json"


def claim_approval_receipt(approval_id, run_id, case):
    """Durably prevent a receipt from authorizing a second run or scenario."""
    ledger = Path(APPROVAL_LEDGER)
    ledger.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256(approval_id.encode()).hexdigest()
    descriptor = os.open(ledger, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        for line in stream:
            if json.loads(line)["approval_sha256"] == digest:
                raise ValueError("operator approval receipt was already used")
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps({"approval_sha256": digest, "run_id": run_id,
                                 "scenario": case, "recorded_wall_ns": time.time_ns()},
                                sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)


def authorize(directory, run_id, case, approval_id, valid_for):
    """Record the already granted, one-run operator approval on drives."""
    if not RUN_ID.fullmatch(run_id) or case not in ("lane_hold", "merge_hold"):
        raise ValueError("invalid fixture run or scenario")
    if not RUN_ID.fullmatch(approval_id) or not 30 <= valid_for <= MAX_AUTH_SECONDS:
        raise ValueError("invalid approval receipt or expiry")
    root = Path(directory)
    root.mkdir(mode=0o700)
    if any(root.iterdir()):
        raise FileExistsError("fixture authorization directory is not empty")
    if not service_active() or len(count_unflagged()) != 1:
        raise RuntimeError("canonical plant service/process must be active before approval record")
    claim_approval_receipt(approval_id, run_id, case)
    now = time.time_ns()
    record = {"version": 1, "authorization_id": uuid.uuid4().hex,
              "approval_id": approval_id, "run_id": run_id, "scenario": case,
              "guest_directory": str(root.resolve()),
              "unit": SERVICE, "stop_argv": list(STOP_ARGV),
              "start_argv": list(START_ARGV), "authorized_wall_ns": now,
              "expires_wall_ns": now + int(valid_for * 1_000_000_000),
              "initial_service_state": "active", "initial_unflagged_processes": 1,
              "scope": "one stop and its restorative start for this run only"}
    atomic(authorization_path(root), record)
    return record


def validate_authorization(directory, run_id, case, *, require_fresh=True):
    """Reject malformed or reused scope before any service mutation."""
    record = json.loads(authorization_path(directory).read_text())
    required = {"version", "authorization_id", "approval_id", "run_id", "scenario",
                "guest_directory",
                "unit", "stop_argv", "start_argv", "authorized_wall_ns",
                "expires_wall_ns", "initial_service_state", "initial_unflagged_processes",
                "scope"}
    if set(record) != required:
        raise ValueError("malformed fixture authorization fields")
    if (type(record["version"]) is not int or record["version"] != 1 or
        record["run_id"] != run_id or
        record["scenario"] != case or record["unit"] != SERVICE or
        record["guest_directory"] != str(Path(directory).resolve()) or
        record["stop_argv"] != list(STOP_ARGV) or
        record["start_argv"] != list(START_ARGV) or
        record["initial_service_state"] != "active" or
        type(record["initial_unflagged_processes"]) is not int or
        record["initial_unflagged_processes"] != 1 or
        record["scope"] != "one stop and its restorative start for this run only" or
        not isinstance(record["authorization_id"], str) or
        not RUN_ID.fullmatch(record["authorization_id"]) or
        not isinstance(record["approval_id"], str) or
        not RUN_ID.fullmatch(record["approval_id"]) or
        type(record["authorized_wall_ns"]) is not int or
        type(record["expires_wall_ns"]) is not int):
        raise ValueError("fixture authorization binding mismatch")
    issued, expires = record["authorized_wall_ns"], record["expires_wall_ns"]
    if expires <= issued or expires - issued > MAX_AUTH_SECONDS * 1_000_000_000:
        raise ValueError("fixture authorization expiry is malformed")
    if require_fresh and not issued <= time.time_ns() < expires:
        raise ValueError("fixture authorization is stale or not yet valid")
    if require_fresh and (Path(directory) / "claimed.json").exists():
        raise ValueError("fixture authorization was already used")
    return record


def count_unflagged():
    output = subprocess.run(["pgrep", "-af", "^/home/kevin/venv/bin/python /home/kevin/plant.py"],
                            capture_output=True, text=True, check=False)
    return [line for line in output.stdout.splitlines() if "--block-" not in line]


def fixture_command(case):
    if case == "lane_hold":
        return [PYTHON, PLANT, "--block-lane", "1", "--block-zone", "premerge",
                "--block-after-token", "1", "--block-duration", "60"]
    if case == "merge_hold":
        return [PYTHON, PLANT, "--block-merge", "--block-after-token", "1",
                "--block-duration", "60"]
    raise ValueError("no plant fixture for scenario")


def work(args):
    global STOP
    STOP = False
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    root = Path(args.directory)
    own = identity(args.run_id, args.case)
    child = None
    stop_attempted = False
    error = None
    cleanup_errors = []
    authorization_id = None
    started = time.monotonic()
    atomic(root / "pid.json", {**own, **stamp()})
    try:
        approved = validate_authorization(root, args.run_id, args.case,
                                          require_fresh=False)
        claim = json.loads((root / "claimed.json").read_text())
        if (claim.get("authorization_id") != approved["authorization_id"] or
            claim.get("run_id") != args.run_id or claim.get("scenario") != args.case):
            raise ValueError("fixture authorization claim mismatch")
        if not approved["authorized_wall_ns"] <= time.time_ns() < approved["expires_wall_ns"]:
            raise ValueError("fixture authorization expired before stop")
        authorization_id = approved["authorization_id"]
        if not service_active() or len(count_unflagged()) != 1:
            raise RuntimeError("normal plant service not initially active")
        stop_attempted = True
        systemctl("stop")
        if STOP:
            raise InterruptedError("fixture aborted after service stop")
        with (root / "plant.log").open("x") as log:
            child = subprocess.Popen(fixture_command(args.case), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
        atomic(root / "ready.json", {**own, "fixture_pid": child.pid,
                                     "fixture_start_ticks": ticks(child.pid),
                                     "authorization_id": authorization_id, **stamp()})
        while not STOP and time.monotonic() - started < args.duration:
            if child.poll() is not None:
                raise RuntimeError(f"plant fixture exited {child.returncode}")
            time.sleep(.1)
        if not STOP:
            raise TimeoutError("fixture supervisor deadline")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        child_running = False
        if child is not None:
            try:
                child_running = child.poll() is None
            except Exception as exc:
                cleanup_errors.append(f"fixture poll: {exc}")
                child_running = True
        if child_running:
            try:
                child.terminate(); child.wait(timeout=10)
            except Exception as exc:
                cleanup_errors.append(f"fixture stop: {exc}")
                try:
                    child.kill(); child.wait(timeout=5)
                except Exception as kill_exc:
                    cleanup_errors.append(f"fixture kill: {kill_exc}")
        if stop_attempted:
            try:
                systemctl("start")
            except Exception as exc:
                cleanup_errors.append(f"service start: {exc}")
        try:
            if not service_active():
                cleanup_errors.append("normal plant service inactive")
        except Exception as exc:
            cleanup_errors.append(f"service status: {exc}")
        try:
            process_deadline = time.monotonic() + 8
            normal = count_unflagged()
            while len(normal) != 1 and time.monotonic() < process_deadline:
                time.sleep(.1)
                normal = count_unflagged()
            if len(normal) != 1:
                cleanup_errors.append(f"expected exactly one unflagged plant: {normal}")
        except Exception as exc:
            cleanup_errors.append(f"plant process status: {exc}")
        atomic(root / "terminal.json", {**own, "status": "complete" if not error and not cleanup_errors else "error",
                                        "error": error, "cleanup_errors": cleanup_errors,
                                        "authorization_id": authorization_id,
                                        "reason": "signal" if STOP else "deadline" if error and "TimeoutError" in error else "failure" if error else "complete",
                                        "restoration_attempted": stop_attempted,
                                        "service_restored": not cleanup_errors, **stamp()})
    return 0 if not error and not cleanup_errors else 1


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    authorization = sub.add_parser("authorize")
    authorization.add_argument("--directory", required=True)
    authorization.add_argument("--run-id", required=True)
    authorization.add_argument("--case", choices=("lane_hold", "merge_hold"), required=True)
    authorization.add_argument("--approval-id", required=True)
    authorization.add_argument("--valid-for", type=float, default=180)
    launch = sub.add_parser("launch")
    launch.add_argument("--directory", required=True)
    launch.add_argument("--run-id", required=True)
    launch.add_argument("--case", choices=("lane_hold", "merge_hold"), required=True)
    launch.add_argument("--duration", type=float, default=150)
    for action in ("worker", "inspect", "stop"):
        p = sub.add_parser(action)
        p.add_argument("--directory", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--case", choices=("lane_hold", "merge_hold"), required=True)
        if action == "worker":
            p.add_argument("--duration", type=float, required=True)
        else:
            p.add_argument("--pid", type=int, required=True)
            p.add_argument("--start-ticks", type=int, required=True)
    args = cli.parse_args(argv)
    if args.action == "authorize":
        print(json.dumps(authorize(args.directory, args.run_id, args.case,
                                   args.approval_id, args.valid_for), sort_keys=True))
        return 0
    if args.action == "launch":
        if not 30 <= args.duration <= 180:
            raise ValueError("unbounded fixture duration")
        root = Path(args.directory)
        approved = validate_authorization(root, args.run_id, args.case)
        if set(path.name for path in root.iterdir()) != {"authorization.json"}:
            raise FileExistsError("fixture authorization directory has unexpected data")
        atomic(root / "claimed.json", {"run_id": args.run_id,
                                       "scenario": args.case,
                                       "authorization_id": approved["authorization_id"],
                                       **stamp()})
        argv = [sys.executable, str(Path(__file__).resolve()), "worker", "--directory", str(root),
                "--run-id", args.run_id, "--case", args.case, "--duration", str(args.duration)]
        with (root / "stdout.log").open("x") as log:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True,
                                     close_fds=True)
        print(json.dumps({"run_id": args.run_id, "pid": child.pid}))
        return 0
    if args.action == "worker":
        return work(args)
    expected = identity(args.run_id, args.case, args.pid, args.start_ticks)
    state = inspect(args.directory, expected, script=__file__)
    if args.action == "stop" and state["alive"]:
        os.kill(args.pid, signal.SIGTERM)
    print(json.dumps({**state, "stop_sent": args.action == "stop" and state["alive"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

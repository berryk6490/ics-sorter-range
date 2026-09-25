"""Bounded drives-side plant fixture supervisor for Phase 2A validation.

Only the fixed sorter-plant.service stop/start commands need narrowly scoped
sudo -n authorization. A deadline and finally block restore the service even
if the host controller disconnects. This helper has no PLC write path.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from live_accumulation_detached import atomic, identity, inspect, paths, stamp, ticks

PLANT = "/home/kevin/plant.py"
PYTHON = "/home/kevin/venv/bin/python"
SERVICE = "sorter-plant.service"
STOP = False


def interrupted(_signum, _frame):
    global STOP
    STOP = True


def systemctl(action):
    return subprocess.run(["sudo", "-n", "systemctl", action, SERVICE],
                          capture_output=True, text=True, timeout=15, check=True)


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
    service_stopped = False
    error = None
    cleanup_errors = []
    started = time.monotonic()
    atomic(root / "pid.json", {**own, **stamp()})
    try:
        if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode:
            raise RuntimeError("normal plant service not initially active")
        systemctl("stop")
        service_stopped = True
        with (root / "plant.log").open("x") as log:
            child = subprocess.Popen(fixture_command(args.case), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
        atomic(root / "ready.json", {**own, "fixture_pid": child.pid,
                                     "fixture_start_ticks": ticks(child.pid), **stamp()})
        while not STOP and time.monotonic() - started < args.duration:
            if child.poll() is not None:
                raise RuntimeError(f"plant fixture exited {child.returncode}")
            time.sleep(.1)
        if not STOP:
            raise TimeoutError("fixture supervisor deadline")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if child is not None and child.poll() is None:
            try:
                child.terminate(); child.wait(timeout=10)
            except Exception as exc:
                cleanup_errors.append(f"fixture stop: {exc}")
                child.kill(); child.wait(timeout=5)
        if service_stopped:
            try:
                systemctl("start")
            except Exception as exc:
                cleanup_errors.append(f"service start: {exc}")
        if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode:
            cleanup_errors.append("normal plant service inactive")
        process_deadline = time.monotonic() + 8
        normal = count_unflagged()
        while len(normal) != 1 and time.monotonic() < process_deadline:
            time.sleep(.1)
            normal = count_unflagged()
        if len(normal) != 1:
            cleanup_errors.append(f"expected exactly one unflagged plant: {normal}")
        atomic(root / "terminal.json", {**own, "status": "complete" if not error and not cleanup_errors else "error",
                                        "error": error, "cleanup_errors": cleanup_errors,
                                        "service_restored": not cleanup_errors, **stamp()})
    return 0 if not error and not cleanup_errors else 1


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
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
    if args.action == "launch":
        if not 30 <= args.duration <= 180:
            raise ValueError("unbounded fixture duration")
        root = Path(args.directory)
        root.mkdir(mode=0o700)
        if any(root.iterdir()):
            raise FileExistsError("fixture directory exists with data")
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

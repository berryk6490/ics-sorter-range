"""Host control of the drives readiness monitor over the existing serial shell.

No guest route or bridge address is added. Only the repository monitor script
may be launched or signalled. The separate ``probe`` gate must pass before
``scenario`` can start the canonical SCADA accumulation runner.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

import pexpect

GUEST_PYTHON = "/home/kevin/venv/bin/python"
GUEST_MONITOR = "/home/kevin/live_accumulation_monitor.py"
SCADA_PYTHON = "/home/kevin/opcua/bin/python"
SCADA_RUNNER = "/home/kevin/sorter-services/live_accumulation.py"
CASES = ("normal", "lane_hold", "merge_hold", "drive_stop")
MAX_EVIDENCE_BYTES = 8_000_000


class GuestCommandError(RuntimeError):
    def __init__(self, code, output):
        super().__init__(f"guest command exited {code}: {output[-700:]}")
        self.code = code
        self.output = output


class SerialGuest:
    """One bounded command per attachment; leaves the guest login shell alive."""

    def __init__(self, vm):
        self.vm = vm

    def _run_shell(self, command, timeout=20):
        marker = "__MON_RC_" + uuid.uuid4().hex + "__"
        tty = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", self.vm],
                            encoding="utf-8", timeout=timeout, maxread=200000)
        echo_disabled = False
        try:
            tty.expect("Escape character")
            tty.sendline("")
            if tty.expect([rf"kevin@{self.vm}:.*\$ ", "login: "]) == 1:
                raise RuntimeError(f"Log in on {self.vm} serial console first")
            tty.sendline("stty -echo")
            tty.expect(rf"kevin@{self.vm}:.*\$ ")
            echo_disabled = True
            tty.sendline(command + f"; printf '{marker}%s\\n' \"$?\"")
            tty.expect(re.escape(marker) + r"(\d+)", timeout=timeout)
            output = tty.before.strip()
            code = int(tty.match.group(1))
            tty.expect(rf"kevin@{self.vm}:.*\$ ")
            tty.sendline("stty echo")
            tty.expect(rf"kevin@{self.vm}:.*\$ ")
            echo_disabled = False
            if code:
                raise GuestCommandError(code, output)
            return output
        finally:
            if echo_disabled:
                try:
                    tty.sendline("stty echo")
                    tty.expect(rf"kevin@{self.vm}:.*\$ ", timeout=2)
                except (pexpect.ExceptionPexpect, OSError):
                    pass
            tty.send("\x1d")
            tty.close()

    def run(self, argv, timeout=20):
        return self._run_shell(shlex.join([str(value) for value in argv]), timeout)

    def launch(self, argv, log_path):
        command = ("nohup " + shlex.join([str(value) for value in argv]) +
                   " > " + shlex.quote(str(log_path)) + " 2>&1 < /dev/null & " +
                   "printf '__MONITOR_LAUNCHED__%s\\n' \"$!\"")
        output = self._run_shell(command, 20)
        match = re.search(r"__MONITOR_LAUNCHED__(\d+)", output)
        if not match or int(match.group(1)) <= 0:
            raise RuntimeError(f"no guest monitor PID: {output}")
        return int(match.group(1))

    def read_file(self, path):
        size = int(self.run(["stat", "-c", "%s", "--", path]).splitlines()[-1])
        if size > MAX_EVIDENCE_BYTES:
            raise ValueError(f"guest evidence exceeds {MAX_EVIDENCE_BYTES} bytes: {path}")
        if size == 0:
            return b""
        output = self.run(["base64", "-w0", "--", path], timeout=45)
        # Bash emits a bracketed-paste mode escape on this serial terminal.
        # It precedes the base64 payload even with command echo disabled.
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)
        encoded = clean.splitlines()[-1] if clean else ""
        data = base64.b64decode(encoded, validate=True)
        # A live JSONL monitor can append after stat but before base64.
        # Shrinkage is invalid; appended bytes are expected during probe.
        if len(data) < size:
            raise IOError(f"guest file shrank during transfer: {path}")
        return data


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_control(path, value):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_control(path):
    data = json.loads(Path(path).read_text())
    if data["version"] != 1 or data["pid"] <= 0 or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,96}", data["run_id"]):
        raise ValueError("invalid monitor control record")
    return data


class Controller:
    def __init__(self, drives=None, scada=None):
        self.drives = drives or SerialGuest("drives")
        self.scada = scada or SerialGuest("scada")

    def start(self, evidence_dir, plc_host="10.10.1.10", plc_port=502,
              duration=300, interval=.12, guest_base="/tmp",
              guest_python=GUEST_PYTHON, guest_monitor=GUEST_MONITOR):
        if not 0.2 <= duration <= 600 or not .05 <= interval <= 5:
            raise ValueError("unbounded monitor duration or interval")
        if not 1 <= plc_port <= 65535:
            raise ValueError("invalid PLC port")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex
        root = Path(evidence_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        local = root / run_id
        local.mkdir(mode=0o700)
        guest_dir = str(Path(guest_base) / ("sorter-accumulation-monitor-" + run_id))
        paths = {name: str(Path(guest_dir) / filename) for name, filename in {
            "output": "samples.jsonl", "ready": "ready.json", "pid_file": "pid.json",
            "result": "terminal.json", "log": "monitor.log"}.items()}
        self.drives.run(["mkdir", "-m", "700", "--", guest_dir])
        argv = [guest_python, guest_monitor, "monitor", "--plc-host", plc_host,
                "--plc-port", str(plc_port), "--run-id", run_id,
                "--output", paths["output"], "--ready", paths["ready"],
                "--pid-file", paths["pid_file"], "--result", paths["result"],
                "--duration", str(duration), "--interval", str(interval)]
        pid = self.drives.launch(argv, paths["log"])
        control = {"version": 1, "run_id": run_id, "pid": pid,
                   "plc_host": plc_host, "plc_port": plc_port,
                   "guest_python": guest_python, "guest_monitor": guest_monitor,
                   "guest_dir": guest_dir, "paths": paths, "local_dir": str(local),
                   "launched_utc": utc(), "duration": duration, "interval": interval}
        control_path = local / "control.json"
        atomic_control(control_path, control)
        return control_path

    def _inspect(self, control):
        argv = [control["guest_python"], control["guest_monitor"], "inspect",
                "--run-id", control["run_id"], "--pid-file",
                control["paths"]["pid_file"], "--pid", str(control["pid"])]
        try:
            output = self.drives.run(argv)
        except GuestCommandError as exc:
            output = exc.output
        return json.loads(output.splitlines()[-1])

    def _file(self, control, name):
        return self.drives.read_file(control["paths"][name])

    def probe(self, control_path, timeout=12):
        control = load_control(control_path)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                pid_file = json.loads(self._file(control, "pid_file"))
            except (FileNotFoundError, GuestCommandError):
                time.sleep(.1)
                continue
            if pid_file.get("run_id") != control["run_id"] or pid_file.get("pid") != control["pid"]:
                raise ValueError("stale or mismatched PID file")
            process = self._inspect(control)
            if not process.get("matches") or not process.get("alive"):
                raise RuntimeError(f"monitor exited before readiness: {process}")
            try:
                terminal = json.loads(self._file(control, "result"))
            except (FileNotFoundError, GuestCommandError):
                terminal = None
            if terminal is not None:
                raise RuntimeError(f"monitor completed before readiness: {terminal}")
            try:
                marker = json.loads(self._file(control, "ready"))
            except (FileNotFoundError, GuestCommandError):
                time.sleep(.1)
                continue
            if (marker.get("run_id") != control["run_id"] or
                marker.get("pid") != control["pid"] or
                marker.get("start_ticks") != pid_file.get("start_ticks")):
                raise ValueError("stale or mismatched ready marker")
            snapshot = self._file(control, "output")
            complete = snapshot.rsplit(b"\n", 1)[0] if not snapshot.endswith(b"\n") else snapshot
            records = [json.loads(line) for line in complete.splitlines()]
            if any(row.get("run_id") != control["run_id"] for row in records):
                raise ValueError("JSONL record run ID mismatch")
            initial = [row for row in records if row.get("event") == "initial_sample"]
            ready_events = [row for row in records if row.get("event") == "monitor_ready"]
            if len(initial) != 1 or len(ready_events) != 1:
                raise ValueError("missing durable initial sample or monitor_ready event")
            line = json.dumps(initial[0], sort_keys=True, separators=(",", ":")) + "\n"
            if hashlib.sha256(line.encode()).hexdigest() != marker.get("initial_sample_sha256"):
                raise ValueError("initial sample does not match ready marker")
            if initial[0]["sample"]["run_id"] != control["run_id"]:
                raise ValueError("initial sample has wrong run ID")
            if any(row["event"] in ("monitor_stopping", "monitor_complete", "monitor_error")
                   for row in records):
                raise RuntimeError("monitor ended before scenario launch")
            if not self._inspect(control).get("alive"):
                raise RuntimeError("monitor exited during readiness probe")
            proof = {"run_id": control["run_id"], "pid": control["pid"],
                     "observed_utc": utc(), "observed_monotonic_ns": time.monotonic_ns(),
                     "marker": marker, "initial_sample": initial[0]}
            atomic_control(Path(control["local_dir"]) / "ready-proof.json", proof)
            return proof
        raise TimeoutError("monitor PID, marker, and initial sample not ready")

    def collect(self, control_path, timeout=15, stop=True):
        control = load_control(control_path)
        result = {"run_id": control["run_id"], "pid": control["pid"],
                  "stop_requested": stop, "cleanup_errors": []}
        local = Path(control["local_dir"])
        try:
            # Launch may return before the guest has written its PID file.
            # Wait for ownership evidence before attempting any signal.
            pid_deadline = time.monotonic() + timeout
            while time.monotonic() < pid_deadline:
                try:
                    pid_record = json.loads(self._file(control, "pid_file"))
                    break
                except (FileNotFoundError, GuestCommandError):
                    time.sleep(.1)
            else:
                raise TimeoutError("monitor PID file was never created")
            if (pid_record.get("run_id") != control["run_id"] or
                pid_record.get("pid") != control["pid"]):
                raise ValueError("PID ownership record mismatch")
            process = self._inspect(control)
            if process.get("alive"):
                if not process.get("matches"):
                    raise RuntimeError("PID ownership mismatch; refusing signal")
                if stop:
                    self.drives.run([control["guest_python"], control["guest_monitor"],
                                     "signal", "--run-id", control["run_id"],
                                     "--pid-file", control["paths"]["pid_file"],
                                     "--pid", str(control["pid"])])
            elif process.get("error") or process.get("run_id") != control["run_id"]:
                raise RuntimeError(f"PID ownership cannot be established: {process}")
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                status = self._inspect(control)
                if status.get("error"):
                    raise RuntimeError(f"monitor PID identity became uncertain: {status}")
                try:
                    terminal = json.loads(self._file(control, "result"))
                except (FileNotFoundError, GuestCommandError):
                    terminal = None
                if terminal and not status.get("alive"):
                    break
                time.sleep(.1)
            else:
                raise TimeoutError("monitor did not exit with a terminal result")
            if (terminal.get("run_id") != control["run_id"] or
                terminal.get("pid") != control["pid"] or
                terminal.get("start_ticks") != pid_record.get("start_ticks")):
                raise ValueError("terminal marker identity mismatch")
            records_data = self._file(control, "output")
            records = [json.loads(line) for line in records_data.splitlines()]
            if not records or any(row.get("run_id") != control["run_id"] for row in records):
                raise ValueError("JSONL run ID mismatch")
            if records[-1]["event"] not in ("monitor_complete", "monitor_error"):
                raise ValueError("missing JSONL terminal event")
            if terminal["status"] == "complete" and records[-1]["event"] != "monitor_complete":
                raise ValueError("terminal/JSONL disagreement")
            if terminal["status"] == "error" and records[-1]["event"] != "monitor_error":
                raise ValueError("terminal/JSONL disagreement")
            (local / "samples.jsonl").write_bytes(records_data)
            (local / "terminal.json").write_text(json.dumps(terminal, sort_keys=True, indent=2) + "\n")
            (local / "monitor.log").write_bytes(self._file(control, "log"))
            result.update({"terminal": terminal, "orphan": False,
                           "record_count": len(records), "collected_utc": utc()})
            if terminal["status"] != "complete":
                raise RuntimeError(f"monitor terminal status: {terminal['status']}")
        except Exception as exc:
            result["cleanup_errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            (local / "cleanup.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        return result

    def scenario(self, control_path, case, probe_timeout=12):
        if case not in CASES:
            raise ValueError("unknown accumulation scenario")
        control = load_control(control_path)
        local = Path(control["local_dir"])
        failure = None
        try:
            proof = self.probe(control_path, probe_timeout)
            launched = {"run_id": control["run_id"], "case": case,
                        "monitor_ready_observed_monotonic_ns": proof["observed_monotonic_ns"],
                        "scenario_launch_monotonic_ns": time.monotonic_ns(),
                        "scenario_launch_utc": utc()}
            atomic_control(local / "scenario-launch.json", launched)
            output = self.scada.run([SCADA_PYTHON, SCADA_RUNNER, case], timeout=330)
            (local / "scenario-output.txt").write_text(output + "\n")
            if not self._inspect(control).get("alive"):
                raise RuntimeError("monitor exited before scenario completed")
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, GuestCommandError):
                (local / "scenario-output.txt").write_text(exc.output + "\n")
        cleanup = self.collect(control_path, timeout=20, stop=True)
        outcome = {"run_id": control["run_id"], "case": case,
                   "scenario_failure": failure, "monitor_cleanup": cleanup}
        (local / "scenario-result.json").write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n")
        return outcome


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start")
    start.add_argument("--evidence-dir", type=Path, required=True)
    start.add_argument("--plc-host", default="10.10.1.10")
    start.add_argument("--plc-port", type=int, default=502)
    start.add_argument("--duration", type=float, default=300)
    start.add_argument("--interval", type=float, default=.12)
    for action in ("probe", "collect", "stop"):
        command = sub.add_parser(action)
        command.add_argument("--control", type=Path, required=True)
        command.add_argument("--timeout", type=float, default=12)
    scenario = sub.add_parser("scenario")
    scenario.add_argument("--control", type=Path, required=True)
    scenario.add_argument("--case", choices=CASES, required=True)
    args = cli.parse_args(argv)
    manager = Controller()
    if args.action == "start":
        path = manager.start(args.evidence_dir, args.plc_host, args.plc_port,
                             args.duration, args.interval)
        control = load_control(path)
        result = {"control": str(path), "run_id": control["run_id"], "pid": control["pid"]}
    elif args.action == "probe":
        result = manager.probe(args.control, args.timeout)
    elif args.action in ("collect", "stop"):
        result = manager.collect(args.control, args.timeout, stop=args.action == "stop")
        if result["cleanup_errors"]:
            print(json.dumps(result, sort_keys=True), flush=True)
            return 1
    else:
        result = manager.scenario(args.control, args.case)
        if result["scenario_failure"] or result["monitor_cleanup"]["cleanup_errors"]:
            print(json.dumps(result, sort_keys=True), flush=True)
            return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

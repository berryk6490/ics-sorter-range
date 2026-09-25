"""Host control of detached SCADA validation, over the existing serial shell."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.request import urlopen

from accumulation_monitor_control import (Controller as MonitorController, SerialGuest,
                                          SCADA_PYTHON, GuestCommandError,
                                          atomic_control, load_control)
from accumulation_state_snapshot import capture as typed_capture, compare as typed_compare, save as typed_save

GUEST = "/home/kevin/sorter-services/live_accumulation_detached.py"
RUNNER = "/home/kevin/sorter-services/live_accumulation.py"
DRIVES_PYTHON = "/home/kevin/venv/bin/python"
DRIVES_STATE = "/home/kevin/read_accumulation_state.py"
DRIVES_FIXTURE = "/home/kevin/live_accumulation_fixture.py"
FIXTURE_UNIT = "sorter-plant.service"
FIXTURE_STOP = ["sudo", "-n", "/usr/bin/systemctl", "stop", FIXTURE_UNIT]
FIXTURE_START = ["sudo", "-n", "/usr/bin/systemctl", "start", FIXTURE_UNIT]
CASES = ("smoke", "lane_hold", "merge_hold", "drive_stop", "normal")
RESTORATION_GATE = Path.home() / "vm" / "sorter-evidence" / "phase2a-restoration-gate.json"


def validate_typed_launch(evidence_root, typed_baseline, monitor_control):
    gate = RESTORATION_GATE
    if gate.exists() and json.loads(gate.read_text()).get("status") != "PASS":
        raise ValueError("previous scenario has no passing typed restoration comparison")
    baseline = json.loads(Path(typed_baseline).read_text())
    baseline_time = datetime.fromisoformat(baseline["captured_utc"])
    monitor_time = datetime.fromisoformat(load_control(monitor_control)["launched_utc"])
    age = (datetime.now(timezone.utc) - baseline_time).total_seconds()
    if not 0 <= age <= 600 or baseline_time >= monitor_time:
        raise ValueError("typed baseline must be fresh and precede monitor launch")
    baseline_report = typed_compare(baseline, baseline)
    if baseline_report["status"] != "PASS":
        raise ValueError(f"unsafe typed baseline: {baseline_report['differences']}")
    return baseline, baseline_report


def identity(control):
    return {"run_id": control["run_id"], "scenario": control["scenario"],
            "pid": control["pid"], "start_ticks": control["start_ticks"]}


def valid(row, control):
    if any(row.get(k) != v for k, v in identity(control).items()):
        raise ValueError("detached lifecycle identity mismatch")
    return row


class ScenarioController:
    def __init__(self, scada=None, drives=None, monitor=None):
        self.scada = scada or SerialGuest("scada")
        self.drives = drives or SerialGuest("drives")
        self.monitor = monitor or MonitorController(drives=self.drives, scada=self.scada)

    def _file(self, control, name):
        data = self.scada.read_file(f"{control['guest_dir']}/{name}")
        return json.loads(data)

    def state(self):
        return json.loads(self.drives.run([DRIVES_PYTHON, DRIVES_STATE]).splitlines()[-1])

    def _inspect(self, control):
        args = [SCADA_PYTHON, GUEST, "inspect", "--directory", control["guest_dir"],
                "--run-id", control["run_id"], "--case", control["scenario"],
                "--pid", control["pid"], "--start-ticks", control["start_ticks"]]
        return valid(json.loads(self.scada.run(args).splitlines()[-1]), control)

    def launch(self, evidence_dir, case, monitor_control, typed_baseline,
               startup_timeout=30, hold_timeout=30):
        if case not in CASES or not 1 <= startup_timeout <= 60 or not 1 <= hold_timeout <= 60:
            raise ValueError("invalid scenario or timeout")
        manifest = json.loads(Path("deploy/deployment_manifest.json").read_text())
        expected = next(c["sha256"] for c in manifest["components"]
                        if c["repository_source"] == "tests/live_accumulation.py")
        if hashlib.sha256(Path("tests/live_accumulation.py").read_bytes()).hexdigest() != expected:
            raise ValueError("local runner does not match deployment manifest")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex
        evidence_root = Path(evidence_dir).resolve()
        gate = RESTORATION_GATE
        baseline, baseline_report = validate_typed_launch(evidence_root, typed_baseline,
                                                          monitor_control)
        local = evidence_root / run_id
        local.mkdir(parents=True, mode=0o700)
        typed_save(local / "typed-before.json", baseline)
        typed_save(local / "typed-baseline-check.json", baseline_report)
        gate.parent.mkdir(parents=True, exist_ok=True)
        atomic_control(gate, {"status": "PENDING", "run_id": run_id,
                              "baseline": str(local / "typed-before.json")})
        initial = self.state()
        if initial["identity"] != 24113 or initial["coils_880_920"][0] or any(
                row[4] for row in initial["slots"]):
            raise ValueError("PLC is not at a stopped, empty baseline")
        atomic_control(local / "initial-state.json", initial)
        guest_dir = f"/tmp/sorter-accumulation-scenario-{run_id}"
        commit = __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        argv = [SCADA_PYTHON, GUEST, "launch", "--directory", guest_dir,
                "--run-id", run_id, "--case", case, "--commit", commit,
                "--runner-sha256", expected, "--startup-timeout", startup_timeout,
                "--hold-timeout", hold_timeout]
        result = json.loads(self.scada.run(argv).splitlines()[-1])
        if result["run_id"] != run_id or result["scenario"] != case or result["pid"] <= 0:
            raise ValueError("launch identity mismatch")
        control = {"version": 1, "run_id": run_id, "scenario": case,
                   "pid": result["pid"], "start_ticks": None,
                   "guest_dir": guest_dir, "local_dir": str(local),
                   "monitor_control": str(Path(monitor_control).resolve()),
                   "commit": commit, "runner_sha256": expected,
                   "launch_utc": datetime.now(timezone.utc).isoformat()}
        path = local / "scenario-control.json"
        atomic_control(path, control)
        return path

    def probe_ready(self, path, timeout=12):
        control = json.loads(Path(path).read_text())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                pid = self._file(control, "pid.json")
                if pid.get("run_id") != control["run_id"] or pid.get("pid") != control["pid"]:
                    raise ValueError("PID ownership mismatch")
                if control["start_ticks"] is None:
                    control["start_ticks"] = pid["start_ticks"]
                    atomic_control(Path(path).with_name("identity-proof.json"), pid)
                    Path(path).write_text(json.dumps(control, sort_keys=True, indent=2) + "\n")
                state = self._inspect(control)
                if not state["alive"] or not state["matches"]:
                    raise RuntimeError("detached runner exited before ready")
                ready = valid(self._file(control, "ready.json"), control)
                if ready.get("mutation_started") is not False or ready["preflight"]["runner_sha256"] != control["runner_sha256"]:
                    raise ValueError("runner preflight/identity mismatch")
                try:
                    self._file(control, "terminal.json")
                except GuestCommandError:
                    pass
                else:
                    raise RuntimeError("runner terminal before authorization")
                monitor_proof = Path(load_control(control["monitor_control"])["local_dir"]) / "ready-proof.json"
                if not monitor_proof.exists():
                    self.monitor.probe(control["monitor_control"], timeout=3)
                else:
                    monitor_control = load_control(control["monitor_control"])
                    monitor_identity = json.loads(monitor_proof.read_text())
                    if monitor_identity["run_id"] != monitor_control["run_id"]:
                        raise ValueError("monitor ready proof run ID mismatch")
                    monitor_state = self.monitor._inspect(monitor_control)
                    if not monitor_state.get("alive") or not monitor_state.get("matches"):
                        raise RuntimeError("readiness monitor no longer live")
                proof = {"run_id": control["run_id"], "ready": ready,
                         "shell_returned": True, "pid_alive": True,
                         "monitor_ready": True,
                         "observed_monotonic_ns": time.monotonic_ns()}
                ready_proof = Path(control["local_dir"]) / "scenario-ready-proof.json"
                if not ready_proof.exists():
                    atomic_control(ready_proof, proof)
                return proof
            except (FileNotFoundError, GuestCommandError, TimeoutError):
                time.sleep(.15)
        raise TimeoutError("detached runner did not pass startup gates")

    def action(self, path, action):
        control = json.loads(Path(path).read_text())
        if action == "authorize":
            self.probe_ready(path)
            control = json.loads(Path(path).read_text())
        if action not in ("authorize", "begin", "release", "abort"):
            raise ValueError(action)
        if action == "begin" and control["scenario"] in ("lane_hold", "merge_hold"):
            fixture = Path(control["local_dir"]) / "fixture-control.json"
            if not fixture.exists():
                raise RuntimeError("bounded drives fixture not ready")
            self.fixture_probe(path)
        if action == "begin":
            monitor = load_control(control["monitor_control"])
            state = self.monitor._inspect(monitor)
            if not state.get("alive") or not state.get("matches"):
                raise RuntimeError("readiness monitor died before scenario begin")
        if action == "release":
            proof = Path(control["local_dir"]) / "evidence-proof.json"
            if not proof.exists():
                raise RuntimeError("independent evidence proof missing")
            valid(json.loads(proof.read_text()), control)
        args = [SCADA_PYTHON, GUEST, action, "--directory", control["guest_dir"],
                "--run-id", control["run_id"], "--case", control["scenario"],
                "--pid", control["pid"], "--start-ticks", control["start_ticks"]]
        return valid(json.loads(self.scada.run(args).splitlines()[-1]), control)

    def fixture_start(self, path, duration=150):
        control = json.loads(Path(path).read_text())
        if control["scenario"] not in ("lane_hold", "merge_hold"):
            raise ValueError("scenario has no plant fixture")
        if not 30 <= duration <= 180:
            raise ValueError("fixture duration out of bounds")
        if not (Path(control["local_dir"]) / "scenario-ready-proof.json").exists():
            raise RuntimeError("scenario startup gate missing")
        valid(self._file(control, "authorize.json"), control)
        authorization_file = Path(control["local_dir"]) / "fixture-authorization.json"
        if not authorization_file.exists():
            raise RuntimeError("one-run fixture authorization missing")
        approved = json.loads(authorization_file.read_text())
        if (approved.get("run_id") != control["run_id"] or
            approved.get("scenario") != control["scenario"] or
            approved.get("unit") != FIXTURE_UNIT or
            approved.get("stop_argv") != FIXTURE_STOP or
            approved.get("start_argv") != FIXTURE_START or
            approved.get("initial_service_state") != "active"):
            raise ValueError("fixture authorization binding mismatch")
        if (Path(control["local_dir"]) / "fixture-control.json").exists():
            raise ValueError("fixture authorization was already used")
        guest_dir = f"/tmp/sorter-accumulation-fixture-{control['run_id']}"
        if approved.get("guest_directory") != guest_dir:
            raise ValueError("fixture authorization guest directory mismatch")
        remote = json.loads(self.drives.read_file(f"{guest_dir}/authorization.json"))
        if remote != approved:
            raise ValueError("guest fixture authorization differs from approved record")
        argv = [DRIVES_PYTHON, DRIVES_FIXTURE, "launch", "--directory", guest_dir,
                "--run-id", control["run_id"], "--case", control["scenario"],
                "--duration", duration]
        launched = json.loads(self.drives.run(argv).splitlines()[-1])
        fixture = {"run_id": control["run_id"], "scenario": control["scenario"],
                   "pid": launched["pid"], "guest_dir": guest_dir}
        atomic_control(Path(control["local_dir"]) / "fixture-control.json", fixture)
        return self.fixture_probe(path)

    def fixture_authorize(self, path, approval_id, valid_for=180):
        """Record one already granted operator approval for this exact run."""
        control = json.loads(Path(path).read_text())
        if control["scenario"] not in ("lane_hold", "merge_hold"):
            raise ValueError("scenario has no service fixture")
        if not 30 <= valid_for <= 180:
            raise ValueError("fixture authorization expiry out of bounds")
        manifest = json.loads(Path("deploy/deployment_manifest.json").read_text())
        component = next(item for item in manifest["components"] if
                         item["repository_source"] == "tests/live_accumulation_fixture.py")
        expected_hash = component["sha256"]
        if hashlib.sha256(Path("tests/live_accumulation_fixture.py").read_bytes()).hexdigest() != expected_hash:
            raise ValueError("repository fixture hash differs from deployment manifest")
        deployed = self.drives.run(["sha256sum", component["canonical_destination"]])
        if not any(line.startswith(expected_hash + "  " + component["canonical_destination"])
                   for line in deployed.splitlines()):
            raise ValueError("deployed fixture hash differs from deployment manifest")
        self.probe_ready(path)
        valid(self._file(control, "authorize.json"), control)
        guest_dir = f"/tmp/sorter-accumulation-fixture-{control['run_id']}"
        argv = [DRIVES_PYTHON, DRIVES_FIXTURE, "authorize", "--directory", guest_dir,
                "--run-id", control["run_id"], "--case", control["scenario"],
                "--approval-id", approval_id, "--valid-for", valid_for]
        approved = json.loads(self.drives.run(argv).splitlines()[-1])
        if (approved.get("run_id") != control["run_id"] or
            approved.get("scenario") != control["scenario"] or
            approved.get("approval_id") != approval_id or
            approved.get("guest_directory") != guest_dir or
            approved.get("unit") != FIXTURE_UNIT or
            approved.get("stop_argv") != FIXTURE_STOP or
            approved.get("start_argv") != FIXTURE_START or
            approved.get("initial_service_state") != "active"):
            raise ValueError("guest fixture authorization response mismatch")
        atomic_control(Path(control["local_dir"]) / "fixture-authorization.json", approved)
        return approved

    def fixture_probe(self, path, timeout=12):
        control = json.loads(Path(path).read_text())
        fixture = json.loads((Path(control["local_dir"]) / "fixture-control.json").read_text())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                pid = json.loads(self.drives.read_file(f"{fixture['guest_dir']}/pid.json"))
                ready = json.loads(self.drives.read_file(f"{fixture['guest_dir']}/ready.json"))
                if pid["run_id"] != control["run_id"] or pid["pid"] != fixture["pid"]:
                    raise ValueError("fixture PID mismatch")
                if any(ready.get(k) != pid[k] for k in ("run_id", "pid", "start_ticks")):
                    raise ValueError("fixture ready identity mismatch")
                approved = json.loads((Path(control["local_dir"]) /
                                       "fixture-authorization.json").read_text())
                if ready.get("authorization_id") != approved["authorization_id"]:
                    raise ValueError("fixture ready authorization mismatch")
                fixture["start_ticks"] = pid["start_ticks"]
                (Path(control["local_dir"]) / "fixture-control.json").write_text(
                    json.dumps(fixture, sort_keys=True, indent=2) + "\n")
                return ready
            except GuestCommandError:
                try:
                    terminal = json.loads(self.drives.read_file(
                        f"{fixture['guest_dir']}/terminal.json"))
                except GuestCommandError:
                    pass
                else:
                    raise RuntimeError(f"fixture exited before ready: {terminal}")
                time.sleep(.1)
        raise TimeoutError("bounded plant fixture did not start")

    def fixture_stop(self, path, timeout=25):
        control = json.loads(Path(path).read_text())
        fixture = json.loads((Path(control["local_dir"]) / "fixture-control.json").read_text())
        if "start_ticks" not in fixture:
            pid = json.loads(self.drives.read_file(f"{fixture['guest_dir']}/pid.json"))
            if pid.get("run_id") != fixture["run_id"] or pid.get("pid") != fixture["pid"]:
                raise RuntimeError("fixture PID ownership unproven")
            fixture["start_ticks"] = pid["start_ticks"]
            (Path(control["local_dir"]) / "fixture-control.json").write_text(
                json.dumps(fixture, sort_keys=True, indent=2) + "\n")
        argv = [DRIVES_PYTHON, DRIVES_FIXTURE, "stop", "--directory", fixture["guest_dir"],
                "--run-id", fixture["run_id"], "--case", fixture["scenario"],
                "--pid", fixture["pid"], "--start-ticks", fixture["start_ticks"]]
        self.drives.run(argv)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                terminal = json.loads(self.drives.read_file(f"{fixture['guest_dir']}/terminal.json"))
                if terminal["run_id"] != fixture["run_id"] or terminal["pid"] != fixture["pid"]:
                    raise ValueError("fixture terminal identity mismatch")
                approved = json.loads((Path(control["local_dir"]) /
                                       "fixture-authorization.json").read_text())
                if terminal.get("authorization_id") != approved["authorization_id"]:
                    raise ValueError("fixture terminal authorization mismatch")
                process = self.fixture_inspect(fixture)
                if process.get("alive"):
                    time.sleep(.1)
                    continue
                atomic_control(Path(control["local_dir"]) / "fixture-terminal.json", terminal)
                if not terminal["service_restored"]:
                    raise RuntimeError("plant service was not restored")
                if terminal.get("status") != "complete":
                    raise RuntimeError(f"fixture ended with {terminal.get('status')}: {terminal.get('error')}")
                return terminal
            except GuestCommandError:
                time.sleep(.1)
        raise TimeoutError("fixture did not exit and restore service")

    def fixture_inspect(self, fixture):
        argv = [DRIVES_PYTHON, DRIVES_FIXTURE, "inspect", "--directory", fixture["guest_dir"],
                "--run-id", fixture["run_id"], "--case", fixture["scenario"],
                "--pid", fixture["pid"], "--start-ticks", fixture["start_ticks"]]
        state = json.loads(self.drives.run(argv).splitlines()[-1])
        if any(state.get(key) != fixture[key] for key in ("run_id", "pid", "start_ticks")):
            raise ValueError("fixture process identity mismatch")
        return state

    def probe_checkpoint(self, path, timeout=90):
        control = json.loads(Path(path).read_text())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = self._inspect(control)
            if not state["alive"]:
                raise RuntimeError("scenario exited before checkpoint")
            try:
                checkpoint = valid(self._file(control, "checkpoint.json"), control)
            except GuestCommandError:
                time.sleep(.2)
                continue
            sample = checkpoint["sample"]
            if control["scenario"] != "smoke":
                # The independent drives monitor samples PLC Modbus. The
                # checkpoint is only a trigger, never the sole evidence.
                monitor = load_control(control["monitor_control"])
                monitor_state = self.monitor._inspect(monitor)
                if not monitor_state.get("alive") or not monitor_state.get("matches"):
                    raise RuntimeError("readiness monitor died at checkpoint")
                rows = [json.loads(line) for line in self.drives.read_file(monitor["paths"]["output"]).splitlines()]
                recent = [r["sample"] for r in rows[-25:] if r.get("event") in ("sample", "change")]
                if not recent or not any(r["commit_sequence"] >= sample["commit_sequence"] and
                                         r["plc_identity"] == 24113 for r in recent):
                    raise AssertionError("independent Modbus monitor did not observe checkpoint commit")
                motion = {"lane_hold": 2, "merge_hold": 3, "drive_stop": 4}[control["scenario"]]
                output = f"{control['guest_dir']}/opc-check.json"
                command = [SCADA_PYTHON, "/home/kevin/sorter-services/check_accumulation_views.py",
                           "--motion", motion, "--timeout", 8, "--output", output]
                agreement = json.loads(self.scada.run(command, timeout=13).splitlines()[-1])
                for row in sample["rows"]:
                    if row["state"] not in (1, 2, 3, 4) or row["motion"] != motion:
                        continue
                    slot = row["slot"]
                    expected = [row["zone"], row["motion"], row["hold_reason"],
                                row["dwell"], row["sequence"], row["quality"]]
                    for source in ("modbus", "opc", "hmi"):
                        actual = agreement[source]["rows" if source != "hmi" else "zone_rows"][slot]
                        if actual[:3] != expected[:3] or actual[4:] != expected[4:] or abs(actual[3] - expected[3]) > 15:
                            raise AssertionError(f"{source} slot {slot} differs from checkpoint")
                (Path(control["local_dir"]) / "opc-agreement.json").write_text(
                    json.dumps(agreement, sort_keys=True, indent=2) + "\n")
            proof = {**identity(control), "checkpoint": checkpoint,
                     "monitor_modbus_agreement": True,
                     "opc_hmi_agreement": control["scenario"] == "smoke" or bool(agreement),
                     "observed_monotonic_ns": time.monotonic_ns()}
            atomic_control(Path(control["local_dir"]) / "checkpoint-proof.json", proof)
            return proof
        raise TimeoutError("scenario checkpoint not reached")

    def evidence(self, path, api_url="http://127.0.0.1:18000/api", screenshot=None):
        control = json.loads(Path(path).read_text())
        proof = json.loads((Path(control["local_dir"]) / "checkpoint-proof.json").read_text())
        valid(proof, control)
        with urlopen(api_url, timeout=8) as response:
            api = json.load(response)
        if control["scenario"] != "smoke":
            sample = proof["checkpoint"]["sample"]
            if not api.get("accumulation_mode") or api.get("zone_fault") != sample["zone_fault"]:
                raise AssertionError("HMI API does not match checkpoint fault/mode")
            target_motion = {"lane_hold": 2, "merge_hold": 3, "drive_stop": 4}[control["scenario"]]
            matching = 0
            for row in sample["rows"]:
                if row["motion"] != target_motion or not row["package_id"]:
                    continue
                slot = row["slot"]
                plant = api["plant_rows"][slot]
                lane = api["plant_lane"][slot]
                observed_id = f"l{lane}-{plant[0]+plant[1]*30000}-{plant[2]}-{plant[3]}-{plant[4]}"
                zone = api["zone_rows"][slot]
                if (observed_id != row["package_id"] or zone[:3] != [row["zone"], row["motion"], row["hold_reason"]] or
                    zone[4:] != [row["sequence"], row["quality"]] or
                    abs(zone[3] - row["dwell"]) > 15 or api["plant_status"][slot] != 1):
                    raise AssertionError(f"HMI package or bounded zone lag mismatch at slot {slot}")
                matching += 1
            if matching < (3 if control["scenario"] == "merge_hold" else 1):
                raise AssertionError("required HMI package evidence absent")
            if screenshot is None or not Path(screenshot).is_file():
                raise AssertionError("rendered live screenshot missing")
        result = {**identity(control), "api": api, "screenshot": str(screenshot) if screenshot else None,
                  "captured_utc": datetime.now(timezone.utc).isoformat()}
        atomic_control(Path(control["local_dir"]) / "evidence-proof.json", result)
        return result

    def wait(self, path, timeout=240):
        control = json.loads(Path(path).read_text())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = self._inspect(control)
            try:
                terminal = valid(self._file(control, "terminal.json"), control)
            except GuestCommandError:
                terminal = None
            if terminal and not state["alive"]:
                fixture_path = Path(control["local_dir"]) / "fixture-control.json"
                if fixture_path.exists():
                    try:
                        restored = self.fixture_stop(path)
                        terminal["fixture_restoration"] = restored
                    except Exception as exc:
                        failure = f"{type(exc).__name__}: {exc}"
                        atomic_control(Path(control["local_dir"]) /
                                       "fixture-restoration-failure.json",
                                       {**identity(control), "error": failure,
                                        "scenario_terminal": terminal})
                        terminal["fixture_restoration_error"] = failure
                return terminal
            time.sleep(.25)
        raise TimeoutError("scenario has no terminal exit")

    def collect(self, path, timeout=240):
        control = json.loads(Path(path).read_text())
        terminal = self.wait(path, timeout)
        local = Path(control["local_dir"])
        for name in ("pid.json", "ready.json", "authorize.json", "begin.json", "checkpoint.json",
                     "release.json", "terminal.json", "stdout.log"):
            try:
                (local / name).write_bytes(self.scada.read_file(f"{control['guest_dir']}/{name}"))
            except GuestCommandError:
                if name in ("pid.json", "ready.json", "terminal.json", "stdout.log"):
                    raise
        state = self._inspect(control)
        if state["alive"]:
            raise RuntimeError("orphan detached scenario")
        return {"terminal": terminal, "orphan": False, "local_dir": str(local),
                "cleanup_error": terminal.get("fixture_restoration_error")}

    def verify_clean(self, path):
        control = json.loads(Path(path).read_text())
        before = json.loads((Path(control["local_dir"]) / "typed-before.json").read_text())
        after = typed_capture()
        typed_save(Path(control["local_dir"]) / "typed-after.json", after)
        typed_report = typed_compare(before, after)
        typed_save(Path(control["local_dir"]) / "typed-restoration-report.json", typed_report)
        if typed_report["status"] != "PASS":
            raise AssertionError(f"typed restoration failed: {typed_report['differences']}")
        original = json.loads((Path(control["local_dir"]) / "initial-state.json").read_text())
        final = self.state()
        if self._inspect(control)["alive"]:
            raise RuntimeError("detached scenario orphan remains")
        for key in ("setpoints_200_210", "seed", "trailer_counters"):
            if final[key] != original[key]:
                raise AssertionError(f"not restored: {key}")
        for address in (0, *range(1, 8), 34, 35, 38, 39, 40):
            if final["coils_880_920"][address] != original["coils_880_920"][address]:
                raise AssertionError(f"operator coil {880 + address} differs")
        if final["coils_880_920"][0] or any(row[4] for row in final["slots"]):
            raise AssertionError("sorter running or slot occupied")
        if (final["photoeye_faults"] != [0, 0, 0] or final["zone_view"][22] or
            final["plant_faults"][0] or final["scanner_fault_mask"]):
            raise AssertionError("plant, scanner, photoeye, or zone fault remains")
        monitor_control = load_control(control["monitor_control"])
        monitor_state = self.monitor._inspect(monitor_control)
        if monitor_state.get("alive"):
            raise AssertionError("readiness monitor orphan remains")
        service = self.drives.run(["/usr/bin/systemctl", "is-active",
                                   "sorter-plant.service"]).splitlines()[-1]
        if service != "active":
            raise AssertionError("canonical plant service inactive")
        fixture_path = Path(control["local_dir"]) / "fixture-control.json"
        if fixture_path.exists():
            fixture = json.loads(fixture_path.read_text())
            fixture_terminal = Path(control["local_dir"]) / "fixture-terminal.json"
            if not fixture_terminal.exists():
                raise AssertionError("fixture cleanup proof missing")
            if not json.loads(fixture_terminal.read_text()).get("service_restored"):
                raise AssertionError("fixture service restoration failed")
            if self.fixture_inspect(fixture).get("alive"):
                raise AssertionError("fixture supervisor orphan remains")
        guest_patterns = ((self.scada, "^/home/kevin/opcua/bin/python /home/kevin/sorter-services/xle.py"),
                          (self.scada, "^/home/kevin/opcua/bin/python /home/kevin/sorter-services/asx.py"),
                          (self.drives, "^/home/kevin/venv/bin/python /home/kevin/plant.py --block-"))
        for guest, pattern in guest_patterns:
            try:
                output = guest.run(["pgrep", "-af", pattern])
            except GuestCommandError as exc:
                if exc.code == 1:
                    continue
                raise
            if any(re.match(r"^\d+\s", line.strip()) for line in output.splitlines()):
                raise AssertionError(f"test process remains: {output}")
        normal = self.drives.run(["pgrep", "-af",
                                  "^/home/kevin/venv/bin/python /home/kevin/plant.py"]).splitlines()
        if len([line for line in normal if re.match(r"^\d+\s", line.strip()) and
                "--block-" not in line]) != 1:
            raise AssertionError("expected one canonical unflagged plant process")
        processes = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True)
        if any("tests/serial_hmi_proxy.py" in line and
               re.search(r"\bpython(?:3(?:\.\d+)?)?\s+tests/serial_hmi_proxy\.py\b", line)
               for line in processes.splitlines()):
            raise AssertionError("SCADA HMI proxy remains")
        proof = {**identity(control), "restored": True, "plant_service": service,
                 "initial": original, "final": final}
        atomic_control(Path(control["local_dir"]) / "final-state.json", proof)
        atomic_control(RESTORATION_GATE,
                       {"status": "PASS", "run_id": control["run_id"],
                        "report": str(Path(control["local_dir"]) / "typed-restoration-report.json")})
        return proof


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    p = sub.add_parser("launch")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--case", choices=CASES, required=True)
    p.add_argument("--monitor-control", required=True)
    p.add_argument("--typed-baseline", required=True)
    p.add_argument("--startup-timeout", type=float, default=30)
    p.add_argument("--hold-timeout", type=float, default=30)
    for name in ("probe-ready", "authorize", "begin", "probe-checkpoint", "release", "wait",
                 "collect", "abort", "evidence", "verify-clean", "fixture-authorize",
                 "fixture-start", "fixture-stop"):
        p = sub.add_parser(name)
        p.add_argument("--control", required=True)
        p.add_argument("--timeout", type=float, default=15)
        if name == "evidence":
            p.add_argument("--screenshot")
        if name == "fixture-start":
            p.add_argument("--duration", type=float, default=150)
        if name == "fixture-authorize":
            p.add_argument("--approval-id", required=True)
            p.add_argument("--valid-for", type=float, default=180)
    args = cli.parse_args(argv)
    control = ScenarioController()
    try:
        if args.action == "launch":
            result = {"control": str(control.launch(args.evidence_dir, args.case,
                                                    args.monitor_control, args.typed_baseline,
                                                    args.startup_timeout, args.hold_timeout))}
        elif args.action == "probe-ready":
            result = control.probe_ready(args.control, args.timeout)
        elif args.action == "probe-checkpoint":
            result = control.probe_checkpoint(args.control, args.timeout)
        elif args.action == "evidence":
            result = control.evidence(args.control, screenshot=args.screenshot)
        elif args.action == "fixture-start":
            result = control.fixture_start(args.control, args.duration)
        elif args.action == "fixture-authorize":
            result = control.fixture_authorize(args.control, args.approval_id,
                                               args.valid_for)
        elif args.action == "fixture-stop":
            result = control.fixture_stop(args.control, args.timeout)
        elif args.action in ("authorize", "begin", "release", "abort"):
            result = control.action(args.control, args.action)
        elif args.action == "wait":
            result = control.wait(args.control, args.timeout)
        elif args.action == "collect":
            result = control.collect(args.control, args.timeout)
        else:
            result = control.verify_clean(args.control)
    except Exception as exc:
        if args.action != "launch":
            record = json.loads(Path(args.control).read_text())
            category = ("evidence_failure" if args.action in ("evidence", "probe-checkpoint") else
                        "monitor_failure" if args.action in ("probe-ready", "authorize") else
                        "cleanup_failure" if args.action in ("collect", "fixture-stop", "verify-clean") else
                        "scenario_failure")
            atomic_control(Path(record["local_dir"]) / ("validation-failure-" + uuid.uuid4().hex + ".json"),
                           {**identity(record), "status": category,
                            "action": args.action,
                            "original_error": f"{type(exc).__name__}: {exc}",
                            "cleanup": "pending_or_separate", "recorded_utc": datetime.now(timezone.utc).isoformat()})
        raise
    print(json.dumps(result, sort_keys=True, default=str), flush=True)
    if isinstance(result, dict) and (result.get("cleanup_error") or
                                    result.get("fixture_restoration_error")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

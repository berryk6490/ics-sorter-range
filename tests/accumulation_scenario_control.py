"""Host control of detached SCADA validation, over the existing serial shell."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid
from urllib.request import urlopen

from accumulation_monitor_control import (Controller as MonitorController, SerialGuest,
                                          SCADA_PYTHON, GuestCommandError,
                                          atomic_control, load_control)
from accumulation_state_snapshot import capture as typed_capture, compare as typed_compare, save as typed_save
from accumulation_state_snapshot import PLANT, TEMPORARY
from deployment_preflight import guest_read, MANIFEST

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
MIN_AUTHORIZATION_MARGIN = 25.0
POST_READY_GATE_TIMEOUT = 25.0
GATE_READERS = {
    "plc": ("/usr/bin/python3", "/home/kevin/read_authorization_gate.py"),
    "drives": (DRIVES_PYTHON, "/home/kevin/read_authorization_gate.py"),
    "scada": (SCADA_PYTHON, "/home/kevin/sorter-services/read_authorization_gate.py"),
}
APPROVAL_LIFETIME = 300
APPROVAL_LEDGER = Path.home() / "vm" / "sorter-evidence" / "phase2a-host-approval-ledger.jsonl"
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}_[0-9a-f]{32}")
PREPARATION_SCHEMA = 2


def exclusive_record(path, value):
    """Durably claim one lifecycle step without replacing an earlier attempt."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepared_directory(path, record, case, baseline, preflight, *, approved):
    """Accept only the exact directory reserved by prepare for this launch."""
    root = path.parent
    expected = {"preparation.json", "operator-approval.json"} if approved else {"preparation.json"}
    if (path.is_symlink() or not path.is_dir() or not RUN_ID.fullmatch(path.name) or
        record.get("version") != PREPARATION_SCHEMA or record.get("run_id") != path.name or
        record.get("scenario") != case or
        record.get("typed_baseline") != str(Path(baseline).resolve()) or
        record.get("preflight_report") != str(Path(preflight).resolve()) or
        not isinstance(record.get("prepared_utc"), str)):
        raise ValueError("prepared run identity or arguments differ from launch")
    contents = {item.name for item in path.iterdir()}
    if contents != expected or any(item.is_symlink() or not stat.S_ISREG(item.stat().st_mode)
                                   for item in path.iterdir()):
        raise ValueError(f"prepared run directory has unexpected contents: {sorted(contents)}")
    if root.resolve() != root:
        raise ValueError("prepared evidence root is not canonical")
    return path


def write_run_gate(local, run_id, scenario, status, **details):
    record = {"run_id": run_id, "scenario": scenario, "status": status,
              "recorded_utc": datetime.now(timezone.utc).isoformat(), **details}
    if status == "PASS":
        atomic_control(local / "restoration-gate.json", record)
        atomic_control(RESTORATION_GATE, record)
    else:
        atomic_control(RESTORATION_GATE, record)
        atomic_control(local / "restoration-gate.json", record)
    return record


def validate_unchanged_plc(before, current):
    """Short pre-authorization subset of the typed baseline, with no VFD inventory."""
    if current.get("identity") != before.get("identity") or current.get("identity") != 24113:
        raise ValueError("PLC program identity changed")
    for key in ("coils_880_920", "setpoints_200_210", "seed", "photoeye_config_744_747"):
        if current.get(key) != before.get(key):
            raise ValueError(f"PLC operator configuration changed: {key}")
    if current["coils_880_920"][0] or any(row[4] for row in current["slots"]):
        raise ValueError("PLC master or package slot changed")
    if (any(current["lanes"]) or any(current["trailer_counters"]) or
        any(current["photoeye_faults"]) or current["plant_faults"][:2] != [0, 0] or
        current["scanner_state"] or current["scanner_fault_mask"] or
        current["run_identity"]["epoch_fault"] or current["xle_health"][1] or
        current["zone_view"][20] or current["zone_view"][22]):
        raise ValueError("PLC no longer at safe stopped baseline")
    if any(current["process_214_242"][i] for i in (*range(7), *range(8, 29))):
        raise ValueError("PLC process counter changed")
    if any(current["scanner_counters_250_254"]) or any(
            current["zone_view"][i] for i in (0, 1, 2, 3, 5, 6, 7, 8,
                                              10, 11, 12, 13, 15, 16, 17, 18)):
        raise ValueError("scanner count or validated zone state changed")


def service_groups():
    manifest = json.loads(MANIFEST.read_text())
    result = {}
    for entry in manifest["components"]:
        result.setdefault(entry["guest"], set()).update(entry["associated_service"])
    return {vm: sorted(names) for vm, names in result.items() if names}


def validate_typed_launch(evidence_root, typed_baseline, monitor_control, preflight_report):
    gate = RESTORATION_GATE
    if gate.exists() and json.loads(gate.read_text()).get("status") != "PASS":
        raise ValueError("previous scenario has no passing typed restoration comparison")
    baseline = json.loads(Path(typed_baseline).read_text())
    full = json.loads(Path(preflight_report).read_text())
    manifest = Path("deploy/deployment_manifest.json")
    expected_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    expected_count = len(json.loads(manifest.read_text())["components"])
    if (full.get("schema_version") != 1 or full.get("status") != "PASS" or
        full.get("live") is not True or full.get("manifest_sha256") != expected_hash or
        full.get("source_and_guest_hashes") != expected_count or
        full.get("program_identity") != baseline.get("program_identity")):
        raise ValueError("full deployment preflight receipt is missing or mismatched")
    report_time = datetime.fromisoformat(full["completed_utc"])
    baseline_time = datetime.fromisoformat(baseline["captured_utc"])
    age = (datetime.now(timezone.utc) - baseline_time).total_seconds()
    if not 0 <= age <= 600 or not report_time <= baseline_time:
        raise ValueError("full preflight must precede a fresh typed baseline")
    if monitor_control:
        monitor_time = datetime.fromisoformat(load_control(monitor_control)["launched_utc"])
        if baseline_time >= monitor_time:
            raise ValueError("typed baseline must precede monitor launch")
    baseline_report = typed_compare(baseline, baseline)
    if baseline_report["status"] != "PASS":
        raise ValueError(f"unsafe typed baseline: {baseline_report['differences']}")
    return baseline, baseline_report


def claim_host_approval(approval_id, run_id, case):
    """Spend an operator receipt before launch, even if the guest is never reached."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", approval_id):
        raise ValueError("invalid operator approval receipt ID")
    APPROVAL_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(approval_id.encode()).hexdigest()
    fd = os.open(APPROVAL_LEDGER, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        if any(json.loads(line)["approval_sha256"] == digest for line in stream):
            raise ValueError("operator approval receipt was already used")
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps({"approval_sha256": digest, "run_id": run_id,
                                 "scenario": case, "recorded_utc": datetime.now(timezone.utc).isoformat()},
                                sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def valid_preapproval(preparation, approval, *, now=None):
    """Validate the exact prelaunch approval; a failed launch spends its receipt."""
    now = time.time_ns() if now is None else now
    if (approval.get("version") != 1 or approval.get("run_id") != preparation["run_id"] or
        approval.get("scenario") != preparation["scenario"] or
        approval.get("unit") != FIXTURE_UNIT or approval.get("stop_argv") != FIXTURE_STOP or
        approval.get("start_argv") != FIXTURE_START or
        approval.get("initial_service_state") != "active" or
        approval.get("scope") != "one stop and its restorative start for this run only" or
        not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", str(approval.get("approval_id", ""))) or
        type(approval.get("approved_wall_ns")) is not int or
        type(approval.get("expires_wall_ns")) is not int or
        not 0 < approval["expires_wall_ns"] - approval["approved_wall_ns"] <= APPROVAL_LIFETIME * 10**9 or
        not approval["approved_wall_ns"] <= now < approval["expires_wall_ns"]):
        raise ValueError("prelaunch operator approval missing, mismatched, or expired")
    return approval


def identity(control):
    return {"run_id": control["run_id"], "scenario": control["scenario"],
            "pid": control["pid"], "start_ticks": control["start_ticks"]}


def valid(row, control):
    if any(row.get(k) != v for k, v in identity(control).items()):
        raise ValueError("detached lifecycle identity mismatch")
    return row


def validate_hold_snapshot(sample, plc, motion):
    """Compare committed identity and safety state; retain changing telemetry."""
    if (plc["epoch"] != sample["epoch"] or plc["nonce"] != sample["nonce"] or
        plc["faults"] != {"plant": 0, "zone": 0, "photoeyes": [0, 0, 0]} or
        any(plc["counters"]) or not plc["master"]):
        raise AssertionError("consistent PLC snapshot violates hold safety invariants")
    telemetry = []
    for row in sample["rows"]:
        if row["state"] not in (1, 2, 3, 4) or row["motion"] != motion:
            continue
        slot = row["slot"]
        observed = plc["slots"][slot]
        stable = ("token", "serial", "scanner_sequence", "barcode", "state",
                  "destination", "actual")
        if (plc["identities"][slot] != row["package_id"] or
            plc["lanes"][slot] != row["lane"] or
            any(observed[i] != row[key] for i, key in enumerate(stable))):
            raise AssertionError(f"consistent PLC identity mismatch at slot {slot}")
        actual = plc["rows"][slot]
        if actual[:3] != [row["zone"], row["motion"], row["hold_reason"]] or actual[5] != 1:
            raise AssertionError(f"PLC hold invariant changed at slot {slot}")
        telemetry.append({"slot": slot, "package_id": row["package_id"],
                          "checkpoint_dwell": row["dwell"], "current_dwell": actual[3],
                          "checkpoint_sequence": row["sequence"],
                          "current_sequence": actual[4],
                          "current_position": plc["plant_rows"][slot][6]})
    if not telemetry:
        raise AssertionError("no required held package in committed PLC snapshot")
    return telemetry


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

    def prepare(self, evidence_dir, case, typed_baseline, preflight_report):
        """Reserve the run identity before requesting human approval."""
        if case not in CASES:
            raise ValueError("unknown scenario")
        root = Path(evidence_dir).resolve()
        validate_typed_launch(root, typed_baseline, None, preflight_report)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex
        local = root / run_id
        local.mkdir(parents=True, mode=0o700, exist_ok=False)
        record = {"version": PREPARATION_SCHEMA, "run_id": run_id, "scenario": case,
                  "typed_baseline": str(Path(typed_baseline).resolve()),
                  "preflight_report": str(Path(preflight_report).resolve()),
                  "prepared_utc": datetime.now(timezone.utc).isoformat()}
        path = local / "preparation.json"
        exclusive_record(path, record)
        return path

    def record_approval(self, preparation_path, approval_id, valid_for=APPROVAL_LIFETIME):
        """Record one fresh operator attestation before any worker is launched."""
        if not 30 <= valid_for <= APPROVAL_LIFETIME:
            raise ValueError("prelaunch approval expiry out of bounds")
        path = Path(preparation_path).resolve()
        preparation = json.loads(path.read_text())
        local = path.parent
        if preparation.get("scenario") not in ("lane_hold", "merge_hold") or path.name != "preparation.json":
            raise ValueError("fixture preparation identity mismatch")
        prepared_directory(local, preparation, preparation["scenario"],
                           preparation["typed_baseline"], preparation["preflight_report"],
                           approved=False)
        validate_typed_launch(local.parent, preparation["typed_baseline"], None,
                              preparation["preflight_report"])
        claim_host_approval(approval_id, preparation["run_id"], preparation["scenario"])
        now = time.time_ns()
        approval = {"version": 1, "run_id": preparation["run_id"],
                    "scenario": preparation["scenario"], "approval_id": approval_id,
                    "unit": FIXTURE_UNIT, "stop_argv": FIXTURE_STOP, "start_argv": FIXTURE_START,
                    "initial_service_state": "active",
                    "approved_wall_ns": now, "expires_wall_ns": now + int(valid_for * 1e9),
                    "scope": "one stop and its restorative start for this run only"}
        exclusive_record(local / "operator-approval.json", approval)
        return {"run_id": preparation["run_id"], "scenario": preparation["scenario"],
                "approval_record": str(local / "operator-approval.json"),
                "expires_wall_ns": approval["expires_wall_ns"]}

    def _failed_launch(self, local, run_id, case, monitor_control, baseline,
                       guest_dir, commit, runner_sha256, original_error, guest_attempted):
        """Retain original, process cleanup, typed postflight and service results separately."""
        cleanup = {"worker": None, "monitor": None, "errors": []}
        control_path = local / "scenario-control.json"
        if guest_attempted and not control_path.exists():
            end = time.monotonic() + 3
            while time.monotonic() < end:
                try:
                    pid = json.loads(self.scada.read_file(f"{guest_dir}/pid.json"))
                    if pid.get("run_id") != run_id or pid.get("scenario") != case:
                        raise ValueError("guest PID file belongs to another run")
                    control = {"version": 1, "run_id": run_id, "scenario": case,
                               "pid": pid["pid"], "start_ticks": pid["start_ticks"],
                               "guest_dir": guest_dir, "local_dir": str(local),
                               "monitor_control": str(Path(monitor_control).resolve()),
                               "commit": commit, "runner_sha256": runner_sha256}
                    exclusive_record(control_path, control)
                    break
                except GuestCommandError:
                    time.sleep(.15)
                except Exception as exc:
                    cleanup["errors"].append(f"worker identity: {type(exc).__name__}: {exc}")
                    break
        if control_path.exists():
            try:
                cleanup["worker"] = self._abort_failed_gate(control_path, str(original_error))
                cleanup["monitor"] = cleanup["worker"].get("monitor")
                cleanup["errors"].extend(cleanup["worker"].get("errors", []))
            except Exception as exc:
                cleanup["errors"].append(f"worker cleanup: {type(exc).__name__}: {exc}")
        if cleanup["monitor"] is None:
            try:
                cleanup["monitor"] = self.monitor.collect(monitor_control, timeout=20, stop=True)
                cleanup["errors"].extend(cleanup["monitor"].get("cleanup_errors", []))
                if cleanup["monitor"].get("orphan"):
                    cleanup["errors"].append("monitor orphan remains")
            except Exception as exc:
                cleanup["errors"].append(f"monitor cleanup: {type(exc).__name__}: {exc}")
        typed = {"status": "ERROR", "error": "postflight not captured"}
        service = {"required_start": False, "sorter_plant_active": None,
                   "fixture_started": (local / "fixture-control.json").exists()}
        try:
            after = typed_capture()
            typed_save(local / "typed-after.json", after)
            typed = typed_compare(baseline, after)
            typed_save(local / "typed-restoration-report.json", typed)
            service["sorter_plant_active"] = (
                after["environment"]["services"].get("drives:sorter-plant.service") == "active")
        except Exception as exc:
            typed = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        restored = (not cleanup["errors"] and typed["status"] == "PASS" and
                    service["sorter_plant_active"] and not service["fixture_started"])
        prior = json.loads(RESTORATION_GATE.read_text()) if RESTORATION_GATE.exists() else None
        if prior and prior.get("status") != "PASS" and prior.get("run_id") != run_id:
            # This attempt cannot close another run's unresolved restoration gate.
            gate = {"run_id": run_id, "scenario": case, "status": "FAIL",
                    "result": "launch_blocked_by_prior_run", "prior_run_id": prior.get("run_id")}
            atomic_control(local / "restoration-gate.json", gate)
        else:
            gate = write_run_gate(local, run_id, case, "PASS" if restored else "FAIL",
                                  result="launch_failed", report=str(local / "typed-restoration-report.json"))
        failure = {"run_id": run_id, "scenario": case, "status": "launch_failed",
                   "original_error": f"{type(original_error).__name__}: {original_error}",
                   "cleanup": cleanup, "typed_restoration": typed,
                   "service_restoration": service, "restoration_gate": gate}
        atomic_control(local / "launch-failure.json", failure)
        return failure

    def launch(self, evidence_dir, case, monitor_control, typed_baseline, preflight_report,
               startup_timeout=120, hold_timeout=30, preparation_path=None):
        if case not in CASES or not 1 <= startup_timeout <= 120 or not 1 <= hold_timeout <= 60:
            raise ValueError("invalid scenario or timeout")
        manifest = json.loads(Path("deploy/deployment_manifest.json").read_text())
        expected = next(c["sha256"] for c in manifest["components"]
                        if c["repository_source"] == "tests/live_accumulation.py")
        if hashlib.sha256(Path("tests/live_accumulation.py").read_bytes()).hexdigest() != expected:
            raise ValueError("local runner does not match deployment manifest")
        evidence_root = Path(evidence_dir).resolve()
        prepared = preparation_path is not None
        if case in ("lane_hold", "merge_hold") and not prepared:
            self.monitor.collect(monitor_control, timeout=20, stop=True)
            raise ValueError("fixture scenario requires a prepared, approved run")
        local = None
        claimed = False
        guest_attempted = False
        baseline = None
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        try:
            if prepared:
                preparation_file = Path(preparation_path).resolve()
                if preparation_file.name != "preparation.json" or preparation_file.parent.parent != evidence_root:
                    raise ValueError("preparation path is outside the evidence root")
                local = preparation_file.parent
                preparation = json.loads(preparation_file.read_text())
                if (local / "launch-claimed.json").exists():
                    raise ValueError("prepared run was already launched or attempted")
                prepared_directory(local, preparation, case, typed_baseline, preflight_report,
                                   approved=case in ("lane_hold", "merge_hold"))
                run_id = preparation["run_id"]
                exclusive_record(local / "launch-claimed.json",
                                 {"run_id": run_id, "scenario": case,
                                  "claimed_utc": datetime.now(timezone.utc).isoformat()})
                claimed = True
                baseline, baseline_report = validate_typed_launch(evidence_root, typed_baseline,
                                                                  monitor_control, preflight_report)
            else:
                run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex
                baseline, baseline_report = validate_typed_launch(evidence_root, typed_baseline,
                                                                  monitor_control, preflight_report)
                local = evidence_root / run_id
                local.mkdir(parents=True, mode=0o700, exist_ok=False)
                exclusive_record(local / "launch-claimed.json",
                                 {"run_id": run_id, "scenario": case,
                                  "claimed_utc": datetime.now(timezone.utc).isoformat()})
                claimed = True
            guest_dir = f"/tmp/sorter-accumulation-scenario-{run_id}"
            if case in ("lane_hold", "merge_hold"):
                valid_preapproval(preparation, json.loads((local / "operator-approval.json").read_text()))
            RESTORATION_GATE.parent.mkdir(parents=True, exist_ok=True)
            write_run_gate(local, run_id, case, "PENDING",
                           baseline=str(local / "typed-before.json"))
            typed_save(local / "typed-before.json", baseline)
            typed_save(local / "typed-baseline-check.json", baseline_report)
            initial = self.state()
            if initial["identity"] != 24113 or initial["coils_880_920"][0] or any(
                    row[4] for row in initial["slots"]):
                raise ValueError("PLC is not at a stopped, empty baseline")
            atomic_control(local / "initial-state.json", initial)
            argv = [SCADA_PYTHON, GUEST, "launch", "--directory", guest_dir,
                    "--run-id", run_id, "--case", case, "--commit", commit,
                    "--runner-sha256", expected, "--startup-timeout", startup_timeout,
                    "--hold-timeout", hold_timeout]
            guest_attempted = True
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
            exclusive_record(path, control)
            return path
        except Exception as exc:
            if claimed and local is not None:
                if baseline is None:
                    try:
                        baseline = json.loads(Path(typed_baseline).read_text())
                    except Exception:
                        baseline = None
                try:
                    failure = self._failed_launch(local, run_id, case, monitor_control, baseline,
                                                  f"/tmp/sorter-accumulation-scenario-{run_id}",
                                                  commit, expected, exc, guest_attempted)
                except Exception as cleanup_exc:
                    raise RuntimeError(f"launch failed for {run_id}: {exc}; "
                                       f"cleanup/postflight also failed: {cleanup_exc}") from exc
                raise RuntimeError(f"launch failed for {run_id}: {exc}; "
                                   f"cleanup={failure['cleanup']['errors']}; "
                                   f"typed={failure['typed_restoration']['status']}") from exc
            cleanup = self.monitor.collect(monitor_control, timeout=20, stop=True)
            if cleanup.get("cleanup_errors") or cleanup.get("orphan"):
                raise RuntimeError(f"launch rejected: {exc}; monitor cleanup: {cleanup}") from exc
            raise

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

    def _abort_failed_gate(self, path, original_error):
        """No fixture exists at this stage; never call fixture_stop here."""
        control = json.loads(Path(path).read_text())
        cleanup = {"abort": None, "worker_terminal": None, "monitor": None, "errors": []}
        if (Path(control["local_dir"]) / "fixture-control.json").exists():
            cleanup["errors"].append("unexpected fixture before authorization gate")
        try:
            if control.get("start_ticks") is None:
                pid = self._file(control, "pid.json")
                if pid.get("run_id") != control["run_id"] or pid.get("pid") != control["pid"]:
                    raise ValueError("worker PID ownership cannot be established")
                control["start_ticks"] = pid["start_ticks"]
                atomic_control(Path(path), control)
            cleanup["abort"] = self.action(path, "abort")
            end = time.monotonic() + 20
            while time.monotonic() < end:
                state = self._inspect(control)
                try:
                    terminal = valid(self._file(control, "terminal.json"), control)
                except GuestCommandError:
                    terminal = None
                if terminal and not state.get("alive"):
                    cleanup["worker_terminal"] = terminal
                    break
                time.sleep(.15)
            if cleanup["worker_terminal"] is None:
                cleanup["errors"].append("worker did not terminate after abort")
        except Exception as exc:
            cleanup["errors"].append(f"worker abort: {type(exc).__name__}: {exc}")
        try:
            cleanup["monitor"] = self.monitor.collect(control["monitor_control"], timeout=20, stop=True)
            cleanup["errors"].extend(cleanup["monitor"].get("cleanup_errors", []))
        except Exception as exc:
            cleanup["errors"].append(f"monitor stop: {type(exc).__name__}: {exc}")
        atomic_control(Path(control["local_dir"]) / "post-ready-gate-failure.json",
                       {**identity(control), "error": original_error, "cleanup": cleanup,
                        "recorded_utc": datetime.now(timezone.utc).isoformat()})
        return cleanup

    def pre_authorization_gate(self, path, timeout=POST_READY_GATE_TIMEOUT):
        """Bounded live-state check after readiness; no repeat hash inventory."""
        if not 1 <= timeout <= POST_READY_GATE_TIMEOUT:
            raise ValueError("post-ready gate timeout out of bounds")
        started = time.monotonic()
        control = json.loads(Path(path).read_text())
        try:
            if control["scenario"] in ("lane_hold", "merge_hold"):
                preparation = json.loads((Path(control["local_dir"]) / "preparation.json").read_text())
                approval = json.loads((Path(control["local_dir"]) / "operator-approval.json").read_text())
                valid_preapproval(preparation, approval)
            ready_path = Path(control["local_dir"]) / "scenario-ready-proof.json"
            if ready_path.exists():
                proof = json.loads(ready_path.read_text())
                if (proof.get("run_id") != control["run_id"] or
                    proof.get("pid_alive") is not True or
                    proof.get("monitor_ready") is not True):
                    raise ValueError("saved readiness proof mismatched")
            else:
                proof = self.probe_ready(path, timeout=min(8, timeout))
            control = json.loads(Path(path).read_text())
            ready = proof["ready"]
            if (ready.get("repository_commit") != control["commit"] or
                ready.get("mutation_started") is not False or
                ready.get("preflight", {}).get("runner_sha256") != control["runner_sha256"] or
                any(ready.get(k) != control[k] for k in ("run_id", "scenario", "pid", "start_ticks"))):
                raise ValueError("worker ready record changed or mismatched")
            monitor = load_control(control["monitor_control"])
            worker_state = self._inspect(control)
            if not worker_state.get("alive") or not worker_state.get("matches"):
                raise ValueError("worker is no longer live")
            monitor_state = self.monitor._inspect(monitor)
            if not monitor_state.get("alive") or not monitor_state.get("matches"):
                raise ValueError("monitor is no longer ready and live")
            if (Path(control["local_dir"]) / "fixture-control.json").exists() or \
               (Path(control["local_dir"]) / "fixture-authorization.json").exists():
                raise ValueError("fixture authorization or service transition started before gate")
            before = json.loads((Path(control["local_dir"]) / "typed-before.json").read_text())["plc"]
            checked_services = 0
            current = None
            for vm, (python, script) in GATE_READERS.items():
                output = guest_read(vm, f"{python} {script} {vm}", f"post_ready:{vm}")
                observation = json.loads(output.splitlines()[-1])
                if observation.get("schema_version") != 1 or observation.get("role") != vm:
                    raise ValueError(f"post-ready reader mismatch: {vm}")
                names = service_groups()[vm]
                if (set(observation["services"]) != set(names) or
                    any(value != "active" for value in observation["services"].values())):
                    raise ValueError(f"required service changed on {vm}: {observation['services']}")
                checked_services += len(names)
                if vm == "drives":
                    current = observation["plc"]
                    validate_unchanged_plc(before, current)
                rows = observation["processes"]
                if vm in ("drives", "scada"):
                    expected_pid = monitor["pid"] if vm == "drives" else control["pid"]
                    temporary = [row for row in rows if TEMPORARY.search(row["args"])]
                    if len(temporary) != 1 or temporary[0]["pid"] != expected_pid:
                        raise ValueError(f"competing or missing validation process on {vm}: {temporary}")
                if vm == "drives":
                    plant = [row for row in rows if PLANT.match(row["args"])]
                    if len(plant) != 1 or "--block-" in plant[0]["args"]:
                        raise ValueError("canonical unflagged plant process changed")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("post-ready gate exceeded its bound")
            host_ps = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                                     text=True, timeout=5, check=True).stdout
            if any(re.match(r"\s*\d+\s+python(?:3(?:\.\d+)?)?\s+tests/serial_hmi_proxy\.py(?:\s|$)", line)
                   for line in host_ps.splitlines()):
                raise ValueError("HMI proxy occupies validation console")
            state = self._inspect(control)
            if not state.get("alive") or not state.get("matches"):
                raise ValueError("worker exited during post-ready gate")
            remaining = state.get("authorization_remaining_seconds", 0)
            duration = time.monotonic() - started
            if duration > timeout or remaining < MIN_AUTHORIZATION_MARGIN:
                raise TimeoutError(f"authorization window expired: gate={duration:.3f}s remaining={remaining:.3f}s")
            if control["scenario"] in ("lane_hold", "merge_hold"):
                valid_preapproval(preparation, approval)
            result = {**identity(control), "status": "PASS", "duration_seconds": round(duration, 3),
                      "authorization_remaining_seconds": round(remaining, 3),
                      "services_checked": checked_services, "plc_identity": current["identity"],
                      "monitor_run_id": monitor["run_id"], "checked_utc": datetime.now(timezone.utc).isoformat()}
            atomic_control(Path(control["local_dir"]) / "post-ready-gate.json", result)
            return result
        except Exception as exc:
            cleanup = self._abort_failed_gate(path, f"{type(exc).__name__}: {exc}")
            raise RuntimeError(f"post-ready gate failed: {exc}; cleanup={cleanup}") from exc

    def action(self, path, action):
        control = json.loads(Path(path).read_text())
        if action == "authorize":
            try:
                gate_path = Path(control["local_dir"]) / "post-ready-gate.json"
                if not gate_path.exists():
                    raise RuntimeError("post-ready gate proof missing")
                gate = valid(json.loads(gate_path.read_text()), control)
                if gate.get("status") != "PASS":
                    raise RuntimeError("post-ready gate did not pass")
                if control["scenario"] in ("lane_hold", "merge_hold"):
                    local = Path(control["local_dir"])
                    valid_preapproval(json.loads((local / "preparation.json").read_text()),
                                      json.loads((local / "operator-approval.json").read_text()))
                monitor = load_control(control["monitor_control"])
                monitor_state = self.monitor._inspect(monitor)
                if not monitor_state.get("alive") or not monitor_state.get("matches"):
                    raise RuntimeError("monitor died before authorization")
                state = self._inspect(control)
                if state.get("authorization_remaining_seconds", 0) < MIN_AUTHORIZATION_MARGIN:
                    raise TimeoutError("authorization window expired before approval")
            except Exception as exc:
                cleanup = self._abort_failed_gate(path, f"{type(exc).__name__}: {exc}")
                raise RuntimeError(f"authorization refused: {exc}; cleanup={cleanup}") from exc
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
        worker = self._inspect(control)
        if (not worker.get("alive") or not worker.get("matches") or
            worker.get("authorization_remaining_seconds", 0) < MIN_AUTHORIZATION_MARGIN):
            cleanup = self._abort_failed_gate(path, "worker authorization window expired before fixture launch")
            raise RuntimeError(f"fixture launch refused: worker authorization window expired; cleanup={cleanup}")
        argv = [DRIVES_PYTHON, DRIVES_FIXTURE, "launch", "--directory", guest_dir,
                "--run-id", control["run_id"], "--case", control["scenario"],
                "--duration", duration]
        launched = json.loads(self.drives.run(argv).splitlines()[-1])
        fixture = {"run_id": control["run_id"], "scenario": control["scenario"],
                   "pid": launched["pid"], "guest_dir": guest_dir}
        atomic_control(Path(control["local_dir"]) / "fixture-control.json", fixture)
        return self.fixture_probe(path)

    def fixture_authorize(self, path, approval_id, valid_for=180):
        try:
            return self._fixture_authorize_checked(path, approval_id, valid_for)
        except Exception as exc:
            control = json.loads(Path(path).read_text())
            if not (Path(control["local_dir"]) / "fixture-control.json").exists():
                cleanup = self._abort_failed_gate(path, f"fixture authorization: {type(exc).__name__}: {exc}")
                raise RuntimeError(f"fixture authorization refused: {exc}; cleanup={cleanup}") from exc
            raise

    def _fixture_authorize_checked(self, path, approval_id, valid_for=180):
        """Record one already granted operator approval for this exact run."""
        control = json.loads(Path(path).read_text())
        if control["scenario"] not in ("lane_hold", "merge_hold"):
            raise ValueError("scenario has no service fixture")
        if not 30 <= valid_for <= 180:
            raise ValueError("fixture authorization expiry out of bounds")
        local = Path(control["local_dir"])
        if not (local / "post-ready-gate.json").exists():
            raise ValueError("post-ready gate missing")
        valid(json.loads((local / "post-ready-gate.json").read_text()), control)
        preparation = json.loads((local / "preparation.json").read_text())
        prior = valid_preapproval(preparation, json.loads((local / "operator-approval.json").read_text()))
        if prior["approval_id"] != approval_id:
            raise ValueError("operator approval receipt differs from prepared run")
        if (local / "fixture-authorization-claimed.json").exists():
            raise ValueError("operator approval already claimed for this fixture")
        remaining = (prior["expires_wall_ns"] - time.time_ns()) / 1e9
        if remaining < 55:
            raise ValueError("operator approval expires before fixture startup")
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
        valid_preapproval(preparation, prior)
        # Guest issuance uses a bounded 20-second serial round trip. Reserve
        # 25 seconds so guest stop permission cannot outlive host approval.
        valid_for = min(valid_for, (prior["expires_wall_ns"] - time.time_ns()) / 1e9 - 25)
        if valid_for < 30:
            raise ValueError("operator approval expires before guest authorization")
        atomic_control(local / "fixture-authorization-claimed.json",
                       {"run_id": control["run_id"], "scenario": control["scenario"],
                        "approval_sha256": hashlib.sha256(approval_id.encode()).hexdigest(),
                        "claimed_utc": datetime.now(timezone.utc).isoformat()})
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
                telemetry = validate_hold_snapshot(sample, agreement["modbus"], motion)
                agreement["changing_telemetry"] = telemetry
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
                if (observed_id != row["package_id"] or
                    zone[:3] != [row["zone"], row["motion"], row["hold_reason"]] or
                    zone[5] != 1 or api["plant_status"][slot] != 1):
                    raise AssertionError(f"HMI package identity or hold invariant mismatch at slot {slot}")
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
        local_gate = json.loads((Path(control["local_dir"]) / "restoration-gate.json").read_text())
        global_gate = json.loads(RESTORATION_GATE.read_text())
        if (local_gate.get("run_id") != control["run_id"] or
            global_gate.get("run_id") != control["run_id"] or
            local_gate.get("status") not in ("PENDING", "PASS") or
            global_gate.get("status") not in ("PENDING", "PASS")):
            raise ValueError("restoration gate belongs to another run or is not verifiable")
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
        write_run_gate(Path(control["local_dir"]), control["run_id"], control["scenario"],
                       "PASS", result="completed", report=str(Path(control["local_dir"]) /
                                                          "typed-restoration-report.json"))
        return proof


def main(argv=None):
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--case", choices=CASES, required=True)
    p.add_argument("--typed-baseline", required=True)
    p.add_argument("--preflight-report", required=True)
    p = sub.add_parser("record-approval")
    p.add_argument("--preparation", required=True)
    p.add_argument("--approval-id", required=True)
    p.add_argument("--valid-for", type=float, default=APPROVAL_LIFETIME)
    p = sub.add_parser("launch")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--case", choices=CASES, required=True)
    p.add_argument("--monitor-control", required=True)
    p.add_argument("--typed-baseline", required=True)
    p.add_argument("--preflight-report", required=True)
    p.add_argument("--preparation")
    p.add_argument("--startup-timeout", type=float, default=120)
    p.add_argument("--hold-timeout", type=float, default=30)
    for name in ("probe-ready", "post-ready-gate", "authorize", "begin", "probe-checkpoint", "release", "wait",
                 "collect", "abort", "evidence", "verify-clean", "fixture-authorize",
                 "fixture-start", "fixture-stop"):
        p = sub.add_parser(name)
        p.add_argument("--control", required=True)
        p.add_argument("--timeout", type=float, default=15)
        if name == "post-ready-gate":
            p.set_defaults(timeout=POST_READY_GATE_TIMEOUT)
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
        if args.action == "prepare":
            result = {"preparation": str(control.prepare(args.evidence_dir, args.case,
                                                          args.typed_baseline, args.preflight_report))}
        elif args.action == "record-approval":
            result = control.record_approval(args.preparation, args.approval_id, args.valid_for)
        elif args.action == "launch":
            result = {"control": str(control.launch(args.evidence_dir, args.case,
                                                    args.monitor_control, args.typed_baseline,
                                                    args.preflight_report,
                                                    args.startup_timeout, args.hold_timeout,
                                                    args.preparation))}
        elif args.action == "probe-ready":
            result = control.probe_ready(args.control, args.timeout)
        elif args.action == "post-ready-gate":
            result = control.pre_authorization_gate(args.control, args.timeout)
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
        if args.action not in ("launch", "prepare", "record-approval"):
            record = json.loads(Path(args.control).read_text())
            category = ("evidence_failure" if args.action in ("evidence", "probe-checkpoint") else
                        "monitor_failure" if args.action in ("probe-ready", "post-ready-gate", "authorize") else
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

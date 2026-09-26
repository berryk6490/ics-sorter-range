"""One bounded host command from post-approval preflight through restoration."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from accumulation_monitor_control import Controller as MonitorController, atomic_control
from accumulation_scenario_control import ScenarioController, APPROVAL_LIFETIME
from accumulation_state_snapshot import capture as typed_capture, save as typed_save
from browser_accumulation import LIVE_CASES
from deployment_preflight import PreflightFailure, guest_read

ROOT = Path(__file__).resolve().parent
PROXY = ROOT / "serial_hmi_proxy.py"
BROWSER = ROOT / "browser_accumulation.py"
RECOVERY = "/home/kevin/sorter-services/recover_accumulation_state.py"
PREFLIGHT = ROOT / "deployment_preflight.py"
FIXTURE_CASES = ("lane_hold", "merge_hold")
PACKAGE_CASES = FIXTURE_CASES + ("drive_stop",)
ATTEMPT_CASES = PACKAGE_CASES + ("smoke",)
BROWSER_CASES = {"lane_hold": "lane_hold", "merge_hold": "merge_hold",
                 "drive_stop": "drive_stop"}


def browser_argv(case, output_dir):
    mapped = BROWSER_CASES.get(case)
    if mapped not in LIVE_CASES:
        raise ValueError(f"unsupported live browser scenario: {case}")
    return [sys.executable, str(BROWSER), mapped, "--output-dir", str(output_dir)]


def error(exc):
    return f"{type(exc).__name__}: {exc}"


def need_recovery(state):
    return (any(row[4] for row in state["slots"]) or
            any(state["plant_faults"][:2]) or state["run_identity"]["epoch_fault"] or
            any(state["photoeye_faults"]) or state["zone_view"][22])


def verify_postflight(sc, control_path, local, *, shell_probe=guest_read):
    """Retry one read-only typed capture after a proven SCADA transport lapse."""
    attempts = []
    for number in (1, 2):
        try:
            report = sc.verify_clean(control_path)
            attempts.append({"number": number, "status": "PASS"})
            atomic_control(local / "postflight-attempts.json", attempts)
            return report
        except PreflightFailure as exc:
            attempts.append({"number": number, "status": "FAIL", "error": error(exc),
                             "kind": exc.kind})
            atomic_control(local / "postflight-attempts.json", attempts)
            if number != 1 or exc.kind != "liveness_wrapper_error" or "scada" not in str(exc):
                raise
            # guest_read itself requires completion, prompt and a distinct
            # nonce-bound liveness probe. This command has no PLC side effect.
            try:
                shell_probe("scada", "printf 'POSTFLIGHT_SHELL_READY\\n'",
                            "postflight_transport_retry")
                attempts.append({"event": "shell_probe", "status": "PASS"})
            except BaseException as probe_exc:
                attempts.append({"event": "shell_probe", "status": "FAIL",
                                 "error": error(probe_exc)})
                atomic_control(local / "postflight-attempts.json", attempts)
                raise
            atomic_control(local / "postflight-attempts.json", attempts)
        except BaseException as exc:
            attempts.append({"number": number, "status": "FAIL", "error": error(exc)})
            atomic_control(local / "postflight-attempts.json", attempts)
            raise


def evidence_hashes(root, stdout_path=None):
    # A caller may redirect this command's final JSON to a file under the
    # evidence root. That file grows *after* this function runs; exclude only
    # that active output and let the caller hash it after process exit.
    if stdout_path is None:
        try:
            stdout_path = Path("/proc/self/fd/1").resolve()
        except OSError:
            stdout_path = None
    stdout_path = Path(stdout_path).resolve() if stdout_path is not None else None
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS" and path.resolve() != stdout_path:
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}")
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n")
    return len(rows)


def approval_age(approved_at, *, now=None):
    try:
        when = datetime.fromisoformat(approved_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("approval timestamp missing or malformed") from exc
    if when.tzinfo is None:
        raise ValueError("approval timestamp must include timezone")
    seconds = ((now or datetime.now(timezone.utc)) - when).total_seconds()
    if not 0 <= seconds < APPROVAL_LIFETIME:
        raise ValueError("operator approval expired or dated in the future")
    return seconds


def live_preflight(report, log):
    with log.open("w") as stream:
        subprocess.run([sys.executable, str(PREFLIGHT), "--live", "--report", str(report)],
                       cwd=ROOT.parent, stdout=stream, stderr=subprocess.STDOUT,
                       timeout=180, check=True)


def run_reserved(reservation, approved_at, approval_id=None, *, scenario=None,
                 preflight_run=live_preflight, capture=typed_capture, **attempt_options):
    """Spend one reservation, then create a new baseline after human approval."""
    sc = scenario or ScenarioController()
    reservation, record = sc.claim_reservation(reservation)
    root = reservation.parent.parent
    run_id, case = record["run_id"], record["scenario"]
    if case not in ATTEMPT_CASES:
        raise ValueError("unsupported canonical attempt scenario")
    if case in PACKAGE_CASES:
        browser_argv(case, reservation.parent.parent / run_id)
    result_path = reservation.with_suffix(".result.json")
    preflight = reservation.with_suffix(".preflight.json")
    baseline = reservation.with_suffix(".typed-before.json")
    approval = None
    try:
        if case in FIXTURE_CASES and not approval_id:
            raise ValueError("fresh approval receipt ID required for plant fixture")
        if case not in FIXTURE_CASES and approval_id:
            raise ValueError("scenario has no plant-service approval receipt")
        approval = approval_age(approved_at)
        start = time.monotonic()
        preflight_run(preflight, reservation.with_suffix(".preflight.log"))
        preflight_seconds = round(time.monotonic() - start, 3)
        typed_save(baseline, capture())
        # The human wait has no worker timer, but the attestation itself is
        # short lived and checked again immediately before receipt issuance.
        approval_at_receipt = approval_age(approved_at)
        prepared = sc.activate_reservation(reservation, record, baseline, preflight)
        approval_at_receipt = approval_age(approved_at)
        outcome = run_attempt(prepared, approval_id, scenario=sc, **attempt_options)
        outcome["reservation_age_seconds"] = round(
            (datetime.now(timezone.utc) - datetime.fromisoformat(record["reserved_utc"])).total_seconds(), 3)
        outcome["approval_age_before_preflight_seconds"] = round(approval, 3)
        outcome["approval_age_at_receipt_seconds"] = round(approval_at_receipt, 3)
        outcome["fresh_preflight_seconds"] = preflight_seconds
        atomic_control(prepared.parent / "attempt-result.json", outcome)
        evidence_hashes(root)
        return outcome
    except BaseException as exc:
        failure = {"run_id": run_id, "scenario": case, "status": "FAIL",
                   "functional_outcome": "NOT_STARTED", "evidence_quality": "NOT_CAPTURED",
                   "service_restoration": "NOT_TOUCHED", "typed_postflight": "NOT_RUN",
                   "orphans": "NONE_STARTED", "approval_receipt_spent": False,
                   "error": error(exc), "recorded_utc": datetime.now(timezone.utc).isoformat()}
        atomic_control(result_path, failure)
        evidence_hashes(root)
        return failure


def start_proxy(path, *, timeout=8):
    stream = path.open("w")
    process = subprocess.Popen([sys.executable, str(PROXY)],
                               stdout=stream, stderr=subprocess.STDOUT)
    stream.close()
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError(f"HMI proxy exited {process.returncode}")
            try:
                with urlopen("http://127.0.0.1:18000/api", timeout=1) as response:
                    if response.status == 200:
                        return process
            except OSError:
                time.sleep(.15)
        raise TimeoutError("HMI proxy did not become ready")
    except BaseException:
        stop_proxy(process)
        raise


def start_driver(path, *, timeout=8):
    stream = path.open("w")
    process = subprocess.Popen(["geckodriver", "--port", "4445"],
                               stdout=stream, stderr=subprocess.STDOUT)
    stream.close()
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError(f"geckodriver exited {process.returncode}")
            try:
                with urlopen("http://127.0.0.1:4445/status", timeout=1) as response:
                    if response.status == 200:
                        return process
            except OSError:
                time.sleep(.15)
        raise TimeoutError("rendered browser driver did not become ready")
    except BaseException:
        stop_proxy(process)
        raise


def stop_proxy(process):
    if process is None:
        return None
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        return "proxy required SIGKILL"
    return None


def run_attempt(preparation, approval_id=None, *, scenario=None, monitor=None,
                proxy_start=start_proxy, driver_start=start_driver, browser_run=None):
    preparation = Path(preparation).resolve()
    reserved = json.loads(preparation.read_text())
    case = reserved["scenario"]
    if case not in ATTEMPT_CASES:
        raise ValueError("unsupported canonical attempt scenario")
    browser_args = browser_argv(case, preparation.parent) if case in PACKAGE_CASES else None
    if case in FIXTURE_CASES and not approval_id:
        raise ValueError("fresh run-bound operator approval ID required")
    if case not in FIXTURE_CASES and approval_id:
        raise ValueError("scenario has no plant-service approval receipt")
    local = preparation.parent
    sc = scenario or ScenarioController()
    mc = monitor or MonitorController()
    result = {"run_id": reserved["run_id"], "scenario": case,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "functional_outcome": "NOT_STARTED", "evidence_quality": "NOT_CAPTURED",
              "service_restoration": "NOT_APPLICABLE" if case not in FIXTURE_CASES else "UNKNOWN",
              "plc_recovery": "NOT_NEEDED", "typed_postflight": "NOT_RUN",
              "orphans": "UNKNOWN", "errors": {}, "artifacts": {}}
    monitor_path = control_path = None
    proxy = driver = None
    released = False
    begun = False
    try:
        if case in FIXTURE_CASES:
            result["approval"] = sc.record_approval(preparation, approval_id)
        monitor_path = mc.start(local.parent / "monitor", duration=360)
        result["artifacts"]["monitor_control"] = str(monitor_path)
        result["monitor_ready"] = mc.probe(monitor_path, timeout=12)["run_id"]
        control_path = sc.launch(local.parent, case, monitor_path,
                                 reserved["typed_baseline"], reserved["preflight_report"],
                                 startup_timeout=120, hold_timeout=60,
                                 preparation_path=preparation)
        result["artifacts"]["scenario_control"] = str(control_path)
        sc.probe_ready(control_path, timeout=12)
        result["post_ready_gate"] = sc.pre_authorization_gate(control_path)
        sc.action(control_path, "authorize")
        if case in FIXTURE_CASES:
            sc.fixture_authorize(control_path, approval_id)
            sc.fixture_start(control_path, duration=180)
            sc.fixture_probe(control_path)
        sc.action(control_path, "begin")
        begun = True
        result["checkpoint"] = sc.probe_checkpoint(control_path, timeout=90)
        proxy = proxy_start(local / "hmi-proxy.log")
        if case in PACKAGE_CASES:
            driver = driver_start(local / "geckodriver.log")
            if browser_run is None:
                completed = subprocess.run(
                    browser_args,
                    capture_output=True, text=True, timeout=35, check=True)
                observation = json.loads(completed.stdout.splitlines()[-1])
            else:
                observation = browser_run(case, local)
            if observation.get("case") != BROWSER_CASES[case]:
                raise ValueError("browser evidence scenario mismatch")
            screenshot = Path(observation["screenshot"])
            result["artifacts"]["screenshot"] = str(screenshot)
        else:
            screenshot = None
        result["evidence"] = sc.evidence(control_path, screenshot=screenshot)
        result["evidence_quality"] = "PASS"
        proxy_error = stop_proxy(proxy)
        proxy = None
        driver_error = stop_proxy(driver)
        driver = None
        if proxy_error:
            raise RuntimeError(proxy_error)
        if driver_error:
            raise RuntimeError(driver_error)
        sc.action(control_path, "release")
        released = True
        result["release_sent"] = True
    except BaseException as exc:
        result["errors"]["attempt"] = error(exc)
        result["functional_outcome"] = "FAIL"
    finally:
        try:
            proxy_error = stop_proxy(proxy)
            if proxy_error:
                result["errors"]["proxy_cleanup"] = proxy_error
        except BaseException as exc:
            result["errors"]["proxy_cleanup"] = error(exc)
        try:
            driver_error = stop_proxy(driver)
            if driver_error:
                result["errors"]["browser_driver_cleanup"] = driver_error
        except BaseException as exc:
            result["errors"]["browser_driver_cleanup"] = error(exc)
        if control_path is not None:
            if not released:
                try:
                    sc.action(control_path, "abort")
                    result["abort_sent"] = True
                except BaseException as exc:
                    result["errors"]["abort"] = error(exc)
            try:
                collected = sc.collect(control_path, timeout=240)
                result["terminal"] = collected["terminal"]
                result["orphans"] = "FAIL" if collected["orphan"] else "PASS"
                result["service_restoration"] = (
                    "NOT_APPLICABLE" if case not in FIXTURE_CASES else
                    "PASS" if
                    collected["terminal"].get("fixture_restoration", {}).get("service_restored")
                    else "FAIL")
                if result["functional_outcome"] != "FAIL":
                    result["functional_outcome"] = (
                        "PASS" if collected["terminal"].get("status") == "complete" and
                        not collected.get("cleanup_error") else "FAIL")
                # Preserve the terminal result before any operator reset clears
                # PLC counters and transient package rows.
                atomic_control(local / "outcome-before-reset.json", {
                    "run_id": result["run_id"], "functional_outcome": result["functional_outcome"],
                    "terminal": collected["terminal"],
                    "recorded_utc": datetime.now(timezone.utc).isoformat()})
                if case in PACKAGE_CASES:
                    observations = []
                    for line in (local / "stdout.log").read_text().splitlines():
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "journal" in row and "rows" in row:
                            observations.append(row)
                    if len(observations) == 1 and observations[0].get("case") == case:
                        atomic_control(local / "scenario-output-before-reset.json", observations[0])
                    else:
                        result["errors"]["outcome_capture"] = (
                            "exactly one matching structured package outcome is required")
                        result["functional_outcome"] = "FAIL"
            except BaseException as exc:
                result["errors"]["collection"] = error(exc)
                result["functional_outcome"] = "FAIL"
                if case in FIXTURE_CASES:
                    try:
                        result["fixture_terminal"] = sc.fixture_stop(control_path, timeout=25)
                        result["service_restoration"] = "PASS"
                    except BaseException as restore_exc:
                        result["errors"]["service_restoration"] = error(restore_exc)
                        result["service_restoration"] = "FAIL"
        if monitor_path is not None:
            try:
                monitor_result = mc.collect(monitor_path, timeout=25, stop=True)
                result["monitor_collection"] = monitor_result
                if monitor_result.get("orphan") or monitor_result.get("cleanup_errors"):
                    result["errors"]["monitor_cleanup"] = monitor_result
                    result["orphans"] = "FAIL" if monitor_result.get("orphan") else result["orphans"]
            except BaseException as exc:
                result["errors"]["monitor_cleanup"] = error(exc)
        if control_path is not None:
            try:
                state = sc.state()
                if (case in PACKAGE_CASES and (begun or need_recovery(state))):
                    if state["coils_880_920"][0]:
                        raise RuntimeError("sorter still running before operator reset")
                    if case in FIXTURE_CASES and result["service_restoration"] != "PASS":
                        raise RuntimeError("plant service not restored before operator reset")
                    if not (local / "outcome-before-reset.json").exists():
                        raise RuntimeError("terminal outcome not preserved before operator reset")
                    result["plc_recovery"] = "ATTEMPTED"
                    output = sc.scada.run(["/home/kevin/opcua/bin/python", RECOVERY,
                                           "--reset-run"],
                                          timeout=55)
                    result["plc_recovery"] = json.loads(output.splitlines()[-1])
                result["postflight"] = verify_postflight(sc, control_path, local)
                result["typed_postflight"] = "PASS"
                result["orphans"] = "PASS"
            except BaseException as exc:
                key = "plc_recovery" if result["plc_recovery"] == "ATTEMPTED" else "postflight"
                result["errors"][key] = error(exc)
                if key == "plc_recovery":
                    result["plc_recovery"] = "FAIL"
                result["typed_postflight"] = "FAIL"
        result["completed_utc"] = datetime.now(timezone.utc).isoformat()
        if result["errors"] or result["functional_outcome"] != "PASS" or \
                result["evidence_quality"] != "PASS" or result["typed_postflight"] != "PASS":
            result["status"] = "FAIL"
        else:
            result["status"] = "PASS"
        atomic_control(local / "attempt-result.json", result)
        result["evidence_files_hashed"] = evidence_hashes(local.parent)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reservation", type=Path)
    source.add_argument("--preparation", type=Path)
    parser.add_argument("--approval-id")
    parser.add_argument("--approved-at", help="UTC timestamp of this run's operator approval")
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise InterruptedError(f"host attempt interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    outcome = (run_reserved(args.reservation, args.approved_at, args.approval_id)
               if args.reservation else run_attempt(args.preparation, args.approval_id))
    print(json.dumps(outcome, sort_keys=True, default=str), flush=True)
    raise SystemExit(0 if outcome["status"] == "PASS" else 1)

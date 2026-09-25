"""One bounded host command for an approved Phase 2A validation attempt.

Full deployment preflight, typed baseline and run preparation precede this
command. For a fixture run, the supplied approval ID is spent on entry.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from accumulation_monitor_control import Controller as MonitorController, atomic_control
from accumulation_scenario_control import ScenarioController

ROOT = Path(__file__).resolve().parent
PROXY = ROOT / "serial_hmi_proxy.py"
BROWSER = ROOT / "browser_accumulation.py"
RECOVERY = "/home/kevin/sorter-services/recover_accumulation_state.py"


def error(exc):
    return f"{type(exc).__name__}: {exc}"


def need_recovery(state):
    return (any(row[4] for row in state["slots"]) or
            any(state["plant_faults"][:2]) or state["run_identity"]["epoch_fault"] or
            any(state["photoeye_faults"]) or state["zone_view"][22])


def evidence_hashes(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}")
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n")
    return len(rows)


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
    if case not in ("lane_hold", "smoke"):
        raise ValueError("canonical attempt supports lane_hold or smoke")
    if case == "lane_hold" and not approval_id:
        raise ValueError("fresh run-bound operator approval ID required")
    if case == "smoke" and approval_id:
        raise ValueError("no service approval is used by smoke")
    local = preparation.parent
    sc = scenario or ScenarioController()
    mc = monitor or MonitorController()
    result = {"run_id": reserved["run_id"], "scenario": case,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "functional_outcome": "NOT_STARTED", "evidence_quality": "NOT_CAPTURED",
              "service_restoration": "NOT_APPLICABLE" if case == "smoke" else "UNKNOWN",
              "plc_recovery": "NOT_NEEDED", "typed_postflight": "NOT_RUN",
              "orphans": "UNKNOWN", "errors": {}, "artifacts": {}}
    monitor_path = control_path = None
    proxy = driver = None
    released = False
    try:
        if case == "lane_hold":
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
        if case == "lane_hold":
            sc.fixture_authorize(control_path, approval_id)
            sc.fixture_start(control_path, duration=180)
            sc.fixture_probe(control_path)
        sc.action(control_path, "begin")
        result["checkpoint"] = sc.probe_checkpoint(control_path, timeout=90)
        proxy = proxy_start(local / "hmi-proxy.log")
        if case == "lane_hold":
            driver = driver_start(local / "geckodriver.log")
            if browser_run is None:
                completed = subprocess.run(
                    [sys.executable, str(BROWSER), "lane_hold", "--output-dir", str(local)],
                    capture_output=True, text=True, timeout=35, check=True)
                observation = json.loads(completed.stdout.splitlines()[-1])
            else:
                observation = browser_run(case, local)
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
                    "PASS" if case == "smoke" or
                    collected["terminal"].get("fixture_restoration", {}).get("service_restored")
                    else "FAIL")
                if result["functional_outcome"] != "FAIL":
                    result["functional_outcome"] = (
                        "PASS" if collected["terminal"].get("status") == "complete" and
                        not collected.get("cleanup_error") else "FAIL")
            except BaseException as exc:
                result["errors"]["collection"] = error(exc)
                result["functional_outcome"] = "FAIL"
                if case == "lane_hold":
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
                if need_recovery(state):
                    result["plc_recovery"] = "ATTEMPTED"
                    output = sc.scada.run(["/home/kevin/opcua/bin/python", RECOVERY],
                                          timeout=55)
                    result["plc_recovery"] = json.loads(output.splitlines()[-1])
                result["postflight"] = sc.verify_clean(control_path)
                result["typed_postflight"] = "PASS"
                result["orphans"] = "PASS"
            except BaseException as exc:
                result["errors"]["postflight"] = error(exc)
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
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--approval-id")
    args = parser.parse_args()
    outcome = run_attempt(args.preparation, args.approval_id)
    print(json.dumps(outcome, sort_keys=True, default=str), flush=True)
    raise SystemExit(0 if outcome["status"] == "PASS" else 1)

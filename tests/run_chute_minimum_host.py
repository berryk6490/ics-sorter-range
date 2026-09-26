"""One bounded chute run, genuine browser proof, then independent restoration.

Run only after the approved deployment, exact hash preflight, stopped typed
baseline and temporary four-package ASX plan are recorded. The guest runner
has its own deadline and stops master on every exit. This host controller
always restores the ASX bytes and invokes canonical PLC recovery.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import pexpect

import accumulation_state_snapshot as typed
import chute_migration_postflight as migration
from run_accumulation_attempt import start_proxy, start_driver, stop_proxy
from serial_command import execute as guest

ROOT = Path(__file__).resolve().parents[1]
PLAN = "/etc/sorter-xle/sort-plan.json"
PLAN_BACKUP = ("/home/kevin/sorter-backups/"
               "chute-preflight-PpZ2fWYc/xle/01-sort-plan.json")
ORIGINAL_PLAN_HASH = "b8ce42bb2a16a47d01f8efb50d981c82e897496477d657ea0aeedf680295bc2b"
TEMP_PLAN_HASH = "97bf1116950a096a8f308cbcb0cc7effb9be9a76c86d7bdefdbdb41df42c164a"
RECOVERY = ("/home/kevin/opcua/bin/python "
            "/home/kevin/sorter-services/recover_accumulation_state.py "
            "--remote-xle --reset-run")


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def root_xle(command):
    """Use only an already open root serial shell; never accept credentials."""
    tty = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", "xle"],
                        encoding="utf-8", timeout=25, maxread=200000)
    try:
        tty.expect("Escape character")
        tty.sendline("")
        tty.expect(r"root@xle:[^\r\n]*# ")
        marker = "__CHUTE_ROOT_RC__"
        tty.sendline(command + f"; printf '{marker}%s\\n' \"$?\"")
        tty.expect(re.escape(marker) + r"(\d+)", timeout=25)
        output, rc = tty.before, int(tty.match.group(1))
        tty.expect(r"root@xle:[^\r\n]*# ")
        if rc:
            raise RuntimeError(f"xle root command rc={rc}: {output[-500:]}")
        return output
    finally:
        tty.send("\x1d")
        tty.close()


def restore_plan():
    before = root_xle("sha256sum " + PLAN)
    if ORIGINAL_PLAN_HASH in before:
        return {"status": "PASS", "already_original": True,
                "sha256": ORIGINAL_PLAN_HASH}
    if TEMP_PLAN_HASH not in before:
        raise RuntimeError("ASX plan changed unexpectedly; refusing overwrite")
    command = ("install -o root -g root -m 0644 " + PLAN_BACKUP + " " + PLAN +
               " && sha256sum " + PLAN)
    after = root_xle(command)
    if ORIGINAL_PLAN_HASH not in after:
        raise RuntimeError("ASX byte restoration hash mismatch")
    return {"status": "PASS", "already_original": False,
            "sha256": ORIGINAL_PLAN_HASH}


def drop_root_xle():
    """Return from the temporary sudo shell to the persistent kevin shell."""
    tty = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", "xle"],
                        encoding="utf-8", timeout=15)
    try:
        tty.expect("Escape character")
        tty.sendline("")
        tty.expect(r"root@xle:[^\r\n]*# ")
        tty.sendline("exit")
        tty.expect(r"kevin@xle:.*\$ ")
    finally:
        tty.send("\x1d")
        tty.close()


def fetch_guest_jsonl(path, output):
    read = guest("drives", "gzip -c " + path + " | base64 -w0", timeout=90)
    if read["command_rc"]:
        raise RuntimeError("guest evidence fetch failed")
    lines = [line for line in read["output"].splitlines()
             if re.fullmatch(r"[A-Za-z0-9+/=]{100,}", line)]
    if len(lines) != 1:
        raise RuntimeError("missing or ambiguous compressed guest evidence")
    import gzip
    payload = gzip.decompress(base64.b64decode(lines[0]))
    output.write_bytes(payload)
    if not payload.endswith(b"\n"):
        raise RuntimeError("truncated guest JSONL")
    return sum(1 for line in payload.splitlines() if json.loads(line))


def journal_outcomes(epoch, nonce, seconds=8):
    attempts = []
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        output = root_xle("python3 /opt/sorter-xle/read_chute_journal.py "
                          f"--epoch {epoch} --nonce {nonce}")
        record = next(json.loads(line) for line in output.splitlines()
                      if line.startswith("{") and '"outcomes"' in line)
        attempts.append({"wall_ns": time.time_ns(), "count": record["outcome_count"]})
        if record["outcome_count"] == 4:
            return record, attempts
        time.sleep(.4)
    return record, attempts


def run(evidence, baseline_old, baseline_new):
    evidence.mkdir(parents=True, exist_ok=False)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    guest_file = f"/home/kevin/chute-minimum-{run_id}.jsonl"
    result = {"run_id": run_id, "functional": "FAIL", "evidence": "NOT_CAPTURED",
              "service_restoration": "NOT_RUN", "plc_recovery": "NOT_RUN",
              "typed_postflight": "NOT_RUN", "orphans": "NOT_CHECKED",
              "errors": {}}
    proxy = driver = worker = browser = None
    worker_stream = browser_stream = None
    try:
        if TEMP_PLAN_HASH not in root_xle("sha256sum " + PLAN):
            raise RuntimeError("run-specific ASX plan missing")
        proxy = start_proxy(evidence / "proxy.log")
        driver = start_driver(evidence / "geckodriver.log")
        worker_stream = (evidence / "guest-runner.log").open("w")
        worker = subprocess.Popen([sys.executable, str(ROOT / "tests/serial_command.py"),
                "drives", "/home/kevin/venv/bin/python /home/kevin/live_chute_minimum.py "
                f"--evidence {guest_file} --duration 210", "--timeout", "260", "--json"],
                stdout=worker_stream, stderr=subprocess.STDOUT)
        browser_stream = (evidence / "browser.log").open("w")
        browser = subprocess.Popen([sys.executable, str(ROOT / "tests/browser_chute_minimum.py"),
                "--output-dir", str(evidence / "browser")],
                stdout=browser_stream, stderr=subprocess.STDOUT)
        try:
            browser.wait(timeout=180)
        except subprocess.TimeoutExpired:
            result["errors"]["browser"] = "browser evidence timeout"
            browser.terminate()
            browser.wait(timeout=5)
        try:
            worker.wait(timeout=275)
        except subprocess.TimeoutExpired:
            result["errors"]["guest_runner"] = "guest wrapper timeout"
            worker.terminate()
            worker.wait(timeout=5)
        worker_stream.close(); worker_stream = None
        browser_stream.close(); browser_stream = None
        lines = (evidence / "guest-runner.log").read_text().splitlines()
        wrapper = next((json.loads(line) for line in reversed(lines)
                        if line.startswith("{") and '"completion_marker"' in line), None)
        terminal = None
        if wrapper:
            terminal = next((json.loads(line) for line in wrapper["output"].splitlines()
                             if line.startswith("{") and '"functional"' in line), None)
        save(evidence / "guest-terminal.json", terminal or {"error": "missing terminal"})
        result["functional"] = ("PASS" if worker.returncode == 0 and terminal and
                terminal.get("functional") == "PASS" and
                terminal.get("cleanup", {}).get("status") == "PASS" and
                browser.returncode == 0 else "FAIL")
        result["browser_exit"] = browser.returncode
        result["runner_exit"] = worker.returncode
        if terminal and terminal.get("epoch") and terminal.get("nonce"):
            record, attempts = journal_outcomes(terminal["epoch"], terminal["nonce"])
            save(evidence / "xle-journal.json", record)
            save(evidence / "xle-journal-probes.json", attempts)
            if record["outcome_count"] != 4:
                result["functional"] = "FAIL"
                result["errors"]["journal"] = "expected four outcomes"
        result["samples"] = fetch_guest_jsonl(guest_file, evidence / "direct-plc-raw-plant.jsonl")
        plant_log = guest("drives", "journalctl --no-pager "
                          "-u sorter-plant.service -n 160", timeout=30)
        (evidence / "plant-journal.log").write_text(plant_log["output"])
        for unit, name in (("sorter-xle.service", "xle-service.log"),
                           ("sorter-asx.service", "asx-service.log")):
            output = root_xle("journalctl --no-pager -u " + unit + " -n 160")
            (evidence / name).write_text(output)
        result["evidence"] = "PASS" if browser.returncode == 0 else "FAIL"
        if result["evidence"] != "PASS":
            result["functional"] = "FAIL"
    except BaseException as exc:
        result["errors"]["scenario"] = repr(exc)
    finally:
        if browser is not None and browser.poll() is None:
            browser.terminate()
            try: browser.wait(timeout=5)
            except subprocess.TimeoutExpired: browser.kill(); browser.wait()
        if worker is not None and worker.poll() is None:
            # Guest runner has its own 210-second deadline and must stop master.
            try: worker.wait(timeout=265)
            except subprocess.TimeoutExpired:
                worker.terminate(); worker.wait(timeout=5)
                result["errors"]["worker_cleanup"] = "host wrapper timeout"
        if worker_stream: worker_stream.close()
        if browser_stream: browser_stream.close()
        for name, process in (("proxy", proxy), ("driver", driver)):
            try:
                error = stop_proxy(process)
                if error: result["errors"][name] = error
            except BaseException as exc:
                result["errors"][name] = repr(exc)
        try:
            result["service_restoration"] = restore_plan()
        except BaseException as exc:
            result["service_restoration"] = {"status": "FAIL", "error": repr(exc)}
        try:
            drop_root_xle()
        except BaseException as exc:
            result["errors"]["root_shell_cleanup"] = repr(exc)
        try:
            response = guest("scada", RECOVERY, timeout=65)
            (evidence / "plc-recovery.log").write_text(response["output"])
            record = next(json.loads(line) for line in response["output"].splitlines()
                          if line.startswith("{") and '"status"' in line)
            result["plc_recovery"] = record
            if response["command_rc"] or record["status"] != "PASS":
                result["errors"]["plc_recovery"] = "canonical recovery failed"
        except BaseException as exc:
            result["plc_recovery"] = {"status": "FAIL", "error": repr(exc)}
        try:
            after = typed.capture()
            typed.save(str(evidence / "typed-after.json"), after)
            old = migration.compare_migration(json.loads(baseline_old.read_text()), after)
            same = typed.compare(json.loads(baseline_new.read_text()), after)
            save(evidence / "typed-migration.json", old)
            save(evidence / "typed-same-program.json", same)
            result["typed_postflight"] = ("PASS" if old["status"] == same["status"] == "PASS"
                                          else "FAIL")
            result["orphans"] = "NONE" if same["status"] == "PASS" else "UNVERIFIED"
        except BaseException as exc:
            result["typed_postflight"] = "FAIL"
            result["errors"]["typed"] = repr(exc)
        if (result["service_restoration"].get("status") != "PASS" or
            result["plc_recovery"].get("status") != "PASS" or
            result["typed_postflight"] != "PASS"):
            result["errors"]["restoration"] = "one or more restoration gates failed"
        save(evidence / "result.json", result)
        hashes = [(hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(evidence))
                  for p in evidence.rglob("*") if p.is_file() and p.name != "SHA256SUMS"]
        (evidence / "SHA256SUMS").write_text("".join(f"{h}  {p}\n" for h, p in sorted(hashes)))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--old-baseline", type=Path, required=True)
    parser.add_argument("--new-baseline", type=Path, required=True)
    args = parser.parse_args()
    outcome = run(args.evidence, args.old_baseline, args.new_baseline)
    print(json.dumps(outcome, sort_keys=True))
    raise SystemExit(0 if (outcome["functional"] == "PASS" and
                           outcome["evidence"] == "PASS" and
                           outcome["service_restoration"].get("status") == "PASS" and
                           outcome["plc_recovery"].get("status") == "PASS" and
                           outcome["typed_postflight"] == "PASS") else 1)

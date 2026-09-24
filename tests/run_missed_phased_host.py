"""Guarded host orchestration for one missed-tunnel live evidence run."""
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
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
GUEST_DIR_PREFIX = "/tmp/sorter-missed-"
REQUIRED = {
    "plc": ["openplc.service"],
    "drives": ["sorter-plant.service", *[f"scanner@tunnel{i}.service" for i in (1, 2, 3)],
               *[f"vfd@{name}.service" for name in
                 ("induct1", "induct2", "induct3", "outbnd1", "outbnd2", "outbnd3")]],
    "scada": ["opcua-server.service", "sorter-hmi.service"],
}


def plain(output):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)


def guest(vm, command, timeout=30):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/serial_command.py"), vm, command,
         "--timeout", str(timeout)], capture_output=True, text=True,
        timeout=timeout + 10)
    if result.returncode:
        raise RuntimeError(f"{vm}: {command}: {result.stdout[-800:]} {result.stderr[-800:]}")
    return result.stdout


def guest_json(vm, command):
    output = guest(vm, command)
    for line in output.splitlines():
        try:
            value = json.loads(line.strip())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"no JSON in {vm} response: {output[-800:]}")


def state():
    vms = {}
    for name in ("drives", "plc", "fw", "scada", "analyst", "base", "lfs-lab", "ubuntu24.04"):
        r = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", name],
                           capture_output=True, text=True, check=True)
        vms[name] = r.stdout.strip()
    services = {vm: {} for vm in REQUIRED}
    for vm, names in REQUIRED.items():
        output = guest(vm, "systemctl is-active " + " ".join(names))
        lines = [line.strip() for line in output.splitlines() if line.strip() == "active"]
        assert len(lines) == len(names), (vm, output)
        services[vm] = dict.fromkeys(names, "active")
    return {"vms": vms, "services": services}


PLC_READ = '''import json
from pymodbus.client import ModbusTcpClient
c=ModbusTcpClient("10.10.1.10",port=502,timeout=2)
assert c.connect()
r=lambda a,n=1:c.read_holding_registers(a,n,slave=1).registers
b=lambda a,n=1:c.read_coils(a,n,slave=1).bits[:n]
print(json.dumps(dict(identity=r(249)[0],run=b(880)[0],slots=r(530,24)+r(647,12),
 lanes=r(644,2)+r(659),modes=b(914,6),plant_fault=r(591)[0],
 photoeye_fault=r(748,3),scanner_state=r(255,4),scanner_nonce=r(509)[0],
 enables=b(881,7),setpoints=r(200,11),seed=r(247)[0],
 photoeye_config=r(744,4),counters=r(220,11))))
c.close()'''

SCAN_READ = '''import json
from pymodbus.client import ModbusTcpClient
nonces=[]
for last in (27,28,29):
 client=ModbusTcpClient("10.10.1.%d" % last,port=502,timeout=2)
 assert client.connect()
 nonces.append(client.read_holding_registers(10,1,slave=1).registers[0])
 client.close()
print(json.dumps({"device_nonces":nonces}))'''


def plc_state():
    payload = base64.b64encode(PLC_READ.encode()).decode()
    command = ("/home/kevin/opcua/bin/python -c " + shlex.quote(
        f"import base64;exec(base64.b64decode('{payload}'))"))
    result = guest_json("scada", command)
    scanner_payload = base64.b64encode(SCAN_READ.encode()).decode()
    scanner_command = "/home/kevin/venv/bin/python -c " + shlex.quote(
        f"import base64;exec(base64.b64decode('{scanner_payload}'))")
    result.update(guest_json("drives", scanner_command))
    return result


def preflight():
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip(), "Git must be clean"
    subprocess.run([sys.executable, str(ROOT / "tests/deployment_preflight.py"), "--live"],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=90)
    machine = state()
    assert all(machine["vms"][vm] == "running" for vm in REQUIRED)
    plc = plc_state()
    assert plc["identity"] == 24113, plc
    assert not plc["run"] and [plc["slots"][i] for i in (0, 12, 24)] == [0, 0, 0], plc
    assert plc["plant_fault"] == 0 and plc["photoeye_fault"] == [0, 0, 0], plc
    assert plc["scanner_state"][0:2] == [0, 0], plc
    assert not any(plc["modes"]), plc
    info = plain(guest("drives", "systemctl show -p MainPID --value sorter-plant.service; pgrep -af /home/kevin/plant.py"))
    processes = re.findall(r"(?m)^([0-9]+) /home/kevin/venv/bin/python /home/kevin/plant.py(.*)\r?$", info)
    assert len(processes) == 1 and not processes[0][1].strip(), info
    plant_pid = int(processes[0][0])
    return {"machine": machine, "plc": plc, "plant_pid": plant_pid,
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}


def wait_file(directory, name, seconds, runner_pid=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        output = guest("scada", f"test -f {directory}/{name} && echo PRESENT || true", 15)
        if re.search(r"(?m)^PRESENT\r?$", output):
            return
        if runner_pid:
            result = guest("scada", f"kill -0 {runner_pid} 2>/dev/null && echo RUNNING || true", 15)
            if not re.search(r"(?m)^RUNNING\r?$", result):
                raise RuntimeError(f"SCADA runner exited before {name}")
        time.sleep(.5)
    raise TimeoutError(f"waiting for {name}")


def fetch(directory, name, target):
    command = ("python3 -c " + shlex.quote(
        "import base64;print('EVIDENCE:' + base64.b64encode(open('" + directory + "/" + name +
        "','rb').read()).decode() + ':END')"))
    output = guest("scada", command, 20)
    found = re.search(r"EVIDENCE:([A-Za-z0-9+/=]*):END", output)
    if not found:
        raise RuntimeError(f"could not fetch {name}")
    target.write_bytes(base64.b64decode(found.group(1)))
    return json.loads(target.read_text()) if name.endswith(".json") else target.read_text()


def restore_plant(plant_pid):
    exact = ("^/home/kevin/venv/bin/python /home/kevin/plant.py "
             "--photoeye-fault-sensor tunnel --photoeye-fault-token 1 "
             "--photoeye-fault-kind missed$")
    output = plain(guest("drives", f"pkill -TERM -f {shlex.quote(exact)} || true; "
                   f"kill -KILL {plant_pid} 2>/dev/null || true; sleep 4; "
                   "systemctl is-active sorter-plant.service; pgrep -af /home/kevin/plant.py", 20))
    processes = re.findall(r"(?m)^([0-9]+) /home/kevin/venv/bin/python /home/kevin/plant.py(.*)\r?$", output)
    assert len(processes) == 1 and not processes[0][1].strip(), output
    return {"normal_pid": int(processes[0][0]), "service": "active"}


def browser_capture(evidence):
    proxy = driver = None
    try:
        proxy = subprocess.Popen([sys.executable, str(ROOT / "tests/serial_hmi_proxy.py")],
                                 cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = subprocess.Popen(["geckodriver", "--port", "4445"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from browser_plant import WD, HMI, request, wd
        end = time.monotonic() + 12
        while time.monotonic() < end:
            try:
                request(HMI + "/api")
                request(WD + "/status")
                break
            except Exception:
                time.sleep(.3)
        else:
            raise TimeoutError("browser proxy/driver startup")
        response = request(WD + "/session", "POST", {"capabilities": {"alwaysMatch": {
            "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})
        session = response["value"]["sessionId"]
        try:
            wd(session, "/url", "POST", {"url": HMI + "/"})
            end = time.monotonic() + 15
            while time.monotonic() < end:
                data = wd(session, "/execute/sync", "POST", {"script": """
                  return {text:document.body.innerText,
                    packages:[...document.querySelectorAll('.pkg.plant')].map(e=>({
                    id:e.dataset.packageId,raw:e.dataset.photoeyeRaw,
                    conditioned:e.dataset.photoeyeConditioned,
                    quality:e.dataset.photoeyeQuality}))};""", "args": []})
                if ("PHOTOEYE LANE 1 SENSOR 2 FAULT" in data["text"] and
                    any(p["id"].startswith("l1-") and p["raw"] == "4" and
                        p["conditioned"] == "4" and p["quality"] == "5"
                        for p in data["packages"])):
                    image = base64.b64decode(wd(session, "/screenshot"))
                    (evidence / "missed-hmi.png").write_bytes(image)
                    (evidence / "browser.json").write_text(json.dumps(data, indent=2) + "\n")
                    return {"screenshot": "missed-hmi.png", "packages": data["packages"]}
                time.sleep(.25)
            raise TimeoutError("rendered photoeye alarm/package not observed")
        finally:
            wd(session, "", "DELETE")
    finally:
        for process in (driver, proxy):
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def validate_phase(fault, restored):
    f, p = fault["modbus"], restored["modbus"]
    assert f["photoeye_fault"] == p["photoeye_fault"] == [1, 2, 1]
    assert f["validated_row"][5:] == p["validated_row"][5:] == [4, 4, 5]
    assert not f["master"] and not p["master"]
    assert f["trailers"] == p["trailers"] == [0] * 9
    assert f["slot"][:5] == p["slot"][:5] and p["inducted"] == f["inducted"] == 1
    for item in (fault, restored):
        modbus = item["modbus"]
        views = item["views"]
        assert views["opc"]["Photoeyes"] == views["hmi"]["row"] == modbus["validated_row"]
        assert [views["opc"][n] for n in ("PhotoeyeFaultMask", "PhotoeyeFaultSensor",
                                           "PhotoeyeFaultLane")] == views["hmi"]["fault"] == [1, 2, 1]
    assert fault["detection_latency_ms"] > 0


def plant_only_phase(directory, evidence, initial_pid, runner_pid, fault):
    """Restore the plant and prove the latch before permitting PLC recovery."""
    plant = restore_plant(initial_pid)
    (evidence / "plant_restoration.json").write_text(json.dumps(plant, indent=2) + "\n")
    guest("scada", f"touch {directory}/plant_restored")
    wait_file(directory, "plant_only.json", 30, runner_pid)
    restored = fetch(directory, "plant_only.json", evidence / "plant_only.json")
    validate_phase(fault, restored)
    guest("scada", f"touch {directory}/recover")
    return restored


def sums(evidence):
    rows = []
    for path in sorted(evidence.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (evidence / "SHA256SUMS").write_text("\n".join(rows) + "\n")


def emergency_recover(initial):
    """Documented master-off/modes-off/scanner reset if the guest runner dies."""
    settings = {key: initial["plc"][key] for key in
                ("enables", "setpoints", "seed", "photoeye_config", "modes")}
    code = '''import json,time
from pymodbus.client import ModbusTcpClient
s=json.loads(%r)
c=ModbusTcpClient("10.10.1.10",port=502,timeout=2);assert c.connect()
w=lambda a,v:c.write_register(a,v,slave=1)
b=lambda a,v:c.write_coil(a,v,slave=1)
r=lambda a,n=1:c.read_holding_registers(a,n,slave=1).registers
b(880,False)
for a in (914,915,918,919):b(a,False)
old=r(243)[0];b(910,True);seen=False
for i in range(300):
 tick,state=r(243)[0],r(255)[0]
 seen=seen or tick<old or state in (1,2)
 if seen and state==0 and r(748,3)==[0,0,0]:break
 time.sleep(.1)
else:raise RuntimeError("emergency documented reset timeout")
w(247,s["seed"])
for i,v in enumerate(s["setpoints"]):w(200+i,v)
for i,v in enumerate(s["photoeye_config"]):w(744+i,v)
for i,v in enumerate(s["enables"]):b(881+i,v)
for i,v in enumerate(s["modes"]):b(914+i,v)
assert not c.read_coils(880,1,slave=1).bits[0]
print("EMERGENCY_RECOVERY_DONE")
c.close()''' % json.dumps(settings)
    payload = base64.b64encode(code.encode()).decode()
    output = guest("scada", "/home/kevin/opcua/bin/python -c " + shlex.quote(
        f"import base64;exec(base64.b64decode('{payload}'))"), 45)
    assert re.search(r"(?m)^EMERGENCY_RECOVERY_DONE\r?$", output), output


def main(evidence_root):
    initial = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = evidence_root / f"missed-photoeye-{stamp}"
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "preflight.json").write_text(json.dumps(initial, indent=2) + "\n")
    directory = GUEST_DIR_PREFIX + stamp
    plant_paused = False
    plant_restored = False
    runner_pid = None
    failure = None
    cleanup_errors = []
    guest_result_seen = False
    browser = None
    try:
        guest("drives", f"kill -STOP {initial['plant_pid']}")
        plant_paused = True
        command = ("nohup /home/kevin/venv/bin/python /home/kevin/plant.py "
                   "--photoeye-fault-sensor tunnel --photoeye-fault-token 1 "
                   "--photoeye-fault-kind missed > /tmp/sorter-missed-fixture.log 2>&1 "
                   "< /dev/null & echo FIXTURE_PID:$!")
        output = guest("drives", command)
        found = re.search(r"FIXTURE_PID:([0-9]+)", output)
        assert found, output
        (evidence / "fixture.json").write_text(json.dumps({"fixture_pid": int(found.group(1))}, indent=2) + "\n")
        command = (f"nohup /home/kevin/opcua/bin/python /home/kevin/live_missed_phased.py "
                   f"{directory} > /tmp/sorter-missed-runner.log 2>&1 < /dev/null & "
                   "echo RUNNER_PID:$!")
        output = guest("scada", command)
        found = re.search(r"RUNNER_PID:([0-9]+)", output)
        assert found, output
        runner_pid = int(found.group(1))
        wait_file(directory, "fault.json", 100, runner_pid)
        fault = fetch(directory, "fault.json", evidence / "fault.json")
        for name in ("initial.json", "moving.json", "operator_before.json", "processes.json"):
            fetch(directory, name, evidence / name)
        try:
            browser = browser_capture(evidence)
        except Exception as exc:
            browser = {"error": f"{type(exc).__name__}: {exc}"}
        (evidence / "browser_result.json").write_text(json.dumps(browser, indent=2) + "\n")
        restored = plant_only_phase(directory, evidence, initial["plant_pid"], runner_pid, fault)
        plant_restored = True
        wait_file(directory, "result.json", 45, runner_pid)
        result = fetch(directory, "result.json", evidence / "result.json")
        guest_result_seen = True
        assert result == {"failure": None, "cleanup_errors": []}, result
        for name in ("recovery.json", "xle.jsonl", "asx.jsonl"):
            fetch(directory, name, evidence / name)
        normal_output = guest("scada", "/home/kevin/opcua/bin/python "
                              "/home/kevin/live_plant_lane3.py all --stateful", 240)
        (evidence / "normal-run.log").write_text(normal_output)
        lines = [json.loads(line) for line in normal_output.splitlines()
                 if line.startswith("{")]
        normal = next(item for item in lines if "rows" in item)
        assert normal["stateful"] and normal["trailer"] == [0, 1, 0, 0, 1, 0, 0, 1, 0]
        assert normal["photoeye_fault"] == [0, 0, 0]
        assert {v["barcode"] for v in normal["rows"].values()} == {6001, 5002, 3003}
        assert len(normal["journal"]) == 3
        (evidence / "normal-summary.json").write_text(json.dumps({
            "rows": normal["rows"], "trailer": normal["trailer"],
            "photoeye_fault": normal["photoeye_fault"],
            "journal_rows": normal["journal"]}, indent=2) + "\n")
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if plant_paused and not plant_restored:
            try:
                restore_plant(initial["plant_pid"])
                plant_restored = True
            except Exception as exc:
                cleanup_errors.append(f"plant restore: {exc}")
        if runner_pid:
            try:
                guest("scada", f"touch {directory}/plant_restored; touch {directory}/abort")
                try:
                    wait_file(directory, "result.json", 35)
                except Exception:
                    guest("scada", f"kill -TERM {runner_pid} 2>/dev/null || true")
                    wait_file(directory, "result.json", 35)
                for name in ("result.json", "recovery.json", "error.json", "xle.jsonl", "asx.jsonl"):
                    if not (evidence / name).exists():
                        try:
                            fetch(directory, name, evidence / name)
                        except Exception:
                            pass
                guest_result_seen = guest_result_seen or (evidence / "result.json").exists()
            except Exception as exc:
                cleanup_errors.append(f"guest runner: {exc}")
        if runner_pid and plant_restored and not guest_result_seen:
            try:
                emergency_recover(initial)
            except Exception as exc:
                cleanup_errors.append(f"emergency recovery: {exc}")
        try:
            final = {"machine": state(), "plc": plc_state(),
                     "plant": guest("drives", "pgrep -af /home/kevin/plant.py")}
            (evidence / "final.json").write_text(json.dumps(final, indent=2) + "\n")
            assert final["machine"] == initial["machine"]
            assert not final["plc"]["run"]
            assert [final["plc"]["slots"][i] for i in (0, 12, 24)] == [0, 0, 0]
            for key in ("enables", "setpoints", "seed", "photoeye_config", "modes"):
                assert final["plc"][key] == initial["plc"][key], key
            assert final["plc"]["photoeye_fault"] == [0, 0, 0]
            assert "--photoeye-fault-kind" not in final["plant"]
        except Exception as exc:
            cleanup_errors.append(f"final verification: {exc}")
        (evidence / "outcome.json").write_text(json.dumps({"failure": failure,
            "cleanup_errors": cleanup_errors, "evidence": str(evidence),
            "expected_runtime_changes": ["plant PID", "scanner nonce", "PLC ticks"]}, indent=2) + "\n")
        sums(evidence)
    if failure or cleanup_errors:
        raise RuntimeError(f"test failure={failure}; cleanup={cleanup_errors}; evidence={evidence}")
    print(json.dumps({"result": "PASS", "evidence": str(evidence),
                      "screenshot": browser.get("screenshot") if browser else None}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path,
                        default=ROOT.parent / "sorter-evidence")
    args = parser.parse_args()
    main(args.evidence_root)

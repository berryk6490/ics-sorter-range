"""Run one token-1 photoeye fault on the owned drives/SCADA guests and clean up."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = {"stuck_blocked": "induction", "missed": "tunnel"}


def guest(vm, command, timeout=120):
    result = subprocess.run(
        [sys.executable, str(ROOT / "serial_command.py"), vm, command,
         "--timeout", str(timeout)], capture_output=True, text=True,
        timeout=timeout + 10)
    if result.returncode:
        raise RuntimeError(f"{vm} command failed: {result.stdout[-1200:]} "
                           f"{result.stderr[-1200:]}")
    return result.stdout


def main(kind):
    sensor = FIXTURES[kind]
    check = ('/home/kevin/opcua/bin/python -c "from pymodbus.client import '
             'ModbusTcpClient; c=ModbusTcpClient(\\\"10.10.1.10\\\"); '
             'assert not c.read_coils(880,1,slave=1).bits[0]; '
             'assert not any(c.read_coils(918,2,slave=1).bits[:2])"')
    guest("scada", check, 20)
    info = guest("drives", "systemctl is-active sorter-plant.service; "
                 "systemctl show -p MainPID --value sorter-plant.service; "
                 "pgrep -af /home/kevin/plant.py", 20)
    info = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", info)
    processes = re.findall(
        r"(?m)^([0-9]+) /home/kevin/venv/bin/python /home/kevin/plant.py(.*)\r?$",
        info)
    if (len(processes) != 1 or processes[0][1].strip() or "active" not in info):
        raise RuntimeError("normal plant service is not the sole plant process")
    normal_pid = int(processes[0][0])
    paused = False
    try:
        guest("drives", f"kill -STOP {normal_pid}", 20)
        paused = True
        command = ("nohup /home/kevin/venv/bin/python /home/kevin/plant.py "
                   f"--photoeye-fault-sensor {sensor} --photoeye-fault-token 1 "
                   f"--photoeye-fault-kind {kind} "
                   f"> /tmp/photoeye-{kind}-fixture.log 2>&1 < /dev/null & "
                   "echo __FIXTURE_PID__$!")
        output = guest("drives", command, 20)
        found = re.search(r"__FIXTURE_PID__([0-9]+)\r?", output)
        if not found:
            raise RuntimeError("fixture process PID unavailable")
        print(f"temporary fixture PID {int(found.group(1))}", flush=True)
        output = guest("scada", "/home/kevin/opcua/bin/python "
                       f"/home/kevin/live_photoeye_fault.py {kind}", 110)
        print(output.strip(), flush=True)
    finally:
        if paused:
            exact = ("^/home/kevin/venv/bin/python /home/kevin/plant.py "
                     f"--photoeye-fault-sensor {sensor} --photoeye-fault-token 1 "
                     f"--photoeye-fault-kind {kind}$")
            info = guest("drives", f"pkill -TERM -f '{exact}' || true; "
                         f"kill -KILL {normal_pid}; sleep 4; "
                         "systemctl is-active sorter-plant.service; "
                         "pgrep -af /home/kevin/plant.py", 20)
            info = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", info)
            remaining = re.findall(
                r"(?m)^([0-9]+) /home/kevin/venv/bin/python /home/kevin/plant.py(.*)\r?$",
                info)
            if len(remaining) != 1 or remaining[0][1].strip():
                raise RuntimeError("normal plant service was not restored alone")
        guest("scada", check, 20)
        guest("scada", '/home/kevin/opcua/bin/python -c "from pymodbus.client '
              'import ModbusTcpClient; c=ModbusTcpClient(\\\"10.10.1.10\\\"); '
              'assert c.read_holding_registers(748,3,slave=1).registers == [0,0,0]"', 20)
        print(f"fixture {kind} removed; normal plant service restored", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=tuple(FIXTURES))
    main(parser.parse_args().kind)

"""One bounded, read-only guest snapshot for the Phase 2A post-ready gate."""
import argparse
import json
import re
import subprocess

SERVICES = {
    "plc": ("openplc.service",),
    "drives": ("sorter-plant.service", "scanner@tunnel1.service", "scanner@tunnel2.service",
               "scanner@tunnel3.service", *(f"vfd@{belt}{lane}.service"
                                         for belt in ("induct", "outbnd") for lane in (1, 2, 3))),
    "scada": ("opcua-server.service", "sorter-hmi.service"),
}
INTERESTING = re.compile(r"(?:^/home/kevin/venv/bin/python /home/kevin/plant\.py(?:\s|$)|"
                         r"/live_accumulation_monitor\.py|/live_accumulation_detached\.py|"
                         r"/live_accumulation_fixture\.py|/sorter-services/(?:xle|asx)\.py)")


def read(role, run=subprocess.run):
    units = SERVICES[role]
    service = run(["env", "SYSTEMD_PAGER=cat", "SYSTEMD_COLORS=0",
                   "/usr/bin/systemctl", "--no-pager", "is-active", *units],
                  capture_output=True, text=True, timeout=6)
    states = service.stdout.splitlines()
    if len(states) != len(units) or any(state not in
            ("active", "inactive", "failed", "activating", "deactivating") for state in states):
        raise RuntimeError(f"incomplete service read: {role}: {states}")
    processes = run(["ps", "-eo", "pid=,args="], capture_output=True,
                    text=True, timeout=6, check=True)
    rows = []
    for line in processes.stdout.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if match and INTERESTING.search(match.group(2)):
            rows.append({"pid": int(match.group(1)), "args": match.group(2)})
    if len(rows) > 32:
        raise RuntimeError("too many validation processes")
    plc = None
    if role == "drives":
        from pymodbus.client import ModbusTcpClient
        from read_accumulation_state import read_state
        client = ModbusTcpClient("10.10.1.10", port=502, timeout=2)
        if not client.connect():
            raise RuntimeError("PLC unavailable")
        try:
            plc = read_state(client)
        finally:
            client.close()
    return {"schema_version": 1, "role": role,
            "services": dict(zip(units, states)), "processes": rows,
            "plc": plc}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=tuple(SERVICES))
    args = parser.parse_args()
    print(json.dumps(read(args.role), sort_keys=True))


if __name__ == "__main__":
    main()

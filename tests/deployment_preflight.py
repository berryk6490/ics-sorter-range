"""Read-only hash and service preflight over already logged-in serial consoles."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "deployment_manifest.json"


def check(guest=None, live=False):
    manifest = json.loads(MANIFEST.read_text())
    checked = 0
    services = {}
    for entry in manifest["components"]:
        if guest and entry["guest"] != guest:
            continue
        source = ROOT / entry["repository_source"]
        local = hashlib.sha256(source.read_bytes()).hexdigest()
        assert local == entry["sha256"], f"repository hash changed: {source}"
        if live:
            command = entry["verification_command"]
            result = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "serial_command.py"),
                 entry["guest"], command, "--timeout", "20"],
                capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, (entry["guest"], command, result.stderr)
            match = re.search(r"(?m)^([0-9a-f]{64})  (\S+)\r?$", result.stdout)
            assert match and match.group(1) == local and match.group(2) == entry["canonical_destination"], (
                entry["guest"], command, result.stdout)
            services.setdefault(entry["guest"], set()).update(entry["associated_service"])
        checked += 1
    if live:
        for vm, names in services.items():
            for name in sorted(names):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tests" / "serial_command.py"), vm,
                     f"systemctl is-active --quiet {name}", "--timeout", "20"],
                    capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, (vm, name, result.stdout, result.stderr)
    print(f"deployment preflight: {checked} source hashes" + (" and guest hashes" if live else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--guest", choices=("plc", "drives", "scada"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    check(args.guest, args.live)

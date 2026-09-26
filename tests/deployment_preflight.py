"""Read-only hash, PLC identity, VM and service preflight over serial consoles."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "deployment_manifest.json"
SERIAL = ROOT / "tests" / "serial_command.py"
SYSTEMCTL = "env SYSTEMD_PAGER=cat SYSTEMD_COLORS=0 /usr/bin/systemctl --no-pager"


class PreflightFailure(RuntimeError):
    def __init__(self, kind, vm, label, detail=""):
        self.kind = kind
        super().__init__(f"{kind}: {vm} {label}: {detail}")


def _serial(vm, command, label, probe=False):
    nonce = secrets.token_hex(16)
    argv = [sys.executable, str(SERIAL), vm, command, "--timeout", "20",
            "--nonce", nonce, "--json"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise PreflightFailure("liveness_timeout" if probe else "command_timeout",
                               vm, label, "host serial wrapper exceeded 30 seconds") from exc
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise PreflightFailure("liveness_wrapper_error" if probe else "wrapper_error",
                               vm, label, result.stderr[-400:]) from exc
    if data.get("nonce") != nonce:
        raise PreflightFailure("stale_liveness_marker" if probe else "stale_command_marker", vm, label)
    if data.get("error"):
        kind = data["error"]
        if probe and kind in ("command_timeout", "attach_timeout", "missing_prompt"):
            kind = "liveness_missing_prompt" if kind == "missing_prompt" else "liveness_timeout"
        raise PreflightFailure(kind, vm, label, data.get("detail", ""))
    if not data.get("completion_marker"):
        raise PreflightFailure("liveness_missing_marker" if probe else "missing_completion_marker", vm, label)
    if not data.get("prompt"):
        raise PreflightFailure("liveness_missing_prompt" if probe else "missing_prompt", vm, label)
    if result.returncode != 0 or data.get("command_rc") != 0:
        raise PreflightFailure("liveness_command_failure" if probe else "command_failure",
                               vm, label, f"rc={data.get('command_rc')}")
    return data["output"], nonce


def guest_read(vm, command, label):
    """Run one intended read, then prove the same guest shell responds anew."""
    output, _ = _serial(vm, command, label)
    alive = secrets.token_hex(16)
    probe_output, probe_nonce = _serial(vm, f"printf 'PREFLIGHT_ALIVE_{alive}\\n'",
                                        f"liveness after {label}", probe=True)
    if f"PREFLIGHT_ALIVE_{alive}" not in probe_output.splitlines():
        raise PreflightFailure("stale_liveness_marker", vm, label, probe_nonce)
    print(json.dumps({"guest": vm, "check": label, "result": "ok",
                      "liveness_nonce": alive, "wrapper_nonce": probe_nonce}), flush=True)
    return output


def _vm_running(vm):
    try:
        result = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", vm],
                                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise PreflightFailure("vm_timeout", vm, "domstate") from exc
    if result.returncode or result.stdout.strip() != "running":
        raise PreflightFailure("vm_not_running", vm, "domstate", result.stdout.strip())
    print(json.dumps({"guest": vm, "check": "vm_state", "state": "running"}), flush=True)


def service_read_command(name):
    if not re.fullmatch(r"[A-Za-z0-9@._-]+", name):
        raise PreflightFailure("invalid_service_name", "manifest", name)
    return f"{SYSTEMCTL} is-active {name}"


def check(guest=None, live=False):
    started = time.monotonic()
    manifest = json.loads(MANIFEST.read_text())
    entries = [e for e in manifest["components"] if not guest or e["guest"] == guest]
    services = {}
    if live:
        for vm in sorted({e["guest"] for e in entries}):
            _vm_running(vm)
    for entry in entries:
        source = ROOT / entry["repository_source"]
        local = hashlib.sha256(source.read_bytes()).hexdigest()
        if local != entry["sha256"]:
            raise PreflightFailure("source_hash_mismatch", entry["guest"], str(source))
        if live:
            command = entry["verification_command"]
            output = guest_read(entry["guest"], command, f"hash:{entry['repository_source']}")
            match = re.search(r"(?m)^([0-9a-f]{64})  (\S+)\r?$", output)
            if not match or match.group(1) != local or match.group(2) != entry["canonical_destination"]:
                raise PreflightFailure("guest_hash_mismatch", entry["guest"], command, output[-400:])
            services.setdefault(entry["guest"], set()).update(entry["associated_service"])
    if live:
        if not guest or guest == "drives":
            output = guest_read("drives", "/home/kevin/venv/bin/python /home/kevin/read_accumulation_state.py",
                                "plc_program_identity")
            try:
                identity = json.loads(output.strip().splitlines()[-1])["identity"]
            except (ValueError, IndexError, KeyError) as exc:
                raise PreflightFailure("plc_identity_unreadable", "drives", "register249") from exc
            if identity != manifest["plc_program_identity"]:
                raise PreflightFailure("plc_identity_mismatch", "drives", "register249", str(identity))
            print(json.dumps({"guest": "drives", "check": "plc_program_identity",
                              "identity": identity}), flush=True)
        for vm, names in sorted(services.items()):
            for name in sorted(names):
                output = guest_read(vm, service_read_command(name), f"service:{name}")
                if "active" not in output.splitlines():
                    raise PreflightFailure("service_not_active", vm, name, output[-400:])
    print(f"deployment preflight: {len(entries)} source hashes" +
          (" and guest hashes; PLC identity, VMs and services verified" if live else ""))
    return {"schema_version": 1, "status": "PASS", "live": live,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "source_and_guest_hashes": len(entries),
            "program_identity": manifest["plc_program_identity"]}


def save_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--guest", choices=("plc", "drives", "scada", "xle", "fw"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--report", help="write a successful full-live preflight receipt")
    args = parser.parse_args()
    try:
        if args.report and (not args.live or args.guest):
            raise PreflightFailure("invalid_report_scope", "host", "--report",
                                   "requires an unfiltered --live check")
        report = check(args.guest, args.live)
        if args.report:
            save_report(args.report, report)
    except PreflightFailure as exc:
        print(f"deployment preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

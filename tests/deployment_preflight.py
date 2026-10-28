"""Read-only hash, PLC identity, VM and service preflight over serial consoles."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
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


def _serial(vm, command, label, probe=False, *, deadline=None, trace=None, timeout=20,
            shell_user='kevin'):
    if shell_user not in ('kevin','root') or (shell_user=='root' and vm not in ('plc','drives','xle','scada')):
        raise PreflightFailure('unsupported_console_context',vm,label)
    nonce = secrets.token_hex(16)
    argv = [sys.executable, str(SERIAL), vm, command, "--timeout", str(timeout),
            "--nonce", nonce, "--json"]
    if shell_user!='kevin':argv+=['--shell-user',shell_user]
    host_timeout = timeout + 10
    if deadline is not None:
        host_timeout = min(host_timeout, deadline - time.monotonic())
        if host_timeout <= .5:
            raise PreflightFailure('deadline_expired', vm, label)
        argv += ['--total-timeout', str(host_timeout - .5)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=host_timeout)
    except subprocess.TimeoutExpired as exc:
        if trace:
            trace({'vm': vm, 'label': label, 'probe': probe, 'argv': argv,
                   'error': 'host_serial_timeout', 'stdout': str(exc.stdout or ''),
                   'stderr': str(exc.stderr or '')})
        raise PreflightFailure("liveness_timeout" if probe else "command_timeout",
                               vm, label, f"host serial wrapper exceeded {host_timeout} seconds") from exc
    if trace:
        trace({'vm': vm, 'label': label, 'probe': probe, 'argv': argv,
               'exit_status': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
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
    if shell_user=='root' and (data.get('shell_user')!='root' or
                              type(data.get('context_uid')) is not int or data['context_uid']!=0):
        raise PreflightFailure('context_uid_unproven',vm,label)
    if result.returncode != 0 or data.get("command_rc") != 0:
        raise PreflightFailure("liveness_command_failure" if probe else "command_failure",
                               vm, label, f"rc={data.get('command_rc')}")
    return data["output"], nonce


def guest_read(vm, command, label, *, deadline=None, trace=None, timeout=20,shell_user='kevin'):
    """Run one intended read, then prove the same guest shell responds anew."""
    options = {} if deadline is None and trace is None else {'deadline': deadline, 'trace': trace}
    if shell_user!='kevin':options={**options,'shell_user':shell_user}
    command_options = options if timeout == 20 else {**options, 'timeout': timeout}
    output, _ = _serial(vm, command, label, **command_options)
    alive = secrets.token_hex(16)
    probe_output, probe_nonce = _serial(vm, f"printf 'PREFLIGHT_ALIVE_{alive}\\n'",
                                        f"liveness after {label}", probe=True, **options)
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


def batch_read_command(paths, units):
    """Same fixed hashes/service states, one authenticated read per guest."""
    code="""import hashlib,json,pathlib,subprocess
paths=PATHS
units=UNITS
states={}
if units:
 r=subprocess.run(['env','SYSTEMD_PAGER=cat','SYSTEMD_COLORS=0','/usr/bin/systemctl',
                   '--no-pager','show',*units,'-p','Id','-p','ActiveState'],
                  capture_output=True,text=True,timeout=3,check=True)
 if r.stderr: raise RuntimeError('service query stderr: '+r.stderr)
 for block in r.stdout.strip().split('\\n\\n'):
  fields={}
  for line in block.splitlines():
   key,sep,value=line.partition('=')
   if not sep or key in fields: raise ValueError('ambiguous service record')
   fields[key]=value
  if set(fields)!={'Id','ActiveState'} or fields['Id'] in states: raise ValueError('invalid service record')
  states[fields['Id']]=fields['ActiveState']
 if set(states)!=set(units): raise ValueError('service identity set differs')
print('M2_PREFLIGHT_V1='+json.dumps({'hashes':{p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in paths},'services':states}))
""".replace('PATHS',repr(paths)).replace('UNITS',repr(units))
    return shlex.join(['/usr/bin/python3','-B','-c',code])


def batch_payload(output):
    def unique(pairs):
        row={}
        for key,value in pairs:
            if key in row:raise ValueError('duplicate JSON field')
            row[key]=value
        return row
    rows=[line[len('M2_PREFLIGHT_V1='):] for line in output.splitlines()
          if line.startswith('M2_PREFLIGHT_V1=')]
    if len(rows)!=1:raise ValueError('missing/ambiguous preflight payload')
    result=json.loads(rows[0],object_pairs_hook=unique)
    if not isinstance(result,dict) or set(result)!={'hashes','services'}:
        raise ValueError('malformed preflight payload')
    return result


def check(guest=None, live=False, batch=False):
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
        if live and not batch:
            command = entry["verification_command"]
            output = guest_read(entry["guest"], command, f"hash:{entry['repository_source']}")
            match = re.search(r"(?m)^([0-9a-f]{64})  (\S+)\r?$", output)
            if not match or match.group(1) != local or match.group(2) != entry["canonical_destination"]:
                raise PreflightFailure("guest_hash_mismatch", entry["guest"], command, output[-400:])
        if live:services.setdefault(entry["guest"], set()).update(entry["associated_service"])
    if live:
        if batch:
            for vm in sorted({e['guest'] for e in entries}):
                selected=[e for e in entries if e['guest']==vm]
                expected={e['canonical_destination']:e['sha256'] for e in selected}
                if len(expected)!=len(selected):raise PreflightFailure('duplicate_destination',vm,'manifest')
                command=batch_read_command(sorted(expected),sorted(services.get(vm,set())))
                output=guest_read(vm,command,'batch_hashes_services_'+vm)
                try:
                    data=batch_payload(output)
                    if data['hashes']!=expected:raise ValueError('hash/path set differs')
                    if data['services']!={name:'active' for name in services.get(vm,set())}:
                        raise ValueError('service identity/state set differs')
                except (ValueError,TypeError,KeyError) as exc:
                    raise PreflightFailure('batched_guest_mismatch',vm,'hashes/services',str(exc)) from exc
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
        for vm, names in ([] if batch else sorted(services.items())):
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
            "transport_mode": "batched-per-guest" if batch else "per-entry",
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
    parser.add_argument("--batch", action="store_true",help="batch compatible read-only hashes/services per guest")
    parser.add_argument("--report", help="write a successful full-live preflight receipt")
    args = parser.parse_args()
    try:
        if args.report and (not args.live or args.guest):
            raise PreflightFailure("invalid_report_scope", "host", "--report",
                                   "requires an unfiltered --live check")
        report = check(args.guest, args.live, args.batch)
        if args.report:
            save_report(args.report, report)
    except PreflightFailure as exc:
        print(f"deployment preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

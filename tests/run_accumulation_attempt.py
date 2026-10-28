"""One bounded host command from post-approval preflight through restoration."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from accumulation_monitor_control import Controller as MonitorController, GuestCommandError, atomic_control
from accumulation_scenario_control import ScenarioController, APPROVAL_LIFETIME, serialized_operation
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
DEDICATED_CASES={'healthy_idle':None,'normal_sorting':'normal','recirculation':'recirculation',
                 'backpressure':'backpressure','drive_stop_resume':'drive_stop','documented_recovery':'recovery'}
DEDICATED_CHECKS=('accepted_startup_receipts','exact_case_controls_and_reset','capture_coverage_and_supervision',
                  'readable_journal_and_logs','console_coordination','storage_and_clock_bounds')


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


def dedicated_argv(case,source):
    """Reviewed host runner inline; installed measurement files stay unchanged."""
    if case not in ('normal','recirculation','backpressure','drive_stop'):raise ValueError(case)
    return ['/usr/bin/timeout','--signal=TERM','--kill-after=3s','300s',
            '/home/kevin/venv/bin/python','-B','-c',source,'--case',case]


def verify_dedicated_journal(result,journal):
    rows=result['rows'];epoch=result['epoch'];nonce=result['scanner_nonce']
    if journal['integrity']!='ok' or journal['epoch']!=epoch or journal['nonce']!=nonce or journal['outcome_count']!=len(rows):
        raise ValueError('dedicated journal identity/count/integrity differs')
    seen=set()
    for entry in journal['outcomes']:
        payload=entry['payload'];package=payload['package_id']
        if package not in rows or package in seen:raise ValueError('missing/duplicate/unexpected package journal identity')
        row=rows[package];seen.add(package)
        expected=f"{epoch}:{nonce}:{row['lane']}:{row['token']}:{row['serial']}"
        if (entry['identity']!=expected or payload['actual_trailer']!=row['actual'] or
            payload['state']!=('loaded' if row['actual'] else 'recirculated')):
            raise ValueError('journal/PLC terminal outcome differs')
    if seen!=set(rows):raise ValueError('incomplete dedicated journal outcomes')


def observe_healthy_idle(io,deadline,initial,journal_before):
    """Measure between successful observations; first-read latency is not idle."""
    import m2_plc_startup_guard as g
    samples=[]
    while True:
        if time.monotonic()>=deadline:raise TimeoutError('healthy-idle observation deadline')
        current=io.json('drives',g.STATE,'healthy-idle-'+str(len(samples)),deadline)
        observed=time.monotonic()
        if observed>=deadline:raise TimeoutError('healthy-idle observation deadline')
        g.safe_state(current)
        if current['run_identity']!=initial['run_identity']:
            raise ValueError('healthy-idle run identity changed')
        if samples and current['startup_extra']['590']==samples[-1]['heartbeat']:
            raise ValueError('healthy-idle plant heartbeat did not advance')
        samples.append({'observed_monotonic':observed,'identity':current['run_identity'],
                        'heartbeat':current['startup_extra']['590']})
        span=observed-samples[0]['observed_monotonic']
        if len(samples)>=2 and span>=10:break
        time.sleep(min(.5,max(0,deadline-time.monotonic())))
    idle_journal=io.json('xle',g.JOURNAL,'healthy-idle-journal-after',deadline)
    if idle_journal!=journal_before:raise ValueError('healthy-idle journal inventory changed')
    return {'status':'PASS','case':'healthy_idle','observation_seconds':span,
            'samples':samples,'journal_unchanged':True}


def selected_log_sinks(units,*,source_root=None,manifest_path=None):
    """Bind log paths to the selected deployment's exact service templates."""
    import m2_plc_startup_guard as g
    root=Path(source_root or g.SELECTED);manifest=Path(manifest_path or root/'deploy/deployment_manifest.json')
    data=json.loads(manifest.read_text());sinks={}
    for unit in units:
        if not any(unit in item['associated_service'] for item in data['components']):
            raise ValueError('service is not declared in selected manifest: '+unit)
        template=unit.split('@',1)[0]+'@.service' if '@' in unit else unit
        candidates=[item for item in data['components'] if
                    Path(item['canonical_destination']).name==template and
                    Path(item['repository_source']).name==template]
        paths={item['repository_source'] for item in candidates}
        if len(paths)!=1:raise ValueError('unambiguous selected service template required: '+unit)
        item=candidates[0];raw=(root/item['repository_source']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('selected log template hash differs')
        values=re.findall(r'(?m)^Standard(?:Output|Error)=(\S+)\s*$',raw.decode())
        mode='journal' if not values else values[0]
        if values and (len(values)!=2 or values[0]!=values[1]):raise ValueError('ambiguous selected log sinks')
        if mode=='journal':sink={'kind':'journal'}
        elif mode.startswith('append:/'):
            path=mode.removeprefix('append:')
            if '%i' in path:
                if '@' not in unit or not unit.endswith('.service'):raise ValueError('unit instance missing')
                path=path.replace('%i',unit.split('@',1)[1].removesuffix('.service'))
            if '%' in path or '..' in Path(path).parts:raise ValueError('unsupported log path')
            sink={'kind':'file','path':path}
        else:raise ValueError('unsupported selected log sink')
        sinks[unit]={**sink,'unit_source':item['repository_source'],'unit_sha256':item['sha256']}
    return sinks


def scenario_support_command(units,since,*,sinks=None,cursors=None,initial_baseline=False):
    """Explicit initial positioning or witnessed interval; history/events stay separate."""
    import m2_plc_startup_guard as g
    if type(initial_baseline) is not bool or (initial_baseline and (cursors is not None or since is not None)):
        raise ValueError('initial baseline requires a fresh explicit observation without prior cursors/interval')
    sinks=selected_log_sinks(units) if sinks is None else sinks
    code="""import base64,datetime,hashlib,json,os,pathlib,stat,subprocess
units=UNITS
since=SINCE
log_contract=LOG_CONTRACT
cursors=CURSORS
prior_required=PRIOR_REQUIRED
initial_baseline=INITIAL_BASELINE
observed=datetime.datetime.now(datetime.timezone.utc)
if since is None:since=(observed-datetime.timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S UTC')
else:since=datetime.datetime.fromisoformat(since).strftime('%Y-%m-%d %H:%M:%S UTC')
def read(argv):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=3,check=True)
 if r.stderr:raise RuntimeError('evidence command stderr: '+r.stderr)
 if len(r.stdout.encode())>1048576:raise ValueError('evidence output byte bound')
 return r.stdout
show=['env','SYSTEMD_PAGER=cat','SYSTEMD_COLORS=0','systemctl','--no-pager','show',*units]
show += [part for name in FIELDS for part in ['-p',name]]
first=read(show)
sockets=read(['ss','-H','-antp'])
journal_units=[unit for unit in units if log_contract[unit]['kind']=='journal']
journal=read(['journalctl','--quiet','--no-pager','--since',since,'-n','200','-o','json',
 *[part for unit in journal_units for part in ['-u',unit]]]) if journal_units else ''
service={}
for block in first.strip().split('\\n\\n'):
 fields=dict(line.split('=',1) for line in block.splitlines())
 service[fields['Id']]=fields
def process_start(pid):
 raw=pathlib.Path('/proc/'+pid+'/stat').read_text()
 return int(raw[raw.rfind(')')+2:].split()[19])
logs={}
for unit in units:
 try:
  pid=service[unit]['MainPID']
  if not pid.isascii() or not pid.isdigit() or int(pid)<=0:raise ValueError('invalid service PID')
  start=process_start(pid);sink=log_contract[unit]
  if sink['kind']=='journal':
   targets=[os.readlink('/proc/'+pid+'/fd/'+fd) for fd in ('1','2')]
   if not all(t.startswith('socket:[') and t.endswith(']') for t in targets):raise ValueError('actual stdout/stderr not journal sockets')
   logs[unit]=dict(kind='journal',available=True,stdout_targets=targets,process_start_ticks=start)
  else:
   fd=os.open(sink['path'],os.O_RDONLY|os.O_NOFOLLOW)
   try:
    a=os.fstat(fd)
    if not stat.S_ISREG(a.st_mode):raise ValueError('log is not regular')
    for number in ('1','2'):
     actual=os.stat('/proc/'+pid+'/fd/'+number)
     if (actual.st_dev,actual.st_ino)!=(a.st_dev,a.st_ino):raise ValueError('selected file is not actual service stdout/stderr')
    old=cursors.get(unit)
    if prior_required and unit not in cursors:raise ValueError('prior log cursor missing; no automatic rebaseline')
    if unit in cursors and not isinstance(old,dict):raise ValueError('malformed prior log cursor')
    if old is not None:
     if (set(old)!={'dev','inode','offset','anchor_offset','anchor_bytes','anchor_sha256','interval_status'} or
      any(type(old[k]) is not int or old[k]<0 for k in ('dev','inode','offset','anchor_offset','anchor_bytes')) or
      old['anchor_offset']!=max(0,old['offset']-256) or old['anchor_bytes']!=min(256,old['offset']) or
      not isinstance(old['anchor_sha256'],str) or len(old['anchor_sha256'])!=64):raise ValueError('bounded prior log anchor missing/malformed')
     if old['interval_status'] not in ('BASELINE_VALID','BOUNDED_OBSERVATION'):raise ValueError('prior bounded observation incomplete; no automatic rebaseline')
     if (old['dev'],old['inode'])!=(a.st_dev,a.st_ino):raise ValueError('log rotated since pre-observation')
    offset=old['offset'] if old is not None else max(0,a.st_size-8192)
    if offset<0 or offset>a.st_size:raise ValueError('log truncated since pre-observation')
    if old is not None:
     prior=os.pread(fd,old['anchor_bytes'],old['anchor_offset'])
     if len(prior)!=old['anchor_bytes'] or hashlib.sha256(prior).hexdigest()!=old['anchor_sha256']:
      raise ValueError('prior log content continuity lost')
    length=min(8192,a.st_size-offset);data=os.pread(fd,length,offset);z=os.fstat(fd)
    anchor_offset=max(0,a.st_size-256);anchor=os.pread(fd,a.st_size-anchor_offset,anchor_offset)
    if len(anchor)!=a.st_size-anchor_offset:raise ValueError('short log anchor read')
    z=os.fstat(fd)
    stable=(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns)
    if len(data)!=length:raise ValueError('short log read')
    truncated=offset>0 and not old or a.st_size-offset>length
    status='UNSTABLE' if not stable else 'NO_EVENTS' if not data else 'TRUNCATED' if truncated else 'BOUNDED_EVENT_BYTES'
    position='BASELINE_VALID' if initial_baseline and stable else 'BOUNDED_OBSERVATION' if (
     status=='BOUNDED_EVENT_BYTES' or old is not None and stable and not truncated) else 'INCOMPLETE'
    logs[unit]=dict(kind='file',path=sink['path'],available=True,status=status,offset=offset,
     size_before=a.st_size,size_after=z.st_size,dev=a.st_dev,inode=a.st_ino,process_start_ticks=start,
     bytes_base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),
     cursor=dict(dev=a.st_dev,inode=a.st_ino,offset=a.st_size,anchor_offset=anchor_offset,
      anchor_bytes=len(anchor),anchor_sha256=hashlib.sha256(anchor).hexdigest(),
      interval_status=position),
     previous_witness_base64=base64.b64encode(prior).decode() if old is not None else None,
     next_witness_base64=base64.b64encode(anchor).decode(),
     continuity=dict(status='BOUNDED_ANCHOR_MATCH' if old is not None else 'INITIAL_SNAPSHOT',previous_cursor=old,
      previous_anchor_sha256=hashlib.sha256(prior).hexdigest() if old is not None else None),
     history_completeness='UNPROVEN; bounded bytes/anchor do not prove untouched whole-file history',
     truncated=bool(truncated),stable=stable)
   finally:os.close(fd)
  if process_start(pid)!=start:raise ValueError('service process incarnation changed')
 except Exception as exc:logs[unit]=dict(kind=log_contract[unit]['kind'],available=False,status='UNAVAILABLE',error=type(exc).__name__+': '+str(exc))
second=read(show)
print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 units=units,first=first,second=second,sockets=sockets,journal=journal,observed_utc=observed.isoformat(),
 initial_baseline=initial_baseline,
 log_contract=log_contract,application_logs=logs,
 since_utc=since,journal_limit=200,journal_truncation='POSSIBLE; bounded tail')))
""".replace('UNITS',repr(units)).replace('SINCE',repr(since)).replace('FIELDS',repr(g.XLE_SERVICE_FIELDS)).replace('LOG_CONTRACT',repr(sinks)).replace('CURSORS',repr(cursors or {})).replace('PRIOR_REQUIRED',repr(cursors is not None)).replace('INITIAL_BASELINE',repr(initial_baseline))
    return g.py(code)


def scenario_support_check(payload,units,*,sinks=None,cursors=None,initial_baseline=False):
    import m2_plc_startup_guard as g
    if (type(initial_baseline) is not bool or type(payload.get('initial_baseline',False)) is not bool or
        payload.get('initial_baseline',False)!=initial_baseline or initial_baseline and cursors is not None):
        raise ValueError('initial baseline phase/request mismatch; no automatic rebaseline')
    g.normalize_boot_id(payload['boot_id'])
    if datetime.fromisoformat(payload['observed_utc']).tzinfo is None:
        raise ValueError('guest observation timezone missing')
    if payload['units']!=units or payload['first']!=payload['second']:
        raise ValueError('service identity changed across socket/log evidence')
    states={}
    sinks=selected_log_sinks(units) if sinks is None else sinks
    if payload.get('log_contract')!=sinks or set(payload.get('application_logs',{}))!=set(units):
        raise ValueError('selected application log contract differs')
    for block in payload['first'].strip().split('\n\n'):
        state=g.properties(block);unit=state['Id']
        if (unit in states or state['ActiveState']!='active' or state['SubState']!='running' or
            not state['MainPID'].isdigit() or int(state['MainPID'])<=0 or
            state['ExecMainPID']!=state['MainPID'] or
            not state['ExecMainStartTimestampMonotonic'].isdigit() or int(state['ExecMainStartTimestampMonotonic'])<=0 or
            not state['NRestarts'].isdigit()):
            raise ValueError('invalid service incarnation/state')
        g.normalize_boot_id(state['InvocationID'])
        if not re.search(r'\bpid='+state['MainPID']+r'\b',payload['sockets']):
            raise ValueError('required service socket PID ownership unavailable: '+unit)
        if unit=='sorter-xle.service':g.xle_exec_start(state['ExecStart'])
        states[unit]=state
    if set(states)!=set(units):raise ValueError('service evidence unit set differs')
    records=[]
    raw=payload['journal'].strip()
    if raw and raw!='-- No entries --':
        for line in raw.splitlines():
            entry=json.loads(line)
            if not isinstance(entry,dict) or not isinstance(entry.get('MESSAGE'),str):
                raise ValueError('malformed journal record')
            if entry.get('_BOOT_ID') is not None:
                if g.normalize_boot_id(entry['_BOOT_ID'])!=g.normalize_boot_id(payload['boot_id']):
                    raise ValueError('stale-boot journal record')
            records.append(entry)
    evidence={};next_cursors={}
    def checked_cursor(cursor):
        if (not isinstance(cursor,dict) or
            set(cursor)!={'dev','inode','offset','anchor_offset','anchor_bytes','anchor_sha256','interval_status'} or
            any(type(cursor[k]) is not int or cursor[k]<0 for k in ('dev','inode','offset','anchor_offset','anchor_bytes')) or
            cursor['anchor_offset']!=max(0,cursor['offset']-256) or cursor['anchor_bytes']!=min(256,cursor['offset']) or
            not isinstance(cursor['anchor_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',cursor['anchor_sha256']) or
            cursor['interval_status'] not in ('BASELINE_VALID','BOUNDED_OBSERVATION','INCOMPLETE')):
            raise ValueError('invalid bounded file cursor')
    for unit,sink in sinks.items():
        log=payload['application_logs'][unit]
        if log.get('available') is not True or log.get('kind')!=sink['kind']:
            raise ValueError('selected application log unavailable: '+unit)
        if type(log.get('process_start_ticks')) is not int or log['process_start_ticks']<=0:
            raise ValueError('application log incarnation missing')
        if sink['kind']=='file':
            data=base64.b64decode(log['bytes_base64'],validate=True)
            cursor=log['cursor'];old=(cursors or {}).get(unit)
            checked_cursor(cursor)
            if cursors is not None and unit not in cursors:raise ValueError('prior bounded cursor missing')
            if unit in (cursors or {}):
                checked_cursor(old)
                if old['interval_status'] not in ('BASELINE_VALID','BOUNDED_OBSERVATION'):raise ValueError('prior bounded observation incomplete')
            if (any(type(log[k]) is not int or log[k]<0 for k in ('dev','inode','offset','size_before','size_after','bytes')) or
                (cursor['dev'],cursor['inode'],cursor['offset'])!=(log['dev'],log['inode'],log['size_before']) or
                (old is not None and (old['dev'],old['inode'])!=(log['dev'],log['inode'])) or
                log.get('continuity')!={'status':'BOUNDED_ANCHOR_MATCH' if old is not None else 'INITIAL_SNAPSHOT',
                    'previous_cursor':old,'previous_anchor_sha256':old['anchor_sha256'] if old is not None else None}):
                raise ValueError('file log cursor/continuity binding differs')
            offset=old['offset'] if old is not None else max(0,log['size_before']-8192)
            if offset>log['size_before']:raise ValueError('file size below prior cursor')
            length=min(8192,log['size_before']-offset)
            witness=base64.b64decode(log['next_witness_base64'],validate=True)
            if (len(witness)!=cursor['anchor_bytes'] or hashlib.sha256(witness).hexdigest()!=cursor['anchor_sha256'] or
                log['offset']!=offset or len(data)!=length):raise ValueError('file returned/witness range differs')
            begin=max(offset,cursor['anchor_offset']);end=min(offset+len(data),cursor['offset'])
            if (end>begin and data[begin-offset:end-offset]!=witness[begin-cursor['anchor_offset']:end-cursor['anchor_offset']]):
                raise ValueError('file data/next witness overlap differs')
            if old is not None:
                prior=base64.b64decode(log['previous_witness_base64'],validate=True)
                if len(prior)!=old['anchor_bytes'] or hashlib.sha256(prior).hexdigest()!=old['anchor_sha256']:
                    raise ValueError('prior witness differs')
            elif log['previous_witness_base64'] is not None:raise ValueError('unexpected prior witness')
            if (log.get('path')!=sink['path'] or len(data)!=log['bytes'] or len(data)>8192 or
                hashlib.sha256(data).hexdigest()!=log['sha256'] or
                log['offset']+len(data)>log['size_before'] or log['status'] not in
                ('NO_EVENTS','TRUNCATED','UNSTABLE','BOUNDED_EVENT_BYTES')):
                raise ValueError('invalid file log evidence')
            truncated=(offset>0 and old is None) or log['size_before']-offset>length
            if (type(log['stable']) is not bool or type(log['truncated']) is not bool or log['truncated']!=truncated or
                (log['stable'] and log['size_after']!=log['size_before'])):raise ValueError('invalid file stability evidence')
            expected_status='UNSTABLE' if not log['stable'] else 'NO_EVENTS' if not data else 'TRUNCATED' if truncated else 'BOUNDED_EVENT_BYTES'
            position='BASELINE_VALID' if initial_baseline and log['stable'] else 'BOUNDED_OBSERVATION' if (
                expected_status=='BOUNDED_EVENT_BYTES' or old is not None and log['stable'] and not truncated) else 'INCOMPLETE'
            if log['status']!=expected_status or cursor['interval_status']!=position:
                raise ValueError('false bounded log evidence')
            evidence[unit]={'sink':sink,'status':log['status'],'bytes':len(data),
                            'continuity':log['continuity']['status'],'history_completeness':'UNPROVEN',
                            'initial_baseline_validity':'VALID' if position=='BASELINE_VALID' else 'NOT ESTABLISHED'}
            next_cursors[unit]=cursor
        else:
            candidates=[entry for entry in records if entry.get('_SYSTEMD_UNIT')==unit and
                 entry.get('_PID')==states[unit]['MainPID'] and entry.get('MESSAGE') and
                 entry.get('_BOOT_ID') is not None and
                 g.normalize_boot_id(entry['_BOOT_ID'])==g.normalize_boot_id(payload['boot_id'])]
            own=[entry for entry in candidates if entry.get('_SYSTEMD_INVOCATION_ID') is not None and
                 g.normalize_boot_id(entry['_SYSTEMD_INVOCATION_ID'])==g.normalize_boot_id(states[unit]['InvocationID'])]
            evidence[unit]={'sink':sink,'status':('INCOMPLETE_INCARNATION' if len(own)!=len(candidates)
                            else 'BOUNDED_JOURNAL_EVENTS' if own else 'NO_EVENTS'),
                            'matched_application_records':len(own),'unbound_incarnation_records':len(candidates)-len(own),
                            'truncation':'POSSIBLE'}
    return {'services':states,'journal_records':len(records),'guest_observed_utc':payload['observed_utc'],
            'initial_baseline':initial_baseline,
            'interval_start_readiness':'READY' if initial_baseline and all(
                v.get('initial_baseline_validity')=='VALID' or v['status']=='BOUNDED_JOURNAL_EVENTS'
                for v in evidence.values()) else 'INCOMPLETE',
            'log_cursors':next_cursors,'application_evidence':evidence,
            'application_attribution':'SUPPORTED_WITH_LIMITS' if not (initial_baseline and next_cursors) and all(v['status'] in ('BOUNDED_EVENT_BYTES','BOUNDED_JOURNAL_EVENTS') for v in evidence.values()) else 'INCOMPLETE',
            'attribution':'Service/incarnation and ss PID/socket window; correlate raw journal _PID/unit. '
                          'Missing tuples, absent events and nonmatching PIDs remain AMBIGUOUS, never infer from IP.'}


def collect_scenario_support(io,deadline,since,label,*,initial_baseline=False):
    import m2_plc_startup_guard as g
    if type(initial_baseline) is not bool or initial_baseline==isinstance(since,dict):
        raise ValueError('explicit initial baseline or complete prior assessment required; no automatic rebaseline')
    manifest=json.loads((g.SELECTED/'deploy/deployment_manifest.json').read_text())
    views={}
    for vm in ('drives','scada','xle'):
        units=sorted({unit for item in manifest['components'] if item['guest']==vm
                      for unit in item['associated_service'] if unit!='nftables.service'})
        guest_since=since[vm]['guest_observed_utc'] if isinstance(since,dict) else None
        sinks=selected_log_sinks(units)
        cursors=since[vm]['log_cursors'] if isinstance(since,dict) else None
        payload=io.json(vm,scenario_support_command(units,guest_since,sinks=sinks,cursors=cursors,
                       initial_baseline=initial_baseline),label+'-'+vm,deadline)
        views[vm]=scenario_support_check(payload,units,sinks=sinks,cursors=cursors,initial_baseline=initial_baseline)
    io.save(label+'-assessment.json',views)
    return views


def run_dedicated(case,prerequisites,evidence,approval_id,approved_at):
    """Canonical one-use approval/serial/typed recovery, no detached worker or capture.

    A transport failure never authorizes a reset while worker completion is
    unknown. The independent guest timeout bounds that worker; restoration stays
    INCOMPLETE and the canonical restoration gate blocks the next attempt.
    """
    import accumulation_scenario_control as control
    import accumulation_state_snapshot as typed
    import deployment_preflight as pre
    import m2_plc_startup_guard as g
    import shlex
    if case not in DEDICATED_CASES:raise ValueError('unsupported dedicated scenario')
    record=json.loads(Path(prerequisites).read_text());source_path=ROOT/'live_xle_vm_three_lane.py'
    source=source_path.read_text();sha=lambda data:hashlib.sha256(data).hexdigest()
    approval_age(approved_at)
    if (record.get('status')!='PASS' or record.get('scenario')!=case or
        record.get('approval_id')!=approval_id or record.get('approved_utc')!=approved_at or
        record.get('selected_source')!=g.COMMIT or record.get('total_seconds')!=540 or
        record.get('host_runner_sha256')!=sha(Path(__file__).read_bytes()) or
        record.get('scenario_source_sha256')!=sha(source.encode()) or
        record.get('guard_sha256')!=sha(Path(g.__file__).read_bytes()) or
        record.get('preflight_sha256')!=sha(Path(pre.__file__).read_bytes()) or
        record.get('scenario_control_sha256')!=sha(Path(control.__file__).read_bytes()) or
        record.get('allowed_remote_recovery') is not True):
        raise ValueError('exact separately reviewed dedicated scenario/recovery scope required')
    if (set(record.get('checks',{}))!=set(DEDICATED_CHECKS) or
        any(record['checks'][key]!='PASS' for key in DEDICATED_CHECKS)):
        raise ValueError('every detailed dedicated prerequisite must independently pass')
    references=record.get('check_evidence',{})
    if set(references)!=set(DEDICATED_CHECKS):raise ValueError('detailed prerequisite evidence references missing')
    for key in DEDICATED_CHECKS:
        if not isinstance(references[key],list) or not references[key]:raise ValueError('empty prerequisite evidence: '+key)
        for item in references[key]:
            if (not isinstance(item,dict) or set(item)!={'path','sha256'} or
                sha(Path(item['path']).read_bytes())!=item['sha256']):
                raise ValueError('prerequisite evidence hash differs: '+key)
    startup_bytes=Path(record['startup_results']).read_bytes()
    if sha(startup_bytes)!=record.get('startup_results_sha256'):
        raise ValueError('reviewed accepted startup result hash differs')
    startup=json.loads(startup_bytes)
    if (startup.get('status')!='PASS' or startup.get('terminal') is not True or
        startup.get('selected_source_commit')!=g.COMMIT or
        any(startup.get(stage,{}).get('status')!='PASS' for stage in ('startup','listener','handshake','verification')) or
        startup.get('final_state',{}).get('status')!='PASS' or
        startup.get('final_state',{}).get('core',{}).get('uid')!=0 or
        startup.get('final_state',{}).get('core',{}).get('euid')!=0 or
        startup.get('final_state',{}).get('endpoint')!='CORE_RUNNING_MASTER_OFF' or
        startup.get('abort',{}).get('watchdog',{}).get('status')!='ACCEPTED' or
        startup.get('abort',{}).get('watchdog',{}).get('terminal') is not True or
        startup.get('abort',{}).get('watchdog',{}).get('ownership_validated') is not True):
        raise ValueError('successful complete startup acceptance required; readiness/aborted attempt is insufficient')
    return _run_dedicated_operation(case,record,startup,source,evidence,approval_id)


@serialized_operation()
def _run_dedicated_operation(case,record,startup,source,evidence,approval_id):
    import accumulation_scenario_control as control
    import accumulation_state_snapshot as typed
    import deployment_preflight as pre
    import m2_plc_startup_guard as g
    import shlex
    sha=lambda data:hashlib.sha256(data).hexdigest()
    if control.RESTORATION_GATE.exists() and json.loads(control.RESTORATION_GATE.read_text()).get('status')!='PASS':
        raise ValueError('prior canonical restoration gate unresolved')
    local=Path(evidence).resolve()
    if local.exists() or local.is_relative_to(g.ROOT) or local.is_relative_to(g.SELECTED):
        raise ValueError('new outside-source scenario evidence required')
    if (subprocess.check_output(['git','-C',str(g.SELECTED),'rev-parse','HEAD'],text=True).strip()!=g.COMMIT or
        subprocess.check_output(['git','-C',str(g.SELECTED),'status','--porcelain'],text=True).strip() or
        sha((g.SELECTED/'deploy/deployment_manifest.json').read_bytes())!=g.MANIFEST_SHA):
        raise ValueError('clean selected measurement source/manifest required')
    control.claim_host_approval(approval_id,approval_id,case)
    local.mkdir();io=g.LiveIO(local,shell_users=g.OPERATOR_ROOT_SHELLS);end=time.monotonic()+540
    outcome={key:'NOT RUN' for key in ('functional_outcome','capture_completeness','application_attribution','service_restoration',
              'plc_recovery','typed_postflight','orphan_checks','plan_restoration')}
    outcome.update(scenario=case,scenario_id=approval_id,status='INCOMPLETE',errors={},selected_source=g.COMMIT,
                   started_utc=datetime.now(timezone.utc).isoformat(),deadline_monotonic=end,total_seconds=540)
    control.write_run_gate(local,approval_id,case,'PENDING')
    io.save('reviewed-prerequisites.json',record);io.save('exact-runner-source.json',{'sha256':sha(source.encode()),'source':source})
    saved_pre=pre.guest_read;saved_typed=typed.guest_read;completed=False;operation_started=False
    saved_root,saved_manifest=pre.ROOT,pre.MANIFEST
    before=None
    def deadline_expired(signum,frame):raise TimeoutError('dedicated scenario 540-second total deadline')
    previous_alarm=signal.signal(signal.SIGALRM,deadline_expired)
    signal.setitimer(signal.ITIMER_REAL,max(.001,end-time.monotonic()))
    def read(vm,command,label):return io.read(vm,command,label.replace('/','_').replace(':','-'),end)
    try:
        pre.guest_read=read;typed.guest_read=read
        pre.ROOT=g.SELECTED;pre.MANIFEST=g.SELECTED/'deploy/deployment_manifest.json'
        io.save('preflight.json',pre.check(live=True,batch=True))
        before=typed.capture();typed.save(local/'typed-before.json',before)
        if typed.compare(before,before)['status']!='PASS':raise ValueError('typed stopped baseline invalid')
        installed=io.json('plc',g.PLC_INSPECT,'current-owned-core',end)
        g.installed_guard(installed,stopped=False,core=startup['final_state']['core'])
        initial=io.json('drives',g.STATE,'initial-stopped-state',end);g.safe_state(initial)
        journal_before=io.json('xle',g.JOURNAL,'journal-before',end)
        expected_identity=dict(initial['run_identity']);expected_journal=journal_before
        outcome['application_attribution']='INCOMPLETE'
        support_before=collect_scenario_support(io,end,outcome['started_utc'],'support-before',initial_baseline=True)
        mode=DEDICATED_CASES[case]
        operation_started=True
        if mode=='recovery':
            result=io.json('scada',g.HANDSHAKE,'documented-recovery',end)
        elif mode:
            text=io.read('scada',shlex.join(dedicated_argv(mode,source)),'dedicated-package',end)
            result=json.loads(text.splitlines()[-1]);completed=True
            if result.get('status')!='PASS' or result.get('case')!=mode or result.get('operator_restoration')!='PASS':
                raise ValueError('functional/operator restoration proof incomplete')
            command=shlex.join(['/opt/sorter-xle/venv/bin/python','/opt/sorter-xle/read_chute_journal.py',
                               '--epoch',str(result['epoch']),'--nonce',str(result['scanner_nonce'])])
            journal=io.json('xle',command,'scenario-journal-outcomes',end)
            verify_dedicated_journal(result,journal)
        else:
            result=observe_healthy_idle(io,end,initial,journal_before)
        completed=True;io.save('functional-result.json',result);outcome['functional_outcome']='PASS'
        if mode:
            if mode!='recovery':result=io.json('scada',g.HANDSHAKE,'post-scenario-recovery',end)
            if result.get('status')!='PASS' or result.get('journal_authority')!='dedicated_sorter_xle_service':
                raise ValueError('canonical remote recovery incomplete')
            first=io.json('drives',g.STATE,'recovered-first',end);time.sleep(.5)
            second=io.json('drives',g.STATE,'recovered-second',end)
            journal_after=io.json('xle',g.JOURNAL,'recovery-journal-after',end)
            g.handshake_check(initial,first,second,journal_before,journal_after)
            expected_identity=dict(second['run_identity']);expected_journal=journal_after
            outcome['plc_recovery']='PASS'
        else:outcome['plc_recovery']='NOT REQUIRED'
        after=typed.capture();typed.save(local/'typed-after.json',after)
        comparison=typed.compare(before,after);io.save('typed-postflight.json',comparison)
        if comparison['status']!='PASS':raise ValueError('typed postflight differs')
        outcome['typed_postflight']='PASS'
        support_after=collect_scenario_support(io,end,support_before,'support-after')
        outcome['application_attribution']=('SUPPORTED_WITH_LIMITS' if all(
            v['initial_baseline'] is True and v['interval_start_readiness']=='READY'
            for v in support_before.values()) and all(v['application_attribution']=='SUPPORTED_WITH_LIMITS'
            for v in support_after.values()) else 'INCOMPLETE')
        if any(support_before[vm]['services']!=support_after[vm]['services'] for vm in support_before):
            raise ValueError('service incarnation/restart changed during scenario')
        io.save('postflight-preflight.json',pre.check(live=True,batch=True))
        final=io.json('drives',g.STATE,'final-stopped-state',end);g.safe_state(final)
        if final['run_identity']!=expected_identity:raise ValueError('final run identity changed after observation/recovery')
        if io.json('xle',g.JOURNAL,'final-scenario-journal',end)!=expected_journal:
            raise ValueError('final journal inventory changed after observation/recovery')
        g.freshness_check(io.json('scada',g.FRESHNESS,'final-ua-hmi',end))
        outcome.update(service_restoration='NOT REQUIRED',plan_restoration='NOT REQUIRED',orphan_checks='PASS',status='PASS')
        outcome['restoration_notes']='No services/plans/daemons started or changed; typed process/service inventories and exact hashes verified. Synchronous worker completion authenticated.'
    except BaseException as exc:
        outcome['errors']['primary']=error(exc)
        if operation_started and outcome['functional_outcome']=='NOT RUN':outcome['functional_outcome']='INCOMPLETE' if not completed else 'FAIL'
        if operation_started and outcome['plc_recovery']=='NOT RUN':outcome['plc_recovery']='INCOMPLETE'
        # Read-only failure evidence never authorizes recovery while a worker's
        # completion is unknown. Keep postflight failure separate from primary.
        if operation_started:outcome['orphan_checks']='INCOMPLETE'
        if before is not None and time.monotonic()<end:
            try:
                after=typed.capture();typed.save(local/'typed-failure-after.json',after)
                comparison=typed.compare(before,after);io.save('typed-failure-postflight.json',comparison)
                outcome['typed_postflight']=comparison['status']
            except BaseException as post:
                outcome['typed_postflight']='INCOMPLETE';outcome['errors']['postflight']=error(post)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous_alarm)
        pre.guest_read=saved_pre;typed.guest_read=saved_typed
        pre.ROOT,pre.MANIFEST=saved_root,saved_manifest
        outcome['ended_utc']=datetime.now(timezone.utc).isoformat()
        try:
            io.save('scenario-result.json',outcome)
            control.write_run_gate(local,approval_id,case,'PASS' if outcome['status']=='PASS' else 'INCOMPLETE',
                                   seal=(lambda:evidence_hashes(local)) if outcome['status']=='PASS' else None)
            if outcome['status']!='PASS':evidence_hashes(local)
        except BaseException as export:
            outcome['status']='INCOMPLETE';outcome['errors']['evidence_export']=error(export)
            # Evidence failure must also close the canonical next-run gate.
            # A retry is evidence-only, never another package/reset operation.
            try:control.write_run_gate(local,approval_id,case,'INCOMPLETE')
            except BaseException as gate:outcome['errors']['gate_export']=error(gate)
            try:io.save('scenario-result.json',outcome);evidence_hashes(local)
            except BaseException as sealing:outcome['errors']['failure_sealing']=error(sealing)
    return outcome


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
                    try:
                        output = sc.scada.run(["/home/kevin/opcua/bin/python", RECOVERY,
                                               "--reset-run", "--remote-xle"], timeout=55)
                    except GuestCommandError as recovery_exc:
                        for line in reversed(recovery_exc.output.splitlines()):
                            try:
                                payload = json.loads(line.strip())
                            except json.JSONDecodeError:
                                continue
                            if (isinstance(payload, dict) and payload.get("status") == "FAIL" and
                                "counter_sample" in payload):
                                atomic_control(local / "plc-recovery-failure.json", payload)
                                result["plc_recovery_result"] = payload
                                break
                        raise
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
    source.add_argument("--dedicated-prerequisites",type=Path)
    parser.add_argument("--dedicated-case",choices=tuple(DEDICATED_CASES))
    parser.add_argument("--evidence",type=Path)
    parser.add_argument("--approval-id")
    parser.add_argument("--approved-at", help="UTC timestamp of this run's operator approval")
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise InterruptedError(f"host attempt interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.dedicated_prerequisites:
        if not args.dedicated_case or not args.evidence or not args.approval_id or not args.approved_at:
            parser.error('dedicated path needs case, evidence and exact reviewed approval ID/time')
        outcome=run_dedicated(args.dedicated_case,args.dedicated_prerequisites,args.evidence,args.approval_id,args.approved_at)
    else:outcome = (run_reserved(args.reservation, args.approved_at, args.approval_id)
               if args.reservation else run_attempt(args.preparation, args.approval_id))
    print(json.dumps(outcome, sort_keys=True, default=str), flush=True)
    raise SystemExit(0 if outcome["status"] == "PASS" else 1)

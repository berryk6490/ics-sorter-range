"""One reviewed installed-core startup and no-package handshake; host only.

plan is inert. run is for a separately approved operation, never preparation.
Serial completion/liveness, typed comparison and approval spending are canonical.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

import accumulation_state_snapshot as typed
import deployment_preflight as pre
from accumulation_register_contract import RESET_ZERO_PROCESS_COUNTERS
from accumulation_scenario_control import claim_host_approval
import m2_owned_core as owned

ROOT = Path(__file__).resolve().parents[1]
SELECTED = Path('/home/kevin/vm/sorter-m2-measurement-source')
COMMIT = 'f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9'
MANIFEST_SHA = '9a6b3b9c84fc1552fa73f7eb9835d30b4ab781fa52604a55dea23bd1362665ab'
PLC_ROOT = '/home/kevin/OpenPLC_v3/webserver'
QUIT = 'quit()'  # Protocol bytes only; never execute a naked listener-targeted nc command.
MODBUS = 'start_modbus(502)'  # Fixed protocol operation, sent only through owned_command.
HANDSHAKE = ("cd /home/kevin/sorter-services && /usr/bin/timeout --signal=TERM "
             "--kill-after=3s 75s /home/kevin/opcua/bin/python -B "
             "recover_accumulation_state.py --remote-xle --reset-run")
BOUNDS = {'prestart': 240, 'startup': 10, 'listener': 10, 'initial': 35,
          'typed_before': 150, 'handshake': 95, 'verification': 270}
INITIALIZATION_SECONDS = sum(BOUNDS[s] for s in ('initial', 'typed_before', 'handshake'))
WATCHDOG_SECONDS = 600
OPERATOR_ROOT_SHELLS = {'plc':'root','drives':'root','xle':'root'}
ORIGINAL_READ = pre.guest_read


def utc():
    return datetime.now(timezone.utc).isoformat()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def py(code, interpreter='/usr/bin/python3'):
    return shlex.join([interpreter, '-B', '-c', code])


# Read installed files/processes only. No SQLite repair, compile, upload or RPC.
PLC_INSPECT = py(r"""
import hashlib,json,os,pathlib,re,sqlite3,subprocess,sys
p=pathlib.Path('/home/kevin/OpenPLC_v3/webserver')
with sqlite3.connect('file:'+str(p/'openplc.db')+'?mode=ro',uri=True,timeout=2) as db:
 settings=dict(db.execute("SELECT Key,Value FROM Settings WHERE Key IN ('Start_run_mode','Modbus_port')"))
 programs=db.execute("SELECT Prog_ID,File FROM Programs WHERE File='xle_sorter.st'").fetchall()
 integrity=db.execute('PRAGMA integrity_check').fetchall()
cores=[]
for f in pathlib.Path('/proc').iterdir():
 if not f.name.isdigit(): continue
 try:
  if (f/'comm').read_text().strip()!='openplc': continue
  s=(f/'stat').read_text();v=s[s.rfind(')')+2:].split()
  u=re.search(r'(?m)^Uid:\s+(\d+)\s+(\d+)',(f/'status').read_text())
  env=(f/'environ').read_bytes().split(b'\0')
  marker=[v.split(b'=',1)[1].decode() for v in env if v.startswith(b'SORTER_CORE_ATTEMPT=')]
  cores.append(dict(pid=int(f.name),start_ticks=int(v[19]),uid=int(u[1]),euid=int(u[2]),
   boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
   exe=os.readlink(f/'exe'),exe_sha256=hashlib.sha256((f/'exe').read_bytes()).hexdigest(),
   attempt=marker[0] if len(marker)==1 else None))
 except (FileNotFoundError,ProcessLookupError): pass
ports=subprocess.run(['ss','-H','-ltn'],capture_output=True,text=True,timeout=2,check=True).stdout
web=subprocess.run(['curl','-sS','--max-time','2','-o','/dev/null','-w','%{http_code}',
 'http://127.0.0.1:8080/login'],capture_output=True,text=True,timeout=3,check=True).stdout
target=None
if len(sys.argv)>1:
 f=pathlib.Path('/proc')/str(int(sys.argv[1]))
 try:
  s=(f/'stat').read_text();v=s[s.rfind(')')+2:].split()
  target=dict(pid=int(f.name),start_ticks=int(v[19]),
   boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),state=v[0])
 except (FileNotFoundError,ProcessLookupError): pass
print(json.dumps(dict(active_program=(p/'active_program').read_text().strip(),
 settings=settings,programs=programs,integrity=integrity,cores=cores,
 target=target,ports=ports,web=web,hashes={n:hashlib.sha256((p/n).read_bytes()).hexdigest()
 for n in ['core/openplc','core/POUS.c','openplc.py','st_files/xle_sorter.st']})))
""")
PLC_HASHES = {
    'core/openplc': 'b29befd48766a7b9428e67159192195fae9fbe7a46cd93586023a4914592069f',
    'core/POUS.c': 'e506efcd97de5c6d3295f70eac9dca8d0d45af6a4585b49d1b2b6dcd8ea21f2f',
    'openplc.py': 'c965b97930b4850240e78f0a1538f6f68901c3a2147072974436d58a35be7cf8',
    'st_files/xle_sorter.st': '952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6',
}
STATE = 'cd /home/kevin && ' + py("""
import json
from pymodbus.client import ModbusTcpClient
from read_accumulation_state import read_state
c=ModbusTcpClient('10.10.1.10',port=502,timeout=2)
try:
 if not c.connect(): print(json.dumps({'unavailable':True}))
 else:
  s=read_state(c,include_vfds=True)
  def reg(a):
   r=c.read_holding_registers(a,1,slave=1)
   if r.isError() or len(r.registers)!=1: raise RuntimeError('invalid extra read '+str(a))
   return r.registers[0]
  s['startup_extra']={str(a):reg(a) for a in (257,555,556,557,560,564,565,566,567,573,590,593)}
  print(json.dumps(s))
finally: c.close()
""", '/home/kevin/venv/bin/python')
XLE_SERVICE_FIELDS = ('Id','ActiveState','SubState','MainPID','NRestarts','ExecMainPID',
                      'ExecMainStartTimestampMonotonic','InvocationID','ExecStart')
XLE_STATUS = ("env SYSTEMD_PAGER=cat SYSTEMD_COLORS=0 /usr/bin/systemctl --no-pager "
              "show sorter-xle.service"+''.join(' -p '+name for name in XLE_SERVICE_FIELDS))
XLE_PRESTART = py("""
import json,pathlib,subprocess,time
props=['LoadState','ActiveState','SubState','Result','MainPID','ExecMainPID','ExecMainCode',
       'ExecMainStatus','NRestarts','Restart','ExecStart']
args=['env','SYSTEMD_PAGER=cat','SYSTEMD_COLORS=0','/usr/bin/systemctl','--no-pager',
      'show','sorter-xle.service']
for name in props:args+=['-p',name]
s=subprocess.run(args,capture_output=True,text=True,timeout=3,check=True)
fields=dict(v.split('=',1) for v in s.stdout.splitlines() if '=' in v)
j=subprocess.run(['journalctl','--no-pager','-u','sorter-xle.service','--since','-10 seconds',
                  '-n','81','-o','json'],capture_output=True,text=True,timeout=3,check=True)
rows=[json.loads(v) for v in j.stdout.splitlines()]
pid=int(fields['MainPID']);cmdline=None
if pid:
 try:cmdline=(pathlib.Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\\0',b' ').decode().strip()
 except FileNotFoundError:pass
print(json.dumps(dict(service=fields,journal=rows,journal_stderr=j.stderr,status_stderr=s.stderr,
 now_us=time.time_ns()//1000,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 truncated=len(rows)>80,current_cmdline=cmdline)))
""")
# Executable selected explicitly by deploy/systemd/sorter-xle.service:12 at
# f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9; independent of observed argv[0].
XLE_EXECUTABLE = '/opt/sorter-xle/venv/bin/python'
XLE_ARGV = shlex.split('/opt/sorter-xle/venv/bin/python /opt/sorter-xle/xle.py '
                      '--plc 10.10.1.10 --asx http://127.0.0.1:8089/sort-plan '
                      '--multi --packages 0 --journal /var/lib/sorter-xle/outcomes.sqlite3')


def normalize_boot_id(value):
    """Accept only journald hex32 or /proc UUID spelling, returning hex32."""
    require(isinstance(value,str) and re.fullmatch(
        r'(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})',value),
        'missing or malformed boot ID')
    return uuid.UUID(value).hex


def xle_prestart_gate(evidence):
    """Accept only corroborated, recent connection-refusal restart behavior."""
    def verdict(kind,reason):
        return {'status':kind,'accepted':kind=='EXPECTED_PLC_UNAVAILABLE_RETRY','reason':reason}
    try:
        s=evidence['service']
        if s['Result']=='start-limit-hit':
            return verdict('START_LIMIT_FAILURE','systemd start limit reached')
        if s['Result'] not in ('success','exit-code'):
            return verdict('UNEXPECTED_FAILURE','unexpected systemd result')
        if s['LoadState']!='loaded' or s['Restart']!='on-failure':
            return verdict('CONFIGURATION_ERROR','unit not loaded or restart policy differs')
        if s['ExecMainStatus'].isdigit() and 200<=int(s['ExecMainStatus'])<=255:
            return verdict('CONFIGURATION_ERROR','systemd execution/context failure')
        command=s['ExecStart']
        argv=re.search(r'argv\[\]=(.*?);',command)
        if shlex.split(argv[1].strip() if argv else command)!=XLE_ARGV:
            return verdict('CONFIGURATION_ERROR','loaded ExecStart differs from selected XLe')
        state=(s['ActiveState'],s['SubState'])
        if state not in (('activating','auto-restart'),('active','running')):
            return verdict('UNEXPECTED_FAILURE','service is not running or awaiting automatic retry')
        if evidence['truncated'] or evidence['journal_stderr'] or evidence['status_stderr']:
            return verdict('INSUFFICIENT_EVIDENCE','truncated or restricted service/journal read')
        now=evidence['now_us'];rows=evidence['journal'];groups={}
        boot=normalize_boot_id(evidence['boot_id'])
        if not 1<=len(rows)<=80:return verdict('INSUFFICIENT_EVIDENCE','no bounded recent journal')
        refusal=re.compile(r'Connection to \(10\.10\.1\.10,\s*502\) failed:.*(?:Connection refused|Errno 111)')
        for row in rows:
            age=now-int(row['__REALTIME_TIMESTAMP'])
            if not 0<=age<=10_000_000 or normalize_boot_id(row['_BOOT_ID'])!=boot:
                return verdict('INSUFFICIENT_EVIDENCE','stale/future/cross-boot journal entry')
            text=row['MESSAGE']
            if not isinstance(text,str) or len(text)>2048:
                return verdict('INSUFFICIENT_EVIDENCE','invalid/oversized message')
            lower=text.lower()
            if any(v in lower for v in ('start request repeated too quickly','start-limit')):
                return verdict('START_LIMIT_FAILURE','start-limit journal failure')
            if any(v in lower for v in ('failed at step','failed to execute','configuration error')):
                return verdict('CONFIGURATION_ERROR','configuration journal failure')
            if row.get('_SYSTEMD_UNIT')=='sorter-xle.service':
                pid=str(int(row['_PID']));g=groups.setdefault(pid,{'refusal':False,'terminal':False})
                if refusal.search(text):g['refusal']=True
                elif text=='RuntimeError: PLC Modbus unavailable':g['terminal']=True
                elif text=='Traceback (most recent call last):' or text[:1].isspace():pass
                else:return verdict('UNEXPECTED_FAILURE','unrelated or unclassified XLe application message')
            else:
                if not (str(row.get('_PID'))=='1' and
                        row.get('UNIT',row.get('OBJECT_SYSTEMD_UNIT'))=='sorter-xle.service'):
                    return verdict('INSUFFICIENT_EVIDENCE','unattributed manager journal row')
                manager=text.removeprefix('sorter-xle.service: ')
                if manager in ("Main process exited, code=exited, status=1/FAILURE",
                               "Failed with result 'exit-code'."):
                    pass
                elif re.fullmatch(r'Scheduled restart job, restart counter is at \d+\.',manager):
                    pass
                elif re.fullmatch(r'(?:Started|Stopped) (?:sorter-xle\.service - )?'
                                  r'Sorter XLe route execution and durable outcome journal\.',manager):
                    pass
                else:
                    return verdict('UNEXPECTED_FAILURE','unclassified manager failure or transition')
        proven={pid for pid,g in groups.items() if g['refusal'] and g['terminal']}
        if not proven:return verdict('INSUFFICIENT_EVIDENCE','no same-PID refusal plus expected terminal error')
        if state==('activating','auto-restart'):
            if not (s['Result']=='exit-code' and s['ExecMainCode']=='1' and s['ExecMainStatus']=='1'
                    and s['MainPID']=='0' and s['ExecMainPID'] in proven):
                return verdict('UNEXPECTED_FAILURE','last service exit does not match proven retry PID/status')
        else:
            if not (int(s['MainPID'])>0 and s['MainPID']==s['ExecMainPID'] and
                    int(s['NRestarts'])>0 and evidence['current_cmdline'] and
                    shlex.split(evidence['current_cmdline'])==XLE_ARGV):
                return verdict('INSUFFICIENT_EVIDENCE','current retry process is not canonical')
        return verdict('EXPECTED_PLC_UNAVAILABLE_RETRY','recent same-PID connection refusal and normal retry')
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        return verdict('INSUFFICIENT_EVIDENCE',f'malformed structured evidence: {exc}')


JOURNAL = py("""
import json,sqlite3,time
from contextlib import closing
end=time.monotonic()+3
with closing(sqlite3.connect('file:/var/lib/sorter-xle/outcomes.sqlite3?mode=ro',uri=True,timeout=2)) as d:
 d.execute('PRAGMA query_only=ON')
 d.set_progress_handler(lambda:int(time.monotonic()>=end),1000)
 d.execute('BEGIN')
 print(json.dumps({'integrity':d.execute('PRAGMA integrity_check').fetchall(),
 'epochs':[r[0] for r in d.execute('SELECT identity FROM run_epochs ORDER BY identity')],
 'outcomes':[r[0] for r in d.execute('SELECT identity FROM outcomes ORDER BY identity')]}))
""")
PLC_LAUNCH_CONTEXT = py("""
import json,os,pathlib
p=pathlib.Path('/proc/self')
s=dict(v.split(':',1) for v in (p/'status').read_text().splitlines() if ':' in v)
for name in ('stat','status','environ','exe'):
 with (p/name).open('rb') as f:f.read(1)
list((p/'fd').iterdir());os.readlink(p/'exe')
for name in ('tcp','tcp6'):
 with pathlib.Path('/proc/net',name).open('rb') as f:f.read(1)
fd=os.pidfd_open(os.getpid());os.close(fd)
print(json.dumps(dict(uid=os.getuid(),euid=os.geteuid(),cap_eff=s['CapEff'].strip(),
 cap_bnd=s['CapBnd'].strip(),no_new_privs=s['NoNewPrivs'].strip(),
 proc_support=True,
 threshold=int(pathlib.Path('/proc/sys/net/ipv4/ip_unprivileged_port_start').read_text()),
 initial_namespaces=all(os.readlink(p/'ns'/n)==os.readlink(pathlib.Path('/proc/1/ns')/n)
                        for n in ('net','user')))))
""")

# Fixed nine-worker read only. Values outside the selected ADDR/LANE whitelist
# never enter output, including in a mismatch/error. No full environ or hashes.
DRIVES_READ_CODE = r'''
import json,os,pathlib,re,socket,subprocess
def selected_environment(raw,expected):
 values={}
 for item in raw.split(b'\0'):
  if b'=' not in item:continue
  key,value=item.split(b'=',1)
  if key.decode(errors='replace') in expected:
   key=key.decode()
   if key in values:raise ValueError('duplicate selected environment key')
   if value!=expected[key].encode():raise ValueError('selected environment mismatch (values withheld)')
   values[key]=value.decode()
 if values!=expected:raise ValueError('required environment key missing')
 return values
def birth(p):
 s=(p/'stat').read_text();v=s[s.rfind(')')+2:].split()
 u=re.search(r'(?m)^Uid:\s+(\d+)\s+(\d+)',(p/'status').read_text())
 return dict(pid=int(p.name),start_ticks=int(v[19]),uid=int(u[1]),euid=int(u[2]),state=v[0])
def unit(name):
 s=subprocess.check_output(['/usr/bin/systemctl','--no-pager','show',name,
  '-p','MainPID','-p','ActiveState','-p','SubState'],text=True,timeout=1)
 d=dict(v.split('=',1) for v in s.splitlines() if '=' in v)
 if d['ActiveState']!='active' or d['SubState']!='running':raise ValueError('worker unit not running')
 return int(d['MainPID'])
def main():
 import select
 if os.getuid()!=0 or os.geteuid()!=0:raise ValueError('operator root read context required')
 specs=[('vfd',k+str(i),str(21+j),None) for j,(k,i) in enumerate(
  [(k,i) for k in ('induct','outbnd') for i in (1,2,3)])]
 specs += [('scanner','tunnel'+str(i),str(26+i),str(i)) for i in (1,2,3)]
 rows=[]
 for kind,name,suffix,lane in specs:
  service=kind+'@'+name+'.service';pid=unit(service)
  if pid<=0:raise ValueError('invalid worker PID')
  p=pathlib.Path('/proc')/str(pid);fd=os.pidfd_open(pid)
  try:
   before=birth(p)
   if before['uid']!=0 or before['euid']!=0 or before['state']=='Z':raise ValueError('worker identity differs')
   expected={'ADDR':'10.10.1.'+suffix}
   if lane is not None:expected['LANE']=lane
   env=selected_environment((p/'environ').read_bytes(),expected)
   args=['/home/kevin/venv/bin/python','/home/kevin/'+kind+'.py',expected['ADDR'],name]
   if lane is not None:args.append(lane)
   if (p/'cmdline').read_bytes().split(b'\0')[:-1]!=[v.encode() for v in args]:raise ValueError('worker argv mismatch (values withheld)')
   inodes=set()
   for f in (p/'fd').iterdir():
    try:
     m=re.fullmatch(r'socket:\[(\d+)\]',os.readlink(f))
     if m:inodes.add(int(m[1]))
    except FileNotFoundError:pass
   matches=[]
   for line in pathlib.Path('/proc/net/tcp').read_text().splitlines()[1:]:
    v=line.split();address,port=v[1].split(':')
    if v[3]=='0A' and socket.inet_ntoa(bytes.fromhex(address)[::-1])==expected['ADDR'] and int(port,16)==502:
     matches.append(int(v[9]))
   if len(matches)!=1 or matches[0] not in inodes:raise ValueError('worker socket ownership unproven')
   after=birth(p)
   if any(after[k]!=before[k] for k in ('pid','start_ticks','uid','euid')) or after['state']=='Z' or unit(service)!=pid or select.select([fd],[],[],0)[0]:raise ValueError('worker incarnation changed')
   rows.append(dict(service=service,**before,selected_environment=env,listener_inode=matches[0],port=502))
  finally:os.close(fd)
 print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
  workers=rows,environment_policy='ADDR/LANE only; all other values discarded')))
if __name__=='__main__':main()
'''
DRIVES_ROOT_READ = '/usr/bin/timeout --signal=TERM --kill-after=2s 20s '+py(DRIVES_READ_CODE)


def launch_context_guard(r):
    require(type(r['uid']) is int and type(r['euid']) is int and r['uid']==0 and r['euid']==0 and
            type(r['threshold']) is int and 0<=r['threshold']<=65535 and
            r['initial_namespaces'] is True and r['proc_support'] is True,
            'reviewed existing root/initial-network context required')
    require(r['threshold']<=502 or
            (int(r['cap_eff'],16)&(1<<10) and int(r['cap_bnd'],16)&(1<<10)),
            'existing context lacks CAP_NET_BIND_SERVICE for502; do not launch or grant rights')


FRESHNESS = py("""
import asyncio,json,time,urllib.request
from asyncua import Client
async def read():
 rows=[]
 async with Client('opc.tcp://10.10.2.10:4840/sorter/',timeout=3) as c:
  i=await c.get_namespace_index('urn:sorter:level2')
  async def value(path):
   n=await c.nodes.objects.get_child([str(i)+':'+v for v in ['Sorter',*path]])
   d=await n.read_data_value()
   if not d.StatusCode.is_good(): raise RuntimeError('bad UA quality')
   return d.Value.Value
  for sample in range(2):
   stamp=await value(['Process','Status','PlcPollMonotonicMs'])
   h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api',timeout=3))
   rows.append(dict(poll_ms=stamp,age_ms=time.monotonic_ns()//1000000-stamp,
    identity=await value(['Run','ProgramHash']),hmi=h))
   if sample==0: await asyncio.sleep(.5)
 print(json.dumps(rows))
asyncio.run(read())
""", '/home/kevin/opcua/bin/python')


def timing_policy():
    return {'bounds_seconds':dict(BOUNDS),'overall_watchdog_seconds':WATCHDOG_SECONDS,
            'abort_seconds':30,'owned_rpc_max_launch_age_seconds':630}


def freshness_check(rows):
    require(isinstance(rows,list) and len(rows)==2,'two UA/HMI freshness samples required')
    for row in rows:
        require(all(type(row.get(k)) is int for k in ('poll_ms','age_ms','identity')) and
                row['poll_ms']>0 and row['identity']==24115 and 0<=row['age_ms']<=1500,
                'bad/stale UA identity or poll data')
        h=row['hmi']
        require(h['connected'] is True and h['chute']['poll_fresh'] is True and h['run'] is False and
                all(type(h[k]) is int and h[k]==expected for k,expected in
                    [('plant_fault',0),('scanner_state',0),('scanner_fault_mask',0),
                     ('scanner_ack_mask',7),('zone_fault',0),('inducted',0)]),'stale/unsafe HMI data')
    require(rows[1]['poll_ms']>rows[0]['poll_ms'],'UA not advancing')
    return {'status':'PASS','samples':2,'endpoint':'opc.tcp://10.10.2.10:4840/sorter/'}


def listening(report, port):
    return any(re.search(r':'+str(port)+r'\s', line) for line in report['ports'].splitlines())


def installed_guard(report, *, stopped, core=None):
    require(report['active_program'] == 'xle_sorter.st' and
            report['settings'] == {'Start_run_mode': 'false', 'Modbus_port': '502'} and
            report['programs'] == [[38, 'xle_sorter.st']] and
            report['integrity'] == [['ok']] and report['hashes'] == PLC_HASHES and
            report['web'] == '200', 'installed PLC/configuration/Web UI identity changed')
    if stopped:
        require(report['cores'] == [] and not listening(report, 502) and
                not listening(report, 43628), 'core/listeners must initially be absent')
    else:
        require(len(report['cores']) == 1, 'not exactly one installed core')
        r = report['cores'][0]
        require(r['exe'] == PLC_ROOT+'/core/openplc' and r['exe_sha256']==PLC_HASHES['core/openplc'],
                'wrong running executable')
        if core is not None:
            require(r == core, 'core PID/start/UID/boot/executable changed')
    return report['cores'][0] if report['cores'] else None


def safe_state(s, *, phase='ready', plant_initial=False, epoch_initial=False,
               initialization_deadline=None):
    """Startup policy supplements (never changes) the canonical comparator.

    The caller supplies a one-shot startup deadline. Flags have no effect in
    ready phase. QW564 is a fresh host-side read, not a guessed reader field.
    """
    require(phase in ('initializing','ready'), 'invalid state-check phase')
    initializing = phase == 'initializing'
    if initializing:
        require(initialization_deadline is not None and
                0 < initialization_deadline-time.monotonic() <= INITIALIZATION_SECONDS,
                'initialization deadline expired or unbounded')
    require(s.get('reader_schema') == 3 and s.get('identity') == 24115, 'PLC identity/schema')
    lengths = {'coils_880_920':41,'slots':3,'lanes':3,'process_214_242':29,
               'scanner_counters_250_254':5,'trailer_counters':9,'plant_faults':3,
               'photoeye_faults':3,'xle_health':2,'zone_view':23}
    require(all(isinstance(s.get(k),list) and len(s[k])==n for k,n in lengths.items()),
            'incomplete safe-state response')
    require(all(len(r)==12 for r in s['slots']), 'incomplete slot response')
    require(all(type(v) is bool for v in s['coils_880_920']), 'invalid coil types')
    identity=s['run_identity']
    require(isinstance(identity['epoch'],list) and len(identity['epoch'])==2 and
            all(type(v) is int and 0<=v<30000 for v in identity['epoch']) and
            isinstance(identity['plant_epoch_nonce'],list) and len(identity['plant_epoch_nonce'])==3 and
            type(identity['scanner_nonce']) is int and 0<=identity['scanner_nonce']<=30000,
            'incomplete/invalid run identity')
    words=[*s['lanes'],*s['process_214_242'],*s['scanner_counters_250_254'],
           *s['trailer_counters'],*s['plant_faults'],*s['photoeye_faults'],
           *s['xle_health'],*s['zone_view'],*[v for r in s['slots'] for v in r]]
    require(all(type(v) is int and 0<=v<=65535 for v in words),'invalid register types/ranges')
    forbidden = (880,910,912,913,916,917,920,914,915,918,919)
    require(not any(s['coils_880_920'][a-880] for a in forbidden) and
            s['chute']['mode'] is False, 'master/reset/ACK/retry/forbidden mode asserted')
    require(not any(r[4] for r in s['slots']) and not any(s['lanes']), 'package occupancy')
    require(not any(s['process_214_242'][a-214] for a in RESET_ZERO_PROCESS_COUNTERS) and
            not any(s['scanner_counters_250_254']) and not any(s['trailer_counters']),
            'no-package counters changed')
    plant = s['plant_faults'][0]
    epoch_fault = s['run_identity']['epoch_fault']
    request = s['startup_extra']['564']
    require(type(request) is int and request == 0, 'XLe fail-closed request or invalid QW564')
    require(type(epoch_fault) is int, 'invalid epoch fault')
    cold_epoch = identity['epoch'] == [0,0]
    require((epoch_fault == 0 and not cold_epoch) or
            (initializing and epoch_initial and epoch_fault == 1 and cold_epoch),
            'epoch fault/accepted epoch inconsistent with phase or explicit initialization policy')
    require(plant == 0 or (initializing and plant_initial and plant == 1),
            'plant fault outside explicit initialization exception')
    require(not s['plant_faults'][1] and not any(s['photoeye_faults']) and
            not s['scanner_state'] and not s['scanner_fault_mask'] and
            not s['xle_health'][1] and not s['zone_view'][20] and not s['zone_view'][22] and
            s['chute']['validated'][2] == 0 and s['chute']['validated'][3] == 0 and
            s['chute']['validated'][6] == 1, 'other fault, confirmation, ready or chute unsafe')
    require(set(s['vfds']) == set(typed.VFD_NAMES), 'missing six VFDs')
    for r in s['vfds'].values():
        require(r['fault'] == 0 and 0 <= r['feedback_rpm'] <= 40 and
                r['status'] & 1 and not r['status'] & 6, 'VFD not healthy/stopped')
    return {'status':'INITIALIZING' if initializing else 'PASS', 'phase':phase,
            'run_ready':not initializing, 'plant_fault':plant,'epoch_fault':epoch_fault,
            'epoch_fault_request':request,
            'initialization_deadline_monotonic':initialization_deadline if initializing else None}


def handshake_check(before, first, second, journal_before, journal_after):
    for s in (first, second):
        safe_state(s)
        e=s['startup_extra']; identity=s['run_identity']
        require(e['257']==7 and 1<=identity['scanner_nonce']<=30000 and
                identity['scanner_nonce'] != before['run_identity']['scanner_nonce'],
                'scanner nonce/ACK mask not fresh')
        epoch=identity['epoch'][1]*30000+identity['epoch'][0]
        require(epoch>0 and identity['epoch'] != before['run_identity']['epoch'] and
                identity['plant_epoch_nonce']==[*identity['epoch'],identity['scanner_nonce']] and
                identity['plant_epoch_nonce'] != before['run_identity']['plant_epoch_nonce'],
                'plant/epoch tuple not fresh')
        require(1<=e['573']<=30000 and e['573']==e['567'] and e['593']<=30, 'XLe ACK or plant age')
        require(1<=e['557']<=30000 and e['560']==e['557'] and
                [e['555'],e['556']]==identity['epoch'] and
                [e['565'],e['566']]==identity['epoch'], 'epoch offer/ACK or heartbeat epoch mismatch')
        require(epoch in journal_after['epochs'] and epoch not in journal_before['epochs'] and
                journal_after['integrity']==[['ok']], 'new epoch not backed by writable-owner journal')
        prefix=f"{epoch}:{identity['scanner_nonce']}:"
        require(not any(v.startswith(prefix) for v in journal_after['outcomes']), 'unexpected package journal outcome')
    require(first['run_identity']==second['run_identity'], 'handshake identity unstable')
    require(first['startup_extra']['590'] != second['startup_extra']['590'], 'plant heartbeat frozen')


class LiveIO:
    def __init__(self, directory, best_effort=False,shell_users=None):
        self.directory=Path(directory); self.best_effort=best_effort; self.errors=[]
        self.shell_users=dict(shell_users or {})

    def save(self, name, value):
        try:
            typed.save(self.directory/name,value)
        except Exception as exc:
            if not self.best_effort: raise
            self.errors.append(f'{name}: {type(exc).__name__}: {exc}')

    def read(self, vm, command, label, deadline):
        attempt=label+'-'+str(time.monotonic_ns())
        def trace(row):
            self.save('serial-'+attempt+'-'+('alive' if row['probe'] else 'command')+'.json',
                      {**row,'recorded_utc':utc(),'deadline_monotonic':deadline})
            if label=='launch-once' and not row['probe']:
                # Canonical trace runs before command-rc validation can raise.
                # Keep raw receipt first, then an explicitly untrusted readable
                # extraction, including failed guest launch/cleanup diagnostics.
                events=[];errors=[]
                try:
                    output=json.loads(row['stdout'].splitlines()[-1])['output']
                    for line in output.splitlines():
                        try:event=json.loads(line)
                        except ValueError:continue
                        if isinstance(event,dict) and event.get('kind')=='m2-core-launch':events.append(event)
                except (ValueError,KeyError,IndexError,TypeError) as exc:errors.append(str(exc))
                self.save('launch-diagnostics.json',{'untrusted':True,'events':events,
                          'extraction_errors':errors,'complete_terminal':any(
                              e.get('event')=='terminal' for e in events)})
        lock=None
        try:
            if vm=='plc':
                # Shared by this controller/watchdog only. Never force a console.
                lock_path=self.directory/'plc-console.lock'
                lock=lock_path.open('r' if lock_path.exists() else 'a')
                while True:
                    try:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        require(time.monotonic()<deadline,'PLC console lock deadline')
                        time.sleep(.02)
            # Scenario transport outlasts the independently bounded 300+3s
            # inline worker. The caller's overall deadline still caps it.
            timeout=305 if label=='dedicated-package' else (85 if label in (
                'canonical-handshake','documented-recovery','post-scenario-recovery') else 20)
            output=ORIGINAL_READ(vm,command,label,deadline=deadline,trace=trace,
                                 timeout=timeout,
                                 **({'shell_user':self.shell_users[vm]} if vm in self.shell_users else {}))
        finally:
            if lock is not None: lock.close()
        self.save(label+'.json',{'vm':vm,'command':command,'output':output,'completed_utc':utc()})
        return output

    def json(self, vm, command, label, deadline):
        return json.loads(self.read(vm,command,label,deadline).splitlines()[-1])


def owned_command(action, data):
    """Render this reviewed host helper inline; no guest file is installed."""
    return py(Path(owned.__file__).read_text())+' '+shlex.join([action,json.dumps(data,sort_keys=True)])


def valid_log_path(log):
    require(isinstance(log,str) and str(Path(log))==log and log.startswith('/') and
            re.fullmatch(r'/[A-Za-z0-9_./-]+',log) and '..' not in Path(log).parts,
            'reviewed absolute safe log path required')
    return log


def export_core_log(io,log,label,deadline,filename):
    valid_log_path(log)
    exported=io.json('plc',py("import base64,hashlib,json,pathlib; p=pathlib.Path("+repr(log)+"); "
        "n=p.stat().st_size; assert n<=65536,'core log exceeds reviewed 64 KiB export cap'; "
        "b=p.read_bytes(); assert len(b)==n,'core log changed during export'; "
        "print(json.dumps(dict(path=str(p),size=n,sha256=hashlib.sha256(b).hexdigest(),"
        "base64=base64.b64encode(b).decode())))"),label,deadline)
    payload=base64.b64decode(exported['base64'],validate=True)
    require(len(payload)==exported['size'] and len(payload)<=65536 and
            hashlib.sha256(payload).hexdigest()==exported['sha256'],'core log export integrity')
    (io.directory/filename).write_bytes(payload)
    return {'status':'COMPLETE','path':log,'size':len(payload),'sha256':exported['sha256'],
            'host_file':filename,'errors':[]}


def watchdog(receiver, directory, token, overall=WATCHDOG_SECONDS, io_factory=LiveIO, core_log=None,
             shell_users=None):
    """Independent bounds; a validated private receipt is required for RPC."""
    os.setsid()
    io=io_factory(directory,best_effort=True,**({'shell_users':shell_users} if shell_users else {}))
    record={'token':token,'status':'READY','terminal':False,'abort':'NOT RUN','armed':False,
            'errors':[],'ownership_errors':[],'log_export':{'status':'NOT RUN','errors':[]}}
    deadline=time.monotonic()+3;total_end=time.monotonic()+overall
    accepted=False;authorization=None;cleaning=False
    def interrupted(signum,frame):
        if not cleaning:raise RuntimeError(f'watchdog signal {signum}')
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,interrupted)
    try:
        io.save('watchdog.json',record)
        while True:
            remaining=min(deadline,total_end)-time.monotonic()
            if remaining<=0 or not receiver.poll(remaining):
                raise TimeoutError('independent stage/overall watchdog expired')
            message=receiver.recv()
            stage=message['stage']
            if stage=='owned-core':
                require(record['armed'],'ownership before launch stage')
                candidate=message['authorization'];owned.validate_authorization(candidate,token)
                require(authorization is None or authorization==candidate,'core ownership changed')
                authorization=candidate
                record['ownership_validated']=True;io.save('watchdog.json',record)
                continue  # does not renew any deadline
            if stage=='accept':
                proof=json.loads((Path(directory)/'verification-PASS.json').read_text())
                require(proof=={'status':'PASS','token':token} and authorization is not None,
                        'missing acceptance/ownership proof')
                accepted=True;record['status']='ACCEPTED';break
            if stage=='abort':raise RuntimeError('controller requested abort')
            require(stage in BOUNDS and stage!='prestart','invalid watchdog stage')
            record['armed']=True;deadline=time.monotonic()+BOUNDS[stage];record['stage']=stage
            io.save('watchdog.json',record)
    except BaseException as exc:
        record['error']=f'{type(exc).__name__}: {exc}'
        record['status']='INCOMPLETE'
    finally:
        cleaning=True
        if record['armed'] and not accepted:
            record['abort']='INCOMPLETE';record['operator_recovery_needed']=True
            abort_end=time.monotonic()+30;target=None;rpc=None
            try:
                require(authorization is not None,'no validated ownership received from controller')
                persisted=json.loads((Path(directory)/'started-core-identity.json').read_text())
                require(persisted==authorization,'missing/corrupt/mismatched ownership artifact')
                target=owned.validate_authorization(authorization,token)
                rpc=io.json('plc',owned_command('abort',{'authorization':authorization,'token':token}),
                            'abort-owned-rpc',min(time.monotonic()+20,abort_end-10))
                record['rpc']=rpc
                require(rpc.get('status')=='SENT' and rpc.get('rpc_sent') is True,
                        'owned RPC refused or unconfirmed: '+str(rpc.get('error',rpc)))
            except BaseException as exc:
                record['ownership_errors'].append(f'{type(exc).__name__}: {exc}')
            # Process exit is independently established from PID/start/boot.
            # An absent listener, or an unreadable/missing identity, is not exit proof.
            end=min(time.monotonic()+10,abort_end)
            try:
                while True:
                    command=PLC_INSPECT if target is None else PLC_INSPECT+' '+str(target['pid'])
                    final=io.json('plc',command,'abort-final',end)
                    record['final_observation']=final
                    if target is not None:require('target' in final,'missing exact-PID observation')
                    observed=final.get('target')
                    same=(target is not None and observed is not None and
                          all(observed[k]==target[k] for k in ('pid','start_ticks','boot_id')))
                    outstanding=bool(final['cores'] or listening(final,502) or listening(final,43628) or same)
                    if outstanding:
                        # A refused RPC must not spin hoping another owner disappears.
                        require(rpc is not None and rpc.get('rpc_sent') is True,
                                'stop refused; operator recovery required')
                        require(time.monotonic()<end,'core/listener exit deadline')
                        time.sleep(.1);continue
                    installed_guard(final,stopped=True)
                    record['final_state']=final
                    record['final_status']='PASS' if target is not None and not record['ownership_errors'] else 'INCOMPLETE'
                    if target is not None and not record['ownership_errors']:
                        record['abort']='PASS';record['operator_recovery_needed']=False
                    break
            except BaseException as exc:
                record['errors'].append(f'stop verification: {type(exc).__name__}: {exc}')
            # Lower priority than stop/proof. Export never receives a new budget.
            try:
                require(core_log is not None,'reviewed core log path unavailable')
                require(abort_end-time.monotonic()>.5,'abort budget exhausted before log export')
                record['log_export']=export_core_log(io,core_log,'abort-core-log',
                    min(time.monotonic()+5,abort_end),'abort-core.log')
            except BaseException as exc:
                record['log_export']={'status':'INCOMPLETE','errors':[f'{type(exc).__name__}: {exc}']}
            io.save('abort-log-export.json',record['log_export'])
        record['ended_utc']=utc();record['terminal']=True;record['evidence_errors']=io.errors
        io.save('watchdog.json',record)
        print(json.dumps(record),flush=True)
        receiver.close()


def startup_command(log,token='0'*32,uid=0,cleanup_on_failure=False):
    valid_log_path(log)
    return f'cd {PLC_ROOT} && '+owned_command('launch',{'log':log,'token':token,'uid':uid,
                                                     'cleanup_on_failure':cleanup_on_failure})

def properties(output, *, command=XLE_STATUS, fields=XLE_SERVICE_FIELDS):
    """One exact systemctl payload, optionally preceded by its canonical echo.

    This selects service properties, never rewrites a receipt or authenticates
    transport. LiveIO/guest_read must already have checked nonce/root/prompt/rc
    and separate liveness. Explicit legacy fields are for offline subset review;
    the live stability gate always requires XLE_SERVICE_FIELDS.
    """
    require(isinstance(output,str),'invalid service-observation output')
    lines=output.splitlines()
    echo=re.escape(command)+r"; printf '__SORTER_RC_[0-9a-f]{32}__%s\\n' "+re.escape('"$?"')
    if lines and re.fullmatch(echo,lines[0]):lines=lines[1:]
    require(len(lines)==len(fields),'missing, extra or ambiguous service payload')
    result={}
    for line in lines:
        name,sep,value=line.partition('=')
        require(sep and name in fields and name not in result and value!='',
                'malformed, duplicate or unrelated service property')
        result[name]=value
    require(set(result)==set(fields),'missing required service properties')
    return result


def validate_xle_exec_start(entry):
    """No path/argument coercion, resolution or substitution from argv[0]."""
    require(isinstance(entry,dict) and set(entry)=={'path','argv'},'malformed XLe ExecStart entry')
    require(isinstance(entry['path'],str) and entry['path']==XLE_EXECUTABLE,
            'XLe ExecStart executable path differs from selected service')
    require(isinstance(entry['argv'],list) and all(isinstance(arg,str) for arg in entry['argv']) and
            entry['argv']==XLE_ARGV,'XLe ExecStart argv differs from selected service')
    return entry


def xle_exec_start(raw):
    """Parse one canonical systemd entry; retain the original text in the view."""
    require(isinstance(raw,str),'XLe ExecStart must be a string')
    entry=re.fullmatch(r'\{([^{}]*)\}',raw)
    require(entry is not None,'XLe ExecStart must contain one unambiguous canonical entry')
    parts=entry[1].split(';')
    if not parts[-1].strip():parts.pop()  # optional final canonical separator
    fields={}
    allowed={'path','argv[]','ignore_errors','start_time','stop_time','pid','code','status'}
    for part in parts:
        field=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*(?:\[\])?)=(.*)',part.strip())
        require(field is not None and field[1] in allowed and field[1] not in fields and
                field[2].strip()!='','malformed, duplicate or ambiguous XLe ExecStart field')
        fields[field[1]]=field[2].strip()
    require('path' in fields and 'argv[]' in fields,'XLe ExecStart path/argv missing')
    return validate_xle_exec_start({'path':fields['path'],'argv':shlex.split(fields['argv[]'])})


def xle_stability_check(first,second):
    """Compare only the complete, explicitly selected service/incarnation view."""
    for sample in (first,second):
        require(isinstance(sample,dict) and set(sample)==set(XLE_SERVICE_FIELDS) and
                all(isinstance(v,str) for v in sample.values()),'incomplete XLe service identity')
        require(sample['Id']=='sorter-xle.service' and sample['ActiveState']=='active' and
                sample['SubState']=='running','XLe unstable/restarting: service identity/state')
        for name in ('MainPID','ExecMainPID','ExecMainStartTimestampMonotonic'):
            require(re.fullmatch(r'[1-9][0-9]*',sample[name]),'invalid XLe execution identity: '+name)
        require(sample['MainPID']==sample['ExecMainPID'],'XLe main/execution PID mismatch')
        require(re.fullmatch(r'0|[1-9][0-9]*',sample['NRestarts']),'invalid XLe restart count')
        require(re.fullmatch(r'[0-9a-f]{32}',sample['InvocationID']) and
                sample['InvocationID']!='0'*32,'invalid XLe invocation identity')
        xle_exec_start(sample['ExecStart'])
    changed=[name for name in XLE_SERVICE_FIELDS if first[name]!=second[name]]
    require(not changed,'XLe unstable/restarting: changed '+','.join(changed))
    return {'status':'PASS','service':'sorter-xle.service','compared_fields':list(XLE_SERVICE_FIELDS)}


class Controller:
    def __init__(self, io, sender, token, *, plant_initial=False, epoch_initial=False):
        self.io,self.sender,self.token=io,sender,token
        self.plant_initial=plant_initial
        self.epoch_initial=epoch_initial
        self.initialization_deadline=None
        self.ready_identity=None
        self.results={k:{'status':'NOT RUN'} for k in
                      ('prestart','startup','listener','initial','typed_before','handshake','verification','abort','final_state')}
        self.results.update(schema_version=1,selected_source_commit=COMMIT,functional_outcome='NOT RUN')
        self.deadline=None

    @contextmanager
    def stage(self, name):
        self.deadline=time.monotonic()+BOUNDS[name]
        if name!='prestart': self.sender.send({'stage':name})
        self.results[name]={'status':'IN_PROGRESS','started_utc':utc()}
        try:
            yield
            require(time.monotonic()<=self.deadline, name+' deadline expired')
            self.results[name].update(status='INITIALIZING' if name in ('initial','typed_before')
                                      else 'PASS',ended_utc=utc())
        except BaseException as exc:
            self.results[name].update(status='FAIL',error=f'{type(exc).__name__}: {exc}',ended_utc=utc())
            raise
        finally:
            self.io.save('results.json',self.results)

    def read(self, vm, command, label):
        return self.io.read(vm,command,label,self.deadline)

    def request_abort(self, exc):
        self.results['primary_error']=f'{type(exc).__name__}: {exc}'
        try: self.sender.send({'stage':'abort'})
        except (OSError,BrokenPipeError): pass

    def state(self, label):
        return self.io.json('drives',STATE,label,self.deadline)

    def initializing(self, state):
        require(self.ready_identity is None, 'initialization allowance cannot recur after readiness')
        require(self.results.get('initial',{}).get('started_utc') is not None,
                'initialization not armed by startup')
        return safe_state(state,phase='initializing',plant_initial=self.plant_initial,
                          epoch_initial=self.epoch_initial,
                          initialization_deadline=min(self.deadline,self.initialization_deadline))

    def verify_ready_identity(self, state, journal):
        handshake_check(self.before,self.ready_sample,state,self.journal_before,journal)
        require(state['run_identity']==self.ready_identity, 'identity changed after synchronization')

    def start(self, log, expected_uid):
        self.core_log=log
        with self.stage('startup'):
            response=self.read('plc',startup_command(log,self.token,expected_uid,
                               getattr(self,'cleanup_on_failure',False)),'launch-once')
            self.io.save('launch-observation.json',{'token':self.token,'untrusted_output':response})
            try:launch=json.loads(response.splitlines()[-1])
            except (ValueError,IndexError) as exc:
                raise ValueError('ambiguous launch receipt; no ownership authorized') from exc
            owned.validate_launch(launch,self.token,expected_uid)
            while True:
                r=self.io.json('plc',PLC_INSPECT,'started-core',self.deadline)
                self.io.save('started-core-observation.json',{'token':self.token,'untrusted_report':r})
                if not r['cores']:
                    require(time.monotonic()<self.deadline,'core appearance deadline')
                    time.sleep(.1);continue
                observed=installed_guard(r,stopped=False,core=getattr(self,'core',None))
                authorization=owned.validate_observation(launch,observed,self.token,expected_uid)
                require(not listening(r,502),'unexpected early 502 listener')
                if not hasattr(self,'core'):
                    self.io.save('started-core-identity.json',authorization)
                    self.sender.send({'stage':'owned-core','authorization':authorization})
                self.core=observed;self.authorization=authorization
                if not listening(r,43628):
                    require(time.monotonic()<self.deadline,'RPC appearance deadline')
                    time.sleep(.1);continue
                break
        with self.stage('listener'):
            reply=self.io.json('plc',owned_command('modbus',{'authorization':self.authorization,
                               'token':self.token}),'start-modbus-once',self.deadline)
            self.results['listener']['rpc_ownership']=reply
            require(reply.get('status')=='SENT' and reply.get('rpc_sent') is True and
                    reply.get('response','').strip()=='OK',
                    'Modbus RPC did not acknowledge OK')
            while True:
                r=self.io.json('plc',PLC_INSPECT,'listener-check',self.deadline)
                installed_guard(r,stopped=False,core=self.core)
                if listening(r,502): break
                require(time.monotonic()<self.deadline,'502 listener deadline');time.sleep(.1)
        with self.stage('initial'):
            self.initialization_deadline=time.monotonic()+INITIALIZATION_SECONDS
            while True:
                self.before=self.state('initial-state')
                if not self.before.get('unavailable'): break
                require(time.monotonic()<self.deadline,'initial readable-state deadline');time.sleep(.2)
            policy=self.initializing(self.before)
            self.results['initial']['policy']=policy
            self.results['initial']['run_ready']=False
            self.io.save('initial-policy.json',policy)  # never relabel an exception as run-ready

    def complete(self):
        # Canonical readers/comparator and full preflight retain their contracts.
        # Only their transport is supplied with this stage's remaining deadline.
        pre.guest_read=lambda vm,command,label:self.read(vm,command,label.replace('/','_').replace(':','-'))
        typed.guest_read=pre.guest_read
        with self.stage('typed_before'):
            before=typed.capture();self.io.save('typed-before.json',before)
            # The canonical guest reader does not include QW564. Retain its
            # raw snapshot and use a distinct fresh policy sample for that
            # field, requiring consistent run identity between both reads.
            current=self.state('typed-before-policy')
            self.initializing(current)
            require(before['plc']['run_identity']==current['run_identity'],
                    'typed/policy run identity changed before handshake')
            policy=self.initializing({**before['plc'],'startup_extra':current['startup_extra']})
            self.io.save('typed-before-policy.json',{'policy':policy,'sample':current})
            comparison=typed.compare(before,before);self.io.save('typed-before-self.json',comparison)
            allowed=set()
            if self.plant_initial and before['plc']['plant_faults'][0]==1: allowed.add('plc.plant_faults.0')
            if self.epoch_initial and before['plc']['run_identity']['epoch_fault']==1:
                allowed.add('plc.run_identity.epoch_fault')
            require(all(r['field'] in allowed for r in comparison['differences']),
                    'typed pre-handshake failures outside explicit initialization policy')
            self.results['typed_before']['initialization_exception_fields']=sorted(allowed)
            self.results['typed_before'].update(policy=policy,run_ready=False)
            self.before=before['plc']
            self.journal_before=self.io.json('xle',JOURNAL,'journal-before',self.deadline)
            require(self.journal_before['integrity']==[['ok']],'journal integrity')
        with self.stage('handshake'):
            # Existing helper only: no scanner ACK/Retry, local XLe, or internal writes.
            pre_reset=self.state('before-reset')
            self.initializing(pre_reset)
            require(pre_reset['run_identity']==self.before['run_identity'],
                    'run identity changed before the canonical reset')
            output=self.read('scada',HANDSHAKE,'canonical-handshake')
            helper=json.loads(output.splitlines()[-1])
            require(helper.get('status')=='PASS' and helper.get('journal_authority')==
                    'dedicated_sorter_xle_service','canonical handshake failed')
            first=self.state('handshake-first');time.sleep(.5)
            second=self.state('handshake-second')
            journal=self.io.json('xle',JOURNAL,'journal-after',self.deadline)
            handshake_check(self.before,first,second,self.journal_before,journal)
            require(time.monotonic()<min(self.deadline,self.initialization_deadline),
                    'synchronization proof exceeded startup/handshake deadline')
            self.ready_identity=first['run_identity'].copy()
            self.ready_sample=first
            self.io.save('readiness.json',{'status':'SYNCHRONIZED','identity':self.ready_identity,
                                         'token':self.token,'baseline_result':'NOT_EVALUATED'})
        with self.stage('verification'):
            after=typed.capture();self.io.save('typed-after.json',after)
            comparison=typed.compare(before,after);self.io.save('typed-comparison.json',comparison)
            require(comparison['status']=='PASS','typed restoration failed')
            require(after['plc']['run_identity']==self.ready_identity,'typed identity changed after readiness')
            report=pre.check(live=True,batch=True);self.io.save('deployment-preflight.json',report)
            a=properties(self.read('xle',XLE_STATUS,'xle-stable-first'));time.sleep(2)
            b=properties(self.read('xle',XLE_STATUS,'xle-stable-second'))
            self.io.save('xle-stability-observations.json',{'first':a,'second':b})
            self.io.save('xle-stability.json',xle_stability_check(a,b))
            r=self.io.json('plc',PLC_INSPECT,'final-installed-core',self.deadline)
            installed_guard(r,stopped=False,core=self.core)
            final_state=self.state('final-plc-state')
            final_journal=self.io.json('xle',JOURNAL,'journal-final',self.deadline)
            self.verify_ready_identity(final_state,final_journal)
            for vm,unit in (('xle','sorter-xle.service'),('drives','sorter-plant.service')):
                self.read(vm,'journalctl --no-pager -u '+unit+' -n 80 -o cat','final-journal-'+vm)
            # Bounded complete export, not a root-owned host capture directory.
            export_core_log(self.io,self.core_log,'core-log-export',self.deadline,'core.log')
            # Last remote gate: evidence export and final identity reads must
            # not age out the freshness observation before acceptance.
            fresh=self.io.json('scada',FRESHNESS,'ua-hmi-freshness',self.deadline)
            self.io.save('ua-hmi-freshness-check.json',freshness_check(fresh))
            self.results['final_state']={'status':'PASS','endpoint':'CORE_RUNNING_MASTER_OFF',
                                         'core':self.core}
        self.io.save('verification-PASS.json',{'status':'PASS','token':self.token})
        self.sender.send({'stage':'accept'})


def source_and_prestart(controller, log):
    require(controller.expected_uid==0,'UID1000 launch is blocked; reviewed root context required')
    require(subprocess.check_output(['git','-C',str(SELECTED),'rev-parse','HEAD'],text=True).strip()==COMMIT,
            'selected measurement source changed')
    require(not subprocess.check_output(['git','-C',str(SELECTED),'status','--porcelain'],text=True).strip(),
            'measurement source must be clean')
    pre.ROOT=SELECTED;pre.MANIFEST=SELECTED/'deploy/deployment_manifest.json'
    require(hashlib.sha256(pre.MANIFEST.read_bytes()).hexdigest()==MANIFEST_SHA,'manifest changed')
    manifest=json.loads(pre.MANIFEST.read_text())
    pre.check(live=False)
    c=controller
    with c.stage('prestart'):
        for vm in typed.VM_NAMES: pre._vm_running(vm)
        for vm in ('plc','drives','scada','xle','fw'):
            entries=[r for r in manifest['components'] if r['guest']==vm]
            output=c.read(vm,shlex.join(['sha256sum','--',*[r['canonical_destination'] for r in entries]]),
                          'hashes-'+vm)
            observed=dict((path,digest) for digest,path in re.findall(r'(?m)^([0-9a-f]{64})  (\S+)$',output))
            require(observed=={r['canonical_destination']:r['sha256'] for r in entries},
                    'selected deployed hashes differ: '+vm)
            units=sorted({u for r in entries for u in r['associated_service']})
            for unit in units:
                if unit=='sorter-xle.service':
                    evidence=c.io.json('xle',XLE_PRESTART,'xle-prestart-gate',c.deadline)
                    decision=xle_prestart_gate(evidence);c.io.save('xle-prestart-decision.json',decision)
                    require(decision['accepted'],'XLe prestart blocked: '+str(decision))
                else:
                    status=c.read(vm,pre.service_read_command(unit),'unit-'+vm+'-'+unit)
                    require(status.splitlines()[-1:] == ['active'],'required service inactive: '+unit)
        for vm in ('drives','scada','xle'):
            raw=c.read(vm,'ps -eo pid=,args=','prestart-processes-'+vm)
            rows=[m.group(2) for line in raw.splitlines()
                  if (m:=re.match(r'^\s*(\d+)\s+(.+)$',line))]
            require(not any(typed.TEMPORARY.search(r) for r in rows),'competing validation process')
            if vm=='drives':
                plant=[r for r in rows if typed.PLANT.match(r)]
                require(len(plant)==1 and '--block-' not in plant[0],'canonical plant absent/competing')
            if vm=='xle':
                require(len([r for r in rows if typed.XLE.match(r)])<=1 and
                        len([r for r in rows if typed.ASX.match(r)])==1,'competing XLe/ASX')
        r=c.io.json('plc',PLC_INSPECT,'installed-prestart',c.deadline)
        installed_guard(r,stopped=True)
        # Readability/execute/log-parent only. No file is created in this guard.
        c.read('plc',f'test -x {PLC_ROOT}/core/openplc && test -x /usr/bin/nc && '
               f'test -d {shlex.quote(str(Path(log).parent))} && '
               f'test -w {shlex.quote(str(Path(log).parent))} && test ! -e {shlex.quote(log)}',
               'launch-context')
        uid=c.read('plc','id -u','launch-uid').splitlines()[-1]
        require(uid==str(c.expected_uid),'canonical shell UID differs from reviewed launch UID')
        launch_context_guard(c.io.json('plc',PLC_LAUNCH_CONTEXT,'launch-context-privilege',c.deadline))
        # Fresh observations near arming, not a permanent exclusion guarantee.
        for vm in ('plc','drives','scada','xle','fw'):
            c.read(vm,'/usr/bin/who','prestart-console-who-'+vm)
        require(c.deadline-time.monotonic()>.01,'pre-start console observation deadline')
        process=subprocess.run(['ps','-eo','pid=,args='],capture_output=True,text=True,
                               check=True,timeout=max(.01,min(2,c.deadline-time.monotonic())))
        owners=[]
        for line in process.stdout.splitlines():
            row=line.strip().split(None,1)
            if len(row)!=2 or int(row[0])==os.getpid():continue
            args=row[1].split();exe=Path(args[0]).name
            if exe=='virsh' and 'console' in args and set(args)&{'plc','drives','scada','xle','fw'}:
                owners.append({'pid':int(row[0]),'role':'virsh-console'})
            elif re.fullmatch(r'python(?:3(?:\.\d+)?)?',exe) and any(
                    Path(a).name=='recover_accumulation_state.py' or
                    (Path(a).name=='m2_plc_startup_guard.py' and 'run' in args)
                    for a in args[1:5]):
                owners.append({'pid':int(row[0]),'role':'startup-or-recovery'})
        c.io.save('prestart-console-observation.json',{'observed_utc':utc(),'owners':owners,
                  'scope':'instantaneous observation; operator coordination still required'})
        require(not owners,'competing console/startup/recovery owner observed')
        # Additional scanner/VFD env, aliases, mapping, policy and privilege
        # review is a separate explicit operator prerequisite; see the procedure.


def validate_prerequisite_phases(reviewed,core_uid,execution_context):
    """Runtime socket proof is deliberately NOT a stopped-core prerequisite."""
    require(core_uid==0 and execution_context=='operator-root-consoles',
            'UID1000 cannot bind502; explicit operator-provided root consoles required')
    require(reviewed.get('schema_version')==2 and reviewed.get('core_uid')==0 and
            reviewed.get('execution_context')==execution_context and
            reviewed.get('console_shell_users')==OPERATOR_ROOT_SHELLS,
            'reviewed root execution context missing')
    require(all(reviewed.get(k)=='PASS' for k in ('drives_bindings_and_environment','plc_slave_mapping',
            'launch_privilege_and_nc_timeout','console_exclusivity','readable_evidence_export',
            'owned_rpc_prestart_support')),'pre-start access/support checks incomplete')
    require(reviewed.get('runtime_checks')=={'modbus_accepted_socket':'NOT RUN',
            'conditional_abort_recheck':'NOT RUN'},'startup-only proofs must remain NOT RUN before launch')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','run'])
    p.add_argument('--core-log',required=True)
    p.add_argument('--evidence',type=Path)
    p.add_argument('--approval-id')
    p.add_argument('--expected-core-uid',type=int)
    p.add_argument('--execution-context',choices=['operator-root-consoles'],
                   help='operator-authenticated root consoles on plc/drives/xle; no login/elevation')
    p.add_argument('--allow-initial-plant-fault-1',action='store_true')
    p.add_argument('--allow-initial-epoch-fault-1',action='store_true',
                   help='startup only: QW561=1 with epoch [0,0], QW564=0 and all safe guards')
    p.add_argument('--allow-launch-failure-child-cleanup',action='store_true',
                   help='separate explicit scope: held-pidfd TERM/KILL of this unreaped launch child on failure')
    p.add_argument('--reviewed-prerequisites',type=Path)
    args=p.parse_args()
    launch=startup_command(args.core_log,uid=0,cleanup_on_failure=args.allow_launch_failure_child_cleanup)
    if args.action=='plan':
        print(json.dumps({'status':'REVIEW ONLY','selected_source':COMMIT,'bounds_seconds':BOUNDS,
                          'overall_watchdog_seconds':WATCHDOG_SECONDS,'abort_seconds':30,
                          'timing_policy':timing_policy(),
                          'initialization_policy':{
                              'epoch_fault_1_enabled':args.allow_initial_epoch_fault_1,
                              'plant_fault_1_enabled':args.allow_initial_plant_fault_1,
                              'phase':'INITIALIZING','accepted_epoch':[0,0],'QW564':0,
                              'maximum_seconds':INITIALIZATION_SECONDS,
                              'synchronize_inside_handshake_seconds':BOUNDS['handshake'],
                              'source_commit':COMMIT,'source_file':'Sorter.st',
                              'source_sha256':PLC_HASHES['st_files/xle_sorter.st'],
                              'source_lines':[ [855,859],[949,979],[2112,2128] ]},
                          'bounds_analysis':{
                              'maximum_stage_sum_seconds':sum(v for k,v in BOUNDS.items() if k!='prestart'),
                              'overall_margin_seconds':WATCHDOG_SECONDS-sum(v for k,v in BOUNDS.items() if k!='prestart'),
                              'guest_handshake_timeout_and_kill_seconds':78,
                              'handshake_remaining_for_transport_and_proof_seconds':BOUNDS['handshake']-78},
                          'launch':launch,'modbus':'same owned established-socket proof, start_modbus(502) once',
                          'handshake':HANDSHAKE,
                          'abort':'ownership-checked established TCP connection; refuse if unproven',
                          'abort_log_export':'best effort <=5s inside existing 30s abort budget',
                          'launch_failure_cleanup':{'authorized':args.allow_launch_failure_child_cleanup,
                              'policy':owned.LAUNCH_CLEANUP_POLICY,
                              'observe_seconds':owned.LAUNCH_OBSERVE_SECONDS,
                              'cleanup_seconds':owned.LAUNCH_CLEANUP_SECONDS},
                          'state_reader':STATE,'installed_reader':PLC_INSPECT,'freshness_reader':FRESHNESS,
                          'journal_reader':JOURNAL,'xle_status_reader':XLE_STATUS,
                          'drives_root_reader':DRIVES_ROOT_READ,'plc_launch_context_reader':PLC_LAUNCH_CONTEXT,
                          'xle_prestart_reader':XLE_PRESTART,
                          'owned_core_helper_sha256':hashlib.sha256(Path(owned.__file__).read_bytes()).hexdigest(),
                          'serial_command_sha256':hashlib.sha256((ROOT/'tests/serial_command.py').read_bytes()).hexdigest(),
                          'deployment_preflight_sha256':hashlib.sha256((ROOT/'tests/deployment_preflight.py').read_bytes()).hexdigest(),
                          'preview_launch_token':'0'*32,
                          'execution_context':'operator-root-consoles',
                          'expected_core_uid':0,'console_shell_users':OPERATOR_ROOT_SHELLS,
                          'runtime_checks':{'modbus_accepted_socket':'NOT RUN','conditional_abort_recheck':'NOT RUN'},
                          'abort_ownership_requirement':'validated private and durable launch/core receipt; same established accepted socket owned by pinned core',
                          'manifest_sha256':MANIFEST_SHA,'installed_hashes_expected':PLC_HASHES,
                          'success_endpoint':'CORE_RUNNING_MASTER_OFF'},indent=2));return 0
    require(args.evidence and args.approval_id and args.expected_core_uid is not None and
            args.reviewed_prerequisites,'explicit operator-reviewed execution arguments required')
    reviewed=json.loads(args.reviewed_prerequisites.read_text())
    require(args.allow_launch_failure_child_cleanup and
            reviewed.get('launch_failure_cleanup')==owned.LAUNCH_CLEANUP_POLICY,
            'explicit reviewed direct-child failure-cleanup scope required before any guest operation')
    require(json.dumps(reviewed.get('timing_policy'),sort_keys=True)==json.dumps(timing_policy(),sort_keys=True),
            'explicit review of current stage/overall/abort timing policy required')
    validate_prerequisite_phases(reviewed,args.expected_core_uid,args.execution_context)
    require(reviewed.get('selected_source')==COMMIT and reviewed.get('status')=='PASS' and
            reviewed.get('success_endpoint')=='CORE_RUNNING_MASTER_OFF' and
            reviewed.get('approval_id')==args.approval_id,'reviewed prerequisites/endpoint missing')
    require(0 <= time.time()-datetime.fromisoformat(reviewed['reviewed_utc']).timestamp() <= 600 and
            reviewed.get('core_log')==args.core_log and reviewed.get('core_uid')==args.expected_core_uid and
            reviewed.get('host_guard_sha256')==hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and
            reviewed.get('serial_command_sha256')==hashlib.sha256((ROOT/'tests/serial_command.py').read_bytes()).hexdigest() and
            reviewed.get('deployment_preflight_sha256')==hashlib.sha256((ROOT/'tests/deployment_preflight.py').read_bytes()).hexdigest() and
            reviewed.get('owned_core_helper_sha256')==hashlib.sha256(Path(owned.__file__).read_bytes()).hexdigest(),
            'fresh exact prerequisites missing; see MILESTONE2_PLC_STARTUP.md')
    directory=args.evidence.resolve()
    require(not directory.is_relative_to(ROOT) and not directory.is_relative_to(SELECTED) and
            not directory.exists(),'new evidence path outside source worktrees required')
    directory.mkdir()
    token=os.urandom(16).hex()
    claim_host_approval(args.approval_id,token,'m2-no-package-startup')
    io=LiveIO(directory,shell_users=OPERATOR_ROOT_SHELLS);io.save('reviewed-prerequisites.json',reviewed)
    receiver,sender=multiprocessing.get_context('spawn').Pipe(duplex=False)
    c=Controller(io,sender,token,plant_initial=args.allow_initial_plant_fault_1,
                 epoch_initial=args.allow_initial_epoch_fault_1)
    c.core_log=args.core_log;c.expected_uid=args.expected_core_uid
    c.cleanup_on_failure=args.allow_launch_failure_child_cleanup
    io.save('host-source-identity.json',{'selected_measurement_source':COMMIT,
        'manifest_sha256':MANIFEST_SHA,'host_only_undeployed_tools':{
            name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('tests/m2_plc_startup_guard.py','tests/serial_command.py','tests/deployment_preflight.py',
             'tests/accumulation_state_snapshot.py','tests/m2_owned_core.py')},
        'execution_context':'operator-root-consoles','console_shell_users':OPERATOR_ROOT_SHELLS,
        'capture_mechanics':'PASS per FINAL_DIAGNOSTIC_SMOKE_VERIFICATION.md; not rerun'})
    process=None
    def interrupted(signum,frame): raise RuntimeError(f'controller signal {signum}')
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP): signal.signal(sig,interrupted)
    try:
        source_and_prestart(c,args.core_log)
        process=multiprocessing.get_context('spawn').Process(target=watchdog,args=(receiver,str(directory),token),
                                                           kwargs={'core_log':args.core_log,'shell_users':OPERATOR_ROOT_SHELLS})
        process.start();receiver.close()
        end=time.monotonic()+3
        while True:
            require(process.is_alive() and time.monotonic()<end,'watchdog not ready')
            try: ready=json.loads((directory/'watchdog.json').read_text())
            except (OSError,ValueError): ready={}
            if ready.get('token')==token and ready.get('status')=='READY':break
            time.sleep(.02)
        c.start(args.core_log,args.expected_core_uid)
        c.complete()
    except BaseException as exc:
        c.request_abort(exc)
    finally:
        sender.close()
        if process:
            process.join(timeout=32)
            if process.is_alive():
                c.results['watchdog_error']='watchdog did not finish; operator intervention required'
            try:
                wd=json.loads((directory/'watchdog.json').read_text())
                require(wd.get('token')==token and wd.get('terminal') is True,
                        'missing/mismatched watchdog terminal evidence')
                c.results['abort']={'status':wd['abort'],'watchdog':wd}
                if wd['abort']!='NOT RUN':
                    final_status=wd.get('final_status','INCOMPLETE')
                    c.results['final_state']={'status':final_status,'endpoint':'CORE_STOPPED' if
                                              final_status=='PASS' else 'UNKNOWN',
                                              'plc_state':'UNAVAILABLE_AFTER_CORE_STOP' if
                                                  final_status=='PASS' else 'UNVERIFIED_ABORT_INCOMPLETE',
                                              'typed_postflight':'NOT RUN'}
            except Exception as exc:
                c.results['watchdog_error']=str(exc)
        c.results['terminal']=True
        c.results['status']='PASS' if ('primary_error' not in c.results and
            c.results['verification']['status']=='PASS' and
            c.results.get('abort',{}).get('watchdog',{}).get('status')=='ACCEPTED' and
            not c.results.get('abort',{}).get('watchdog',{}).get('evidence_errors') and
            'watchdog_error' not in c.results and process is not None and process.exitcode==0) else 'INCOMPLETE'
        io.save('results.json',c.results)
        print(json.dumps(c.results),flush=True)
    return 0 if c.results['status']=='PASS' else 2


if __name__=='__main__':
    raise SystemExit(main())

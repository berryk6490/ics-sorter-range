"""Pinned guest entry for the existing collector and bounded serial retrieval.

build/plan are host-only and inert. All other commands require separate explicit
operator approval. No provisioning, authentication, elevation or scenario control.
"""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import shlex
import socket
import stat
import subprocess
import sys
import time
import zipfile

import baseline_capture as capture

MAX_METADATA=2*1024*1024
CHUNK=65536
MODULES=('baseline_capture.py','pcap_modbus_summary.py','m2_guest_capture.py')
REVIEW_KEYS=('route_interface','tools_proc_visibility','execution_context_destination',
             'storage_clock_bounds','readable_export','capture_attempt_authorized')
# Fixed reviewed bootstrap: validate before importing ANY archive code, retain
# its inode through /proc/self/fd, and refuse writable/untrusted executable bytes.
BOOTSTRAP="""import hashlib,os,runpy,stat,sys
path,expected=sys.argv[1:3]
fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
a=os.fstat(fd)
if not stat.S_ISREG(a.st_mode) or a.st_uid not in (0,os.geteuid()) or a.st_mode&0o022 or a.st_size>1048576:raise ValueError('unsafe runtime archive identity/mode/size')
data=os.read(fd,a.st_size+1);z=os.fstat(fd)
if (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)!=(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns) or len(data)!=a.st_size or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('runtime archive pin differs before execution')
sys.argv=['/proc/self/fd/'+str(fd),*sys.argv[3:]]
runpy.run_path(sys.argv[0],run_name='__main__')
"""


def runtime_argv(path,pin,args,*,interpreter='/usr/bin/python3'):
    return [interpreter,'-I','-B','-c',BOOTSTRAP,str(path),pin,*args]


def digest(data):return hashlib.sha256(data).hexdigest()


def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON field')
        result[key]=value
    return result


def decode(data):return json.loads(data,object_pairs_hook=unique_object)


def build(path,*,sources=None):
    """Deterministic local archive; does not copy anything to a guest."""
    sources=sources or {name:Path(__file__).with_name(name).read_bytes() for name in MODULES}
    if set(sources)!=set(MODULES):raise ValueError('exact runtime modules required')
    members={**sources,'__main__.py':b'import m2_guest_capture\nraise SystemExit(m2_guest_capture.main())\n',
             'module-pins.json':encode({name:digest(data) for name,data in sources.items()})}
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,data in sorted(members.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.external_attr=0o100644<<16
            archive.writestr(info,data)
    return {'archive_sha256':capture.sha(Path(path)),'module_sha256':decode(members['module-pins.json']),
            'size':Path(path).stat().st_size,'guest_provisioning':'NOT EXECUTED'}


def archive_identity(expected):
    path=Path(sys.argv[0]).resolve()
    if not re.fullmatch('[0-9a-f]{64}',expected) or capture.sha(path)!=expected:
        raise ValueError('runtime archive pin differs')
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist())!=set(MODULES)|{'__main__.py','module-pins.json'} or len(archive.namelist())!=5:
            raise ValueError('ambiguous runtime archive')
        pins=decode(archive.read('module-pins.json'))
        if set(pins)!=set(MODULES) or any(digest(archive.read(name))!=pins[name] for name in MODULES):
            raise ValueError('runtime module pins differ')
    return {'archive_sha256':expected,'module_sha256':pins,'path':str(path)}


def plan(config,names,location,directory,scenario,bundle_path,bundle_sha256,uid,seconds=10,
         packets=5000,max_bytes=8388608,grace=3,*,host_identity=None,module_pins=None):
    capture.limits(seconds,packets,max_bytes,grace)
    capture.validate_config(config)
    if location not in ('xle','scada','drives') or type(uid) is not int or uid!=(1000 if location=='scada' else 0):
        raise ValueError('existing root xle/drives or kevin scada context required')
    if not names or len(set(names))!=len(names) or not capture.NAME.fullmatch(scenario):
        raise ValueError('unique points and exact scenario required')
    if any(config['points'][name]['location']!=location or not config['points'][name]['verified_in_handoff'] for name in names):
        raise ValueError('guest location or verified configuration missing; do not enable drives silently')
    if (not Path(directory).is_absolute() or '..' in Path(directory).parts or
        not Path(bundle_path).is_absolute() or not re.fullmatch('[0-9a-f]{64}',bundle_sha256)):
        raise ValueError('absolute reviewed paths and archive SHA required')
    return {'schema_version':1,'kind':'M2_GUEST_CAPTURE','status':'NOT REVIEWED','location':location,
            'expected_hostname':location,'uid':uid,'user':'kevin' if uid==1000 else 'root',
            'configuration':config,'configuration_sha256':digest(encode(config)),
            'selected_points':names,'output':str(directory),'scenario_id':scenario,
            'bundle_path':str(bundle_path),'bundle_sha256':bundle_sha256,
            'runtime_module_sha256':module_pins if module_pins is not None else {
                name:capture.sha(Path(__file__).with_name(name)) for name in MODULES},
            'source_identity':host_identity if host_identity is not None else capture.identity(config),
            'seconds':seconds,'packets':packets,'max_bytes':max_bytes,'grace':grace,
            'clock_note':'UNMEASURED; independently review clock brackets before correlation',
            'checks':{key:'NOT REVIEWED' for key in REVIEW_KEYS},
            'capture_open':'UNPROVEN; only a separately approved capture attempt can test it'}


def validate_contract(contract):
    if (contract.get('schema_version')!=1 or contract.get('kind')!='M2_GUEST_CAPTURE' or
        contract.get('status')!='PASS' or set(contract.get('checks',{}))!=set(REVIEW_KEYS) or
        any(contract['checks'][key]!='PASS' for key in REVIEW_KEYS)):
        raise ValueError('exact independently reviewed capture-only contract required')
    # Reapply every structural/configuration/context bound; no host Git on guests.
    check=plan(contract['configuration'],contract['selected_points'],contract['location'],contract['output'],
               contract['scenario_id'],contract['bundle_path'],contract['bundle_sha256'],contract['uid'],
               contract['seconds'],contract['packets'],contract['max_bytes'],contract['grace'],
               host_identity=contract['source_identity'],module_pins=contract['runtime_module_sha256'])
    for key in ('configuration_sha256','expected_hostname','user'):
        if contract.get(key)!=check[key]:raise ValueError('capture contract differs: '+key)
    source=contract['source_identity']
    if (set(contract['runtime_module_sha256'])!=set(MODULES) or
        any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in contract['runtime_module_sha256'].values())):
        raise ValueError('exact runtime module pins required')
    if (source.get('selected_source_commit')!=contract['configuration']['selected_source_commit'] or
        source.get('manifest_sha256')!=contract['configuration']['selected_manifest_sha256']):
        raise ValueError('selected measurement identity differs')


def load_contract(path,expected,bundle_sha256):
    raw=Path(path).read_bytes()
    if len(raw)>MAX_METADATA or digest(raw)!=expected:raise ValueError('capture contract pin/size differs')
    contract=decode(raw);validate_contract(contract)
    runtime=archive_identity(bundle_sha256)
    if (contract['bundle_sha256']!=bundle_sha256 or contract['runtime_module_sha256']!=runtime['module_sha256'] or
        str(Path(path).resolve())!=contract['guest_contract_path'] or
        str(Path(contract['bundle_path']).resolve())!=runtime['path'] or
        socket.gethostname().split('.')[0]!=contract['expected_hostname'] or
        os.getuid()!=contract['uid'] or os.geteuid()!=contract['uid'] or
        pwd.getpwuid(os.geteuid()).pw_name!=contract['user']):
        raise ValueError('actual guest/execution identity differs; no elevation or drop-user change')
    return contract,runtime


def guest_collect(contract,runtime,contract_sha256,*,launch=None):
    parent=directory_fd(Path(contract['output']).parent)
    try:
        st=os.fstat(parent)
        if st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)&0o022:
            raise ValueError('capture parent must belong to invoking identity and not be group/other writable')
        try:os.stat(Path(contract['output']).name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise ValueError('capture output already exists, including symlinks')
        destination={'parent_dev':st.st_dev,'parent_inode':st.st_ino,'parent_uid':st.st_uid,
                     'parent_mode':stat.S_IMODE(st.st_mode),'invoking_uid':os.geteuid(),
                     'drop_user':'same invoking identity; no inference about another user rights'}
    finally:os.close(parent)
    identity={'kind':'GUEST_PINNED_INPUTS','location':contract['location'],'runtime':runtime,
              'contract_sha256':contract_sha256,'host_source_identity':contract['source_identity'],
              'configuration_sha256':contract['configuration_sha256'],'uid':os.getuid(),'euid':os.geteuid(),
              'user':pwd.getpwuid(os.geteuid()).pw_name,'hostname':socket.gethostname(),
              'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'destination':destination,
              'capture_open':'NOT PROVEN until tcpdump actually opens the approved interface'}
    # The drop identity is the invoking identity, including root. No root0700
    # directory is handed to a dropped kevin process; export keeps originals.
    return capture.collect(contract['configuration'],contract['selected_points'],contract['output'],
        contract['scenario_id'],contract['seconds'],contract['packets'],contract['max_bytes'],contract['grace'],
        contract['clock_note'],guest_execution={'location':contract['location'],'identity':identity,
        'runtime':runtime,'entry':runtime_argv(runtime['path'],runtime['archive_sha256'],[],interpreter=sys.executable),
        'launch':launch})


def open_binding(contract,pin,boot):
    return dict(contract_sha256=pin,archive_sha256=contract['bundle_sha256'],
        module_sha256=contract['runtime_module_sha256'],configuration_sha256=contract['configuration_sha256'],
        source_identity_sha256=digest(encode(contract['source_identity'])),scenario_id=contract['scenario_id'],
        selected_points=contract['selected_points'],location=contract['location'],uid=contract['uid'],boot_id=boot)


def launch_request(token):
    if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):raise ValueError('exact launch token required')
    return {'launch_token':token}


def read_capture_json(contract,name):
    directory=directory_fd(contract['output'])
    try:
        fd,_=artifact_fd(directory,name,contract)
        with os.fdopen(fd,'rb') as stream:return decode(stream.read(MAX_METADATA+1))
    finally:os.close(directory)


def checked_open(contract,pin,checkpoint,token):
    launch_request(token)
    if (type(checkpoint.get('schema_version')) is not int or checkpoint['schema_version']!=1 or checkpoint.get('status')!='OPEN' or
        checkpoint.get('collection_token')!=token or checkpoint.get('scenario_id')!=contract['scenario_id']):
        raise ValueError('capture-open token/status differs')
    binding=checkpoint['binding'];boot=binding.get('boot_id')
    if (not isinstance(boot,str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot) or
        encode(binding)!=encode(open_binding(contract,pin,boot))):raise ValueError('capture-open pinned binding differs')
    fields={'pid','start_ticks','uid','boot_id','state','ppid','pgid','sid'}
    def identity(row):
        if (not isinstance(row,dict) or not fields<=row.keys() or
            any(type(row[k]) is not int or row[k]<0 for k in fields-{'boot_id','state'}) or
            not row['pid'] or not row['start_ticks'] or row['uid']!=contract['uid'] or row['boot_id']!=boot or
            not isinstance(row['state'],str) or len(row['state'])!=1):raise ValueError('capture-open incarnation/UID/boot malformed')
    identity(checkpoint['controller_identity']);identity(checkpoint['supervisor_identity'])
    if checkpoint['controller_identity']['sid']!=checkpoint['controller_identity']['pid']:
        raise ValueError('controller not detached into its own session')
    observed=checkpoint.get('observed_monotonic_ns')
    if type(observed) is not int or observed<=0 or set(checkpoint['points'])!=set(contract['selected_points']):
        raise ValueError('capture-open observation/point set differs')
    for name,row in checkpoint['points'].items():
        point=contract['configuration']['points'][name]
        expected=capture.bounded_argv(point,Path(contract['output'])/(name+'.pcap'),contract['packets'],contract['seconds'],contract['grace'])
        proof=row['proof'];identity(row['wrapper_identity']);identity(proof['capture_identity'])
        start=row['started_monotonic_ns'];end=row['deadline_monotonic_ns']
        packet=proof.get('packet_row')
        if (not isinstance(packet,list) or len(packet)!=9 or not all(type(v) is str for v in packet) or
            packet[2]!='3' or int(packet[3],16)!=3 or packet[5]!='1' or
            int(packet[4])!=proof.get('interface_index') or int(packet[7])!=contract['uid'] or int(packet[8])!=proof.get('socket_inode')):
            raise ValueError('capture-open packet socket row differs')
        if (row['point']!=point or row['argv']!=expected or type(start) is not int or type(end) is not int or
            not 0<start<=observed<end or end!=start+int(contract['seconds']*1e9) or
            proof.get('kind')!='LINUX_PACKET_SOCKET' or type(proof.get('socket_inode')) is not int or proof['socket_inode']<=0 or
            type(proof.get('interface_index')) is not int or proof['interface_index']<=0 or
            not isinstance(proof.get('executable_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',proof['executable_sha256']) or
            not isinstance(row.get('listening_line'),str) or not row['listening_line'].startswith('tcpdump: listening on '+point['interface']+', ')):
            raise ValueError('capture-open point/socket/deadline differs')
    return checkpoint


def guest_launch(contract,runtime,pin,request):
    """One bounded launch of the complete existing controller, never just its supervisor."""
    if request!=launch_request(request.get('launch_token')):raise ValueError('launch request differs')
    parent=directory_fd(Path(contract['output']).parent)
    try:
        st=os.fstat(parent)
        if st.st_uid!=os.geteuid() or st.st_mode&0o022:raise ValueError('unsafe invoking-owned capture parent')
        if os.path.lexists(contract['output']):raise ValueError('capture output already exists')
        # One-use reservation survives every failure. It confers no signal authority.
        os.mkdir(Path(contract['output']).name+'.launch',mode=0o700,dir_fd=parent)
    finally:os.close(parent)
    launch={**request,'binding':open_binding(contract,pin,capture.BOOT_ID),
            'launch_monotonic_ns':time.monotonic_ns(),'stdio':'DEVNULL; close_fds; new session'}
    record={'status':'INCOMPLETE','launch':launch,'errors':{'primary':None,'cleanup':[]}}
    process=None;held=None;birth=None;owned_child=False
    try:
        argv=runtime_argv(runtime['path'],runtime['archive_sha256'],['guest-detached','--contract',contract['guest_contract_path'],
            '--contract-sha256',pin,'--bundle-sha256',contract['bundle_sha256'],'--request',base64.b64encode(encode(launch)).decode()],
            interpreter=sys.executable)
        process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 start_new_session=True,close_fds=True)
        birth=capture.process_identity(process.pid);held=os.pidfd_open(process.pid)
        if (birth is None or birth['pid']!=process.pid or birth['ppid']!=os.getpid() or
            birth['uid']!=contract['uid'] or birth['boot_id']!=capture.BOOT_ID or
            not capture.same_process(birth,capture.process_identity(process.pid))):raise ValueError('launched child incarnation unproven')
        owned_child=True
        until=time.monotonic()+5
        while time.monotonic()<until:
            if process.poll() is not None:raise ValueError('early controller terminal before capture-open')
            try:checkpoint=read_capture_json(contract,'capture-open.json')
            except FileNotFoundError:time.sleep(.02);continue
            checked_open(contract,pin,checkpoint,request['launch_token'])
            if (not capture.same_process(birth,checkpoint['controller_identity']) or
                any(checkpoint['controller_identity'][key]!=birth[key] for key in ('ppid','pgid','sid')) or
                checkpoint['binding']['boot_id']!=capture.BOOT_ID):raise ValueError('open checkpoint is not launched controller')
            current=read_capture_json(contract,'collection.json')
            if current.get('terminal'):raise ValueError('early terminal at capture-open')
            if time.monotonic_ns()>=min(v['deadline_monotonic_ns'] for v in checkpoint['points'].values()):
                raise TimeoutError('capture-open deadline exhausted')
            record.update(status='OPEN',checkpoint=checkpoint,checkpoint_sha256=digest(encode(checkpoint)))
            break
        else:raise TimeoutError('five-second capture-open bound exhausted')
    except Exception as exc:record['errors']['primary']=type(exc).__name__+': '+str(exc)
    finally:
        if process is not None and record['status']!='OPEN':
            # Only this live Popen/direct-child + held pidfd is signal authority.
            # The original controller/supervisor retain all descendant ownership.
            if held is not None and owned_child:
                try:
                    signal.pidfd_send_signal(held,signal.SIGTERM)
                    process.wait(timeout=contract['grace']+capture.REAP_SECONDS+3)
                except ProcessLookupError:pass
                except Exception as exc:
                    record['errors']['cleanup'].append(type(exc).__name__+': '+str(exc))
                    if capture.pidfd_alive(held):signal.pidfd_send_signal(held,signal.SIGKILL)
            if process.poll() is None:record['errors']['cleanup'].append('controller exit unresolved; independent supervisor bound still applies')
        if held is not None:os.close(held)
    return record


def detached_controller(contract,runtime,pin,launch):
    if (launch.get('binding')!=open_binding(contract,pin,capture.BOOT_ID) or
        launch.get('stdio')!='DEVNULL; close_fds; new session' or
        type(launch.get('launch_monotonic_ns')) is not int or
        not 0<=time.monotonic_ns()-launch['launch_monotonic_ns']<=int(5e9) or
        os.getsid(0)!=os.getpid() or any(os.readlink('/proc/self/fd/'+str(n))!='/dev/null' for n in (0,1,2))):
        raise ValueError('detached controller stdio/session/start binding differs')
    launch_request(launch.get('launch_token'))
    return guest_collect(contract,runtime,pin,launch=launch)


def guest_join(contract,pin,request):
    """Read-only original terminal join. Never adopt/signal/relaunch a stored PID."""
    checkpoint=read_capture_json(contract,'capture-open.json')
    if request!={'launch_token':checkpoint.get('collection_token'),'checkpoint_sha256':digest(encode(checkpoint))}:
        raise ValueError('terminal join token/checkpoint differs')
    checked_open(contract,pin,checkpoint,request['launch_token'])
    if checkpoint['binding']['boot_id']!=capture.BOOT_ID:raise ValueError('stale-boot join')
    until=max(v['deadline_monotonic_ns'] for v in checkpoint['points'].values())/1e9+2*contract['grace']+capture.REAP_SECONDS+5
    while time.monotonic()<until:
        result=read_capture_json(contract,'collection.json')
        if result.get('terminal') and (Path(contract['output'])/'sealing.json').exists():
            original=checkpoint['controller_identity'];observed=capture.process_identity(original['pid'])
            if capture.same_process(original,observed) and observed['state']!='Z':time.sleep(.02);continue
            inv=inventory(contract,pin)
            capture.verify(contract['output'],guest_postflight=inv['postflight'])
            if time.monotonic()>=until:raise TimeoutError('original terminal join deadline exhausted')
            return {'status':'COMPLETE','collection':result,'checkpoint_sha256':request['checkpoint_sha256'],
                    'postflight':inv['postflight'],'controller_postflight':{'expected':original,'observed':observed},
                    'terminal_manifest_sha256':capture.sha(Path(contract['output'])/'SHA256SUMS'),
                    'joined_monotonic_ns':time.monotonic_ns()}
        time.sleep(.02)
    raise TimeoutError('original terminal join deadline exhausted; no renewal')


def launch_spec(contract,contract_path,contract_sha256):
    validate_contract(contract)
    argv=runtime_argv(contract['bundle_path'],contract['bundle_sha256'],['guest-run','--contract',str(contract_path),
          '--contract-sha256',contract_sha256,'--bundle-sha256',contract['bundle_sha256']])
    return {'guest':contract['location'],'shell_user':'kevin' if contract['uid']==1000 else 'root',
            'command':shlex.join(argv),'timeout':640,'total_seconds':660,
            'scope':'capture-only; no provisioning/scenario/recovery/privilege action'}


def allowed_names(contract):
    return {'configuration.json','identity.json','collection.json','supervision.json','supervision-plan.json',
            'supervisor.stdout','supervisor.stderr','failure.json','SHA256SUMS','sealing.json',
            'detached-start.json','capture-open.json'}|{
        name+'.'+suffix for name in contract['selected_points'] for suffix in ('pcap','stdout','stderr')}


def directory_fd(path):
    """Walk every component without following a symlink, without changing it."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('exact absolute directory required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        return fd
    except BaseException:os.close(fd);raise


def file_pin(st):
    if not stat.S_ISREG(st.st_mode):raise ValueError('only regular capture artifacts may be exported')
    return {key:getattr(st,'st_'+key) for key in ('dev','ino','uid','gid','mode','size','mtime_ns','ctime_ns')}


def artifact_fd(directory,name,contract):
    if name not in allowed_names(contract):raise ValueError('not a contracted capture artifact')
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
    try:
        pin=file_pin(os.fstat(fd));limit=contract['max_bytes'] if name.endswith('.pcap') else MAX_METADATA
        if pin['size']>limit:raise ValueError('artifact exceeds export limit')
        return fd,pin
    except BaseException:os.close(fd);raise


def inventory(contract,contract_sha256):
    directory=directory_fd(contract['output']);result={'schema_version':1,'contract_sha256':contract_sha256,
        'location':contract['location'],'scenario_id':contract['scenario_id'],'files':{},'errors':[],
        'observed_utc':datetime.now(timezone.utc).isoformat(),'observed_monotonic_ns':time.monotonic_ns()}
    try:
        st=os.fstat(directory);result['directory_pin']={'dev':st.st_dev,'ino':st.st_ino,'uid':st.st_uid}
        names=set(os.listdir(directory));result['unexpected_names']=sorted(names-allowed_names(contract))
        for name in sorted(names&allowed_names(contract)):
            fd,pin=artifact_fd(directory,name,contract)
            with os.fdopen(fd,'rb') as stream:
                h=hashlib.sha256()
                for data in iter(lambda:stream.read(CHUNK),b''):h.update(data)
                if file_pin(os.fstat(stream.fileno()))!=pin:raise ValueError('artifact changed during inventory')
            result['files'][name]={'pin':pin,'sha256':h.hexdigest()}
        # Exact same canonical incarnation reader, in the guest namespace.
        report_fd,_=artifact_fd(directory,'supervision.json',contract)
        with os.fdopen(report_fd,'rb') as stream:report=decode(stream.read(MAX_METADATA+1))
        identities={capture.process_key(row):row for row in [report['supervisor_identity'],*report.get('processes',[])]}
        result['postflight']={'location':contract['location'],'collection_token':report['collection_token'],
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'errors':[],
            'observed_utc':result['observed_utc'],'observed_monotonic_ns':result['observed_monotonic_ns'],'checks':[]}
        for expected in identities.values():
            result['postflight']['checks'].append({'expected':expected,'observed':capture.process_identity(expected['pid'])})
    except Exception as exc:result['errors'].append(type(exc).__name__+': '+str(exc))
    finally:os.close(directory)
    return result


def chunk(contract,request):
    if set(request)!={'name','pin','directory_pin','offset','length'}:raise ValueError('chunk request shape differs')
    if (type(request['offset']) is not int or type(request['length']) is not int or
        request['offset']<0 or not 1<=request['length']<=CHUNK):raise ValueError('invalid chunk bounds')
    if request['name'] not in allowed_names(contract):raise ValueError('not a contracted capture artifact')
    directory=directory_fd(contract['output'])
    try:
        st=os.fstat(directory)
        if request['directory_pin']!={'dev':st.st_dev,'ino':st.st_ino,'uid':st.st_uid}:raise ValueError('directory incarnation changed')
        fd,pin=artifact_fd(directory,request['name'],contract)
        with os.fdopen(fd,'rb') as stream:
            if pin!=request['pin'] or request['offset']+request['length']>pin['size']:
                raise ValueError('file incarnation/size or requested range differs')
            stream.seek(request['offset']);data=stream.read(request['length'])
            if len(data)!=request['length'] or file_pin(os.fstat(stream.fileno()))!=pin:
                raise ValueError('short or changing artifact chunk')
        return {**request,'data_base64':base64.b64encode(data).decode(),'chunk_sha256':digest(data)}
    finally:os.close(directory)


def remote_command(contract,contract_path,contract_sha256,action,request=None):
    argv=runtime_argv(contract['bundle_path'],contract['bundle_sha256'],[action,'--contract',str(contract_path),
          '--contract-sha256',contract_sha256,'--bundle-sha256',contract['bundle_sha256']])
    if request is not None:argv+=['--request',base64.b64encode(encode(request)).decode()]
    return shlex.join(argv)


def serial_call(contract,command,label,deadline,traces,*,timeout=20,read=None):
    if read is None:
        from deployment_preflight import guest_read
        read=guest_read
    def trace(row):traces.append({**row,'recorded_utc':datetime.now(timezone.utc).isoformat(),
                                        'recorded_monotonic':time.monotonic()})
    output=read(contract['location'],command,label,deadline=deadline,trace=trace,timeout=timeout,
                shell_user='kevin' if contract['uid']==1000 else 'root')
    return decode(output.splitlines()[-1])


def check_receipt_pair(contract,rows,*,command,label,result,timeout,seen=None,
                       interpreter=None,allowances=None):
    """The same canonical intended/read + separate-liveness validator."""
    if not isinstance(rows,list) or len(rows)!=2:raise ValueError('canonical receipt pair missing')
    seen=set() if seen is None else seen
    for index,row in enumerate(rows):
        argv=row.get('argv')
        if not isinstance(argv,list) or not all(type(arg) is str for arg in argv):
            raise ValueError('canonical argv must contain strings only')
        if (row.get('vm')!=contract['location'] or row.get('probe') is not bool(index) or
            row.get('label')!=('liveness after '+label if index else label) or
            type(row.get('exit_status')) is not int or row['exit_status']!=0 or len(argv)<11 or
            argv[1]!=str(Path(__file__).with_name('serial_command.py')) or argv[2]!=contract['location'] or
            (interpreter is not None and argv[0]!=interpreter)):
            raise ValueError('canonical transport execution binding differs')
        nonce=argv[argv.index('--nonce')+1]
        if not re.fullmatch('[0-9a-f]{24,64}',nonce) or 'nonce:'+nonce in seen:
            raise ValueError('canonical nonce invalid/reused')
        seen.add('nonce:'+nonce)
        if index==0:intended=command;nominal=str(timeout)
        else:
            intended=argv[3];nominal='20'
            marker=re.fullmatch(r"printf '(PREFLIGHT_ALIVE_[0-9a-f]{32})\\n'",intended)
            if marker is None or 'alive:'+marker[1] in seen:raise ValueError('separate fresh liveness command missing/reused')
            seen.add('alive:'+marker[1])
        expected=[argv[0],str(Path(__file__).with_name('serial_command.py')),contract['location'],intended,
                  '--timeout',nominal,'--nonce',nonce,'--json']
        if contract['uid']==0:expected+=['--shell-user','root']
        total=argv[argv.index('--total-timeout')+1];allowance=float(total)
        bound=(timeout+10 if index==0 else 30) if allowances is None else allowances[index]
        if not math.isfinite(allowance) or not 0<allowance<=bound+1e-6:
            raise ValueError('canonical timeout allowance invalid')
        expected+=['--total-timeout',total]
        if argv!=expected:raise ValueError('canonical intended argv differs')
        lines=row['stdout'].strip().splitlines()
        if len(lines)!=1:raise ValueError('ambiguous canonical receipt payload')
        data=decode(lines[0])
        if (data.get('nonce')!=nonce or data.get('completion_marker') is not True or data.get('prompt') is not True or
            type(data.get('command_rc')) is not int or data['command_rc']!=0 or data.get('error')):
            raise ValueError('canonical completion/prompt/nonce/rc proof missing')
        if contract['uid']==0:
            if data.get('shell_user')!='root' or type(data.get('context_uid')) is not int or data['context_uid']!=0:
                raise ValueError('canonical root identity missing')
        elif (data.get('shell_user','kevin')!='kevin' or
              ('context_uid' in data and (type(data['context_uid']) is not int or data['context_uid']!=1000))):
            raise ValueError('canonical kevin context differs')
        if index==0:
            if encode(decode(data['output'].splitlines()[-1]))!=encode(result):raise ValueError('result differs from intended receipt')
        elif marker[1] not in data['output'].splitlines():raise ValueError('separate fresh liveness missing')
    return seen


def check_collection_transport(contract,contract_sha256,record):
    """Bind retained canonical receipts; this never grants operator authority."""
    if record.get('mode')=='DETACHED_JOIN':return check_detached_transport(contract,contract_sha256,record)
    if record.get('mode') is not None:raise ValueError('unrecognized collection transport mode')
    spec=launch_spec(contract,contract['guest_contract_path'],contract_sha256)
    if record.get('spec')!=spec:raise ValueError('canonical collection receipt pair missing')
    result=record.get('result',{})
    if (result.get('status')!='COMPLETE' or result.get('execution_location')!=contract['location'] or
        result.get('scenario_id')!=contract['scenario_id'] or result.get('selected_points')!=contract['selected_points'] or
        not isinstance(result.get('collection_token'),str) or not re.fullmatch('[0-9a-f]{32}',result['collection_token'])):
        raise ValueError('collection result contract/guest binding differs')
    return check_receipt_pair(contract,record.get('traces'),command=spec['command'],label='m2-guest-capture',
                              result=result,timeout=640)


def remote_collect(contract_path,contract_sha256,evidence,*,read=None):
    raw=Path(contract_path).read_bytes()
    if digest(raw)!=contract_sha256:raise ValueError('host reviewed contract pin differs')
    contract=decode(raw)
    # Host and guest contract destinations may differ; use the explicitly
    # reviewed guest_contract_path, never provision a file as a side effect.
    spec=launch_spec(contract,contract['guest_contract_path'],contract_sha256)
    evidence=Path(evidence);evidence.mkdir(exist_ok=False,mode=0o700);traces=[]
    record={'status':'INCOMPLETE','scope':'CAPTURE_ONLY','spec':spec,'contract_sha256':contract_sha256,
            'operator_authority':'NOT_EVALUATED','errors':[],'artifact_errors':[]}
    try:
        record['result']=serial_call(contract,spec['command'],'m2-guest-capture',time.monotonic()+660,traces,timeout=640,read=read)
        if record['result'].get('status')!='COMPLETE':raise ValueError('guest collector incomplete')
        check_collection_transport(contract,contract_sha256,{**record,'traces':traces})
        record['status']='COMPLETE'
    except Exception as exc:record['errors'].append(type(exc).__name__+': '+str(exc))
    finally:
        record['traces']=traces
        try:capture.save(evidence/'capture-transport.json',record)
        except Exception as exc:
            record['status']='INCOMPLETE';record['artifact_errors'].append(type(exc).__name__+': '+str(exc))
    return record


def detached_spec(contract,pin,action,request):
    return {'guest':contract['location'],'shell_user':'kevin' if contract['uid']==1000 else 'root',
            'command':remote_command(contract,contract['guest_contract_path'],pin,action,request),
            'timeout':20 if action=='guest-launch' else 640,
            'total_seconds':30 if action=='guest-launch' else 660}


def check_detached_call(contract,record,*,spec,label,seen=None):
    start=finite_time(record.get('started_monotonic'));end=finite_time(record.get('ended_monotonic'))
    if (record.get('spec')!=spec or not start<=end<start+spec['total_seconds'] or
        not isinstance(record.get('transport_interpreter'),str) or
        not isinstance(record.get('host_boot_id'),str) or not re.fullmatch(
            r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',record['host_boot_id'])):
        raise ValueError('detached transport scope/time binding differs')
    rows=record.get('traces',[])
    if len(rows)!=2:raise ValueError('detached canonical receipt pair missing')
    times=[finite_time(row.get('recorded_monotonic')) for row in rows]
    if not start<=times[0]<=times[1]<=end:raise ValueError('detached receipt order/time differs')
    dates=[datetime.fromisoformat(row['recorded_utc']) for row in rows]
    if any(d.tzinfo is None for d in dates) or dates[1]<dates[0]:raise ValueError('detached receipt UTC invalid/regressed')
    return check_receipt_pair(contract,rows,command=spec['command'],label=label,result=record['result'],
        timeout=spec['timeout'],seen=seen,interpreter=record['transport_interpreter'],
        allowances=[min(spec['timeout']+10,spec['total_seconds'])-.5,
                    min(30,start+spec['total_seconds']-times[0])-.5])


def check_start_transport(contract,pin,start):
    token=start.get('launch_token');request=launch_request(token)
    result=start.get('result',{});checkpoint=result.get('checkpoint',{})
    if (start.get('mode')!='DETACHED_START' or start.get('status')!='OPEN' or start.get('errors') or
        start.get('artifact_errors') or start.get('contract_sha256')!=pin or result.get('status')!='OPEN' or
        result.get('errors')!={'primary':None,'cleanup':[]} or
        result.get('checkpoint_sha256')!=digest(encode(checkpoint))):raise ValueError('incomplete detached start/open evidence')
    checked_open(contract,pin,checkpoint,token)
    if (result['launch']['launch_token']!=token or result['launch']['binding']!=checkpoint['binding'] or
        type(result['launch'].get('launch_monotonic_ns')) is not int or
        not 0<=checkpoint['observed_monotonic_ns']-result['launch']['launch_monotonic_ns']<int(5e9) or
        result['launch'].get('stdio')!='DEVNULL; close_fds; new session'):
        raise ValueError('detached launch/open binding differs')
    return check_detached_call(contract,start,spec=detached_spec(contract,pin,'guest-launch',request),
                               label='m2-guest-launch')


def check_detached_transport(contract,pin,record):
    start=record['launch'];seen=check_start_transport(contract,pin,start)
    opened=start['result'];checkpoint=opened['checkpoint'];join=record.get('join_result',{})
    request={'launch_token':start['launch_token'],'checkpoint_sha256':opened['checkpoint_sha256']}
    until=max(v['deadline_monotonic_ns'] for v in checkpoint['points'].values())+int((2*contract['grace']+capture.REAP_SECONDS+5)*1e9)
    result=record.get('result',{})
    if (record.get('status')!='COMPLETE' or record.get('contract_sha256')!=pin or record.get('errors') or record.get('artifact_errors') or
        record.get('transport_interpreter')!=start['transport_interpreter'] or record.get('host_boot_id')!=start['host_boot_id'] or
        record.get('started_monotonic',-1)<start['ended_monotonic'] or join.get('status')!='COMPLETE' or
        join.get('checkpoint_sha256')!=request['checkpoint_sha256'] or encode(join.get('collection'))!=encode(result) or
        type(join.get('joined_monotonic_ns')) is not int or not checkpoint['observed_monotonic_ns']<=join['joined_monotonic_ns']<until or
        result.get('status')!='COMPLETE' or result.get('collection_token')!=start['launch_token'] or
        result.get('terminal') is not True or result.get('cleanup')!='PASS' or
        type(result.get('supervisor_exit_status')) is not int or result['supervisor_exit_status']!=0 or
        not isinstance(result.get('errors'),dict) or any(result['errors'].values()) or
        result.get('scenario_id')!=contract['scenario_id'] or result.get('execution_location')!=contract['location'] or
        result.get('selected_points')!=contract['selected_points'] or
        encode(result.get('controller_identity'))!=encode(checkpoint['controller_identity']) or
        encode(result.get('detached_launch'))!=encode(opened['launch'])):
        raise ValueError('detached terminal/start/checkpoint/deadline differs')
    cp=join.get('controller_postflight',{})
    if encode(cp.get('expected'))!=encode(checkpoint['controller_identity']):raise ValueError('controller postflight identity differs')
    observed=cp.get('observed')
    if observed is not None:
        fields={'pid','start_ticks','uid','boot_id','state','ppid','pgid','sid'}
        if (not isinstance(observed,dict) or set(observed)!=fields or
            any(type(observed[k]) is not int or observed[k]<0 for k in fields-{'boot_id','state'}) or
            not observed['start_ticks'] or observed['pid']!=cp['expected']['pid'] or
            observed['boot_id']!=cp['expected']['boot_id'] or not isinstance(observed['state'],str) or len(observed['state'])!=1 or
            capture.same_process(cp['expected'],observed) and observed['state']!='Z'):
            raise ValueError('controller exit not proven')
    call={**record,'result':join}
    return check_detached_call(contract,call,spec=detached_spec(contract,pin,'guest-join',request),label='m2-guest-join',seen=seen)


def verify_detached_files(contract,pin,transport,raw):
    if transport.get('mode')!='DETACHED_JOIN':return
    raw=Path(raw);cp=decode((raw/'capture-open.json').read_bytes());report=decode((raw/'supervision.json').read_bytes())
    result=decode((raw/'collection.json').read_bytes());start=transport['launch']['result']
    plan=decode((raw/'supervision-plan.json').read_bytes())
    if (encode(cp)!=encode(start['checkpoint']) or encode(result)!=encode(transport['result']) or
        encode(decode((raw/'detached-start.json').read_bytes()))!=encode(
            {'launch':start['launch'],'controller_identity':cp['controller_identity']}) or
        encode(cp['supervisor_identity'])!=encode(report['supervisor_identity']) or
        encode(cp['controller_identity'])!=encode(plan['controller_identity']) or
        encode(cp['binding'])!=encode(plan['open_binding']) or
        transport['join_result']['terminal_manifest_sha256']!=capture.sha(raw/'SHA256SUMS') or
        transport['join_result']['joined_monotonic_ns']<max(row['ended_monotonic_ns'] for row in result['points'].values())):
        raise ValueError('detached archived checkpoint/start/terminal/seal differs')
    checked_open(contract,pin,cp,transport['launch']['launch_token'])
    for name,row in cp['points'].items():
        canonical=report['points'][name];proof=row['proof'];identity=proof['capture_identity']
        matches=[v for v in report['processes'] if capture.process_key(v)==capture.process_key(identity)]
        if (len(matches)!=1 or encode(matches[0])!=encode(identity) or
            encode(row['wrapper_identity'])!=encode(canonical['wrapper_identity']) or
            row['started_monotonic_ns']!=canonical['started_monotonic_ns'] or row['argv']!=canonical['argv'] or
            row['point']!=canonical['point']):raise ValueError('open process identity differs from canonical supervision')
        chain={capture.process_key(v):v for v in report['processes']};seen=set();cursor=identity
        while capture.process_key(cursor)!=capture.process_key(row['wrapper_identity']):
            key=capture.process_key(cursor)
            if key in seen:raise ValueError('capture-open ownership cycle')
            seen.add(key);parent=cursor.get('ownership_parent',{});cursor=chain.get((parent.get('pid'),parent.get('start_ticks')))
            if cursor is None or encode({k:cursor[k] for k in ('pid','start_ticks','uid','boot_id')})!=encode(parent):
                raise ValueError('capture-open process not owned under selected wrapper')
    capture.verify(raw,guest_postflight=transport['join_result']['postflight'])


def remote_start(contract_path,pin,evidence,*,read=None):
    raw=Path(contract_path).read_bytes()
    if digest(raw)!=pin:raise ValueError('host reviewed contract pin differs')
    contract=decode(raw);validate_contract(contract);token=os.urandom(16).hex()
    root=Path(evidence);root.mkdir(exist_ok=False,mode=0o700);traces=[];spec=detached_spec(contract,pin,'guest-launch',launch_request(token))
    record={'mode':'DETACHED_START','status':'INCOMPLETE','contract_sha256':pin,'launch_token':token,
        'spec':spec,'errors':[],'artifact_errors':[],'started_monotonic':time.monotonic(),
        'host_boot_id':capture.BOOT_ID,'transport_interpreter':sys.executable}
    try:
        record['result']=serial_call(contract,spec['command'],'m2-guest-launch',record['started_monotonic']+30,traces,read=read)
        record.update(status='OPEN',traces=traces,ended_monotonic=time.monotonic())
        check_start_transport(contract,pin,record)
    except Exception as exc:record['status']='INCOMPLETE';record['errors'].append(type(exc).__name__+': '+str(exc))
    finally:
        record.update(traces=traces,ended_monotonic=time.monotonic())
        try:capture.save(root/'capture-start.json',record)
        except Exception as exc:record['status']='INCOMPLETE';record['artifact_errors'].append(str(exc))
    return record


def remote_join(contract_path,pin,start_path,evidence,*,read=None):
    raw=Path(contract_path).read_bytes()
    if digest(raw)!=pin:raise ValueError('host reviewed contract pin differs')
    contract=decode(raw);validate_contract(contract);start=decode(Path(start_path).read_bytes());check_start_transport(contract,pin,start)
    if start['host_boot_id']!=capture.BOOT_ID or start['transport_interpreter']!=sys.executable:
        raise ValueError('foreign host boot/interpreter; no stale join')
    request={'launch_token':start['launch_token'],'checkpoint_sha256':start['result']['checkpoint_sha256']}
    spec=detached_spec(contract,pin,'guest-join',request);root=Path(evidence);root.mkdir(exist_ok=False,mode=0o700);traces=[]
    record={'mode':'DETACHED_JOIN','status':'INCOMPLETE','launch':start,'spec':spec,'contract_sha256':pin,
        'errors':[],'artifact_errors':[],'started_monotonic':time.monotonic(),'host_boot_id':capture.BOOT_ID,
        'transport_interpreter':sys.executable}
    try:
        joined=serial_call(contract,spec['command'],'m2-guest-join',record['started_monotonic']+660,traces,timeout=640,read=read)
        record.update(status='COMPLETE',join_result=joined,result=joined.get('collection'),traces=traces,ended_monotonic=time.monotonic())
        check_detached_transport(contract,pin,record)
    except Exception as exc:record['status']='INCOMPLETE';record['errors'].append(type(exc).__name__+': '+str(exc))
    finally:
        record.update(traces=traces,ended_monotonic=time.monotonic())
        try:capture.save(root/'capture-transport.json',record)
        except Exception as exc:record['status']='INCOMPLETE';record['artifact_errors'].append(str(exc))
    return record


def overlap_schedule(starts,*,now=None,case_seconds=540,margin=5):
    """Pure admission arithmetic on ONE host clock; no scenario/control calls."""
    now=time.monotonic() if now is None else finite_time(now)
    if (not starts or type(case_seconds) is not int or case_seconds!=540 or
        type(margin) not in (int,float) or not math.isfinite(margin) or not 0<margin<=10):
        raise ValueError('fixed case bound and explicit margin required')
    ends=[]
    for contract,pin,record in starts:
        check_start_transport(contract,pin,record)
        if record['host_boot_id']!=capture.BOOT_ID or not record['ended_monotonic']<=now:
            raise ValueError('capture start from another host/clock or not yet completed')
        # Conservative: host request start precedes the actual packet opener.
        ends.append(record['started_monotonic']+contract['seconds'])
    end=min(ends)
    if now+case_seconds+margin>end:raise TimeoutError('insufficient remaining capture overlap; no lease extension')
    return dict(status='ADMITTED_ARITHMETIC_ONLY',case_seconds=540,margin_seconds=margin,
        earliest_conservative_capture_end_monotonic=end,latest_case_start_monotonic=end-case_seconds-margin)


def finite_time(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('invalid monotonic time/budget')
    return value


def check_export_read(contract,pin,meta,rows,*,action,label,request,result,overall,seen):
    """Authenticate one bounded export result through the canonical pair."""
    fields={'action','label','request','started_monotonic','deadline_monotonic','ended_monotonic',
            'trace_offset','trace_count','result_sha256'}
    if (not isinstance(meta,dict) or set(meta)!=fields or
        (meta['action'],meta['label'])!=(action,label) or encode(meta['request'])!=encode(request) or
        meta['trace_count']!=2 or type(meta['trace_count']) is not int or
        meta['result_sha256']!=digest(encode(result))):raise ValueError('export read/result specification differs')
    start=finite_time(meta['started_monotonic']);end=finite_time(meta['ended_monotonic'])
    deadline=finite_time(meta['deadline_monotonic'])
    if (not overall['started_monotonic']<=start<=end<deadline or
        deadline!=min(overall['deadline_monotonic'],start+30) or end>overall['ended_monotonic']):
        raise ValueError('export read shared deadline differs/exhausted')
    if not isinstance(rows,list) or len(rows)!=2:raise ValueError('canonical export receipt pair missing')
    times=[finite_time(row.get('recorded_monotonic')) for row in rows]
    if not start<=times[0]<=times[1]<=end:raise ValueError('stale/reordered export receipts')
    begin=datetime.fromisoformat(overall['started_utc']);finish=datetime.fromisoformat(overall['ended_utc'])
    stamps=[datetime.fromisoformat(row['recorded_utc']) for row in rows]
    if (begin.tzinfo is None or finish.tzinfo is None or any(t.tzinfo is None for t in stamps) or
        not begin<=stamps[0]<=stamps[1]<=finish):raise ValueError('export receipt UTC window differs')
    return check_receipt_pair(contract,rows,command=remote_command(contract,contract['guest_contract_path'],pin,action,request),
        label=label,result=result,timeout=20,seen=seen,interpreter=overall['transport_interpreter'],
        allowances=[min(30,deadline-start)-.5,min(30,deadline-times[0])-.5])


def check_export_reads(contract,pin,report,transport,raw):
    """Offline replay of the exact required read order/coverage and receipts."""
    start=finite_time(report.get('started_monotonic'));end=finite_time(report.get('ended_monotonic'))
    if transport.get('mode')=='DETACHED_JOIN' and transport.get('transport_interpreter')!=report.get('transport_interpreter'):
        raise ValueError('detached collection/export interpreter differs')
    seconds=finite_time(report.get('budget_seconds'));deadline=finite_time(report.get('deadline_monotonic'))
    if (not 0<seconds<=3600 or deadline!=start+seconds or not start<=end<deadline or
        report.get('transport_interpreter')!=sys.executable or
        type(report.get('max_total_bytes')) is not int or not 0<report['max_total_bytes']<=256*1024*1024):
        raise ValueError('export total budget/execution context differs')
    reads=report.get('reads');traces=report.get('traces')
    if not isinstance(reads,list) or not isinstance(traces,list) or len(traces)!=2*len(reads):
        raise ValueError('canonical export read set missing')
    first=report['inventory'];last=report['final_inventory']
    for inv in (first,last):
        if (set(inv)!={'schema_version','contract_sha256','location','scenario_id','files','errors',
                       'observed_utc','observed_monotonic_ns','directory_pin','unexpected_names','postflight'} or
            type(inv.get('schema_version')) is not int or inv['schema_version']!=1 or
            inv.get('errors')!=[] or inv.get('unexpected_names')!=[] or inv.get('contract_sha256')!=pin or
            inv.get('location')!=contract['location'] or inv.get('scenario_id')!=contract['scenario_id'] or
            type(inv.get('observed_monotonic_ns')) is not int or
            inv.get('postflight',{}).get('observed_monotonic_ns')!=inv['observed_monotonic_ns'] or
            inv.get('postflight',{}).get('observed_utc')!=inv.get('observed_utc')):
            raise ValueError('inventory/postflight scope differs')
    if (first['files']!=last['files'] or report['files']!=first['files'] or
        first['directory_pin']!=last['directory_pin'] or
        last['observed_monotonic_ns']<first['observed_monotonic_ns'] or
        sum(item['pin']['size'] for item in first['files'].values())>report['max_total_bytes']):
        raise ValueError('inventory file set/order/total bound differs')
    seen=check_collection_transport(contract,pin,transport);index=0;previous=start
    def check(action,label,result,request=None):
        nonlocal index,previous,seen
        if index>=len(reads):raise ValueError('required export read missing')
        meta=reads[index]
        if (type(meta.get('trace_offset')) is not int or meta['trace_offset']!=2*index or
            finite_time(meta.get('started_monotonic'))<previous):raise ValueError('export read order differs')
        seen=check_export_read(contract,pin,meta,traces[2*index:2*index+2],action=action,label=label,
                               request=request,result=result,overall=report,seen=seen)
        previous=meta['ended_monotonic'];index+=1
    check('guest-inventory','m2-export-inventory',first)
    for name,item in sorted(first['files'].items()):
        if name not in allowed_names(contract):raise ValueError('uncontracted export file')
        size=item['pin']['size']
        if type(size) is not int or size<0 or size>(contract['max_bytes'] if name.endswith('.pcap') else MAX_METADATA):
            raise ValueError('invalid export size bound')
        with (Path(raw)/name).open('rb') as stream:
            for offset in range(0,size,CHUNK):
                data=stream.read(min(CHUNK,size-offset))
                req=dict(name=name,pin=item['pin'],directory_pin=first['directory_pin'],offset=offset,length=min(CHUNK,size-offset))
                result={**req,'data_base64':base64.b64encode(data).decode(),'chunk_sha256':digest(data)}
                if len(data)!=req['length']:raise ValueError('short offline chunk')
                check('guest-chunk','m2-export-'+name+'-'+str(offset),result,req)
    check('guest-inventory','m2-export-final-inventory',last)
    if index!=len(reads):raise ValueError('extra/duplicate export reads')
    # Both inventories are independently authenticated and checked in their
    # guest namespace, never against the verifier host's numeric PIDs.
    capture.verify(raw,guest_postflight=first['postflight'])
    result=capture.verify(raw,guest_postflight=last['postflight'])
    verify_detached_files(contract,pin,transport,raw)
    return result


def export_capture(contract_path,contract_sha256,transport_path,destination,*,seconds=600,max_total=80*1024*1024,
                   failure_evidence=False,read=None):
    if (type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=3600 or
        type(max_total) is not int or not 0<max_total<=256*1024*1024):raise ValueError('bounded export budget required')
    raw=Path(contract_path).read_bytes()
    if digest(raw)!=contract_sha256:raise ValueError('host reviewed contract pin differs')
    contract=decode(raw);validate_contract(contract);transport=decode(Path(transport_path).read_bytes())
    if (transport.get('status') not in (('COMPLETE','INCOMPLETE') if failure_evidence else ('COMPLETE',)) or
        transport.get('contract_sha256')!=contract_sha256):
        raise ValueError('complete canonical collection transport required; missing/lost receipt cannot PASS')
    if not failure_evidence:check_collection_transport(contract,contract_sha256,transport)
    root=Path(destination);root.mkdir(exist_ok=False,mode=0o700);(root/'raw').mkdir(mode=0o700);traces=[]
    started=time.monotonic();deadline=started+seconds
    seen=set() if failure_evidence else check_collection_transport(contract,contract_sha256,transport)
    (root/'contract.json').write_bytes(raw);(root/'capture-transport.json').write_bytes(Path(transport_path).read_bytes())
    report={'status':'INCOMPLETE','transfer_status':'INCOMPLETE','operator_authority':'NOT_EVALUATED',
            'contract_sha256':contract_sha256,'transport_sha256':digest(Path(transport_path).read_bytes()),'files':{},'errors':[],
            'artifact_errors':[],'sealing_errors':[],'failure_evidence_only':failure_evidence,
            'started_monotonic':started,'deadline_monotonic':deadline,'budget_seconds':seconds,'max_total_bytes':max_total,
            'started_utc':datetime.now(timezone.utc).isoformat(),'transport_interpreter':sys.executable,'reads':[],
            'original_collection_errors':transport.get('errors',[])}
    def call(action,label,request=None):
        nonlocal seen
        start=time.monotonic()
        if start>=deadline:raise TimeoutError('fixed export deadline exhausted')
        meta=dict(action=action,label=label,request=request,started_monotonic=start,
                  deadline_monotonic=min(deadline,start+30),trace_offset=len(traces),trace_count=0)
        report['reads'].append(meta)
        try:
            result=serial_call(contract,remote_command(contract,contract['guest_contract_path'],contract_sha256,action,request),label,
                               meta['deadline_monotonic'],traces,read=read)
            meta['result_sha256']=digest(encode(result))
        except Exception as exc:
            meta['read_error']=type(exc).__name__+': '+str(exc)
            raise
        finally:
            meta['ended_monotonic']=time.monotonic();meta['trace_count']=len(traces)-meta['trace_offset']
        report['ended_monotonic']=meta['ended_monotonic'];report['ended_utc']=datetime.now(timezone.utc).isoformat()
        seen=check_export_read(contract,contract_sha256,meta,traces[meta['trace_offset']:],action=action,label=label,
                               request=request,result=result,overall=report,seen=seen)
        return result
    try:
        first=call('guest-inventory','m2-export-inventory');report['inventory']=first
        if ((first['errors'] or first['unexpected_names']) and not failure_evidence) or first['contract_sha256']!=contract_sha256:
            raise ValueError('incomplete/ambiguous inventory')
        if first['location']!=contract['location'] or first['scenario_id']!=contract['scenario_id']:
            raise ValueError('inventory scope differs')
        total=sum(row['pin']['size'] for row in first['files'].values())
        if total>max_total or set(first['files'])-allowed_names(contract):raise ValueError('export file set/total bound differs')
        for name,info in sorted(first['files'].items()):
            with (root/'raw'/name).open('xb') as stream:
                for offset in range(0,info['pin']['size'],CHUNK):
                    req=dict(name=name,pin=info['pin'],directory_pin=first['directory_pin'],offset=offset,
                             length=min(CHUNK,info['pin']['size']-offset))
                    row=call('guest-chunk','m2-export-'+name+'-'+str(offset),req)
                    if any(row.get(k)!=v for k,v in req.items()):raise ValueError('mismatched chunk binding')
                    data=base64.b64decode(row['data_base64'],validate=True)
                    if len(data)!=req['length'] or digest(data)!=row['chunk_sha256']:raise ValueError('truncated/tampered chunk')
                    stream.write(data)
            if (root/'raw'/name).stat().st_size!=info['pin']['size'] or capture.sha(root/'raw'/name)!=info['sha256']:
                raise ValueError('whole exported file hash/size differs')
            report['files'][name]=info
        last=call('guest-inventory','m2-export-final-inventory');report['final_inventory']=last
        if (((last['errors'] or last['unexpected_names']) and not failure_evidence) or last['files']!=first['files'] or
            last['directory_pin']!=first['directory_pin'] or last['contract_sha256']!=contract_sha256):
            raise ValueError('source changed or final evidence incomplete')
        report['transfer_status']='COMPLETE'
        if failure_evidence:raise ValueError('failure-only export; never collection acceptance')
        result=decode((root/'raw/collection.json').read_bytes())
        if result['collection_token']!=transport['result']['collection_token']:raise ValueError('capture token differs from transport')
        observed_identity=decode((root/'raw/identity.json').read_bytes())
        if (decode((root/'raw/configuration.json').read_bytes())!=contract['configuration'] or
            result['execution_location']!=contract['location'] or result['scenario_id']!=contract['scenario_id'] or
            result['selected_points']!=contract['selected_points'] or
            observed_identity.get('contract_sha256')!=contract_sha256 or
            observed_identity.get('runtime',{}).get('archive_sha256')!=contract['bundle_sha256']):
            raise ValueError('exported runtime/configuration/contract identity differs')
        report['ended_monotonic']=time.monotonic();report['ended_utc']=datetime.now(timezone.utc).isoformat()
        report['verification']=check_export_reads(contract,contract_sha256,{**report,'traces':traces},transport,root/'raw')
        if time.monotonic()>=deadline:raise TimeoutError('fixed export deadline exhausted')
        report['status']='COMPLETE'
    except Exception as exc:report['errors'].append(type(exc).__name__+': '+str(exc))
    finally:
        report['ended_monotonic']=time.monotonic();report['ended_utc']=datetime.now(timezone.utc).isoformat()
        try:capture.save(root/'export.json',{**report,'traces':traces})
        except Exception as exc:
            report['status']='INCOMPLETE';report['artifact_errors'].append(type(exc).__name__+': '+str(exc))
        try:
            # Seal exported bytes/receipt separately, never change guest originals.
            sums=''.join(capture.sha(f)+'  '+str(f.relative_to(root))+'\n' for f in sorted(root.rglob('*')) if f.is_file() and f.name!='EXPORT_SHA256SUMS')
            (root/'EXPORT_SHA256SUMS').write_text(sums)
        except Exception as exc:
            report['status']='INCOMPLETE';report['sealing_errors'].append(type(exc).__name__+': '+str(exc))
            try:capture.save(root/'export.json',{**report,'traces':traces})
            except Exception as later:report['artifact_errors'].append(type(later).__name__+': '+str(later))
    return report


def verify_export(root):
    """Offline exact byte/receipt/namespace verification, never guest contact."""
    root=Path(root);seen=set()
    for line in (root/'EXPORT_SHA256SUMS').read_text().splitlines():
        match=re.fullmatch(r'([0-9a-f]{64})  ((?:raw/)?[A-Za-z0-9_.-]+)',line)
        if not match or match[2] in seen:raise ValueError('unsafe/duplicate export manifest')
        path=root/match[2]
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or capture.sha(path)!=match[1]:
            raise ValueError('missing/changed exported bytes')
        seen.add(match[2])
    actual={str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
    if actual!=seen|{'EXPORT_SHA256SUMS'} or not {'contract.json','capture-transport.json','export.json'}<=seen:
        raise ValueError('incomplete/unexplained export artifacts')
    report=decode((root/'export.json').read_bytes());raw=(root/'contract.json').read_bytes();contract=decode(raw)
    transport=decode((root/'capture-transport.json').read_bytes());validate_contract(contract)
    if (report.get('status')!='COMPLETE' or report.get('transfer_status')!='COMPLETE' or report.get('errors') or
        report.get('artifact_errors') or report.get('sealing_errors') or report.get('failure_evidence_only') or
        report.get('contract_sha256')!=digest(raw) or report.get('transport_sha256')!=capture.sha(root/'capture-transport.json')):
        raise ValueError('incomplete export/collection evidence')
    check_collection_transport(contract,digest(raw),transport)
    first=report['inventory'];last=report['final_inventory']
    if (first['files']!=last['files'] or last['errors'] or last['unexpected_names'] or
        last['contract_sha256']!=digest(raw) or
        {str(p.relative_to(root/'raw')) for p in (root/'raw').iterdir()}!=set(last['files'])):
        raise ValueError('export inventory binding differs')
    for name,row in last['files'].items():
        if name not in allowed_names(contract) or capture.sha(root/'raw'/name)!=row['sha256'] or (root/'raw'/name).stat().st_size!=row['pin']['size']:
            raise ValueError('exported file differs from guest inventory')
    identity=decode((root/'raw/identity.json').read_bytes());result=decode((root/'raw/collection.json').read_bytes())
    if (identity.get('contract_sha256')!=digest(raw) or identity.get('runtime',{}).get('archive_sha256')!=contract['bundle_sha256'] or
        decode((root/'raw/configuration.json').read_bytes())!=contract['configuration'] or
        result['collection_token']!=transport['result']['collection_token'] or result['execution_location']!=contract['location']):
        raise ValueError('exported contract/runtime/collection differs')
    return check_export_reads(contract,digest(raw),report,transport,root/'raw')


def main():
    if len(sys.argv)>1 and sys.argv[1]=='_supervise':
        plan_data=decode(Path(sys.argv[2]).read_bytes())
        archive_identity(plan_data['guest_runtime']['archive_sha256'])
        return capture.supervisor(plan_data,int(sys.argv[3]))
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('build');s.add_argument('output',type=Path)
    s=sub.add_parser('verify-export');s.add_argument('directory',type=Path)
    s=sub.add_parser('plan');s.add_argument('--config',type=Path,default=capture.DEFAULT_CONFIG)
    s.add_argument('--point',action='append',required=True);s.add_argument('--location',choices=['xle','scada','drives'],required=True)
    s.add_argument('--output',required=True);s.add_argument('--scenario-id',required=True);s.add_argument('--bundle-path',required=True)
    s.add_argument('--bundle-sha256',required=True);s.add_argument('--uid',type=int,required=True)
    for flag,kind,default in [('seconds',float,10),('packets',int,5000),('max-bytes',int,8388608),('grace',float,3)]:s.add_argument('--'+flag,type=kind,default=default)
    for action in ('guest-run','guest-detached','guest-launch','guest-join','guest-inventory','guest-chunk',
                   'remote-collect','remote-start','remote-join','export'):
        s=sub.add_parser(action);s.add_argument('--contract',type=Path,required=True);s.add_argument('--contract-sha256',required=True)
        if action.startswith('guest-'):s.add_argument('--bundle-sha256',required=True)
        if action in ('guest-chunk','guest-launch','guest-detached','guest-join'):s.add_argument('--request',required=True)
        if action in ('remote-collect','remote-start','remote-join'):s.add_argument('--evidence',type=Path,required=True)
        if action=='remote-join':s.add_argument('--start',type=Path,required=True)
        if action=='export':
            s.add_argument('--transport',type=Path,required=True);s.add_argument('--output',type=Path,required=True)
            s.add_argument('--seconds',type=float,default=600);s.add_argument('--max-total-bytes',type=int,default=80*1024*1024)
            s.add_argument('--failure-evidence',action='store_true')
    args=p.parse_args()
    if args.action=='build':value=build(args.output)
    elif args.action=='verify-export':value=verify_export(args.directory)
    elif args.action=='plan':
        value=plan(capture.load_config(args.config),args.point,args.location,args.output,args.scenario_id,args.bundle_path,
                   args.bundle_sha256,args.uid,args.seconds,args.packets,args.max_bytes,args.grace)
        value['guest_contract_path']=str(Path(args.bundle_path).parent/'m2-capture-contract-REVIEWED.json')
    elif args.action=='remote-collect':value=remote_collect(args.contract,args.contract_sha256,args.evidence)
    elif args.action=='remote-start':value=remote_start(args.contract,args.contract_sha256,args.evidence)
    elif args.action=='remote-join':value=remote_join(args.contract,args.contract_sha256,args.start,args.evidence)
    elif args.action=='export':value=export_capture(args.contract,args.contract_sha256,args.transport,args.output,seconds=args.seconds,max_total=args.max_total_bytes,failure_evidence=args.failure_evidence)
    else:
        contract,runtime=load_contract(args.contract,args.contract_sha256,args.bundle_sha256)
        if args.action=='guest-run':value=guest_collect(contract,runtime,args.contract_sha256)
        elif args.action=='guest-launch':value=guest_launch(contract,runtime,args.contract_sha256,decode(base64.b64decode(args.request,validate=True)))
        elif args.action=='guest-detached':value=detached_controller(contract,runtime,args.contract_sha256,decode(base64.b64decode(args.request,validate=True)))
        elif args.action=='guest-join':value=guest_join(contract,args.contract_sha256,decode(base64.b64decode(args.request,validate=True)))
        elif args.action=='guest-inventory':value=inventory(contract,args.contract_sha256)
        else:value=chunk(contract,decode(base64.b64decode(args.request,validate=True)))
    print(json.dumps(value,sort_keys=True),flush=True)
    return 0 if value.get('status') not in ('INCOMPLETE',) else 2


if __name__=='__main__':raise SystemExit(main())

"""Fixed installed-core launch receipt and ownership-bound RPC.

Host source is rendered inline through canonical serial transport, never
installed. Importing this module performs no operation. Failure-only signals
require explicit approval and a held pidfd for this launcher's own direct child.
"""
import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time

EXE = '/home/kevin/OpenPLC_v3/webserver/core/openplc'
EXE_SHA = 'b29befd48766a7b9428e67159192195fae9fbe7a46cd93586023a4914592069f'
COMM = 'openplc'
PORT = 43628
ENV_KEY = 'SORTER_CORE_ATTEMPT'
CORE_FIELDS = ('pid','start_ticks','uid','euid','boot_id','exe','exe_sha256','attempt')
BIRTH_FIELDS = ('pid','start_ticks','uid','euid','boot_id','attempt')
LAUNCH_OBSERVE_SECONDS = 2
LAUNCH_CLEANUP_SECONDS = 2
LAUNCH_CLEANUP_POLICY = 'owned-direct-child-pidfd-term-kill-v1'


def need(condition, message):
    if not condition:
        raise ValueError(message)


class LaunchValidationError(ValueError):
    def __init__(self, failures):
        self.diagnostics={'status':'FAIL','failed_predicates':failures}
        super().__init__('invalid launch proof: '+json.dumps(self.diagnostics,sort_keys=True))


def validate_launch(launch, token, uid):
    """Report every failed predicate, including original JSON types; UID0 is valid."""
    failed=[]
    def check(name,observed,ok,expected):
        if not ok:failed.append({'predicate':name,'observed':observed,
                                 'observed_type':type(observed).__name__,'expected':expected})
    check('required_uid',uid,type(uid) is int and uid>=0,'nonnegative integer (including 0)')
    check('launch.object',launch,isinstance(launch,dict),'object')
    record=launch if isinstance(launch,dict) else {}
    value=record.get('schema_version')
    check('launch.schema_version',value,type(value) is int and value==1,1)
    check('launch.token',record.get('token'),record.get('token')==token,token)
    birth=record.get('birth')
    check('birth.object',birth,isinstance(birth,dict),'object')
    birth=birth if isinstance(birth,dict) else {}
    for name in ('pid','start_ticks','uid','euid','ppid'):
        value=birth.get(name)
        check('birth.'+name+'.type_range',value,type(value) is int and value>=0,'nonnegative integer')
    for name in ('pid','start_ticks'):
        value=birth.get(name)
        check('birth.'+name+'.positive',value,type(value) is int and value>0,'positive integer')
    for name in ('uid','euid'):
        check('birth.'+name,birth.get(name),type(birth.get(name)) is int and
              type(uid) is int and birth[name]==uid,uid)
    parent=record.get('parent_pid')
    check('launch.parent_pid',parent,type(parent) is int and parent>0,'positive integer')
    check('birth.ppid',birth.get('ppid'),type(birth.get('ppid')) is int and
          type(parent) is int and birth['ppid']==parent,parent)
    check('birth.attempt',birth.get('attempt'),birth.get('attempt')==token,token)
    value=birth.get('boot_id')
    check('birth.boot_id',value,isinstance(value,str) and bool(value),'nonempty string')
    value=record.get('issued_monotonic_ns')
    check('launch.issued_monotonic_ns',value,type(value) is int and value>0,'positive integer')
    if 'spawn_pid' in record:
        value=record['spawn_pid']
        check('birth.pid_matches_spawn',birth.get('pid'),type(value) is int and value>0 and
              type(birth.get('pid')) is int and birth['pid']==value,value)
    if failed:raise LaunchValidationError(failed)
    return birth


def validate_authorization(auth, token):
    need(isinstance(auth,dict) and auth.get('schema_version')==2 and
         auth.get('token')==token, 'missing/stale/mismatched authorization')
    core=auth['core']
    need(all(type(core[k]) is int for k in ('pid','start_ticks','uid','euid')),
         'malformed core identity')
    birth=validate_launch(auth['launch'],token,core['uid'])
    need(all(core[k]==birth[k] for k in BIRTH_FIELDS) and
         core['exe']==EXE and core['exe_sha256']==EXE_SHA,
         'core does not match launched incarnation/executable')
    return core


def validate_observation(launch, observed, token, uid):
    validate_launch(launch,token,uid)
    auth={'schema_version':2,'token':token,'launch':launch,'core':observed}
    validate_authorization(auth,token)
    return auth


class Linux:
    def incarnation(self,pid):
        """Small /proc observation independent of exe/environ across exec/exit."""
        p=Path('/proc')/str(pid)
        try:
            raw=(p/'stat').read_text();v=raw[raw.rfind(')')+2:].split()
            u=re.search(r'(?m)^Uid:\s+(\d+)\s+(\d+)',(p/'status').read_text())
            return dict(pid=pid,start_ticks=int(v[19]),ppid=int(v[1]),state=v[0],
                        uid=int(u[1]),euid=int(u[2]),
                        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        except (FileNotFoundError,ProcessLookupError):return None

    def process(self,pid):
        p=Path('/proc')/str(pid)
        try:
            s=(p/'stat').read_text();v=s[s.rfind(')')+2:].split()
            u=re.search(r'(?m)^Uid:\s+(\d+)\s+(\d+)',(p/'status').read_text())
            env=(p/'environ').read_bytes().split(b'\0')
            marker=[e.split(b'=',1)[1].decode() for e in env if e.startswith((ENV_KEY+'=').encode())]
            return dict(pid=pid,start_ticks=int(v[19]),ppid=int(v[1]),state=v[0],
                uid=int(u[1]),euid=int(u[2]),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                exe=os.readlink(p/'exe'),exe_sha256=hashlib.sha256((p/'exe').read_bytes()).hexdigest(),
                attempt=marker[0] if len(marker)==1 else None)
        except (FileNotFoundError,ProcessLookupError):
            return None

    def cores(self):
        result=[]
        for p in Path('/proc').iterdir():
            if not p.name.isdigit():continue
            try:
                if (p/'comm').read_text().strip()==COMM:result.append(int(p.name))
            except (FileNotFoundError,ProcessLookupError):pass
        return sorted(result)

    def sockets(self,pid):
        result=set()
        for f in (Path('/proc')/str(pid)/'fd').iterdir():
            try:
                m=re.fullmatch(r'socket:\[(\d+)\]',os.readlink(f))
                if m:result.add(int(m[1]))
            except (FileNotFoundError,ProcessLookupError):pass
        return result

    def tcp(self):
        result=[]
        for version in ('tcp','tcp6'):
            for line in Path('/proc/net',version).read_text().splitlines()[1:]:
                v=line.split()
                def endpoint(s):
                    address,port=s.split(':')
                    return (socket.inet_ntoa(bytes.fromhex(address)[::-1]) if version=='tcp'
                            else address,int(port,16))
                result.append(dict(local=endpoint(v[1]),remote=endpoint(v[2]),
                                   state=v[3],inode=int(v[9])))
        return result

    def pin(self,pid):return os.pidfd_open(pid)
    def alive(self,fd):return not select.select([fd],[],[],0)[0]
    def close(self,fd):os.close(fd)
    def signal(self,fd,number):signal.pidfd_send_signal(fd,number)
    def connect(self,timeout):return socket.create_connection(('127.0.0.1',PORT),timeout=timeout)
    def now(self):return time.monotonic()
    def now_ns(self):return time.monotonic_ns()
    def sleep(self,seconds):time.sleep(seconds)


def check_process(kernel,core,fd):
    row=kernel.process(core['pid'])
    need(row is not None and row['state']!='Z' and
         all(row[k]==core[k] for k in CORE_FIELDS) and kernel.alive(fd),
         'authorized incarnation disappeared, changed or was reused')
    need(kernel.cores()==[core['pid']], 'unexpected or indeterminate core set')


def listener(kernel,core):
    rows=[r for r in kernel.tcp() if r['state']=='0A' and r['local'][1]==PORT]
    need(len(rows)==1 and rows[0]['inode']>0 and
         rows[0]['inode'] in kernel.sockets(core['pid']),
         'RPC listener is absent, ambiguous or not owned by this core')
    return rows[0]['inode']


def accepted(kernel,core,connection,listener_inode):
    need(listener(kernel,core)==listener_inode,'RPC listener replaced')
    rows=[r for r in kernel.tcp() if r['state']=='01' and
          r['local']==connection.getpeername() and r['remote']==connection.getsockname()]
    need(len(rows)<=1,'ambiguous RPC connection tuple')
    if not rows or not rows[0]['inode']:return None
    need(rows[0]['inode'] in kernel.sockets(core['pid']),
         'accepted RPC connection not owned by authorized core')
    return rows[0]['inode']


def owned_rpc(auth,token,kernel=None,seconds=6,command='quit()'):
    """Connect first, prove accepted-socket ownership, send once on that socket.

    TCP connections cannot be retargeted to a replacement listener. The final
    recheck detects replacement before transmission; replacement after it can
    only close or leave this already-established connection, never redirect it.
    The reviewed binary/kernel must not transfer accepted descriptors to another
    process; hostile ptrace/root or descriptor passing is outside this proof.
    """
    kernel=kernel or Linux();fd=None;connection=None
    result={'status':'REFUSED','rpc_attempted':False,'rpc_sent':False}
    try:
        need(command in ('quit()','start_modbus(502)'), 'unsupported RPC')
        core=validate_authorization(auth,token)
        age=(kernel.now_ns()-auth['launch']['issued_monotonic_ns'])/1e9
        need(0<=age<=630,'stale launch receipt')
        end=kernel.now()+seconds
        fd=kernel.pin(core['pid'])
        check_process(kernel,core,fd)
        listen_inode=listener(kernel,core)
        connection=kernel.connect(min(3,max(.01,end-kernel.now())))
        need(connection.getpeername()==('127.0.0.1',PORT),'unexpected RPC peer')
        while True:
            check_process(kernel,core,fd)
            inode=accepted(kernel,core,connection,listen_inode)
            if inode is not None:break
            need(kernel.now()<end,'accepted socket ownership deadline')
            kernel.sleep(.01)
        # Revalidate both incarnations and the exact accepted socket immediately
        # before writing. No fresh connect or nc invocation follows this check.
        check_process(kernel,core,fd)
        need(accepted(kernel,core,connection,listen_inode)==inode,'RPC accepted socket replaced')
        need(kernel.now()<end,'owned RPC deadline expired')
        connection.settimeout(min(3,end-kernel.now()))
        result.update(rpc_attempted=True,rpc_sent=None,accepted_inode=inode,listener_inode=listen_inode)
        connection.sendall((command+'\n').encode())
        result.update(status='SENT',rpc_sent=True)
        try:result['response']=connection.recv(4096).decode(errors='replace')
        except OSError as exc:result['response_error']=str(exc)  # never retry an ambiguous send
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
        if result['rpc_attempted']:result['status']='SEND_UNCONFIRMED'
    finally:
        if connection is not None:connection.close()
        if fd is not None:kernel.close(fd)
    return result


def owned_quit(auth,token,kernel=None,seconds=6):
    return owned_rpc(auth,token,kernel,seconds)


def launch_check(name,observed,ok,expected):
    if not ok:raise LaunchValidationError([{'predicate':name,'observed':observed,
                                           'observed_type':type(observed).__name__,'expected':expected}])


def cleanup_launch_child(child,fd,anchor,parent,uid,authorized,end,kernel):
    """Only this unreaped Popen child; no numeric PID signal or RPC fallback."""
    end=min(end,kernel.now()+LAUNCH_CLEANUP_SECONDS)
    result={'status':'INCOMPLETE','exit_confirmed':False,'signals':[],
            'spawn_pid':child.pid,'last_observed_running':None,
            'authorized':authorized,'errors':[],'operator_recovery_needed':True}
    try:
        # poll/wait refer to our retained child, not a discovered PID. No other
        # thread/reaper is permitted in this single-process inline launcher.
        code=child.poll()
        result['last_observed_running']=code is None
        if code is None:
            need(authorized is True,'direct-child signal cleanup was not authorized')
            need(fd is not None and anchor is not None,'missing pinned direct-child incarnation')
            for sig in (signal.SIGTERM,signal.SIGKILL):
                need(kernel.now()<end,'launch cleanup deadline exhausted')
                current=kernel.incarnation(child.pid)
                if not kernel.alive(fd):break
                fields=('pid','start_ticks','ppid','uid','euid','boot_id')
                need(current is not None and all(current[n]==anchor[n] for n in fields) and
                     anchor['pid']==child.pid and anchor['ppid']==os.getpid() and
                     anchor['uid']==anchor['euid']==uid and
                     same_parent(kernel.incarnation(os.getpid()),parent),
                     'direct-child cleanup incarnation/parent/UID ownership not proved')
                kernel.signal(fd,sig);result['signals'].append(sig.name)
                try:
                    code=child.wait(timeout=max(0,min(.5 if sig==signal.SIGTERM else 2,end-kernel.now())))
                    break
                except subprocess.TimeoutExpired:pass
            if code is None:
                code=child.wait(timeout=max(0,end-kernel.now()))
        result.update(status='CHILD_EXITED',exit_confirmed=True,returncode=code,
                      last_observed_running=False,operator_recovery_needed=False)
    except BaseException as exc:result['errors'].append(f'{type(exc).__name__}: {exc}')
    return result


def same_parent(current,parent):
    return current is not None and parent is not None and all(
        current[n]==parent[n] for n in ('pid','start_ticks','ppid','uid','euid','boot_id'))


def launch(log,token,uid,cleanup_on_failure=False,*,publish_receipt=False):
    """Observe exec <=2s, reserve <=2s cleanup, inside the unchanged startup10.

    Observations are flushed before validation. They never authorize an RPC.
    On failure a retained child/pidfd may be cleaned only under explicit new
    launch authority covering LAUNCH_CLEANUP_POLICY; missing proof refuses signals.
    """
    kernel=Linux();fd=None;child=None;anchor=None;parent=None;receipt=None
    end=kernel.now()+LAUNCH_OBSERVE_SECONDS+LAUNCH_CLEANUP_SECONDS
    previous_term=None
    report={'status':'INCOMPLETE','primary_error':None,'validation':None,
            'cleanup':{'status':'NOT RUN'},'evidence_errors':[],'resource_errors':[],
            'untrusted_observations':{'untrusted':True,'parent':None,'child_anchor':None,
                                      'attempted_event':None}}
    def emit(event,data):
        try:print(json.dumps({'kind':'m2-core-launch','event':event,'untrusted':True,'data':data}),flush=True)
        except BaseException as exc:
            report['evidence_errors'].append(f'{type(exc).__name__}: {exc}');raise
    try:
        need(isinstance(token,str) and re.fullmatch(r'[0-9a-f]{32}',token) is not None,'invalid attempt token')
        need(type(uid) is int and uid>=0 and os.getuid()==uid and os.geteuid()==uid,
             'launch UID/EUID differs from review')
        need(type(cleanup_on_failure) is bool,'cleanup authorization must be boolean')
        need(signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL,'exclusive unreaped-child lifecycle required')
        if cleanup_on_failure:need(hasattr(signal,'pidfd_send_signal'),'pidfd signal support required before launch')
        def interrupted(signum,frame):raise RuntimeError('launcher received SIGTERM')
        previous_term=signal.signal(signal.SIGTERM,interrupted)
        parent=kernel.incarnation(os.getpid())
        report['untrusted_observations']['parent']=deepcopy(parent)
        need(parent is not None,'launcher incarnation unavailable')
        with open(log,'xb') as out:
            child=subprocess.Popen(['nohup','./core/openplc'],stdout=out,stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL,env={**os.environ,ENV_KEY:token})
            report['spawn_pid']=child.pid
            # Do not poll/wait/reap before pinning. PPID alone is not the proof:
            # retain Popen, verify our unchanged parent incarnation, and hold pidfd.
            fd=kernel.pin(child.pid);anchor=kernel.incarnation(child.pid)
            event_data={'spawn_pid':child.pid,'parent':parent,'anchor':anchor}
            # Retain independent diagnostic snapshots BEFORE the first write.
            # Cleanup/validation still use their original live proofs, never
            # this untrusted report (including when no birth receipt exists).
            report['untrusted_observations']['child_anchor']=deepcopy(anchor)
            report['untrusted_observations']['attempted_event']=deepcopy({
                'kind':'m2-core-launch','event':'child-created','untrusted':True,'data':event_data})
            emit('child-created',event_data)
            launch_check('direct_child_anchor',anchor,anchor is not None and
                         anchor['pid']==child.pid and anchor['ppid']==os.getpid() and
                         anchor['uid']==anchor['euid']==uid and anchor['start_ticks']>0 and
                         same_parent(kernel.incarnation(os.getpid()),parent),
                         {'spawn_pid':child.pid,'parent_pid':os.getpid(),'uid':uid,'euid':uid})
            observe_end=min(end-LAUNCH_CLEANUP_SECONDS,kernel.now()+LAUNCH_OBSERVE_SECONDS)
            issued=kernel.now_ns()  # original launch issue time, never renewed by polling
            previous=None
            for index in range(32):
                birth=kernel.process(child.pid)
                receipt={'schema_version':1,'token':token,'parent_pid':os.getpid(),
                         'spawn_pid':child.pid,'issued_monotonic_ns':issued,'birth':birth}
                emit('birth-before-validation',receipt)
                validate_launch(receipt,token,uid)
                launch_check('birth.matches_pinned_anchor',birth,
                             all(birth[n]==anchor[n] for n in ('pid','start_ticks','ppid','uid','euid','boot_id')),
                             anchor)
                launch_check('child.live',birth.get('state'),birth.get('state')!='Z' and kernel.alive(fd),'live pinned child')
                # nohup execs in the same PID/start/UID incarnation. Do not
                # confuse its transient executable with the selected core, or
                # authorize it. Require two matching final executable samples.
                if birth.get('exe')==EXE and birth.get('exe_sha256')==EXE_SHA:
                    if previous is not None and all(birth[n]==previous[n] for n in CORE_FIELDS):break
                    previous=birth
                else:previous=None
                launch_check('selected_exec.before_deadline',birth,kernel.now()<observe_end,
                             {'exe':EXE,'exe_sha256':EXE_SHA,'within_seconds':LAUNCH_OBSERVE_SECONDS})
                kernel.sleep(min(.05,max(0,observe_end-kernel.now())))
            else:launch_check('selected_exec.observation_count',birth,False,
                              {'exe':EXE,'exe_sha256':EXE_SHA,'maximum_observations':32})
            need(kernel.now()<=observe_end,'selected exec observation deadline expired')
        report.update(status='VALIDATED',validation={'status':'PASS'})
        report['raw_receipt']=receipt
        # A failed diagnostic write is still a launch failure while both handles
        # remain available. It must not bypass authorized child cleanup.
        emit('validated',report)
    except BaseException as exc:
        report['status']='INCOMPLETE'
        report['primary_error']=f'{type(exc).__name__}: {exc}'
        report['validation']=getattr(exc,'diagnostics',None)
        if child is not None:
            report['cleanup']=cleanup_launch_child(child,fd,anchor,parent,uid,cleanup_on_failure,end,kernel)
    report['raw_receipt']=receipt
    try:
        try:
            emit('terminal',report)
            if publish_receipt and report['status']=='VALIDATED':
                try:print(json.dumps(receipt),flush=True)
                except BaseException as exc:
                    report['evidence_errors'].append(f'receipt publication: {type(exc).__name__}: {exc}');raise
        except BaseException as exc:
            # Output failure after validation must still use the held handles.
            if report['primary_error'] is None:
                report['primary_error']=f'{type(exc).__name__}: {exc}'
                if child is not None:
                    report['cleanup']=cleanup_launch_child(child,fd,anchor,parent,uid,cleanup_on_failure,end,kernel)
            report['status']='INCOMPLETE'
            try:emit('terminal',report)
            except BaseException:pass
    finally:
        if previous_term is not None:signal.signal(signal.SIGTERM,previous_term)
        if fd is not None:
            try:kernel.close(fd)
            except BaseException as exc:report['resource_errors'].append(f'{type(exc).__name__}: {exc}')
    if report['resource_errors']:
        report['status']='INCOMPLETE'
        if child is not None and report['cleanup']['status']=='NOT RUN':
            # A failed close cannot prove that the descriptor is still a valid
            # signal handle. Observe/reap our child, never fall back to its PID.
            report['cleanup']=cleanup_launch_child(child,None,anchor,parent,uid,False,end,kernel)
        try:emit('terminal',report)
        except BaseException:pass
    if report['status']!='VALIDATED' or report['evidence_errors'] or report['resource_errors']:
        report['status']='INCOMPLETE'
        error=ValueError('launch failed: '+json.dumps(report,sort_keys=True));error.report=report;raise error
    return receipt


if __name__=='__main__':
    action=sys.argv[1];data=json.loads(sys.argv[2])
    if action=='launch':launch(data['log'],data['token'],data['uid'],
                               data.get('cleanup_on_failure',False),publish_receipt=True)
    elif action=='abort':print(json.dumps(owned_quit(data['authorization'],data['token'])),flush=True)
    elif action=='modbus':print(json.dumps(owned_rpc(data['authorization'],data['token'],
                                                  command='start_modbus(502)')),flush=True)
    else:raise ValueError('unsupported fixed action')

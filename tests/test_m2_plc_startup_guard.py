"""Synthetic startup/handshake and real host watchdog fixtures. No guest/network."""
from copy import deepcopy
import base64
import ctypes
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import m2_plc_startup_guard as g
from test_accumulation_state_snapshot import sample

BOOT_PROC='f41ef16b-879d-4d26-8a6e-ae2b13f8d117'
BOOT_JOURNAL='f41ef16b879d4d268a6eae2b13f8d117'


def state():
    s=sample()['plc']
    s['startup_extra']={'257':7,'555':1,'556':0,'557':1,'560':1,'564':0,
                        '565':1,'566':0,'567':4,'573':4,'590':20,'593':0}
    return s


def cold_state():
    """Selected ST defaults, after the automatic L1 scanner reset ACKs.

    Epoch/plant synchronization has NOT happened. This is a synthetic first
    safe readable cold state, not a claim that the very first PLC scan was read.
    """
    s=state()
    s['coils_880_920'][1:8]=[True]*7
    s['setpoints_200_210']=[200,200,200,233,233,233,2,14,14,14,30]
    s['process_214_242'][221-214]=1
    s['plant_faults'][0]=1
    s['run_identity'].update(run_id=0,epoch=[0,0],epoch_fault=1,
                             scanner_nonce=1,plant_epoch_nonce=[0,0,0])
    s['startup_extra'].update({'555':0,'556':0,'557':0,'560':0,'565':0,'566':0,
                               '567':0,'573':0,'590':0})
    for idx,name in enumerate(g.typed.VFD_NAMES):
        s['vfds'][name].update(host=f'10.10.1.{21+idx}',setpoint=0)
    return s


def synchronized_state():
    s=cold_state()
    s['plant_faults'][0]=0
    s['run_identity'].update(epoch=[2,0],epoch_fault=0,scanner_nonce=2,plant_epoch_nonce=[2,0,2])
    s['startup_extra'].update({'555':2,'556':0,'557':1,'560':1,'565':2,'566':0,
                               '567':4,'573':4,'590':20})
    return s


def initializing(s,**kwargs):
    return g.safe_state(s,phase='initializing',initialization_deadline=time.monotonic()+35,**kwargs)


def installed(running=False,modbus=False,token='test'):
    return {'active_program':'xle_sorter.st',
            'settings':{'Start_run_mode':'false','Modbus_port':'502'},
            'programs':[[38,'xle_sorter.st']],'integrity':[['ok']],
            'web':'200','hashes':g.PLC_HASHES.copy(),'target':None,
            'cores':[{'pid':77,'start_ticks':99,'uid':1000,'euid':1000,
                      'attempt':token,'boot_id':'SYNTHETIC',
                      'exe':g.PLC_ROOT+'/core/openplc','exe_sha256':g.PLC_HASHES['core/openplc']}]
                     if running else [],
            'ports':('LISTEN 0 5 127.0.0.1:43628 0.0.0.0:*\n' if running else '')+
                    ('LISTEN 0 5 0.0.0.0:502 0.0.0.0:*\n' if modbus else '')}


def authorization(token='test'):
    core=installed(True,token=token)['cores'][0]
    birth={**core,'ppid':12,'state':'S'}
    launch={'schema_version':1,'token':token,'parent_pid':12,
            'issued_monotonic_ns':time.monotonic_ns(),'birth':birth}
    return {'schema_version':2,'token':token,'launch':launch,'core':core}


def xle_evidence():
    return {'service':{'LoadState':'loaded','ActiveState':'activating','SubState':'auto-restart',
             'Result':'exit-code','MainPID':'0','ExecMainPID':'17','ExecMainCode':'1','ExecMainStatus':'1',
             'Restart':'on-failure','NRestarts':'4','ExecStart':
             '/opt/sorter-xle/venv/bin/python /opt/sorter-xle/xle.py --plc 10.10.1.10 '
             '--asx http://127.0.0.1:8089/sort-plan --multi --packages 0 '
             '--journal /var/lib/sorter-xle/outcomes.sqlite3'},
            'now_us':100_000_000,'boot_id':BOOT_PROC,'truncated':False,
            'journal_stderr':'','status_stderr':'','current_cmdline':None,
            'journal':[{'__REALTIME_TIMESTAMP':'99000000','_BOOT_ID':BOOT_JOURNAL,
                '_SYSTEMD_UNIT':'sorter-xle.service','_PID':'17','MESSAGE':message} for message in
                ('Connection to (10.10.1.10, 502) failed: [Errno 111] Connection refused',
                 'Traceback (most recent call last):',
                 '  File "/opt/sorter-xle/xle.py", line 282, in run_multi',
                 'RuntimeError: PLC Modbus unavailable')]}


class FakeIO(g.LiveIO):
    """Records fake RPCs in an ordinary host file, never executes a command."""
    def __init__(self,directory,best_effort=False):
        super().__init__(directory,best_effort)
        self.running=False;self.modbus=False;self.s=state();self.calls=[];self.labels=[];self.token='test'

    def read(self,vm,command,label,deadline):
        self.calls.append(command);self.labels.append(label)
        with (self.directory/'fake-commands.jsonl').open('a') as out:
            out.write(json.dumps({'fixture':True,'vm':vm,'command':command})+'\n')
        if label=='launch-once':
            data=json.loads(shlex.split(command)[-1]);self.token=data['token'];self.running=True
            return json.dumps(authorization(self.token)['launch'])
        return 'SYNTHETIC'

    def json(self,vm,command,label,deadline):
        if command.startswith(g.PLC_INSPECT):return installed(self.running,self.modbus,self.token)
        if command==g.STATE:return deepcopy(self.s)
        if command==g.XLE_PRESTART:return xle_evidence()
        if command==g.PLC_LAUNCH_CONTEXT:
            return {'uid':0,'euid':0,'initial_namespaces':True,'threshold':1024,
                    'cap_eff':'400','cap_bnd':'400','proc_support':True}
        if label in ('abort-owned-rpc','start-modbus-once'):
            opcode=g.QUIT if label=='abort-owned-rpc' else g.MODBUS
            self.calls.append(opcode)
            with (self.directory/'fake-commands.jsonl').open('a') as out:
                out.write(json.dumps({'fixture':True,'vm':vm,'command':opcode})+'\n')
            if label=='start-modbus-once':self.modbus=True
            return {'status':'SENT','rpc_sent':True,'response':'OK'}
        if label in ('abort-core-log','core-log-export'):
            b=b'SYNTHETIC CORE LOG\n'
            return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest(),'base64':base64.b64encode(b).decode()}
        raise AssertionError('unexpected fake read')


class Sender:
    def __init__(self):self.messages=[]
    def send(self,message):self.messages.append(message)


class ExactTargetIO(FakeIO):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.observations=0
    def json(self,*a,**kw):
        r=super().json(*a,**kw)
        if a[2]!='abort-final':return r
        self.observations+=1
        with (self.directory/'target-checks.txt').open('a') as out:out.write('check\n')
        # No name/listener match, yet the recorded incarnation is still alive.
        if self.observations==1:r['target']={'pid':77,'start_ticks':99,'boot_id':'SYNTHETIC','state':'S'}
        return r


class LogFailureIO(FakeIO):
    def json(self,vm,command,label,deadline):
        if label=='abort-core-log':raise OSError('SYNTHETIC export denied')
        return super().json(vm,command,label,deadline)


class RefusedAbortIO(FakeIO):
    def json(self,vm,command,label,deadline):
        if label=='abort-owned-rpc':
            return {'status':'REFUSED','rpc_sent':False,'error':'SYNTHETIC listener replaced'}
        if label=='abort-final':return installed(True,token=self.token)
        return super().json(vm,command,label,deadline)


class HandshakeIO(FakeIO):
    def __init__(self,directory):
        super().__init__(directory)
        self.s=cold_state()
        self.done=False;self.samples=0

    def read(self,vm,command,label,deadline):
        if command==g.HANDSHAKE:
            self.done=True;self.s=synchronized_state()
            return json.dumps({'status':'PASS','journal_authority':'dedicated_sorter_xle_service'})
        if command==g.XLE_STATUS:
            fields={'Id':'sorter-xle.service','ActiveState':'active','SubState':'running',
                    'MainPID':'17','NRestarts':'4','ExecMainPID':'17',
                    'ExecMainStartTimestampMonotonic':'12345','InvocationID':'1'*32,
                    'ExecStart':'{ path='+g.XLE_EXECUTABLE+' ; argv[]='+' '.join(g.XLE_ARGV)+'; }'}
            return '\n'.join(name+'='+fields[name] for name in g.XLE_SERVICE_FIELDS)
        return super().read(vm,command,label,deadline)

    def json(self,vm,command,label,deadline):
        if command==g.JOURNAL:
            return {'integrity':[['ok']],'epochs':[1,2] if self.done else [1],'outcomes':[]}
        if command==g.FRESHNESS:
            h={'connected':True,'chute':{'poll_fresh':True},'run':False,'plant_fault':0,
               'scanner_state':0,'scanner_fault_mask':0,'scanner_ack_mask':7,'zone_fault':0,'inducted':0}
            return [{'poll_ms':n,'age_ms':10,'identity':24115,'hmi':h} for n in (100,101)]
        if label=='core-log-export':
            b=b'SYNTHETIC CORE LOG\n'
            return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest(),'base64':base64.b64encode(b).decode()}
        if command==g.STATE and self.done:
            self.samples+=1;self.s['startup_extra']['590']=20+self.samples
        return super().json(vm,command,label,deadline)


class FailureHandshakeIO(HandshakeIO):
    def __init__(self,directory,fault):
        super().__init__(directory);self.fault=fault
        if fault=='occupied':self.s['slots'][0][4]=1
        if fault=='master':self.s['coils_880_920'][0]=True
        if fault=='unexpected':self.s['photoeye_faults'][0]=1
        if fault=='epoch2':self.s['run_identity']['epoch_fault']=2
        if fault=='fail_closed':self.s['startup_extra']['564']=2
        if fault=='nonzero_epoch':self.s['run_identity']['epoch']=[1,0]

    def read(self,vm,command,label,deadline):
        if command==g.HANDSHAKE and self.fault=='timeout':
            raise TimeoutError('SYNTHETIC canonical handshake deadline')
        output=super().read(vm,command,label,deadline)
        if command==g.HANDSHAKE and self.fault=='persistent':self.s=cold_state()
        return output

    def json(self,vm,command,label,deadline):
        if label=='before-reset' and self.fault=='pre_reset_identity':
            s=super().json(vm,command,label,deadline);s['run_identity']['scanner_nonce']=9;return s
        if label=='final-plc-state' and self.fault=='recurrence':return cold_state()
        if label=='final-plc-state' and self.fault=='ack_recurrence':
            s=super().json(vm,command,label,deadline);s['startup_extra']['573']=0;return s
        return super().json(vm,command,label,deadline)


class Clock:
    def __init__(self):self.now=100.
    def monotonic(self):return self.now
    def advance(self,seconds):self.now+=seconds


class BudgetIO(HandshakeIO):
    def __init__(self,directory,clock,helper_seconds=85):
        super().__init__(directory);self.clock=clock;self.helper_seconds=helper_seconds
    def read(self,vm,command,label,deadline):
        self.clock.advance(self.helper_seconds if command==g.HANDSHAKE else 2)
        return super().read(vm,command,label,deadline)
    def json(self,vm,command,label,deadline):
        self.clock.advance(30 if label=='initial-state' else 1 if label in
                           ('before-reset','typed-before-policy') else 2)
        return super().json(vm,command,label,deadline)


class StartupGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_plant_exception_is_narrow_and_other_faults_never_waived(self):
        s=cold_state()
        with self.assertRaisesRegex(ValueError,'epoch fault'):g.safe_state(s)
        with self.assertRaisesRegex(ValueError,'plant fault'):initializing(s,epoch_initial=True)
        with self.assertRaisesRegex(ValueError,'epoch fault'):initializing(s,plant_initial=True)
        self.assertEqual(initializing(s,plant_initial=True,epoch_initial=True)['status'],'INITIALIZING')
        for epoch in ([1,0],[0,1]):
            bad=deepcopy(s);bad['run_identity'].update(epoch=epoch,epoch_fault=1)
            with self.assertRaisesRegex(ValueError,'epoch fault'):
                initializing(bad,plant_initial=True,epoch_initial=True)
        for mutate in (lambda s:s['plant_faults'].__setitem__(0,2),
                       lambda s:s['plant_faults'].__setitem__(1,1),
                       lambda s:s['coils_880_920'].__setitem__(0,True),
                       lambda s:s['slots'][0].__setitem__(4,1),
                       lambda s:s['process_214_242'].__setitem__(6,1),
                       lambda s:s['vfds']['induct1'].__setitem__('fault',1)):
            unsafe=deepcopy(s);mutate(unsafe)
            with self.subTest(unsafe=unsafe),self.assertRaises(ValueError):
                initializing(unsafe,plant_initial=True,epoch_initial=True)

    def test_epoch_fault_2_fail_closed_or_inconsistent_zero_epoch_never_allowed(self):
        for fault,request,epoch in ((2,0,[0,0]),(2,2,[0,0]),(1,2,[0,0]),
                                    (0,2,[1,0]),(0,0,[0,0]),(1,1,[0,0])):
            s=cold_state();s['run_identity'].update(epoch=epoch,epoch_fault=fault)
            s['startup_extra']['564']=request
            with self.subTest(fault=fault,request=request,epoch=epoch),self.assertRaises(ValueError):
                initializing(s,epoch_initial=True,plant_initial=True)
        missing=cold_state();missing['startup_extra'].pop('564')
        with self.assertRaises(KeyError):initializing(missing,epoch_initial=True,plant_initial=True)

    def test_initialization_deadline_and_ready_phase_cannot_be_waived(self):
        s=cold_state()
        for deadline in (time.monotonic()-1,float('inf')):
            with self.assertRaisesRegex(ValueError,'initialization deadline'):
                g.safe_state(s,phase='initializing',epoch_initial=True,plant_initial=True,
                             initialization_deadline=deadline)
        with self.assertRaisesRegex(ValueError,'epoch fault'):
            g.safe_state(s,phase='ready',epoch_initial=True,plant_initial=True,
                         initialization_deadline=time.monotonic()+35)

    def test_missing_short_and_invalid_types_are_not_zero_filled(self):
        for mutate in (lambda s:s.pop('slots'),
                       lambda s:s['slots'][0].pop(),
                       lambda s:s['run_identity'].__setitem__('epoch',[]),
                       lambda s:s['process_214_242'].__setitem__(6,False)):
            s=state();mutate(s)
            with self.assertRaises((ValueError,KeyError)):g.safe_state(s)

    def test_one_launch_one_listener_then_safe_initial_gate(self):
        io=FakeIO(self.root);sender=Sender();c=g.Controller(io,sender,'test')
        c.start('/home/kevin/m2-fixture.log',1000)
        self.assertEqual(io.labels.count('launch-once'),1)
        self.assertEqual(io.calls.count(g.MODBUS),1)
        self.assertEqual([v['stage'] for v in sender.messages],['startup','owned-core','listener','initial'])
        self.assertNotIn(g.HANDSHAKE,io.calls)
        self.assertEqual(c.results['handshake']['status'],'NOT RUN')

    def test_first_readable_unsafe_state_fails_without_waiting_or_reset(self):
        io=FakeIO(self.root);io.s['process_214_242'][6]=1
        c=g.Controller(io,Sender(),'test')
        with self.assertRaisesRegex(ValueError,'counters'):c.start('/home/kevin/m2-fixture.log',1000)
        self.assertEqual(c.results['initial']['status'],'FAIL')
        self.assertNotIn(g.HANDSHAKE,io.calls)
        self.assertEqual(io.labels.count('launch-once'),1)

    def test_ambiguous_launch_is_spent_and_never_retried(self):
        io=FakeIO(self.root)
        with patch.object(io,'read',return_value='no pid') as read:
            c=g.Controller(io,Sender(),'test')
            with self.assertRaisesRegex(ValueError,'ambiguous launch'):c.start('/home/kevin/m2-fixture.log',1000)
        self.assertEqual(read.call_count,1)
        self.assertEqual(c.results['listener']['status'],'NOT RUN')

    def test_mismatched_observation_is_retained_but_never_authorized(self):
        for field,value in (('pid',88),('uid',0),('euid',0),('start_ticks',100),
                            ('boot_id','OTHER'),('attempt','OTHER')):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                io=FakeIO(temp);sender=Sender();original=io.read
                def launch(*args):
                    receipt=json.loads(original(*args))
                    if field not in ('uid','euid'):receipt['birth'][field]=value
                    return json.dumps(receipt)
                original_json=io.json
                def observe(vm,command,label,deadline):
                    report=original_json(vm,command,label,deadline)
                    if label=='started-core' and field in ('uid','euid'):
                        report['cores'][0][field]=value
                    return report
                c=g.Controller(io,sender,'test')
                with patch.object(io,'read',side_effect=launch), \
                        patch.object(io,'json',side_effect=observe),self.assertRaises(ValueError):
                    c.start('/home/kevin/new.log',1000)
                self.assertFalse((Path(temp)/'started-core-identity.json').exists())
                self.assertTrue((Path(temp)/'launch-observation.json').exists())
                if field!='attempt':
                    self.assertTrue((Path(temp)/'started-core-observation.json').exists())
                self.assertNotIn('owned-core',[m['stage'] for m in sender.messages])
                self.assertNotIn(g.MODBUS,io.calls)

    def prestart_fixture(self,bad_hash=False,bad_xle=False,owner=False):
        io=FakeIO(self.root);sender=Sender();c=g.Controller(io,sender,'test');c.expected_uid=0
        manifest=json.loads(g.pre.MANIFEST.read_text())
        calls=[]
        original_json=io.json
        def read_json(vm,command,label,deadline):
            if command==g.XLE_PRESTART and bad_xle:
                return {'service':{'Result':'start-limit-hit'}}
            return original_json(vm,command,label,deadline)
        def read(vm,command,label,deadline):
            calls.append(command)
            if label.startswith('hashes-'):
                return '\n'.join(('0'*64 if bad_hash else r['sha256'])+'  '+r['canonical_destination']
                                  for r in manifest['components'] if r['guest']==vm)
            if label.startswith('prestart-processes-'):
                return {'drives':'12 /home/kevin/venv/bin/python /home/kevin/plant.py',
                        'scada':'','xle':'14 /opt/sorter-xle/venv/bin/python /opt/sorter-xle/asx.py'}[vm]
            if label=='launch-uid':return '0'
            return 'active'
        with patch.object(g.subprocess,'check_output',side_effect=[g.COMMIT+'\n','']), \
                patch.object(g.pre,'ROOT'),patch.object(g.pre,'MANIFEST'), \
                patch.object(g.pre,'check'),patch.object(g.pre,'_vm_running'), \
                patch.object(io,'read',side_effect=read),patch.object(io,'json',side_effect=read_json), \
                patch.object(g.subprocess,'run',return_value=type('PS',(),{'stdout':
                    '123 virsh -c qemu:///system console plc\n' if owner else ''})()):
            # Preserve the exact manifest fixture when patching global settings.
            with patch.object(g.pre,'MANIFEST',g.SELECTED/'deploy/deployment_manifest.json'):
                if bad_hash:
                    with self.assertRaisesRegex(ValueError,'deployed hashes differ'):
                        g.source_and_prestart(c,'/home/kevin/new.log')
                elif bad_xle:
                    with self.assertRaisesRegex(ValueError,'XLe prestart blocked'):
                        g.source_and_prestart(c,'/home/kevin/new.log')
                elif owner:
                    with self.assertRaisesRegex(ValueError,'competing console'):
                        g.source_and_prestart(c,'/home/kevin/new.log')
                else:g.source_and_prestart(c,'/home/kevin/new.log')
        self.assertEqual(sender.messages,[])
        self.assertFalse(any('nohup' in command or command in (g.MODBUS,g.QUIT,g.HANDSHAKE) for command in calls))
        return c

    def test_prestart_hashes_do_not_depend_on_open_502_or_active_xle(self):
        self.assertEqual(self.prestart_fixture().results['prestart']['status'],'PASS')

    def test_prestart_hash_failure_blocks_all_control_writes(self):
        c=self.prestart_fixture(bad_hash=True)
        self.assertEqual(c.results['prestart']['status'],'FAIL')

    def test_prestart_xle_failure_blocks_before_launch(self):
        c=self.prestart_fixture(bad_xle=True)
        self.assertEqual(c.results['prestart']['status'],'FAIL')
        self.assertEqual(c.results['startup']['status'],'NOT RUN')

    def test_observed_console_owner_blocks_and_is_never_evicted(self):
        c=self.prestart_fixture(owner=True)
        self.assertEqual(c.results['prestart']['status'],'FAIL')
        r=json.loads((self.root/'prestart-console-observation.json').read_text())
        self.assertEqual(r['owners'],[{'pid':123,'role':'virsh-console'}])
        self.assertIn('instantaneous',r['scope'])
        self.assertEqual(c.results['startup']['status'],'NOT RUN')

    def test_handshake_requires_new_journal_epoch_nonce_ack_and_advancing_heartbeat(self):
        before=state();before['run_identity'].update(epoch=[0,0],scanner_nonce=1,plant_epoch_nonce=[0,0,1])
        first=state();first['run_identity'].update(epoch=[2,0],scanner_nonce=2,plant_epoch_nonce=[2,0,2])
        first['startup_extra'].update({'555':2,'565':2})
        second=deepcopy(first);second['startup_extra']['590']=21
        old={'epochs':[1]};journal={'epochs':[1,2],'integrity':[['ok']],'outcomes':[]}
        g.handshake_check(before,first,second,old,journal)
        for edit in (lambda s:s['startup_extra'].__setitem__('257',3),
                     lambda s:s['startup_extra'].__setitem__('573',0),
                     lambda s:s['startup_extra'].__setitem__('590',20),
                     lambda s:s['startup_extra'].__setitem__('593',31),
                     lambda s:s['startup_extra'].__setitem__('560',0),
                     lambda s:s['startup_extra'].__setitem__('564',2),
                     lambda s:s['startup_extra'].__setitem__('565',1),
                     lambda s:s['run_identity'].__setitem__('plant_epoch_nonce',[0,0,2])):
            bad=deepcopy(second);edit(bad)
            with self.assertRaises(ValueError):g.handshake_check(before,first,bad,old,journal)
        with self.assertRaises(ValueError):
            g.handshake_check(before,first,second,old,{**journal,'outcomes':['2:2:7:8:9']})
        with self.assertRaises(ValueError):
            g.handshake_check(before,first,second,{'epochs':[1,2]},journal)

    def test_changed_web_settings_core_or_db_fails(self):
        for key,value in [('settings',{'Start_run_mode':'true','Modbus_port':'502'}),
                          ('integrity',[['corrupt']]),('web','500')]:
            r=installed();r[key]=value
            with self.assertRaises(ValueError):g.installed_guard(r,stopped=True)
        r=installed(True,True);core=r['cores'][0].copy();core['start_ticks']+=1
        with self.assertRaises(ValueError):g.installed_guard(r,stopped=False,core=core)

    def test_inline_commands_are_syntax_checked_without_executing(self):
        import ast,shlex
        for command in (g.PLC_INSPECT,g.STATE,g.JOURNAL,g.FRESHNESS,g.XLE_PRESTART,
                        g.PLC_LAUNCH_CONTEXT,
                        g.owned_command('abort',{'authorization':authorization(),'token':'test'})):
            args=shlex.split(command);ast.parse(args[args.index('-c')+1])
        result=subprocess.run(['bash','-n'],input=g.startup_command('/home/kevin/new.log')+
                              '\n'+g.HANDSHAKE+'\n'+g.owned_command('abort',
                              {'authorization':authorization(),'token':'test'}),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_plan_never_reads_a_guest_or_claims_approval(self):
        with patch.object(sys,'argv',['guard','plan','--core-log','/home/kevin/new.log']), \
                patch.object(g,'ORIGINAL_READ',side_effect=AssertionError('guest read')), \
                patch.object(g,'claim_host_approval',side_effect=AssertionError('approval')), \
                patch('builtins.print'):
            self.assertEqual(g.main(),0)

    def test_initial_unavailable_reads_have_one_total_deadline(self):
        io=FakeIO(self.root);original=io.json;calls=[]
        def unavailable(vm,command,label,deadline):
            if command==g.STATE:
                calls.append(deadline);return {'unavailable':True}
            return original(vm,command,label,deadline)
        now=[0]
        def tick():now[0]+=.7;return now[0]
        c=g.Controller(io,Sender(),'test')
        with patch.object(io,'json',side_effect=unavailable), \
                patch.dict(g.BOUNDS,initial=1),patch.object(g.time,'monotonic',side_effect=tick), \
                patch.object(g.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'initial readable-state deadline'):
                c.start('/home/kevin/m2-fixture.log',1000)
        self.assertEqual(len(set(calls)),1)
        self.assertEqual(c.results['handshake']['status'],'NOT RUN')

    def complete_fixture(self,changed=False):
        io=HandshakeIO(self.root);sender=Sender()
        c=g.Controller(io,sender,'test',plant_initial=True,epoch_initial=True)
        c.start('/home/kevin/m2-fixture.log',1000)
        before=sample();before['plc']=deepcopy(io.s)
        before['plc'].pop('startup_extra')
        after=sample();after['plc']=synchronized_state()
        after['plc'].pop('startup_extra')
        if changed:after['plc']['seed']+=1
        with patch.object(g.typed,'capture',side_effect=[before,after]), \
                patch.object(g.pre,'check',return_value={'status':'PASS','source_and_guest_hashes':43}), \
                patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'), \
                patch.object(g.time,'sleep'):
            if changed:
                with self.assertRaisesRegex(ValueError,'typed restoration failed'):c.complete()
            else:c.complete()
        return c,sender

    def test_success_requires_canonical_typed_comparison_and_readable_export(self):
        c,sender=self.complete_fixture()
        self.assertEqual(c.results['handshake']['status'],'PASS')
        self.assertEqual(c.results['verification']['status'],'PASS')
        self.assertEqual(c.results['abort']['status'],'NOT RUN')
        self.assertEqual(sender.messages[-1],{'stage':'accept'})
        self.assertEqual((self.root/'core.log').read_text(),'SYNTHETIC CORE LOG\n')
        self.assertEqual(json.loads((self.root/'typed-before-self.json').read_text())['status'],'FAIL')
        self.assertEqual(json.loads((self.root/'typed-comparison.json').read_text())['status'],'PASS')
        self.assertEqual(c.results['initial']['status'],'INITIALIZING')
        self.assertEqual(c.results['typed_before']['status'],'INITIALIZING')
        self.assertEqual(c.results['typed_before']['initialization_exception_fields'],
                         ['plc.plant_faults.0','plc.run_identity.epoch_fault'])
        self.assertEqual(c.ready_identity['epoch'],[2,0])
        with self.assertRaisesRegex(ValueError,'cannot recur'):
            c.initializing(cold_state())

    def test_reset_configuration_difference_stops_acceptance(self):
        c,sender=self.complete_fixture(changed=True)
        self.assertEqual(c.results['handshake']['status'],'PASS')
        self.assertEqual(c.results['verification']['status'],'FAIL')
        self.assertNotIn({'stage':'accept'},sender.messages)

    def test_documented_success_path_fits_proposed_stage_and_unchanged_overall_limits(self):
        self.assertEqual(g.BOUNDS,{'prestart':240,'startup':10,'listener':10,'initial':35,
                                  'typed_before':150,'handshake':95,'verification':270})
        self.assertEqual(g.INITIALIZATION_SECONDS,280)
        self.assertEqual(sum(v for k,v in g.BOUNDS.items() if k!='prestart'),570)
        clock=Clock();io=BudgetIO(self.root,clock);sender=Sender()
        c=g.Controller(io,sender,'test',plant_initial=True,epoch_initial=True)
        before=sample();before['plc']=cold_state();before['plc'].pop('startup_extra')
        after=sample();after['plc']=synchronized_state();after['plc'].pop('startup_extra')
        snapshots=iter([(140,before),(150,after)])
        def capture():
            seconds,snapshot=next(snapshots);clock.advance(seconds);return snapshot
        def preflight(**kw):clock.advance(10);return {'status':'PASS'}
        with patch.object(g.time,'monotonic',clock.monotonic),patch.object(g.time,'sleep',clock.advance), \
                patch.object(g.typed,'capture',side_effect=capture),patch.object(g.pre,'check',side_effect=preflight), \
                patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'):
            c.start('/home/kevin/new.log',1000);c.complete()
        self.assertEqual(c.results['verification']['status'],'PASS')
        self.assertLess(clock.now-100,g.WATCHDOG_SECONDS)
        self.assertEqual(g.WATCHDOG_SECONDS,600)
        self.assertEqual(sender.messages[-1],{'stage':'accept'})

    def test_slow_helper_cannot_extend_handshake_or_initialization_allowance(self):
        clock=Clock();io=BudgetIO(self.root,clock,helper_seconds=96);sender=Sender()
        c=g.Controller(io,sender,'test',plant_initial=True,epoch_initial=True)
        before=sample();before['plc']=cold_state();before['plc'].pop('startup_extra')
        with patch.object(g.time,'monotonic',clock.monotonic),patch.object(g.time,'sleep',clock.advance), \
                patch.object(g.typed,'capture',return_value=before), \
                patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'):
            c.start('/home/kevin/new.log',1000)
            with self.assertRaisesRegex(ValueError,'deadline'):c.complete()
        self.assertEqual(c.results['handshake']['status'],'FAIL')
        self.assertEqual(c.results['verification']['status'],'NOT RUN')
        self.assertIsNone(c.ready_identity)
        self.assertFalse((self.root/'readiness.json').exists())
        self.assertNotIn({'stage':'accept'},sender.messages)

    def test_typed_policy_sidecar_must_match_the_raw_canonical_identity(self):
        io=HandshakeIO(self.root);sender=Sender()
        c=g.Controller(io,sender,'test',plant_initial=True,epoch_initial=True)
        c.start('/home/kevin/new.log',1000)
        before=sample();before['plc']=cold_state();before['plc']['run_identity']['scanner_nonce']=9
        with patch.object(g.typed,'capture',return_value=before), \
                patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'):
            with self.assertRaisesRegex(ValueError,'typed/policy run identity'):c.complete()
        self.assertEqual(c.results['handshake']['status'],'NOT RUN')


class PreparationAccessTests(unittest.TestCase):
    def test_prestart_and_startup_only_receipt_phases_are_distinct(self):
        receipt={'schema_version':2,'core_uid':0,'execution_context':'operator-root-consoles',
                 'console_shell_users':g.OPERATOR_ROOT_SHELLS.copy(),
                 'runtime_checks':{'modbus_accepted_socket':'NOT RUN','conditional_abort_recheck':'NOT RUN'}}
        for key in ('drives_bindings_and_environment','plc_slave_mapping','launch_privilege_and_nc_timeout',
                    'console_exclusivity','readable_evidence_export','owned_rpc_prestart_support'):
            receipt[key]='PASS'
        g.validate_prerequisite_phases(receipt,0,'operator-root-consoles')
        for key,value in (('owned_rpc_prestart_support','NOT REVIEWED'),
                          ('runtime_checks',{'modbus_accepted_socket':'PASS','conditional_abort_recheck':'NOT RUN'}),
                          ('console_shell_users',{}),('core_uid',1000)):
            bad=deepcopy(receipt);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                g.validate_prerequisite_phases(bad,0,'operator-root-consoles')
        with self.assertRaises(ValueError):g.validate_prerequisite_phases(receipt,1000,None)

    def test_launch_context_requires_existing_root_binding_rights(self):
        r={'uid':0,'euid':0,'initial_namespaces':True,'threshold':1024,'cap_eff':'400','cap_bnd':'400','proc_support':True}
        g.launch_context_guard(r)
        for key,value in (('uid',1000),('euid',1000),('cap_eff','0'),('cap_bnd','0'),
                          ('initial_namespaces',False),('uid',False),('proc_support',False)):
            bad={**r,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):g.launch_context_guard(bad)

    def test_root_profile_is_forwarded_without_authentication_or_deadline_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            io=g.LiveIO(directory,shell_users=g.OPERATOR_ROOT_SHELLS)
            with patch.object(g,'ORIGINAL_READ',return_value='ok') as read:
                io.read('plc','FIXED_READ','root-profile',123)
                self.assertEqual(read.call_args.kwargs['shell_user'],'root')
                self.assertEqual(read.call_args.kwargs['deadline'],123)
                io.read('scada','FIXED_READ','kevin-profile',123)
                self.assertNotIn('shell_user',read.call_args.kwargs)

    def test_worker_environment_output_contains_only_verified_whitelist_values(self):
        namespace={'__name__':'fixture'};exec(compile(g.DRIVES_READ_CODE,'drives-read','exec'),namespace)
        select=namespace['selected_environment'];expected={'ADDR':'10.10.1.27','LANE':'1'}
        raw=b'ADDR=10.10.1.27\0LANE=1\0SECRET_TOKEN=DO_NOT_REPORT\0PATH=/private\0'
        self.assertEqual(select(raw,expected),expected)
        for raw in (b'ADDR=DO_NOT_REPORT\0LANE=1\0',b'ADDR=10.10.1.27\0',
                    b'ADDR=10.10.1.27\0ADDR=DO_NOT_REPORT\0LANE=1\0'):
            with self.assertRaises(ValueError) as error:select(raw,expected)
            self.assertNotIn('DO_NOT_REPORT',str(error.exception))

    def test_journal_queries_share_a_consistent_readonly_wal_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'journal.sqlite3';connect=sqlite3.connect
            with connect(db) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('CREATE TABLE run_epochs(identity INTEGER)')
                writer.execute('CREATE TABLE outcomes(identity TEXT)')
                writer.execute('INSERT INTO run_epochs VALUES(1)')
            statements=[]
            class Hook(sqlite3.Connection):
                def execute(self,sql,*args):
                    statements.append(sql)
                    if sql.startswith('SELECT identity FROM outcomes'):
                        with connect(db) as w:
                            w.execute('INSERT INTO run_epochs VALUES(2)')
                            w.execute("INSERT INTO outcomes VALUES('2:2:fixture')")
                    return super().execute(sql,*args)
            def reader(filename,**kwargs):
                self.assertIn('?mode=ro',filename)
                return connect(filename,**kwargs,factory=Hook)
            code=shlex.split(g.JOURNAL)[-1].replace('/var/lib/sorter-xle/outcomes.sqlite3',str(db))
            with patch.object(sqlite3,'connect',side_effect=reader),patch('builtins.print') as output:
                exec(compile(code,'journal-read','exec'),{})
            proof=json.loads(output.call_args.args[0])
            self.assertEqual(proof['epochs'],[1]);self.assertEqual(proof['outcomes'],[])
            self.assertIn('BEGIN',statements);self.assertIn('PRAGMA query_only=ON',statements)
            with connect(db) as now:self.assertEqual(now.execute('SELECT identity FROM run_epochs').fetchall(),[(1,),(2,)])


class XLeGateTests(unittest.TestCase):
    def test_actual_uuid_spellings_normalize_and_different_boots_or_malformed_ids_block(self):
        for proc,journal in ((BOOT_PROC,BOOT_JOURNAL),(BOOT_JOURNAL,BOOT_PROC),
                             (BOOT_PROC.upper(),BOOT_JOURNAL),(BOOT_PROC,BOOT_PROC)):
            e=xle_evidence();e['boot_id']=proc
            for row in e['journal']:row['_BOOT_ID']=journal
            self.assertTrue(g.xle_prestart_gate(e)['accepted'])
            self.assertEqual(g.normalize_boot_id(proc),BOOT_JOURNAL)
        for value in (None,'','SYNTHETIC',BOOT_PROC+'x','{'+BOOT_PROC+'}',
                      BOOT_PROC.replace('-','_',1),'a'*31,'g'*32,
                      'e41ef16b879d4d268a6eae2b13f8d117'):
            for side in ('proc','journal'):
                e=xle_evidence()
                if side=='proc':e['boot_id']=value
                else:e['journal'][0]['_BOOT_ID']=value
                with self.subTest(value=value,side=side):self.assertFalse(g.xle_prestart_gate(e)['accepted'])
        e=xle_evidence();e['journal'][0].pop('_BOOT_ID')
        self.assertFalse(g.xle_prestart_gate(e)['accepted'])

    def test_expected_retry_accepts_both_auto_restart_and_canonical_running_retry(self):
        e=xle_evidence()
        self.assertEqual(g.xle_prestart_gate(e)['status'],'EXPECTED_PLC_UNAVAILABLE_RETRY')
        for message in ('sorter-xle.service: Main process exited, code=exited, status=1/FAILURE',
                        "sorter-xle.service: Failed with result 'exit-code'.",
                        'sorter-xle.service: Scheduled restart job, restart counter is at 4.',
                        'Started sorter-xle.service - Sorter XLe route execution and durable outcome journal.'):
            e['journal'].append({'__REALTIME_TIMESTAMP':'99000000','_BOOT_ID':BOOT_JOURNAL,
                '_PID':'1','UNIT':'sorter-xle.service','MESSAGE':message})
        self.assertTrue(g.xle_prestart_gate(e)['accepted'])
        e['service']['ExecStart']='{ path=/opt/sorter-xle/venv/bin/python ; argv[]='+e['service']['ExecStart']+' ; ignore_errors=no ; }'
        self.assertTrue(g.xle_prestart_gate(e)['accepted'])
        e['service'].update(ActiveState='active',SubState='running',MainPID='18',ExecMainPID='18')
        e['current_cmdline']=shlex.join(g.XLE_ARGV)
        self.assertTrue(g.xle_prestart_gate(e)['accepted'])

    def test_unrelated_failure_start_limit_configuration_and_insufficient_evidence_block(self):
        mutations=[
            ('START_LIMIT_FAILURE',lambda e:e['service'].update(Result='start-limit-hit')),
            ('START_LIMIT_FAILURE',lambda e:e['journal'][-1].update(MESSAGE='Start request repeated too quickly.')),
            ('CONFIGURATION_ERROR',lambda e:e['service'].update(LoadState='not-found')),
            ('CONFIGURATION_ERROR',lambda e:e['service'].update(ExecMainStatus='203')),
            ('CONFIGURATION_ERROR',lambda e:e['service'].update(ExecStart='/tmp/other.py')),
            ('CONFIGURATION_ERROR',lambda e:e['service'].update(ExecStart=e['service']['ExecStart']+' --packages 5')),
            ('UNEXPECTED_FAILURE',lambda e:e['journal'][-1].update(MESSAGE='ValueError: broken configuration')),
            ('UNEXPECTED_FAILURE',lambda e:e['service'].update(ExecMainCode='2',ExecMainStatus='11')),
            ('UNEXPECTED_FAILURE',lambda e:e['service'].update(ActiveState='failed',SubState='failed')),
            ('INSUFFICIENT_EVIDENCE',lambda e:e.update(journal=[])),
            ('INSUFFICIENT_EVIDENCE',lambda e:e.update(truncated=True)),
            ('INSUFFICIENT_EVIDENCE',lambda e:e.update(journal_stderr='Permission denied')),
            ('INSUFFICIENT_EVIDENCE',lambda e:e['journal'][0].update(__REALTIME_TIMESTAMP='1')),
            ('INSUFFICIENT_EVIDENCE',lambda e:e['journal'][0].update(_BOOT_ID='OLD')),
            ('INSUFFICIENT_EVIDENCE',lambda e:e['journal'][-1].update(_PID='99')),
            ('INSUFFICIENT_EVIDENCE',lambda e:e['journal'].pop(0)),
            ('INSUFFICIENT_EVIDENCE',lambda e:e['journal'].append({
                '__REALTIME_TIMESTAMP':'99000000','_BOOT_ID':BOOT_JOURNAL,'MESSAGE':'Unknown status text'})),
            ('UNEXPECTED_FAILURE',lambda e:e['journal'].append({
                '__REALTIME_TIMESTAMP':'99000000','_BOOT_ID':BOOT_JOURNAL,'_PID':'1',
                'UNIT':'sorter-xle.service','MESSAGE':"sorter-xle.service: Failed with result 'signal'."})),
            ('UNEXPECTED_FAILURE',lambda e:e['journal'].append({
                '__REALTIME_TIMESTAMP':'99000000','_BOOT_ID':BOOT_JOURNAL,'_PID':'1',
                'UNIT':'sorter-xle.service','MESSAGE':'Unknown manager transition'}))]
        for expected,mutate in mutations:
            e=xle_evidence();mutate(e)
            with self.subTest(expected=expected,evidence=e):
                verdict=g.xle_prestart_gate(e)
                self.assertFalse(verdict['accepted']);self.assertEqual(verdict['status'],expected)
        for e in ({},None,'successful command transport'):
            self.assertFalse(g.xle_prestart_gate(e)['accepted'])


class WatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.libc=ctypes.CDLL(None)
        old=ctypes.c_int()
        assert cls.libc.prctl(37,ctypes.byref(old),0,0,0)==0
        cls.prior=old.value
        assert cls.libc.prctl(36,1,0,0,0)==0

    @classmethod
    def tearDownClass(cls):
        cls.libc.prctl(36,cls.prior,0,0,0)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def commands(self):
        p=self.root/'fake-commands.jsonl'
        return [json.loads(v)['command'] for v in p.read_text().splitlines()] if p.exists() else []

    def launch(self,budget=.2,factory=FakeIO):
        auth=authorization()
        (self.root/'started-core-identity.json').write_text(json.dumps(auth))
        ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
        p=ctx.Process(target=g.watchdog,args=(receiver,str(self.root),'test',budget,factory),
                      kwargs={'core_log':'/home/kevin/synthetic.log'})
        p.start();receiver.close()
        self.addCleanup(lambda: (p.kill(),p.join()) if p.is_alive() else p.join())
        sender.send({'stage':'startup'})
        sender.send({'stage':'owned-core','authorization':auth})
        return p,sender

    def test_independent_deadline_stops_once_without_controller_progress(self):
        p,sender=self.launch();start=time.monotonic()
        p.join(4);sender.close()
        self.assertFalse(p.is_alive());self.assertLess(time.monotonic()-start,4)
        self.assertEqual(self.commands().count(g.QUIT),1)
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(r['abort'],'PASS')
        self.assertIn('watchdog expired',r['error'])

    def test_pipe_eof_stops_once_and_cannot_be_accepted(self):
        p,sender=self.launch(3);sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(r['abort'],'PASS')
        self.assertNotEqual(r['status'],'ACCEPTED')
        self.assertEqual(self.commands().count(g.QUIT),1)
        self.assertEqual((self.root/'abort-core.log').read_bytes(),b'SYNTHETIC CORE LOG\n')
        self.assertEqual(r['log_export']['status'],'COMPLETE')

    def test_early_loss_without_ownership_exports_log_and_refuses_rpc(self):
        ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
        p=ctx.Process(target=g.watchdog,args=(receiver,str(self.root),'test',3,FakeIO),
                      kwargs={'core_log':'/home/kevin/synthetic.log'})
        p.start();receiver.close();sender.send({'stage':'startup'});sender.close();p.join(4)
        self.addCleanup(lambda:(p.kill(),p.join()) if p.is_alive() else p.join())
        self.assertFalse(p.is_alive())
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(r['abort'],'INCOMPLETE');self.assertTrue(r['operator_recovery_needed'])
        self.assertIn('EOFError',r['error']);self.assertTrue(r['ownership_errors'])
        self.assertEqual(self.commands().count(g.QUIT),0)
        self.assertEqual((self.root/'abort-core.log').read_bytes(),b'SYNTHETIC CORE LOG\n')

    def test_export_failure_retains_primary_failure_and_owned_cleanup_result(self):
        p,sender=self.launch(3,LogFailureIO);sender.send({'stage':'abort'});sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertIn('controller requested abort',r['error'])
        self.assertEqual(r['abort'],'PASS');self.assertEqual(r['status'],'INCOMPLETE')
        self.assertEqual(self.commands().count(g.QUIT),1)
        self.assertEqual(r['log_export']['status'],'INCOMPLETE')
        self.assertIn('export denied',r['log_export']['errors'][0])
        self.assertEqual(r['errors'],[])

    def test_log_export_uses_only_remaining_abort_budget(self):
        clock=Clock();io=FakeIO(self.root);original=io.json;deadlines=[]
        auth=authorization();(self.root/'started-core-identity.json').write_text(json.dumps(auth))
        messages=iter([{'stage':'startup'},{'stage':'owned-core','authorization':auth},
                       {'stage':'abort'}])
        class Receiver:
            def poll(self,seconds):return True
            def recv(self):return next(messages)
            def close(self):pass
        def json_read(vm,command,label,deadline):
            if label=='abort-owned-rpc':clock.advance(19.5)
            if label=='abort-final':clock.advance(9.7)
            if label=='abort-core-log':
                deadlines.append(deadline)
                clock.advance(deadline-clock.now)
                raise TimeoutError('SYNTHETIC export budget exhausted')
            return original(vm,command,label,deadline)
        with patch.object(g.os,'setsid'),patch.object(g.signal,'signal'), \
                patch.object(g.time,'monotonic',clock.monotonic), \
                patch.object(io,'json',side_effect=json_read),patch('builtins.print'):
            g.watchdog(Receiver(),str(self.root),'test',io_factory=lambda *a,**kw:io,
                       core_log='/home/kevin/synthetic.log')
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(deadlines,[130]);self.assertEqual(clock.now,130)
        self.assertEqual(r['abort'],'PASS');self.assertIn('controller requested abort',r['error'])
        self.assertEqual(r['log_export']['status'],'INCOMPLETE')

    def test_refused_listener_cannot_be_retried_or_claim_stopped(self):
        p,sender=self.launch(3,RefusedAbortIO);sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(self.commands().count(g.QUIT),0)
        self.assertEqual(r['abort'],'INCOMPLETE');self.assertTrue(r['operator_recovery_needed'])
        self.assertTrue(r['ownership_errors']);self.assertTrue(r['errors'])
        self.assertEqual(r['log_export']['status'],'COMPLETE')

    def test_corrupt_or_changed_durable_identity_never_authorizes_rpc(self):
        for content in ('{broken', '{}', json.dumps(authorization('OTHER'))):
            with self.subTest(content=content):
                p,sender=self.launch(3)
                (self.root/'started-core-identity.json').write_text(content)
                sender.close();p.join(4)
                self.assertFalse(p.is_alive())
                r=json.loads((self.root/'watchdog.json').read_text())
                self.assertEqual(self.commands().count(g.QUIT),0)
                self.assertEqual(r['abort'],'INCOMPLETE')
                self.assertTrue(r['operator_recovery_needed']);self.assertTrue(r['ownership_errors'])
                self.assertEqual(r['log_export']['status'],'COMPLETE')

    def test_storage_failure_cannot_bypass_quit(self):
        (self.root/'watchdog.json').mkdir()
        p,sender=self.launch(3);sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        self.assertEqual(self.commands().count(g.QUIT),1)

    def test_missing_acceptance_proof_triggers_abort(self):
        p,sender=self.launch(3);sender.send({'stage':'accept'});sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        self.assertEqual(self.commands().count(g.QUIT),1)

    def test_missing_started_identity_never_attests_complete_stop(self):
        p,sender=self.launch(3);(self.root/'started-core-identity.json').unlink()
        sender.close();p.join(4)
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(self.commands().count(g.QUIT),0)
        self.assertEqual(r['abort'],'INCOMPLETE')
        self.assertEqual(r['final_status'],'INCOMPLETE')

    def test_exact_started_pid_must_exit_even_when_name_and_ports_are_absent(self):
        p,sender=self.launch(3,ExactTargetIO);sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        self.assertEqual((self.root/'target-checks.txt').read_text().splitlines(),['check','check'])
        self.assertEqual(self.commands().count(g.QUIT),1)
        self.assertEqual(json.loads((self.root/'watchdog.json').read_text())['abort'],'PASS')

    def test_verified_success_disarms_without_stop(self):
        (self.root/'verification-PASS.json').write_text(json.dumps({'status':'PASS','token':'test'}))
        p,sender=self.launch(3);sender.send({'stage':'accept'});sender.close();p.join(4)
        self.assertFalse(p.is_alive())
        self.assertFalse((self.root/'fake-commands.jsonl').exists())
        r=json.loads((self.root/'watchdog.json').read_text())
        self.assertEqual(r['status'],'ACCEPTED');self.assertEqual(r['abort'],'NOT RUN')

    def test_watchdog_catchable_termination_still_attempts_quit(self):
        p,sender=self.launch(3)
        end=time.monotonic()+3
        while True:
            try:r=json.loads((self.root/'watchdog.json').read_text())
            except (OSError,ValueError):r={}
            if r.get('ownership_validated'):break
            self.assertLess(time.monotonic(),end);time.sleep(.02)
        p.terminate();p.join(4);sender.close()
        self.assertFalse(p.is_alive())
        self.assertEqual(self.commands().count(g.QUIT),1)

    def test_unsafe_cold_states_timeout_persistence_and_recurrence_request_the_reviewed_abort(self):
        for fault in ('nonzero_epoch','epoch2','fail_closed','occupied','master','unexpected',
                      'pre_reset_identity','timeout','persistent','recurrence','ack_recurrence'):
            with self.subTest(fault=fault):
                directory=self.root/fault;directory.mkdir()
                ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
                p=ctx.Process(target=g.watchdog,args=(receiver,str(directory),'test',3,FakeIO),
                              kwargs={'core_log':'/home/kevin/synthetic.log'})
                p.start();receiver.close()
                io=FailureHandshakeIO(directory,fault)
                c=g.Controller(io,sender,'test',plant_initial=True,epoch_initial=True)
                before=sample();before['plc']=cold_state();before['plc'].pop('startup_extra')
                after=sample();after['plc']=synchronized_state();after['plc'].pop('startup_extra')
                try:
                    with patch.object(g.typed,'capture',side_effect=[before,after]), \
                            patch.object(g.pre,'check',return_value={'status':'PASS'}), \
                            patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'), \
                            patch.object(g.time,'sleep'):
                        try:
                            c.start('/home/kevin/new.log',1000);c.complete()
                        except (ValueError,TimeoutError) as exc:c.request_abort(exc)
                        else:self.fail('unsafe fixture was accepted')
                    sender.close();p.join(4)
                    self.assertFalse(p.is_alive())
                    r=json.loads((directory/'watchdog.json').read_text())
                    self.assertEqual(r['abort'],'PASS',r)
                    self.assertNotEqual(r['status'],'ACCEPTED')
                    commands=[json.loads(v)['command'] for v in
                              (directory/'fake-commands.jsonl').read_text().splitlines()]
                    self.assertEqual(commands.count(g.QUIT),1)
                    self.assertFalse((directory/'verification-PASS.json').exists())
                    self.assertIn('primary_error',c.results)
                    if fault in ('recurrence','ack_recurrence'):
                        self.assertEqual(c.results['handshake']['status'],'PASS')
                        self.assertEqual(c.results['verification']['status'],'FAIL')
                        self.assertIsNotNone(c.ready_identity)
                    elif fault in ('pre_reset_identity','timeout','persistent'):
                        self.assertEqual(c.results['handshake']['status'],'FAIL')
                        self.assertEqual(c.results['verification']['status'],'NOT RUN')
                finally:
                    sender.close()
                    if p.is_alive():p.kill()
                    p.join(2)

    def test_abrupt_controller_death_leaves_watchdog_to_stop(self):
        harness=self.root/'controller.py'
        harness.write_text("""import json,multiprocessing,pathlib,sys,time
import m2_plc_startup_guard as g
from test_m2_plc_startup_guard import FakeIO,authorization
if __name__=='__main__':
 auth=authorization()
 pathlib.Path(sys.argv[1]+'/started-core-identity.json').write_text(json.dumps(auth))
 ctx=multiprocessing.get_context('spawn');r,w=ctx.Pipe(duplex=False)
 p=ctx.Process(target=g.watchdog,args=(r,sys.argv[1],'test',3,FakeIO),kwargs={'core_log':'/home/kevin/synthetic.log'});p.start();r.close()
 w.send({'stage':'startup'})
 w.send({'stage':'owned-core','authorization':auth})
 pathlib.Path(sys.argv[1]+'/parent-ready.json').write_text(json.dumps({'pid':p.pid}))
 while True:time.sleep(.05)
""")
        parent=subprocess.Popen([sys.executable,str(harness),str(self.root)],
            env={**os.environ,'PYTHONPATH':str(g.ROOT/'tests')},stdout=subprocess.DEVNULL)
        self.addCleanup(lambda:(parent.kill(),parent.wait()) if parent.poll() is None else parent.wait())
        end=time.monotonic()+4
        while not (self.root/'parent-ready.json').exists():
            self.assertLess(time.monotonic(),end);time.sleep(.02)
        pid=json.loads((self.root/'parent-ready.json').read_text())['pid']
        try:
            parent.kill();parent.wait(2)
            while True:
                try:r=json.loads((self.root/'watchdog.json').read_text())
                except (OSError,ValueError):r={}
                if r.get('ended_utc'):break
                self.assertLess(time.monotonic(),end);time.sleep(.02)
            self.assertEqual(r['abort'],'PASS')
            self.assertEqual(self.commands().count(g.QUIT),1)
            self.assertEqual((self.root/'abort-core.log').read_bytes(),b'SYNTHETIC CORE LOG\n')
            self.assertEqual(r['log_export']['status'],'COMPLETE')
            # Reap only this known adopted fixture child.
            while os.waitpid(pid,os.WNOHANG)==(0,0):
                self.assertLess(time.monotonic(),end);time.sleep(.02)
        finally:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            try:os.waitpid(pid,0)
            except ChildProcessError:pass


if __name__=='__main__':unittest.main()

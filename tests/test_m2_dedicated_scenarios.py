"""Selected-service scenario preparation fixtures. No guest or Modbus access."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import accumulation_scenario_control as control
import accumulation_state_snapshot as typed
import deployment_preflight as pre
import m2_plc_startup_guard as g
import run_accumulation_attempt as runner
from test_accumulation_state_snapshot import sample
from test_m2_plc_startup_guard import installed, synchronized_state


def support_payload(units,*,sinks=None,events=True,after=False,initial_baseline=False,previous_baseline=False):
    import base64
    sinks=runner.selected_log_sinks(units) if sinks is None else sinks
    states=[]
    for i,unit in enumerate(units):
        row=dict(Id=unit,ActiveState='active',SubState='running',MainPID=str(100+i),
                 NRestarts='0',ExecMainPID=str(100+i),ExecMainStartTimestampMonotonic='10',
                 InvocationID='1'*32,ExecStart='{ path='+g.XLE_EXECUTABLE+' ; argv[]='+' '.join(g.XLE_ARGV)+'; }')
        states.append('\n'.join(k+'='+row[k] for k in g.XLE_SERVICE_FIELDS))
    text='\n\n'.join(states)
    logs={};journal=[];data=b'FIXTURE application event\n'
    for i,unit in enumerate(units):
        if sinks[unit]['kind']=='journal':
            logs[unit]=dict(kind='journal',available=True,stdout_targets=['socket:[123]']*2,process_start_ticks=42)
            if events:journal.append({'_SYSTEMD_UNIT':unit,'_PID':str(100+i),'_BOOT_ID':'f41ef16b879d4d268a6eae2b13f8d117','MESSAGE':'FIXTURE application event','_SYSTEMD_INVOCATION_ID':'1'*32})
        else:
            size=len(data)*(2 if after else 1);offset=len(data) if after else 0
            def cursor(value):
                return dict(dev=1,inode=10+i,offset=len(value),anchor_offset=max(0,len(value)-256),
                            anchor_bytes=min(256,len(value)),anchor_sha256=hashlib.sha256(value[-256:]).hexdigest(),
                            interval_status='BOUNDED_OBSERVATION')
            old=cursor(data) if after else None
            if old is not None and previous_baseline:old['interval_status']='BASELINE_VALID'
            current=cursor(data*(2 if after else 1))
            if initial_baseline:current['interval_status']='BASELINE_VALID'
            logs[unit]=dict(kind='file',path=sinks[unit]['path'],available=True,status='BOUNDED_EVENT_BYTES',
                offset=offset,size_before=size,size_after=size,dev=1,inode=10+i,process_start_ticks=42,
                bytes_base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),
                cursor=current,truncated=False,stable=True,
                previous_witness_base64=base64.b64encode(data).decode() if after else None,
                next_witness_base64=base64.b64encode(data*(2 if after else 1)).decode(),
                continuity=dict(status='BOUNDED_ANCHOR_MATCH' if old else 'INITIAL_SNAPSHOT',previous_cursor=old,
                    previous_anchor_sha256=old['anchor_sha256'] if old else None),
                history_completeness='UNPROVEN')
    return dict(boot_id='f41ef16b-879d-4d26-8a6e-ae2b13f8d117',units=units,first=text,second=text,
                sockets='\n'.join('FIXTURE ONLY pid='+str(100+i)+',fd=3' for i in range(len(units))),journal='\n'.join(json.dumps(v) for v in journal),
                log_contract=sinks,application_logs=logs,
                observed_utc='2026-10-27T12:00:00+00:00',initial_baseline=initial_baseline)


def worker():
    spec=importlib.util.spec_from_file_location('dedicated_worker_fixture',runner.ROOT/'live_xle_vm_three_lane.py')
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'pymodbus.client':SimpleNamespace(ModbusTcpClient=object),
          'live_xle_multi':SimpleNamespace(holding=None,coils=None,set_coil=None,set_register=None)}):
        spec.loader.exec_module(module)
    return module


class DedicatedScenarioTests(unittest.TestCase):
    def test_existing_scanner_stream_proves_seed_one_no_decision_without_plan_change(self):
        tree=ast.parse((g.SELECTED/'devices/scanner.py').read_text())
        stream=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Stream')
        namespace={};exec(compile(ast.Module(body=[stream],type_ignores=[]),'<selected-scanner-Stream>','exec'),namespace)
        config=worker().scenario_config('recirculation')
        dest=namespace['Stream'](1103515245,12345,7919);dest.seed(config['seed'])
        fail=namespace['Stream'](1103515245,12345,6282);fail.seed(config['seed'])
        barcode=(dest.next(9)+1)*1000+1
        plan=json.loads((g.SELECTED/'services/xle_vm_sort_plan.json').read_text())
        self.assertEqual(barcode,1001);self.assertEqual(fail.next(1000),793)
        self.assertNotIn(str(barcode),plan['barcodes'])
        self.assertEqual(config['trailer_counters'],[0]*9)

    def test_drive_and_backpressure_require_validated_hold_and_no_false_load(self):
        m=worker();block=[0]*36;zone=[0]*23
        for i in range(3):
            block[i*12+4]=2;zone[i*5+1:i*5+4]=[4,3,10];zone[15+i]=1
        self.assertTrue(m.hold_proven('backpressure',block,zone,[0]*9,None))
        for change in ('quality','motion','reason','dwell','loaded','missing-slot'):
            b=block[:];z=zone[:];c=[0]*9
            if change=='quality':z[15]=0
            elif change=='motion':z[1]=1
            elif change=='reason':z[2]=1
            elif change=='dwell':z[3]=9
            elif change=='loaded':c[1]=1
            else:b[4]=0
            with self.subTest(change=change):self.assertFalse(m.hold_proven('backpressure',b,z,c,None))
        block[16]=block[28]=0
        self.assertTrue(m.hold_proven('drive_stop',block,zone,[0]*9,0))
        self.assertFalse(m.hold_proven('drive_stop',block,zone,[0]*9,1))

    def test_sustained_hold_rechecks_every_sample_and_rejects_transient_clear_or_load(self):
        m=worker();block=[0]*36;zone=[0]*23
        for i in range(3):block[i*12+4]=2;zone[i*5+1:i*5+4]=[4,3,10];zone[15+i]=1
        h,ready=m.observe_hold(None,'backpressure',block,zone,[0]*9,None,[False]*3,0)
        self.assertFalse(ready)
        h,ready=m.observe_hold(h,'backpressure',block,zone,[0]*9,None,[False]*3,1)
        self.assertFalse(ready)
        h,ready=m.observe_hold(h,'backpressure',block,zone,[0]*9,None,[False]*3,3)
        self.assertTrue(ready);self.assertEqual(len(h['samples']),3)
        for bad in ('motion','load','enable'):
            z=zone[:];c=[0]*9;e=[False]*3
            if bad=='motion':z[1]=1
            elif bad=='load':c[1]=1
            else:e[0]=True
            with self.subTest(bad=bad),self.assertRaisesRegex(AssertionError,'proof lost'):
                m.observe_hold(deepcopy(h),'backpressure',block,z,c,None,e,4)
        block[16]=block[28]=0
        stopped,_=m.observe_hold(None,'drive_stop',block,zone,[0]*9,0,[False],0)
        with self.assertRaisesRegex(AssertionError,'proof lost'):
            m.observe_hold(stopped,'drive_stop',block,zone,[0]*9,1,[False],1)

    def test_exact_service_socket_journal_command_and_fail_closed_access_and_parser(self):
        units=['sorter-xle.service'];p=support_payload(units,events=False)
        code=shlex.split(runner.scenario_support_command(units,'2026-10-27T12:00:00+00:00'))[-1]
        outputs=iter([p['first'],p['sockets'],p['journal'],p['second']]);out=io.StringIO()
        def proc_text(path,*args,**kwargs):
            return '100 (fixture) '+' '.join(['S']+['0']*18+['42']) if str(path).endswith('/stat') else p['boot_id']
        with patch('subprocess.run',side_effect=lambda *a,**k:SimpleNamespace(stdout=next(outputs),stderr='')) as run, \
                patch.object(Path,'read_text',proc_text),patch('os.readlink',return_value='socket:[123]'),redirect_stdout(out):
            exec(compile(code,'<exact-service-support>','exec'),{})
        payload=json.loads(out.getvalue());self.assertEqual(runner.scenario_support_check(payload,units)['journal_records'],0)
        self.assertTrue(all(call.kwargs['timeout']==3 for call in run.call_args_list))
        for change in ('permission','duplicate','socket','restart'):
            q=deepcopy(p)
            if change=='duplicate':q['first']+='\n\n'+p['first'];q['second']=q['first']
            elif change=='socket':q['sockets']=''
            elif change=='restart':q['second']=q['first'].replace('NRestarts=0','NRestarts=1')
            else:
                with patch('subprocess.run',return_value=SimpleNamespace(stdout='',stderr='Permission denied')):
                    with self.assertRaisesRegex(RuntimeError,'Permission denied'):exec(compile(code,'<denied-service-support>','exec'),{})
                continue
            with self.subTest(change=change),self.assertRaises(ValueError):runner.scenario_support_check(q,units)

    def test_all_four_terminal_outcome_contracts_and_journal_binding(self):
        m=worker()
        for case in m.CASES:
            cfg=m.scenario_config(case);rows={};outcomes=[]
            for lane,(barcode,dest) in cfg['expected'].items():
                package=f'l{lane}-2-3-{lane}-{lane}'
                row=dict(package_id=package,lane=lane,token=lane,serial=lane,barcode=barcode,
                         destination=dest,actual=dest,state=5 if dest else 6,reason=0 if dest else 1,
                         scanner_sequence=1,scan_tick=10,accept_tick=20 if dest else 0,
                         divert_tick=30,command_id=1 if dest else 0)
                rows[package]=row
                outcomes.append({'identity':f'2:3:{lane}:{lane}:{lane}',
                    'payload':{'package_id':package,'actual_trailer':dest,'state':'loaded' if dest else 'recirculated'}})
            result=dict(rows=rows,epoch=2,scanner_nonce=3)
            journal=dict(integrity='ok',epoch=2,nonce=3,outcome_count=len(rows),outcomes=outcomes)
            with self.subTest(case=case):
                m.validate_outcomes(rows,cfg);runner.verify_dedicated_journal(result,journal)
                bad=deepcopy(rows);next(iter(bad.values()))['reason']=9
                with self.assertRaises(AssertionError):m.validate_outcomes(bad,cfg)
                bad=deepcopy(journal);bad['outcomes'][0]['identity']='2:4:1:1:1'
                with self.assertRaises(ValueError):runner.verify_dedicated_journal(result,bad)

    def test_failure_after_first_write_runs_original_operator_restoration(self):
        m=worker();writes=[];plc=SimpleNamespace(connect=lambda:True,close=lambda:writes.append(('close',)))
        def regs(client,address,count=1):
            return {200:[120]*11,247:[137],249:[24115],530+4:[0],542+4:[0],647+4:[0],
                    591:[0],788:[0],748:[0,0,0]}[address]
        def coils(client,address,count=1):return [True]*7 if address==881 else [False]*count
        def register(client,address,value):
            writes.append(('register',address,value))
            if address==247 and value==1:raise RuntimeError('FIXTURE FIRST MUTATION FAILURE')
        with patch.object(m,'ModbusTcpClient',return_value=plc),patch.object(m,'holding',side_effect=regs), \
                patch.object(m,'coils',side_effect=coils),patch.object(m,'set_register',side_effect=register), \
                patch.object(m,'set_coil',side_effect=lambda c,a,v:writes.append(('coil',a,v))):
            with self.assertRaisesRegex(RuntimeError,'FIRST MUTATION FAILURE'):m.main('recirculation')
        self.assertIn(('coil',880,False),writes)
        for address in (914,915,918,919,920):self.assertIn(('coil',address,False),writes)
        for address in range(881,888):self.assertIn(('coil',address,True),writes)
        self.assertIn(('register',247,137),writes);self.assertEqual(writes[-1],('close',))

    def test_package_transport_has_independent_worker_bound_and_separate_liveness_budget(self):
        argv=runner.dedicated_argv('recirculation','FIXTURE ONLY')
        self.assertEqual(argv[:4],['/usr/bin/timeout','--signal=TERM','--kill-after=3s','300s'])
        with tempfile.TemporaryDirectory() as tmp,patch.object(g,'ORIGINAL_READ',return_value='FIXTURE') as read:
            g.LiveIO(tmp).read('scada',shlex.join(argv),'dedicated-package',12345)
        self.assertEqual(read.call_args.kwargs['timeout'],305)
        self.assertEqual(read.call_args.kwargs['deadline'],12345)
        self.assertTrue(callable(read.call_args.kwargs['trace']))


class DedicatedHostPathTests(unittest.TestCase):
    def test_real_idle_helper_slow_first_read_cannot_satisfy_observation_span(self):
        state=synchronized_state();clock=[0];calls=[]
        class IO:
            def json(self,vm,command,label,deadline):
                calls.append(label)
                if command==g.JOURNAL:return {'epochs':[2],'outcomes':[]}
                clock[0]+=11 if len(calls)==1 else 5
                value=deepcopy(state);value['startup_extra']['590']+=len(calls)
                return value
        with patch.object(runner.time,'monotonic',side_effect=lambda:clock[0]),patch.object(runner.time,'sleep'):
            with self.assertRaisesRegex(TimeoutError,'observation deadline'):
                runner.observe_healthy_idle(IO(),10,state,{'epochs':[2],'outcomes':[]})
            self.assertEqual(calls,['healthy-idle-0'])
            clock[0]=0;calls.clear()
            result=runner.observe_healthy_idle(IO(),40,state,{'epochs':[2],'outcomes':[]})
        self.assertEqual(result['observation_seconds'],10)
        self.assertEqual([v['observed_monotonic'] for v in result['samples']],[11,16,21])
        self.assertEqual(calls[-1],'healthy-idle-journal-after')

    def setup_receipts(self,root,case,startup_status='PASS'):
        startup=installed(True);core=startup['cores'][0];core.update(uid=0,euid=0)
        path=root/'startup.json';path.write_text(json.dumps({'status':startup_status,'terminal':True,'selected_source_commit':g.COMMIT,
            **{stage:{'status':'PASS'} for stage in ('startup','listener','handshake','verification')},
            'final_state':{'status':'PASS','endpoint':'CORE_RUNNING_MASTER_OFF','core':core},
            'abort':{'watchdog':{'status':'ACCEPTED','terminal':True,'ownership_validated':True}}}))
        now=datetime.now(timezone.utc).isoformat();sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
        receipt={'status':'PASS','scenario':case,'approval_id':'fixture_m2_approval','approved_utc':now,
            'selected_source':g.COMMIT,'total_seconds':540,'host_runner_sha256':sha(runner.__file__),
            'scenario_source_sha256':sha(runner.ROOT/'live_xle_vm_three_lane.py'),'guard_sha256':sha(g.__file__),
            'preflight_sha256':sha(pre.__file__),'allowed_remote_recovery':True,'startup_results':str(path),
            'startup_results_sha256':sha(path),'scenario_control_sha256':sha(control.__file__),
            'checks':{key:'PASS' for key in runner.DEDICATED_CHECKS},
            'check_evidence':{key:[{'path':str(path),'sha256':sha(path)}] for key in runner.DEDICATED_CHECKS}}
        source=root/'reviewed-fixture.json';source.write_text(json.dumps(receipt))
        return source,now,startup

    def test_incomplete_startup_blocks_before_spending_or_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt,now,_=self.setup_receipts(root,'recirculation','INCOMPLETE')
            with patch.object(control,'claim_host_approval') as claim,patch.object(g,'LiveIO') as transport:
                with self.assertRaisesRegex(ValueError,'complete startup acceptance'):
                    runner.run_dedicated('recirculation',receipt,root/'new','fixture_m2_approval',now)
                claim.assert_not_called();transport.assert_not_called()

    def test_contradictory_startup_result_resealed_by_checksum_is_not_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt,now,_=self.setup_receipts(root,'normal_sorting')
            record=json.loads(receipt.read_text());path=Path(record['startup_results'])
            original=json.loads(path.read_text())
            for change in ('verification','watchdog','uid'):
                startup=deepcopy(original)
                if change=='verification':startup['verification']['status']='NOT RUN'
                elif change=='watchdog':startup['abort']['watchdog']['status']='INCOMPLETE'
                else:startup['final_state']['core']['uid']=1000
                path.write_text(json.dumps(startup));record['startup_results_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                record['check_evidence']={key:[{'path':str(path),'sha256':record['startup_results_sha256']}] for key in runner.DEDICATED_CHECKS}
                receipt.write_text(json.dumps(record))
                with self.subTest(change=change),patch.object(control,'claim_host_approval') as claim,patch.object(g,'LiveIO') as transport:
                    with self.assertRaisesRegex(ValueError,'complete startup acceptance'):
                        runner.run_dedicated('normal_sorting',receipt,root/'new','fixture_m2_approval',now)
                    claim.assert_not_called();transport.assert_not_called()
            r=json.loads(receipt.read_text());r['status']='NOT REVIEWED';receipt.write_text(json.dumps(r))
            with patch.object(control,'claim_host_approval') as claim,patch.object(g,'LiveIO') as transport:
                with self.assertRaisesRegex(ValueError,'separately reviewed'):
                    runner.run_dedicated('normal_sorting',receipt,root/'new','fixture_m2_approval',now)
                claim.assert_not_called();transport.assert_not_called()

    def host_fixture(self,case,worker_error=False,export_error=False,idle_change=None,support_denied=False,initial_support_incomplete=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt,now,installed_core=self.setup_receipts(root,case)
            states=[synchronized_state()]
            calls=[];original_io=g.LiveIO
            class FixtureIO(original_io):
                def json(self,vm,command,label,deadline):
                    calls.append(label)
                    if label.startswith('support-'):
                        if support_denied:raise PermissionError('FIXTURE journal/socket evidence denied')
                        manifest=json.loads((g.SELECTED/'deploy/deployment_manifest.json').read_text())
                        units=sorted({u for e in manifest['components'] if e['guest']==vm for u in e['associated_service'] if u!='nftables.service'})
                        after=label.startswith('support-after')
                        return support_payload(units,after=after,initial_baseline=not after,previous_baseline=after)
                    if label.startswith('healthy-idle-') and label!='healthy-idle-journal-after' and idle_change=='identity':
                        states[0]['run_identity']['scanner_nonce']+=1
                    if command==g.PLC_INSPECT:return installed_core
                    if command==g.STATE:
                        result=deepcopy(states[0]);result['startup_extra']['590']+=len(calls);return result
                    if command==g.JOURNAL:return {'integrity':[['ok']],'epochs':[1,2]+([3] if states[0]['run_identity']['epoch']==[3,0] else []),'outcomes':['FIXTURE unexpected outcome'] if idle_change=='journal' and label=='healthy-idle-journal-after' else []}
                    if command==g.HANDSHAKE:
                        states[0]['run_identity'].update(epoch=[3,0],scanner_nonce=3,plant_epoch_nonce=[3,0,3])
                        states[0]['startup_extra'].update({'555':3,'556':0,'565':3,'566':0})
                        return {'status':'PASS','journal_authority':'dedicated_sorter_xle_service'}
                    if command==g.FRESHNESS:return [dict(poll_ms=n,age_ms=1,identity=24115,hmi={
                        'connected':True,'run':False,'chute':{'poll_fresh':True},'plant_fault':0,
                        'scanner_state':0,'scanner_fault_mask':0,'scanner_ack_mask':7,'zone_fault':0,'inducted':0}) for n in (100,101)]
                    raise AssertionError(label)
                def read(self,vm,command,label,deadline):
                    calls.append(label)
                    if worker_error:raise pre.PreflightFailure('fixture_missing_prompt',vm,label)
                    raise AssertionError('package fixture never returns fabricated operating PASS')
            def snapshot():
                s=sample();s['plc']=deepcopy(states[0]);s['plc'].pop('startup_extra');return s
            original_hashes=runner.evidence_hashes;hash_calls=[]
            clock=[0]
            def tick():clock[0]+=.6;return clock[0]
            def hashes(path):
                hash_calls.append(path)
                if export_error and len(hash_calls)==1:raise OSError('FIXTURE sealing failed')
                return original_hashes(path)
            original_support=runner.collect_scenario_support
            def support(*args,**kwargs):
                views=original_support(*args,**kwargs)
                if initial_support_incomplete and args[-1]=='support-before':
                    views['scada']['application_attribution']='INCOMPLETE'
                    views['scada']['interval_start_readiness']='INCOMPLETE'
                return views
            with patch.object(control,'APPROVAL_LEDGER',root/'fixture-ledger.jsonl'), \
                    patch.object(control,'RESTORATION_GATE',root/'fixture-gate.json'), \
                    patch.object(g,'LiveIO',FixtureIO),patch.object(pre,'check',return_value={'status':'PASS'}), \
                    patch.object(typed,'capture',side_effect=snapshot),patch.object(runner.time,'sleep'), \
                    patch.object(runner,'evidence_hashes',side_effect=hashes),patch.object(runner.time,'monotonic',side_effect=tick), \
                    patch.object(runner,'collect_scenario_support',side_effect=support):
                result=runner.run_dedicated(case,receipt,root/'new','fixture_m2_approval',now)
                gate=json.loads((root/'fixture-gate.json').read_text())
                self.assertTrue((root/'new/SHA256SUMS').is_file())
                with self.assertRaisesRegex(ValueError,'restoration gate unresolved' if worker_error or export_error or idle_change or support_denied else 'already used'):
                    runner.run_dedicated(case,receipt,root/'second','fixture_m2_approval',now)
            return result,gate,calls

    def test_idle_and_documented_recovery_use_existing_typed_handshake_and_leave_capture_separate(self):
        for case in ('healthy_idle','documented_recovery'):
            with self.subTest(case=case):
                result,gate,calls=self.host_fixture(case)
                self.assertEqual(result['status'],'PASS',result)
                self.assertEqual(gate['status'],'PASS')
                self.assertEqual(result['capture_completeness'],'NOT RUN')
                self.assertEqual(result['typed_postflight'],'PASS')
                self.assertNotIn('dedicated-package',calls)

    def test_unknown_worker_completion_blocks_recovery_and_next_run_and_seals_failure(self):
        result,gate,calls=self.host_fixture('recirculation',worker_error=True)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(gate['status'],'INCOMPLETE')
        self.assertEqual(result['functional_outcome'],'INCOMPLETE')
        self.assertIn('fixture_missing_prompt',result['errors']['primary'])
        self.assertNotIn('post-scenario-recovery',calls)
        self.assertEqual(result['capture_completeness'],'NOT RUN')

    def test_failed_sealing_closes_gate_and_preserves_primary_outcome_separately(self):
        result,gate,calls=self.host_fixture('recirculation',worker_error=True,export_error=True)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(gate['status'],'INCOMPLETE')
        self.assertIn('fixture_missing_prompt',result['errors']['primary'])
        self.assertIn('sealing failed',result['errors']['evidence_export'])
        self.assertNotIn('post-scenario-recovery',calls)

    def test_idle_epoch_nonce_or_journal_change_cannot_pass_endpoint_comparison(self):
        for change in ('identity','journal'):
            with self.subTest(change=change):
                result,gate,calls=self.host_fixture('healthy_idle',idle_change=change)
                self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(gate['status'],'INCOMPLETE')
                self.assertIn('healthy-idle',result['errors']['primary'])

    def test_missing_service_log_or_socket_access_blocks_before_any_package_and_stays_not_run(self):
        result,gate,calls=self.host_fixture('recirculation',support_denied=True)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(result['functional_outcome'],'NOT RUN')
        self.assertEqual(result['plc_recovery'],'NOT RUN');self.assertEqual(gate['status'],'INCOMPLETE')
        self.assertNotIn('dedicated-package',calls);self.assertNotIn('post-scenario-recovery',calls)

    def test_later_supported_read_cannot_replace_prior_incomplete_attribution(self):
        result,gate,_=self.host_fixture('healthy_idle',initial_support_incomplete=True)
        self.assertEqual(result['functional_outcome'],'PASS')
        self.assertEqual(result['application_attribution'],'INCOMPLETE')

    def test_explicit_file_baseline_is_positioning_only_and_does_not_poison_valid_interval(self):
        result,gate,_=self.host_fixture('healthy_idle')
        self.assertEqual(result['functional_outcome'],'PASS')
        self.assertEqual(result['application_attribution'],'SUPPORTED_WITH_LIMITS')
        self.assertEqual(gate['status'],'PASS')

    def test_unreviewed_detailed_check_or_tampered_reference_blocks_before_operation_lock_or_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,now,_=self.setup_receipts(root,'normal_sorting');original=json.loads(path.read_text())
            for failure in ('check','reference'):
                record=deepcopy(original)
                if failure=='check':record['checks']['console_coordination']='NOT REVIEWED'
                else:record['check_evidence']['console_coordination'][0]['sha256']='0'*64
                path.write_text(json.dumps(record))
                with patch.object(control,'claim_host_approval') as claim,patch.object(g,'LiveIO') as transport:
                    with self.assertRaises(ValueError):runner.run_dedicated('normal_sorting',path,root/'new','fixture_m2_approval',now)
                    claim.assert_not_called();transport.assert_not_called()

    def test_kernel_operation_lock_blocks_another_process_and_pass_waits_for_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);gate=root/'gate.json';local=root/'local';local.mkdir()
            with patch.object(control,'RESTORATION_GATE',gate):
                control.write_run_gate(local,'FIXTURE','normal_sorting','PENDING')
                with control.serialized_operation():
                    code='import accumulation_scenario_control as c;from pathlib import Path;c.RESTORATION_GATE=Path('+repr(str(gate))+');\ntry:\n with c.serialized_operation():raise RuntimeError("UNEXPECTED LOCK ACQUISITION")\nexcept ValueError:print("BLOCKED")'
                    child=subprocess.run([sys.executable,'-B','-c',code],env={**__import__('os').environ,'PYTHONPATH':str(runner.ROOT)},capture_output=True,text=True,timeout=5)
                    self.assertEqual((child.returncode,child.stdout.strip()),(0,'BLOCKED'),child.stderr)
                    def broken_seal():
                        self.assertEqual(json.loads(gate.read_text())['status'],'PENDING')
                        raise OSError('FIXTURE SEALED PASS REFUSED')
                    with self.assertRaises(OSError):control.write_run_gate(local,'FIXTURE','normal_sorting','PASS',seal=broken_seal)
                    self.assertEqual(json.loads(gate.read_text())['status'],'PENDING')
                    control.write_run_gate(local,'FIXTURE','normal_sorting','PASS',seal=lambda:None)
                    self.assertEqual(json.loads(gate.read_text())['status'],'PASS')


if __name__=='__main__':unittest.main()

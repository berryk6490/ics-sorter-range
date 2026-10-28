"""Consolidated read-only final-verification fixtures. No guest or network IO."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

import deployment_preflight as pre
import m2_plc_startup_guard as g


def hmi():
    return {'connected':True,'chute':{'poll_fresh':True},'run':False,'plant_fault':0,
            'scanner_state':0,'scanner_fault_mask':0,'scanner_ack_mask':7,'zone_fault':0,'inducted':0}


class CompletionTests(unittest.TestCase):
    def test_exact_freshness_collector_uses_installed_endpoint_paths_and_two_samples(self):
        endpoint=[];paths=[];polls=iter([100000,100001])
        class Node:
            def __init__(self,path):self.path=path
            async def read_data_value(self):
                value=next(polls) if self.path[-1].endswith(':PlcPollMonotonicMs') else 24115
                return SimpleNamespace(StatusCode=SimpleNamespace(is_good=lambda:True),Value=SimpleNamespace(Value=value))
        class Client:
            def __init__(self,url,timeout):endpoint.append(url);self.nodes=SimpleNamespace(objects=self)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def get_namespace_index(self,uri):
                self_test.assertEqual(uri,'urn:sorter:level2');return 2
            async def get_child(self,path):paths.append(path);return Node(path)
        self_test=self
        code=shlex.split(g.FRESHNESS)[-1];stream=io.StringIO()
        with patch.dict(sys.modules,{'asyncua':SimpleNamespace(Client=Client)}), \
                patch('urllib.request.urlopen',side_effect=lambda *a,**kw:io.StringIO(json.dumps(hmi()))) as http, \
                patch('time.monotonic_ns',return_value=100010000000), \
                patch('asyncio.sleep',new_callable=AsyncMock) as sleep,redirect_stdout(stream):
            exec(compile(code,'<exact-reviewed-freshness>','exec'),{})
        rows=json.loads(stream.getvalue())
        self.assertEqual(endpoint,['opc.tcp://10.10.2.10:4840/sorter/'])
        self.assertEqual(http.call_count,2);sleep.assert_awaited_once_with(.5)
        self.assertEqual(paths,[['2:Sorter','2:Process','2:Status','2:PlcPollMonotonicMs'],
                                ['2:Sorter','2:Run','2:ProgramHash']]*2)
        self.assertEqual(g.freshness_check(rows)['status'],'PASS')
        # Pin compatibility to the selected server's real source interface.
        tree=ast.parse((g.ROOT/'scada/opcua_server.py').read_text())
        actual=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
                    any(isinstance(t,ast.Name) and t.id=='ENDPOINT' for t in n.targets))
        self.assertEqual(endpoint[0],actual)

    def test_stale_nonadvancing_bad_identity_or_unsafe_hmi_never_accepts(self):
        good=[dict(poll_ms=n,age_ms=10,identity=24115,hmi=hmi()) for n in (100,101)]
        for mutate in [lambda r:r[1].update(age_ms=1501),lambda r:r[1].update(poll_ms=100),
                       lambda r:r[1].update(identity=24113),lambda r:r[1].update(age_ms=False),
                       lambda r:r[1]['hmi'].update(run=True),lambda r:r[1]['hmi'].update(connected=False),
                       lambda r:r[1]['hmi']['chute'].update(poll_fresh=False),
                       lambda r:r[1]['hmi'].update(scanner_ack_mask=0)]:
            rows=deepcopy(good);mutate(rows)
            with self.subTest(rows=rows),self.assertRaises(ValueError):g.freshness_check(rows)

    def test_timing_change_requires_explicit_receipt_and_preserves_abort_age_bound(self):
        self.assertEqual(g.BOUNDS['verification'],270);self.assertEqual(g.WATCHDOG_SECONDS,600)
        self.assertEqual(sum(v for k,v in g.BOUNDS.items() if k!='prestart'),570)
        self.assertEqual(g.timing_policy()['owned_rpc_max_launch_age_seconds'],630)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'NOT-REVIEWED.json'
            path.write_text(json.dumps({'launch_failure_cleanup':g.owned.LAUNCH_CLEANUP_POLICY}))
            argv=['guard','run','--core-log','/home/kevin/REVIEW_ONLY.log','--evidence',tmp+'/not-created',
                  '--approval-id','REVIEW_ONLY','--expected-core-uid','0','--reviewed-prerequisites',str(path),
                  '--allow-launch-failure-child-cleanup']
            with patch.object(sys,'argv',argv),patch.object(g,'ORIGINAL_READ') as read, \
                    patch.object(g,'claim_host_approval') as claim:
                with self.assertRaisesRegex(ValueError,'timing policy'):g.main()
                read.assert_not_called();claim.assert_not_called()

    def test_complete_verification_preserves_xle_separation_exports_before_freshness_and_accepts_last(self):
        from test_m2_plc_startup_guard import HandshakeIO, Sender, sample, synchronized_state
        with tempfile.TemporaryDirectory() as tmp:
            events=[];transport=HandshakeIO(Path(tmp));sender=Sender()
            c=g.Controller(transport,sender,'test',plant_initial=True,epoch_initial=True)
            c.start('/home/kevin/SYNTHETIC.log',1000)
            before=sample();before['plc']=deepcopy(transport.s);before['plc'].pop('startup_extra')
            after=sample();after['plc']=synchronized_state();after['plc'].pop('startup_extra')
            original_json=transport.json;original_read=transport.read
            def read(vm,command,label,deadline):
                events.append(label);return original_read(vm,command,label,deadline)
            def read_json(vm,command,label,deadline):
                events.append(label);return original_json(vm,command,label,deadline)
            def sleep(seconds):events.append(('sleep',seconds))
            with patch.object(transport,'read',side_effect=read),patch.object(transport,'json',side_effect=read_json), \
                    patch.object(g.typed,'capture',side_effect=[before,after]), \
                    patch.object(g.pre,'check',return_value={'status':'PASS'}) as preflight, \
                    patch.object(g.pre,'guest_read'),patch.object(g.typed,'guest_read'),patch.object(g.time,'sleep',side_effect=sleep):
                c.complete()
            preflight.assert_called_once_with(live=True,batch=True)
            self.assertLess(events.index('xle-stable-first'),events.index(('sleep',2)))
            self.assertLess(events.index(('sleep',2)),events.index('xle-stable-second'))
            self.assertEqual(events[-1],'ua-hmi-freshness')
            self.assertLess(events.index('core-log-export'),events.index('ua-hmi-freshness'))
            self.assertEqual(sender.messages[-1],{'stage':'accept'})
            self.assertEqual(json.loads((Path(tmp)/'verification-PASS.json').read_text())['token'],'test')


class BatchPreflightTests(unittest.TestCase):
    def test_exact_batched_guest_collector_parses_real_systemctl_multiblock_shape(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.write_bytes(b'fixture')
            code=shlex.split(pre.batch_read_command([str(source)],['a.service','b.service']))[-1]
            stdout='Id=a.service\nActiveState=active\n\nId=b.service\nActiveState=active\n'
            output=io.StringIO()
            with patch('subprocess.run',return_value=SimpleNamespace(stdout=stdout,stderr='')) as run,redirect_stdout(output):
                exec(compile(code,'<exact-batched-guest-read>','exec'),{})
            self.assertEqual(pre.batch_payload(output.getvalue()),{
                'hashes':{str(source):hashlib.sha256(b'fixture').hexdigest()},
                'services':{'a.service':'active','b.service':'active'}})
            self.assertEqual(run.call_args.kwargs['timeout'],3)
            for broken in (stdout.replace('Id=b.service','Id=a.service'),stdout+'Id=c.service\nActiveState=active\n',
                           stdout.replace('ActiveState=active','ActiveState=active\nActiveState=active',1)):
                with self.subTest(raw=broken),patch('subprocess.run',return_value=SimpleNamespace(stdout=broken,stderr='')):
                    with self.assertRaises(ValueError):exec(compile(code,'<invalid-batched-read>','exec'),{})
            with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('systemctl',3)):
                with self.assertRaises(subprocess.TimeoutExpired):exec(compile(code,'<timed-out-batched-read>','exec'),{})

    def run_fixture(self,fault=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entries=[]
            for number,vm in enumerate(('scada','scada','xle')):
                name=f'source{number}';(root/name).write_text(name)
                entries.append({'repository_source':name,'guest':vm,'canonical_destination':'/fixture/'+name,
                                'sha256':hashlib.sha256(name.encode()).hexdigest(),
                                'verification_command':'UNUSED PER-ENTRY', 'associated_service':[vm+'.service']})
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'components':entries,'plc_program_identity':24115}))
            calls=[]
            def read(vm,command,label):
                calls.append((vm,command,label))
                if label=='plc_program_identity':return json.dumps({'identity':24115})
                code=shlex.split(command)[-1];tree=ast.parse(code)
                declared={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                          and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('paths','units')}
                expected=[e for e in entries if e['guest']==vm]
                self.assertEqual(declared['paths'],sorted(e['canonical_destination'] for e in expected))
                self.assertEqual(declared['units'],[vm+'.service'])
                result={'hashes':{e['canonical_destination']:e['sha256'] for e in expected},'services':{vm+'.service':'active'}}
                if fault=='missing':result['hashes'].pop(next(iter(result['hashes'])))
                if fault=='wrong':result['hashes'][next(iter(result['hashes']))]='0'*64
                if fault=='service':result['services'][vm+'.service']='inactive'
                if fault=='extra':result['services']['other.service']='active'
                payload=json.dumps(result)
                if fault=='duplicate':payload='{"hashes":{},'+payload[1:]
                out=command+'\nM2_PREFLIGHT_V1='+payload
                if fault=='ambiguous':out+='\nM2_PREFLIGHT_V1='+payload
                return out
            with patch.object(pre,'ROOT',root),patch.object(pre,'MANIFEST',manifest), \
                    patch.object(pre,'_vm_running') as vms,patch.object(pre,'guest_read',side_effect=read),redirect_stdout(io.StringIO()):
                result=pre.check(live=True,batch=True)
            self.assertEqual(result['source_and_guest_hashes'],3)
            self.assertEqual(result['transport_mode'],'batched-per-guest')
            self.assertEqual(len(calls),3)  # two complete guest groups plus unchanged PLC identity read
            self.assertEqual(vms.call_count,2)
            return result

    def test_all_hashes_services_and_identity_remain_checked_with_fewer_round_trips(self):
        self.assertEqual(self.run_fixture()['status'],'PASS')

    def test_missing_wrong_duplicate_ambiguous_or_inactive_evidence_cannot_pass(self):
        for fault in ('missing','wrong','service','extra','duplicate','ambiguous'):
            with self.subTest(fault=fault),self.assertRaises(pre.PreflightFailure):self.run_fixture(fault)


if __name__=='__main__':unittest.main()

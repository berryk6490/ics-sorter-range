"""Synthetic archive/controller/export integration. No tcpdump/guest/privilege IO."""
import ast
import base64
import ctypes
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import baseline_capture as capture
import m2_guest_capture as guest


WRITER=r'''
import json,os,signal,struct,sys,time
path=sys.argv[1]
with open(path,'wb') as f:f.write(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1))
print(json.dumps(dict(fixture=True,pid=os.getpid())),flush=True)
stop=False
def end(*args):
 global stop
 stop=True
signal.signal(signal.SIGINT,end)
signal.signal(signal.SIGTERM,end)
if sys.argv[2]=='descendant':
 pid=os.fork()
 if pid:
  print(json.dumps(dict(fixture=True,descendant=pid)),flush=True)
  raise SystemExit(0)
 os.setsid();signal.signal(signal.SIGINT,signal.SIG_IGN)
while not stop:time.sleep(.01)
sys.stderr.write('0 packets captured\n0 packets received by filter\n0 packets dropped by kernel\n')
'''


class GuestCaptureTests(unittest.TestCase):
    def setUp(self):
        if os.getuid() not in (0,1000):self.skipTest('fixture requires one existing reviewed UID context')
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.children=[]
        self.libc=ctypes.CDLL(None);flag=ctypes.c_int()
        self.libc.prctl(37,ctypes.byref(flag),0,0,0);self.old_subreaper=flag.value
        capture.enable_subreaper()

    def tearDown(self):
        for process in self.children:
            if process.poll() is None:process.kill()
            process.wait(timeout=3)
        # Reap only fixture-owned adopted supervisor/capture identities.
        for path in self.root.glob('*/supervision.json'):
            report=json.loads(path.read_text())
            for row in [report['supervisor_identity'],*report.get('processes',[])]:
                current=capture.process_identity(row['pid'])
                if capture.same_process(row,current) and current['state']!='Z':
                    fd=os.pidfd_open(row['pid'])
                    try:
                        if capture.same_process(row,capture.process_identity(row['pid'])):
                            signal.pidfd_send_signal(fd,signal.SIGKILL)
                    finally:os.close(fd)
                try:os.waitpid(row['pid'],os.WNOHANG)
                except (ChildProcessError,ProcessLookupError):pass
        self.libc.prctl(36,self.old_subreaper,0,0,0);self.tmp.cleanup()

    def fixture(self,mode='normal',seconds=.8):
        location='scada' if os.getuid()==1000 else 'xle';name=location+'-local'
        sources={n:Path(guest.__file__).with_name(n).read_bytes() for n in guest.MODULES}
        original=sources['baseline_capture.py'].decode();tree=ast.parse(original)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='capture_argv')
        lines=original.splitlines(True)
        replacement='def capture_argv(point, output, packets):\n    return [sys.executable,"-u","-c",'+repr(WRITER)+',str(output),'+repr(mode)+']\n'
        sources['baseline_capture.py']=(''.join(lines[:fn.lineno-1])+replacement+''.join(lines[fn.end_lineno:])).encode()
        if mode=='artifact':
            text=sources['baseline_capture.py'].decode()
            text=text.replace('def save(path, data):\n',
                "def save(path, data):\n    if path.name=='supervision.json' and data.get('points') and not data.get('terminal'):\n        point=next(iter(data['points']))\n        output=path.parent/(point+'.stdout')\n        if output.exists() and output.stat().st_size:raise OSError('SYNTHETIC supervisor artifact denied after capture child start')\n")
            sources['baseline_capture.py']=text.encode()
        sources['m2_guest_capture.py']=sources['m2_guest_capture.py'].replace(b'import socket\n',b'import socket\n# SYNTHETIC HOST FIXTURE ONLY; not the production archive.\nsocket.gethostname=lambda:'+repr(location).encode()+b'\n')
        archive=self.root/('runtime-'+mode+'.pyz');info=guest.build(archive,sources=sources)
        config=capture.load_config(capture.DEFAULT_CONFIG)
        identity={'kind':'SYNTHETIC_FIXTURE','selected_source_commit':config['selected_source_commit'],
                  'manifest_sha256':config['selected_manifest_sha256']}
        contract=guest.plan(config,[name],location,str(self.root/('capture-'+mode)),'fixture-'+mode,
                            str(archive),info['archive_sha256'],os.getuid(),seconds,100,4096,.15,
                            host_identity=identity,module_pins=info['module_sha256'])
        contract.update(status='PASS',checks={k:'PASS' for k in guest.REVIEW_KEYS},guest_contract_path=str(self.root/(mode+'-contract.json')))
        path=Path(contract['guest_contract_path']);path.write_bytes(guest.encode(contract));pin=guest.digest(path.read_bytes())
        return contract,path,pin,archive

    def start(self,contract,path,pin,archive):
        argv=guest.runtime_argv(archive,contract['bundle_sha256'],['guest-run','--contract',str(path),'--contract-sha256',pin,
              '--bundle-sha256',contract['bundle_sha256']],interpreter=sys.executable)
        out=(self.root/'controller.stdout').open('wb');err=(self.root/'controller.stderr').open('wb')
        process=subprocess.Popen(argv,stdout=out,stderr=err);out.close();err.close();self.children.append(process)
        return process

    def wait_report(self,contract,predicate=lambda r:True,timeout=4):
        until=time.monotonic()+timeout;path=Path(contract['output'])/'supervision.json'
        while time.monotonic()<until:
            try:report=json.loads(path.read_text())
            except (FileNotFoundError,ValueError):time.sleep(.01);continue
            if predicate(report):return report
            time.sleep(.01)
        self.fail('bounded synthetic supervision receipt missing: '+(self.root/'controller.stderr').read_text())

    def assert_gone(self,report):
        for row in report.get('processes',[]):
            current=capture.process_identity(row['pid'])
            self.assertFalse(capture.same_process(row,current) and current['state']!='Z',row)

    def read_fixture(self,vm,command,label,**options):
        # Synthetic subprocess and representative canonical frames for EVERY
        # read, including inventories/chunks. No serial transport is invoked.
        entered=time.monotonic();argv=shlex.split(command);argv[0]=sys.executable
        result=subprocess.run(argv,capture_output=True,text=True,timeout=4)
        if result.returncode:raise RuntimeError('FIXTURE read failed: '+result.stderr)
        self.serial_sequence=getattr(self,'serial_sequence',0)+1;seq=self.serial_sequence
        for probe in (False,True):
            nonce=format(seq*2+int(probe),'032x');alive='PREFLIGHT_ALIVE_'+format(seq,'032x')
            intended="printf '"+alive+"\\n'" if probe else command
            nominal=20 if probe else options.get('timeout',20)
            args=[sys.executable,str(Path(guest.__file__).with_name('serial_command.py')),vm,intended,
                  '--timeout',str(nominal),'--nonce',nonce,'--json']
            reply=dict(nonce=nonce,output=alive+'\n' if probe else result.stdout,command_rc=0,
                       completion_marker=True,prompt=True,fixture=True)
            if options['shell_user']=='root':
                args+=['--shell-user','root'];reply.update(shell_user='root',context_uid=0)
            allowance=min(nominal+10,options['deadline']-(time.monotonic() if probe else entered))-.5
            args+=['--total-timeout',str(allowance)]
            options['trace'](dict(vm=vm,label='liveness after '+label if probe else label,probe=probe,
                                  argv=args,exit_status=0,stdout=json.dumps(reply),stderr='',fixture=True))
        return result.stdout

    def test_self_contained_guest_controller_reexec_seal_and_chunked_export(self):
        c,p,h,a=self.fixture()
        collected=guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)
        self.assertEqual(collected['status'],'COMPLETE',collected)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        result=json.loads((Path(c['output'])/'collection.json').read_text())
        self.assertEqual(result['execution_location'],c['location']);self.assertEqual(result['status'],'COMPLETE')
        with self.assertRaisesRegex(ValueError,'guest namespace'):capture.verify(c['output'])
        transport=self.root/'transport-call/capture-transport.json'
        exported=guest.export_capture(p,h,transport,self.root/'export',seconds=30,read=self.read_fixture)
        self.assertEqual(exported['status'],'COMPLETE',exported)
        self.assertEqual(guest.verify_export(self.root/'export')['status'],'COMPLETE')
        self.assertEqual((self.root/'export/raw/collection.json').read_bytes(),(Path(c['output'])/'collection.json').read_bytes())

    def test_export_rejects_short_tampered_mismatched_or_failed_chunks_and_unsafe_names(self):
        c,p,h,a=self.fixture();record=guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)
        self.assertEqual(record['status'],'COMPLETE',record)
        for fault in ('short','tamper-rehashed','offset','read-error','changed-final-inventory'):
            def read(vm,command,label,**kwargs):
                value=self.read_fixture(vm,command,label,**kwargs)
                if 'guest-chunk' in command:
                    if fault=='read-error':raise OSError('FIXTURE chunk transport failed')
                    row=json.loads(value)
                    if fault=='short':
                        data=base64.b64decode(row['data_base64'])[:-1];row['data_base64']=base64.b64encode(data).decode();row['chunk_sha256']=guest.digest(data)
                    if fault=='tamper-rehashed':
                        data=bytearray(base64.b64decode(row['data_base64']));data[0]^=1
                        row['data_base64']=base64.b64encode(data).decode();row['chunk_sha256']=guest.digest(data)
                    if fault=='offset':row['offset']+=1
                    return json.dumps(row)
                if fault=='changed-final-inventory' and label=='m2-export-final-inventory':
                    row=json.loads(value);row['files']['collection.json']['pin']['size']+=1;return json.dumps(row)
                return value
            with self.subTest(fault=fault):
                exported=guest.export_capture(p,h,self.root/'transport-call/capture-transport.json',self.root/('export-'+fault),seconds=30,read=read)
                self.assertEqual(exported['status'],'INCOMPLETE');self.assertTrue(exported['errors'])
        with self.assertRaisesRegex(ValueError,'not a contracted'):
            guest.chunk(c,dict(name='../secret',pin={},directory_pin={},offset=0,length=1))

    def test_receipts_with_recomputed_checksums_still_need_nonce_context_completion_and_liveness(self):
        c,p,h,a=self.fixture();collected=guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)
        self.assertEqual(collected['status'],'COMPLETE',collected)
        record=json.loads((self.root/'transport-call/capture-transport.json').read_text())
        for fault in ('missing','nonce','prompt','numeric-argv','liveness'):
            row=deepcopy(record)
            if fault=='missing':row['traces'].pop()
            elif fault=='numeric-argv':row['traces'][1]['argv'][-1]=True
            else:
                data=json.loads(row['traces'][1]['stdout'])
                if fault=='nonce':data['nonce']='a'*32
                if fault=='prompt':data['prompt']=False
                if fault=='liveness':data['output']='stale'
                row['traces'][1]['stdout']=json.dumps(data)
            with self.subTest(fault=fault),self.assertRaises(ValueError):guest.check_collection_transport(c,h,row)

    def test_wrapper_death_and_surviving_descendant_are_bounded_without_touching_bystander(self):
        for mode in ('normal','descendant'):
            with self.subTest(mode=mode):
                c,p,h,a=self.fixture(mode);proc=self.start(c,p,h,a)
                report=self.wait_report(c,lambda r:bool(r.get('points')))
                bystander=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);self.children.append(bystander)
                if mode=='normal':
                    row=next(iter(report['points'].values()))['wrapper_identity'];fd=os.pidfd_open(row['pid'])
                    try:
                        self.assertTrue(capture.same_process(row,capture.process_identity(row['pid'])))
                        signal.pidfd_send_signal(fd,signal.SIGKILL)
                    finally:os.close(fd)
                self.assertNotEqual(proc.wait(timeout=4),0)
                terminal=self.wait_report(c,lambda r:r.get('terminal'));self.assertEqual(terminal['cleanup'],'PASS',terminal)
                self.assert_gone(terminal);self.assertIsNone(bystander.poll())

    def test_controller_sigterm_and_sigkill_preserve_bound_and_missing_terminal_never_passes(self):
        for sig,mode in ((signal.SIGTERM,'term'),(signal.SIGKILL,'kill')):
            with self.subTest(sig=sig):
                c,p,h,a=self.fixture(mode);proc=self.start(c,p,h,a)
                self.wait_report(c,lambda r:bool(r.get('points')));proc.send_signal(sig);proc.wait(timeout=4)
                report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
                self.assertEqual(report['cleanup'],'PASS',report)
                if sig==signal.SIGKILL:self.assertFalse(json.loads((Path(c['output'])/'collection.json').read_text())['terminal'])
                with self.assertRaises((ValueError,FileNotFoundError)):capture.verify(c['output'])

    def test_transport_loss_cannot_accept_collection_and_guest_bound_still_cleans(self):
        c,p,h,a=self.fixture()
        def lost(*args,**kwargs):
            self.start(c,p,h,a)
            raise RuntimeError('FIXTURE transport lost; no terminal/liveness receipt')
        record=guest.remote_collect(p,h,self.root/'host-transport',read=lost)
        self.assertEqual(record['status'],'INCOMPLETE')
        self.children[-1].wait(timeout=4);report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        with self.assertRaisesRegex(ValueError,'transport required'):
            guest.export_capture(p,h,self.root/'host-transport/capture-transport.json',self.root/'forbidden-export',read=self.read_fixture)

    def test_inert_plan_does_not_enable_drives_or_create_capture_and_pins_reject_tampering(self):
        c,p,h,a=self.fixture()
        self.assertFalse(Path(c['output']).exists())
        cfg=capture.load_config(capture.DEFAULT_CONFIG)
        with self.assertRaisesRegex(ValueError,'do not enable drives'):
            guest.plan(cfg,['drives-local'],'drives',str(self.root/'never'),'fixture-drives',str(a),c['bundle_sha256'],0,host_identity=c['source_identity'])
        p.write_bytes(p.read_bytes()+b' ')
        proc=self.start(c,p,h,a);self.assertNotEqual(proc.wait(timeout=4),0)
        self.assertFalse(Path(c['output']).exists())

    def test_bootstrap_checks_bytes_before_running_archive_main_and_rejects_path_escape(self):
        import zipfile
        archive=self.root/'untrusted.pyz';marker=self.root/'must-not-execute'
        fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream,zipfile.ZipFile(stream,'w') as z:
            z.writestr('__main__.py','from pathlib import Path\nPath('+repr(str(marker))+').write_text("UNEXPECTED")\n')
        result=subprocess.run(guest.runtime_argv(archive,'0'*64,[],interpreter=sys.executable),capture_output=True,text=True,timeout=3)
        self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())
        config=capture.load_config(capture.DEFAULT_CONFIG)
        config['points']['../secret']=dict(config['points']['scada-local'])
        with self.assertRaisesRegex(ValueError,'invalid point'):
            guest.plan(config,['../secret'],'scada',str(self.root/'out'),'fixture-path',str(archive),'0'*64,1000,host_identity={})

    def test_artifact_failure_still_cleans_and_failure_only_export_never_becomes_pass(self):
        c,p,h,a=self.fixture('artifact');proc=self.start(c,p,h,a)
        self.assertNotEqual(proc.wait(timeout=4),0)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assertEqual(report['cleanup'],'PASS',report);self.assert_gone(report)
        raw=json.loads((Path(c['output'])/'collection.json').read_text())
        self.assertEqual(raw['status'],'INCOMPLETE');self.assertIn('artifact denied',raw['errors']['primary'])
        transport=self.root/'failed-transport.json';transport.write_bytes(guest.encode(dict(status='INCOMPLETE',contract_sha256=h,errors=['FIXTURE original collection failed'])))
        exported=guest.export_capture(p,h,transport,self.root/'failure-export',seconds=30,failure_evidence=True,read=self.read_fixture)
        self.assertEqual(exported['status'],'INCOMPLETE');self.assertEqual(exported['transfer_status'],'COMPLETE',exported)
        self.assertEqual(exported['original_collection_errors'],['FIXTURE original collection failed'])
        self.assertEqual((self.root/'failure-export/raw/collection.json').read_bytes(),(Path(c['output'])/'collection.json').read_bytes())

    def test_fixed_export_budget_and_storage_failure_preserve_errors_without_another_guest_read(self):
        c,p,h,a=self.fixture();record=guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)
        self.assertEqual(record['status'],'COMPLETE',record)
        now=[0];calls=[]
        def slow(vm,command,label,**kwargs):
            calls.append(label);out=self.read_fixture(vm,command,label,**kwargs);now[0]=2;return out
        with patch.object(guest.time,'monotonic',side_effect=lambda:now[0]):
            result=guest.export_capture(p,h,self.root/'transport-call/capture-transport.json',self.root/'expired',seconds=1,read=slow)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(calls,['m2-export-inventory'])
        self.assertIn('deadline',result['errors'][0])
        original=capture.save
        def denied(path,data):
            if Path(path).name=='export.json':raise OSError('FIXTURE host receipt storage denied')
            return original(path,data)
        with patch.object(capture,'save',side_effect=denied):
            result=guest.export_capture(p,h,self.root/'transport-call/capture-transport.json',self.root/'storage-denied',seconds=30,read=self.read_fixture)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertTrue(result['artifact_errors'])

    def test_legacy_python_failure_note_fallback_keeps_original_error(self):
        class LegacyError(OSError):add_note=None
        with patch.object(capture.json,'dump',side_effect=LegacyError('FIXTURE original serialization error')), \
                patch.object(Path,'unlink',side_effect=OSError('FIXTURE temporary cleanup error')):
            with self.assertRaises(LegacyError) as raised:capture.save(self.root/'receipt.json',{})
        self.assertIn('original serialization',str(raised.exception))
        self.assertIn('temporary cleanup',raised.exception.__notes__[0])

    def test_guest_postflight_expected_identity_is_bound_to_canonical_supervision(self):
        c,p,h,a=self.fixture();proc=self.start(c,p,h,a);self.assertEqual(proc.wait(timeout=4),0)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        proof=guest.inventory(c,h)['postflight']
        self.assertEqual(capture.verify(c['output'],guest_postflight=proof)['status'],'COMPLETE')
        live=deepcopy(proof);live['checks'][0]['observed']={**live['checks'][0]['expected'],'state':'S'}
        for fault in ('live','uid','boot_id','missing','extra','duplicate','malformed','wrong-observed-pid'):
            bad=deepcopy(live)
            if fault=='uid':bad['checks'][0]['expected']['uid']+=1
            if fault=='boot_id':bad['checks'][0]['expected']['boot_id']='0'*8+'-'+'0'*4+'-'+'0'*4+'-'+'0'*4+'-'+'0'*12
            if fault=='missing':bad['checks'].pop()
            if fault=='extra':bad['checks'].append(deepcopy(bad['checks'][0]))
            if fault=='duplicate':bad['checks'][-1]=deepcopy(bad['checks'][0])
            if fault=='malformed':bad['checks'][0]['expected']['uid']=True
            if fault=='wrong-observed-pid':bad['checks'][0]['observed']['pid']+=1
            with self.subTest(fault=fault),self.assertRaises(ValueError):capture.verify(c['output'],guest_postflight=bad)

    def reseal_export(self,root):
        (root/'EXPORT_SHA256SUMS').write_text(''.join(capture.sha(f)+'  '+str(f.relative_to(root))+'\n'
            for f in sorted(root.rglob('*')) if f.is_file() and f.name!='EXPORT_SHA256SUMS'))

    def test_export_requires_each_canonical_pair_at_runtime_and_preserves_collection_outcome(self):
        c,p,h,a=self.fixture();transport=self.root/'transport-call/capture-transport.json'
        collected=guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)
        self.assertEqual(collected['status'],'COMPLETE',collected)
        for fault in ('zero','nonce','wrong-result'):
            def read(vm,command,label,**options):
                trace=options['trace']
                def altered(row):
                    if fault=='zero':return
                    if not row['probe']:
                        data=json.loads(row['stdout'])
                        if fault=='nonce':data['nonce']='f'*32
                        if fault=='wrong-result':data['output']=json.dumps({'unrelated':'SYNTHETIC'})
                        row={**row,'stdout':json.dumps(data)}
                    trace(row)
                return self.read_fixture(vm,command,label,**{**options,'trace':altered})
            with self.subTest(fault=fault):
                result=guest.export_capture(p,h,transport,self.root/('unproven-'+fault),seconds=30,read=read)
                self.assertEqual(result['status'],'INCOMPLETE');self.assertTrue(result['errors'])
                self.assertEqual(result['original_collection_errors'],[])
        self.assertEqual(json.loads(transport.read_text())['status'],'COMPLETE')

    def test_offline_export_rejects_resealed_missing_foreign_stale_reordered_and_mismatched_read_proofs(self):
        c,p,h,a=self.fixture();transport=self.root/'transport-call/capture-transport.json'
        self.assertEqual(guest.remote_collect(p,h,self.root/'transport-call',read=self.read_fixture)['status'],'COMPLETE')
        original=self.root/'canonical-export'
        self.assertEqual(guest.export_capture(p,h,transport,original,seconds=30,read=self.read_fixture)['status'],'COMPLETE')
        self.assertEqual(guest.verify_export(original)['status'],'COMPLETE')
        faults=('zero','missing','duplicate','reorder','foreign-guest','foreign-interpreter','wrong-user','numeric-argv',
                'nonce','reused-nonce','prompt','liveness','wrong-result','budget','shared-budget','expired','stale-time',
                'partial-pair','request','result-digest','read-order','extra-read','inventory-postflight',
                'omit-chunk-pair','stale-utc','resealed-missing-inventory-field')
        for fault in faults:
            dest=self.root/('resealed-'+fault);shutil.copytree(original,dest)
            report=json.loads((dest/'export.json').read_text());row=report['traces'][0]
            if fault=='zero':report['traces']=[]
            elif fault=='missing':report['traces'].pop()
            elif fault=='duplicate':report['traces'][2]=deepcopy(row)
            elif fault=='reorder':report['traces'][0],report['traces'][1]=report['traces'][1],report['traces'][0]
            elif fault=='foreign-guest':row['vm']='xle' if c['location']!='xle' else 'scada'
            elif fault=='foreign-interpreter':row['argv'][0]='/not-selected/python'
            elif fault=='numeric-argv':row['argv'][-1]=True
            elif fault=='reused-nonce':row['argv'][row['argv'].index('--nonce')+1]=json.loads((dest/'capture-transport.json').read_text())['traces'][0]['argv'][7]
            elif fault in ('wrong-user','nonce','prompt','liveness','wrong-result'):
                target=report['traces'][1] if fault=='liveness' else row;data=json.loads(target['stdout'])
                if fault=='wrong-user':data.update(shell_user='root' if c['uid'] else 'kevin',context_uid=0 if c['uid'] else 1000)
                if fault=='nonce':data['nonce']='f'*32
                if fault=='prompt':data['prompt']=False
                if fault=='liveness':data['output']='stale marker'
                if fault=='wrong-result':data['output']=json.dumps({'unrelated':True})
                target['stdout']=json.dumps(data)
            elif fault=='budget':row['argv'][-1]='31'
            elif fault=='shared-budget':report['reads'][0]['deadline_monotonic']+=1
            elif fault=='expired':report['ended_monotonic']=report['deadline_monotonic']
            elif fault=='stale-time':row['recorded_monotonic']=report['started_monotonic']-1
            elif fault=='partial-pair':report['reads'][0]['trace_count']=1
            elif fault=='request':report['reads'][1]['request']['offset']+=1
            elif fault=='result-digest':report['reads'][0]['result_sha256']='0'*64
            elif fault=='read-order':report['reads'][1],report['reads'][2]=report['reads'][2],report['reads'][1]
            elif fault=='extra-read':report['reads'].append(deepcopy(report['reads'][-1]))
            elif fault=='inventory-postflight':report['final_inventory']['postflight']['checks'][0]['expected']['uid']+=1
            elif fault=='omit-chunk-pair':
                report['reads'].pop(1);del report['traces'][2:4]
                for i,meta in enumerate(report['reads']):meta['trace_offset']=2*i
            elif fault=='stale-utc':row['recorded_utc']='2000-01-01T00:00:00+00:00'
            elif fault=='resealed-missing-inventory-field':
                del report['inventory']['errors']
                data=json.loads(row['stdout']);data['output']=json.dumps(report['inventory'])
                row['stdout']=json.dumps(data)
                report['reads'][0]['result_sha256']=guest.digest(guest.encode(report['inventory']))
            (dest/'export.json').write_bytes(guest.encode(report));self.reseal_export(dest)
            with self.subTest(fault=fault),self.assertRaises(ValueError):guest.verify_export(dest)


if __name__=='__main__':unittest.main()

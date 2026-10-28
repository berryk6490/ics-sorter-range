"""Subprocess integration fixtures only: no tcpdump, sockets, sudo or guests."""
import ctypes
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

import baseline_capture as capture

FAKE = r'''
import json,os,pathlib,signal,sys,time
from baseline_capture import process_identity
output,log,mode=map(str,sys.argv[1:4])
pathlib.Path(output).write_bytes(bytes.fromhex('d4c3b2a10200040000000000000000000000040001000000'))
def record(role):
    fd=os.open(log,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    os.write(fd,(json.dumps({'role':role,**process_identity(os.getpid())})+'\n').encode());os.close(fd)
def stop(*args):
    print('0 packets captured\n0 packets received by filter\n0 packets dropped by kernel',file=sys.stderr,flush=True)
    sys.exit(0)
if mode in ('ignore','descendant'):
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT,stop)
record('capture')
if mode=='supervision_write':
    path=pathlib.Path(output).parent/'supervision.json'
    path.unlink(missing_ok=True);path.mkdir()
if mode in ('descendant','survivor'):
    pid=os.fork()
    if pid==0:
        os.setsid()
        signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN)
        record('escaped_descendant')
        while True: time.sleep(.01)
while True: time.sleep(.01)
'''
HARNESS = r'''
import json,pathlib,sys,time
import baseline_capture as c
root=pathlib.Path(sys.argv[1]);mode=sys.argv[2];fault=sys.argv[3]
c.identity=lambda config:{'kind':'SYNTHETIC FIXTURE, NOT DEPLOYED'}
c.capture_argv=lambda point,path,packets:[sys.executable,str(root/'fake.py'),str(path),str(root/'pids.jsonl'),mode]
if mode=='survivor':
    # This case requires a genuinely successful direct wrapper, independently
    # of the installed timeout variant's signal/exit behavior. Other cases
    # retain the real timeout; the independent supervisor still bounds this one.
    c.bounded_argv=lambda point,path,packets,seconds,grace:c.capture_argv(point,path,packets)
original_save,original_sha,original_summary=c.save,c.sha,c.summarize
original_finish=c.finish_supervisor
if fault in ('proc_before_launch','proc_after_launch'):
    original_popen=c.subprocess.Popen
    def popen(argv,*args,**kwargs):
        if len(argv)>2 and argv[2]=='_supervise':
            argv=[sys.executable,str(root/'supervisor-fixture.py'),*argv[3:],fault]
        return original_popen(argv,*args,**kwargs)
    c.subprocess.Popen=popen
def finish(*args):
    errors=original_finish(*args)
    return errors+(['SYNTHETIC cleanup reporting failure'] if fault=='artifact_cleanup' else [])
c.finish_supervisor=finish
failed=False
def save(path,data):
    global failed
    if fault in ('after_launch','artifact_cleanup') and path.name=='collection.json' and data.get('supervisor_identity') and not failed:
        until=time.monotonic()+3
        while not (root/'pids.jsonl').exists() and time.monotonic()<until: time.sleep(.01)
        failed=True
        raise OSError('SYNTHETIC artifact write failure after launch')
    if fault=='terminal_write' and path.name=='collection.json' and data.get('terminal'):
        raise OSError('SYNTHETIC terminal artifact unavailable')
    if fault=='sealing_write' and path.name=='sealing.json':
        raise OSError('SYNTHETIC seal write unavailable')
    return original_save(path,data)
def sha(path):
    if fault=='hash' and path.name.endswith('.pcap'):
        raise OSError('SYNTHETIC hashing unavailable')
    return original_sha(path)
def summary(*args,**kwargs):
    if fault=='analysis': raise RuntimeError('SYNTHETIC analysis failure')
    return original_summary(*args,**kwargs)
c.save,c.sha,c.summarize=save,sha,summary
result=c.collect(c.load_config(c.DEFAULT_CONFIG),['fw-l2'],root/'evidence','SYNTHETIC-integration',.8,5000,8192,.15,'FIXTURE CLOCK')
print(json.dumps(result),flush=True)
sys.exit(0 if result['status']=='COMPLETE' else 2)
'''
SUPERVISOR_FIXTURE = r'''
import json,os,sys
import baseline_capture as c
original_children,original_scan=c.process_children,c.OwnedProcesses.scan
denied=sys.argv[3]=='proc_before_launch'
def children(pid):
    if denied and pid==os.getpid():
        raise PermissionError('SYNTHETIC supervisor children access denied')
    return original_children(pid)
def scan(self):
    global denied
    original_scan(self)
    # Deny subsequent discovery only once wrapper, fake capture and escaped
    # child have real, established ownership and retained pidfds.
    if sys.argv[3]=='proc_after_launch' and len(self.records)>=3:
        denied=True
c.process_children,c.OwnedProcesses.scan=children,scan
sys.exit(c.supervisor(json.loads(open(sys.argv[1]).read()),int(sys.argv[2])))
'''


class ProcessIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Adopt/reap only our test controller's orphan supervisor after SIGKILL.
        cls.libc = ctypes.CDLL(None, use_errno=True)
        prior = ctypes.c_int()
        assert cls.libc.prctl(37, ctypes.byref(prior), 0, 0, 0) == 0
        cls.prior = prior.value
        capture.enable_subreaper()

    @classmethod
    def tearDownClass(cls):
        cls.libc.prctl(36, cls.prior, 0, 0, 0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'fake.py').write_text(FAKE)
        (self.root/'controller.py').write_text(HARNESS)
        (self.root/'supervisor-fixture.py').write_text(SUPERVISOR_FIXTURE)
        self.controller = None
        self.identities = []
        self.streams = []

    def rows(self):
        p = self.root/'pids.jsonl'
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def report(self):
        try:
            return json.loads((self.root/'evidence/supervision.json').read_text())
        except (OSError,ValueError):
            return {}

    def send(self, row, sig):
        if not capture.same_process(row,capture.process_identity(row['pid'])):
            return
        fd=os.pidfd_open(row['pid'])
        try:
            if capture.same_process(row,capture.process_identity(row['pid'])):
                signal.pidfd_send_signal(fd,sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)

    def start(self,mode='normal',fault='none'):
        out=(self.root/'controller.stdout').open('w');err=(self.root/'controller.stderr').open('w')
        self.streams += [out,err]
        self.controller=subprocess.Popen([sys.executable,str(self.root/'controller.py'),str(self.root),mode,fault],
            stdout=out,stderr=err,env={**os.environ,'PYTHONPATH':str(capture.ROOT/'tests')},start_new_session=True)
        self.identities.append(capture.process_identity(self.controller.pid))
        return self.controller

    def ready(self,minimum=1):
        until=time.monotonic()+5
        while time.monotonic()<until:
            report=self.report()
            if len(self.rows())>=minimum and report.get('points'):
                self.identities.append(report['supervisor_identity'])
                return report
            time.sleep(.01)
        self.fail(f'fixture not ready: {self.report()} {(self.root/"controller.stderr").read_text()}')

    def finish(self):
        started=time.monotonic()
        self.controller.wait(timeout=6)
        until=time.monotonic()+4
        while time.monotonic()<until:
            report=self.report()
            if report.get('terminal'):
                self.identities.append(report['supervisor_identity'])
                self.assertLess(time.monotonic()-started,2.5,
                    'fixture .8s collection + .15s grace + 1s reap + scheduling margin exceeded')
                return report
            time.sleep(.01)
        self.fail('independent supervisor failed to finish within bound')

    def assert_no_captures(self,report,cleanup='PASS'):
        self.assertEqual(report['cleanup'],cleanup,report)
        self.assertEqual(report['remaining_processes'],[])
        if cleanup == 'PASS':
            self.assertEqual(report['unreaped_child_pids'],[])
        rows=self.rows()+report['processes']
        until=time.monotonic()+2
        while time.monotonic()<until:
            live=[r for r in rows if capture.same_process(r,capture.process_identity(r['pid']))]
            if not live: break
            time.sleep(.01)
        self.assertEqual(live,[],f'capture descendants remain: {live}')
        self.assertTrue(any(r['role']=='capture' for r in self.rows()))

    def result(self):
        return json.loads((self.root/'controller.stdout').read_text().splitlines()[-1])

    def tearDown(self):
        # Failure of an assertion must also leave no fixture process behind.
        report=self.report()
        rows=self.rows()+report.get('processes',[])+self.identities
        if report.get('supervisor_identity'): rows.append(report['supervisor_identity'])
        for row in rows:
            if row: self.send(row,signal.SIGKILL)
        if self.controller:
            self.controller.wait(timeout=3)
        for row in rows:
            if not row: continue
            try: os.waitpid(row['pid'],os.WNOHANG)
            except ChildProcessError: pass
        for stream in self.streams: stream.close()
        self.temp.cleanup()

    def test_wrapper_sigkill_with_escaped_descendant(self):
        bystander=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        self.addCleanup(lambda: (bystander.terminate(),bystander.wait(timeout=2)))
        self.start('descendant'); report=self.ready(2)
        self.send(report['points']['fw-l2']['wrapper_identity'],signal.SIGKILL)
        end=self.finish(); self.assert_no_captures(end)
        self.assertEqual(self.controller.returncode,2)
        result=self.result()
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertIn('wrapper exited',result['errors']['primary'])
        self.assertNotEqual(self.rows()[0]['sid'],self.rows()[1]['sid'])
        self.assertIsNone(bystander.poll(),'unrelated process must not be signalled')

    def test_controller_sigterm_cleans_and_seals_terminal_failure(self):
        self.start('descendant'); self.ready(2)
        self.controller.terminate()
        end=self.finish(); self.assert_no_captures(end)
        result=self.result()
        self.assertEqual(self.controller.returncode,2)
        self.assertTrue(result['terminal'])
        self.assertIn('controller signal 15',result['errors']['primary'])
        self.assertEqual(result['sealing']['status'],'COMPLETE')
        with self.assertRaises(ValueError): capture.verify(self.root/'evidence')

    def test_abrupt_controller_sigkill_cannot_finalize_receipt(self):
        self.start('descendant'); self.ready(2)
        self.controller.kill()
        end=self.finish(); self.assert_no_captures(end)
        self.assertEqual(self.controller.returncode,-9)
        self.assertIn('controller_lost',end['primary_error'])
        raw=json.loads((self.root/'evidence/collection.json').read_text())
        self.assertFalse(raw['terminal'])
        with self.assertRaises((ValueError,OSError)): capture.verify(self.root/'evidence')
        self.assertEqual(capture.inspect(self.root/'evidence')['status'],'INCOMPLETE')

    def test_wrapper_and_controller_loss_still_bounded(self):
        self.start('descendant'); report=self.ready(2)
        self.send(report['points']['fw-l2']['wrapper_identity'],signal.SIGKILL)
        self.controller.kill()
        end=self.finish(); self.assert_no_captures(end)
        self.assertEqual(end['status'],'INCOMPLETE')

    def test_successful_wrapper_cannot_hide_surviving_child(self):
        self.start('survivor'); self.ready(2)
        end=self.finish(); self.assert_no_captures(end)
        self.assertEqual(self.controller.returncode,2)
        self.assertEqual(end['points']['fw-l2']['exit_status'],0)
        self.assertIn('surviving_descendants',end['primary_error'],
            (end,(self.root/'evidence/fw-l2.stderr').read_text()))

    def test_artifact_write_after_launch_keeps_original_failure(self):
        self.start('descendant','after_launch'); self.ready(2)
        end=self.finish(); self.assert_no_captures(end)
        result=self.result()
        self.assertIn('artifact write failure after launch',result['errors']['primary'])
        self.assertEqual(result['errors']['cleanup'],[])
        self.assertEqual(result['sealing']['status'],'COMPLETE')

    def test_analysis_failure_is_after_process_cleanup(self):
        self.start('normal','analysis'); self.ready()
        end=self.finish(); self.assert_no_captures(end)
        result=self.result()
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertIn('analysis failure',result['errors']['analysis'][0])
        self.assertEqual(result['sealing']['status'],'COMPLETE')

    def test_original_artifact_and_cleanup_errors_are_independent(self):
        self.start('descendant','artifact_cleanup');self.ready(2)
        end=self.finish();self.assert_no_captures(end)
        result=self.result()
        self.assertIn('artifact write failure after launch',result['errors']['primary'])
        self.assertIn('SYNTHETIC cleanup reporting failure',result['errors']['cleanup'])
        self.assertEqual(result['status'],'INCOMPLETE')

    def test_supervisor_artifact_failure_still_removes_capture(self):
        self.start('supervision_write')
        self.controller.wait(timeout=6)
        path=self.root/'evidence/supervisor.stdout'
        end=json.loads(path.read_text().splitlines()[-1])
        self.identities.append(end['supervisor_identity'])
        self.assert_no_captures(end)
        self.assertEqual(self.controller.returncode,2)
        self.assertTrue(self.result()['errors']['artifacts'])
        self.assertEqual(self.result()['sealing']['status'],'INCOMPLETE')

    def test_hash_failure_retains_partial_seal_without_acceptance(self):
        self.start('normal','hash'); self.ready()
        end=self.finish(); self.assert_no_captures(end)
        result=self.result()
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['sealing']['status'],'INCOMPLETE')
        self.assertTrue(result['errors']['sealing'])
        with self.assertRaises(ValueError): capture.verify(self.root/'evidence')

    def test_terminal_and_sealing_writes_cannot_become_pass(self):
        for fault in ('terminal_write','sealing_write'):
            with self.subTest(fault=fault):
                # Separate subprocess/output directory for each injected failure.
                if self.controller is not None:
                    self.tearDown(); self.setUp()
                self.start('normal',fault);self.ready()
                end=self.finish();self.assert_no_captures(end)
                self.assertEqual(self.result()['status'],'INCOMPLETE')
                with self.assertRaises((ValueError,OSError)): capture.verify(self.root/'evidence')

    def test_process_visibility_preflight_denial_launches_no_capture(self):
        self.start('descendant','proc_before_launch')
        end=self.finish()
        self.assertEqual(self.controller.returncode,2)
        self.assertEqual(self.rows(),[])
        self.assertEqual(end['points'],{})
        self.assertEqual(end['cleanup'],'INCOMPLETE')
        self.assertIn('PermissionError',end['discovery_errors'][0])
        result=self.result()
        self.assertTrue(result['errors']['discovery'])
        self.assertEqual(result['status'],'INCOMPLETE')

    def test_postlaunch_discovery_denial_cleans_pinned_descendants_without_pass(self):
        self.start('descendant','proc_after_launch');self.ready(2)
        end=self.finish();self.assert_no_captures(end,cleanup='INCOMPLETE')
        self.assertEqual(self.controller.returncode,2)
        self.assertIsNone(end['unreaped_child_pids'],'unreadable is not a verified empty list')
        self.assertTrue(end['discovery_errors'])
        result=self.result()
        self.assertTrue(result['errors']['discovery'])
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['sealing']['status'],'COMPLETE')
        with self.assertRaises(ValueError):capture.verify(self.root/'evidence')

    def check_documented_smoke(self,cases):
        doc=(capture.ROOT/'MILESTONE2_BASELINE.md').read_text()
        start=doc.index('```sh\n(\n')+6
        script=doc[start:doc.index('\n```',start)]
        fake=self.root/'fake-command.py'
        fake.write_text('''import json,os,pathlib,sys
stage=sys.argv[2] if sys.argv[1].endswith('baseline_capture.py') else 'summary'
with open(os.environ['FIXTURE_LOG'],'a') as out: out.write(stage+'\\n')
if stage=='collect':
 p=pathlib.Path(sys.argv[sys.argv.index('--output')+1]);p.mkdir();(p/'SHA256SUMS').write_text('FIXTURE')
print(json.dumps({'kind':'SYNTHETIC SEQUENCING FIXTURE','stage':stage}))
sys.exit(7 if stage==os.environ['FAIL_STAGE'] else 2 if stage=='inspect' else 0)
''')
        script=script.replace('/usr/bin/python3',shlex.join([sys.executable,str(fake)]))
        for stage,block_hash_output in cases:
            evidence=self.root/f'packet-{stage}-{block_hash_output}'
            if block_hash_output:
                # Controlled EISDIR failure: no permission changes, even as root.
                Path(str(evidence)+'-failure-SHA256SUMS').mkdir()
            commands=script.replace('EVIDENCE=/home/kevin/vm/sorter-evidence/m2-capture-smoke-REVIEWED',
                                    'EVIDENCE='+shlex.quote(str(evidence)))
            log=self.root/f'{stage}-{block_hash_output}-commands.log'
            result=subprocess.run(['bash','-c',commands],text=True,capture_output=True,timeout=5,
                env={**os.environ,'FIXTURE_LOG':str(log),'FAIL_STAGE':stage})
            self.assertEqual(result.returncode,7,result.stderr)
            calls=log.read_text().splitlines()
            self.assertEqual(calls,['collect','inspect'] if stage=='collect' else ['collect','verify','inspect'])
            self.assertTrue(Path(str(evidence)+'-failure.json').is_file())
            if block_hash_output:
                self.assertRegex(result.stderr,r'failure-evidence hashing rc=[1-9]\d*; hash-output/sealing rc=[1-9]\d*')
                self.assertTrue(Path(str(evidence)+'-failure-SHA256SUMS').is_dir())
            else:
                self.assertTrue(Path(str(evidence)+'-failure-SHA256SUMS').is_file())
                self.assertIn('failure-evidence hashing rc=0; hash-output/sealing rc=0',result.stderr)
            self.assertEqual(Path(str(evidence)+'-stage-exit.txt').read_text(),'7\n')
            self.assertFalse(Path(str(evidence)+'-derived-SHA256SUMS').exists())

    def test_documented_smoke_stops_analysis_and_keeps_failure_evidence(self):
        self.check_documented_smoke((('collect',False),('verify',False)))

    def test_documented_smoke_hash_output_redirection_failure(self):
        self.check_documented_smoke((('collect',True),))


if __name__=='__main__': unittest.main()

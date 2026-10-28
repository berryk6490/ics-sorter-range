"""FIXTURE ONLY: real host children, exec, pidfds and failure evidence; no guests."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import m2_owned_core as o
import m2_plc_startup_guard as g
from test_m2_owned_core import auth, TOKEN


class ValidationTests(unittest.TestCase):
    def test_root_identity_is_valid_without_coercing_uid_or_euid(self):
        a=auth()
        for row in (a['core'],a['launch']['birth']):row.update(uid=0,euid=0)
        self.assertEqual(o.validate_observation(a['launch'],a['core'],TOKEN,0),a)
        for key,value in (('uid',False),('euid',0.0),('uid','0'),('euid',1000)):
            row=deepcopy(a['launch']);row['birth'][key]=value
            with self.subTest(field=key,value=value),self.assertRaises(o.LaunchValidationError) as caught:
                o.validate_launch(row,TOKEN,0)
            failed=caught.exception.diagnostics['failed_predicates']
            self.assertTrue(any(f['predicate'].startswith('birth.'+key) for f in failed))

    def test_all_failed_predicates_include_original_observations_and_expectations(self):
        row=auth()['launch'];row['parent_pid']=False
        row['birth'].update(uid=0,euid='1000',start_ticks=0,attempt=None,boot_id='')
        row['issued_monotonic_ns']=-1
        with self.assertRaises(o.LaunchValidationError) as caught:o.validate_launch(row,TOKEN,1000)
        failed={f['predicate']:f for f in caught.exception.diagnostics['failed_predicates']}
        for name in ('birth.uid','birth.euid','birth.start_ticks.positive','birth.attempt',
                     'birth.boot_id','birth.ppid','launch.parent_pid','launch.issued_monotonic_ns'):
            self.assertIn(name,failed)
            self.assertIn('observed',failed[name]);self.assertIn('expected',failed[name])
        self.assertEqual(failed['birth.uid']['observed'],0)
        self.assertEqual(failed['birth.uid']['expected'],1000)
        self.assertEqual(failed['birth.euid']['observed_type'],'str')

    def test_wrong_required_uid_blocks_before_child_creation(self):
        with patch.object(o.subprocess,'Popen') as spawn,redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError) as caught:o.launch('/unused',TOKEN,os.getuid()+1,True)
        spawn.assert_not_called()
        self.assertEqual(caught.exception.report['cleanup']['status'],'NOT RUN')

    def test_new_cleanup_scope_requires_both_flag_and_reviewed_receipt_before_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt=Path(tmp)/'NOT-REVIEWED.json'
            for flag,value in ((False,o.LAUNCH_CLEANUP_POLICY),(True,None),(True,True)):
                receipt.write_text(json.dumps({'status':'NOT REVIEWED','launch_failure_cleanup':value}))
                argv=['guard','run','--core-log','/home/kevin/REVIEWED.log',
                      '--evidence',str(Path(tmp)/'NOT-CREATED'),'--approval-id','m2-startup-REVIEWED',
                      '--expected-core-uid','0','--execution-context','operator-root-consoles',
                      '--reviewed-prerequisites',str(receipt)]
                if flag:argv.append('--allow-launch-failure-child-cleanup')
                with self.subTest(flag=flag,value=value),patch.object(sys,'argv',argv), \
                        patch.object(g,'ORIGINAL_READ') as read,patch.object(g,'claim_host_approval') as claim:
                    with self.assertRaisesRegex(ValueError,'failure-cleanup scope required'):g.main()
                    read.assert_not_called();claim.assert_not_called()
                self.assertFalse((Path(tmp)/'NOT-CREATED').exists())


class FirstDiagnosticFailureTests(unittest.TestCase):
    """Preserved review reproduction, extended to both mocked cleanup scopes.

    No Popen/process/pidfd/signal syscall; UID0 and all identities are synthetic.
    """
    def first_write_failure(self,authorized,fail_terminal=False):
        child_pid=424242;parent_pid=os.getpid()
        parent=dict(pid=parent_pid,start_ticks=100,ppid=1,uid=0,euid=0,
                    boot_id='FIXTURE_BOOT',state='R',details={'samples':['parent-before-write']})
        anchor=dict(pid=child_pid,start_ticks=101,ppid=parent_pid,uid=0,euid=0,
                    boot_id='FIXTURE_BOOT',state='S',details={'samples':['anchor-before-write']})
        expected_parent=deepcopy(parent);expected_anchor=deepcopy(anchor)
        first_error='FIXTURE ONLY first child-created diagnostic write failed'
        later_error='FIXTURE ONLY later terminal export failed'
        case=self
        class Child:
            pid=child_pid;returncode=None;waits=[]
            def poll(self):return self.returncode
            def wait(self,timeout=None):
                case.assertTrue(authorized,'unauthorized cleanup must not wait after signal refusal')
                self.waits.append(timeout);self.returncode=-signal.SIGTERM
                return self.returncode  # independent mocked wait result, not inferred from send
        child=Child()
        class Kernel:
            signals=[];closed=[];process_reads=0
            def now(self):return 100.0
            def incarnation(self,pid):
                case.assertIn(pid,(parent_pid,child_pid))
                return parent if pid==parent_pid else anchor
            def pin(self,pid):case.assertEqual(pid,child_pid);return 71
            def alive(self,fd):case.assertEqual(fd,71);return child.returncode is None
            def signal(self,fd,number):
                case.assertTrue(authorized);case.assertEqual((fd,number),(71,signal.SIGTERM))
                self.signals.append((fd,number))  # mocked boundary; no signal syscall
            def close(self,fd):case.assertEqual(fd,71);self.closed.append(fd)
            def process(self,pid):
                self.process_reads+=1;raise AssertionError('birth sampling has not started')
        kernel=Kernel();captured=io.StringIO();original_print=print
        attempted=None;terminal_failed=False
        def writer(value,**kwargs):
            nonlocal attempted,terminal_failed
            event=json.loads(value)
            if event.get('event')=='child-created':
                attempted=deepcopy(event)
                # Mutation after the write attempt proves snapshots were deep
                # copies made BEFORE it, not aliases or copies taken on error.
                parent['state']='CHANGED_AFTER_WRITE';anchor['state']='CHANGED_AFTER_WRITE'
                parent['details']['samples'].append('AFTER');anchor['details']['samples'].append('AFTER')
                raise OSError(first_error)
            if event.get('event')=='terminal' and fail_terminal and not terminal_failed:
                terminal_failed=True;raise OSError(later_error)
            return original_print(value,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(captured), \
                patch.object(o,'Linux',return_value=kernel), \
                patch.object(o.subprocess,'Popen',return_value=child) as spawn, \
                patch.object(o.os,'getuid',return_value=0),patch.object(o.os,'geteuid',return_value=0), \
                patch.object(o.signal,'getsignal',return_value=signal.SIG_DFL), \
                patch.object(o.signal,'signal',return_value=signal.SIG_DFL), \
                patch.object(o.signal,'pidfd_send_signal',create=True) as signal_syscall, \
                patch.object(o,'validate_launch') as validate,patch('builtins.print',side_effect=writer):
            with self.assertRaises(ValueError) as caught:
                o.launch(str(Path(tmp)/'FIXTURE_ONLY.log'),'1'*32,0,authorized)
            self.assertEqual(spawn.call_count,1);validate.assert_not_called();signal_syscall.assert_not_called()
        report=caught.exception.report
        events=[json.loads(line) for line in captured.getvalue().splitlines()]
        self.assertEqual(report['status'],'INCOMPLETE');self.assertIsNone(report['raw_receipt'])
        self.assertIsNone(report['validation']);self.assertEqual(kernel.process_reads,0)
        self.assertEqual(report['primary_error'],'OSError: '+first_error)
        self.assertEqual(report['evidence_errors'],['OSError: '+first_error]+
                         (['OSError: '+later_error] if fail_terminal else []))
        self.assertEqual(report['resource_errors'],[]);self.assertEqual(kernel.closed,[71])
        snapshots=report['untrusted_observations']
        self.assertIs(snapshots['untrusted'],True)
        self.assertEqual(snapshots['parent'],expected_parent)
        self.assertEqual(snapshots['child_anchor'],expected_anchor)
        self.assertEqual(snapshots['attempted_event'],attempted)
        self.assertIs(snapshots['attempted_event']['untrusted'],True)
        self.assertEqual(attempted['data'],{'spawn_pid':child_pid,'parent':expected_parent,'anchor':expected_anchor})
        self.assertIsNot(snapshots['parent'],parent);self.assertIsNot(snapshots['child_anchor'],anchor)
        self.assertIsNot(snapshots['parent']['details'],parent['details'])
        self.assertIsNot(snapshots['child_anchor']['details'],anchor['details'])
        self.assertIsNot(snapshots['attempted_event']['data']['parent'],snapshots['parent'])
        self.assertIsNot(snapshots['attempted_event']['data']['anchor'],snapshots['child_anchor'])
        self.assertEqual(events[-1]['event'],'terminal');self.assertTrue(events[-1]['untrusted'])
        self.assertEqual(events[-1]['data'],report)
        self.assertNotIn('authorization',report);self.assertNotIn('launch',snapshots)
        return report,child,kernel

    def test_first_write_failure_retains_untrusted_observations_without_cleanup_authority(self):
        report,child,kernel=self.first_write_failure(False)
        self.assertEqual(report['cleanup']['status'],'INCOMPLETE')
        self.assertFalse(report['cleanup']['exit_confirmed'])
        self.assertTrue(report['cleanup']['operator_recovery_needed'])
        self.assertEqual(kernel.signals,[]);self.assertEqual(child.waits,[])
        self.assertIsNone(child.poll())
        self.assertIn('not authorized',' '.join(report['cleanup']['errors']))

    def test_first_write_failure_retains_untrusted_observations_with_explicit_cleanup_authority(self):
        report,child,kernel=self.first_write_failure(True)
        self.assertEqual(report['cleanup']['status'],'CHILD_EXITED')
        self.assertTrue(report['cleanup']['exit_confirmed'])
        self.assertFalse(report['cleanup']['operator_recovery_needed'])
        self.assertEqual(kernel.signals,[(71,signal.SIGTERM)])
        self.assertEqual(len(child.waits),1);self.assertLessEqual(child.waits[0],.5)
        self.assertEqual(report['cleanup']['returncode'],-signal.SIGTERM)
        self.assertEqual(child.poll(),-signal.SIGTERM)

    def test_later_terminal_export_error_does_not_replace_first_write_or_cleanup_outcomes(self):
        for authorized in (False,True):
            with self.subTest(authorized=authorized):
                report,child,kernel=self.first_write_failure(authorized,fail_terminal=True)
                self.assertEqual(report['cleanup']['exit_confirmed'],authorized)
                self.assertEqual(len(kernel.signals),int(authorized))
                self.assertEqual(len(child.waits),int(authorized))


@unittest.skipUnless(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),
                     'Linux pidfd support required for host subprocess fixtures')
class LaunchSubprocessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.children=[];self.outputs=[]
        self.real_popen=subprocess.Popen
        self.exe=os.readlink('/proc/self/exe');self.exe_sha=o.hashlib.sha256(Path('/proc/self/exe').read_bytes()).hexdigest()
        self.addCleanup(self.finish_children)

    def finish_children(self):
        # Test-owned fixture processes only, including deliberately unresolved
        # children. This teardown is not the launcher's production cleanup.
        for child in self.children:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)

    def run_launch(self,kernel=None,authorized=True,code=None,printer=None,publish_receipt=False):
        parent=self
        code=code or 'import time; time.sleep(60)'
        def spawn(argv,**kwargs):
            self.assertEqual(argv,['nohup','./core/openplc'])
            self.assertEqual(kwargs['env'][o.ENV_KEY],TOKEN)
            # A shell execs the fake Python core in the same direct-child PID.
            # Sleep is bounded .12s; no real OpenPLC, guest or service is used.
            child=parent.real_popen(['/bin/sh','-c','sleep .12; exec "$1" -c "$2"',
                                     'FIXTURE_ONLY',sys.executable,code],**kwargs)
            parent.children.append(child);return child
        output=io.StringIO()
        with patch.object(o,'EXE',self.exe),patch.object(o,'EXE_SHA',self.exe_sha), \
                patch.object(o,'Linux',return_value=kernel or o.Linux()), \
                patch.object(o.subprocess,'Popen',side_effect=spawn),redirect_stdout(output):
            if printer is None:
                try:result=o.launch(str(self.root/('core-'+str(len(self.children))+'.log')),TOKEN,os.getuid(),authorized,publish_receipt=publish_receipt)
                except ValueError as exc:result=exc
            else:
                with patch('builtins.print',side_effect=printer):
                    try:result=o.launch(str(self.root/('output-failure-'+str(len(self.children))+'.log')),TOKEN,os.getuid(),authorized,publish_receipt=publish_receipt)
                    except ValueError as exc:result=exc
        text=output.getvalue();self.outputs.append(text)
        export=os.environ.get('M2_LAUNCH_FIXTURE_EXPORT')
        if export:
            directory=Path(export);directory.mkdir(exist_ok=True)
            (directory/(self._testMethodName+'.json')).write_text(json.dumps({
                'scope':'FIXTURE ONLY; host child, not a guest or authority',
                'events':self.events(text),'failure':getattr(result,'report',None)},indent=2)+'\n')
        return result,text

    @staticmethod
    def events(text):
        return [json.loads(line) for line in text.splitlines() if line.startswith('{')]

    def assert_exited(self,result):
        self.assertIsInstance(result,ValueError)
        cleanup=result.report['cleanup']
        self.assertEqual(cleanup['status'],'CHILD_EXITED',result.report)
        self.assertTrue(cleanup['exit_confirmed']);self.assertFalse(cleanup['operator_recovery_needed'])
        self.assertIsNotNone(self.children[-1].poll())

    def test_successful_direct_child_exec_preserves_birth_before_validation(self):
        result,text=self.run_launch()
        self.assertIsInstance(result,dict,text)
        self.assertEqual(result['birth']['pid'],self.children[-1].pid)
        self.assertEqual(result['spawn_pid'],self.children[-1].pid)
        self.assertEqual(result['birth']['uid'],os.getuid())
        self.assertEqual(result['birth']['euid'],os.geteuid())
        self.assertEqual(result['birth']['exe'],self.exe)
        observations=[e['data'] for e in self.events(text) if e['event']=='birth-before-validation']
        self.assertGreaterEqual(len(observations),3)
        self.assertTrue(any(row['birth']['exe']!=self.exe for row in observations))
        self.assertEqual({row['birth']['start_ticks'] for row in observations},{result['birth']['start_ticks']})
        self.assertIsNone(self.children[-1].poll())  # success intentionally leaves the owned child running

    def test_mismatched_pid_cleans_only_created_child_unrelated_process_survives(self):
        other=self.real_popen([sys.executable,'-c','import time; time.sleep(60)'])
        self.children.append(other)
        class Wrong(o.Linux):
            def process(self,pid):
                row=super().process(pid)
                if row is not None:row['pid']=other.pid
                return row
        result,text=self.run_launch(Wrong())
        self.assert_exited(result);self.assertIsNone(other.poll())
        failed=result.report['validation']['failed_predicates']
        item=next(f for f in failed if f['predicate']=='birth.pid_matches_spawn')
        self.assertEqual(item['observed'],other.pid);self.assertEqual(item['expected'],self.children[-1].pid)
        self.assertTrue(any(e['event']=='birth-before-validation' for e in self.events(text)))

    def test_mismatched_euid_diagnostics_survive_confirmed_child_cleanup(self):
        class Wrong(o.Linux):
            def process(self,pid):
                row=super().process(pid)
                if row is not None:row['euid']+=1
                return row
        result,text=self.run_launch(Wrong());self.assert_exited(result)
        terminal=self.events(text)[-1]['data']
        item=next(f for f in terminal['validation']['failed_predicates'] if f['predicate']=='birth.euid')
        self.assertEqual(item['observed'],os.geteuid()+1);self.assertEqual(item['expected'],os.geteuid())
        self.assertEqual(terminal['raw_receipt']['birth']['euid'],os.geteuid()+1)

    def test_child_exits_during_validation_is_confirmed_without_signals(self):
        parent=self
        class Exited(o.Linux):
            def process(self,pid):
                parent.children[-1].wait(timeout=2)
                return super().process(pid)
        result,text=self.run_launch(Exited(),code='raise SystemExit(7)');self.assert_exited(result)
        self.assertEqual(result.report['cleanup']['signals'],[])
        self.assertEqual(result.report['cleanup']['returncode'],7)
        self.assertIsNone(result.report['raw_receipt']['birth'])
        self.assertEqual(self.events(text)[-1]['event'],'terminal')

    def test_failure_after_creation_with_missing_pidfd_stays_unresolved(self):
        class Denied(o.Linux):
            def pin(self,pid):raise PermissionError('SYNTHETIC pidfd denial')
        result,text=self.run_launch(Denied())
        self.assertIn('SYNTHETIC pidfd denial',result.report['primary_error'])
        self.assertEqual(result.report['spawn_pid'],self.children[-1].pid)
        self.assertFalse(result.report['cleanup']['exit_confirmed'])
        self.assertEqual(result.report['cleanup']['signals'],[])
        self.assertTrue(result.report['cleanup']['last_observed_running'])
        self.assertIsNone(self.children[-1].poll())
        self.assertTrue(self.events(text)[-1]['data']['cleanup']['operator_recovery_needed'])

    def test_observation_failure_after_creation_still_cleans_pinned_child(self):
        class Failed(o.Linux):
            def process(self,pid):raise OSError('SYNTHETIC observation failure')
        result,text=self.run_launch(Failed());self.assert_exited(result)
        self.assertIn('SYNTHETIC observation failure',result.report['primary_error'])
        self.assertEqual(self.events(text)[0]['event'],'child-created')

    def test_unapproved_cleanup_never_signals_and_reports_running_child(self):
        class Failed(o.Linux):
            def process(self,pid):raise OSError('SYNTHETIC failure')
        result,text=self.run_launch(Failed(),authorized=False)
        self.assertEqual(result.report['cleanup']['status'],'INCOMPLETE')
        self.assertEqual(result.report['cleanup']['signals'],[])
        self.assertTrue(result.report['cleanup']['operator_recovery_needed'])
        self.assertIsNone(self.children[-1].poll())

    def test_cleanup_incarnation_mismatch_refuses_signal_and_preserves_primary_failure(self):
        class Reused(o.Linux):
            changed=False
            def process(self,pid):self.changed=True;raise OSError('SYNTHETIC primary')
            def incarnation(self,pid):
                row=super().incarnation(pid)
                if row and self.changed and pid!=os.getpid():row['start_ticks']+=1
                return row
        result,text=self.run_launch(Reused())
        self.assertIn('SYNTHETIC primary',result.report['primary_error'])
        self.assertFalse(result.report['cleanup']['exit_confirmed'])
        self.assertIn('ownership not proved',' '.join(result.report['cleanup']['errors']))
        self.assertEqual(result.report['cleanup']['signals'],[]);self.assertIsNone(self.children[-1].poll())

    def test_cleanup_timeout_does_not_claim_a_running_child_exited(self):
        class Stuck(o.Linux):
            def process(self,pid):raise OSError('SYNTHETIC primary failure')
            def signal(self,fd,number):pass  # model accepted signals with no exit; real child stays alive
        start=time.monotonic();result,text=self.run_launch(Stuck())
        self.assertLess(time.monotonic()-start,2.6)
        self.assertIn('SYNTHETIC primary failure',result.report['primary_error'])
        self.assertEqual(result.report['cleanup']['signals'],['SIGTERM','SIGKILL'])
        self.assertFalse(result.report['cleanup']['exit_confirmed'])
        self.assertEqual(result.report['cleanup']['status'],'INCOMPLETE')
        self.assertTrue(result.report['cleanup']['operator_recovery_needed'])
        self.assertIn('TimeoutExpired',' '.join(result.report['cleanup']['errors']))
        self.assertIsNone(self.children[-1].poll())

    def test_term_then_kill_is_bounded_and_exit_is_independently_waited(self):
        ready=self.root/'ready'
        class Failed(o.Linux):
            def process(self,pid):
                end=time.monotonic()+1
                while not ready.exists() and time.monotonic()<end:time.sleep(.005)
                if not ready.exists():raise AssertionError('fixture child did not become ready')
                raise OSError('SYNTHETIC failure after child ignores TERM')
        code=f'import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(ready)!r}).touch(); time.sleep(60)'
        start=time.monotonic();result,text=self.run_launch(Failed(),code=code)
        self.assert_exited(result);self.assertLess(time.monotonic()-start,4.5)
        self.assertEqual(result.report['cleanup']['signals'],['SIGTERM','SIGKILL'])
        self.assertEqual(result.report['cleanup']['returncode'],-signal.SIGKILL)

    def test_diagnostic_write_failure_preserves_primary_error_and_uses_held_child(self):
        original=print;failed=False
        def printer(value,**kwargs):
            nonlocal failed
            if not failed and json.loads(value).get('event')=='birth-before-validation':
                failed=True;raise OSError('SYNTHETIC diagnostic storage failure')
            return original(value,**kwargs)
        result,text=self.run_launch(printer=printer);self.assert_exited(result)
        self.assertIn('diagnostic storage failure',result.report['primary_error'])
        self.assertTrue(result.report['evidence_errors'])
        self.assertIsNotNone(self.events(text)[-1]['data']['raw_receipt'])

    def test_catchable_launcher_termination_cleans_child_and_restores_handler(self):
        previous=signal.getsignal(signal.SIGTERM)
        class Interrupted(o.Linux):
            def process(self,pid):signal.raise_signal(signal.SIGTERM)
        result,text=self.run_launch(Interrupted());self.assert_exited(result)
        self.assertIn('received SIGTERM',result.report['primary_error'])
        self.assertEqual(signal.getsignal(signal.SIGTERM),previous)

    def test_evidence_failure_after_exec_validation_still_cleans_held_child(self):
        for event in ('validated','terminal'):
            failed=False;original=print
            def printer(value,**kwargs):
                nonlocal failed
                if not failed and json.loads(value).get('event')==event:
                    failed=True;raise OSError('SYNTHETIC post-validation output failure')
                return original(value,**kwargs)
            with self.subTest(event=event):
                result,text=self.run_launch(printer=printer);self.assert_exited(result)
                self.assertEqual(result.report['status'],'INCOMPLETE')
                self.assertTrue(result.report['evidence_errors'])

    def test_wrong_executable_observation_bound_fails_and_cleans_child(self):
        class Pending(o.Linux):
            def process(self,pid):
                row=super().process(pid)
                if row is not None:row['exe']='/SYNTHETIC/not-selected';row['exe_sha256']='0'*64
                return row
        start=time.monotonic();result,text=self.run_launch(Pending());self.assert_exited(result)
        self.assertLess(time.monotonic()-start,4.5)
        failed=result.report['validation']['failed_predicates']
        self.assertTrue(failed[0]['predicate'].startswith('selected_exec.'))
        self.assertEqual(failed[0]['expected']['exe'],self.exe)
        self.assertEqual(g.BOUNDS['startup'],10);self.assertEqual(g.WATCHDOG_SECONDS,600)

    def test_final_receipt_publication_failure_cleans_before_releasing_pidfd(self):
        original=print
        def printer(value,**kwargs):
            if json.loads(value).get('schema_version')==1:
                raise OSError('SYNTHETIC final receipt publication failure')
            return original(value,**kwargs)
        result,text=self.run_launch(printer=printer,publish_receipt=True);self.assert_exited(result)
        self.assertEqual(self.events(text)[-1]['data']['status'],'INCOMPLETE')
        self.assertTrue(self.events(text)[-1]['data']['cleanup']['exit_confirmed'])

    def test_canonical_failed_command_retains_host_diagnostics_without_liveness_or_authority(self):
        class Failed(o.Linux):
            def process(self,pid):raise OSError('SYNTHETIC failed launch')
        result,text=self.run_launch(Failed());self.assert_exited(result)
        def serial(argv,**kwargs):
            nonce=argv[argv.index('--nonce')+1]
            data={'nonce':nonce,'output':text,'command_rc':1,'completion_marker':True,
                  'prompt':True,'shell_user':'root','context_uid':0}
            return subprocess.CompletedProcess(argv,0,json.dumps(data),'')
        live=g.LiveIO(self.root,shell_users={'plc':'root'})
        with patch.object(g.pre.subprocess,'run',side_effect=serial) as transport:
            with self.assertRaises(g.pre.PreflightFailure):
                live.read('plc','FIXTURE_ONLY_NEVER_EXECUTED','launch-once',time.monotonic()+2)
        self.assertEqual(transport.call_count,1)
        stored=json.loads((self.root/'launch-diagnostics.json').read_text())
        self.assertTrue(stored['untrusted']);self.assertTrue(stored['complete_terminal'])
        self.assertTrue(stored['events'][-1]['data']['cleanup']['exit_confirmed'])
        self.assertEqual(len(list(self.root.glob('serial-launch-once-*-command.json'))),1)
        self.assertFalse((self.root/'started-core-identity.json').exists())


if __name__=='__main__':unittest.main()

"""Saved-output shape and synthetic richer service/transport fixtures; no guests."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import m2_plc_startup_guard as g

SAVED=json.loads((Path(__file__).parent/'fixtures/m2_xle_stability/saved-status-output.json').read_text())
LEGACY_FIELDS=('ActiveState','SubState','MainPID','NRestarts')


def service():
    # The four legacy values are preserved. Added identity fields are explicitly
    # SYNTHETIC fixtures, never attributed to the saved historical attempt.
    return {'Id':'sorter-xle.service','ActiveState':'active','SubState':'running',
            'MainPID':'34302','NRestarts':'32263','ExecMainPID':'34302',
            'ExecMainStartTimestampMonotonic':'123456789','InvocationID':'1'*32,
            'ExecStart':'{ path='+g.XLE_EXECUTABLE+' ; argv[]='+' '.join(g.XLE_ARGV)+'; }'}


def output(fields=None,nonce='a'*32,command=None):
    fields=service() if fields is None else fields
    command=g.XLE_STATUS if command is None else command
    return command+"; printf '__SORTER_RC_"+nonce+"__%s\\n' \"$?\"\n"+\
           '\n'.join(name+'='+value for name,value in fields.items())


class ParserTests(unittest.TestCase):
    def test_preserved_shape_reproduces_echo_nonce_bug_without_inventing_missing_identity(self):
        parsed=[];old=[]
        for row in SAVED['samples'].values():
            parsed.append(g.properties(row['output'],command=row['command'],fields=LEGACY_FIELDS))
            old.append(dict(line.split('=',1) for line in row['output'].splitlines() if '=' in line))
        self.assertEqual(parsed[0],parsed[1]);self.assertNotEqual(old[0],old[1])
        self.assertEqual({k for k in old[0] if old[0][k]!=old[1][k]},{'env SYSTEMD_PAGER'})
        with self.assertRaisesRegex(ValueError,'incomplete XLe service identity'):
            g.xle_stability_check(*parsed)
        for row in SAVED['samples'].values():
            with self.assertRaises(ValueError):g.properties(row['output'])

    def test_only_canonical_service_view_compares_equal_with_different_echo_nonces(self):
        first=g.properties(output(nonce='a'*32));second=g.properties(output(nonce='b'*32))
        self.assertEqual(first,service());self.assertEqual(second,service())
        self.assertEqual(g.xle_stability_check(first,second)['status'],'PASS')
        self.assertNotIn('env SYSTEMD_PAGER',first)

    def test_changed_pid_incarnation_restart_or_execution_fields_fail(self):
        changes=[{'MainPID':'34303','ExecMainPID':'34303'},
                 {'ExecMainStartTimestampMonotonic':'123456790'},
                 {'InvocationID':'2'*32},{'NRestarts':'32264'},{'NRestarts':'32262'},
                 {'Id':'other.service'},{'ExecMainPID':'34303'},
                 {'ExecStart':service()['ExecStart']+' '},
                 {'ExecStart':'{ argv[]=/tmp/other; }'}]
        for change in changes:
            with self.subTest(change=change):
                second=service();second.update(change)
                with self.assertRaises(ValueError):g.xle_stability_check(service(),second)

    def test_inactive_failed_restarting_and_indeterminate_service_never_pass(self):
        for active,sub in [('inactive','dead'),('failed','failed'),('activating','auto-restart'),
                           ('active','exited'),('deactivating','stop-sigterm')]:
            with self.subTest(active=active,sub=sub):
                row=service();row.update(ActiveState=active,SubState=sub)
                with self.assertRaises(ValueError):g.xle_stability_check(row,row)

    def test_malformed_missing_duplicate_ambiguous_or_extra_payloads_fail_closed(self):
        valid=output();body=valid.split('\n',1)[1]
        invalid=[None,{},'',valid+'\nMainPID=34302',valid+'\n'+body,
                 valid+'\n'+json.dumps(service()),json.dumps(service()),
                 valid+'\nUNRELATED=extra',valid+'\ncompleted_utc=2099-01-01',
                 valid+'\nPREFLIGHT_ALIVE_'+'c'*32,
                 valid.replace('Id=sorter-xle.service','BROKEN'),
                 valid.replace('InvocationID='+'1'*32,'InvocationID='),
                 valid.replace('Id=sorter-xle.service','ActiveState=active'),
                 output(command=g.XLE_STATUS.replace('sorter-xle.service','other.service'))]
        for name in g.XLE_SERVICE_FIELDS:
            invalid.append('\n'.join(line for line in valid.splitlines() if not line.startswith(name+'=')))
        for index,text in enumerate(invalid):
            with self.subTest(case=index),self.assertRaises(ValueError):g.properties(text)
        # A bare single canonical block is valid; unrelated output cannot take
        # the place of one, even if a later JSON object looks like valid state.
        self.assertEqual(g.properties(body),service())

    def test_invalid_identity_values_and_ambiguous_exec_command_fail(self):
        cases=[('MainPID','0'),('ExecMainPID','false'),('NRestarts','-1'),
               ('ExecMainStartTimestampMonotonic','0'),('InvocationID','0'*32),
               ('InvocationID','not-an-id'),('MainPID',34302),
               ('ExecStart',service()['ExecStart']+' { argv[]='+' '.join(g.XLE_ARGV)+'; }')]
        for key,value in cases:
            with self.subTest(field=key,value=value):
                row=service();row[key]=value
                with self.assertRaises(ValueError):g.xle_stability_check(row,row)


class ExecStartTests(unittest.TestCase):
    def test_selected_contract_path_and_canonical_entry_pass(self):
        unit=Path(__file__).resolve().parents[1]/'deploy/systemd/sorter-xle.service'
        import shlex
        selected=shlex.split(next(line.split('=',1)[1] for line in unit.read_text().splitlines()
                                 if line.startswith('ExecStart=')))
        self.assertEqual(g.XLE_EXECUTABLE,'/opt/sorter-xle/venv/bin/python')
        self.assertEqual(g.XLE_EXECUTABLE,selected[0]);self.assertEqual(g.XLE_ARGV,selected)
        raw=service()['ExecStart']
        self.assertEqual(g.xle_exec_start(raw),{'path':g.XLE_EXECUTABLE,'argv':g.XLE_ARGV})
        # Same metadata grammar as preserved systemd output; these values are
        # synthetic and are not a new historical incarnation observation.
        full=(raw[:-1]+'ignore_errors=no ; start_time=[Tue 2026-10-27 14:08:49 UTC] ; '
              'stop_time=[n/a] ; pid=34302 ; code=(null) ; status=0/0 }')
        self.assertEqual(g.xle_exec_start(full),{'path':g.XLE_EXECUTABLE,'argv':g.XLE_ARGV})
        self.assertEqual(g.xle_stability_check(service(),service())['status'],'PASS')

    def test_wrong_path_with_identical_argv_fails(self):
        row=service()
        row['ExecStart']=row['ExecStart'].replace('path=/opt/sorter-xle/venv/bin/python',
                                               'path=/tmp/unselected',1)
        self.assertIn('argv[]='+' '.join(g.XLE_ARGV),row['ExecStart'])
        # Both service views agree; only independent executable selection can
        # reject the review's counterexample, not pairwise string inequality.
        with self.assertRaises(ValueError):g.xle_stability_check(row,deepcopy(row))

    def test_missing_malformed_relative_duplicate_or_ambiguous_entries_fail(self):
        argv=' '.join(g.XLE_ARGV);selected='/opt/sorter-xle/venv/bin/python'
        bad=['{ argv[]='+argv+'; }',
             '{ path= ; argv[]='+argv+'; }',
             '{ path=opt/sorter-xle/venv/bin/python ; argv[]='+argv+'; }',
             '{ path=/opt/sorter-xle/venv/bin/../bin/python ; argv[]='+argv+'; }',
             '{ path="'+selected+'" ; argv[]='+argv+'; }',
             '{ path=null ; argv[]='+argv+'; }',
             '{ path=123 ; argv[]='+argv+'; }',
             '{ path '+selected+' ; argv[]='+argv+'; }',
             '{ path='+selected+' ; path='+selected+' ; argv[]='+argv+'; }',
             '{ path=/tmp/other ; path='+selected+' ; argv[]='+argv+'; }',
             service()['ExecStart']+' '+service()['ExecStart'],
             '{ path=/tmp/other ; argv[]='+argv+'; } '+service()['ExecStart'],
             '{ path='+selected+' ; argv[]='+argv+'; argv[]='+argv+'; }',
             '{ executable_path='+selected+' ; argv[]='+argv+'; }',
             '[ '+service()['ExecStart']+' ]',
             '{ path='+selected+' ;; argv[]='+argv+'; }']
        for raw in bad:
            with self.subTest(raw=raw):
                row=service();row['ExecStart']=raw
                with self.assertRaises(ValueError):g.xle_stability_check(row,deepcopy(row))

    def test_nonstring_path_and_nonstring_argv_elements_fail_without_coercion(self):
        for path in (None,False,0,1.25,[],{},Path('/opt/sorter-xle/venv/bin/python')):
            with self.subTest(path=path),self.assertRaises(ValueError):
                g.validate_xle_exec_start({'path':path,'argv':list(g.XLE_ARGV)})
        for value in (None,False,0,1.25,[],{}):
            argv=list(g.XLE_ARGV);argv[-1]=value
            with self.subTest(argv_value=value),self.assertRaises(ValueError):
                g.validate_xle_exec_start({'path':'/opt/sorter-xle/venv/bin/python','argv':argv})
        with self.assertRaises(ValueError):
            g.validate_xle_exec_start({'path':'/opt/sorter-xle/venv/bin/python','argv':tuple(g.XLE_ARGV)})

    def test_expected_path_with_wrong_argv_fails(self):
        for argv in ([*g.XLE_ARGV[:-1],'/tmp/other.sqlite3'],['/tmp/other',*g.XLE_ARGV[1:]],
                     g.XLE_ARGV[:-1]):
            with self.subTest(argv=argv):
                row=service();row['ExecStart']='{ path=/opt/sorter-xle/venv/bin/python ; argv[]='+' '.join(argv)+'; }'
                with self.assertRaises(ValueError):g.xle_stability_check(row,deepcopy(row))


class TransportTests(unittest.TestCase):
    def read_pair(self,directory,fault=None):
        calls=[]
        def serial(argv,**kwargs):
            nonce=argv[argv.index('--nonce')+1];command=argv[3];calls.append(argv)
            probe=command.startswith("printf 'PREFLIGHT_ALIVE_")
            if probe:
                marker=re.fullmatch(r"printf '(PREFLIGHT_ALIVE_[0-9a-f]{32})\\n'",command)[1]
                text=command+'\n'+('WRONG_ALIVE' if fault=='liveness' else marker)
            else:
                self.assertEqual(command,g.XLE_STATUS);self.assertEqual(argv[2],'xle')
                text=output(nonce=nonce)
            data={'nonce':nonce,'output':text,'command_rc':0,'completion_marker':True,
                  'prompt':True,'shell_user':'root','context_uid':0}
            if not probe:
                if fault=='nonce':data['nonce']='0'*32
                if fault=='uid':data['context_uid']=1000
                if fault=='bool-uid':data['context_uid']=False
                if fault=='completion':data['completion_marker']=False
                if fault=='prompt':data['prompt']=False
                if fault=='command-exit':data['command_rc']=1
            return subprocess.CompletedProcess(argv,0,json.dumps(data),'')
        live=g.LiveIO(directory,shell_users={'xle':'root'})
        with patch.object(g.pre.subprocess,'run',side_effect=serial),redirect_stdout(io.StringIO()):
            a=g.properties(live.read('xle',g.XLE_STATUS,'xle-stable-first',time.monotonic()+30))
            b=g.properties(live.read('xle',g.XLE_STATUS,'xle-stable-second',time.monotonic()+30))
        return a,b,calls

    def test_service_equality_ignores_receipt_metadata_but_preserves_bound_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);a,b,calls=self.read_pair(directory)
            self.assertEqual(g.xle_stability_check(a,b)['status'],'PASS')
            self.assertEqual(len(calls),4)
            nonces={argv[argv.index('--nonce')+1] for argv in calls};self.assertEqual(len(nonces),4)
            records=[json.loads(f.read_text()) for f in directory.glob('serial-*.json')]
            self.assertEqual(len(records),4);self.assertEqual(sum(row['probe'] for row in records),2)
            self.assertTrue(all('recorded_utc' in row and 'deadline_monotonic' in row for row in records))
            for name in ['xle-stable-first','xle-stable-second']:
                summary=json.loads((directory/(name+'.json')).read_text())
                self.assertEqual(summary['command'],g.XLE_STATUS);self.assertEqual(summary['vm'],'xle')
                self.assertIn('__SORTER_RC_',summary['output'])
                self.assertIn('path='+g.XLE_EXECUTABLE,summary['output'])
                self.assertIn('argv[]='+' '.join(g.XLE_ARGV),summary['output'])

    def test_valid_payload_does_not_bypass_nonce_uid_completion_prompt_exit_or_liveness(self):
        for fault in ['nonce','uid','bool-uid','completion','prompt','command-exit','liveness']:
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(g.pre.PreflightFailure):self.read_pair(Path(tmp),fault)
                self.assertFalse((Path(tmp)/'xle-stable-first.json').exists())
                records=list(Path(tmp).glob('serial-*.json'))
                self.assertEqual(len(records),2 if fault=='liveness' else 1)


if __name__=='__main__':unittest.main()

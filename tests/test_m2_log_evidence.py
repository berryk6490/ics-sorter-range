"""Actual selected log-sink and bounded reader fixtures; no guest commands."""
import base64
from copy import deepcopy
from contextlib import redirect_stdout
import io
import hashlib
import json
import os
from pathlib import Path
import shlex
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import run_accumulation_attempt as runner
from test_m2_dedicated_scenarios import support_payload


class LogEvidenceTests(unittest.TestCase):
    def test_selected_manifest_sinks_are_files_for_scada_scanners_vfds_and_journal_for_xle(self):
        units=['opcua-server.service','sorter-hmi.service','scanner@tunnel1.service',
               'vfd@induct1.service','sorter-xle.service','sorter-asx.service','sorter-plant.service']
        sinks=runner.selected_log_sinks(units)
        self.assertEqual(sinks['opcua-server.service']['path'],'/home/kevin/opcua.log')
        self.assertEqual(sinks['sorter-hmi.service']['path'],'/home/kevin/hmi.log')
        self.assertEqual(sinks['scanner@tunnel1.service']['path'],'/var/log/scanner-tunnel1.log')
        self.assertEqual(sinks['vfd@induct1.service']['path'],'/var/log/vfd-induct1.log')
        for unit in ('sorter-xle.service','sorter-asx.service','sorter-plant.service'):
            self.assertEqual(sinks[unit]['kind'],'journal')
        with self.assertRaisesRegex(ValueError,'not declared'):runner.selected_log_sinks(['unrelated.service'])

    def read_file_fixture(self,content,*,fault=None,cursor=None,existing=None,initial_baseline=False):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(existing) if existing is not None else Path(tmp)/'application.log'
            if existing is None:path.write_bytes(content)
            units=['opcua-server.service'];sinks={units[0]:{'kind':'file','path':str(path),'unit_source':'FIXTURE','unit_sha256':'f'*64}}
            payload=support_payload(units,sinks=sinks)
            code=shlex.split(runner.scenario_support_command(units,None,sinks=sinks,cursors=cursor,
                                                          initial_baseline=initial_baseline))[-1]
            outputs=iter([payload['first'],payload['sockets'],payload['second']]);out=io.StringIO()
            real_open=os.open;real_stat=os.stat
            def open_file(name,*args,**kwargs):
                if str(name)==str(path) and fault in ('denied','missing'):
                    raise PermissionError('FIXTURE denied') if fault=='denied' else FileNotFoundError('FIXTURE missing')
                return real_open(name,*args,**kwargs)
            def stat_file(name,*args,**kwargs):
                if str(name).startswith('/proc/100/fd/'):
                    if fault=='wrong-fd':return SimpleNamespace(st_dev=-1,st_ino=-1)
                    return real_stat(path)
                return real_stat(name,*args,**kwargs)
            def text(p,*args,**kwargs):
                return '100 (fixture) '+' '.join(['S']+['0']*18+['42']) if str(p).endswith('/stat') else payload['boot_id']
            with patch('subprocess.run',side_effect=lambda *a,**k:SimpleNamespace(stdout=next(outputs),stderr='')) as commands, \
                    patch.object(Path,'read_text',text),patch('os.stat',side_effect=stat_file), \
                    patch('os.open',side_effect=open_file),redirect_stdout(out):
                exec(compile(code,'<selected-file-log-reader>','exec'),{})
            result=json.loads(out.getvalue())
            self.assertFalse(any('journalctl' in call.args[0] for call in commands.call_args_list))
            evidence=os.environ.get('M2_B1_FIXTURE_OUTPUT')
            if evidence:
                folder=Path(evidence);folder.mkdir(parents=True,exist_ok=True)
                with path.open('rb') as stream:source=stream.read(16384)
                source_stat=path.stat()
                with (folder/(self._testMethodName+'.jsonl')).open('a') as stream:
                    stream.write(json.dumps(dict(fixture_only=True,source_bytes_limit=16384,
                        source_base64=base64.b64encode(source).decode(),source_size=source_stat.st_size,
                        source_dev=source_stat.st_dev,source_inode=source_stat.st_ino,
                        payload=result),sort_keys=True)+'\n')
            return result,sinks,units

    def test_explicit_initial_baseline_positions_long_and_empty_logs_without_claiming_events(self):
        for history in (b'OLD HISTORY\n'*1000,b''):
            with self.subTest(history_bytes=len(history)),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'application.log';path.write_bytes(history)
                raw,sinks,units=self.read_file_fixture(None,existing=path,initial_baseline=True)
                before=runner.scenario_support_check(raw,units,sinks=sinks,initial_baseline=True)
                self.assertEqual(before['log_cursors'][units[0]]['interval_status'],'BASELINE_VALID')
                self.assertEqual(before['application_attribution'],'INCOMPLETE')
                self.assertEqual(before['interval_start_readiness'],'READY')
                self.assertEqual(before['application_evidence'][units[0]]['history_completeness'],'UNPROVEN')
                self.assertEqual(raw['application_logs'][units[0]]['status'],'TRUNCATED' if history else 'NO_EVENTS')
                quiet,_,_=self.read_file_fixture(None,existing=path,cursor=before['log_cursors'])
                empty=runner.scenario_support_check(quiet,units,sinks=sinks,cursors=before['log_cursors'])
                self.assertEqual(empty['application_attribution'],'INCOMPLETE')
                self.assertEqual(empty['application_evidence'][units[0]]['status'],'NO_EVENTS')
                self.assertEqual(empty['log_cursors'][units[0]]['interval_status'],'BOUNDED_OBSERVATION')
                with path.open('ab') as f:f.write(b'NEW INTERVAL EVENT\n')
                appended,_,_=self.read_file_fixture(None,existing=path,cursor=empty['log_cursors'])
                after=runner.scenario_support_check(appended,units,sinks=sinks,cursors=empty['log_cursors'])
                self.assertEqual(after['application_attribution'],'SUPPORTED_WITH_LIMITS')
                self.assertEqual(base64.b64decode(appended['application_logs'][units[0]]['bytes_base64']),b'NEW INTERVAL EVENT\n')
                self.assertEqual(appended['application_logs'][units[0]]['offset'],len(history))
                self.assertEqual(empty['application_attribution'],'INCOMPLETE')  # earlier quiet interval is not rewritten

    def test_initial_baseline_requires_explicit_bound_phase_and_cannot_adopt_failed_cursor(self):
        raw,sinks,units=self.read_file_fixture(b'OLD\n'*3000)
        legacy=runner.scenario_support_check(raw,units,sinks=sinks)
        self.assertEqual(legacy['log_cursors'][units[0]]['interval_status'],'INCOMPLETE')
        with self.assertRaisesRegex(ValueError,'phase/request mismatch'):
            runner.scenario_support_check(raw,units,sinks=sinks,initial_baseline=True)
        for value in (True,1,'yes'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                runner.scenario_support_command(units,None,sinks=sinks,cursors=legacy['log_cursors'],initial_baseline=value)
        with self.assertRaises(ValueError):
            runner.scenario_support_command(units,'2026-10-27T00:00:00+00:00',sinks=sinks,initial_baseline=True)
        for since,initial in ((None,False),({},True)):
            with self.subTest(since=since,initial=initial),self.assertRaisesRegex(ValueError,'no automatic rebaseline'):
                runner.collect_scenario_support(object(),0,since,'FIXTURE',initial_baseline=initial)
        baseline,_,_=self.read_file_fixture(b'',initial_baseline=True)
        for value in (False,1,'true',None):
            bad=deepcopy(baseline);bad['initial_baseline']=value
            with self.subTest(phase=value),self.assertRaises(ValueError):
                runner.scenario_support_check(bad,units,sinks=sinks,initial_baseline=True)

    def test_valid_initial_baseline_does_not_clear_exhausted_interval_or_content_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'application.log';history=b'OLD HISTORY\n'*1000;path.write_bytes(history)
            raw,sinks,units=self.read_file_fixture(None,existing=path,initial_baseline=True)
            before=runner.scenario_support_check(raw,units,sinks=sinks,initial_baseline=True)
            cursor=before['log_cursors']
            with path.open('ab') as f:f.write(b'X'*9000)
            raw,_,_=self.read_file_fixture(None,existing=path,cursor=cursor)
            failed=runner.scenario_support_check(raw,units,sinks=sinks,cursors=cursor)
            self.assertEqual(failed['application_attribution'],'INCOMPLETE')
            self.assertEqual(failed['application_evidence'][units[0]]['status'],'TRUNCATED')
            self.assertEqual(failed['log_cursors'][units[0]]['interval_status'],'INCOMPLETE')
            blocked,_,_=self.read_file_fixture(None,existing=path,cursor=failed['log_cursors'])
            self.assertFalse(blocked['application_logs'][units[0]]['available'])
            with self.assertRaises(ValueError):
                runner.scenario_support_command(units,None,sinks=sinks,cursors=failed['log_cursors'],initial_baseline=True)
            identity=(path.stat().st_dev,path.stat().st_ino)
            with path.open('wb') as f:f.write(b'REWRITTEN HISTORY\n'*1000)
            self.assertEqual((path.stat().st_dev,path.stat().st_ino),identity)
            rejected,_,_=self.read_file_fixture(None,existing=path,cursor=cursor)
            self.assertFalse(rejected['application_logs'][units[0]]['available'])
            self.assertIn('continuity lost',rejected['application_logs'][units[0]]['error'])

    def test_explicit_baseline_keeps_unstable_fd_and_journal_failures_independent(self):
        for fault in ('missing','denied','wrong-fd'):
            raw,sinks,units=self.read_file_fixture(b'OLD\n'*3000,fault=fault,initial_baseline=True)
            with self.subTest(fault=fault),self.assertRaisesRegex(ValueError,'unavailable'):
                runner.scenario_support_check(raw,units,sinks=sinks,initial_baseline=True)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'application.log';path.write_bytes(b'OLD\n'*3000)
            original=os.pread;reads=[0]
            def changed(fd,length,offset):
                value=original(fd,length,offset);reads[0]+=1
                if reads[0]==2:
                    with path.open('ab') as f:f.write(b'RACE\n')
                return value
            with patch('os.pread',side_effect=changed):
                raw,sinks,units=self.read_file_fixture(None,existing=path,initial_baseline=True)
            unstable=runner.scenario_support_check(raw,units,sinks=sinks,initial_baseline=True)
            self.assertEqual(raw['application_logs'][units[0]]['status'],'UNSTABLE')
            self.assertEqual(unstable['log_cursors'][units[0]]['interval_status'],'INCOMPLETE')
            self.assertEqual(unstable['interval_start_readiness'],'INCOMPLETE')
        units=['sorter-xle.service']
        for kind in ('empty','mixed-invocation'):
            raw=support_payload(units,events=kind!='empty',initial_baseline=True)
            if kind=='mixed-invocation':
                other=json.loads(raw['journal']);other['_SYSTEMD_INVOCATION_ID']='2'*32
                raw['journal']+='\n'+json.dumps(other)
            assessed=runner.scenario_support_check(raw,units,initial_baseline=True)
            self.assertEqual(assessed['application_attribution'],'INCOMPLETE')
            self.assertEqual(assessed['interval_start_readiness'],'INCOMPLETE')

    def test_complete_file_bytes_have_exact_hash_range_and_current_stdout_binding(self):
        payload,sinks,units=self.read_file_fixture(b'FIXTURE current application event\n')
        result=runner.scenario_support_check(payload,units,sinks=sinks)
        self.assertEqual(result['application_attribution'],'SUPPORTED_WITH_LIMITS')
        row=payload['application_logs'][units[0]]
        self.assertEqual(base64.b64decode(row['bytes_base64']),b'FIXTURE current application event\n')
        row['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'invalid file log'):runner.scenario_support_check(payload,units,sinks=sinks)

    def test_empty_truncated_missing_denied_or_wrong_stdout_never_become_application_pass(self):
        for content,fault in [(b'',None),(b'x'*9000,None),(b'event','missing'),(b'event','denied'),(b'event','wrong-fd')]:
            with self.subTest(length=len(content),fault=fault):
                payload,sinks,units=self.read_file_fixture(content,fault=fault)
                if fault:
                    self.assertFalse(payload['application_logs'][units[0]]['available'])
                    with self.assertRaisesRegex(ValueError,'unavailable'):runner.scenario_support_check(payload,units,sinks=sinks)
                else:self.assertEqual(runner.scenario_support_check(payload,units,sinks=sinks)['application_attribution'],'INCOMPLETE')

    def test_empty_or_manager_only_journal_is_not_application_event_evidence(self):
        units=['sorter-xle.service'];payload=support_payload(units,events=False)
        self.assertEqual(runner.scenario_support_check(payload,units)['application_attribution'],'INCOMPLETE')
        payload['journal']=json.dumps({'MESSAGE':'unit lifecycle only','_PID':'1','_SYSTEMD_UNIT':units[0]})
        self.assertEqual(runner.scenario_support_check(payload,units)['application_attribution'],'INCOMPLETE')

    def test_same_inode_copytruncate_regrowth_rejected_and_genuine_append_retains_bounded_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'application.log';old=b'OLD complete bytes\n';path.write_bytes(old)
            pre,sinks,units=self.read_file_fixture(None,existing=path)
            before=runner.scenario_support_check(pre,units,sinks=sinks);identity=(path.stat().st_dev,path.stat().st_ino)
            with path.open('ab') as f:f.write(b'GENUINE appended event\n')
            post,_,_=self.read_file_fixture(None,existing=path,cursor=before['log_cursors'])
            accepted=runner.scenario_support_check(post,units,sinks=sinks,cursors=before['log_cursors'])
            self.assertEqual(accepted['application_attribution'],'SUPPORTED_WITH_LIMITS')
            self.assertEqual(accepted['application_evidence'][units[0]]['history_completeness'],'UNPROVEN')
            self.assertEqual(post['application_logs'][units[0]]['continuity']['status'],'BOUNDED_ANCHOR_MATCH')
            self.assertEqual(base64.b64decode(post['application_logs'][units[0]]['bytes_base64']),b'GENUINE appended event\n')
            self.assertEqual(post['application_logs'][units[0]]['offset'],len(old))
            with path.open('wb') as f:f.write(b'NEW overwritten prefix and NEW event after old cursor\n')
            self.assertEqual((path.stat().st_dev,path.stat().st_ino),identity)
            lost,_,_=self.read_file_fixture(None,existing=path,cursor=before['log_cursors'])
            self.assertFalse(lost['application_logs'][units[0]]['available'])
            self.assertIn('continuity lost',lost['application_logs'][units[0]]['error'])
            with self.assertRaisesRegex(ValueError,'unavailable'):
                runner.scenario_support_check(lost,units,sinks=sinks,cursors=before['log_cursors'])

    def test_missing_anchor_rotation_and_short_previous_content_read_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'application.log';path.write_bytes(b'prior event\n')
            pre,sinks,units=self.read_file_fixture(None,existing=path)
            cursor=runner.scenario_support_check(pre,units,sinks=sinks)['log_cursors']
            from copy import deepcopy
            for fault in ('missing-anchor','rotation','short-read'):
                bad=deepcopy(cursor)
                if fault=='missing-anchor':del bad[units[0]]['anchor_sha256']
                if fault=='rotation':bad[units[0]]['inode']+=1
                real_pread=os.pread
                def read(fd,length,offset):
                    value=real_pread(fd,length,offset)
                    return value[:-1] if fault=='short-read' and value else value
                with self.subTest(fault=fault),patch('os.pread',side_effect=read):
                    raw,_,_=self.read_file_fixture(None,existing=path,cursor=bad)
                self.assertFalse(raw['application_logs'][units[0]]['available'])
                with self.assertRaises(ValueError):runner.scenario_support_check(raw,units,sinks=sinks,cursors=bad)

    def test_malformed_witness_and_exact_returned_range_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'application.log';path.write_bytes(b'OLD complete bytes\n')
            raw,sinks,units=self.read_file_fixture(None,existing=path)
            before=runner.scenario_support_check(raw,units,sinks=sinks);cursor=before['log_cursors']
            missing,_,_=self.read_file_fixture(None,existing=path,cursor={})
            self.assertFalse(missing['application_logs'][units[0]]['available'])
            self.assertIn('prior log cursor missing',missing['application_logs'][units[0]]['error'])
            with self.assertRaises(ValueError):runner.scenario_support_check(missing,units,sinks=sinks,cursors={})
            for field,value in [('offset',True),('anchor_bytes',999),('anchor_offset',1),
                                ('anchor_sha256',None),('interval_status','INCOMPLETE')]:
                bad=deepcopy(cursor);bad[units[0]][field]=value
                with self.subTest(field=field):
                    failed,_,_=self.read_file_fixture(None,existing=path,cursor=bad)
                    self.assertFalse(failed['application_logs'][units[0]]['available'])
                    with self.assertRaises(ValueError):runner.scenario_support_check(failed,units,sinks=sinks,cursors=bad)
            with path.open('ab') as f:f.write(b'GENUINE append\n')
            good,_,_=self.read_file_fixture(None,existing=path,cursor=cursor)
            for fault in ('offset','short-suffix-rehashed','length-type','next-witness','prior-witness','cursor-type'):
                bad=deepcopy(good);row=bad['application_logs'][units[0]]
                if fault=='offset':row['offset']+=1
                elif fault=='short-suffix-rehashed':
                    data=base64.b64decode(row['bytes_base64'])[:-1]
                    row.update(bytes_base64=base64.b64encode(data).decode(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
                elif fault=='length-type':row['bytes']=True
                elif fault=='next-witness':row['next_witness_base64']=''
                elif fault=='prior-witness':row['previous_witness_base64']=''
                elif fault=='cursor-type':row['cursor']['anchor_bytes']=False
                with self.subTest(fault=fault),self.assertRaises(ValueError):
                    runner.scenario_support_check(bad,units,sinks=sinks,cursors=cursor)

    def test_unstable_truncated_or_empty_observation_cannot_be_laundered_by_next_cursor(self):
        for kind in ('unstable','truncated','empty'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'application.log';path.write_bytes(b'' if kind=='empty' else b'OLD complete bytes\n')
                pre,sinks,units=self.read_file_fixture(None,existing=path)
                first=runner.scenario_support_check(pre,units,sinks=sinks)
                if kind=='empty':current=first
                else:
                    with path.open('ab') as f:f.write(b'x'*9000 if kind=='truncated' else b'append\n')
                    original=os.pread;reads=[0]
                    def read(fd,length,offset):
                        value=original(fd,length,offset);reads[0]+=1
                        if kind=='unstable' and reads[0]==2:
                            with path.open('ab') as f:f.write(b'mutated inside combined read\n')
                        return value
                    with patch('os.pread',side_effect=read):
                        raw,_,_=self.read_file_fixture(None,existing=path,cursor=first['log_cursors'])
                    current=runner.scenario_support_check(raw,units,sinks=sinks,cursors=first['log_cursors'])
                    self.assertEqual(raw['application_logs'][units[0]]['status'],'UNSTABLE' if kind=='unstable' else 'TRUNCATED')
                self.assertEqual(current['application_attribution'],'INCOMPLETE')
                self.assertEqual(current['log_cursors'][units[0]]['interval_status'],'INCOMPLETE')
                with path.open('ab') as f:f.write(b'later append cannot repair failed interval\n')
                later,_,_=self.read_file_fixture(None,existing=path,cursor=current['log_cursors'])
                self.assertFalse(later['application_logs'][units[0]]['available'])
                self.assertIn('no automatic rebaseline',later['application_logs'][units[0]]['error'])
                with self.assertRaises(ValueError):runner.scenario_support_check(later,units,sinks=sinks,cursors=current['log_cursors'])

    def test_journal_current_invocation_required_even_with_same_unit_pid_and_boot(self):
        units=['sorter-xle.service']
        for invocation,status in [('1'*32,'SUPPORTED_WITH_LIMITS'),('2'*32,'INCOMPLETE'),(None,'INCOMPLETE')]:
            raw=support_payload(units);entry=json.loads(raw['journal'])
            if invocation is None:entry.pop('_SYSTEMD_INVOCATION_ID')
            else:entry['_SYSTEMD_INVOCATION_ID']=invocation
            raw['journal']=json.dumps(entry)
            with self.subTest(invocation=invocation):
                result=runner.scenario_support_check(raw,units)
                self.assertEqual(result['application_attribution'],status)
                self.assertEqual(result['application_evidence'][units[0]]['matched_application_records'],int(status!='INCOMPLETE'))
                self.assertEqual(result['application_evidence'][units[0]]['unbound_incarnation_records'],int(status=='INCOMPLETE'))
        raw=support_payload(units);wrong=json.loads(raw['journal']);wrong.pop('_SYSTEMD_INVOCATION_ID')
        raw['journal']+='\n'+json.dumps(wrong)
        mixed=runner.scenario_support_check(raw,units)
        self.assertEqual(mixed['application_evidence'][units[0]]['matched_application_records'],1)
        self.assertEqual(mixed['application_evidence'][units[0]]['status'],'INCOMPLETE_INCARNATION')
        self.assertEqual(mixed['application_attribution'],'INCOMPLETE')


if __name__=='__main__':unittest.main()

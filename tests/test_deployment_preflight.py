"""Fail-closed serial deployment preflight tests; no guest access."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import deployment_preflight as pre


class Reply:
    def __init__(self, nonce, output="", rc=0, **extra):
        self.returncode = rc
        self.stderr = ""
        self.stdout = json.dumps({"nonce": nonce, "output": output, "command_rc": rc,
                                  "completion_marker": True, "prompt": True, **extra})


class PreflightTests(unittest.TestCase):
    def test_scada_root_keeps_distinct_receipts_and_one_shared_deadline(self):
        calls = []; traces = []
        def fake(argv, **kwargs):
            calls.append((argv, kwargs))
            nonce = argv[argv.index('--nonce')+1]
            output = 'fixture read' if len(calls)==1 else argv[3].split("'")[1].replace('\\n', '')
            return Reply(nonce, output, shell_user='root', context_uid=0)
        with patch.object(pre.secrets, 'token_hex', side_effect=['a'*32, 'c'*32, 'b'*32]), \
                patch.object(pre.time, 'monotonic', side_effect=[10, 12]), \
                patch.object(pre.subprocess, 'run', side_effect=fake):
            self.assertEqual(pre.guest_read('scada', 'FIXED_READ', 'scada-root',
                shell_user='root', deadline=15, trace=traces.append), 'fixture read')
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(argv[2]=='scada' and argv[argv.index('--shell-user')+1]=='root'
                            for argv, kwargs in calls))
        self.assertEqual([kwargs['timeout'] for argv, kwargs in calls], [5, 3])
        self.assertEqual([argv[-1] for argv, kwargs in calls], ['4.5', '2.5'])
        self.assertEqual([argv[argv.index('--nonce')+1] for argv, kwargs in calls], ['a'*32, 'b'*32])
        self.assertEqual([row['probe'] for row in traces], [False, True])

    def test_scada_root_bad_command_or_liveness_receipt_is_rejected(self):
        faults = [('context_uid', 1000, 'context_uid_unproven'),
                  ('context_uid', False, 'context_uid_unproven'),
                  ('context_uid', 0.0, 'context_uid_unproven'),
                  ('context_uid', '0', 'context_uid_unproven'),
                  ('shell_user', 'kevin', 'context_uid_unproven'),
                  ('completion_marker', False, 'missing_completion_marker'),
                  ('prompt', False, 'missing_prompt'),
                  ('nonce', 'f'*32, 'stale_command_marker')]
        for stage in (1, 2):
            for field, value, kind in faults:
                calls = []
                def fake(argv, **kwargs):
                    calls.append(argv)
                    nonce = argv[argv.index('--nonce')+1]
                    output = 'read' if len(calls)==1 else argv[3].split("'")[1].replace('\\n', '')
                    reply = Reply(nonce, output, shell_user='root', context_uid=0)
                    if len(calls)==stage:
                        data = json.loads(reply.stdout); data[field] = value
                        reply.stdout = json.dumps(data)
                    return reply
                expected = (('liveness_missing_marker' if field=='completion_marker'
                             else 'liveness_missing_prompt' if field=='prompt'
                             else 'stale_liveness_marker' if field=='nonce' else kind)
                            if stage==2 else kind)
                with self.subTest(stage=stage, field=field, value=value), \
                        patch.object(pre.subprocess, 'run', side_effect=fake), \
                        self.assertRaises(pre.PreflightFailure) as caught:
                    pre.guest_read('scada', 'FIXED_READ', 'scada-root', shell_user='root')
                self.assertEqual(caught.exception.kind, expected)
                self.assertEqual(len(calls), stage)

    def test_unsupported_context_still_blocks_before_serial_wrapper(self):
        for vm, user in [('fw', 'root'), ('analyst', 'root'), ('unknown', 'root'),
                         ('scada', 'admin'), ('scada', ''), ('scada', None)]:
            with self.subTest(vm=vm, user=user), patch.object(pre.subprocess, 'run') as run:
                with self.assertRaises(pre.PreflightFailure) as caught:
                    pre._serial(vm, 'DO_NOT_SEND', 'unsupported', shell_user=user)
                self.assertEqual(caught.exception.kind, 'unsupported_console_context')
                run.assert_not_called()

    def test_explicit_root_profile_applies_to_command_and_separate_liveness(self):
        calls=[]
        def fake(argv,**kw):
            calls.append(argv);nonce=argv[argv.index('--nonce')+1]
            output='read' if len(calls)==1 else argv[3].split("'")[1].replace('\\n','')
            return Reply(nonce,output,shell_user='root',context_uid=0)
        with patch.object(pre.subprocess,'run',side_effect=fake):
            self.assertEqual(pre.guest_read('xle','FIXED_READ','root-read',shell_user='root'),'read')
        self.assertEqual(len(calls),2)
        self.assertTrue(all(a[a.index('--shell-user')+1]=='root' for a in calls))

    def test_unproven_root_context_fails_before_dependent_probe(self):
        for fields in ({},{'shell_user':'root','context_uid':1000},
                       {'shell_user':'root','context_uid':False}):
            with self.subTest(fields=fields),patch.object(pre.secrets,'token_hex',return_value='a'*32), \
                    patch.object(pre.subprocess,'run',return_value=Reply('a'*32,**fields)) as run:
                with self.assertRaises(pre.PreflightFailure) as r:
                    pre.guest_read('plc','FIXED_READ','root-read',shell_user='root')
                self.assertEqual(r.exception.kind,'context_uid_unproven');self.assertEqual(run.call_count,1)

    def test_deadline_caps_command_and_separate_liveness_and_keeps_trace(self):
        traces=[];calls=[]
        def fake(argv,**kw):
            calls.append((argv,kw))
            nonce=argv[argv.index('--nonce')+1]
            text='ok' if len(calls)==1 else argv[3].split("'")[1].replace('\\n','')
            return Reply(nonce,text)
        with patch.object(pre.time,'monotonic',side_effect=[10,12]), \
                patch.object(pre.subprocess,'run',side_effect=fake):
            self.assertEqual(pre.guest_read('plc','true','bounded',deadline=15,trace=traces.append),'ok')
        self.assertEqual([kw['timeout'] for argv,kw in calls],[5,3])
        self.assertEqual([argv[-1] for argv,kw in calls],['4.5','2.5'])
        self.assertEqual(len(traces),2)
        self.assertFalse(traces[0]['probe']);self.assertTrue(traces[1]['probe'])

    def test_expired_budget_launches_no_serial_and_long_helper_is_explicit(self):
        with patch.object(pre.time,'monotonic',return_value=10),patch.object(pre.subprocess,'run') as run:
            with self.assertRaises(pre.PreflightFailure):
                pre.guest_read('plc','true','expired',deadline=10)
            run.assert_not_called()
        with patch.object(pre.secrets,'token_hex',return_value='a'*32), \
                patch.object(pre.subprocess,'run',return_value=Reply('a'*32)) as run:
            pre._serial('scada','reviewed helper','handshake',timeout=85)
        self.assertEqual(run.call_args.kwargs['timeout'],95)
        self.assertIn('85',run.call_args.args[0])

    def test_distinct_live_probe_and_shell_contract(self):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            self.assertEqual(argv[1], str(pre.SERIAL))
            self.assertEqual(kw["timeout"], 30)
            nonce = argv[argv.index("--nonce") + 1]
            output = "read result" if len(calls) == 1 else argv[3].split("'")[1].replace("\\n", "")
            return Reply(nonce, output)

        with patch.object(pre.subprocess, "run", side_effect=fake):
            self.assertEqual(pre.guest_read("drives", "sha256sum /tmp/file", "hash"), "read result")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][calls[0].index("--nonce") + 1],
                            calls[1][calls[1].index("--nonce") + 1])
        self.assertTrue(calls[1][3].startswith("printf 'PREFLIGHT_ALIVE_"))
        self.assertFalse(any(x in " ".join(" ".join(call) for call in calls).lower()
                             for x in (" logout", " exit", " exec")))

    def test_command_failure_stops_before_probe(self):
        with patch.object(pre.secrets, "token_hex", return_value="a" * 32), \
             patch.object(pre.subprocess, "run", return_value=Reply("a" * 32, rc=1)) as run:
            with self.assertRaises(pre.PreflightFailure) as caught:
                pre.guest_read("plc", "false", "bad")
            self.assertEqual(caught.exception.kind, "command_failure")
            self.assertEqual(run.call_count, 1)

    def test_missing_marker_and_prompt(self):
        for field, kind in (("completion_marker", "missing_completion_marker"),
                            ("prompt", "missing_prompt")):
            with self.subTest(field=field), patch.object(pre.secrets, "token_hex", return_value="a" * 32), \
                    patch.object(pre.subprocess, "run", return_value=Reply("a" * 32, **{field: False})) as run:
                with self.assertRaises(pre.PreflightFailure) as caught:
                    pre.guest_read("plc", "true", "bad")
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(run.call_count, 1)

    def test_liveness_failure_and_stale_output(self):
        for second, kind in ((Reply("b" * 32, "anything", rc=1), "liveness_command_failure"),
                             (Reply("b" * 32, "PREFLIGHT_ALIVE_old"), "stale_liveness_marker")):
            with self.subTest(kind=kind), patch.object(pre.secrets, "token_hex",
                    side_effect=["a" * 32, "c" * 32, "b" * 32]), \
                    patch.object(pre.subprocess, "run", side_effect=[Reply("a" * 32, "ok"), second]) as run:
                with self.assertRaises(pre.PreflightFailure) as caught:
                    pre.guest_read("plc", "true", "test")
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(run.call_count, 2)

    def test_command_timeout_and_pager_classification(self):
        from subprocess import TimeoutExpired
        with patch.object(pre.subprocess, "run", side_effect=TimeoutExpired("serial", 30)):
            with self.assertRaises(pre.PreflightFailure) as caught:
                pre.guest_read("plc", "true", "timeout")
            self.assertEqual(caught.exception.kind, "command_timeout")
        with patch.object(pre.secrets, "token_hex", return_value="a" * 32), \
             patch.object(pre.subprocess, "run", return_value=type("R", (), {
                 "stdout": json.dumps({"nonce": "a" * 32, "error": "pager_like"}),
                 "stderr": "", "returncode": 124})()):
            with self.assertRaises(pre.PreflightFailure) as caught:
                pre.guest_read("plc", "true", "pager")
            self.assertEqual(caught.exception.kind, "pager_like")

    def test_liveness_timeout_stops_after_probe(self):
        from subprocess import TimeoutExpired
        with patch.object(pre.secrets, "token_hex", side_effect=["a" * 32, "b" * 32, "c" * 32]), \
             patch.object(pre.subprocess, "run", side_effect=[Reply("a" * 32, "ok"),
                                                               TimeoutExpired("serial", 30)]) as run:
            with self.assertRaises(pre.PreflightFailure) as caught:
                pre.guest_read("plc", "true", "timeout")
            self.assertEqual(caught.exception.kind, "liveness_timeout")
            self.assertEqual(run.call_count, 2)

    def test_service_commands_are_noninteractive(self):
        self.assertEqual(pre.SYSTEMCTL,
                         "env SYSTEMD_PAGER=cat SYSTEMD_COLORS=0 /usr/bin/systemctl --no-pager")
        self.assertEqual(pre.service_read_command("sorter-plant.service"),
                         pre.SYSTEMCTL + " is-active sorter-plant.service")
        with self.assertRaises(pre.PreflightFailure):
            pre.service_read_command("sorter-plant.service; evil")
        source = Path(pre.__file__).read_text()
        self.assertNotIn("systemctl status", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()

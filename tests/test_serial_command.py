"""Serial completion and prompt classification with a fake persistent console."""
import pathlib
import re
import io
import json
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pexpect

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import serial_command as serial


class TTY:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.before = ""
        self.match = None
        self.sent = []
        self.patterns = []
        self.closed = False

    def expect(self, pattern, timeout):
        self.patterns.append(pattern)
        item = next(self.outcomes)
        if isinstance(item, Exception):
            self.before = ""
            raise item
        self.before = item[1]
        self.match = item[2] if len(item) > 2 else None
        return item[0]

    def sendline(self, value):
        self.sent.append(value)

    def send(self, value):
        self.sent.append(value)

    def close(self):
        self.closed = True


class Match:
    def group(self, _):
        return "0"


class ObservedTTY(TTY):
    """Match literal fixture buffers; no subprocess or console is opened."""
    def expect(self, pattern, timeout):
        self.patterns.append(pattern)
        text = next(self.outcomes)
        self.before = text
        for index, candidate in enumerate(pattern if isinstance(pattern, list) else [pattern]):
            match = re.search(candidate, text)
            if match:
                self.before = text[:match.start()]
                self.match = match
                return index
        raise pexpect.TIMEOUT('fixture buffer does not match expected context/marker')


class SerialTests(unittest.TestCase):
    def scada_buffers(self, nonce='a'*32):
        return ['Escape character', 'root@scada:/home/kevin# ',
                f'__SORTER_UID_{nonce}__0', 'root@scada:/home/kevin# ',
                f'fixture read\n__SORTER_RC_{nonce}__0', 'root@scada:/home/kevin# ']

    def test_scada_root_context_and_cli_prove_identity_without_authentication(self):
        tty = ObservedTTY(self.scada_buffers())
        calls = []
        def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            return tty
        result = serial.execute('scada', 'FIXED_READ', 2, 'a'*32,
                                spawn=spawn, shell_user='root', total_timeout=5)
        self.assertEqual((result['shell_user'], result['context_uid']), ('root', 0))
        self.assertTrue(result['completion_marker'] and result['prompt'])
        self.assertEqual(calls[0][0][1], ['-c', 'qemu:///system', 'console', 'scada'])
        self.assertIn('__SORTER_UID_'+'a'*32, tty.sent[1])
        self.assertTrue(tty.sent[2].startswith('FIXED_READ;'))
        self.assertEqual(tty.sent[-1], '\x1d')
        self.assertTrue(tty.closed)
        self.assertFalse(any(word in ' '.join(tty.sent) for word in ('sudo', 'passwd', 'login ', 'su -')))
        cli_tty = ObservedTTY(self.scada_buffers())
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(pathlib.Path(serial.__file__)), 'scada',
                'FIXED_READ', '--shell-user', 'root', '--nonce', 'a'*32,
                '--timeout', '2', '--total-timeout', '5', '--json']), \
                patch.object(pexpect, 'spawn', return_value=cli_tty), redirect_stdout(output):
            with self.assertRaises(SystemExit) as done:
                runpy.run_path(serial.__file__, run_name='__main__')
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())['context_uid'], 0)
        self.assertTrue(cli_tty.closed)

    def test_scada_root_wrong_prompt_uid_or_marker_fails_closed(self):
        cases = [(1, 'kevin@scada:~$ ', 'missing_prompt', False),
                 (1, 'root@plc:~# ', 'missing_prompt', False),
                 (1, 'login: ', 'login_required', False),
                 (2, '__SORTER_UID_'+'a'*32+'__1000', 'context_uid_mismatch', False),
                 (2, '__SORTER_UID_'+'b'*32+'__0', 'context_identity_timeout', False),
                 (3, 'kevin@scada:~$ ', 'context_identity_timeout', False),
                 (4, '__SORTER_RC_'+'b'*32+'__0', 'command_timeout', True),
                 (4, 'root@scada:~# ', 'missing_completion_marker', True),
                 (5, 'root@plc:~# ', 'missing_prompt', True)]
        for position, value, kind, intended_sent in cases:
            buffers = self.scada_buffers(); buffers[position] = value
            tty = ObservedTTY(buffers)
            with self.subTest(kind=kind, value=value), self.assertRaises(serial.SerialFailure) as caught:
                serial.execute('scada', 'FIXED_READ', 2, 'a'*32,
                               spawn=lambda *a, **k: tty, shell_user='root', total_timeout=5)
            self.assertEqual(caught.exception.kind, kind)
            self.assertEqual(any(s.startswith('FIXED_READ;') for s in tty.sent), intended_sent)
            self.assertTrue(tty.closed)

    def test_unsupported_explicit_contexts_do_not_attach(self):
        for vm, user in [('fw', 'root'), ('analyst', 'root'), ('unknown', 'root'),
                         ('scada', 'admin'), ('scada', ''), ('scada', None)]:
            with self.subTest(vm=vm, user=user), patch('pexpect.spawn') as spawn:
                with self.assertRaises(ValueError):
                    serial.execute(vm, 'DO_NOT_SEND', spawn=spawn, shell_user=user)
                spawn.assert_not_called()

    def test_explicit_root_profile_proves_uid_before_intended_command(self):
        tty=TTY([(0,''),(0,''),(0,'',Match()),(0,''),(0,'ok',Match()),(0,'')])
        r=serial.execute('plc','REVIEWED_FIXED_COMMAND',2,'a'*32,spawn=lambda *a,**k:tty,shell_user='root')
        self.assertEqual((r['shell_user'],r['context_uid']),('root',0))
        self.assertIn('/usr/bin/id -u',tty.sent[1])
        self.assertTrue(tty.sent[2].startswith('REVIEWED_FIXED_COMMAND;'))
        self.assertIsNotNone(re.search(tty.patterns[1][0],'root@plc:/home/kevin# '))
        self.assertIsNone(re.search(tty.patterns[1][0],'kevin@plc:~$ '))
        self.assertFalse(any(v in ' '.join(tty.sent) for v in ('sudo','passwd','su -','login ')))

    def test_mismatched_root_uid_never_sends_intended_command(self):
        class UID:
            def group(self,n):return '1000'
        tty=TTY([(0,''),(0,''),(0,'',UID())])
        with self.assertRaises(serial.SerialFailure) as r:
            serial.execute('plc','DO_NOT_SEND',2,'a'*32,spawn=lambda *a,**k:tty,shell_user='root')
        self.assertEqual(r.exception.kind,'context_uid_mismatch')
        self.assertFalse(any('DO_NOT_SEND' in s for s in tty.sent));self.assertTrue(tty.closed)

    def test_root_profile_requires_existing_login_and_bounded_identity(self):
        for outcomes,kind in (([(0,''),(1,'login: ')],'login_required'),
                              ([(0,''),(0,''),pexpect.TIMEOUT('uid')],'context_identity_timeout')):
            tty=TTY(outcomes)
            with self.subTest(kind=kind),self.assertRaises(serial.SerialFailure) as r:
                serial.execute('drives','DO_NOT_SEND',2,'a'*32,spawn=lambda *a,**k:tty,shell_user='root')
            self.assertEqual(r.exception.kind,kind)
            self.assertFalse(any('DO_NOT_SEND' in s for s in tty.sent));self.assertTrue(tty.closed)
        with self.assertRaises(ValueError):serial.execute('fw','true',shell_user='root')

    def test_root_identity_probe_cannot_restart_the_total_deadline(self):
        tty=TTY([(0,''),(0,''),(0,'',Match())]);original=tty.expect;clock=[10.]
        def expect(pattern,timeout):
            result=original(pattern,timeout)
            if isinstance(pattern,str) and '__SORTER_UID_' in pattern:clock[0]=12.
            return result
        tty.expect=expect
        with patch.object(serial.time,'monotonic',side_effect=lambda:clock[0]), \
                self.assertRaises(serial.SerialFailure) as r:
            serial.execute('plc','DO_NOT_SEND',2,'a'*32,spawn=lambda *a,**k:tty,
                           shell_user='root',total_timeout=1)
        self.assertEqual(r.exception.kind,'context_identity_timeout')
        self.assertFalse(any('DO_NOT_SEND' in s for s in tty.sent));self.assertTrue(tty.closed)

    def test_total_deadline_cannot_restart_for_each_expect(self):
        tty=TTY([(0,''),(0,'')])
        with patch.object(serial.time,'monotonic',side_effect=[10,10,10.7,11.1]):
            with self.assertRaises(serial.SerialFailure) as caught:
                serial.execute('drives','true',20,'a'*32,spawn=lambda *a,**k:tty,total_timeout=1)
        self.assertEqual(caught.exception.kind,'command_timeout')
        self.assertTrue(tty.closed)
        self.assertEqual(tty.sent[-1],'\x1d')
        self.assertFalse(any(value.startswith('true;') for value in tty.sent))

    def call(self, outcomes):
        tty = TTY(outcomes)
        try:
            result = serial.execute("drives", "true", 2, "a" * 32, spawn=lambda *a, **k: tty)
            return tty, result
        except serial.SerialFailure as exc:
            return tty, exc

    def test_success_detaches_without_logout(self):
        tty, result = self.call([(0, ""), (0, ""), (0, "ok", Match()), (0, "")])
        self.assertTrue(result["prompt"])
        self.assertTrue(tty.closed)
        self.assertEqual(tty.sent[-1], "\x1d")
        self.assertFalse(any("exit" in item or "logout" in item for item in tty.sent))

    def test_missing_marker(self):
        tty, result = self.call([(0, ""), (0, ""), (1, "prompt")])
        self.assertEqual(result.kind, "missing_completion_marker")
        self.assertTrue(tty.closed)

    def test_missing_prompt(self):
        tty, result = self.call([(0, ""), (0, ""), (0, "ok", Match()), pexpect.TIMEOUT("wait")])
        self.assertEqual(result.kind, "missing_prompt")
        self.assertTrue(tty.closed)

    def test_pager_like_timeout(self):
        tty = TTY([(0, ""), (0, ""), pexpect.TIMEOUT("wait")])
        original_expect = tty.expect

        def expect(pattern, timeout):
            try:
                return original_expect(pattern, timeout)
            except pexpect.TIMEOUT:
                tty.before = "--More--"
                raise

        tty.expect = expect
        with self.assertRaises(serial.SerialFailure) as caught:
            serial.execute("drives", "true", 2, "a" * 32, spawn=lambda *a, **k: tty)
        self.assertEqual(caught.exception.kind, "pager_like")

    def test_zero_output_marker_survives_terminal_mode_escape(self):
        tty, result = self.call([(0, ""), (0, ""), (0, "", Match()), (0, "")])
        self.assertTrue(result["completion_marker"])
        marker = tty.patterns[2][0]
        self.assertIsNotNone(re.search(marker,
            "\x1b[?2004l\r__SORTER_RC_" + "a" * 32 + "__0\r\n"))


if __name__ == "__main__":
    unittest.main()

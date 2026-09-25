"""Serial completion and prompt classification with a fake persistent console."""
import pathlib
import re
import sys
import unittest

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


class SerialTests(unittest.TestCase):
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

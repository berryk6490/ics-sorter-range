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

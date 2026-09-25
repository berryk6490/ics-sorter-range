"""Run-bound fixture approval and restorative service calls, all faked."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import live_accumulation_fixture as fixture


class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fixture"
        self.run_id = "run_12345678"
        self.case = "lane_hold"
        ledger = patch.object(fixture, "APPROVAL_LEDGER", Path(self.temp.name) / "approvals.jsonl")
        ledger.start()
        self.addCleanup(ledger.stop)

    def approved(self, valid_for=120):
        with patch.object(fixture, "service_active", return_value=True), \
             patch.object(fixture, "count_unflagged", return_value=["normal"]):
            return fixture.authorize(self.root, self.run_id, self.case,
                                     "approval_12345678", valid_for)

    def claim(self, record):
        fixture.atomic(self.root / "claimed.json",
                       {"run_id": self.run_id, "scenario": self.case,
                        "authorization_id": record["authorization_id"]})

    def test_exact_sudo_argv_no_shell_or_path_resolved_command(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Mock(returncode=0)
        with patch.object(fixture.subprocess, "run", side_effect=run):
            fixture.systemctl("stop")
            fixture.systemctl("start")
        self.assertEqual([argv for argv, _ in calls], [
            ["sudo", "-n", "/usr/bin/systemctl", "stop", "sorter-plant.service"],
            ["sudo", "-n", "/usr/bin/systemctl", "start", "sorter-plant.service"]])
        self.assertTrue(all("shell" not in kwargs and kwargs["check"] for _, kwargs in calls))
        with self.assertRaises(ValueError):
            fixture.systemctl("restart")
        with patch.object(fixture.subprocess, "run", return_value=Mock(returncode=0)) as status:
            self.assertTrue(fixture.service_active())
        self.assertEqual(status.call_args.args[0],
                         ["/usr/bin/systemctl", "is-active", "--quiet",
                          "sorter-plant.service"])
        self.assertNotIn("shell", status.call_args.kwargs)

    def test_missing_stale_mismatched_reused_and_malformed_rejected(self):
        with self.assertRaises(FileNotFoundError):
            fixture.validate_authorization(self.root, self.run_id, self.case)
        record = self.approved()
        self.assertEqual(record["stop_argv"], list(fixture.STOP_ARGV))
        with self.assertRaises(ValueError):
            fixture.validate_authorization(self.root, "other_12345678", self.case)
        with self.assertRaises(ValueError):
            fixture.validate_authorization(self.root, self.run_id, "merge_hold")
        copied = self.root.parent / "copied"
        copied.mkdir()
        (copied / "authorization.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "binding"):
            fixture.validate_authorization(copied, self.run_id, self.case)
        with patch.object(fixture.time, "time_ns", return_value=record["expires_wall_ns"] + 1):
            with self.assertRaisesRegex(ValueError, "stale"):
                fixture.validate_authorization(self.root, self.run_id, self.case)
        self.claim(record)
        with self.assertRaisesRegex(ValueError, "already used"):
            fixture.validate_authorization(self.root, self.run_id, self.case)
        self.assertEqual(fixture.validate_authorization(
            self.root, self.run_id, self.case, require_fresh=False)["authorization_id"],
            record["authorization_id"])
        self.assertNotEqual(record["authorization_id"], record["approval_id"])
        (self.root / "claimed.json").unlink()
        bad = dict(record, stop_argv=["sudo", "-n", "systemctl", "stop", fixture.SERVICE])
        (self.root / "authorization.json").write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            fixture.validate_authorization(self.root, self.run_id, self.case)

    def test_second_authorization_for_same_directory_is_rejected(self):
        self.approved()
        with patch.object(fixture, "service_active", return_value=True), \
             patch.object(fixture, "count_unflagged", return_value=["normal"]):
            with self.assertRaises(FileExistsError):
                fixture.authorize(self.root, self.run_id, self.case,
                                  "approval_other123", 120)

    def test_approval_receipt_cannot_authorize_another_run_or_scenario(self):
        self.approved()
        with patch.object(fixture, "service_active", return_value=True), \
             patch.object(fixture, "count_unflagged", return_value=["normal"]):
            with self.assertRaisesRegex(ValueError, "already used"):
                fixture.authorize(self.root.parent / "second", "run_other123",
                                  "merge_hold", "approval_12345678", 120)

    def test_claim_is_atomic_and_prevents_second_launch(self):
        record = self.approved()
        fake_child = Mock(pid=456)
        with patch.object(fixture.subprocess, "Popen", return_value=fake_child):
            self.assertEqual(fixture.main(["launch", "--directory", str(self.root),
                                           "--run-id", self.run_id, "--case", self.case,
                                           "--duration", "60"]), 0)
            claim = json.loads((self.root / "claimed.json").read_text())
            self.assertEqual(claim["authorization_id"], record["authorization_id"])
            with self.assertRaises(ValueError):
                fixture.main(["launch", "--directory", str(self.root),
                              "--run-id", self.run_id, "--case", self.case,
                              "--duration", "60"])

    def test_expired_claim_never_calls_sudo(self):
        record = self.approved()
        self.claim(record)
        args = Mock(directory=str(self.root), run_id=self.run_id,
                    case=self.case, duration=30)
        with patch.object(fixture.signal, "signal"), \
             patch.object(fixture.time, "time_ns", return_value=record["expires_wall_ns"] + 1), \
             patch.object(fixture, "service_active", return_value=True), \
             patch.object(fixture, "count_unflagged", return_value=["normal"]), \
             patch.object(fixture, "systemctl") as sudo:
            code = fixture.work(args)
        self.assertEqual(code, 1)
        sudo.assert_not_called()
        terminal = json.loads((self.root / "terminal.json").read_text())
        self.assertIn("expired", terminal["error"])
        self.assertFalse(terminal["restoration_attempted"])

    def worker(self, *, deadline=False, abort=False, start_failure=False, fixture_failure=False):
        record = self.approved()
        self.claim(record)
        args = Mock(directory=str(self.root), run_id=self.run_id,
                    case=self.case, duration=30)
        child = Mock(pid=12345, returncode=None)
        child.poll.return_value = None
        calls = []
        def sudo(action):
            calls.append(action)
            if action == "start" and start_failure:
                raise RuntimeError("start denied")
        def sleep(_duration):
            if abort:
                fixture.STOP = True
            else:
                fixture.STOP = True
        context = [patch.object(fixture.signal, "signal"),
                   patch.object(fixture, "service_active", return_value=True),
                   patch.object(fixture, "count_unflagged", return_value=["normal"]),
                   patch.object(fixture, "systemctl", side_effect=sudo),
                   patch.object(fixture.subprocess, "Popen", return_value=child),
                   patch.object(fixture, "ticks", return_value=777),
                   patch.object(fixture.time, "sleep", side_effect=sleep)]
        if fixture_failure:
            context.append(patch.object(fixture, "fixture_command",
                                        side_effect=RuntimeError("fixture failed")))
        if deadline:
            context.append(patch.object(fixture.time, "monotonic", side_effect=[0, 31, 31]))
        entered = []
        try:
            for manager in context:
                entered.append(manager)
                manager.__enter__()
            code = fixture.work(args)
        finally:
            for manager in reversed(entered):
                manager.__exit__(None, None, None)
        terminal = json.loads((self.root / "terminal.json").read_text())
        return code, terminal, calls, child

    def test_normal_and_abort_restore_without_orphan(self):
        code, terminal, calls, child = self.worker(abort=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["stop", "start"])
        self.assertTrue(terminal["service_restored"])
        self.assertEqual(terminal["reason"], "signal")
        child.terminate.assert_called_once()
        child.wait.assert_called_once()

    def test_timeout_restores_and_reports_timeout(self):
        code, terminal, calls, child = self.worker(deadline=True)
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["stop", "start"])
        self.assertIn("TimeoutError", terminal["error"])
        self.assertTrue(terminal["service_restored"])
        child.terminate.assert_called_once()

    def test_fixture_failure_still_attempts_start(self):
        code, terminal, calls, _ = self.worker(fixture_failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["stop", "start"])
        self.assertIn("fixture failed", terminal["error"])

    def test_restoration_failure_is_separate(self):
        code, terminal, calls, child = self.worker(start_failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["stop", "start"])
        self.assertIsNone(terminal["error"])
        self.assertIn("start denied", terminal["cleanup_errors"][0])
        self.assertFalse(terminal["service_restored"])
        child.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()

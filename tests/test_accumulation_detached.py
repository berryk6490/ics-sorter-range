"""Detached lifecycle and exclusive-console regression tests; no real PLC writes."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import live_accumulation_detached as guest

SCRIPT = Path(guest.__file__).resolve()
RUNNER_HASH = hashlib.sha256(SCRIPT.with_name("live_accumulation.py").read_bytes()).hexdigest()


class FakePLC(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class PLCHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(20)
        self.request.sendall(b"ok")


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api":
            self.send_error(404)
            return
        body = b'{"connected":true,"accumulation_mode":false}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DetachedIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = FakePLC(("127.0.0.1", 0), PLCHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        # The child talks to a controlled TCP endpoint via a narrow fake
        # Modbus adapter injected only into its process environment.
        (self.root / "sitecustomize.py").write_text('''
import os, socket, sys, types
package=types.ModuleType('pymodbus')
client_module=types.ModuleType('pymodbus.client')
package.client=client_module
sys.modules['pymodbus']=package
sys.modules['pymodbus.client']=client_module
class Reply:
    def __init__(self, values): self.registers=values; self.bits=values
    def isError(self): return False
class Client:
    def __init__(self, *a, **k): self.sock=None
    def connect(self):
        self.sock=socket.create_connection(('127.0.0.1',int(os.environ['FAKE_PLC_PORT'])))
        self.sock.sendall(b'ping'); return self.sock.recv(2)==b'ok'
    def close(self): self.sock.close()
    def read_holding_registers(self,address,count=1,slave=1):
        return Reply([24114 if address==249 else 0]*count)
    def read_coils(self,address,count=1,slave=1): return Reply([False]*count)
client_module.ModbusTcpClient=Client
''')
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(self.root) + os.pathsep + str(SCRIPT.parent)
        self.env["FAKE_PLC_PORT"] = str(self.server.server_address[1])
        self.run_id = "test_" + os.urandom(8).hex()
        self.directory = self.root / "run"
        self.base = [sys.executable, str(SCRIPT)]

    def call(self, action, *extra, check=True):
        argv = self.base + [action, "--directory", str(self.directory),
                            "--run-id", self.run_id, "--case", "smoke", *extra]
        return subprocess.run(argv, env=self.env, capture_output=True, text=True,
                              check=check, timeout=8)

    def await_file(self, name, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            path = self.directory / name
            if path.exists():
                return json.loads(path.read_text())
            time.sleep(.03)
        log = (self.directory / "stdout.log").read_text() if (self.directory / "stdout.log").exists() else ""
        terminal = (self.directory / "terminal.json").read_text() if (self.directory / "terminal.json").exists() else ""
        self.fail(f"{name} did not appear: {terminal} {log}")

    def control(self, action, pid):
        return self.call(action, "--pid", str(pid["pid"]),
                         "--start-ticks", str(pid["start_ticks"]))

    def test_detached_launch_frees_console_proxy_checkpoint_release_and_exit(self):
        serial = threading.Lock()
        with serial:
            launched = json.loads(self.call("launch", "--commit", "fake-commit",
                                            "--runner-sha256", RUNNER_HASH,
                                            "--startup-timeout", "5",
                                            "--hold-timeout", "5").stdout)
        self.assertTrue(serial.acquire(blocking=False), "serial shell stayed occupied")
        serial.release()
        pid = self.await_file("pid.json")
        ready = self.await_file("ready.json")
        self.assertEqual(launched["pid"], pid["pid"])
        self.assertFalse(ready["mutation_started"])
        self.assertFalse((self.directory / "checkpoint.json").exists())
        wrong = self.call("authorize", "--pid", str(pid["pid"]),
                          "--start-ticks", str(pid["start_ticks"] + 1), check=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.control("authorize", pid)
        self.control("begin", pid)
        checkpoint = self.await_file("checkpoint.json")
        self.assertEqual(checkpoint["run_id"], self.run_id)
        self.assertFalse((self.directory / "terminal.json").exists())
        with serial:
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                from urllib.request import urlopen
                with urlopen(f"http://127.0.0.1:{proxy.server_address[1]}/api") as response:
                    self.assertTrue(json.load(response)["connected"])
            finally:
                proxy.shutdown(); proxy.server_close(); thread.join()
        wrong_release = {**guest.identity(self.run_id, "smoke", pid["pid"], pid["start_ticks"]),
                         "run_id": "other_run"}
        guest.atomic(self.directory / "release.json", wrong_release)
        terminal = self.await_file("terminal.json", 6)
        self.assertEqual(terminal["status"], "scenario_failure")
        self.assertIn("identity mismatch", terminal["original_error"])
        end = time.monotonic() + 2
        while time.monotonic() < end and guest.inspect(self.directory, guest.identity(
                self.run_id, "smoke", pid["pid"], pid["start_ticks"]))["alive"]:
            time.sleep(.02)
        self.assertFalse(guest.inspect(self.directory, guest.identity(
            self.run_id, "smoke", pid["pid"], pid["start_ticks"]))["alive"])

    def test_correct_release_completes_and_no_orphan(self):
        self.call("launch", "--commit", "fake-commit", "--runner-sha256", RUNNER_HASH,
                  "--startup-timeout", "5", "--hold-timeout", "5")
        pid = self.await_file("pid.json")
        self.await_file("ready.json")
        self.control("authorize", pid); self.control("begin", pid)
        self.await_file("checkpoint.json")
        self.control("release", pid)
        terminal = self.await_file("terminal.json")
        self.assertEqual(terminal["status"], "complete")
        end = time.monotonic() + 3
        while time.monotonic() < end and guest.inspect(self.directory, guest.identity(
                self.run_id, "smoke", pid["pid"], pid["start_ticks"]))["alive"]:
            time.sleep(.02)
        self.assertFalse(guest.inspect(self.directory, guest.identity(
            self.run_id, "smoke", pid["pid"], pid["start_ticks"]))["alive"])

    def test_timeout_and_matching_abort(self):
        self.call("launch", "--commit", "fake-commit", "--runner-sha256", RUNNER_HASH,
                  "--startup-timeout", "2", "--hold-timeout", "1")
        pid = self.await_file("pid.json")
        self.await_file("ready.json")
        self.control("authorize", pid); self.control("begin", pid)
        self.await_file("checkpoint.json")
        terminal = self.await_file("terminal.json", 3)
        self.assertEqual(terminal["status"], "timeout")
        self.assertIn("release", terminal["original_error"])


class DetachedGuards(unittest.TestCase):
    def test_checkpoint_requires_validated_held_state(self):
        import test_accumulation_runner
        runner = test_accumulation_runner.load_runner()
        sample = {"rows": [{"lane": 1, "state": 2, "quality": 1,
                            "motion": 2, "zone": 3, "dwell": 12},
                           {"lane": 0, "state": 0, "quality": 0,
                            "motion": 0, "zone": 0, "dwell": 0}],
                  "zone_fault": 0, "plant_fault": 0,
                  "photoeye_faults": [0, 0, 0]}
        self.assertTrue(runner.checkpoint_valid("lane_hold", sample))
        sample["rows"][0]["quality"] = 2
        self.assertFalse(runner.checkpoint_valid("lane_hold", sample))
        sample["rows"][0]["quality"] = 1
        sample["zone_fault"] = 2
        self.assertFalse(runner.checkpoint_valid("lane_hold", sample))

    def test_wrong_release_and_start_tick_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            own = guest.identity("test_12345678", "smoke")
            guest.atomic(root / "pid.json", own)
            bad = dict(own, start_ticks=own["start_ticks"] + 1)
            with self.assertRaises(ValueError):
                guest.inspect(root, bad)
            guest.atomic(root / "release.json", dict(own, run_id="wrong_12345678"))
            with self.assertRaises(ValueError):
                guest.wait_marker(root / "release.json", own, .2, "release")

    def test_fixture_commands_are_fixed_and_bounded(self):
        import live_accumulation_fixture as fixture
        lane = fixture.fixture_command("lane_hold")
        merge = fixture.fixture_command("merge_hold")
        self.assertEqual(lane[-2:], ["--block-duration", "60"])
        self.assertIn("--block-merge", merge)
        with self.assertRaises(ValueError):
            fixture.fixture_command("normal")

    def test_scenario_and_cleanup_failures_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            own = guest.identity("test_12345678", "lane_hold")
            guest.atomic(Path(temp) / "authorize.json", own)
            guest.atomic(Path(temp) / "begin.json", own)
            args = Mock(directory=temp, run_id=own["run_id"], case="lane_hold",
                        commit="test", runner_sha256="hash", startup_timeout=1,
                        hold_timeout=1)
            fake = types.ModuleType("live_accumulation")
            def fail_run(_case, **kwargs):
                kwargs["cleanup_report"].update(errors=["reset failed"],
                                                 original_failure="RuntimeError('scenario failed')")
                raise RuntimeError("scenario failed")
            fake.run = fail_run
            with patch.object(guest, "preflight", return_value={"plc_identity": 24114}), \
                 patch.dict(sys.modules, {"live_accumulation": fake}), \
                 patch.object(guest.signal, "signal"):
                code = guest.worker(args)
            terminal = json.loads((Path(temp) / "terminal.json").read_text())
            self.assertEqual(code, 1)
            self.assertEqual(terminal["status"], "scenario_failure")
            self.assertTrue(terminal["cleanup_failed"])
            self.assertEqual(terminal["cleanup"]["errors"], ["reset failed"])
            self.assertIn("scenario failed", terminal["original_error"])


if __name__ == "__main__":
    unittest.main()

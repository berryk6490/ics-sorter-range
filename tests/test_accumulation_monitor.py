"""Readiness ordering, failure closure, PID ownership and live-file tests."""
import argparse
import base64
from collections import deque
import json
import os
from pathlib import Path
import shlex
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from accumulation_monitor_control import (Controller, GuestCommandError,
                                          SerialGuest, load_control)
from live_accumulation_monitor import inspect_process, run_monitor

MONITOR = Path(__file__).with_name("live_accumulation_monitor.py")


class Reply:
    def __init__(self, values, coils=False):
        self.registers = values if not coils else []
        self.bits = values if coils else []

    def isError(self):
        return False


class FakeClient:
    def __init__(self, _host, port, timeout):
        self.port = port
        self.timeout = timeout
        self.closed = False

    def connect(self):
        return True

    def read_holding_registers(self, address, count, slave):
        values = [0] * count
        if address == 249:
            values[0] = 24115
        if address == 784:
            values[1] = 4  # commit may advance while mode is off
        return Reply(values)

    def read_coils(self, address, count, slave):
        return Reply([False] * count, coils=True)

    def close(self):
        self.closed = True


def arguments(directory, run_id="test_run_1234", duration=.5):
    return argparse.Namespace(plc_host="127.0.0.1", plc_port=502,
                              run_id=run_id, output=str(directory / "samples.jsonl"),
                              ready=str(directory / "ready.json"),
                              pid_file=str(directory / "pid.json"),
                              result=str(directory / "terminal.json"),
                              duration=duration, interval=.08)


class MonitorUnitTest(unittest.TestCase):
    def test_fake_modbus_initial_sample_is_durable_before_ready(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            args = arguments(directory)
            observed = []
            from live_accumulation_monitor import atomic_new_json

            def observe(path, value):
                if Path(path) == Path(args.ready):
                    records = [json.loads(line) for line in Path(args.output).read_text().splitlines()]
                    self.assertEqual([row["event"] for row in records[:3]],
                                     ["monitor_starting", "initial_sample", "monitor_ready"])
                    self.assertEqual(records[1]["sample"]["validated_ready_mask"], 0)
                    observed.append(records[1]["run_id"])
                atomic_new_json(path, value)

            with patch("live_accumulation_monitor.atomic_new_json", side_effect=observe):
                result = run_monitor(args, FakeClient, install_signals=False)
            self.assertEqual(result, 0)
            self.assertEqual(observed, [args.run_id])
            self.assertEqual(json.loads(Path(args.result).read_text())["status"], "complete")

    def test_connection_failure_has_no_ready_marker(self):
        class Refused(FakeClient):
            def connect(self):
                return False
        with tempfile.TemporaryDirectory() as name:
            args = arguments(Path(name))
            self.assertEqual(run_monitor(args, Refused, install_signals=False), 1)
            self.assertFalse(Path(args.ready).exists())
            self.assertEqual(json.loads(Path(args.result).read_text())["status"], "error")

    def test_initial_read_failure_has_no_ready_marker(self):
        class Broken(FakeClient):
            def read_holding_registers(self, address, count, slave):
                raise IOError("read failed")
        with tempfile.TemporaryDirectory() as name:
            args = arguments(Path(name))
            self.assertEqual(run_monitor(args, Broken, install_signals=False), 1)
            self.assertFalse(Path(args.ready).exists())
            self.assertIn("read failed", Path(args.output).read_text())

    def test_existing_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            args = arguments(Path(name))
            Path(args.ready).write_text("stale")
            with self.assertRaises(FileExistsError):
                run_monitor(args, FakeClient, install_signals=False)
            self.assertFalse(Path(args.output).exists())


class InspectProcessRaceTest(unittest.TestCase):
    """Model /proc changing between the state and cmdline reads."""

    def inspect(self, states, cmdline=b"", start_ticks=12345):
        pid, run_id = 4242, "run_12345678"
        pid_file = Path("/fake/monitor-pid.json")
        proc = Path(f"/proc/{pid}")
        samples = deque(states)
        original_stat = Path.stat

        def read_text(path, *args, **kwargs):
            if path == pid_file:
                return json.dumps({"run_id": run_id, "pid": pid,
                                   "start_ticks": start_ticks})
            self.assertEqual(path, proc / "stat")
            sample = samples.popleft()
            if sample is None:
                raise FileNotFoundError(path)
            state, ticks = sample
            return f"{pid} (monitor.py) " + " ".join(
                [state] + ["0"] * 18 + [str(ticks)])

        def stat(path, *args, **kwargs):
            if path == proc:
                return SimpleNamespace(st_uid=os.getuid())
            return original_stat(path, *args, **kwargs)

        def read_bytes(path, *args, **kwargs):
            self.assertEqual(path, proc / "cmdline")
            return cmdline

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text), \
             patch.object(Path, "stat", autospec=True, side_effect=stat), \
             patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
            result = inspect_process(pid_file, run_id, pid)
        self.assertFalse(samples, "unexpected number of /proc/stat reads")
        return result

    def test_empty_argv_after_exit_is_not_identity_error(self):
        for state in ("Z", "X"):
            with self.subTest(state=state):
                result = self.inspect([("R", 12345), ("R", 12345),
                                       (state, 12345)])
                self.assertEqual(result, {"run_id": "run_12345678", "pid": 4242,
                                          "start_ticks": 12345, "alive": False,
                                          "matches": False})

    def test_empty_argv_with_running_process_is_identity_error(self):
        with self.assertRaisesRegex(ValueError, "PID does not run the canonical monitor"):
            self.inspect([("R", 12345)] * 3)

    def test_different_script_is_identity_error(self):
        with self.assertRaisesRegex(ValueError, "PID does not run the canonical monitor"):
            self.inspect([("R", 12345)] * 2,
                         b"/usr/bin/python3\0/elsewhere/other.py\0--run-id\0run_12345678\0")

    def test_start_ticks_mismatch_is_still_reused(self):
        with self.assertRaisesRegex(ValueError, "monitor PID was reused"):
            self.inspect([("R", 99999)])

    def test_empty_argv_with_reused_zombie_is_still_reused(self):
        with self.assertRaisesRegex(ValueError, "monitor PID was reused"):
            self.inspect([("R", 12345), ("R", 12345), ("Z", 99999)])

    def test_empty_argv_after_proc_disappears_is_exited(self):
        result = self.inspect([("R", 12345), ("R", 12345), None])
        self.assertEqual((result["alive"], result["matches"]), (False, False))


class FakePLC(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakePLCHandler(socketserver.BaseRequestHandler):
    def exact(self, count):
        data = b""
        while len(data) < count:
            part = self.request.recv(count - len(data))
            if not part:
                raise EOFError
            data += part
        return data

    def handle(self):
        try:
            while True:
                header = self.exact(7)
                tid, proto, length, unit = struct.unpack(">HHHB", header)
                pdu = self.exact(length - 1)
                function, address, count = struct.unpack(">BHH", pdu[:5])
                if function == 3:
                    values = [24115 if address + index == 249 else
                              4 if address + index == 785 else 0
                              for index in range(count)]
                    reply = bytes([3, count * 2]) + struct.pack(">" + "H" * count, *values)
                elif function == 1:
                    reply = bytes([1, (count + 7) // 8]) + bytes((count + 7) // 8)
                else:
                    reply = bytes([function | 128, 1])
                self.request.sendall(struct.pack(">HHHB", tid, proto, len(reply) + 1, unit) + reply)
        except (EOFError, ConnectionError, OSError):
            return


class LocalGuest:
    """Test transport that preserves the controller's argv and process rules."""
    def __init__(self, env=None):
        self.processes = []
        self.commands = []
        self.env = env or {}

    def run(self, argv, timeout=20):
        self.commands.append(list(argv))
        if argv[0] == "mkdir":
            Path(argv[-1]).mkdir(mode=0o700)
            return ""
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise GuestCommandError(result.returncode, result.stdout + result.stderr)
        return result.stdout.strip()

    def launch(self, argv, log_path):
        self.commands.append(list(argv))
        stream = open(log_path, "wb")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   env={**os.environ, **self.env})
        stream.close()
        self.processes.append(process)
        return process.pid

    def read_file(self, path):
        return Path(path).read_bytes()


class ControllerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Host Python deliberately has no pymodbus; a tiny test-only client
        # speaks to the controlled TCP endpoint without changing the monitor.
        package = self.root / "pymodbus"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "client.py").write_text('''\
import socket
import struct

class Reply:
    def __init__(self, values, coils=False):
        self.registers = [] if coils else values
        self.bits = values if coils else []
    def isError(self):
        return False

class ModbusTcpClient:
    def __init__(self, host, port=502, timeout=2):
        self.host, self.port, self.timeout = host, port, timeout
        self.socket = None
        self.sequence = 0
    def connect(self):
        self.socket = socket.create_connection((self.host, self.port), self.timeout)
        return True
    def exact(self, count):
        result = b''
        while len(result) < count:
            part = self.socket.recv(count-len(result))
            if not part: raise EOFError('fake PLC closed')
            result += part
        return result
    def read(self, function, address, count):
        self.sequence += 1
        pdu = struct.pack('>BHH', function, address, count)
        self.socket.sendall(struct.pack('>HHHB', self.sequence, 0, len(pdu)+1, 1)+pdu)
        header = self.exact(7)
        length = struct.unpack('>HHHB', header)[2]
        pdu = self.exact(length-1)
        if pdu[0] != function: raise IOError('Modbus exception')
        return pdu[2:]
    def read_holding_registers(self, address, count=1, slave=1):
        payload = self.read(3, address, count)
        return Reply(list(struct.unpack('>'+'H'*count, payload)))
    def read_coils(self, address, count=1, slave=1):
        payload = self.read(1, address, count)
        return Reply([bool(payload[i//8] & (1 << (i%8))) for i in range(count)], True)
    def close(self):
        if self.socket: self.socket.close()
''')
        self.guest = LocalGuest({"PYTHONPATH": str(self.root)})
        self.controller = Controller(drives=self.guest, scada=self.guest)
        self.plc = FakePLC(("127.0.0.1", 0), FakePLCHandler)
        self.thread = threading.Thread(target=self.plc.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.plc.server_close)
        self.addCleanup(self.plc.shutdown)

    def start(self, duration=3):
        return self.controller.start(self.root / "evidence", "127.0.0.1",
                                     self.plc.server_address[1], duration, .08,
                                     guest_base=str(self.root),
                                     guest_python=sys.executable,
                                     guest_monitor=str(MONITOR))

    def test_ready_is_observed_while_process_and_jsonl_are_live(self):
        control_path = self.start()
        control = load_control(control_path)
        proof = self.controller.probe(control_path, 5)
        self.assertEqual(proof["run_id"], control["run_id"])
        self.assertIsNone(self.guest.processes[0].poll())
        self.assertFalse(Path(control["paths"]["result"]).exists())
        live_output = Path(control["paths"]["output"]).read_text()
        self.assertIn('"event":"initial_sample"', live_output)
        self.assertIn('"event":"monitor_ready"', live_output)
        self.assertEqual(proof["initial_sample"]["sample"]["validated_ready_mask"], 0)
        result = self.controller.collect(control_path, stop=True)
        self.assertEqual(result["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)
        self.assertFalse(result["orphan"])
        self.assertEqual(result["terminal"]["status"], "complete")
        self.assertLess(proof["observed_monotonic_ns"], time.monotonic_ns())

    def test_wrong_ready_run_id_rejected_and_cleanup_has_no_orphan(self):
        control_path = self.start()
        self.controller.probe(control_path, 5)
        control = load_control(control_path)
        marker = Path(control["paths"]["ready"])
        value = json.loads(marker.read_text())
        value["run_id"] = "wrong_run_0001"
        marker.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "stale or mismatched ready"):
            self.controller.probe(control_path, 1)
        result = self.controller.collect(control_path, stop=True)
        self.assertEqual(result["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)

    def test_dead_pid_and_early_exit_prevent_launch(self):
        control_path = self.start(duration=.4)
        self.guest.processes[0].wait(timeout=3)
        with self.assertRaises(RuntimeError):
            self.controller.probe(control_path, 1)
        fake_scada = LocalGuest()
        self.controller.scada = fake_scada
        outcome = self.controller.scenario(control_path, "normal", probe_timeout=1)
        self.assertTrue(outcome["scenario_failure"])
        self.assertEqual(fake_scada.commands, [])
        result = self.controller.collect(control_path, stop=True)
        self.assertEqual(result["cleanup_errors"], [])

    def test_timeout_prevents_scenario_launch_and_cleanup_is_separate(self):
        control_path = self.start()
        fake_scada = LocalGuest()
        self.controller.scada = fake_scada
        with patch.object(self.controller, "probe", side_effect=TimeoutError("not ready")):
            outcome = self.controller.scenario(control_path, "normal")
        self.assertIn("not ready", outcome["scenario_failure"])
        self.assertEqual(fake_scada.commands, [])
        self.assertEqual(outcome["monitor_cleanup"]["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)

    def test_scenario_failure_and_cleanup_failure_are_both_preserved(self):
        control_path = self.start()
        with patch.object(self.controller, "probe", side_effect=TimeoutError("not ready")), \
             patch.object(self.controller, "collect", return_value={
                 "cleanup_errors": ["simulated cleanup failure"]}):
            outcome = self.controller.scenario(control_path, "normal")
        self.assertIn("not ready", outcome["scenario_failure"])
        self.assertEqual(outcome["monitor_cleanup"]["cleanup_errors"],
                         ["simulated cleanup failure"])
        actual = self.controller.collect(control_path, stop=True)
        self.assertEqual(actual["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)

    def test_cleanup_wrong_pid_does_not_signal_other_process(self):
        control_path = self.start()
        self.controller.probe(control_path, 5)
        control = load_control(control_path)
        record = Path(control["paths"]["pid_file"])
        value = json.loads(record.read_text())
        value["pid"] += 1
        record.write_text(json.dumps(value))
        result = self.controller.collect(control_path, stop=True)
        self.assertTrue(result["cleanup_errors"])
        self.assertIsNone(self.guest.processes[0].poll())
        record.write_text(json.dumps({**value, "pid": control["pid"]}))
        clean = self.controller.collect(control_path, stop=True)
        self.assertEqual(clean["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)

    def test_missing_terminal_record_is_a_cleanup_failure(self):
        control_path = self.start(duration=.4)
        self.guest.processes[0].wait(timeout=3)
        control = load_control(control_path)
        terminal = Path(control["paths"]["result"])
        saved = terminal.read_bytes()
        terminal.unlink()
        failure = self.controller.collect(control_path, timeout=.3, stop=False)
        self.assertTrue(any("terminal result" in error for error in failure["cleanup_errors"]))
        terminal.write_bytes(saved)
        restored = self.controller.collect(control_path, timeout=1, stop=False)
        self.assertEqual(restored["cleanup_errors"], [])

    def test_scenario_launch_after_proof_only(self):
        control_path = self.start()
        actions = []
        real_probe = self.controller.probe

        def probe(*args):
            actions.append("ready")
            return real_probe(*args)

        class ScenarioGuest:
            def run(self, argv, timeout=330):
                actions.append("scenario")
                return "scenario simulated"

        self.controller.scada = ScenarioGuest()
        with patch.object(self.controller, "probe", side_effect=probe):
            outcome = self.controller.scenario(control_path, "normal")
        self.assertEqual(actions, ["ready", "scenario"])
        self.assertIsNone(outcome["scenario_failure"])
        self.assertEqual(outcome["monitor_cleanup"]["cleanup_errors"], [])
        self.guest.processes[0].wait(timeout=3)

    def test_serial_launch_uses_one_quoted_argument_layer(self):
        guest = SerialGuest("drives")
        argv = ["/path with spaces/python", "/path/monitor.py", "--run-id",
                "id_1234", "--output", "/tmp/name with ' quote.jsonl"]
        commands = []

        def fake(command, timeout):
            commands.append(command)
            return "__MONITOR_LAUNCHED__1234"

        with patch.object(guest, "_run_shell", side_effect=fake):
            self.assertEqual(guest.launch(argv, "/tmp/log with spaces"), 1234)
        self.assertIn(shlex.join(argv), commands[0])
        self.assertIn(shlex.quote("/tmp/log with spaces"), commands[0])

    def test_serial_file_read_accepts_growth_and_terminal_escape(self):
        guest = SerialGuest("drives")
        payload = b'{"event":"initial_sample"}\n{"event":"sample"}\n'
        with patch.object(guest, "run", side_effect=[
                "\x1b[?2004l\r20",
                "\x1b[?2004l\r" + base64.b64encode(payload).decode()]):
            self.assertEqual(guest.read_file("/tmp/growing.jsonl"), payload)
        with patch.object(guest, "run", return_value="\x1b[?2004l\r0") as command:
            self.assertEqual(guest.read_file("/tmp/empty.log"), b"")
            command.assert_called_once()


if __name__ == "__main__":
    unittest.main()

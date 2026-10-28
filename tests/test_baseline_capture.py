"""Collector fixtures use synthetic files and inert Python children, never tcpdump."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import baseline_capture as capture
from test_pcap_modbus_summary import pcap


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.config = capture.load_config(capture.DEFAULT_CONFIG)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'synthetic'

    def collect_fixture(self, body, *, seconds=.15, grace=.1):
        # This process only writes the synthetic PCAP and test statistics.
        script = ("import pathlib,signal,sys,time\n"
                  f"pathlib.Path(sys.argv[1]).write_bytes({pcap([])!r})\n" + body)
        def argv(point, path, packets):
            return [sys.executable, '-c', script, str(path)]
        with patch.object(capture, 'capture_argv', side_effect=argv), \
                patch.object(capture, 'identity', return_value={'kind': 'SYNTHETIC; NOT DEPLOYED'}):
            return capture.collect(self.config, ['fw-l2'], self.directory, 'SYNTHETIC-fixture',
                                   seconds, 5000, 8192, grace, 'FIXTURE; no live clock measurement')

    def test_clean_time_bound_and_offline_integrity(self):
        got = self.collect_fixture(
            "def stop(*args):\n print('0 packets captured\\n0 packets received by filter\\n0 packets dropped by kernel',file=sys.stderr);sys.exit(0)\n"
            "signal.signal(signal.SIGINT,stop)\nwhile True: time.sleep(.01)\n")
        self.assertEqual(got['status'], 'COMPLETE')
        self.assertEqual(got['functional_outcome'], 'NOT RUN')
        self.assertEqual(capture.verify(self.directory)['status'], 'COMPLETE')
        (self.directory/'fw-l2.pcap').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'changed evidence'):
            capture.verify(self.directory)

    def test_permission_failure_keeps_exit_and_unknown_drops(self):
        got = self.collect_fixture("print('permission denied',file=sys.stderr)\nsys.exit(1)\n")
        receipt = got['points']['fw-l2']
        self.assertEqual(got['status'], 'INCOMPLETE')
        self.assertEqual(receipt['exit_status'], 1)
        self.assertIsNone(receipt['statistics']['kernel_dropped'])
        self.assertTrue((self.directory/'SHA256SUMS').exists())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            capture.verify(self.directory)

    def test_uncooperative_child_is_killed_within_bound(self):
        started = time.monotonic()
        got = self.collect_fixture("signal.signal(signal.SIGINT,signal.SIG_IGN)\nwhile True: time.sleep(.01)\n")
        self.assertLess(time.monotonic()-started, 2)
        self.assertEqual(got['points']['fw-l2']['status'], 'INCOMPLETE')
        self.assertIn(got['points']['fw-l2']['exit_status'], (-9, 137))
        self.assertEqual(got['status'], 'INCOMPLETE')

    def test_external_deadline_without_controller_supervision(self):
        inert = [sys.executable, '-c',
                 'import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(60)']
        with patch.object(capture, 'capture_argv', return_value=inert):
            argv = capture.bounded_argv({}, Path('/unused-SYNTHETIC'), 1, .15, .1)
        started = time.monotonic()
        process = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            self.assertNotEqual(process.wait(timeout=2), 0)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9); process.wait(timeout=1)
        self.assertLess(time.monotonic()-started, 2)

    def test_clean_shutdown_with_drops_is_still_incomplete(self):
        got = self.collect_fixture(
            "def stop(*args):\n print('0 packets captured\\n0 packets received by filter\\n1 packets dropped by kernel',file=sys.stderr);sys.exit(0)\n"
            "signal.signal(signal.SIGINT,stop)\nwhile True: time.sleep(.01)\n")
        self.assertEqual(got['points']['fw-l2']['exit_status'], 0)
        self.assertEqual(got['points']['fw-l2']['statistics']['kernel_dropped'], 1)
        self.assertEqual(got['status'], 'INCOMPLETE')

    def test_partial_multi_point_launch_stops_only_own_process(self):
        def argv(point, path, packets):
            return ([sys.executable, '-c', 'import time; time.sleep(60)']
                    if point['interface'] == 'vnet1' else ['/missing-SYNTHETIC-executable'])
        with patch.object(capture, 'identity', return_value={'kind': 'SYNTHETIC'}), \
                patch.object(capture, 'bounded_argv', side_effect=lambda p,o,n,s,g: argv(p,o,n)):
            got = capture.collect(self.config, ['fw-l2', 'plc-l1'], self.directory,
                                  'SYNTHETIC-fixture', .15, 1, 8192, .1, 'fixture')
        self.assertEqual(got['status'], 'INCOMPLETE')
        self.assertEqual(set(got['points']), {'fw-l2', 'plc-l1'})
        report=json.loads((self.directory/'supervision.json').read_text())
        self.assertEqual(report['cleanup'], 'PASS')
        self.assertEqual(capture.remaining_processes(report), [])
        self.assertIn('FileNotFoundError', got['errors']['primary'])

    def test_truncation_drops_and_early_exit_stay_incomplete(self):
        for body in (
            "pathlib.Path(sys.argv[1]).write_bytes(b'truncated')\n",
            "print('0 packets captured\\n0 packets received by filter\\n1 packets dropped by kernel',file=sys.stderr)\n",
            "print('0 packets captured\\n0 packets received by filter\\n0 packets dropped by kernel',file=sys.stderr)\n"):
            with self.subTest(body=body):
                self.directory = Path(self.temp.name)/str(time.monotonic_ns())
                got = self.collect_fixture(body)
                self.assertEqual(got['status'], 'INCOMPLETE')

    def test_launch_failure_and_missing_artifacts(self):
        with patch.object(capture, 'identity', return_value={'kind': 'SYNTHETIC'}), \
                patch.object(capture.subprocess, 'Popen', side_effect=FileNotFoundError('fixture missing')):
            got = capture.collect(self.config, ['fw-l2'], self.directory, 'SYNTHETIC-fixture',
                                  .1, 10, 8192, .1, 'fixture')
        self.assertIn('FileNotFoundError', got['errors']['primary'])
        self.assertEqual(got['status'], 'INCOMPLETE')
        (self.directory/'identity.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            capture.verify(self.directory)

    def test_hard_file_bound_keeps_failed_evidence(self):
        got = self.collect_fixture("pathlib.Path(sys.argv[1]).write_bytes(b'x'*100000)\n")
        self.assertLessEqual((self.directory/'fw-l2.pcap').stat().st_size, 8192)
        self.assertEqual(got['status'], 'INCOMPLETE')
        self.assertNotEqual(got['points']['fw-l2']['exit_status'], 0)

    def test_configuration_is_inert_and_guest_collection_rejected(self):
        with patch.object(capture.subprocess, 'Popen') as popen:
            cmd = capture.guest_command(self.config['points']['xle-local'],
                                        '/tmp/m2-fixture', 'fixture', 10, 5000, 8192, 3, self.config)
            self.assertIn('--signal=INT', cmd)
            self.assertIn('--fsize=8192:8192', cmd)
            self.assertIn('127.0.0.1', cmd)
            popen.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'verified host points only'):
                capture.collect(self.config, ['xle-local'], self.directory, 'fixture', 10, 5000, 8192, 3, 'fixture')
            popen.assert_not_called()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.config['points']['drives-local']['verified_in_handoff'])
        syntax = subprocess.run(['bash', '-n'], input=cmd, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_limits_and_overwrite_rejection(self):
        for limits in ((0,1,8192,1), (601,1,8192,1), (1,0,8192,1),
                       (1,1,2**30,1), (1,1,8192,0), (float('nan'),1,8192,1)):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                capture.limits(*limits)
        self.directory.mkdir()
        with self.assertRaises(FileExistsError):
            capture.collect(self.config, ['fw-l2'], self.directory, 'fixture', 1, 1, 8192, 1, 'fixture')

    def test_results_template_has_independent_not_run_fields(self):
        result = json.loads((capture.ROOT/'tests/milestone2_results.json').read_text())
        self.assertEqual(len(result['records']), 6)
        for row in result['records']:
            for key in ('functional_outcome','capture_completeness','service_restoration',
                        'plc_recovery','typed_postflight','orphan_checks','plan_restoration'):
                self.assertEqual(row[key], 'NOT RUN')
            self.assertEqual(row['evidence'], [])


if __name__ == '__main__':
    unittest.main()

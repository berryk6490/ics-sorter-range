"""Deterministic synthetic /proc races; no real captures or process signals.

The existing subprocess suite separately proves real descendant termination.
Here pinned incarnations, stale reads and denied paths are controlled explicitly.
"""
from copy import deepcopy
import signal
import unittest
from unittest.mock import Mock, patch

import baseline_capture as c


def row(pid, start, ppid=1):
    return dict(pid=pid, start_ticks=start, ppid=ppid, uid=1000,
                boot_id='SYNTHETIC', state='S', pgid=pid, sid=pid)


class ProcFixture:
    def __init__(self):
        self.rows = {10: row(10, 100), 20: row(20, 110, 10),
                     30: row(30, 120, 20), 90: row(90, 50, 1)}
        self.children = {10: [20], 20: [30], 30: [], 90: []}
        self.denied, self.reads, self.signals = set(), [], []
        self.fds, self.closed = {}, set()

    def identity(self, pid):
        self.reads.append(pid)
        if pid in self.denied:
            raise PermissionError(f'SYNTHETIC denied identity {pid}')
        return deepcopy(self.rows.get(pid))

    def children_of(self, pid):
        if pid in self.denied:
            raise PermissionError(f'SYNTHETIC denied children {pid}')
        return list(self.children.get(pid, []))

    def open(self, pid):
        if pid not in self.rows:
            raise ProcessLookupError(pid)
        fd = 1000 + len(self.fds)
        self.fds[fd] = self.rows[pid]  # exact object is this fixture's incarnation
        return fd

    def alive(self, fd):
        pinned = self.fds[fd]
        return self.rows.get(pinned['pid']) is pinned

    def send(self, fd, sig):
        if sig:
            self.signals.append((self.fds[fd]['pid'], sig))
            if self.alive(fd):
                del self.rows[self.fds[fd]['pid']]


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.proc = ProcFixture()
        for name, value in [('process_identity', self.proc.identity),
                            ('process_children', self.proc.children_of),
                            ('pidfd_alive', self.proc.alive),
                            ('os.pidfd_open', self.proc.open),
                            ('os.close', self.proc.closed.add),
                            ('signal.pidfd_send_signal', self.proc.send)]:
            p = patch('baseline_capture.' + name, value)
            p.start(); self.addCleanup(p.stop)

    def tracker(self):
        tracker = c.OwnedProcesses(deepcopy(self.proc.rows[10]))
        self.addCleanup(tracker.close)
        return tracker

    def test_legitimate_descendants_and_unrelated_denied_row(self):
        self.proc.denied.add(90)
        tracker = self.tracker(); tracker.scan()
        self.assertEqual({r['pid'] for r in tracker.live()}, {20, 30})
        self.assertEqual(tracker.discovery_errors, [])
        self.assertNotIn(90, self.proc.reads)
        self.assertEqual({r['pid'] for r in c.descendants_of(self.proc.rows[20], tracker)}, {20, 30})
        tracker.send(signal.SIGKILL)
        self.assertEqual(tracker.live(), [])
        self.assertIn(90, self.proc.rows, 'unrelated incarnation survives')

    def test_stale_ppid_and_stale_child_list_do_not_prove_ownership(self):
        # A stale PPID could point to newly owned PID 20. Its current kernel
        # child list does not confirm that edge after candidate pinning.
        self.proc.rows[90]['ppid'] = 20
        self.proc.rows[90]['start_ticks'] = 130
        original = self.proc.children_of
        counts = {}
        def stale(pid):
            counts[pid] = counts.get(pid, 0) + 1
            return [30, 90] if pid == 20 and counts[pid] == 1 else original(pid)
        with patch.object(c, 'process_children', stale):
            tracker = self.tracker(); tracker.scan()
        self.assertNotIn((90, 130), tracker.records)
        self.assertTrue(tracker.discovery_errors)
        tracker.send(signal.SIGKILL)
        self.assertEqual({pid for pid, sig in self.proc.signals}, {20, 30})
        self.assertIn(90, self.proc.rows)

    def test_parent_pid_reuse_during_child_proof_is_rejected(self):
        original = self.proc.children_of
        calls = 0
        def reuse(pid):
            nonlocal calls
            if pid == 20:
                calls += 1
                if calls == 2:
                    self.proc.rows[20] = row(20, 200, 1)  # unrelated replacement
                    self.proc.rows[30]['ppid'] = 20       # stale numeric observation
            return original(pid)
        with patch.object(c, 'process_children', reuse):
            tracker = self.tracker(); tracker.scan()
        self.assertNotIn((30, 120), tracker.records)
        self.assertTrue(tracker.discovery_errors)
        tracker.send(signal.SIGKILL)
        self.assertEqual(self.proc.signals, [])
        self.assertEqual(self.proc.rows[20]['start_ticks'], 200)
        self.assertIn(30, self.proc.rows)

    def test_recycled_child_pid_after_pin_is_not_signalled(self):
        original = self.proc.children_of
        calls = 0
        def reuse(pid):
            nonlocal calls
            if pid == 20:
                calls += 1
                if calls == 2:
                    self.proc.rows[30] = row(30, 220, 1)
            return original(pid)
        with patch.object(c, 'process_children', reuse):
            tracker = self.tracker(); tracker.scan()
        tracker.send(signal.SIGKILL)
        self.assertEqual({pid for pid, sig in self.proc.signals}, {20})
        self.assertEqual(self.proc.rows[30]['start_ticks'], 220)

    def test_same_tick_descendant_uses_confirmed_edge(self):
        self.proc.rows[30]['start_ticks'] = 110
        tracker = self.tracker(); tracker.scan()
        self.assertIn((30, 110), tracker.records)
        self.assertFalse(tracker.discovery_errors)

    def test_preflight_access_denied_closes_root_fd(self):
        self.proc.denied.add(10)
        with self.assertRaisesRegex(c.ProcessDiscoveryError, 'preflight.*PermissionError'):
            self.tracker()
        self.assertEqual(self.proc.closed, {1000})
        self.assertEqual(self.proc.signals, [])

    def test_denial_after_launch_retains_and_cleans_validated_targets(self):
        tracker = self.tracker(); tracker.scan()
        self.proc.denied.update({10, 20, 30})
        tracker.scan()
        self.assertTrue(tracker.discovery_errors)
        tracker.send(signal.SIGKILL)
        self.assertEqual(tracker.live(), [])
        self.assertEqual({pid for pid, sig in self.proc.signals}, {20, 30})
        self.assertIn(90, self.proc.rows)

    def test_denied_candidate_does_not_abort_other_owned_cleanup(self):
        self.proc.denied.add(30)
        tracker = self.tracker(); tracker.scan()
        self.assertEqual({r['pid'] for r in tracker.live()}, {20})
        self.assertIn('PermissionError', tracker.discovery_errors[0])
        tracker.send(signal.SIGKILL)
        self.assertIn(30, self.proc.rows, 'unproven candidate must not be signalled')

    def test_disappearing_child_is_not_a_permission_failure(self):
        del self.proc.rows[30]
        tracker = self.tracker(); tracker.scan()
        self.assertEqual({r['pid'] for r in tracker.live()}, {20})
        self.assertFalse(tracker.discovery_errors)

    def test_children_file_denial_is_not_an_empty_list(self):
        task = Mock()
        task.__truediv__ = Mock(return_value=Mock())
        task.__truediv__.return_value.read_text.side_effect = PermissionError('SYNTHETIC children denied')
        with patch.object(c, 'Path') as path:
            path.return_value.iterdir.return_value = [task]
            with self.assertRaises(PermissionError):
                # Call the real reader, not the synthetic edge provider.
                self.proc_children(10)

    def test_missing_children_support_is_rejected_for_live_task(self):
        task = Mock()
        task.__truediv__ = Mock(return_value=Mock())
        task.__truediv__.return_value.read_text.side_effect = FileNotFoundError('SYNTHETIC unsupported')
        task.exists.return_value = True
        with patch.object(c, 'Path') as path:
            path.return_value.iterdir.return_value = [task]
            with self.assertRaisesRegex(c.ProcessDiscoveryError, 'required children visibility'):
                self.proc_children(10)

    def test_disappearing_task_is_a_benign_children_read_race(self):
        task = Mock()
        task.__truediv__ = Mock(return_value=Mock())
        task.__truediv__.return_value.read_text.side_effect = FileNotFoundError('SYNTHETIC vanished')
        task.exists.return_value = False
        # Another still-readable task proves support; the vanished thread is
        # not silently treated as a permission denial or missing kernel feature.
        leader = Mock()
        leader.__truediv__ = Mock(return_value=Mock())
        leader.__truediv__.return_value.read_text.return_value = ''
        with patch.object(c, 'Path') as path:
            path.return_value.iterdir.return_value = [task, leader]
            self.assertEqual(self.proc_children(10), [])

    # Save the actual reader before setUp replaces discovery with fixture data.
    proc_children = staticmethod(c.process_children)


if __name__ == '__main__':
    unittest.main()

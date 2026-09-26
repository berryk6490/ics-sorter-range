"""Host-only selected-trailer chute sensor and physical hold contract."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import (PlantModel, Package, TRAILER, Motion, Hold, chute_hold_front,
                   CHUTE_TRAILER, CHUTE_CAPACITY)


class ChuteModelTest(unittest.TestCase):
    def model(self, length=60, spacing=100):
        return PlantModel(length_cm=length, spacing_cm=spacing,
                          accumulation=True, stateful=True,
                          chute_trailer=CHUTE_TRAILER,
                          chute_capacity=CHUTE_CAPACITY)

    @staticmethod
    def move(model, slots, steps=200, rpm=1750, seconds=.1):
        for _ in range(steps):
            model.step(seconds, [rpm] * 6, slots)

    def test_three_accepted_crossings_not_ack_alone_fill(self):
        m = self.model()
        for seq in (1, 2, 3):
            m.reserve_chute(seq, 10 + seq, seq)
            self.assertEqual(m.chute_occupancy, seq - 1)
            self.assertFalse(m.accept_chute(seq, 10 + seq, seq + 10))
            self.assertEqual(m.chute_occupancy, seq - 1)
            self.assertTrue(m.accept_chute(seq, 10 + seq, seq))
        self.assertEqual(m.chute_occupancy, 3)
        self.assertTrue(m.chute_full)
        self.assertFalse(m.accept_chute(3, 13, 3))
        self.assertFalse(m.accept_chute(99, 13, 3))

    def test_wrong_trailer_duplicate_and_late_confirmation(self):
        m = self.model()
        self.assertFalse(m.reserve_chute(1, 11, 1, actual=5))
        self.assertFalse(m.accept_chute(1, 11, 1))
        self.assertTrue(m.reserve_chute(1, 11, 2))
        self.assertTrue(m.accept_chute(1, 11, 2))
        self.assertFalse(m.reserve_chute(1, 11, 2))
        self.assertFalse(m.accept_chute(1, 11, 1))
        self.assertEqual(m.chute_occupancy, 1)

    def test_provisional_reservation_closes_door_before_plc_accepts(self):
        m = self.model()
        for n in (1, 2):
            m.reserve_chute(n, n, n)
            self.assertTrue(m.accept_chute(n, n, n))
        self.assertFalse(m.chute_full)
        m.reserve_chute(3, 3, 3)
        self.assertEqual(m.chute_occupancy, 2)
        self.assertTrue(m.chute_full)
        self.assertFalse(m.accept_chute(3, 3, 99))
        self.assertTrue(m.chute_full)

    def test_event_ack_without_accepted_terminal_remains_provisional(self):
        m = self.model()
        self.assertTrue(m.reserve_chute(7, 17, 21))
        # QW586 can acknowledge the serialized event even if the PLC rejected
        # its terminal outcome. Only the accepted tuple reconciles inventory.
        self.assertEqual(m.chute_occupancy, 0)
        m.step_zones(5.1, [0] * 6, {})
        self.assertTrue(m.chute_unknown)
        self.assertFalse(m.chute_permissive)
        self.assertEqual(m.chute_occupancy, 0)

    def test_empty_ack_and_fresh_clear_do_not_resume(self):
        m = self.model()
        for n in (1, 2, 3):
            m.reserve_chute(n, n, n)
            m.accept_chute(n, n, n)
        m.set_chute_permissive(False)
        self.assertFalse(m.empty_chute(7, 5, (10, 2)))
        self.assertTrue(m.empty_chute(7, CHUTE_TRAILER, (10, 2)))
        self.assertFalse(m.chute_full)
        self.assertFalse(m.chute_permissive)
        self.assertFalse(m.empty_chute(7, CHUTE_TRAILER, (10, 2)))
        m.set_chute_permissive(True)
        self.assertTrue(m.chute_permissive)

    def test_hold_before_door_with_spacing_and_no_false_load(self):
        m = self.model()
        for n in (11, 12, 13):
            m.reserve_chute(n, n, n)
            m.accept_chute(n, n, n)
        for token, lane in ((1, 1), (2, 2), (3, 3)):
            m.request(token, 20 + token, lane=lane)
        slots = {token: (20 + token, 3, 2) for token in (1, 2, 3)}
        self.move(m, slots, 180)
        self.assertFalse(any(e[0] == TRAILER for e in m.pending))
        self.assertTrue(any(p.hold == Hold.CHUTE_FULL for p in m.packages))
        self.assertFalse(any(p.motion == Motion.JAMMED for p in m.packages))
        for p in m.packages:
            if p.outbound:
                self.assertLess(p.outbound_position, 15)
        outbound = sorted((p.outbound_position for p in m.packages if p.outbound), reverse=True)
        for lead, follow in zip(outbound, outbound[1:]):
            self.assertGreaterEqual(lead - follow + 1e-9, 3.2)

    def test_geometry_proof_across_supported_envelope(self):
        for length in (40, 60, 90, 120):
            for rpm in (0, 120, 1750, 3000):
                for period in (.05, .1, .5):
                    step = period * 10 * rpm / 1750
                    front = chute_hold_front(CHUTE_TRAILER, length / 50, step)
                    self.assertTrue(8 < front < 15)
                    m = self.model(length, max(100, 160 - length))
                    for n in (11, 12, 13):
                        m.reserve_chute(n, n, n)
                        m.accept_chute(n, n, n)
                    p = Package(1, 21, m.length, lane=1, position=13.6,
                                outbound=1, outbound_position=14.2,
                                divert_sent=True, target=2)
                    m.packages.append(p)
                    m.step(period, [rpm] * 6, {1: (21, 4, 2)})
                    self.assertLess(p.outbound_position, 15)
                    self.assertFalse(any(e[0] == TRAILER for e in m.pending))

    def test_past_hold_front_freezes_without_teleporting_and_past_door_faults(self):
        m = self.model()
        for n in (11, 12, 13):
            m.reserve_chute(n, n, n)
            m.accept_chute(n, n, n)
        p = Package(1, 21, m.length, lane=1, position=13.6,
                    outbound=1, outbound_position=14.8,
                    divert_sent=True, target=2)
        m.packages.append(p)
        m.step(.5, [1750] * 6, {1: (21, 4, 2)})
        self.assertEqual(p.outbound_position, 14.8)
        self.assertEqual(p.hold, Hold.CHUTE_FULL)
        p.outbound_position = 15.1
        m.step(.1, [1750] * 6, {1: (21, 4, 2)})
        self.assertTrue(m.chute_unknown)
        self.assertFalse(any(e[0] == TRAILER for e in m.pending))

    def test_three_global_held_fit_and_fifo_drain_to_refill(self):
        m = self.model()
        for n in (11, 12, 13):
            m.reserve_chute(n, n, n)
            m.accept_chute(n, n, n)
        for token in (1, 2, 3):
            m.request(token, 20 + token, lane=token)
        slots = {token: (20 + token, 3, 2) for token in (1, 2, 3)}
        self.move(m, slots, 180)
        self.assertEqual(len(m.packages), 3)
        self.assertEqual({p.hold for p in m.packages}, {Hold.CHUTE_FULL})
        self.assertEqual({p.lane for p in m.packages}, {1, 2, 3})
        self.assertTrue(m.empty_chute(7, 2, (77, 1)))
        self.assertFalse(m.chute_permissive)
        self.assertEqual(len([e for e in m.pending if e[0] == TRAILER]), 0)
        m.set_chute_permissive(True)
        for _ in range(250):
            m.step(.1, [1750] * 6, slots)
            outbound = sorted((p.outbound_position for p in m.packages
                               if p.outbound == 1), reverse=True)
            for lead, follow in zip(outbound, outbound[1:]):
                self.assertGreaterEqual(lead - follow + 1e-9, 3.2)
        arrived = [e for e in m.pending if e[0] == TRAILER]
        self.assertEqual(len(arrived), 3)
        self.assertEqual(len({e[1] for e in arrived}), 3)
        self.assertEqual(m.chute_occupancy, 0)
        self.assertTrue(m.chute_full)  # three provisional crossings
        for seq, event in enumerate(arrived, 20):
            self.assertTrue(m.bind_chute_sequence(event[1], event[2], seq))
            self.assertTrue(m.accept_chute(event[1], event[2], seq))
        self.assertEqual(m.chute_occupancy, 3)
        self.assertTrue(m.chute_full)

    def test_restart_and_lost_permissive_fail_closed(self):
        m = self.model()
        m.set_chute_permissive(False)
        self.assertFalse(m.chute_permissive)
        m = self.model()
        m.mark_chute_unknown()
        self.assertFalse(m.chute_permissive)
        self.assertTrue(m.chute_unknown)

    def test_max_feedback_step_preserves_trailer_beam_and_fifo(self):
        m = self.model()
        for n in (11, 12, 13):
            self.assertTrue(m.reserve_chute(n, n, n))
            self.assertTrue(m.accept_chute(n, n, n))
        lead = Package(1, 21, m.length, lane=1, position=13.6,
                       outbound=1, outbound_position=14.2,
                       divert_sent=True, target=2)
        follow = Package(2, 22, m.length, lane=2, position=13.6,
                         outbound=1, outbound_position=10.8,
                         divert_sent=True, target=2)
        m.packages.extend((lead, follow))
        slots = {1: (21, 4, 2), 2: (22, 4, 2)}
        samples = {1: [m.photoeyes(1, 21)], 2: [m.photoeyes(2, 22)]}
        m.step(.5, [3000] * 6, slots)
        for p in (lead, follow):
            samples[p.token].append(m.photoeyes(p.token, p.serial))
        self.assertEqual(lead.outbound_position, 14.6)
        self.assertGreaterEqual(lead.outbound_position - follow.outbound_position + 1e-9, 3.2)
        self.assertEqual([e for e in m.pending if e[0] == TRAILER], [])
        self.assertTrue(m.empty_chute(1, 2, (77, 1)))
        m.set_chute_permissive(True)
        for _ in range(8):
            m.step(.5, [3000] * 6, slots)
            for p in (lead, follow):
                samples[p.token].append(m.photoeyes(p.token, p.serial))
            if lead in m.packages and follow in m.packages:
                self.assertGreaterEqual(lead.outbound_position - follow.outbound_position,
                                        3.2 - 1e-9)
        for token in (1, 2):
            trailer = [bool(mask & 16) for mask in samples[token]]
            leading = next(i for i in range(1, len(trailer))
                           if trailer[i] and not trailer[i - 1])
            trailing = next(i for i in range(leading + 1, len(trailer))
                            if not trailer[i] and trailer[i - 1])
            self.assertLess(leading, trailing)
        arrivals = [e for e in m.pending if e[0] == TRAILER]
        self.assertEqual([e[1:3] for e in arrivals], [(1, 21), (2, 22)])

    def test_max_feedback_step_preserves_each_beam_edge_and_identity(self):
        m = self.model()
        m.set_chute_permissive(True)
        m.request(41, 71, lane=1)
        samples = [m.photoeyes(41, 71)]
        for _ in range(16):
            m.step(.5, [3000] * 6, {41: (71, 3, 2)})
            samples.append(m.photoeyes(41, 71))
        for sensor in range(5):
            states = [bool(mask & (1 << sensor)) for mask in samples]
            rising = [i for i in range(1, len(states))
                      if states[i] and not states[i-1]]
            falling = [i for i in range(1, len(states))
                       if not states[i] and states[i-1]]
            self.assertEqual(len(rising), 1, sensor)
            self.assertEqual(len(falling), 1, sensor)
            self.assertLess(rising[0], falling[0])
        self.assertEqual([(e[0], e[1], e[2]) for e in m.pending],
                         [(1, 41, 71), (2, 41, 71), (3, 41, 71), (4, 41, 71)])


if __name__ == "__main__":
    unittest.main()

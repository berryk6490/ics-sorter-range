"""Deterministic finite-zone model checks; no guest or Modbus writes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import (PlantModel, ZoneBlock, Zone, Motion, Hold, INDUCT, TUNNEL,
                   DIVERT, TRAILER)


FAST = [1750] * 6


class AccumulationTest(unittest.TestCase):
    def model(self, block=None, **kwargs):
        return PlantModel(accumulation=True, stateful=True, zone_block=block, **kwargs)

    def step(self, model, count, slots, rpm=FAST):
        for _ in range(count):
            model.step(.1, rpm, slots)

    def test_zone_capacity_and_nonoverlap_varying_dimensions(self):
        for length, spacing in ((40, 120), (60, 100), (100, 60)):
            m = self.model(length_cm=length, spacing_cm=spacing)
            self.assertGreaterEqual(m.zone_capacity(Zone.APPROACH), 1)
            self.assertEqual(m.zone_capacity(Zone.DECISION), 1)
            for token in (1, 2, 3):
                m.request(token, token + 10, 1)
            slots = {t: (t + 10, 3, 2) for t in (1, 2, 3)}
            for _ in range(220):
                m.step(.1, FAST, slots)
                for belt in (0, 1):
                    packages = [p for p in m.packages if (p.outbound == 1) == bool(belt)]
                    coordinates = [p.outbound_position if belt else p.position
                                   for p in packages]
                    for a, b in zip(sorted(coordinates), sorted(coordinates)[1:]):
                        self.assertGreaterEqual(b - a + 1e-6, m.length + m.spacing)

    def test_lane_hold_propagates_and_fifo_drains(self):
        block = ZoneBlock("lane", 1, 6, lane=1, zone="premerge")
        m = self.model(block)
        for token in (1, 2, 3):
            m.request(token, token + 10, 1)
        slots = {t: (t + 10, 3, 2) for t in (1, 2, 3)}
        held = set()
        inhibited = False
        for _ in range(90):
            m.step(.1, FAST, slots)
            held.update(p.token for p in m.packages if p.motion == Motion.HELD_DOWNSTREAM)
            inhibited |= not m.can_induct(1)
        self.assertTrue(held)
        self.assertTrue(inhibited)
        self.step(m, 250, slots)
        self.assertEqual([e[1] for e in m.pending if e[0] == TRAILER], [1, 2, 3])
        self.assertEqual([e[1] for e in m.pending if e[0] == DIVERT], [1, 2, 3])

    def test_merge_block_fair_three_lanes_and_clearance(self):
        m = self.model(ZoneBlock("merge", 1, 6))
        slots = {t: (t + 10, 3, 2) for t in (1, 2, 3)}
        for lane in (1, 2, 3):
            m.request(lane, lane + 10, lane)
        waiting = set()
        minimum = 1000.0
        for _ in range(270):
            m.step(.1, FAST, slots)
            waiting.update(p.lane for p in m.packages if p.motion == Motion.HELD_MERGE)
            outbound = [p for p in m.packages if p.outbound == 1]
            for a in outbound:
                for b in outbound:
                    if a is not b:
                        minimum = min(minimum, abs(a.outbound_position - b.outbound_position))
        self.assertEqual(waiting, {1, 2, 3})
        self.assertGreaterEqual(minimum, 3.2 - 1e-6)
        self.assertEqual({e[1] for e in m.pending if e[0] == TRAILER}, {1, 2, 3})
        self.assertEqual([e[1] for e in m.pending if e[0] == DIVERT], [1, 3, 2])

    def test_drive_stop_is_distinct_from_downstream_hold(self):
        m = self.model()
        m.request(1, 11)
        slots = {1: (11, 3, 2)}
        self.step(m, 4, slots)
        self.step(m, 4, slots, [0] + FAST[1:])
        p = m.packages[0]
        self.assertEqual((p.motion, p.hold), (Motion.DRIVE_STOPPED, Hold.DRIVE_OFF))
        frozen = p.position
        self.step(m, 5, slots, [0] + FAST[1:])
        self.assertEqual(p.position, frozen)
        self.step(m, 230, slots)
        self.assertEqual([e[0] for e in m.pending],
                         [INDUCT, TUNNEL, DIVERT, TRAILER])

    def test_intentional_hold_keeps_beam_physical(self):
        m = self.model(ZoneBlock("lane", 1, 6, lane=1, zone="premerge"))
        m.request(1, 11)
        slots = {1: (11, 3, 2)}
        masks = []
        held = False
        for _ in range(50):
            m.step(.1, FAST, slots)
            masks.append(m.photoeyes(1, 11))
            held |= any(p.motion == Motion.HELD_DOWNSTREAM for p in m.packages)
        self.assertTrue(any(mask & 1 for mask in masks))
        self.assertTrue(any(mask & 2 for mask in masks))
        self.assertTrue(held)
        self.assertFalse(any(e[0] == TRAILER for e in m.pending))

    def test_fixture_bounds_and_run_scoped_cleanup(self):
        with self.assertRaisesRegex(ValueError, "3.2"):
            self.model(length_cm=40, spacing_cm=100)
        for kwargs in ({"kind": "merge", "after_token": 0, "duration": 3},
                       {"kind": "merge", "after_token": 1, "duration": 61},
                       {"kind": "lane", "after_token": 1, "duration": 3,
                        "lane": 4, "zone": "decision"}):
            with self.assertRaises(ValueError):
                ZoneBlock(**kwargs)
        first = self.model(ZoneBlock("merge", 1, 2))
        first.request(1, 11)
        self.step(first, 50, {1: (11, 3, 2)})
        second = self.model()
        self.assertIsNone(second.zone_block)


if __name__ == "__main__":
    unittest.main()

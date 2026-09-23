import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import PlantModel, INDUCT, TUNNEL, DIVERT, TRAILER, RECIRC, FAILED_CONFIRM


class PlantModelTest(unittest.TestCase):
    def drive(self, model, slots, ticks=250):
        for _ in range(ticks):
            model.step(.1, [1750] * 6, slots)
        return list(model.pending)

    def test_two_packages_have_distinct_sensor_and_trailer_events(self):
        model = PlantModel(length_cm=60, spacing_cm=100)
        model.request(1, 11)
        slots = {1: (11, 3, 2), 2: (12, 3, 5)}
        self.drive(model, slots, 4)
        model.request(2, 12)
        events = self.drive(model, slots)
        self.assertEqual([e[:4] for e in events if e[0] == TRAILER],
                         [(TRAILER, 1, 11, 2), (TRAILER, 2, 12, 5)])
        for token in (1, 2):
            kinds = [e[0] for e in events if e[1] == token]
            self.assertEqual(kinds, [INDUCT, TUNNEL, DIVERT, TRAILER])

    def test_late_decision_recirc_and_missed_confirmation(self):
        model = PlantModel(fail_confirm_token=2)
        model.request(1, 11)
        model.request(2, 12)
        slots = {1: (11, 2, 0), 2: (12, 3, 5)}
        events = self.drive(model, slots)
        self.assertIn((RECIRC, 1, 11, 0), [e[:4] for e in events])
        self.assertIn((FAILED_CONFIRM, 2, 12, 0), [e[:4] for e in events])
        self.assertFalse(any(e[0] == TRAILER for e in events))

    def test_speed_feedback_stops_position_and_identity_mismatch_does_not_route(self):
        model = PlantModel()
        model.request(1, 11)
        model.step(.1, [0] * 6, {})
        self.assertFalse(model.pending)
        slots = {1: (99, 3, 2)}
        events = self.drive(model, slots)
        self.assertIn((RECIRC, 1, 11, 0), [e[:4] for e in events])
        self.assertFalse(any(e[0] == TRAILER for e in events))

    def test_bounded_telemetry_tracks_belt_and_recent_sensor(self):
        model = PlantModel()
        model.request(1, 11)
        model.step(.1, [1750] * 6, {1: (11, 3, 2)})
        model.last_event[1] = (INDUCT, 0)
        self.assertEqual(model.telemetry(1, 11)[:3], (1, 10, INDUCT))
        self.assertEqual(model.telemetry(1, 99), (0, 0, 0, 0))
        self.drive(model, {1: (11, 3, 2)}, 30)
        self.assertEqual(model.telemetry(1, 11), (2, 150, TRAILER, 2))
        self.drive(model, {}, 30)
        self.assertEqual(model.telemetry(1, 11), (0, 0, 0, 0))

    def test_two_lanes_share_outbound_with_clearance_and_no_loss(self):
        model = PlantModel(length_cm=60, spacing_cm=100)
        model.request(1, 11, lane=1)
        model.request(2, 12, lane=2)
        slots = {1: (11, 3, 2), 2: (12, 3, 3)}
        saw_wait = False
        for _ in range(250):
            model.step(.1, [1750] * 6, slots)
            outbound = [p for p in model.packages if p.outbound == 1]
            if len(outbound) == 2:
                self.assertGreaterEqual(abs(outbound[0].outbound_position -
                                            outbound[1].outbound_position),
                                        model.length + model.spacing)
            saw_wait |= any(p.lane == 2 and p.position == 14 and
                            not p.divert_sent for p in model.packages)
        self.assertTrue(saw_wait)
        self.assertEqual([(e[1], e[3]) for e in model.pending if e[0] == TRAILER],
                         [(1, 2), (2, 3)])
        for token in (1, 2):
            self.assertEqual([e[0] for e in model.pending if e[1] == token],
                             [INDUCT, TUNNEL, DIVERT, TRAILER])

    def test_lane_two_failed_confirmation_never_loads_trailer(self):
        model = PlantModel(fail_confirm_token=2)
        model.request(2, 12, lane=2)
        events = self.drive(model, {2: (12, 3, 3)})
        self.assertEqual([e[0] for e in events],
                         [INDUCT, TUNNEL, DIVERT, FAILED_CONFIRM])
        self.assertEqual(model.telemetry(2, 12)[:2], (0, 0))

    def test_lane_three_shares_outbound_with_lane_two_and_reuses_token(self):
        model = PlantModel(length_cm=60, spacing_cm=100)
        model.request(1, 11, lane=2)
        model.request(2, 12, lane=3)
        slots = {1: (11, 3, 3), 2: (12, 3, 2)}
        waited = False
        for _ in range(250):
            model.step(.1, [1750] * 6, slots)
            outbound = [p for p in model.packages if p.outbound == 1]
            if len(outbound) == 2:
                self.assertGreaterEqual(abs(outbound[0].outbound_position -
                                            outbound[1].outbound_position),
                                        model.length + model.spacing)
            waited |= any(p.lane == 3 and p.position == 14 and
                          not p.divert_sent for p in model.packages)
        self.assertTrue(waited)
        self.assertEqual([(e[1], e[3]) for e in model.pending if e[0] == TRAILER],
                         [(1, 3), (2, 2)])
        model.request(3, 13, lane=3)
        self.drive(model, {3: (13, 3, 8)})
        self.assertIn((TRAILER, 3, 13, 8), [e[:4] for e in model.pending])

    def test_lane_three_failed_confirmation_has_no_trailer_event(self):
        model = PlantModel(fail_confirm_token=1)
        model.request(1, 11, lane=3)
        events = self.drive(model, {1: (11, 3, 8)})
        self.assertEqual([e[0] for e in events],
                         [INDUCT, TUNNEL, DIVERT, FAILED_CONFIRM])
        self.assertFalse(any(e[0] == TRAILER for e in events))

    def test_three_lanes_share_one_outbound_with_order_and_clearance(self):
        model = PlantModel(length_cm=60, spacing_cm=100)
        slots = {token: (10 + token, 3, token)
                 for token in (1, 2, 3)}
        for token in (1, 2, 3):
            model.request(token, 10 + token, lane=token)
        waited = set()
        for _ in range(250):
            model.step(.1, [1750] * 6, slots)
            outbound = [p for p in model.packages if p.outbound == 1]
            for left in outbound:
                for right in outbound:
                    if left is not right:
                        self.assertGreaterEqual(
                            abs(left.outbound_position - right.outbound_position),
                            model.length + model.spacing)
            waited.update(p.lane for p in model.packages
                          if p.position == 14 and not p.divert_sent)
        self.assertTrue(waited)
        self.assertEqual(sorted((e[1], e[3]) for e in model.pending
                                if e[0] == TRAILER),
                         [(1, 1), (2, 2), (3, 3)])
        self.assertEqual([e[1] for e in model.pending if e[0] == DIVERT],
                         [1, 3, 2])
        for token in (1, 2, 3):
            self.assertEqual([e[0] for e in model.pending if e[1] == token],
                             [INDUCT, TUNNEL, DIVERT, TRAILER])


if __name__ == "__main__":
    unittest.main()

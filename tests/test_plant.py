import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import PlantModel, INDUCT, TUNNEL, DIVERT, TRAILER, RECIRC, FAILED_CONFIRM


class PlantModelTest(unittest.TestCase):
    def drive(self, model, slots, ticks=250):
        for _ in range(ticks):
            model.step(.1, [1750] * 4, slots)
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
        model.step(.1, [0] * 4, {})
        self.assertFalse(model.pending)
        slots = {1: (99, 3, 2)}
        events = self.drive(model, slots)
        self.assertIn((RECIRC, 1, 11, 0), [e[:4] for e in events])
        self.assertFalse(any(e[0] == TRAILER for e in events))


if __name__ == "__main__":
    unittest.main()

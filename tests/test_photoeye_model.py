import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import PlantModel, PhotoeyeFixture


class PhotoeyeModelTest(unittest.TestCase):
    def collect(self, model, *, token=1, serial=11, rpm=120, ticks=650, dt=.1):
        model.request(token, serial, lane=1)
        result = []
        for _ in range(ticks):
            model.step(dt, [rpm] * 6, {token: (serial, 3, 2)})
            result.append(model.photoeyes(token, serial))
        return result

    def test_five_beams_have_leading_blocked_trailing_clear(self):
        masks = self.collect(PlantModel(stateful=True))
        for sensor in range(5):
            states = [bool(mask & (1 << sensor)) for mask in masks]
            self.assertIn(True, states, sensor)
            leading = states.index(True)
            trailing = states.index(False, leading)
            self.assertGreaterEqual(trailing - leading, 5, sensor)
            self.assertFalse(any(states[trailing:]), sensor)
        self.assertEqual(masks[-1], 0)

    def test_length_and_speed_set_blocked_duration(self):
        def span(length, speed):
            masks = self.collect(PlantModel(length_cm=length, stateful=True),
                                 rpm=speed, ticks=60)
            return sum(bool(mask & 1) for mask in masks)
        short = span(60, 120)
        long = span(100, 120)
        fast = span(60, 240)
        self.assertGreater(long, short)
        self.assertLess(fast, short)

    def test_stuck_clear_and_missed_downstream_are_token_bound(self):
        for kind, sensor, bit in (("stuck_clear", "tunnel", 1),
                                  ("missed", "outbound", 3)):
            model = PlantModel(stateful=True, photoeye_fixture=PhotoeyeFixture(sensor, 1, kind))
            masks = self.collect(model)
            self.assertFalse(any(mask & (1 << bit) for mask in masks))
            fresh = PlantModel(stateful=True)
            self.assertTrue(any(mask & (1 << bit) for mask in self.collect(fresh)))

    def test_stuck_blocked_persists_after_package_leaves(self):
        model = PlantModel(stateful=True,
                           photoeye_fixture=PhotoeyeFixture("trailer", 1, "stuck_blocked"))
        masks = self.collect(model)
        self.assertTrue(masks[-1] & 16)
        self.assertFalse(model.packages)

    def test_bounce_is_short_and_then_settles(self):
        model = PlantModel(stateful=True,
                           photoeye_fixture=PhotoeyeFixture("tunnel", 1, "bounce"))
        masks = self.collect(model, ticks=320, dt=.05)
        states = [bool(mask & 2) for mask in masks]
        first = states.index(True)
        self.assertIn(False, states[first:first + 8])
        self.assertTrue(all(states[first + 9:first + 13]))

    def test_impossible_order_fixture_asserts_downstream_early(self):
        model = PlantModel(stateful=True,
                           photoeye_fixture=PhotoeyeFixture("divert", 1, "early"))
        model.request(1, 11)
        model.step(.1, [120] * 6, {1: (11, 3, 2)})
        self.assertTrue(model.photoeyes(1, 11) & 4)
        self.assertTrue(model.photoeyes(1, 11) & 1)

    def test_invalid_or_incomplete_fixture_refused(self):
        for sensor, token, kind in (("unknown", 1, "bounce"),
                                    ("tunnel", 0, "bounce"),
                                    ("tunnel", 1, "arbitrary")):
            with self.assertRaises(ValueError):
                PhotoeyeFixture(sensor, token, kind)


if __name__ == "__main__":
    unittest.main()

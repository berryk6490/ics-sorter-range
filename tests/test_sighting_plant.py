"""Opt-in sighting plant: route, gates, raw beam edges and private evidence."""
import dataclasses
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
from plant import (BeamEdge, DoorGeometry, SightingLayout, SightingPlant,  # noqa: E402
                   belt_speed)


def run(plant, ticks, primary=200, outbound=233, coils=None, permits=None, dt=.1):
    edges = []
    for _ in range(ticks):
        edges += plant.step(dt, primary, outbound, coils, permits)
    return edges


class SightingLayoutTest(unittest.TestCase):
    def test_default_layout_is_valid_and_matches_contract_speeds(self):
        SightingLayout().validate()
        self.assertAlmostEqual(belt_speed(233), 66.571, places=3)
        self.assertAlmostEqual(belt_speed(200), 57.143, places=3)
        self.assertEqual(belt_speed(-50), 0.0)

    def test_invalid_orderings_and_zone_wider_than_gap_are_rejected(self):
        base = SightingLayout()
        bad = [
            dataclasses.replace(base, primary_tunnel=990.0),
            dataclasses.replace(base, recycle_merge=100.0),
            dataclasses.replace(base, outbound_tunnel=100.0),
            dataclasses.replace(base, end_eye=1050.0),
            dataclasses.replace(base, gap=70.0),
            dataclasses.replace(base, doors=(DoorGeometry("1", 800, 880, 840),)),
            dataclasses.replace(base, doors=(DoorGeometry("E", 800, 880, 900),)),
            dataclasses.replace(base, recycle_hold=1600.0),
            dataclasses.replace(base, chute_speed=float("nan")),
        ]
        for layout in bad:
            with self.subTest(layout=layout), self.assertRaises(ValueError):
                layout.validate()

    def test_induct_rejects_out_of_model_labels_and_lengths(self):
        plant = SightingPlant()
        for label, length in ((0, 60), (40000, 60), (1111, 19.9), (1111, 120)):
            with self.subTest(label=label, length=length), self.assertRaises(ValueError):
                plant.induct(label, length)


class SightingPlantTest(unittest.TestCase):
    def test_single_package_crosses_every_beam_in_route_order(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        edges = run(plant, 900)
        rising = [e.beam for e in edges if e.rising]
        self.assertEqual(rising[:7], ["primary_entry", "primary_tunnel", "outbound_entry",
                                      "merge_entry", "outbound_tunnel", "end_eye",
                                      "recycle_gate"])
        # The recycle return rejoins upstream of the tunnel for a repeat read.
        self.assertEqual(rising[7:10], ["merge_entry", "outbound_tunnel", "end_eye"])
        self.assertEqual([e.seq for e in edges], list(range(1, len(edges) + 1)))

    def test_public_edges_and_scanner_channel_carry_no_physical_id(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        views = []
        for _ in range(400):
            for edge in plant.step(.1, 200, 233):
                self.assertIsInstance(edge, BeamEdge)
                self.assertEqual({f.name for f in dataclasses.fields(edge)},
                                 {"seq", "beam", "rising", "time"})
            for section in ("primary", "outbound"):
                view = plant.tunnel_view(section)
                if view:
                    views.append(view)
        self.assertTrue(views)
        self.assertTrue(all(view == (1111, 60.0) for view in views))
        # The private journal is the only place edges meet physical identity.
        self.assertTrue(any(j["kind"] == "edge" and j["physical_ids"] == [1]
                            for j in plant.journal))

    def test_only_a_door_coil_diverts_and_the_chute_beam_sees_it(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        edges = run(plant, 600)
        self.assertFalse(plant.delivered)
        self.assertFalse(any(e.beam.startswith("chute_") for e in edges))
        plant = SightingPlant()
        plant.induct(1111, 60)
        edges = run(plant, 400, coils={"1": True})
        self.assertEqual([p.label for p in plant.delivered], [1111])
        chute = [e for e in edges if e.beam == "chute_1"]
        self.assertEqual([e.rising for e in chute], [True, False])
        self.assertFalse(any(e.beam == "end_eye" for e in edges))

    def test_closed_handoff_holds_before_the_end_and_nothing_is_deleted(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        run(plant, 400, permits={"handoff": False})
        [package] = plant.packages
        self.assertEqual((package.section, package.front), ("primary", 950.0))
        run(plant, 30, permits={"handoff": True})
        self.assertEqual(plant.packages[0].section, "outbound")

    def test_closed_merge_holds_on_the_recycle_return(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        run(plant, 800, permits={"merge": False})
        [package] = plant.packages
        self.assertEqual((package.section, package.front), ("recycle", 1450.0))

    def test_stopped_belts_produce_no_motion_and_no_edges(self):
        plant = SightingPlant()
        plant.induct(1111, 60)
        run(plant, 50)
        before = [(p.section, p.front) for p in plant.packages]
        self.assertEqual(run(plant, 100, primary=0, outbound=0), [])
        self.assertEqual([(p.section, p.front) for p in plant.packages], before)

    def test_clear_gap_holds_on_every_section_under_random_gates(self):
        for seed in range(8):
            rng = random.Random(seed)
            plant = SightingPlant()
            for _ in range(6):
                plant.induct(rng.randint(1000, 9999), rng.randint(20, 119))
            for _ in range(3000):
                permits = {gate: rng.random() > .3 for gate in ("induction", "handoff", "merge")}
                rpm = rng.choice((0, 150, 233, 300))
                plant.step(.1, rpm, rpm, None, permits)
                for section in ("primary", "outbound", "recycle"):
                    members = plant._members(section)
                    for leader, follower in zip(members, members[1:]):
                        with self.subTest(seed=seed, section=section):
                            self.assertGreaterEqual(
                                leader.front - leader.length - follower.front,
                                plant.layout.gap - 1e-6)
                placed = len(plant.queue) + len(plant.packages) + len(plant.delivered)
                self.assertEqual(placed + len(plant.removed), 6)

    def test_operator_removal_is_journaled(self):
        plant = SightingPlant()
        pid = plant.induct(1111, 60)
        run(plant, 50)
        self.assertTrue(plant.remove(pid, "operator cleared transfer fault"))
        self.assertFalse(plant.packages)
        self.assertEqual(plant.journal[-1]["kind"], "removed")
        self.assertFalse(plant.remove(pid, "twice"))


if __name__ == "__main__":
    unittest.main()

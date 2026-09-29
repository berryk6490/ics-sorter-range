"""Sightings and recycle end to end: plant, reference tracker and XLe ledger.

The tracker plays the PLC and sees only raw edges and scanner results. Plant
truth (physical IDs, journal) is read here only by the test, as offline
evidence, never by the tracker or the ledger.
"""
import dataclasses
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sighting_reference import ReferenceTracker  # noqa: E402
from plant import SightingLayout, SightingPlant  # noqa: E402
from xle import PassLedger  # noqa: E402


class Loop:
    def __init__(self, path, plan=None, unreadable=(), layout=None, failing_doors=(),
                 run_identity=(7, 1)):
        self.plant = SightingPlant(layout)
        self.ledger = PassLedger(path)
        self.unreadable = set(unreadable)
        self.failing = set(failing_doors)
        self.tracker = ReferenceTracker(self.plant.layout, self.ledger, run_identity,
                                        plan or {}, self.scan)

    def scan(self, section):
        # The scanner reads the plant's label; the tracker gets only its result.
        view = self.plant.tunnel_view(section)
        if view is None or view[0] in self.unreadable:
            return False, 0, 0
        return True, view[0], view[1]

    def run(self, primary=200, outbound=233, limit=30000):
        for _ in range(limit):
            coils, permits = self.tracker.outputs()
            coils = {door: on and door not in self.failing for door, on in coils.items()}
            edges = self.plant.step(.1, primary, outbound, coils, permits)
            self.tracker.update(.1, primary, outbound, edges)
            if self.tracker.stopped or not (self.plant.packages or self.plant.queue):
                return
        raise AssertionError("loop did not settle")

    def passes(self, pid):
        return sum(j["kind"] == "entered" and j["section"] == "recycle" and
                   j["physical_id"] == pid for j in self.plant.journal)

    def kinds(self, kind):
        return [e for e in self.tracker.events if e["kind"] == kind]


class SightingLoopTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.loops = []

    def tearDown(self):
        for loop in self.loops:
            loop.ledger.close_db()
        self.dir.cleanup()

    def loop(self, **kwargs):
        path = Path(self.dir.name) / f"ledger{len(self.loops)}.sqlite3"
        self.loops.append(Loop(path, **kwargs))
        return self.loops[-1]

    def test_routed_package_confirms_at_its_door_without_recycling(self):
        loop = self.loop(plan={1111: "1"})
        pid = loop.plant.induct(1111, 60)
        loop.run()
        self.assertEqual([p.physical_id for p in loop.plant.delivered], [pid])
        self.assertEqual(len(loop.kinds("confirmed")), 1)
        self.assertEqual(loop.passes(pid), 0)
        self.assertEqual(loop.tracker.alarms, [])

    def test_unrouted_package_recycles_three_times_then_exception_door(self):
        loop = self.loop()
        pid = loop.plant.induct(2222, 119)
        loop.run(primary=150, outbound=150)
        decisions = [e["decision"] for e in loop.kinds("sighting") if "decision" in e]
        self.assertEqual(decisions, ["route", "route", "route", "exception"])
        self.assertEqual(loop.ledger.used((7, 1), 2222), 3)
        self.assertEqual(loop.passes(pid), 3)
        self.assertEqual(len(loop.kinds("exception_entry")), 1)
        [delivery] = [j for j in loop.plant.journal if j["kind"] == "delivered"]
        self.assertEqual((delivery["physical_id"], delivery["door"]), (pid, "E"))

    def test_unreadable_package_goes_directly_to_exception(self):
        loop = self.loop(plan={3333: "1"}, unreadable={3333})
        pid = loop.plant.induct(3333, 20)
        loop.run(primary=300, outbound=300)
        self.assertEqual(loop.passes(pid), 0)
        self.assertEqual([e["door"] for e in loop.kinds("exception_entry")], ["E"])
        self.assertEqual(loop.ledger.used((7, 1), 3333), 0)

    def test_concurrent_duplicates_share_one_budget_and_never_exceed_the_bound(self):
        loop = self.loop()
        pids = [loop.plant.induct(5555, 80), loop.plant.induct(5555, 40)]
        loop.run()
        self.assertTrue(any(e.get("duplicate_barcode") for e in loop.kinds("sighting")))
        self.assertGreaterEqual(loop.ledger.used((7, 1), 5555), 3)
        self.assertEqual(sorted(p.physical_id for p in loop.plant.delivered), pids)
        # Offline evidence: each physical parcel's own passes stay within the
        # bound. The group count may exceed it, because sightings that were
        # already open when the budget ran out still spend when they recycle.
        self.assertTrue(all(loop.passes(pid) <= 3 for pid in pids))
        self.assertEqual(len(loop.kinds("exception_entry")), 2)

    def test_duplicate_behind_a_routed_label_is_independently_routed(self):
        loop = self.loop(plan={1111: "1"})
        first, second = loop.plant.induct(1111, 60), loop.plant.induct(1111, 60)
        loop.run()
        outbound = [e for e in loop.kinds("sighting") if e["section"] == "outbound"]
        self.assertEqual((outbound[0]["decision"], outbound[1]["decision"]),
                         ("route", "route"))
        self.assertEqual(loop.passes(first), 0)
        self.assertEqual(loop.passes(second), 0)
        self.assertEqual(len(loop.kinds("confirmed")), 2)

    def test_missing_end_eye_pulse_is_lost_never_recycled(self):
        loop = self.loop()
        pid = loop.plant.induct(4444, 60)
        for _ in range(500):
            coils, permits = loop.tracker.outputs()
            loop.tracker.update(.1, 200, 233, loop.plant.step(.1, 200, 233, coils, permits))
            if any(e["kind"] == "sighting" and e["section"] == "outbound"
                   for e in loop.tracker.events):
                break
        loop.plant.remove(pid, "test: parcel leaves the belt unobserved")
        for _ in range(300):
            coils, permits = loop.tracker.outputs()
            loop.tracker.update(.1, 200, 233, loop.plant.step(.1, 200, 233, coils, permits))
        self.assertEqual([a["kind"] for a in loop.tracker.alarms], ["package_lost"])
        self.assertFalse(loop.kinds("recycled"))
        self.assertEqual(loop.ledger.used((7, 1), 4444), 1)

    def test_unconfirmed_exception_divert_stops_instead_of_looping(self):
        loop = self.loop(unreadable={6666}, failing_doors={"E"})
        loop.plant.induct(6666, 60)
        loop.run()
        self.assertTrue(loop.tracker.stopped)
        self.assertEqual(loop.tracker.alarms[-1]["kind"], "exception_divert_failed")
        self.assertFalse(loop.kinds("recycled"))

    def test_late_chute_pulse_is_an_unexpected_entry_and_stops(self):
        slow = dataclasses.replace(SightingLayout(), chute_speed=30.0)
        loop = self.loop(plan={1111: "1"}, layout=slow)
        loop.plant.induct(1111, 60)
        loop.run()
        self.assertTrue(loop.tracker.stopped)
        self.assertEqual(loop.tracker.alarms[-1]["kind"], "unexpected_chute_entry")
        self.assertFalse(loop.kinds("confirmed"))

    def test_every_section_entry_gets_fresh_never_reused_values(self):
        loop = self.loop()
        loop.plant.induct(2222, 60)
        loop.run()
        allocated = loop.tracker.allocated
        # primary, outbound, then (recycle, outbound) for each of three passes
        self.assertEqual(len(allocated), 2 + 2 * 3)
        self.assertEqual(len({t for t, _ in allocated}), len(allocated))
        self.assertEqual(len({s for _, s in allocated}), len(allocated))
        sightings = [e["sighting"] for e in loop.kinds("sighting")]
        self.assertEqual(len(set(sightings)), len(sightings))

    def test_randomized_runs_respect_slots_bound_and_deliver_everything(self):
        for seed in range(6):
            rng = random.Random(seed)
            loop = self.loop(plan={1111: "1", 3333: "1"})
            labels = [rng.choice((1111, 2222, 3333, 4444)) for _ in range(6)]
            pids = [loop.plant.induct(label, rng.randint(20, 119)) for label in labels]
            rpm = rng.choice((150, 200, 233, 300))
            loop.run(primary=rpm, outbound=rpm)
            with self.subTest(seed=seed, rpm=rpm):
                self.assertEqual(loop.tracker.alarms, [])
                self.assertFalse(loop.tracker.stopped)
                self.assertLessEqual(loop.tracker.max_reserved, 3)
                self.assertEqual(sorted(p.physical_id for p in loop.plant.delivered), pids)
                self.assertTrue(all(loop.passes(pid) <= 3 for pid in pids))


if __name__ == "__main__":
    unittest.main()

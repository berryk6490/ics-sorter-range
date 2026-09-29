"""Contract regressions for the host-only sighting prototype."""
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sighting_reference import ReferenceTracker  # noqa: E402
from plant import BeamEdge, SightingLayout  # noqa: E402
from xle import PassLedger  # noqa: E402


class LedgerContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = PassLedger(self.path)

    def tearDown(self):
        self.ledger.close_db()
        self.temp.cleanup()

    def test_opening_spent_is_durable_and_concurrent_closures_can_exceed_n(self):
        run = (7, 11)
        for name in ("earlier-1", "earlier-2"):
            self.ledger.open(run, name, 5555, True)
            self.ledger.close(run, name, "recycled")
        verdicts = [self.ledger.open(run, f"pending-{i}", 5555, True)
                    for i in range(3)]
        self.assertEqual([v["decision"] for v in verdicts], ["route"] * 3)
        self.assertEqual([v["spent_at_open"] for v in verdicts], [2] * 3)
        for i in range(3):
            self.ledger.close(run, f"pending-{i}", "recycled")
        self.assertEqual(self.ledger.used(run, 5555), 5)
        self.assertEqual(self.ledger.open(run, "after", 5555, True)["decision"],
                         "exception")
        self.ledger.close_db()
        self.ledger = PassLedger(self.path)
        self.assertEqual(self.ledger.open(run, "pending-0", 5555, True)["spent_at_open"], 2)

    def test_nonce_is_part_of_durable_group_identity(self):
        self.ledger.open((7, 11), "a", 2222, True)
        self.ledger.close((7, 11), "a", "recycled")
        self.assertEqual(self.ledger.used((7, 11), 2222), 1)
        self.assertEqual(self.ledger.used((7, 12), 2222), 0)
        self.assertEqual(self.ledger.open((7, 12), "a", 2222, True)["spent_at_open"], 0)
        with self.assertRaises(KeyError):
            self.ledger.close((7, 12), "not-open", "recycled")

    def test_concurrent_readable_duplicates_are_independently_routable(self):
        run = (9, 3)
        first = self.ledger.open(run, "first", 1111, True)
        second = self.ledger.open(run, "second", 1111, True)
        self.assertEqual((first["decision"], second["decision"]), ("route", "route"))
        self.assertTrue(second["duplicate_barcode"])
        self.assertEqual(dict(self.ledger.db.execute(
            "SELECT sighting, decision FROM sightings WHERE barcode=1111")),
            {"first": "route", "second": "route"})

    def test_epoch_only_legacy_db_is_rejected_without_guessing_a_nonce(self):
        self.ledger.close_db()
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP TABLE sightings")
            db.execute("CREATE TABLE sightings (run_epoch INTEGER, sighting TEXT)")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(ValueError, "nonce cannot be inferred"):
            PassLedger(self.path)


class MergeContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = PassLedger(Path(self.temp.name) / "ledger.sqlite3")
        self.tracker = ReferenceTracker(SightingLayout(), self.ledger, (7, 11), {},
                                        lambda section: (True, 1111, 60))

    def tearDown(self):
        self.ledger.close_db()
        self.temp.cleanup()

    def edge(self, seq=1, time=.1):
        return BeamEdge(seq, "merge_entry", True, time)

    def test_through_traffic_is_recorded_without_new_identity(self):
        through = self.tracker._open("outbound", 145.0)
        before = len(self.tracker.allocated)
        self.tracker.update(.1, 200, 233, [self.edge()])
        self.assertFalse(self.tracker.stopped)
        self.assertEqual(len(self.tracker.allocated), before)
        self.assertEqual(through.section, "outbound")
        self.assertEqual([e["kind"] for e in self.tracker.events], ["merge_pass_by"])

    def test_missing_edge_stops_at_bounded_deadline(self):
        self.tracker._open("outbound", 145.0)
        for _ in range(8):
            self.tracker.update(.1, 200, 233, [])
            if self.tracker.stopped:
                break
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["kind"], "merge_visibility_fault")

    def test_missing_recycle_merge_edge_stops_without_opening_outbound(self):
        recycle = self.tracker._open("recycle", 1495.0)
        for _ in range(8):
            self.tracker.update(.1, 200, 233, [])
            if self.tracker.stopped:
                break
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["kind"], "merge_visibility_fault")
        self.assertEqual(recycle.section, "recycle")
        self.assertFalse(any(e["kind"] == "merge_admitted" for e in self.tracker.events))

    def test_stale_speed_rejects_edge_and_stops(self):
        self.tracker._open("outbound", 145.0)
        self.tracker.update(.1, 200, 233, [self.edge()], outbound_feedback_age=2.0)
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["kind"], "merge_visibility_fault")

    def test_zero_speed_pauses_prediction_until_motion_resumes(self):
        self.tracker._open("outbound", 130.0)
        self.tracker.update(.1, 200, 233, [])
        self.assertTrue(self.tracker._merge_expected)
        for _ in range(10):
            self.tracker.update(.1, 200, 0, [])
        self.assertFalse(self.tracker.stopped)
        self.tracker.update(.1, 200, 233, [])
        self.tracker.update(.1, 200, 233, [self.edge(time=1.3)])
        self.assertFalse(self.tracker.stopped)
        self.assertEqual(self.tracker.events[-1]["kind"], "merge_pass_by")

    def test_stale_edge_time_fails_closed(self):
        self.tracker._open("outbound", 145.0)
        self.tracker.update(.1, 200, 233, [self.edge(time=-1.0)])
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["reason"], "stale_edge_time")

    def test_ambiguous_edge_stops_without_reassigning_identity(self):
        through = self.tracker._open("outbound", 145.0)
        recycle = self.tracker._open("recycle", 1495.0)
        self.tracker.update(.1, 200, 233, [self.edge()])
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["kind"], "unexpected_merge_entry")
        self.assertEqual((through.section, recycle.section), ("outbound", "recycle"))

    def test_unexpected_edge_stops(self):
        self.tracker.update(.1, 200, 233, [self.edge()])
        self.assertTrue(self.tracker.stopped)
        self.assertEqual(self.tracker.alarms[-1]["kind"], "unexpected_merge_entry")


if __name__ == "__main__":
    unittest.main()

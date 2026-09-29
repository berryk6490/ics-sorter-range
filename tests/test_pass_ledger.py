"""XLe pass ledger: run-scoped barcode-group budget, SIGHTING_CONTRACT.md s6."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from xle import PassLedger  # noqa: E402


class PassLedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "ledger.sqlite3"
        self.ledger = PassLedger(self.path)

    def tearDown(self):
        self.ledger.close_db()
        self.dir.cleanup()

    def cycle(self, run, name, barcode, outcome="recycled", readable=True):
        verdict = self.ledger.open(run, name, barcode, readable)
        self.ledger.close(run, name, outcome)
        return verdict["decision"]

    def test_unreadable_goes_straight_to_exception_and_spends_nothing(self):
        self.assertEqual(self.ledger.open((1, 1), "s1", 0, False)["decision"], "exception")
        self.assertEqual(self.ledger.open((1, 1), "s2", 4321, False)["decision"], "exception")
        self.ledger.close((1, 1), "s1", "exception_entry")
        self.assertEqual(self.ledger.used((1, 1), 4321), 0)

    def test_third_recycle_sends_the_next_sighting_to_exception(self):
        decisions = [self.cycle((1, 1), f"s{i}", 2222) for i in range(3)]
        self.assertEqual(decisions, ["route"] * 3)
        self.assertEqual(self.ledger.used((1, 1), 2222), 3)
        self.assertEqual(self.ledger.open((1, 1), "s3", 2222, True)["decision"], "exception")

    def test_lost_spends_budget_and_confirmed_does_not(self):
        self.cycle((1, 1), "a", 3333, "lost")
        self.cycle((1, 1), "b", 3333, "confirmed")
        self.assertEqual(self.ledger.used((1, 1), 3333), 1)

    def test_close_counts_once_and_rejects_unknown_input(self):
        self.ledger.open((1, 1), "a", 3333, True)
        self.assertTrue(self.ledger.close((1, 1), "a", "recycled")["recorded"])
        self.assertFalse(self.ledger.close((1, 1), "a", "recycled")["recorded"])
        self.assertEqual(self.ledger.used((1, 1), 3333), 1)
        with self.assertRaises(ValueError):
            self.ledger.close((1, 1), "a", "vanished")
        with self.assertRaises(KeyError):
            self.ledger.close((1, 1), "never-opened", "recycled")

    def test_budget_is_durable_and_run_scoped_never_reset_by_closing(self):
        for i in range(2):
            self.cycle((5, 1), f"s{i}", 4444)
        self.ledger.close_db()
        self.ledger = PassLedger(self.path)
        self.assertEqual(self.ledger.used((5, 1), 4444), 2)
        self.assertEqual(self.ledger.open((6, 1), "fresh", 4444, True)["decision"], "route")
        self.cycle((5, 1), "s2", 4444)
        self.assertEqual(self.ledger.open((5, 1), "s3", 4444, True)["decision"], "exception")

    def test_replayed_sighting_keeps_its_decision_after_restart(self):
        first = self.ledger.open((1, 1), "s1", 1111, True)
        self.ledger.close_db()
        self.ledger = PassLedger(self.path)
        again = self.ledger.open((1, 1), "s1", 1111, True)
        self.assertEqual((again["decision"], again["replayed"]), (first["decision"], True))

    def test_concurrent_duplicate_is_diagnostic_and_both_remain_routable(self):
        self.assertEqual(self.ledger.open((1, 1), "early", 5555, True)["decision"], "route")
        second = self.ledger.open((1, 1), "late", 5555, True)
        self.assertEqual((second["decision"], second["duplicate_barcode"]), ("route", True))
        rows = dict(self.ledger.db.execute(
            "SELECT sighting, decision FROM sightings WHERE barcode=5555"))
        self.assertEqual(rows["early"], "route")
        flags = dict(self.ledger.db.execute(
            "SELECT sighting, duplicate_barcode FROM sightings WHERE barcode=5555"))
        self.assertEqual(flags, {"early": 1, "late": 1})
        # Both physical parcels draw on one shared budget.
        self.ledger.close((1, 1), "late", "recycled")
        self.ledger.close((1, 1), "early", "recycled")
        self.assertEqual(self.ledger.used((1, 1), 5555), 2)

    def test_recycled_exception_sighting_is_reported_as_failed(self):
        for i in range(3):
            self.cycle((1, 1), f"s{i}", 6666)
        self.ledger.open((1, 1), "exc", 6666, True)
        self.assertTrue(self.ledger.close((1, 1), "exc", "recycled")["exception_failed"])

    def test_limit_must_be_a_non_negative_integer(self):
        for limit in (-1, 1.5, "3"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                PassLedger(":memory:", limit=limit)


if __name__ == "__main__":
    unittest.main()

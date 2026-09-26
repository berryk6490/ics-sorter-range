"""Cross-version typed restoration must retain every prior enforced field."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_accumulation_state_snapshot import sample
from chute_migration_postflight import compare_migration


def old_baseline():
    value = sample()
    value["schema_version"] = 2
    value["program_identity"] = 24114
    value["plc"]["reader_schema"] = 2
    value["plc"]["identity"] = 24114
    del value["plc"]["chute"]
    return value


class MigrationRestorationTests(unittest.TestCase):
    def test_safe_restoration_across_identity_and_schema(self):
        report = compare_migration(old_baseline(), sample())
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(len(report["fields"]), 219)
        self.assertEqual(report["baseline_program_identity"], 24114)
        self.assertEqual(report["deployed_program_identity"], 24115)

    def test_old_counter_and_new_chute_fault_both_fail(self):
        after = sample()
        after["plc"]["trailer_counters"][1] = 1
        after["plc"]["chute"]["validated"][3] = 4
        report = compare_migration(old_baseline(), after)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual({row["field"] for row in report["differences"]},
                         {"plc.trailer_counters.1", "plc.chute.validated.3"})

    def test_unexpected_program_or_baseline_is_rejected(self):
        after = sample()
        after["program_identity"] = 24114
        self.assertEqual(compare_migration(old_baseline(), after)["status"], "FAIL")
        before = old_baseline()
        before["plc"]["identity"] = 1
        self.assertEqual(compare_migration(before, sample())["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()

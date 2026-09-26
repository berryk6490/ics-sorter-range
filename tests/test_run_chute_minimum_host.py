"""Host-only evidence and restoration guards for the one-run controller."""
import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_chute_minimum_host as stage


class MinimumHostGuards(unittest.TestCase):
    def test_plan_restore_requires_exact_temporary_hash(self):
        with patch.object(stage, "root_xle", return_value="unrelated hash") as guest:
            with self.assertRaisesRegex(RuntimeError, "changed unexpectedly"):
                stage.restore_plan()
        guest.assert_called_once_with("sha256sum " + stage.PLAN)

    def test_plan_restore_uses_preserved_bytes_and_checks_hash(self):
        responses = [stage.TEMP_PLAN_HASH, stage.ORIGINAL_PLAN_HASH]
        with patch.object(stage, "root_xle", side_effect=responses) as guest:
            result = stage.restore_plan()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(guest.call_count, 2)
        self.assertIn(stage.PLAN_BACKUP, guest.call_args.args[0])
        self.assertIn("install -o root -g root -m 0644", guest.call_args.args[0])

    def test_journal_waits_for_four_matching_outcomes(self):
        values = [{"outcomes": [], "outcome_count": n} for n in (3, 4)]
        with patch.object(stage, "root_xle", side_effect=(json.dumps(x) for x in values)):
            with patch.object(stage.time, "sleep"):
                record, probes = stage.journal_outcomes(44, 5)
        self.assertEqual(record["outcome_count"], 4)
        self.assertEqual([p["count"] for p in probes], [3, 4])

    def test_guest_evidence_is_complete_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "record.jsonl"
            row = json.dumps({"run": 1, "values": list(range(100))}).encode() + b"\n"
            payload = row + row
            encoded = base64.b64encode(gzip.compress(payload)).decode()
            # The real serial wrapper includes an echoed command and one
            # bounded base64 line, with a completion marker checked upstream.
            with patch.object(stage, "guest", return_value={
                    "command_rc": 0, "output": "echoed command\n" + encoded + "\n"}):
                self.assertEqual(stage.fetch_guest_jsonl("/guest/file", output), 2)
            self.assertEqual(output.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()

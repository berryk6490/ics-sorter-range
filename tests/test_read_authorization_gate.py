"""The batched guest pre-authorization reader stays read-only and bounded."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import read_authorization_gate as reader


class Result:
    def __init__(self, output):
        self.stdout = output
        self.returncode = 0


class ReaderTests(unittest.TestCase):
    def test_systemd_is_absolute_noninteractive_and_processes_bounded(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == "env":
                return Result("active\n" * (len(argv) - 6))
            return Result("123 /home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation_detached.py worker\n")

        result = reader.read("scada", run)
        self.assertEqual(calls[0][0][:6],
                         ["env", "SYSTEMD_PAGER=cat", "SYSTEMD_COLORS=0",
                          "/usr/bin/systemctl", "--no-pager", "is-active"])
        self.assertEqual(calls[0][1]["timeout"], 6)
        self.assertEqual(result["processes"][0]["pid"], 123)
        self.assertIsNone(result["plc"])

    def test_incomplete_service_read_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete service"):
            reader.read("drives", lambda argv, **kw: Result("active\n"))


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import socket
import unittest

from network_reachability import check


class ReachabilityTest(unittest.TestCase):
    def test_connection_and_refusal_are_distinct(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            matrix = {"sources": {"local": "127.0.0.1"}, "flows": [
                {"id": "open", "source": "local", "destination": "127.0.0.1",
                 "port": port, "expected": "allow"},
            ]}
            self.assertEqual(check(matrix, "local")[0]["observed"], "allow")
        finally:
            listener.close()
        matrix["flows"][0]["expected"] = "deny"
        self.assertEqual(check(matrix, "local")[0]["observed"], "error")

    def test_matrix_is_complete_and_unambiguous(self):
        matrix = json.loads(Path(__file__).with_name("network_flows.json").read_text())
        ids = [row["id"] for row in matrix["flows"]]
        self.assertEqual(len(ids), len(set(ids)))
        for row in matrix["flows"]:
            self.assertIn(row["source"], matrix["sources"])
            self.assertIn(row["expected"], ("allow", "deny"))
            self.assertTrue(row["purpose"])
            self.assertGreater(row["port"], 0)
        self.assertEqual(sum(row.get("probe", True) for row in matrix["flows"]), 48)
        self.assertEqual(matrix["sources"]["xle"], "10.10.2.20")


if __name__ == "__main__":
    unittest.main()

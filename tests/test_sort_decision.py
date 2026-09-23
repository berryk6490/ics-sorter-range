import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import io
import json


ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "services" / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


asx = module("asx")
xle = module("xle")
REQUEST = {"package_id": "l1-1-1-abc", "request_id": "req-1", "barcode": 6001}


class Decisions(unittest.TestCase):
    def test_valid_plan_is_explicit(self):
        response = asx.decide(REQUEST, {"6001": 2})
        self.assertEqual(xle.validate_decision(REQUEST, response), (2, None))
        self.assertEqual(response["destination"], 2)

    def test_unknown_package_has_no_route(self):
        response = asx.decide(REQUEST, {})
        self.assertEqual(xle.validate_decision(REQUEST, response), (None, "unknown_package"))

    def test_stale_response_rejected(self):
        response = asx.decide(REQUEST, {"6001": 2})
        response["package_id"] = "old-package"
        self.assertEqual(xle.validate_decision(REQUEST, response),
                         (None, "stale_or_mismatched_response"))
        response["package_id"] = REQUEST["package_id"]
        response["request_id"] = "old-request"
        self.assertEqual(xle.validate_decision(REQUEST, response),
                         (None, "stale_or_mismatched_response"))

    def test_delayed_lookup_falls_back(self):
        with patch.object(xle.urllib.request, "urlopen", side_effect=TimeoutError):
            destination, reason = xle.lookup(REQUEST, "http://127.0.0.1:8089/sort-plan")
        self.assertIsNone(destination)
        self.assertIn("decision_timeout_or_error", reason)

    def test_asx_plan_file(self):
        plan = json.loads((ROOT / "services/sort_plan.json").read_text())["barcodes"]
        self.assertEqual(asx.decide(REQUEST, plan)["destination"], 2)


if __name__ == "__main__":
    unittest.main()

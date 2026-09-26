"""Host-only checks for the PLC-owned selected chute supervisory surface."""
import ast
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def isolated_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class SupervisoryContractTests(unittest.TestCase):
    def test_new_plc_addresses_are_unique_and_generated_copy_matches(self):
        source = (ROOT / "Sorter.st").read_bytes()
        self.assertEqual(source, (ROOT / "gen_sorter.py").read_bytes())
        words = re.findall(rb"\b(\w+)\s+AT\s+%QW(\d+)", source)
        addresses = [int(address) for _, address in words]
        self.assertEqual(len(addresses), len(set(addresses)))
        self.assertEqual(set(range(790, 833)), set(addresses) & set(range(790, 833)))
        self.assertIn(b"chute_mode AT %QX115.1", source)
        self.assertIn(b"prog_hash := 24115", source)

    def test_hmi_api_exposes_only_trailer_two_as_measured_and_marks_stale(self):
        path = ROOT / "scada/hmi_ua.py"
        ns = {"lock": threading.Lock(), "rates": lambda: (0, 0),
              "jsonify": lambda value: value, "time": time,
              "DRIVE_NODES": ["Induct1", "Induct2", "Induct3",
                              "Outbound1", "Outbound2", "Outbound3"],
              "DRIVE_NAMES": ["induct 1", "induct 2", "induct 3",
                              "outbnd 1", "outbnd 2", "outbnd 3"]}
        tags = isolated_function(path, "_tagmap", ns)()
        required = {"chute_mode", "chute_trailer", "chute_capacity", "chute_state",
                    "chute_quality", "chute_occupied", "chute_permit",
                    "chute_action_ack", "chute_action_result", "plc_poll_ms"}
        self.assertLessEqual(required, tags.keys())
        state = {key: 0 for key in tags}
        state.update(connected=True, chute_mode=True, chute_trailer=2,
                     chute_capacity=3, chute_occupied=3, chute_state=1,
                     chute_quality=1, plc_poll_ms=time.monotonic_ns() // 1_000_000)
        ns["state"] = state
        api = isolated_function(path, "api", ns)
        live = api()["chute"]
        self.assertEqual(live["measured_trailer"], 2)
        self.assertEqual(live["occupied"], 3)
        self.assertTrue(live["poll_fresh"])
        self.assertEqual(live["unmeasured_trailers"], [1, 3, 4, 5, 6, 7, 8, 9])
        state["plc_poll_ms"] -= 2000
        self.assertFalse(api()["chute"]["poll_fresh"])
        state["chute_mode"] = False
        self.assertIsNone(api()["chute"]["measured_trailer"])

    def test_hmi_action_packet_is_run_bound_and_ordered(self):
        path = ROOT / "scada/hmi_ua.py"
        packet = []
        state = {"connected": True, "chute_mode": True, "chute_quality": 1,
                 "chute_trailer": 2, "plc_poll_ms": time.monotonic_ns() // 1_000_000,
                 "chute_action_ack": 9, "chute_action_result": 0,
                 "chute_epoch_lo": 42, "chute_epoch_hi": 0, "chute_nonce": 7}

        class Node:
            def write_value(self, value):
                packet.extend(value)
                return SimpleNamespace(close=lambda: None)

        class Future:
            def result(self, timeout):
                self.assert_timeout(timeout)
                state["chute_action_ack"] = packet[-1]
                state["chute_action_result"] = 1

            def assert_timeout(self, timeout):
                assert timeout == 3

        def run_threadsafe(coroutine, loop):
            self.assertEqual(loop, "loop")
            coroutine.close()
            return Future()

        ns = {"lock": threading.Lock(), "state": state, "time": time,
              "ua_ctx": {"loop": "loop", "nodes": {"chute_action": Node()}},
              "ua": SimpleNamespace(Variant=lambda value, kind: value,
                                    VariantType=SimpleNamespace(Int16=1)),
              "asyncio": SimpleNamespace(run_coroutine_threadsafe=run_threadsafe),
              "jsonify": lambda value: value}
        action = isolated_function(path, "chute_action", ns)
        response, code = action("empty")
        self.assertEqual(code, 200)
        self.assertEqual(response, {"ok": True, "sequence": 10, "result": 1})
        self.assertEqual(packet, [2, 42, 0, 7, 2, 10])


if __name__ == "__main__":
    unittest.main()

"""Host-only gates for the bounded chute live runner."""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).with_name("live_chute_minimum.py")


def function(name, namespace=None):
    tree = ast.parse(SOURCE.read_text())
    node = next(item for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    space = namespace or {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), space)
    return space[name]


class Reply:
    def __init__(self, bad=False):
        self.bad = bad

    def isError(self):
        return self.bad


class Client:
    def __init__(self):
        self.calls = []

    def write_coils(self, address, values, slave):
        self.calls.append((address, values, slave))
        return Reply()


class ChuteLiveRunnerTests(unittest.TestCase):
    def test_all_plant_modes_enable_in_one_modbus_transaction(self):
        client = Client()
        function("enable_plant_modes")(client)
        self.assertEqual(client.calls, [(918, [True, True, True, True], 1)])

    def test_atomic_mode_failure_stops_before_master(self):
        client = Client()
        client.write_coils = lambda *args, **kwargs: Reply(True)
        with self.assertRaisesRegex(RuntimeError, "enable plant"):
            function("enable_plant_modes")(client)

    def test_identity_or_wrong_trailer_is_never_a_pass(self):
        check = function("assert_safe")
        state = {"plant_fault": 0, "zone_fault": 0,
                 "photoeye_faults": [0, 0, 0], "xle_health": [0],
                 "trailer": [0, 3] + [0] * 7, "inducted": 4,
                 "slots": [[1, 1, 1, 6001, 3, 2, 2] + [0] * 5]}
        check(state)
        state["slots"][0][6] = 5
        with self.assertRaisesRegex(AssertionError, "wrong route"):
            check(state)
        state["slots"][0][6] = 2
        state["trailer"][4] = 1
        with self.assertRaisesRegex(AssertionError, "wrong trailer"):
            check(state)


if __name__ == "__main__":
    unittest.main()

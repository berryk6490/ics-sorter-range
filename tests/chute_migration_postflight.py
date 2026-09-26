"""Typed restoration across the deliberate PLC 24114 -> 24115 upgrade.

The pre-deployment snapshot uses reader schema 2. Normalize only the new
chute fields and the expected program identity, then apply the canonical
schema-3 comparator to every prior field and the new safe-state checks.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from accumulation_state_snapshot import SCHEMA, compare

OLD_IDENTITY = 24114
NEW_IDENTITY = 24115


def compare_migration(before, after):
    if (before.get("schema_version") != 2 or
        before.get("program_identity") != OLD_IDENTITY or
        before.get("plc", {}).get("reader_schema") != 2 or
        before["plc"].get("identity") != OLD_IDENTITY):
        return {"status": "FAIL", "error": "invalid 24114 baseline"}
    if (after.get("schema_version") != SCHEMA or
        after.get("program_identity") != NEW_IDENTITY or
        after.get("plc", {}).get("identity") != NEW_IDENTITY):
        return {"status": "FAIL", "error": "invalid 24115 postflight"}
    expected = deepcopy(before)
    expected["schema_version"] = SCHEMA
    expected["program_identity"] = NEW_IDENTITY
    expected["plc"]["reader_schema"] = 3
    expected["plc"]["identity"] = NEW_IDENTITY
    expected["plc"]["chute"] = {
        "mode": False, "configuration": [2, 3],
        "validated": [0] * 6 + [1] + [0] * 15,
        "accepted_terminal": [0] * 3, "raw": [0] * 16,
    }
    report = compare(expected, after)
    report["baseline_program_identity"] = OLD_IDENTITY
    report["deployed_program_identity"] = NEW_IDENTITY
    report["baseline_schema_version"] = 2
    return report


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--before", required=True)
    cli.add_argument("--after", required=True)
    cli.add_argument("--output", required=True)
    args = cli.parse_args()
    result = compare_migration(json.loads(Path(args.before).read_text()),
                               json.loads(Path(args.after).read_text()))
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"],
                      "differences": len(result.get("differences", [])),
                      "fields": len(result.get("fields", []))}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

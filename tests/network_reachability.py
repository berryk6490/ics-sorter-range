"""Run the checked TCP flow matrix on a guest (Python standard library only)."""
import argparse
import json
import socket
import time
from pathlib import Path


def check(matrix, source, timeout=1.2):
    origin = matrix["sources"][source]
    results = []
    for flow in matrix["flows"]:
        if flow["source"] != source or not flow.get("probe", True):
            continue
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.bind((flow.get("source_address", origin), 0))
            sock.connect((flow["destination"], flow["port"]))
            observed = "allow"
            detail = "connected"
        except socket.timeout:
            observed = "deny"
            detail = "timeout"
        except OSError as exc:
            observed = "error"
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            sock.close()
        results.append({"id": flow["id"], "expected": flow["expected"],
                        "observed": observed, "detail": detail,
                        "elapsed_ms": round((time.monotonic() - start) * 1000)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=("plc", "drives", "scada", "xle", "analyst"))
    parser.add_argument("--matrix", type=Path, default=Path(__file__).with_name("network_flows.json"))
    parser.add_argument("--timeout", type=float, default=1.2)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text())
    rows = check(matrix, args.source, args.timeout)
    for row in rows:
        print(json.dumps(row, sort_keys=True), flush=True)
    passed = sum(row["expected"] == row["observed"] for row in rows)
    print(f"{args.source}: {passed}/{len(rows)} matched", flush=True)
    raise SystemExit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()

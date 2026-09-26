"""Read-only, identity-scoped XLe outcome proof for one chute test run."""
import argparse
import json
import sqlite3
from pathlib import Path

JOURNAL = Path("/var/lib/sorter-xle/outcomes.sqlite3")


def read(epoch, nonce):
    if not 1 <= epoch <= 899999999 or not 1 <= nonce <= 30000:
        raise ValueError("invalid run identity")
    with sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=5) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("journal integrity failed: " + integrity)
        prefix = f"{epoch}:{nonce}:"
        rows = [{"identity": identity, "payload": json.loads(payload)}
                for identity, payload in db.execute(
                    "SELECT identity,payload FROM outcomes WHERE identity LIKE ? "
                    "ORDER BY identity", (prefix + "%",))]
    return {"epoch": epoch, "nonce": nonce, "integrity": integrity,
            "outcome_count": len(rows), "outcomes": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--nonce", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(read(args.epoch, args.nonce), sort_keys=True), flush=True)

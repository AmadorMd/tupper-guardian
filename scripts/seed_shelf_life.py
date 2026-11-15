#!/usr/bin/env python3
"""Load src/guardian/shelf_life.json into the ShelfLife table via the AWS CLI.

Usage: seed_shelf_life.py <table-name>
"""

import json
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "src" / "guardian" / "shelf_life.json"
BATCH_SIZE = 25  # BatchWriteItem limit


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    table = argv[1]
    entries = json.loads(DATA.read_text("utf-8"))["categories"]
    requests = [
        {"PutRequest": {"Item": {
            "category": {"S": entry["category"]},
            "days": {"N": str(entry["days"])},
            "examples": {"S": entry["examples"]},
        }}}
        for entry in entries
    ]
    for start in range(0, len(requests), BATCH_SIZE):
        batch = {table: requests[start:start + BATCH_SIZE]}
        result = subprocess.run(
            ["aws", "dynamodb", "batch-write-item", "--request-items", json.dumps(batch),
             "--query", "UnprocessedItems", "--output", "json"],
            check=True, capture_output=True, text=True,
        )
        if json.loads(result.stdout or "{}"):
            sys.exit("Some items were not written; run the seed again.")
    print(f"Seeded {len(requests)} categories into {table}.")


if __name__ == "__main__":
    main(sys.argv)

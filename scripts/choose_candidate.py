#!/usr/bin/env python3
"""Select one immutable line from candidates.jsonl into fork_candidate.json."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input", type=Path, default=Path("candidates.jsonl"))
p.add_argument("--line", type=int, default=-1, help="0-based index; -1 most recent")
p.add_argument("--output", type=Path, default=Path("fork_candidate.json"))
args = p.parse_args()
lines = [x for x in args.input.read_text(encoding="utf-8").splitlines() if x.strip()]
row = json.loads(lines[args.line])
if row.get("status") != "QUOTED_NOT_FORK_VERIFIED":
    raise SystemExit("Unexpected candidate status")
args.output.write_text(json.dumps(row, indent=2), encoding="utf-8")
print(args.output.resolve())

"""Offline paper-signal statistics. Does NOT equate quotes with executed trades."""
from __future__ import annotations
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path


def analyze(rows: list[dict]) -> dict:
    # Key opportunities by actual pair identifiers, not just exchange labels.
    per_route: dict[tuple, set[int]] = defaultdict(set)
    per_route_profit: dict[tuple, int] = {}
    all_blocks: set[int] = set()
    for row in rows:
        if row.get("status") != "QUOTED_NOT_FORK_VERIFIED":
            continue
        block = int(row["block"])
        all_blocks.add(block)
        key = (row["first_pool"].lower(), row["second_pool"].lower())
        per_route[key].add(block)
        per_route_profit[key] = max(per_route_profit.get(key, -10**30),
                                    int(row["estimated_net_usdc"]))
    results = []
    for key, blocks in per_route.items():
        ordered = sorted(blocks)
        streak = longest = 1 if ordered else 0
        for a, b in zip(ordered, ordered[1:]):
            streak = streak + 1 if b == a + 1 else 1
            longest = max(longest, streak)
        results.append({"buy_pool":key[0], "sell_pool":key[1],
                        "positive_sampled_blocks":len(blocks),
                        "longest_consecutive_sampled_block_streak":longest,
                        "max_modeled_profit_usdc":str(Decimal(per_route_profit[key])/10**6)})
    results.sort(key=lambda x: int(x["positive_sampled_blocks"]), reverse=True)
    return {"unique_observed_blocks":len(all_blocks),
            "positive_opportunity_routes":len(results),
            "routes":results,
            "interpretation":"Only blocks where this bot sampled and found candidates. Not continuous elapsed-time survival, realized PnL, or success rate."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=Path("candidates.jsonl"))
    args = p.parse_args()
    if not args.input.exists():
        print("No candidate file (no positive signals detected yet).")
        return
    rows = [json.loads(x) for x in args.input.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(json.dumps(analyze(rows), indent=2))


if __name__ == "__main__": main()

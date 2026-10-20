"""Historical, pinned-block Base QuoterV2 backtest with auditable coverage.

Reads real historical state. No deployment or broadcast. Historic quote math is NOT
an executed trade. Only successful ForkProof execution can validate positive PnL.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from .rpc import RPC, RpcError
from .searcher import discover, USDC_UNIT

ENDPOINTS = (
    ("base_official", "https://mainnet.base.org"),
    ("publicnode", "https://base-rpc.publicnode.com"),
    ("drpc", "https://base.drpc.org"),
)

class HistoricalRPC:
    """Pin EVERY call to the same fixed block; use that block's L2 base fee."""
    def __init__(self, rpc: RPC, block: int):
        if block < 1:
            raise ValueError("Historical block must be positive")
        self.rpc, self.block = rpc, block
        self._block_info = None

    def chain_id(self):
        return self.rpc.chain_id()

    def block_number(self):
        return self.block

    def gas_price(self):
        if self._block_info is None:
            self._block_info = self.rpc.call("eth_getBlockByNumber", [hex(self.block), False])
        if not isinstance(self._block_info, dict):
            raise RpcError("Historical block missing")
        if self._block_info.get("baseFeePerGas") is None:
            raise RpcError("Historical base fee unavailable")
        return int(self._block_info["baseFeePerGas"], 16)

    def eth_call(self, to, data, block, sender=None):
        if block != hex(self.block):
            raise RpcError("Backtest eth_call has mismatched block tag")
        return self.rpc.eth_call(to, data, block, sender)

    def batch_eth_call(self, calls, chunk_size=50):
        if any(tag != hex(self.block) for _, _, tag in calls):
            raise RpcError("Batch contains mismatched historical block")
        return self.rpc.batch_eth_call(calls, chunk_size=chunk_size)


def run_history(*, lookbacks: list[int], amounts: list[int], rpc_timeout: float,
                output: Path, max_seconds: float = 220.0, min_profit_usdc: int = USDC_UNIT,
                target_blocks: list[int] | None = None) -> dict:
    if not lookbacks or min(lookbacks) < 1 or any(x <= 0 for x in amounts):
        raise ValueError("Invalid lookbacks or loan amounts")
    output.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    attempts = []
    # Select first functioning chain-8453 endpoint, then retry per block on provider errors.
    providers = []
    for label, url in ENDPOINTS:
        try:
            rpc = RPC(url, timeout=rpc_timeout)
            if rpc.chain_id() != 8453:
                attempts.append({"endpoint": label, "error": "Wrong chain"})
            else:
                providers.append((label, rpc))
        except (RpcError, OSError, ValueError) as exc:
            attempts.append({"endpoint": label, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    if not providers:
        result = {"status": "RPC_UNAVAILABLE", "provider_attempts": attempts, "snapshots": []}
        (output/"history-report.json").write_text(json.dumps(result, indent=2))
        return result
    head = providers[0][1].rpc.block_number() if hasattr(providers[0][1], "rpc") else providers[0][1].block_number()
    # Event-detected blocks are absolute, so don't drift when the chain head moves
    # between high-volume log detection and historical quote scanning.
    event_lags = [head - b for b in (target_blocks or []) if 0 < head - b < head]
    ordered_lags = list(dict.fromkeys(event_lags + lookbacks))
    snapshots, candidates = [], []
    for back in ordered_lags:
        if time.monotonic()-began >= max_seconds:
            snapshots.append({"lookback": back, "status": "TIME_BUDGET_EXCEEDED"})
            break
        block = head-back
        if block < 1:
            snapshots.append({"lookback": back, "status": "INVALID_BLOCK"})
            continue
        record = {"lookback": back, "block": block, "status": "NO_PROVIDER_SUCCEEDED"}
        for label, rpc in providers:
            try:
                started = time.monotonic()
                snap = HistoricalRPC(rpc, block)
                scan = discover(snap, amounts_usdc=amounts,
                                min_profit_usdc=min_profit_usdc,
                                max_candidates=5)
                record = {
                    "lookback": back, "block": block, "endpoint": label,
                    "status": "QUOTES_COMPLETE",
                    "seconds": round(time.monotonic()-started, 2),
                    "pools": scan["pools_discovered"],
                    "tested": scan["pairs_tested"],
                    "failed_quotes": scan["quote_failures"],
                    "quote_metrics": scan["quote_metrics"],
                    "candidate_count": len(scan["candidates"]),
                    "modeled_gas_usdc_units": scan["gas_model_usdc"],
                }
                for candidate in scan["candidates"]:
                    candidate["historical_backtest"] = True
                    candidate["status"] = "QUOTED_NOT_FORK_VERIFIED"
                    candidate["historic_endpoint"] = label
                    candidates.append(candidate)
                break
            except (RpcError, OSError, ValueError, TypeError, KeyError) as exc:
                record.setdefault("provider_errors", []).append({
                    "endpoint": label, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
        snapshots.append(record)
        (output/"history-progress.json").write_text(
            json.dumps({"snapshots": snapshots, "elapsed": round(time.monotonic()-began, 2)}, indent=2))
    valid = [r for r in snapshots if r["status"] == "QUOTES_COMPLETE"]
    gross = [r["quote_metrics"]["best_before_gas_usdc_units"] for r in valid
             if r["quote_metrics"]["best_before_gas_usdc_units"] is not None]
    net = [r["quote_metrics"]["best_after_modeled_gas_usdc_units"] for r in valid
           if r["quote_metrics"]["best_after_modeled_gas_usdc_units"] is not None]
    report = {
        "status": "HISTORICAL_QUOTE_BACKTEST_ONLY",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "chain": "Base mainnet 8453",
        "head_block": head,
        "lookbacks_requested": ordered_lags,
        "event_triggered_blocks_requested": [b for b in (target_blocks or []) if 0 < b < head],
        "loan_amounts_usdc": [str(Decimal(x)/USDC_UNIT) for x in amounts],
        "snapshot_blocks_attempted": len(snapshots),
        "snapshot_blocks_quoted": len(valid),
        "pairs_quoted": sum(r["quote_metrics"]["completed_quotes"] for r in valid),
        "modeled_positive_candidates": len(candidates),
        "max_gross_after_flash_fee_usdc_units": max(gross) if gross else None,
        "max_net_after_modeled_gas_usdc_units": max(net) if net else None,
        "mean_best_net_per_block_usdc_units":
            round(sum(net)/len(net)) if net else None,
        "historical_samples": snapshots,
        "providers_available": [x[0] for x in providers],
        "provider_attempts": attempts,
        "wall_seconds": round(time.monotonic()-began, 2),
        "historical_execution_verified_positive_count": 0,
        "interpretation": "Quoter-only historical snapshots, NOT full EVM executions or live PnL. A candidate requires Forge fork replay and positive AFTER-GAS net PnL to become verified.",
        "source": "Pinned-block public Base JSON-RPC",
        "broadcast": False,
    }
    (output/"history-report.json").write_text(json.dumps(report, indent=2))
    (output/"history-candidates.jsonl").write_text(
        "".join(json.dumps(c)+"\n" for c in candidates))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("validation/history"))
    parser.add_argument("--lookbacks", default="16,48,128,512,2048,8192")
    parser.add_argument("--event-blocks", type=Path, help="JSON event selector with exact historical block numbers")
    parser.add_argument("--amounts-usdc", default="200,1000,5000,20000")
    parser.add_argument("--timeout", type=float, default=9)
    parser.add_argument("--max-seconds", type=float, default=220)
    parser.add_argument("--min-profit-usdc", type=str, default="1")
    args = parser.parse_args(argv)
    try:
        lags = sorted(set(int(x) for x in args.lookbacks.split(",")))
        amounts = sorted(set(int(Decimal(x)*USDC_UNIT) for x in args.amounts_usdc.split(",")))
        profit = int(Decimal(args.min_profit_usdc)*USDC_UNIT)
        event_blocks = []
        if args.event_blocks and args.event_blocks.is_file():
            events = json.loads(args.event_blocks.read_text())
            event_blocks = [int(r["block"]) for r in events.get("selected", [])]
        summary = run_history(lookbacks=lags, amounts=amounts, rpc_timeout=args.timeout,
                              output=args.out, max_seconds=args.max_seconds,
                              min_profit_usdc=profit, target_blocks=event_blocks)
        print(json.dumps({k:v for k,v in summary.items()
                          if k not in ("historical_samples", "provider_attempts")}, indent=2))
        for item in summary.get("historical_samples", []):
            print(json.dumps(item))
        return 0 if summary.get("snapshot_blocks_quoted", 0) else 2
    except (ValueError, RpcError, OSError) as exc:
        print(f"BACKTEST_UNAVAILABLE: {exc}")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

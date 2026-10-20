"""Read-only Aave V3 Base LiquidationCall event survey.

This indexes *already executed* liquidations. Historical liquidator rewards are
NOT opportunities available to a new liquidator. It does not infer profit from
the liquidation amount or invent a liquidation bonus.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Any

from bot.abi import encode, result_address, keccak256
from bot.networks import BASE_CHAIN_ID, AAVE_PROVIDER, USDC, WETH
from bot.rpc import RPC, RpcError
from bot.historical import ENDPOINTS

SIGNATURE = "LiquidationCall(address,address,address,uint256,uint256,address,bool)"
TOPIC = "0x" + keccak256(SIGNATURE.encode()).hex()
ZERO = "0x" + "0" * 40
ASSET_DECIMALS = {USDC.lower(): ("USDC", 6), WETH.lower(): ("WETH", 18)}
MAX_LOGS = 150

def topic_address(topic: str) -> str:
    if not isinstance(topic, str) or len(topic) != 66 or not topic.startswith("0x"):
        raise ValueError("Malformed indexed address")
    value = int(topic, 16)
    if value >= (1 << 160):
        raise ValueError("Indexed address high bits not zero")
    return "0x" + f"{value:040x}"

def decode_liquidation(log: dict, pool: str) -> dict:
    topics = log.get("topics", [])
    if len(topics) != 4 or topics[0].lower() != TOPIC.lower():
        raise ValueError("Not an Aave LiquidationCall")
    if log["address"].lower() != pool.lower() or log.get("removed", False):
        raise ValueError("Unexpected emitting contract or removed log")
    data = log["data"]
    if not isinstance(data, str) or not data.startswith("0x") or len(data) != 2 + 4 * 64:
        raise ValueError("Malformed LiquidationCall data")
    fields = [int(data[2+i*64:2+(i+1)*64], 16) for i in range(4)]
    debt, collateral, liquidator_word, receive_atoken = fields
    if liquidator_word >= 1 << 160 or receive_atoken not in (0, 1):
        raise ValueError("Noncanonical Aave event ABI")
    if debt <= 0 or collateral <= 0:
        raise ValueError("Invalid liquidation amounts")
    return {
        "block": int(log["blockNumber"], 16),
        "transaction_hash": log["transactionHash"],
        "transaction_index": int(log.get("transactionIndex", "0x0"), 16),
        "log_index": int(log.get("logIndex", "0x0"), 16),
        "collateral_asset": topic_address(topics[1]),
        "debt_asset": topic_address(topics[2]),
        "borrower": topic_address(topics[3]),
        "debt_repaid_units": debt,
        "collateral_received_units": collateral,
        "historical_liquidator": "0x" + f"{liquidator_word:040x}",
        "received_a_tokens": bool(receive_atoken),
        "historically_executed": True,
        "new_strategy_profit_verified": False,
    }

def pool_at_latest(rpc: RPC) -> str:
    if rpc.chain_id() != BASE_CHAIN_ID:
        raise RpcError("Expected Base mainnet")
    pool = result_address(rpc.eth_call(AAVE_PROVIDER, encode("getPool()"), "latest"))
    if int(pool, 16) == 0:
        raise RpcError("Aave provider returned zero Pool")
    code = rpc.call("eth_getCode", [pool, "latest"])
    if not isinstance(code, str) or len(code) < 4:
        raise RpcError("Aave Pool has no runtime code")
    return pool

def survey(rpc: RPC, *, head: int, span: int, chunk: int = 256,
           max_logs: int = MAX_LOGS, windows: int | None = None) -> dict:
    """Audit explicit windows. Widely sampled history != full-period coverage."""
    if not (1 <= span <= 10000000 and 1 <= chunk <= 2000 and 1 <= max_logs <= 1000):
        raise ValueError("Invalid bounded liquidation scan")
    pool = pool_at_latest(rpc)
    first = max(1, head - span + 1)
    interval_count = (head - first + 1 + chunk - 1) // chunk
    if windows is not None and not 1 <= windows <= 300:
        raise ValueError("Sample windows must be within 1..300")
    count = min(interval_count, windows) if windows is not None else interval_count
    if count == interval_count:
        indexes = list(range(interval_count))
    elif count == 1:
        indexes = [0]
    else:
        indexes = sorted(set(i*(interval_count-1)//(count-1) for i in range(count)))
    successes, failures, results, covered = 0, [], [], 0
    scanned_ranges = []
    rpc_failed_windows = 0
    consecutive_failures = 0
    for index in indexes:
        if len(results) >= max_logs:
            break
        upper = head - index*chunk
        lower = max(first, upper - chunk + 1)
        try:
            logs = rpc.call("eth_getLogs", [{
                "address": pool, "fromBlock": hex(lower), "toBlock": hex(upper),
                "topics": [TOPIC],
            }])
            if not isinstance(logs, list):
                raise RpcError("Non-list eth_getLogs response")
            successes += 1
            consecutive_failures = 0
            covered += upper - lower + 1
            scanned_ranges.append([lower,upper])
            for raw in logs:
                try:
                    decoded = decode_liquidation(raw, pool)
                    if not lower <= decoded["block"] <= upper:
                        raise ValueError("Provider supplied wrong block")
                    results.append(decoded)
                except (ValueError, TypeError, KeyError) as exc:
                    failures.append({"from": lower, "to": upper, "error": "Malformed event: " + str(exc)[:120]})
        except (RpcError, OSError, ValueError, TypeError) as exc:
            rpc_failed_windows += 1
            consecutive_failures += 1
            failures.append({"from": lower, "to": upper, "error": str(exc)[:160]})
            if consecutive_failures >= 3:
                break  # hard stop; do not hammer rate-limited or non-archive RPC
        time.sleep(0.18)
    results.sort(key=lambda r: (r["block"], r["transaction_index"], r["log_index"]), reverse=True)
    result = {
        "status": "HISTORICAL_LIQUIDATIONS_OBSERVED" if results else (
            "PARTIAL_COVERAGE_NO_EVENTS" if successes and failures else
            ("NO_EVENTS_IN_SAMPLED_WINDOWS" if successes else "RPC_UNAVAILABLE")),
        "pool": pool,
        "head_block": head,
        "requested_range": [first, head],
        "requested_span_blocks": head-first+1,
        "requested_windows": len(indexes),
        "windows_attempted": successes + rpc_failed_windows,
        "windows_skipped_after_consecutive_errors": len(indexes) - successes - rpc_failed_windows,
        "range_windows_successful": successes,
        "range_windows_failed": rpc_failed_windows,
        "event_records_invalid": len(failures) - rpc_failed_windows,
        "exact_blocks_covered": covered,
        "is_complete_period_coverage": covered == (head-first+1),
        "coverage_fraction": round(covered/(head-first+1), 6),
        "scanned_ranges": scanned_ranges,
        "events_found": len(results),
        "known_debt_assets": {label:sum(x["debt_asset"].lower() == asset for x in results)
                              for asset,(label,_) in ASSET_DECIMALS.items()},
        "failures": failures,
        "events": results[:max_logs],
        "interpretation": "Observed historical liquidation receipts only. Sampled windows may omit events in unsampled history. No counterfactual pre-liquidation fork, quote, liquidation bonus valuation or bot PnL is proven.",
        "no_wallet_no_broadcast": True,
    }
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path("validation/liquidations"))
    p.add_argument("--span", type=int, default=8192)
    p.add_argument("--windows", type=int, default=None, help="Sample windows across requested span; omit for complete contiguous coverage")
    p.add_argument("--chunk", type=int, default=256)
    p.add_argument("--max-logs", type=int, default=80)
    p.add_argument("--rpc-timeout", type=float, default=8)
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    attempts = []
    best = None
    for label,url in ENDPOINTS:
        try:
            rpc = RPC(url, timeout=args.rpc_timeout)
            head = rpc.block_number()
            report = survey(rpc, head=head, span=args.span, chunk=args.chunk,
                            max_logs=args.max_logs, windows=args.windows)
            report["endpoint"] = label
            if best is None or (report["events_found"],report["range_windows_successful"]) > (
                best["events_found"],best["range_windows_successful"]):
                best = report
            if report["events_found"] or report["range_windows_failed"] == 0:
                break
        except (RpcError, ValueError, OSError, TypeError) as exc:
            attempts.append({"endpoint":label, "error":str(exc)[:200]})
    report = best or {"status":"RPC_UNAVAILABLE","events_found":0,
                      "range_windows_successful":0,"range_windows_failed":0,"events":[]}
    report["endpoint_attempts"] = attempts
    report["observed_utc"] = datetime.now(timezone.utc).isoformat()
    (args.out/"liquidation-survey.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ("events","failures")},indent=2))
    for problem in report.get("failures", [])[:5]:
        print(json.dumps({"sample_failure":problem}))
    for event in report["events"][:8]:
        print(json.dumps({"block":event["block"],"debt_asset":event["debt_asset"],
            "collateral_asset":event["collateral_asset"],"debt_units":event["debt_repaid_units"],
            "tx_hash":event["transaction_hash"]}))
    return 0 if report["range_windows_successful"] else 2

if __name__ == "__main__":
    raise SystemExit(main())

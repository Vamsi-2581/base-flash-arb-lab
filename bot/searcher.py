"""Base USDC flash-loan candidate discovery via real V3 QuoterV2 eth_call.

Read-only: no private key, no signing, no broadcasts. Quotes are informative,
not guarantees of transaction-level arbitrage profitability.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
import itertools
import json
import os
from pathlib import Path
import sys
import time

from .abi import QUOTER_SIGNATURE, encode, result_address, result_uint
from .networks import BASE_CHAIN_ID, USDC, WETH, AAVE_PROVIDER, EXCHANGES, Exchange
from .rpc import RPC, RpcError

USDC_UNIT = 10**6
WEI = 10**18
D = Decimal


@dataclass(frozen=True)
class Venue:
    dex_id: int
    dex: str
    fee: int
    pool: str
    quoter: str


@dataclass(frozen=True)
class Candidate:
    status: str
    block: int
    observed_utc: str
    amount_usdc: int
    first_dex: int
    first_fee: int
    second_dex: int
    second_fee: int
    first_pool: str
    second_pool: str
    expected_weth: int
    expected_usdc: int
    flash_bps: int
    flash_fee_usdc: int
    estimated_gas_usdc: int
    estimated_net_usdc: int
    min_weth: int
    min_usdc: int
    min_profit_usdc: int
    deadline_seconds: int
    model_gas_units: int
    gas_price_wei: int

    def to_dict(self) -> dict:
        d = asdict(self)
        for f in ("amount_usdc", "expected_usdc", "flash_fee_usdc",
                  "estimated_gas_usdc", "estimated_net_usdc", "min_usdc",
                  "min_profit_usdc"):
            d[f + "_display"] = str(D(getattr(self, f)) / USDC_UNIT)
        return d


def ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def pool_at(rpc: RPC, venue: Exchange, fee: int, block_hex: str) -> Venue | None:
    raw = rpc.eth_call(venue.factory,
        encode("getPool(address,address,uint24)", USDC, WETH, fee), block_hex)
    address = result_address(raw)
    if int(address, 16) == 0:
        return None
    return Venue(venue.dex_id, venue.label, fee, address, venue.quoter)


def quoter_at(rpc: RPC, quoter: str, token_in: str, token_out: str,
              amount_in: int, fee: int, block_hex: str) -> int:
    raw = rpc.eth_call(quoter,
        encode(QUOTER_SIGNATURE, token_in, token_out, amount_in, fee, 0), block_hex)
    result = result_uint(raw)
    if result <= 0:
        raise RpcError("Zero quoted output")
    return result


def fetch_aave_pool_and_premium(rpc: RPC, block_hex: str) -> tuple[str, int]:
    pool = result_address(rpc.eth_call(AAVE_PROVIDER, encode("getPool()"), block_hex))
    if int(pool, 16) == 0:
        raise RpcError("Aave pool provider returned zero address")
    premium = result_uint(rpc.eth_call(pool, encode("FLASHLOAN_PREMIUM_TOTAL()"), block_hex))
    if not 0 <= premium <= 1000:
        raise RpcError("Unexpected Aave premium; fail closed")
    return pool, premium


def gas_usdc_estimate(rpc: RPC, block_hex: str, gas_price_wei: int,
                      gas_units: int, l1_buffer_usdc: int,
                      gas_multiplier_bps: int) -> int:
    # Conservative spot conversion to USDC using 0.1 WETH swap quote; NOT an oracle.
    # Choose a liquid Uniswap fee tier; at least one must exist.
    samples = []
    for fee in (500, 3000, 100):
        try:
            quoted = quoter_at(rpc, EXCHANGES[0].quoter, WETH, USDC,
                               10**17, fee, block_hex)
            samples.append(quoted)
        except (RpcError, ValueError):
            continue
    if not samples:
        raise RpcError("Cannot price native gas in USDC; fail closed")
    eth_gas_wei = gas_units * gas_price_wei
    # Out of 0.1 WETH quoted, convert to entire gas consumption in WETH.
    return ceil_div(eth_gas_wei * max(samples) * gas_multiplier_bps,
                    10**17 * 10000) + l1_buffer_usdc


def discover(rpc: RPC, *, amounts_usdc: list[int], slip_bps: int = 30,
             min_profit_usdc: int = 2*USDC_UNIT, gas_units: int = 850000,
             l1_buffer_usdc: int = 100000, gas_multiplier_bps: int = 15000,
             max_candidates: int = 10) -> dict:
    if rpc.chain_id() != BASE_CHAIN_ID:
        raise RpcError("RPC is not Base Mainnet (8453); refusing to quote")
    if not 0 <= slip_bps < 500 or min_profit_usdc <= 0 or gas_units <= 0:
        raise ValueError("Invalid strategy parameters")
    block = rpc.block_number()
    block_hex = hex(block)
    pool, premium_bps = fetch_aave_pool_and_premium(rpc, block_hex)
    gas_price = rpc.gas_price()
    estimated_gas = gas_usdc_estimate(rpc, block_hex, gas_price,
                      gas_units, l1_buffer_usdc, gas_multiplier_bps)
    venues: list[Venue] = []
    for dex in EXCHANGES:
        for fee in dex.fees:
            try:
                venue = pool_at(rpc, dex, fee, block_hex)
            except (RpcError, ValueError):
                venue = None
            if venue:
                venues.append(venue)
    if len(venues) < 2:
        raise RpcError("Fewer than two valid pools were found")
    ranked: list[Candidate] = []
    errors = 0
    observations = 0
    completed_quotes = 0
    positive_gross_pairs = 0
    positive_net_pairs = 0
    best_gross_usdc = None
    best_net_usdc = None
    cache: dict[tuple[str, str, int, int], int | None] = {}

    def key(v: Venue, token_in: str, amount: int) -> tuple[str, str, int, int]:
        return v.pool.lower(), token_in.lower(), v.fee, amount

    def prefetch(tasks: list[tuple[Venue, str, str, int]]) -> None:
        """Two-stage batched quoter reads instead of per-path serial roundtrips."""
        unique = {}
        for v, token_in, token_out, amount in tasks:
            k = key(v, token_in, amount)
            if k not in cache and k not in unique:
                unique[k] = (v, token_in, token_out, amount)
        if not unique:
            return
        requests = [(v.quoter, encode(QUOTER_SIGNATURE, tin, tout, n, v.fee, 0), block_hex)
                    for v, tin, tout, n in unique.values()]
        try:
            if not hasattr(rpc, "batch_eth_call"):
                raise RpcError("test RPC without batch")
            responses = rpc.batch_eth_call(requests)
        except RpcError:
            # Not all public RPCs support batching; retain slow read-only fallback.
            # The post-scan staleness gate rejects opportunities that expired.
            responses = []
            for to, calldata, tag in requests:
                try:
                    responses.append(rpc.eth_call(to, calldata, tag))
                except (RpcError, ValueError):
                    responses.append(None)
        for k, raw in zip(unique.keys(), responses):
            try:
                result = result_uint(raw) if raw is not None else 0
                cache[k] = result if result > 0 else None
            except ValueError:
                cache[k] = None

    def quote(v: Venue, token_in: str, token_out: str, amt: int) -> int:
        k = key(v, token_in, amt)
        if k not in cache:
            prefetch([(v, token_in, token_out, amt)])
        value = cache.get(k)
        if value is None:
            raise RpcError("V3 quote unavailable")
        return value

    # First and second legs depend on each other, but every independent quote
    # within one leg can be sent through JSON-RPC batches.
    prefetch([(v, USDC, WETH, n) for v in venues for n in amounts_usdc])
    second_jobs = []
    for first, second in itertools.permutations(venues, 2):
        if first.pool.lower() == second.pool.lower():
            continue
        for loan in amounts_usdc:
            first_quote = cache.get(key(first, USDC, loan))
            if first_quote is not None:
                second_jobs.append((second, WETH, USDC, first_quote))
    prefetch(second_jobs)

    for first, second in itertools.permutations(venues, 2):
        if first.pool.lower() == second.pool.lower():
            continue
        for loan in amounts_usdc:
            observations += 1
            try:
                weth_out = quote(first, USDC, WETH, loan)
                usdc_out = quote(second, WETH, USDC, weth_out)
            except (RpcError, ValueError):
                errors += 1
                continue
            flash_fee = ceil_div(loan * premium_bps, 10000)
            gross = usdc_out - loan - flash_fee  # DEX fees already included in QuoterV2 amounts
            net = gross - estimated_gas
            completed_quotes += 1
            positive_gross_pairs += int(gross > 0)
            positive_net_pairs += int(net > 0)
            best_gross_usdc = gross if best_gross_usdc is None else max(best_gross_usdc, gross)
            best_net_usdc = net if best_net_usdc is None else max(best_net_usdc, net)
            if net < min_profit_usdc:
                continue
            min_weth = weth_out * (10000 - slip_bps) // 10000
            if min_weth <= 0:
                continue
            try:
                # Stress the second leg with the first leg's slippage applied.
                stressed_return = quote(second, WETH, USDC, min_weth)
            except (RpcError, ValueError):
                errors += 1
                continue
            min_usdc = max(stressed_return * (10000 - slip_bps) // 10000,
                           loan + flash_fee + estimated_gas + min_profit_usdc)
            # Minimum on-chain profit must also cover the conservative Gas model.
            if min_usdc > usdc_out:
                continue
            ranked.append(Candidate(
                "QUOTED_NOT_FORK_VERIFIED", block, datetime.now(timezone.utc).isoformat(),
                loan, first.dex_id, first.fee, second.dex_id, second.fee,
                first.pool, second.pool, weth_out, usdc_out, premium_bps,
                flash_fee, estimated_gas, net, min_weth, min_usdc,
                min_profit_usdc, 60, gas_units, gas_price))
    ranked.sort(key=lambda c: c.estimated_net_usdc, reverse=True)
    # The quote itself can take multiple blocks over public/free HTTP RPC.
    latest_after_scan = rpc.block_number()
    return {"chain_id": BASE_CHAIN_ID, "block": block,
            "latest_block_after_scan": latest_after_scan,
            "stale_when_scan_finished": latest_after_scan - block > 2,
            "pool": pool,
            "premium_bps": premium_bps, "gas_price_wei": gas_price,
            "gas_model_usdc": estimated_gas, "pools_discovered": len(venues),
            "pairs_tested": observations, "quote_failures": errors,
            "quote_metrics": {
                "completed_quotes": completed_quotes,
                "positive_before_gas_pairs": positive_gross_pairs,
                "positive_after_modeled_gas_pairs": positive_net_pairs,
                "best_before_gas_usdc_units": best_gross_usdc,
                "best_after_modeled_gas_usdc_units": best_net_usdc,
                "fee_accounting": "DEX fees embedded in quote; subtract flash premium and modeled gas",
                "note": "Pinned-block QuoterV2 estimates; NOT realized returns or executable PnL",
            },
            "candidates": [x.to_dict() for x in ranked[:max_candidates]]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rpc", default=os.environ.get("BASE_RPC_URL", "https://mainnet.base.org"))
    p.add_argument("--amounts-usdc", default="100,500,1000,5000,10000,20000")
    p.add_argument("--min-profit-usdc", default="2")
    p.add_argument("--slippage-bps", type=int, default=30)
    p.add_argument("--gas-units", type=int, default=850000)
    p.add_argument("--l1-buffer-usdc", default="0.1")
    p.add_argument("--gas-multiplier-bps", type=int, default=15000)
    p.add_argument("--interval", type=float, default=0)
    p.add_argument("--output", type=Path, default=Path("candidates.jsonl"))
    args = p.parse_args(argv)
    if args.interval and args.interval < 15:
        p.error("--interval must be at least 15 seconds for free RPC")
    try:
        amounts = sorted({int(D(x) * USDC_UNIT) for x in args.amounts_usdc.split(",")})
        if not amounts or amounts[0] <= 0 or amounts[-1] > 1_000_000 * USDC_UNIT:
            raise ValueError("Invalid loan amount grid")
        min_profit = int(D(args.min_profit_usdc) * USDC_UNIT)
        l1_fee = int(D(args.l1_buffer_usdc) * USDC_UNIT)
        if min_profit <= 0 or l1_fee < 0:
            raise ValueError("Profit and L1 fee constraints invalid")
    except (ValueError, ArithmeticError) as e:
        p.error(str(e))
    rpc = RPC(args.rpc)
    try:
        while True:
            try:
                report = discover(rpc, amounts_usdc=amounts, slip_bps=args.slippage_bps,
                    min_profit_usdc=min_profit, gas_units=args.gas_units,
                    l1_buffer_usdc=l1_fee, gas_multiplier_bps=args.gas_multiplier_bps)
                print(json.dumps({k: v for k, v in report.items() if k != "candidates"}))
                if report["stale_when_scan_finished"]:
                    print("WARNING: data already stale by scan completion; not exporting actionable candidates")
                elif report["candidates"]:
                    with args.output.open("a", encoding="utf-8") as fp:
                        for item in report["candidates"]:
                            fp.write(json.dumps(item) + "\n")
                            print("CANDIDATE:", json.dumps(item))
                else:
                    print("No modeled positive opportunity (normal in efficient markets).")
            except (RpcError, OSError, ValueError) as e:
                print(f"Scan unavailable: {e}", file=sys.stderr)
                if not args.interval:
                    return 2
            if not args.interval:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

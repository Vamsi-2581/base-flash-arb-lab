"""Pinned-block multi-token, multi-DEX Base arbitrage research (NO TRADING).

Discovery-only USDC -> asset -> USDC. Real QuoterV2 or Aerodrome
Pool.getAmountOut is queried at the identical block tag. Any output is a
counterfactual quote, NOT realized PnL, not proof of executable flash loans,
and not eligible for the existing FlashArb executor.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
import argparse
import itertools
import json
import math
from pathlib import Path
import time

from .abi import encode, result_address, result_uint
from .networks import BASE_CHAIN_ID, EXCHANGES, USDC, WETH
from .rpc import RPC, RpcError
from .searcher import ceil_div, fetch_aave_pool_and_premium, gas_usdc_estimate, quoter_at

USDC_UNIT = 1_000_000
AERO_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
ZERO = "0x" + "0" * 40

@dataclass(frozen=True)
class Asset:
    symbol: str
    address: str
    decimals: int

ASSETS = {
    "WETH": Asset("WETH", WETH, 18),
    "cbBTC": Asset("cbBTC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),
    "cbETH": Asset("cbETH", "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", 18),
    "DAI": Asset("DAI", "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", 18),
    "EURC": Asset("EURC", "0x60a3E35Cc302bFA44Cb288bc5a4F316Fdb1adb42", 6),
    "USDbC": Asset("USDbC", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", 6),
}

@dataclass(frozen=True)
class Venue:
    name: str
    kind: str  # 'v3' or 'aerodrome'; only 'v3' supports QuoterV2
    pool: str
    fee_or_stable: int
    quoter: str
    dex_id: int

def parse_assets(spec: str) -> list[Asset]:
    names = [x.strip() for x in spec.split(",") if x.strip()]
    if not names or len(names) != len(set(names)):
        raise ValueError("Nonempty unique asset symbols required")
    unknown = [x for x in names if x not in ASSETS]
    if unknown:
        raise ValueError(f"Unsupported tokens: {unknown}")
    return [ASSETS[x] for x in names]

def discover_venues(rpc, asset: Asset, tag: str) -> tuple[list[Venue], list[str]]:
    found: list[Venue] = []
    errors: list[str] = []
    for dex in EXCHANGES:
        for fee in dex.fees:
            try:
                address = result_address(rpc.eth_call(dex.factory,
                    encode("getPool(address,address,uint24)", USDC, asset.address, fee), tag))
                if int(address, 16):
                    found.append(Venue(dex.label, "v3", address, fee, dex.quoter, dex.dex_id))
            except (RpcError, ValueError) as e:
                errors.append(f"{dex.label}/{fee}: {type(e).__name__}")
    # Aerodrome's classic (not Slipstream) pools use two distinct fee curves,
    # selected via bool stable. The pool itself computes the exact quote.
    for stable in (0, 1):
        try:
            address = result_address(rpc.eth_call(AERO_FACTORY,
                encode("getPool(address,address,bool)", USDC, asset.address, stable), tag))
            if int(address, 16):
                found.append(Venue("AerodromeStable" if stable else "AerodromeVolatile",
                                   "aerodrome", address, stable, "", 2))
        except (RpcError, ValueError) as e:
            errors.append(f"Aerodrome/{stable}: {type(e).__name__}")
    return found, errors

def quote_at(rpc, venue: Venue, token_in: str, token_out: str, amount: int, tag: str) -> int:
    if amount <= 0:
        raise ValueError("Quote amount must be positive")
    if venue.kind == "v3":
        return quoter_at(rpc, venue.quoter, token_in, token_out, amount,
                         venue.fee_or_stable, tag)
    if venue.kind == "aerodrome":
        out = result_uint(rpc.eth_call(venue.pool,
            encode("getAmountOut(uint256,address)", amount, token_in), tag))
        if out <= 0:
            raise RpcError("Aerodrome returned zero quote")
        return out
    raise ValueError("Unknown exchange adapter")

def balanced_routes(venues: list[Venue], max_routes: int) -> list[tuple[Venue, Venue]]:
    """Round-robin across directed DEX pairs, avoiding factory insertion bias.

    Route-cap does not silently exclude V3->V3 because Aerodrome was first
    in the venue list. Does not claim globally optimal path coverage.
    """
    groups: dict[tuple[int,int], list[tuple[Venue,Venue]]] = {}
    for first,second in itertools.permutations(venues,2):
        if first.pool.lower()==second.pool.lower():
            continue
        groups.setdefault((first.dex_id,second.dex_id),[]).append((first,second))
    keys=sorted(groups)
    selected=[]
    while len(selected)<max_routes and any(groups[k] for k in keys):
        for key in keys:
            if groups[key] and len(selected)<max_routes:
                selected.append(groups[key].pop(0))
    return selected

def refine_amounts(amounts: list[int], peak_index: int) -> list[int]:
    """Adaptive logarithmic midpoint probing around coarse winner.

    Not a mathematical global optimum: route profit may not be unimodal.
    """
    values = sorted(set(int(x) for x in amounts if x > 0))
    if not values or not 0 <= peak_index < len(values):
        raise ValueError("Invalid coarse amount/index")
    points = []
    if peak_index > 0:
        lo, hi = values[peak_index-1], values[peak_index]
        midpoint = math.isqrt(lo * hi)
        if lo < midpoint < hi:
            points.append(midpoint)
    if peak_index + 1 < len(values):
        lo, hi = values[peak_index], values[peak_index+1]
        midpoint = math.isqrt(lo * hi)
        if lo < midpoint < hi:
            points.append(midpoint)
    return points

def promote_legacy_fork_candidate(rpc, row: dict, *,
                                  flash_bps: int, gas_price_wei: int,
                                  slippage_bps: int = 5,
                                  min_profit_usdc: int = USDC_UNIT,
                                  gas_units: int = 950000) -> dict | None:
    """Only USDC/WETH routes already implemented by FlashArb can be replayed.

    This generates a STRICT unverified candidate, never marks it profitable.
    Other assets or Aerodrome are deliberately ineligible.
    """
    a, b = row["first"], row["second"]
    if (row.get("asset") != "WETH" or a["kind"] != "v3" or b["kind"] != "v3"
        or a["dex_id"] not in (0,1) or b["dex_id"] not in (0,1)
        or a["pool"].lower() == b["pool"].lower()):
        return None
    size = int(row["amount_usdc_units"])
    mid = int(row["intermediate_units"])
    expected = int(row["received_usdc_units"])
    gas = int(row["modeled_gas_usdc_units"])
    flash = ceil_div(size*flash_bps, 10000)
    if expected - size - flash - gas <= min_profit_usdc:
        return None
    min_weth = mid*(10000-slippage_bps)//10000
    if min_weth <= 0:
        return None
    second = Venue(**b)
    try:
        stressed = quote_at(rpc,second,WETH,USDC,min_weth,hex(int(row["block"])))
    except (RpcError,ValueError,OSError):
        return None
    stressed_min = stressed*(10000-slippage_bps)//10000
    economic_floor = size+flash+gas+min_profit_usdc
    # Crucial: never make a stressed loss scenario look safe by simply
    # raising minOut. That would just force a revert on adverse execution.
    if stressed_min < economic_floor or stressed_min > expected:
        return None
    min_usdc = stressed_min
    return {
        "status":"QUOTED_NOT_FORK_VERIFIED",
        "historical_backtest":True, "block":int(row["block"]),
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "amount_usdc":size, "first_dex":int(a["dex_id"]),
        "first_fee":int(a["fee_or_stable"]), "second_dex":int(b["dex_id"]),
        "second_fee":int(b["fee_or_stable"]),
        "first_pool":a["pool"],"second_pool":b["pool"],
        "expected_weth":mid,"expected_usdc":expected,
        "flash_bps":flash_bps,"flash_fee_usdc":flash,
        "estimated_gas_usdc":gas,
        "estimated_net_usdc":expected-size-flash-gas,
        "min_weth":min_weth,"min_usdc":min_usdc,
        "min_profit_usdc":min_profit_usdc,"deadline_seconds":60,
        "model_gas_units":gas_units,"gas_price_wei":gas_price_wei,
        "warning":"Must PASS full Aave+real-router fork execution; quote profit not verified.",
    }

def research_snapshot(rpc, block: int, assets: list[Asset],
                      amounts: list[int], *, max_seconds: float = 100,
                      max_venues: int = 7, max_routes: int = 20,
                      refine_top: int = 3, min_net_usdc: int = 0) -> dict:
    if rpc.chain_id() != BASE_CHAIN_ID:
        raise RpcError("Unexpected chain ID, expected Base 8453")
    if block <= 0 or not amounts or min(amounts) <= 0 or not assets:
        raise ValueError("Invalid block, amounts or tokens")
    if max_seconds <= 0 or not 1 <= max_venues <= 12 or not 1 <= max_routes <= 100:
        raise ValueError("Invalid bounded research settings")
    start = time.monotonic()
    tag = hex(block)
    pool, flash_bps = fetch_aave_pool_and_premium(rpc, tag)
    # Use conservative USDC gas model: this is NOT exact Base L1 DA fee.
    price = rpc.gas_price()
    gas = gas_usdc_estimate(rpc, tag, price, 950000, 120000, 15000)
    cache: dict[tuple[str, str, int], int | None] = {}
    errors: list[str] = []
    overall: list[dict] = []
    by_asset: list[dict] = []
    quotes_ok = 0
    quotes_failed = 0
    for asset in assets:
        if time.monotonic() - start >= max_seconds:
            by_asset.append({"asset": asset.symbol, "status": "TIME_BUDGET_EXCEEDED"})
            continue
        venues, discovery_errors = discover_venues(rpc, asset, tag)
        errors.extend(f"{asset.symbol}: {x}" for x in discovery_errors)
        if len(venues) > max_venues:
            # Distinct venues spread across DEX types before capacity clipping.
            venues.sort(key=lambda v: (v.kind != "aerodrome", v.dex_id, v.fee_or_stable))
            venues = venues[:max_venues]
        if len(venues) < 2:
            by_asset.append({"asset": asset.symbol, "status": "INSUFFICIENT_POOLS",
                             "venues": [v.name for v in venues]})
            continue
        pool_key = lambda v: v.pool.lower()
        routes = balanced_routes(venues,max_routes)
        rows: list[dict] = []
        best_net = None
        asset_tested = asset_quoted = 0
        def one_quote(v, token_in, token_out, amount):
            nonlocal quotes_failed, quotes_ok
            k = (pool_key(v), token_in.lower(), amount)
            if k not in cache:
                try:
                    cache[k] = quote_at(rpc, v, token_in, token_out, amount, tag)
                    quotes_ok += 1
                except (RpcError, ValueError, OSError):
                    cache[k] = None
                    quotes_failed += 1
            if cache[k] is None:
                raise RpcError("Unquotable route")
            return cache[k]
        def calc(a: Venue, b: Venue, size: int) -> dict | None:
            nonlocal asset_tested, asset_quoted, best_net
            if time.monotonic()-start >= max_seconds:
                return None
            asset_tested += 1
            try:
                mid = one_quote(a, USDC, asset.address, size)
                output = one_quote(b, asset.address, USDC, mid)
            except RpcError:
                return None
            asset_quoted += 1
            flash_fee = ceil_div(size * flash_bps, 10000)
            gross = output - size - flash_fee
            net = gross - gas
            best_net = net if best_net is None else max(best_net, net)
            return {"amount_usdc_units": size, "intermediate_units": mid,
                    "received_usdc_units": output, "flash_fee_usdc_units": flash_fee,
                    "gross_after_flash_usdc_units": gross,
                    "modeled_gas_usdc_units": gas, "modeled_net_usdc_units": net,
                    "asset": asset.symbol, "block": block,
                    "first": asdict(a), "second": asdict(b),
                    "execution_status": "RESEARCH_QUOTE_NOT_FORK_EXECUTED",
                    "can_use_existing_executor": asset.symbol == "WETH" and
                        a.dex_id in (0,1) and b.dex_id in (0,1)}
        winners: list[tuple[int, Venue, Venue, int, list[int]]] = []
        for a,b in routes:
            evaluated = []
            for amount in sorted(set(amounts)):
                result = calc(a,b,amount)
                if result is not None:
                    evaluated.append(result)
                    if result["modeled_net_usdc_units"] > min_net_usdc:
                        rows.append(result)
            if evaluated:
                chosen = max(evaluated,key=lambda r:r["modeled_net_usdc_units"])
                idx = sorted(set(amounts)).index(chosen["amount_usdc_units"])
                winners.append((chosen["modeled_net_usdc_units"],a,b,idx,
                                sorted(set(amounts))))
            if time.monotonic()-start >= max_seconds:
                break
        best_coarse_net = best_net
        # Spend extra RPC calls only on promising routes, not every path.
        additional = 0
        for _,a,b,idx,grid in sorted(winners,key=lambda x:x[0],reverse=True)[:refine_top]:
            for amount in refine_amounts(grid,idx):
                result = calc(a,b,amount)
                additional += 1
                if result is not None and result["modeled_net_usdc_units"] > min_net_usdc:
                    rows.append(result)
        rows.sort(key=lambda r:r["modeled_net_usdc_units"],reverse=True)
        # Keep top quotes globally, plus execution-compatible WETH routes
        # that Aerodrome quote edges might otherwise displace.
        selected = rows[:5]
        for row in rows:
            if row["can_use_existing_executor"] and row not in selected:
                selected.append(row)
                if sum(bool(x["can_use_existing_executor"]) for x in selected) >= 5:
                    break
        overall.extend(selected)
        by_asset.append({
            "asset": asset.symbol, "status": "PARTIAL" if asset_tested <
                len(routes)*len(set(amounts)) else "QUOTED",
            "discovered_venues": [asdict(v) for v in venues],
            "coarse_route_count": len(routes), "quote_paths_attempted": asset_tested,
            "full_two_leg_quotes": asset_quoted,
            "adaptive_extra_amount_evaluations": additional,
            "best_modeled_net_usdc_units": best_net,
            "best_coarse_net_usdc_units": best_coarse_net,
            "refinement_improvement_usdc_units": (best_net - best_coarse_net if best_net is not None and best_coarse_net is not None else None),
            "route_directions_sampled": sorted(set(f"{v.name}->{w.name}" for v,w in routes)),
            "positive_research_quotes": len(rows),
            "warning": "Research only; ERC20+router variants not supported by production FlashArb."
        })
    overall.sort(key=lambda r:r["modeled_net_usdc_units"],reverse=True)
    legacy_fork_candidates = []
    for row in overall:
        if not row["can_use_existing_executor"]:
            continue
        candidate = promote_legacy_fork_candidate(rpc,row,flash_bps=flash_bps,
                       gas_price_wei=price)
        if candidate:
            legacy_fork_candidates.append(candidate)
        if len(legacy_fork_candidates) >= 3:
            break
    elapsed = round(time.monotonic()-start,2)
    return {
        "status": "PINNED_BLOCK_RESEARCH_QUOTES_ONLY",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "chain_id":BASE_CHAIN_ID, "block":block,
        "aave_pool":pool, "flash_bps":flash_bps,
        "modeled_gas_usdc_units":gas, "quote_contract_calls_ok":quotes_ok,
        "quote_contract_calls_failed":quotes_failed,
        "total_full_two_leg_quotes":sum(r.get("full_two_leg_quotes",0) for r in by_asset),
        "asset_summaries":by_asset,"positive_quote_rows":overall[:12],
        "legacy_fork_candidates":legacy_fork_candidates,
        "wall_seconds":elapsed, "warnings":errors[:25],
        "verified_profitable_forks":0,
        "interpretation":"Counterfactual end-of-block pricing. Not an intra-block replay, not a transaction receipt. All new assets/Aerodrome are research-only."
    }

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rpc",default="https://mainnet.base.org")
    p.add_argument("--block",type=int,default=0,help="Exact historical block, 0 means latest")
    p.add_argument("--assets",default="WETH,cbBTC,DAI,EURC")
    p.add_argument("--amounts",default="200,1000,5000,20000")
    p.add_argument("--max-seconds",type=float,default=170)
    p.add_argument("--max-routes",type=int,default=16)
    p.add_argument("--refine-top",type=int,default=3)
    p.add_argument("--output",type=Path,default=Path("validation/extended/snapshot.json"))
    a=p.parse_args(argv)
    amounts=sorted(set(int(Decimal(x)*USDC_UNIT) for x in a.amounts.split(",")))
    rpc=RPC(a.rpc,timeout=9)
    block=a.block or rpc.block_number()
    report=research_snapshot(rpc,block,parse_assets(a.assets),amounts,
        max_seconds=a.max_seconds,max_routes=a.max_routes,refine_top=a.refine_top)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in
       ("positive_quote_rows","asset_summaries")},indent=2))
    for item in report["asset_summaries"]:
        print(json.dumps({k:v for k,v in item.items() if k!="discovered_venues"}))
    print(f"RESEARCH_QUOTE_ROWS={len(report['positive_quote_rows'])}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())

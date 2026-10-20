"""Event-level historical V3 price dispersion: RESEARCH SIGNALS ONLY.

Reconstructs pool spot prices after each transaction's Swap events, using
prev-block slot0 and per-swap sqrtPriceX96 emitted by real pool events.

This is *not* a full EVM state reconstruction: liquidity changes, hooks,
router reserves, intermediate transaction interactions and tick state are
not replayed. Gross spot spreads do not prove an executable arbitrage.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, getcontext
import json
from pathlib import Path
import time
from bot.abi import encode, keccak256, result_address, result_uint
from bot.networks import EXCHANGES, USDC, WETH
from bot.rpc import RPC, RpcError

getcontext().prec = 74
Q192 = 1 << 192
SWAP_TOPIC = "0x" + keccak256(
    b"Swap(address,address,int256,int256,uint160,uint128,int24)"
).hex()

ENDPOINTS = (
    ("base_official", "https://mainnet.base.org"),
    ("publicnode", "https://base-rpc.publicnode.com"),
    ("drpc", "https://base.drpc.org"),
)

def u256_word(data: str, idx: int) -> int:
    raw = data.removeprefix("0x")
    start = idx * 64
    if len(raw) < start + 64:
        raise ValueError("Truncated event ABI words")
    return int(raw[start:start+64], 16)

def decode_swap_price(data: str) -> tuple[int, int]:
    """Swap ABI: int256 amount0, amount1, uint160 sqrtP, uint128 liquidity, int24 tick."""
    sqrt = u256_word(data, 2)
    liquidity = u256_word(data, 3)
    if not 0 < sqrt < 1 << 160 or liquidity >= 1 << 128:
        raise ValueError("Malformed V3 swap sqrtPriceX96/liquidity")
    return sqrt, liquidity

def price_usdc_per_weth(sqrt_price_x96: int, token0: str) -> Decimal:
    if not 0 < sqrt_price_x96 < 1 << 160:
        raise ValueError("Invalid sqrt price")
    square = Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96)
    if token0.lower() == WETH.lower():
        # sqrtP^2 = raw USDC for one raw WETH; scale 10^(18-6).
        return square * (Decimal(10) ** 12) / Decimal(Q192)
    if token0.lower() == USDC.lower():
        # sqrtP^2 = raw WETH for one raw USDC; invert and scale 10^(6-18).
        return Decimal(Q192) * (Decimal(10) ** 12) / square
    raise ValueError("Unknown pool token order")

def implied_roundtrip_spread(
    prices: dict[str, Decimal], fees_ppm: dict[str, int],
    *, flash_premium_bps: int = 5, gas_usdc: Decimal = Decimal("0.15"),
    loan_usdc: Decimal = Decimal("1000")
) -> dict | None:
    """Best infinitesimal arbitrage midprice, NOT executable quote or slippage."""
    if len(prices) < 2 or loan_usdc <= 0:
        return None
    ordered = sorted(prices)
    best = None
    for buy in ordered:
        for sell in ordered:
            if buy == sell or prices[buy] <= 0 or prices[sell] <= 0:
                continue
            if not 0 <= fees_ppm[buy] < 1000000 or not 0 <= fees_ppm[sell] < 1000000:
                continue
            # USDC buy WETH at low quote, WETH sell to USDC at high quote.
            net_multiplier = prices[sell] / prices[buy]
            net_multiplier *= (Decimal(1) - Decimal(fees_ppm[buy])/Decimal(1000000))
            net_multiplier *= (Decimal(1) - Decimal(fees_ppm[sell])/Decimal(1000000))
            model = loan_usdc * (net_multiplier-1)
            model -= loan_usdc*Decimal(flash_premium_bps)/Decimal(10000)
            model -= gas_usdc
            if best is None or model > best["spot_model_usdc"]:
                best = {"buy_pool":buy,"sell_pool":sell,
                        "buy_price":str(prices[buy]),"sell_price":str(prices[sell]),
                        "spot_model_usdc":model,
                        "fees_buy_ppm":fees_ppm[buy],"fees_sell_ppm":fees_ppm[sell]}
    if best:
        buy, sell = best["buy_pool"], best["sell_pool"]
        fee_multiplier = ((Decimal(1) - Decimal(fees_ppm[buy]) / Decimal(1000000)) *
                          (Decimal(1) - Decimal(fees_ppm[sell]) / Decimal(1000000)))
        spread = (prices[sell] / prices[buy] - Decimal(1)) * Decimal(10000)
        needed_ratio = (Decimal(1) + Decimal(flash_premium_bps) / Decimal(10000) +
                        gas_usdc / loan_usdc) / fee_multiplier
        hurdle = (needed_ratio - Decimal(1)) * Decimal(10000)
        best["pre_fee_spread_bps"] = str(spread)
        best["break_even_required_spread_bps"] = str(hurdle)
        best["excess_spread_bps"] = str(spread - hurdle)
        best["spot_model_usdc"] = str(best["spot_model_usdc"])
    return best

def discover_pools(rpc: RPC, block: int, *, max_pools: int = 8) -> list[dict]:
    """Use ~two JSON-RPC batches, not 30 serial historical calls."""
    tag = hex(block)
    discovery = [(ex.label,fee,ex.factory,encode("getPool(address,address,uint24)",USDC,WETH,fee))
                 for ex in EXCHANGES for fee in ex.fees]
    def batch_or_serial(items):
        requests=[(target,calldata,tag) for target,calldata in items]
        try:
            return rpc.batch_eth_call(requests,chunk_size=40)
        except (RpcError,ValueError,AttributeError):
            outputs=[]
            for to,data,at in requests:
                try: outputs.append(rpc.eth_call(to,data,at))
                except (RpcError,ValueError,OSError): outputs.append(None)
            return outputs
    raw_pools = batch_or_serial([(row[2],row[3]) for row in discovery])
    found=[]
    for (label,fee,_,_),raw in zip(discovery,raw_pools):
        if not raw:continue
        try:
            address=result_address(raw)
            if int(address,16):
                found.append((label,fee,address))
        except (TypeError,ValueError):
            continue
    if len(found)<2:
        raise RpcError(f"Only {len(found)} pool factory responses at historical block {block}; try another RPC")
    # Query token0, token1, slot0 on the same block in one second batch.
    requests=[]
    for _label,_fee,address in found:
        requests += [(address,encode("token0()")),(address,encode("token1()")),(address,encode("slot0()"))]
    raws=batch_or_serial(requests)
    pools=[]
    for i,(label,fee,address) in enumerate(found):
        token0raw, token1raw, slotraw=raws[i*3:i*3+3]
        if not token0raw or not token1raw or not slotraw:
            continue
        try:
            token0=result_address(token0raw)
            token1=result_address(token1raw)
            if {token0.lower(),token1.lower()} != {USDC.lower(),WETH.lower()}:
                continue
            sqrt=result_uint(slotraw)
            spot=price_usdc_per_weth(sqrt,token0)
            pools.append({"pool":address.lower(),"dex":label,
                "token0":token0,"fee_ppm":fee,
                "before_sqrt_price_x96":sqrt,
                "before_price_usdc_per_weth":str(spot)})
        except (RpcError,ValueError,TypeError):
            continue
    if len(pools)<2:
        raise RpcError(f"Only {len(pools)}/{len(found)} complete historical V3 pool snapshots at {block}")
    pools.sort(key=lambda x:(x["fee_ppm"],x["dex"]))
    return pools[:max_pools]

def analyze_swaps(pools: list[dict], swap_logs: list[dict], *,
                  flash_premium_bps: int = 5,
                  gas_usdc: Decimal = Decimal("0.15"),
                  loan_usdc: Decimal = Decimal("1000"),
                  max_signals: int = 30) -> dict:
    """Apply all Swap logs within same tx before evaluating; no mid-tx insertion."""
    meta = {p["pool"].lower():p for p in pools}
    prices = {p["pool"].lower():Decimal(p["before_price_usdc_per_weth"]) for p in pools}
    fees = {p["pool"].lower():int(p["fee_ppm"]) for p in pools}
    # Logs must have blockchain ordering; missing txnIndex is tracked.
    cleaned, ignored = [],0
    for log in swap_logs:
        try:
            pool = log["address"].lower()
            if pool not in meta or log.get("removed",False):
                ignored += 1;continue
            if not log.get("topics") or log["topics"][0].lower() != SWAP_TOPIC.lower():
                ignored += 1;continue
            sqrt, liquidity = decode_swap_price(log["data"])
            spot = price_usdc_per_weth(sqrt,meta[pool]["token0"])
            txhash = log["transactionHash"].lower()
            tx_index = int(log["transactionIndex"],16)
            log_index = int(log["logIndex"],16)
            cleaned.append((tx_index,log_index,txhash,pool,spot,liquidity))
        except (KeyError,TypeError,ValueError):
            ignored += 1
    cleaned.sort(key=lambda r:(r[0],r[1]))
    baseline = implied_roundtrip_spread(prices,fees,
                  flash_premium_bps=flash_premium_bps,
                  gas_usdc=gas_usdc,loan_usdc=loan_usdc)
    after_tx, signals = [],[]
    groups = defaultdict(list)
    for event in cleaned:
        groups[(event[0],event[2])].append(event)
    for (tx_index,txhash),events in sorted(groups.items()):
        for _,_,_,pool,spot,_liquidity in events:
            prices[pool] = spot
        best = implied_roundtrip_spread(prices,fees,
                   flash_premium_bps=flash_premium_bps,
                   gas_usdc=gas_usdc,loan_usdc=loan_usdc)
        best_rough = Decimal(best["spot_model_usdc"]) if best else Decimal("-Infinity")
        row={"transaction_index":tx_index,"transaction_hash":txhash,
             "last_log_index":max(e[1] for e in events),
             "updated_pools":sorted(set(e[3] for e in events)),
             "spot_model_usdc":str(best_rough),
             "pre_fee_spread_bps":best.get("pre_fee_spread_bps") if best else None,
             "break_even_required_spread_bps":best.get("break_even_required_spread_bps") if best else None,
             "excess_spread_bps":best.get("excess_spread_bps") if best else None,
             "preliminary_positive":best_rough>0,
             "buy_pool":best["buy_pool"] if best else None,
             "sell_pool":best["sell_pool"] if best else None,
             "NOT_EXECUTABLE":True}
        after_tx.append(row)
        if best_rough > 0:
            signals.append(row)
    signals.sort(key=lambda x:Decimal(x["spot_model_usdc"]),reverse=True)
    return {
        "baseline_best_spot_model_usdc":baseline["spot_model_usdc"] if baseline else None,
        "total_swap_logs_applied":len(cleaned),"ignored_logs":ignored,
        "distinct_transactions_with_tracked_swaps":len(groups),
        "positive_spot_mispricing_transaction_boundaries":len(signals),
        "best_transaction_boundary_spot_model_usdc":str(max(
            (Decimal(x["spot_model_usdc"]) for x in after_tx),default=Decimal("-Infinity")))
            if after_tx else None,
        "best_transaction_boundary_excess_spread_bps":str(max(
            (Decimal(x["excess_spread_bps"]) for x in after_tx if x["excess_spread_bps"] is not None),
            default=Decimal("-Infinity"))) if after_tx else None,
        "signals":signals[:max_signals],
        "transaction_boundary_snapshots":after_tx[:150],
        "methodology":"B-1 pool slot0; apply V3 post-Swap sqrtPriceX96 in log order, assess only AFTER each whole transaction",
        "limitations":"Pool prices only; no storage/liquidity/tick/other-call replay. Signal cannot be labeled flash-loan executable or profitable."
    }

def probe_block(rpc: RPC, block: int, *, loan_usdc: Decimal, max_pools: int=8,
                max_signals: int=30) -> dict:
    if block <= 1:
        raise ValueError("Block should be greater than one")
    pools = discover_pools(rpc,block-1,max_pools=max_pools)
    if len(pools) < 2:
        raise RpcError("Not enough valid pools to screen spot divergence")
    addresses=[p["pool"] for p in pools]
    logs=rpc.call("eth_getLogs",[{"address":addresses,
            "topics":[SWAP_TOPIC],"fromBlock":hex(block),"toBlock":hex(block)}])
    if not isinstance(logs,list):
        raise RpcError("Invalid log response")
    if any(int(l.get("blockNumber","0x0"),16)!=block for l in logs):
        raise RpcError("Swap log response includes wrong block")
    result=analyze_swaps(pools,logs,loan_usdc=loan_usdc,max_signals=max_signals)
    result.update({"block":block,"status":"BLOCK_SWAP_SPOT_ANALYZED",
                   "pools":pools,"swap_log_count":len(logs),
                   "spreads_are":"spot-price upper-bound proxy only, NOT an executable net return"})
    return result

def run(args):
    output=Path(args.out)
    output.mkdir(parents=True,exist_ok=True)
    selector=json.loads(Path(args.events).read_text()) if Path(args.events).is_file() else {}
    selected=[int(x["block"]) for x in selector.get("selected",[])[:args.max_blocks]]
    if not selected:
        return {"status":"NO_HISTORICAL_EVENTS_SELECTED","blocks":[]}
    attempts,results=[],[]
    for block in selected:
        succeeded=False
        for label,url in ENDPOINTS:
            try:
                rpc=RPC(url,timeout=args.timeout)
                if rpc.chain_id()!=8453:raise RpcError("Wrong network")
                start=time.monotonic()
                row=probe_block(rpc,block,loan_usdc=Decimal(args.loan_usdc))
                row["endpoint"]=label
                row["seconds"]=round(time.monotonic()-start,2)
                results.append(row)
                succeeded=True
                break
            except (RpcError,ValueError,OSError,TypeError) as exc:
                attempts.append({"block":block,"endpoint":label,"error":str(exc)[:200]})
        if not succeeded:
            results.append({"block":block,"status":"ALL_PUBLIC_RPC_FAILED"})
        (output/"microstructure-progress.json").write_text(json.dumps(results,indent=2))
    summary={
        "status":"EVENT_MICROSTRUCTURE_SPOT_SIGNALS_ONLY",
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "blocks_requested":len(selected),
        "blocks_analyzed":sum(r["status"]=="BLOCK_SWAP_SPOT_ANALYZED" for r in results),
        "swap_logs":sum(r.get("total_swap_logs_applied",0) for r in results),
        "transaction_boundaries":sum(r.get("distinct_transactions_with_tracked_swaps",0) for r in results),
        "positive_spot_boundaries":sum(r.get("positive_spot_mispricing_transaction_boundaries",0) for r in results),
        "verified_flashloan_net_profit":0,
        "attempts":attempts,"blocks":results,
        "warning":"Only historical event-driven spot dispersion, no transaction-level storage replay, no actual PnL",
        "no_broadcast":True
    }
    (output/"microstructure-report.json").write_text(json.dumps(summary,indent=2))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events",default="validation/history/event-selection.json")
    p.add_argument("--out",default="validation/microstructure")
    p.add_argument("--max-blocks",type=int,default=3)
    p.add_argument("--loan-usdc",default="1000")
    p.add_argument("--timeout",type=float,default=9)
    a=p.parse_args()
    if not 1 <= a.max_blocks <= 12 or Decimal(a.loan_usdc)<=0:
        p.error("Invalid max blocks or loan size")
    report=run(a)
    print(json.dumps({k:v for k,v in report.items() if k not in ("blocks","attempts")},indent=2))
    for row in report.get("blocks",[]):
        print(json.dumps({"block":row["block"],"status":row["status"],
            "logs":row.get("total_swap_logs_applied"),"boundaries":row.get("distinct_transactions_with_tracked_swaps"),
            "spot_positive":row.get("positive_spot_mispricing_transaction_boundaries"),
            "best_spot_proxy_usdc":row.get("best_transaction_boundary_spot_model_usdc"),
            "best_spread_headroom_bps":row.get("best_transaction_boundary_excess_spread_bps"),
            "best_signals":row.get("signals",[])[:2]}))
    return 0 if report.get("blocks_analyzed",0)>0 else 2
if __name__=="__main__":
    raise SystemExit(main())

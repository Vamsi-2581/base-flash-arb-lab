"""Read-only pinned-block Base V3 triangular arbitrage counterfactual backtest.

Paths: USDC -> token A -> token B -> USDC using real Uniswap/Pancake
V3 pools/QuoterV2. No three-hop execution contract exists in this repo,
so a quoted positive is never a verified or runnable trading candidate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import itertools
import json
from pathlib import Path
import time

from bot.abi import encode, result_address, result_uint, QUOTER_SIGNATURE
from bot.extended_research import ASSETS
from bot.historical import HistoricalRPC, ENDPOINTS
from bot.networks import USDC, EXCHANGES
from bot.searcher import fetch_aave_pool_and_premium, gas_usdc_estimate, ceil_div
from bot.rpc import RPC, RpcError

U=10**6

def pairs(spec: str) -> list[tuple[str,str]]:
    out=[]
    for item in spec.split(";"):
        names=[x.strip() for x in item.split(",")]
        if len(names)!=2 or names[0]==names[1] or any(x not in ASSETS for x in names):
            raise ValueError("Use semicolon-separated distinct supported asset pairs")
        if tuple(names) not in out:
            out.append(tuple(names))
    return out

def pools_for_link(rpc, tag: str, token0: str, token1: str, venue_limit: int = 3):
    """Discover cheapest fee-tier available per exchange and cap paths fairly."""
    jobs=[(dex,fee) for dex in EXCHANGES for fee in dex.fees]
    req=[(dex.factory,encode("getPool(address,address,uint24)",token0,token1,fee),tag)
         for dex,fee in jobs]
    try:
        raw=rpc.batch_eth_call(req,chunk_size=40)
    except (RpcError,ValueError,AttributeError):
        raw=[]
        for target,calldata,t in req:
            try:raw.append(rpc.eth_call(target,calldata,t))
            except (RpcError,ValueError,OSError):raw.append(None)
    found=[]
    for (dex,fee),value in zip(jobs,raw):
        if not value:continue
        try:
            address=result_address(value)
            if int(address,16):
                found.append({"pool":address,"dex":dex.label,"dex_id":dex.dex_id,
                    "fee":fee,"quoter":dex.quoter})
        except (TypeError,ValueError):
            pass
    # Preserve at least one venue per exchange to reduce one-DEX bias.
    found.sort(key=lambda row:(row["fee"],row["dex_id"]))
    per_dex={}
    for item in found:
        per_dex.setdefault(item["dex_id"],[]).append(item)
    selected=[]
    while len(selected)<venue_limit and any(per_dex.values()):
        for key in sorted(per_dex):
            if per_dex[key] and len(selected)<venue_limit:
                selected.append(per_dex[key].pop(0))
    return selected

class QuoteCache:
    def __init__(self, rpc, tag):
        self.rpc=rpc; self.tag=tag
        self.data={};self.attempts=0;self.success=0;self.batch_errors=0
    def get_many(self, jobs):
        unique={}
        for pool,token_in,token_out,amount in jobs:
            key=(pool["pool"].lower(),token_in.lower(),amount)
            if key not in self.data and key not in unique:
                unique[key]=(pool,token_in,token_out,amount)
        if unique:
            req=[(pool["quoter"],
                 encode(QUOTER_SIGNATURE,tin,tout,amount,pool["fee"],0),
                 self.tag) for pool,tin,tout,amount in unique.values()]
            self.attempts+=len(req)
            try:
                raw=self.rpc.batch_eth_call(req,chunk_size=40)
            except (RpcError,ValueError,AttributeError):
                self.batch_errors+=1
                raw=[]
                for to,calldata,block in req:
                    try:raw.append(self.rpc.eth_call(to,calldata,block))
                    except (RpcError,ValueError,OSError):raw.append(None)
            for key,item in zip(unique,raw):
                try:
                    n=result_uint(item) if item else 0
                    self.data[key]=n if n>0 else None
                    if n>0:self.success+=1
                except (TypeError,ValueError):self.data[key]=None
        return [self.data.get((p["pool"].lower(),i.lower(),n))
                for p,i,_o,n in jobs]

def scan_one(rpc,block:int,asset_a:str,asset_b:str,amounts:list[int],
             max_seconds:float=90.0,venue_limit:int=3):
    tag=hex(block);begin=time.monotonic()
    a,b=ASSETS[asset_a].address,ASSETS[asset_b].address
    legs=[
        pools_for_link(rpc,tag,USDC,a,venue_limit),
        pools_for_link(rpc,tag,a,b,venue_limit),
        pools_for_link(rpc,tag,b,USDC,venue_limit)]
    if any(not link for link in legs):
        return {"status":"MISSING_TRIANGLE_EDGE","assets":[asset_a,asset_b],
                "pool_counts":[len(x) for x in legs],"quoted_paths":0}
    pool,premium=fetch_aave_pool_and_premium(rpc,tag)
    gas=gas_usdc_estimate(rpc,tag,rpc.gas_price(),1300000,150000,15000)
    cache=QuoteCache(rpc,tag)
    paths=list(itertools.product(*legs))
    # Stage by stage batching to minimize RPC roundtrips.
    stage1=[(route[0],USDC,a,loan) for route in paths for loan in amounts]
    out1=cache.get_many(stage1)
    stage2_jobs=[(route[1],a,b,mid) for (route,loan),mid in
                 zip(((route,loan) for route in paths for loan in amounts),out1)
                 if mid is not None]
    # Multi-stage results indexed by (route,loan) so missing quotes are not shifted.
    pairs1=[(route,loan,mid) for (route,loan),mid in
            zip(((route,loan) for route in paths for loan in amounts),out1)
            if mid is not None]
    out2=cache.get_many(stage2_jobs)
    pairs2=[(route,loan,mid,mid2) for (route,loan,mid),mid2 in zip(pairs1,out2)
            if mid2 is not None]
    out3=cache.get_many([(route[2],b,USDC,mid2) for route,_,_,mid2 in pairs2])
    best=None;complete=0;pos=0
    for (route,loan,mid,mid2),received in zip(pairs2,out3):
        if received is None:continue
        complete+=1
        flash=ceil_div(loan*premium,10000)
        gross=received-loan-flash
        net=gross-gas
        pos+=int(net>0)
        if best is None or net>best["modeled_net_usdc_units"]:
            best={
                "assets":[asset_a,asset_b],"loan_usdc_units":loan,
                "output_usdc_units":received,"flash_fee_usdc_units":flash,
                "three_swaps_and_loan_gross_usdc_units":gross,
                "modeled_gas_usdc_units":gas,"modeled_net_usdc_units":net,
                "pools":[x["pool"] for x in route],
                "dexes":[x["dex"] for x in route],
                "fee_tiers_ppm":[x["fee"] for x in route],
                "intermediate_units":[mid,mid2],
                "block":block,
                "status":"UNEXECUTED_THREE_HOP_RESEARCH_ONLY"
            }
    return {
        "status":"TRIANGLE_QUOTED" if complete>0 else "NO_COMPLETE_THREE_LEG_QUOTES",
        "assets":[asset_a,asset_b],
        "pool_counts":[len(x) for x in legs],
        "paths":len(paths),"loan_sizes":len(amounts),
        "quoted_paths":complete,"positive_after_modeled_gas":pos,
        "quote_contract_calls":cache.attempts,
        "successful_quote_calls":cache.success,
        "batch_fallbacks":cache.batch_errors,
        "aave_premium_bps":premium,
        "best":best,"elapsed_seconds":round(time.monotonic()-begin,2),
        "warning":"Not a valid two-swap executor route; no three-leg Fork proof or real PnL"
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,default=Path("validation/triangle"))
    p.add_argument("--pairs",default="WETH,cbETH;DAI,USDbC;WETH,DAI")
    p.add_argument("--amounts",default="200,1000,5000")
    p.add_argument("--lookbacks",default="64,1024")
    p.add_argument("--venue-limit",type=int,default=3)
    p.add_argument("--max-seconds",type=float,default=180)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    if not 1<=a.venue_limit<=8: p.error("venue-limit 1..8")
    combos=pairs(a.pairs)
    amounts=sorted(set(int(Decimal(x)*U) for x in a.amounts.split(",")))
    if not amounts or amounts[0]<=0:p.error("Positive amounts needed")
    lookbacks=[int(x) for x in a.lookbacks.split(",")]
    if any(x<1 for x in lookbacks):p.error("Positive lookbacks needed")
    started=time.monotonic();providers=[]
    for label,url in ENDPOINTS:
        try:
            rpc=RPC(url,timeout=8)
            if rpc.chain_id()==8453:providers.append((label,rpc))
        except (RpcError,OSError,ValueError):continue
    results=[];head=providers[0][1].block_number() if providers else 0
    for lag in lookbacks:
        block=head-lag
        for first,second in combos:
            if time.monotonic()-started > a.max_seconds:
                results.append({"status":"TIME_BUDGET_EXCEEDED","block":block,"assets":[first,second]})
                continue
            completed=False
            for label,rpc in providers:
                try:
                    snap=scan_one(HistoricalRPC(rpc,block),block,first,second,amounts,
                                  venue_limit=a.venue_limit)
                    if snap["status"] in ("MISSING_TRIANGLE_EDGE", "NO_COMPLETE_THREE_LEG_QUOTES"):
                        continue
                    snap["rpc"]=label
                    snap["block"]=block
                    results.append(snap);completed=True;break
                except (RpcError,OSError,ValueError) as exc:
                    last=str(exc)[:180]
            if not completed:
                results.append({"status":"UNAVAILABLE_OR_MISSING_POOLS","block":block,
                    "assets":[first,second],"error":locals().get("last","Missing 3-hop liquidity")})
            (a.out/"progress.json").write_text(json.dumps(results,indent=2))
    winners=[r["best"] for r in results if r.get("best")]
    report={"status":"HISTORICAL_THREE_HOP_QUOTES_ONLY",
            "timestamp_utc":datetime.now(timezone.utc).isoformat(),
            "blocks_requested":len(lookbacks),
            "strategies":len(combos),
            "strategies_quoted":sum(r["status"]=="TRIANGLE_QUOTED" for r in results),
            "quoted_paths":sum(r.get("quoted_paths",0) for r in results),
            "positive_model_routes":sum(r.get("positive_after_modeled_gas",0) for r in results),
            "best":max(winners,key=lambda x:x["modeled_net_usdc_units"]) if winners else None,
            "full_fork_verified_three_hop_profit":0,
            "execution_adapter_exists":False,
            "real_on_chain_profit":0,
            "results":results,
            "elapsed_seconds":round(time.monotonic()-started,2),
            "no_broadcast":True}
    (a.out/"triangle-report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="results"},indent=2))
    for r in results:
        print(json.dumps({k:r.get(k) for k in ("block","assets","status","paths","quoted_paths","positive_after_modeled_gas")}))
    return 0 if report["strategies_quoted"] else 2

if __name__=="__main__":
    raise SystemExit(main())

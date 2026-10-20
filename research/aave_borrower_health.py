"""Aave Base read-only borrower health monitor from recent Borrow logs.

Incomplete borrower sampling: new/active borrowers without a Borrow event in
the lookback period are omitted. A health factor near/below 1 is NOT proven
profitable liquidation or a guaranteed executable transaction.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path

from bot.abi import keccak256, encode, result_uint
from bot.historical import ENDPOINTS
from bot.rpc import RPC, RpcError
from research.aave_liquidation_survey import pool_at_latest, topic_address

TOPIC="0x"+keccak256(b"Borrow(address,address,address,uint256,uint8,uint256,uint16)").hex()
WAD=10**18

def extract_borrower(log:dict,pool:str)->str:
    topics=log.get("topics",[])
    if (log.get("removed",False) or log.get("address","").lower()!=pool.lower()
        or len(topics)!=4 or topics[0].lower()!=TOPIC.lower()):
        raise ValueError("Unexpected Borrow event")
    return topic_address(topics[2])

def decode_account_data(raw:str)->dict:
    if not isinstance(raw,str) or len(raw)<2+6*64:
        raise ValueError("Short Aave getUserAccountData")
    fields=[result_uint(raw,i) for i in range(6)]
    collateral,debt,available,threshold,ltv,hf=fields
    if threshold>10000 or ltv>10000:
        raise ValueError("Invalid threshold or LTV")
    return {"collateral_base_units":str(collateral),"debt_base_units":str(debt),
            "health_factor_wad":str(hf),
            "health_factor":str(round(hf/WAD,8)) if debt else None,
            "currently_underwater":bool(debt>0 and hf<WAD),
            "near_liquidation":bool(debt>0 and hf<105*WAD//100),
            "has_debt":bool(debt>0)}

def inspect(rpc:RPC, *,span:int=2048,chunk:int=256,max_users:int=40)->dict:
    if not (1<=span<=6000 and 1<=chunk<=1000 and 1<=max_users<=100):
        raise ValueError("Invalid window or user budget")
    pool=pool_at_latest(rpc)
    head=rpc.block_number()
    first=max(1,head-span+1)
    found={}
    ranges=[];errors=[]
    for upper in range(head,first-1,-chunk):
        lower=max(first,upper-chunk+1)
        try:
            logs=rpc.call("eth_getLogs",[{"address":pool,"topics":[TOPIC],
                   "fromBlock":hex(lower),"toBlock":hex(upper)}])
            if not isinstance(logs,list):
                raise RpcError("Invalid Borrow log response")
            ranges.append([lower,upper])
            for log in logs:
                try:
                    addr=extract_borrower(log,pool)
                    found.setdefault(addr.lower(),{"borrower":addr,"observed_at_block":int(log["blockNumber"],16)})
                except (ValueError,KeyError,TypeError):continue
        except (RpcError,ValueError,OSError):
            errors.append([lower,upper])
            if len(errors)>=2:break
        if len(found)>=max_users:
            break
    users=list(found.values())[:max_users]
    addresses=[u["borrower"] for u in users]
    raw_responses=[]
    if addresses:
        requests=[(pool,encode("getUserAccountData(address)",user),"latest") for user in addresses]
        try:
            raw_responses=rpc.batch_eth_call(requests,chunk_size=20)
        except (RpcError,OSError,ValueError):
            for address in addresses:
                try:raw_responses.append(rpc.eth_call(pool,encode("getUserAccountData(address)",address),"latest"))
                except (RpcError,OSError,ValueError):raw_responses.append(None)
    results=[];failures=0
    for user,raw in zip(users,raw_responses):
        try:
            row=decode_account_data(raw)
            row.update(user)
            results.append(row)
        except (ValueError,TypeError):
            failures+=1
    near=[x for x in results if x["near_liquidation"]]
    underwater=[x for x in results if x["currently_underwater"]]
    covered=sum(high-low+1 for low,high in ranges)
    return {
        "status":"BORROW_EVENT_HEALTH_SNAPSHOT" if ranges else "BORROW_LOGS_UNAVAILABLE",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "head_block":head,"pool":pool,
        "requested_blocks":head-first+1,
        "borrow_log_blocks_covered":covered,
        "log_ranges":ranges,"log_failed_ranges":errors,
        "recent_borrower_addresses_sampled":len(users),
        "accounts_read_successfully":len(results),
        "account_read_failures":failures,
        "borrowers_near_hf_1_05":len(near),
        "borrowers_hf_below_one":len(underwater),
        "sample":sorted(results,key=lambda x:int(x["health_factor_wad"]))[:20],
        "eligibility_proven":False,"liquidation_fork_profits_verified":0,
        "limitation":"Only borrowers who emitted Borrow in the sampled windows; latest-state health factors can change before inclusion. Must inspect reserve debt/collateral, close factors, protocol liquidation rules, swaps and actual Gas.",
        "no_signing_no_broadcast":True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,default=Path("validation/p0/health"))
    p.add_argument("--span",type=int,default=2048)
    p.add_argument("--max-users",type=int,default=40)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    attempts=[]
    best=None
    for label,url in ENDPOINTS:
        try:
            rpc=RPC(url,timeout=8)
            if rpc.chain_id()!=8453:continue
            result=inspect(rpc,span=a.span,max_users=a.max_users)
            result["endpoint"]=label
            if best is None or result["accounts_read_successfully"]>best["accounts_read_successfully"]:
                best=result
            if result["accounts_read_successfully"]>0:break
        except (RpcError,ValueError,OSError) as exc:
            attempts.append({"endpoint":label,"error_type":type(exc).__name__})
    report=best or {"status":"ALL_ENDPOINTS_UNAVAILABLE","accounts_read_successfully":0}
    report["attempts"]=attempts
    (a.out/"borrower-health-snapshot.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ("sample","log_ranges")},indent=2))
    return 0 if report.get("accounts_read_successfully",0) else 2

if __name__=="__main__":
    raise SystemExit(main())

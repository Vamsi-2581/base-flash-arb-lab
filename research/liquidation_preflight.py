"""Verify historical Base Aave borrower liquidatability BEFORE costly Foundry forks.

At block B-1 this is not exact intra-block state for transactionIndex>0.
An HF<1 and a positive after-premium quoted collateral margin are necessary
screening conditions, not sufficient to claim a working liquidation executor.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from datetime import datetime,timezone
from bot.abi import encode,result_uint
from bot.rpc import RPC,RpcError
from bot.networks import WETH,USDC,EXCHANGES
from bot.searcher import quoter_at,fetch_aave_pool_and_premium,ceil_div

URLS=(
    ("publicnode","https://base-rpc.publicnode.com"),
    ("base_official","https://mainnet.base.org"),
    ("drpc","https://base.drpc.org"),
    ("alchemy_public","https://base-mainnet.g.alchemy.com/public"),
    ("nodeflare_public","https://rpc.nodeflare.app/base/public"),
    ("rpcfree","https://rpcfree.com/base-rpc"),
)
WAD=10**18

def score_one(rpc:RPC,seed:dict) -> dict:
    block=int(seed["block"])-1
    borrower=seed["borrower"]
    amount=int(seed["amount_usdc"])
    if not 0<block or not amount:
        raise ValueError("Invalid seed block or loan")
    tag=hex(block)
    pool,premium=fetch_aave_pool_and_premium(rpc,tag)
    account=rpc.eth_call(pool,encode("getUserAccountData(address)",borrower),tag)
    hf=result_uint(account,5)
    debt=result_uint(account,1)
    status="ELIGIBLE_HF_LT_ONE_PENDING_REAL_EXECUTION" if debt>0 and hf<WAD else "BORROWER_NOT_UNDERWATER_AT_B_MINUS_ONE"
    result={
        "status":status,"block":block,"borrower":borrower,
        "source_tx_hash":seed["source_tx_hash"],
        "hf_wad":str(hf),"total_debt_base_units":str(debt),
        "flash_premium_bps":premium,"premium_usdc_units":ceil_div(amount*premium,10000),
        "fee_tier_quoted":None,"counterfactual_best_usdc_out_units":None,
        "modeled_potential_surplus_before_gas":None,
        "exact_pre_transaction_state":False,
        "full_fork_executed":False
    }
    if status!="ELIGIBLE_HF_LT_ONE_PENDING_REAL_EXECUTION":
        return result
    collateral=int(seed["actual_historical_weth_liquidated_units"])
    if collateral<=0:
        return result
    quotes=[]
    for ex in EXCHANGES:
        for fee in ex.fees:
            try:
                amount_out=quoter_at(rpc,ex.quoter,WETH,USDC,collateral,fee,tag)
                quotes.append((amount_out,ex.dex_id,fee,ex.label))
            except (RpcError,OSError,ValueError,TypeError):
                pass
    if quotes:
        best=max(quotes)
        proceeds,dex,fee,label=best
        result["fee_tier_quoted"]=fee
        result["dex"]=label
        result["sell_dex"]=dex
        result["counterfactual_best_usdc_out_units"]=proceeds
        result["modeled_potential_surplus_before_gas"]=proceeds-amount-result["premium_usdc_units"]
        result["status"]="B_MINUS_ONE_QUOTE_SCREEN_ONLY"
    return result

def run(src:Path,out:Path,timeout:float=7):
    record=json.loads(src.read_text())
    seeds=record.get("seeds",[])[:8]
    all_rows=[]
    endpoint_availability={}
    for name,url in URLS:
        try:
            rpc=RPC(url,timeout=timeout)
            if rpc.chain_id()!=8453:
                endpoint_availability[name]="WRONG_CHAIN";continue
            successful=0
            for seed in seeds:
                try:
                    row=score_one(rpc,seed)
                    row["endpoint"]=name
                    all_rows.append(row)
                    successful+=1
                except (RpcError,OSError,ValueError,TypeError) as err:
                    all_rows.append({"endpoint":name,"source_tx_hash":seed["source_tx_hash"],
                        "status":"HISTORICAL_STATE_UNAVAILABLE_OR_RPC_ERROR",
                        "exception_type":type(err).__name__,
                        "error_detail":str(err)[:180]})
            endpoint_availability[name]="HISTORICAL_QUERIES_SUCCEEDED" if successful else "NO_HISTORICAL_STATE_AVAILABLE"
        except (RpcError,OSError,ValueError) as err:
            endpoint_availability[name]=type(err).__name__
    complete=[r for r in all_rows if r.get("hf_wad") is not None]
    quotable=[r for r in complete if r.get("modeled_potential_surplus_before_gas") is not None]
    result={
        "status":"HISTORICAL_B_MINUS_ONE_PROBE_ONLY",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "historical_seeds":len(seeds),
        "historical_user_health_reads":len(complete),
        "b_minus_one_underwater_read_count":sum(int(r["hf_wad"])<WAD and int(r["total_debt_base_units"])>0 for r in complete),
        "dex_quotes_completed":len(quotable),
        "positive_quoted_surplus_before_gas":sum(r["modeled_potential_surplus_before_gas"]>0 for r in quotable),
        "archive_rpc_results":endpoint_availability,
        "rows":all_rows,
        "fork_and_repayment_verified_count":0,
        "warning":"Historical liquidations already executed by third parties; B-1 not exact transaction state. Quote is not execution. No signing or broadcasts."
    }
    out.mkdir(parents=True,exist_ok=True)
    # Propose venue/fee only when the same tx's historical borrower HF is
    # below 1 and exact quoter return at B-1 has positive after-loan spread.
    # This is NOT a profit proof; only the subsequent real fork may prove it.
    applied=[]
    for i,seed in enumerate(seeds):
        matching=[r for r in quotable if
            r.get("source_tx_hash","").lower()==seed["source_tx_hash"].lower()
            and r["status"]=="B_MINUS_ONE_QUOTE_SCREEN_ONLY"
            and r["modeled_potential_surplus_before_gas"]>0
            and r.get("sell_dex") in (0,1)
            and r.get("fee_tier_quoted") in (100,500,3000,10000)]
        if not matching:
            continue
        best=max(matching,key=lambda r:r["modeled_potential_surplus_before_gas"])
        candidate=dict(seed)
        candidate["sell_dex"]=best["sell_dex"]
        candidate["sell_fee"]=best["fee_tier_quoted"]
        candidate["preflight_hf_wad"]=best["hf_wad"]
        candidate["preflight_counterfactual_spread_before_gas"]=best["modeled_potential_surplus_before_gas"]
        candidate["preflight_endpoint"]=best["endpoint"]
        candidate["execution_verified"]=False
        (out/f"liquidation-candidate-{i}.json").write_text(json.dumps(candidate,indent=2))
        applied.append({"tx":seed["source_tx_hash"],"dex":best["dex"],
                        "fee":best["fee_tier_quoted"],
                        "surplus_before_gas":best["modeled_potential_surplus_before_gas"]})
    result["candidate_route_reoptimizations"]=applied
    (out/"preflight.json").write_text(json.dumps(result,indent=2))
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,default=Path("validation/liquidation-fork/historical-liquidation-seeds.json"))
    p.add_argument("--out",type=Path,default=Path("validation/liquidation-fork"))
    a=p.parse_args()
    if not a.input.exists():p.error("No historical liquidation seeds")
    result=run(a.input,a.out)
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},indent=2))
    return 0 if result["historical_user_health_reads"] else 2
if __name__=="__main__":
    raise SystemExit(main())

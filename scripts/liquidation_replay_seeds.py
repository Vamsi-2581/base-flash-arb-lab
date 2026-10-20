"""Turn independently published Aave Base liquidation receipts into reproducible fork seeds.

Read-only eth_getTransactionReceipt from public RPC. Historical liquidation
receipts are not evidence that our executor would succeed immediately prior.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from bot.historical import ENDPOINTS
from bot.rpc import RPC,RpcError
from bot.networks import USDC,WETH
from research.aave_liquidation_survey import decode_liquidation,TOPIC

HISTORICAL_TX_HASHES=(
    # May 12 2026, Base block 45903542, public Aave event with WETH/USDC
    "0x6f4c472b846a2d5530ece4596a39c5cb9f088f0ad2e3804f80f95c8b2289d40a",
    # Other past published Aave WETH/USDC liquidation txs, for archive exploration
    "0x26eaac4bbffbfd0f3e20717289f2f288ce5cb4d1636444238bcb56c89f4148d4",
    "0x9669899d57317c1947059aed5ffe519bd8eb656bc5402926fd9218bc74ca749e",
)
AAVE_POOL="0xa238dd80c259a72e81d7e4664a9801593f98d1c5"

def receipt_to_seeds(receipt:dict)->list[dict]:
    if not isinstance(receipt,dict) or int(receipt.get("status","0x0"),16)!=1:
        raise ValueError("Missing or reverted receipt")
    block=int(receipt["blockNumber"],16)
    if block<=1: raise ValueError("Invalid historical liquidation block")
    txindex=int(receipt["transactionIndex"],16)
    entries=[]
    for log in receipt.get("logs",[]):
        try: event=decode_liquidation(log,AAVE_POOL)
        except (KeyError,TypeError,ValueError):continue
        if (event["collateral_asset"].lower()!=WETH.lower() or
            event["debt_asset"].lower()!=USDC.lower() or
            event["received_a_tokens"]):
            continue
        entries.append({
            "status":"ACTUAL_PAST_LIQUIDATION_FORK_SEED_NOT_PROVEN",
            "source_tx_hash":receipt["transactionHash"],
            "source_transaction_index":txindex,
            "block":block,
            "fork_pre_transaction_approx_block":block-1,
            "borrower":event["borrower"],
            "amount_usdc":int(event["debt_repaid_units"]),
            "actual_historical_weth_liquidated_units":int(event["collateral_received_units"]),
            "sell_dex":0, "sell_fee":500,
            "min_usdc":1, "min_profit_usdc":1,
            "result_is_new_profit":False,
            "replay_exactness":"B-1 state only, NOT exact intra-block predecessor state",
        })
    return entries

def seeds_from_survey(survey_data:dict)->list[dict]:
    """Promote only canonical already-recorded WETH/USDC events, no profit claim."""
    if not isinstance(survey_data,dict):
        raise ValueError("Invalid survey")
    if survey_data.get("pool","").lower()!=AAVE_POOL.lower():
        raise ValueError("Survey used a different pool")
    result=[]
    for item in survey_data.get("events",[]):
        if (item.get("debt_asset","").lower()!=USDC.lower() or
            item.get("collateral_asset","").lower()!=WETH.lower() or
            item.get("received_a_tokens") or
            int(item.get("debt_repaid_units",0))<=0 or
            int(item.get("collateral_received_units",0))<=0):
            continue
        tx=item.get("transaction_hash","")
        if not isinstance(tx,str) or len(tx)!=66:continue
        block=int(item["block"])
        if block<=1:continue
        result.append({
            "status":"HISTORICAL_RECORDED_LIQUIDATION_NEEDS_FULL_FORK",
            "source_tx_hash":tx,
            "source_transaction_index":int(item.get("transaction_index",0)),
            "block":block,
            "fork_pre_transaction_approx_block":block-1,
            "borrower":item["borrower"],
            "amount_usdc":int(item["debt_repaid_units"]),
            "actual_historical_weth_liquidated_units":int(item["collateral_received_units"]),
            "sell_dex":0,"sell_fee":500,
            "min_usdc":1,"min_profit_usdc":1,
            "result_is_new_profit":False,
            "replay_exactness":"B-1 state only, not transaction-index exact"
        })
    return result

def gather(output:Path,*,timeout:float=8,survey_file:Path|None=None,include_known:bool=True)->dict:
    output.mkdir(parents=True,exist_ok=True)
    attempts=[];valid=[]
    if survey_file and survey_file.exists():
        valid.extend(seeds_from_survey(json.loads(survey_file.read_text())))
    if not include_known and not valid:
        record={"status":"NO_RECORDED_LIQUIDATION_SEEDS","valid_usdc_debt_weth_collateral_receipts":0,
                "seeds":[],"provider_attempts":[],
                "full_fork_execution_profit_verified":0,"no_broadcast":True}
        (output/"historical-liquidation-seeds.json").write_text(json.dumps(record,indent=2))
        return record
    if include_known:
      for name,url in ENDPOINTS:
          try:
              rpc=RPC(url,timeout=timeout)
              if rpc.chain_id()!=8453:continue
              for tx in HISTORICAL_TX_HASHES:
                  try:
                      receipt=rpc.call("eth_getTransactionReceipt",[tx])
                      if not receipt:
                          attempts.append({"rpc":name,"tx":tx,"status":"RECEIPT_NOT_AVAILABLE"});continue
                      valid.extend(receipt_to_seeds(receipt))
                  except (RpcError,TypeError,ValueError,KeyError) as exc:
                      attempts.append({"rpc":name,"tx":tx,
                          "error_type":type(exc).__name__})
              if valid:break
          except (RpcError,ValueError,OSError) as exc:
              attempts.append({"rpc":name,"error_type":type(exc).__name__})
    dedup={x["source_tx_hash"].lower():x for x in valid}
    valid=list(dedup.values())
    result={
        "status":"VALIDATED_HISTORICAL_LIQUIDATION_SEEDS" if valid else "HISTORICAL_RECEIPTS_UNAVAILABLE",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "trusted_aave_pool":AAVE_POOL,
        "valid_usdc_debt_weth_collateral_receipts":len(valid),
        "seeds":valid,
        "provider_attempts":attempts,
        "full_fork_execution_profit_verified":0,
        "warning":"Historical success does not ensure a fresh pre-tx (B-1) fork can liquidate. Must execute and repay Aave on real fork and price conservative gas.",
        "no_broadcast":True,
    }
    (output/"historical-liquidation-seeds.json").write_text(json.dumps(result,indent=2))
    for index,row in enumerate(valid):
        (output/f"liquidation-candidate-{index}.json").write_text(json.dumps(row,indent=2))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,default=Path("validation/liquidation-fork"))
    p.add_argument("--survey",type=Path,help="Optional same-run Aave liquidation survey JSON")
    p.add_argument("--recent-only",action="store_true",help="Do not retry archived static receipts; use only survey results")
    a=p.parse_args()
    r=gather(a.out,survey_file=a.survey,include_known=not a.recent_only)
    print(json.dumps({k:v for k,v in r.items() if k not in ("seeds","provider_attempts")},indent=2))
    for e in r["seeds"]:
        print(json.dumps({"block":e["block"],"tx_hash":e["source_tx_hash"],
                          "debt_usdc_units":e["amount_usdc"],"borrower":e["borrower"]}))
    return 0 if r["valid_usdc_debt_weth_collateral_receipts"] else 2
if __name__=="__main__":
    raise SystemExit(main())

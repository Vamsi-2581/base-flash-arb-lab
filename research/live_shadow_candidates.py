"""Convert a current, genuinely eligible on-chain snapshot into local-Fork inputs.

All inputs are discovered from a SINGLE pinned Base block; no signatures, keys,
deployments, fund transfers, or transaction broadcasting. USDC/WETH account
balances are read from Aave PoolDataProvider. This does not prove actual
liquidation amount (close-factor and protocol bonuses may differ).
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

CHAIN = 8453
USDC_UNIT = 10**6
WAD = 10**18
MAX_LOAN = 5_000*USDC_UNIT

def choose(snapshot: dict, *, max_candidates: int = 3) -> list[dict]:
    if not isinstance(snapshot,dict) or not 1<=max_candidates<=10:
        raise ValueError("Bad readiness input or candidate limit")
    if (snapshot.get("status")!="MAINNET_SHADOW_READ_ONLY" or
        snapshot.get("chain_id")!=CHAIN or
        not isinstance(snapshot.get("block"),int) or snapshot["block"]<=1 or
        not isinstance(snapshot.get("block_age_seconds"),int) or
        not -5 <= snapshot["block_age_seconds"]<=30 or
        snapshot.get("reserve_risk_flags")!=[] or
        not snapshot.get("aave_pool_data_provider")):
        return []
    result=[]
    for item in snapshot.get("accounts",[]):
        if not isinstance(item,dict): continue
        if (item.get("health_factor_below_one") is not True or
            item.get("USDC_debt_token_balance_verified") is not True or
            item.get("WETH_collateral_aToken_balance_verified") is not True or
            item.get("USDC_WETH_pair_supported_for_liquidation") is not True):
            continue
        if int(item.get("health_factor_wad",WAD))>=WAD:continue
        amount=int(item["verified_usdc_total_debt_units"])
        collateral=int(item["verified_weth_atoken_balance_units"])
        # Conservative sampling allocation, NOT a claimed Aave close factor.
        borrow=min(amount//4,MAX_LOAN)
        if borrow<100*USDC_UNIT or collateral<=0:continue
        addr=item.get("address","")
        if not isinstance(addr,str) or len(addr)!=42 or not addr.startswith("0x"):
            continue
        result.append({
            "status":"LOCAL_SHADOW_FORK_INPUT_ONLY",
            "chain_id":CHAIN,
            "snapshot_block":snapshot["block"],
            "snapshot_block_hash":snapshot.get("block_hash"),
            "borrower":addr,
            "borrowed_usdc_units":borrow,
            "known_usdc_debt_units":amount,
            "known_weth_atoken_units":collateral,
            "health_factor_wad":str(item["health_factor_wad"]),
            "min_profit_usdc_units":5*USDC_UNIT,
            "sell_routes":[{"dex":0,"fee":100},{"dex":0,"fee":500},
                           {"dex":1,"fee":100},{"dex":1,"fee":500}],
            "amount_to_seize_verified":False,
            "fork_simulated":False,
            "live_approved":False,
            "transaction_broadcast":False,
            "caution":"At snapshot end state only; 25% debt is provisional. Aave liquidation close factor/bonus and Pool flash liquidity must be tested by actual local fork. This is not an opportunity valuation."
        })
        if len(result)>=max_candidates:break
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,default=Path("validation/shadow-live/readiness.json"))
    p.add_argument("--out",type=Path,default=Path("validation/shadow-live/candidates"))
    args=p.parse_args()
    if not args.input.exists():
        p.error("No pinned mainnet readiness file")
    candidates=choose(json.loads(args.input.read_text()))
    args.out.mkdir(parents=True,exist_ok=True)
    for i,c in enumerate(candidates):
        (args.out/f"shadow-{i}.json").write_text(json.dumps(c,indent=2))
    result={"classification":"SHADOW_FORK_INPUTS_NOT_LIVE" if candidates else "NO_REPLAYABLE_CURRENT_PAIR",
            "candidate_count":len(candidates),"broadcast_enabled":False,
            "reasons_if_zero":"No currently sampled account with HF<1 AND on-chain USDC debt AND enabled WETH aToken collateral; or source stale/incomplete.",
            "candidates":candidates}
    (args.out/"summary.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!="candidates"},indent=2))
if __name__=="__main__":
    main()

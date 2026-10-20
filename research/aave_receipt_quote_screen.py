"""Indicative, explicitly non-executable valuation of past Aave USDC/WETH receipts.

This runs only when a real LiquidationCall has been observed. Post-block
QuoterV2 estimates cannot reproduce the liquidator's intra-transaction state.
Never classify estimated surplus as a new liquidatable borrower or Fork proof.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from bot.historical import ENDPOINTS, HistoricalRPC
from bot.networks import USDC, WETH, EXCHANGES
from bot.rpc import RPC, RpcError
from bot.searcher import quoter_at, gas_usdc_estimate, fetch_aave_pool_and_premium, ceil_div

def eligible_for_usdc_weth_screen(event: dict) -> bool:
    return (event.get("debt_asset","").lower() == USDC.lower() and
            event.get("collateral_asset","").lower() == WETH.lower() and
            event.get("received_a_tokens") is False and
            int(event.get("debt_repaid_units",0))>0 and
            int(event.get("collateral_received_units",0))>0)

def grade_receipt(debt_units: int, quote_usdc_units: int, premium_bps: int,
                  gas_usdc_units: int, stress_bps: int = 15) -> dict:
    """Pure integer arithmetic, stress is a conservative sensitivity NOT actual slip."""
    if (min(debt_units,quote_usdc_units)>0 and gas_usdc_units>=0 and
        0<=premium_bps<10000 and 0<=stress_bps<10000) is False:
        raise ValueError("Invalid economic parameters")
    flash_fee=ceil_div(debt_units*premium_bps,10000)
    stressed_output=quote_usdc_units*(10000-stress_bps)//10000
    net=stressed_output-debt_units-flash_fee-gas_usdc_units
    return {"historical_quote_usdc_units":quote_usdc_units,
            "stressed_output_usdc_units":stressed_output,
            "loan_principal_usdc_units":debt_units,
            "flash_premium_usdc_units":flash_fee,
            "modeled_gas_usdc_units":gas_usdc_units,
            "modeled_net_usdc_units":net,
            "hypothetically_positive":net>0,
            "status":"RECEIPT_VALUED_AFTER_BLOCK_NOT_FORK_EXECUTED"}

def run(input_file: Path, out: Path, limit: int=10, timeout:float=8) -> dict:
    if not 1<=limit<=50:
        raise ValueError("limit must be 1..50")
    events=json.loads(input_file.read_text()).get("events",[])
    eligible=[e for e in events if eligible_for_usdc_weth_screen(e)][:limit]
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for event in eligible:
        result={"event_block":event["block"],"historical_tx_hash":event["transaction_hash"],
                "status":"NO_SUPPORTED_PROVIDER"}
        for name,url in ENDPOINTS:
            try:
                base=RPC(url,timeout=timeout)
                if base.chain_id()!=8453:
                    raise RpcError("Wrong chain")
                block=int(event["block"])
                snap=HistoricalRPC(base,block)
                _,premium=fetch_aave_pool_and_premium(snap,hex(block))
                gas=gas_usdc_estimate(snap,hex(block),snap.gas_price(),1350000,200000,20000)
                quotes=[]
                for exchange in EXCHANGES:
                    for fee in exchange.fees:
                        try:
                            received=quoter_at(snap,exchange.quoter,WETH,USDC,
                                int(event["collateral_received_units"]),fee,hex(block))
                            quotes.append((received,exchange.label,fee))
                        except (RpcError,ValueError,TypeError):
                            continue
                if not quotes:
                    raise RpcError("No WETH->USDC historical V3 quotes")
                received,dex,fee=max(quotes,key=lambda x:x[0])
                result.update({"venue":dex,"pool_fee_ppm":fee,"provider":name,
                     **grade_receipt(int(event["debt_repaid_units"]),received,premium,gas)})
                break
            except (RpcError,OSError,ValueError,TypeError) as exc:
                result.setdefault("provider_errors",[]).append({
                    "provider":name,"reason":str(exc)[:130]})
        rows.append(result)
    report={"status":"HISTORICAL_LIQUIDATION_RECEIPT_QUOTE_ONLY",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "historical_events_in_input":len(events),
        "supported_usdc_debt_weth_collateral_events":len(eligible),
        "receipt_quotes_complete":sum(r["status"]=="RECEIPT_VALUED_AFTER_BLOCK_NOT_FORK_EXECUTED" for r in rows),
        "positive_post_block_counterfactuals":sum(r.get("hypothetically_positive",False) for r in rows),
        "new_liquidation_opportunities_verified":0,
        "fork_executed_positive_profits":0,
        "results":rows,
        "limitation":"The event was ALREADY liquidated by another actor. Quoter at end-of-block state differs from the intrablock execution, and estimated costs differ from receipt Gas. Nothing here is an executable opportunity.",
        "no_wallet_no_broadcast":True}
    (out/"receipt-quote-screen.json").write_text(json.dumps(report,indent=2))
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,default=Path("validation/p0/liquidations/liquidation-survey.json"))
    p.add_argument("--out",type=Path,default=Path("validation/p0/valuation"))
    p.add_argument("--limit",type=int,default=10)
    a=p.parse_args()
    if not a.input.exists():
        p.error("Survey JSON missing")
    r=run(a.input,a.out,limit=a.limit)
    print(json.dumps({k:v for k,v in r.items() if k!="results"},indent=2))

if __name__=="__main__":
    main()

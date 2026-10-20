"""Read-only quoted surplus for previously FILLED Base Dutch V3 orders.

Requires SDK signature-checked, redacted, single-output filled-order terms.
Quotes post-fill *end-of-block* V3 liquidity, NOT the transaction's pre-fill
state. Does not infer filler PnL or recover an already executed order.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from bot.networks import USDC, WETH, EXCHANGES
from bot.historical import ENDPOINTS, HistoricalRPC
from bot.rpc import RPC, RpcError
from bot.searcher import quoter_at

SUPPORTED_BASE = {USDC.lower(), WETH.lower()}
NATIVE_ZERO = "0x" + "0" * 40
ERC20_ETH_BRIDGE = "0x00000000e20E49e6dCeE6e8283A0C090578F0fb9"

def route_proxy(token_in: str, token_out: str) -> tuple[str,str,bool]:
    """Indicative ONLY: pool quote on WETH when output is native ETH sentinel.

    Native settlement must unwrap WETH and may require extra fees/gas; never
    price ERC20ETH input as regular ERC20. Does not prove protocol semantics.
    """
    if token_in.lower() == NATIVE_ZERO.lower():
        raise ValueError("Native ETH input sentinel needs protocol-specific funding")
    if token_in.lower() == ERC20_ETH_BRIDGE.lower():
        raise ValueError("ERC20ETH input needs special EIP-7702 funding route")
    if token_out.lower() == ERC20_ETH_BRIDGE.lower():
        raise ValueError("ERC20ETH output not a regular V3 pool token")
    native_out=token_out.lower() == NATIVE_ZERO.lower()
    return token_in, WETH if native_out else token_out, native_out

def is_addr(s):
    return isinstance(s,str) and len(s)==42 and s.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in s[2:])

def eligible(item: dict) -> bool:
    terms = item.get("historical_terms")
    return (
        item.get("status") in ("OFFCHAIN_SIGNATURES_VALID_ALREADY_FILLED",
                               "OFFCHAIN_SIGNATURES_VALID_BUT_EXPIRED")
        and item.get("eoa_maker_signature_verified") is True
        and item.get("eoa_cosigner_signature_verified") is True
        and isinstance(terms,dict)
        and is_addr(terms.get("input_token"))
        and is_addr(terms.get("output_token"))
        and terms["input_token"].lower()!=terms["output_token"].lower()
        and (terms["input_token"].lower() in SUPPORTED_BASE or
             terms["output_token"].lower() in SUPPORTED_BASE)
        and int(terms.get("input_amount_units",0)) > 0
        and int(terms.get("required_output_units",0)) > 0
        and int(terms.get("fill_block",0)) > 0
    )

def grade(input_units: int, required_out: int, best_out: int, *, stress_bps: int = 15) -> dict:
    if min(input_units,required_out,best_out) <= 0 or not 0 <= stress_bps < 10000:
        raise ValueError("Invalid quote values or stress")
    gross=best_out-required_out
    stressed_out=best_out*(10000-stress_bps)//10000
    stressed_surplus=stressed_out-required_out
    return {
        "quoted_received_output_token_units":best_out,
        "required_order_output_token_units":required_out,
        "hypothetical_gross_surplus_output_token_units":gross,
        "gross_surplus_positive":gross>0,
        "stress_bps":stress_bps,
        "stressed_quote_output_units":stressed_out,
        "stressed_quote_surplus_units":stressed_surplus,
        "stressed_gross_positive_before_gas":stressed_surplus>0,
        "execution_and_gas_profit_verified":False,
        "status":"POST_FILL_BLOCK_QUOTE_ONLY_NOT_EXECUTABLE"
    }

def analyze(orders_report: Path, output: Path, *, limit: int = 8, timeout:float=7) -> dict:
    if not 1<=limit<=30:
        raise ValueError("limit 1..30")
    sdk=json.loads(orders_report.read_text())
    reports=sdk.get("reports",[])
    entries=[x for report in reports if report.get("requested_order_status")=="filled"
             for x in report.get("orders",[]) if eligible(x)][:limit]
    rows=[]
    for item in entries:
        terms=item["historical_terms"]
        block=int(terms["fill_block"])
        result={"order_hash":item.get("order_hash"),"fill_block":block,
                "input_token":terms["input_token"],"output_token":terms["output_token"],
                "status":"NO_HISTORICAL_QUOTE","provider_errors":[]}
        try:
            route_in,route_out,native_proxy=route_proxy(
                terms["input_token"],terms["output_token"])
        except ValueError:
            result["status"]="UNSUPPORTED_NATIVE_ASSET_ROUTE"
            rows.append(result)
            continue
        result["requires_native_output_settlement"]=native_proxy
        result["spot_quote_uses_weth_proxy_only"]=native_proxy
        for label,url in ENDPOINTS:
            try:
                rpc=RPC(url,timeout=timeout)
                if rpc.chain_id()!=8453:raise RpcError("Wrong chain")
                snap=HistoricalRPC(rpc,block)
                # Do not interpret native ETH sentinel or undeployed token
                # addresses as ordinary pool-backed ERC20 routes.
                for token in (route_in,route_out):
                    if rpc.call("eth_getCode",[token,hex(block)]) in ("0x",None):
                        raise RpcError("Token has no ERC20-like runtime bytecode")
                quotes=[]
                for venue in EXCHANGES:
                    for fee in venue.fees:
                        try:
                            received=quoter_at(snap,venue.quoter,
                                route_in,route_out,
                                int(terms["input_amount_units"]),fee,hex(block))
                            quotes.append((received,venue.label,fee))
                        except (RpcError,ValueError,OSError):
                            continue
                if not quotes:raise RpcError("No valid V3 token-pair quote at the filled block")
                received,venue,fee=max(quotes,key=lambda item:item[0])
                result.update({"rpc_endpoint":label,"dex":venue,"v3_fee_tier_ppm":fee,
                    **grade(int(terms["input_amount_units"]),
                            int(terms["required_output_units"]),received)})
                break
            except (RpcError,ValueError,OSError,TypeError) as exc:
                result["provider_errors"].append({"endpoint":label,
                                                 "error_type":type(exc).__name__})
        rows.append(result)
    complete=[x for x in rows if x["status"]=="POST_FILL_BLOCK_QUOTE_ONLY_NOT_EXECUTABLE"]
    summary={
        "status":"FILLED_ORDERS_POST_BLOCK_V3_QUOTES_ONLY",
        "observed_utc":datetime.now(timezone.utc).isoformat(),
        "filled_orders_seen":sum(len(r.get("orders",[])) for r in reports
                                 if r.get("requested_order_status")=="filled"),
        "eligible_signed_single_output_base_asset_filled_orders":len(entries),
        "post_fill_block_quotes_complete":len(complete),
        "hypothetical_positive_gross_surplus_quotes":sum(x["gross_surplus_positive"] for x in complete),
        "positive_after_15bps_quote_stress_before_gas":sum(x["stressed_gross_positive_before_gas"] for x in complete),
        "simulated_true_execution_profit_count":0,
        "native_eth_weth_proxy_screened":sum(bool(r.get("spot_quote_uses_weth_proxy_only")) for r in rows),
        "warning":"Already-filled orders: end-of-block quote != actual fill. For native ETH output (zero address) WETH proxy only estimates swap value; actual unwrap/settlement, gas, exclusivity, reactor callback, execution ordering and priority costs are NOT verified.",
        "rows":rows,"no_wallet_or_broadcast":True,
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"filled-order-quote-results.json").write_text(json.dumps(summary,indent=2))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,default=Path("validation/p0/uniswapx-sdk/sdk-validation.json"))
    p.add_argument("--out",type=Path,default=Path("validation/p0/filled-quotes"))
    p.add_argument("--limit",type=int,default=8)
    a=p.parse_args()
    if not a.input.exists():
        p.error("SDK observation file unavailable")
    result=analyze(a.input,a.out,limit=a.limit)
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},indent=2))
    for row in result["rows"]:
        print(json.dumps({k:v for k,v in row.items() if k!="provider_errors"}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())

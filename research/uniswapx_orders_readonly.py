"""Read-only UniswapX Base Dutch_V3 order discovery.

No private keys, signatures or signed payloads are persisted. Never interpret
the open-order API envelope as cryptographic verification or executable profit.
Official endpoint rate limit is 4 RPS; this tool does ONE request per run
and DOES NOT retry HTTP 429.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://api.uniswap.org/v2/orders"
CHAIN_ID = 8453
ORDER_TYPE = "Dutch_V3"

def safe_order_identity(order: dict) -> dict:
    if not isinstance(order, dict):
        raise ValueError("Invalid order record")
    chain = order.get("chainId")
    status = order.get("orderStatus", order.get("status"))
    kind = order.get("type", order.get("orderType"))
    if chain is None or int(chain) != CHAIN_ID:
        raise ValueError("Missing or wrong chain")
    if kind != ORDER_TYPE:
        raise ValueError("Missing or wrong Dutch_V3 order type")
    if status is None or str(status).lower() != "open":
        raise ValueError("Order status not explicitly open")
    encoded = order.get("encodedOrder")
    signature = order.get("signature")
    if encoded is not None and (not isinstance(encoded,str) or not encoded.startswith("0x")):
        raise ValueError("Malformed encoded order")
    if signature is not None and (not isinstance(signature,str) or not signature.startswith("0x")):
        raise ValueError("Malformed signature")
    hash_value = order.get("orderHash")
    if not encoded or not signature or not hash_value:
        raise ValueError("Missing encodedOrder / signature / orderHash")
    if hash_value is not None and (not isinstance(hash_value,str) or
                                   not hash_value.startswith("0x") or len(hash_value)!=66):
        raise ValueError("Malformed order hash")
    return {
        "hash": hash_value if isinstance(hash_value,str) else None,
        "reported_chain":chain,
        "reported_status":status,
        "reported_type":kind,
        "has_encoded_order": isinstance(encoded,str) and len(encoded)>2,
        "has_signature": isinstance(signature,str) and len(signature)>2,
        "note":"API envelope only: signature, order authorization, exclusivity, expiration and fillability NOT checked.",
    }

def summarize_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
        raise ValueError("Unexpected order API JSON shape")
    orders = payload["orders"]
    summaries, rejected = [],[]
    for index,order in enumerate(orders):
        try:
            summaries.append(safe_order_identity(order))
        except (ValueError,TypeError,KeyError) as exc:
            rejected.append({"index":index,"reason":str(exc)[:120]})
    return {
        "status":"OPEN_ORDER_ENVELOPES_OBSERVED",
        "orders_returned":len(orders),
        "envelopes_consistent":len(summaries),
        "envelopes_rejected":len(rejected),
        "entries":summaries,
        "invalid_entries":rejected,
        "signature_verified":False,
        "reactor_decode_verified":False,
        "fillability_verified":False,
        "quote_profit_verified":False,
        "reason":"Needs UniswapX SDK DutchV3 signed-order decoding, valid cosignature, exclusivity handling, Permit2 status and pinned Base route quotations.",
        "no_private_keys_or_trades":True,
    }

def request_orders(*, limit:int=15, timeout:float=10) -> dict:
    if not 1 <= limit <= 50:
        raise ValueError("Limit must be 1..50")
    query=urllib.parse.urlencode({"orderStatus":"open","chainId":CHAIN_ID,
                                  "orderType":ORDER_TYPE,"limit":limit})
    req=urllib.request.Request(ENDPOINT+"?"+query,
          headers={"Accept":"application/json","User-Agent":"base-arb-research/0.1"},
          method="GET")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            payload=json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code==429:
            return {"status":"RATE_LIMITED_429_NO_RETRY","orders_returned":None,
                    "no_private_keys_or_trades":True}
        return {"status":"API_HTTP_ERROR","http_status":exc.code,"orders_returned":None,
                "no_private_keys_or_trades":True}
    except (OSError, TimeoutError, ValueError, TypeError) as exc:
        return {"status":"API_UNAVAILABLE","error_type":type(exc).__name__,
                "orders_returned":None,"no_private_keys_or_trades":True}
    try:
        return summarize_payload(payload)
    except (ValueError,TypeError) as exc:
        return {"status":"INVALID_RESPONSE","reason":str(exc)[:150],
                "orders_returned":None,"no_private_keys_or_trades":True}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=Path("validation/uniswapx"))
    parser.add_argument("--limit",type=int,default=15)
    parser.add_argument("--timeout",type=float,default=10)
    args=parser.parse_args(argv)
    args.out.mkdir(parents=True,exist_ok=True)
    result=request_orders(limit=args.limit,timeout=args.timeout)
    result["timestamp_utc"]=datetime.now(timezone.utc).isoformat()
    result["requested_chain_id"]=CHAIN_ID
    result["requested_order_type"]=ORDER_TYPE
    (args.out/"orders-observation.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ("entries","invalid_entries")},indent=2))
    return 0 if result["status"]=="OPEN_ORDER_ENVELOPES_OBSERVED" else 2

if __name__=="__main__":
    raise SystemExit(main())

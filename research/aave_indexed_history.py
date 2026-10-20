"""Optional read-only Aave historical event discovery using Blockscout's legacy logs API.

The hosted Base service may require a paid key or reject these routes; explicit
HTTP 401/403/429/unsupported responses are INACCESSIBLE, not "no liquidations".
No provider key, tx execution or actual-profit assumptions.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

from research.aave_liquidation_survey import decode_liquidation, TOPIC

ENDPOINT="https://base.blockscout.com/api"
BASE_AAVE_POOL="0xa238dd80c259a72e81d7e4664a9801593f98d1c5"

def classify(data: dict, pool: str) -> dict:
    if not isinstance(data,dict) or "status" not in data:
        return {"status":"INDEXER_INVALID_SCHEMA","events":[]}
    status=str(data["status"])
    result=data.get("result")
    if status=="0":
        reason=str(data.get("message",""))+" "+str(result)[:160]
        if "no record" in reason.lower() or "no logs" in reason.lower():
            return {"status":"INDEXER_NO_EVENTS_IN_RESPONDED_QUERY","events":[]}
        return {"status":"INDEXER_UNAVAILABLE_OR_ERROR","reason":reason[:180],"events":[]}
    if status!="1" or not isinstance(result,list):
        return {"status":"INDEXER_INVALID_SCHEMA","events":[]}
    events,invalid=[],0
    for event in result:
        try:
            parsed=decode_liquidation(event,pool)
            events.append(parsed)
        except (ValueError,TypeError,KeyError,IndexError):
            invalid+=1
    return {"status":"INDEXER_LIQUIDATION_EVENTS_OBSERVED" if events else "INDEXER_NO_MATCHING_EVENTS",
            "response_logs":len(result),"invalid_logs":invalid,"events":events}

def survey(*,pool:str=BASE_AAVE_POOL,limit:int=50,timeout:int=12):
    if not 1<=limit<=100:
        raise ValueError("limit")
    query=urllib.parse.urlencode({"module":"logs","action":"getLogs",
        "fromBlock":"0","toBlock":"latest","address":pool,"topic0":TOPIC,
        "page":"1","offset":str(limit)})
    req=urllib.request.Request(ENDPOINT+"?"+query,
        headers={"Accept":"application/json","User-Agent":"base-flash-arb-lab/0.1"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            payload=json.load(response)
        result=classify(payload,pool)
    except urllib.error.HTTPError as exc:
        result={"status":"INDEXER_HTTP_UNAVAILABLE","http_status":exc.code,"events":[]}
    except (OSError,ValueError,TypeError) as exc:
        result={"status":"INDEXER_TRANSPORT_UNAVAILABLE",
                "error_type":type(exc).__name__,"events":[]}
    result["source"]="Base Blockscout legacy address+topic0 logs API (provider terms/rate limits apply)"
    result["events_found"]=len(result["events"])
    result["queried_at_utc"]=datetime.now(timezone.utc).isoformat()
    result["full_history_coverage_proven"]=False
    result["positive_execution_profit_verified"]=False
    result["no_wallet_no_broadcast"]=True
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=Path("validation/p0/indexed"))
    parser.add_argument("--limit",type=int,default=50)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    r=survey(limit=args.limit)
    (args.out/"indexed-history.json").write_text(json.dumps(r,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k!="events"},indent=2))
    return 0 if r["status"].startswith("INDEXER_LIQUIDATION_EVENTS") else 2

if __name__=="__main__":
    raise SystemExit(main())

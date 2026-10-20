"""Event-conditioned, read-only block-boundary Base multi-DEX backtest.

Reads selected actual Uniswap/Pancake WETH/USDC Swap events, then prices
B-1 and B end-of-block states. Does NOT replay transaction order or prove
counterfactual inclusion within B.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from bot.extended_research import research_snapshot, parse_assets, USDC_UNIT
from bot.historical import HistoricalRPC, ENDPOINTS
from bot.rpc import RPC, RpcError

def event_blocks(events: dict, head: int, max_events: int = 2) -> list[dict]:
    rows=[]
    for e in events.get("selected",[])[:max_events]:
        block=int(e["block"])
        if not 1 < block <= head - 2:
            continue
        rows.extend([
            {"block":block-1, "side":"PRE_EVENT_BLOCK", "event_block":block,
             "trigger_transaction":e.get("transaction_hash")},
            {"block":block, "side":"POST_EVENT_BLOCK", "event_block":block,
             "trigger_transaction":e.get("transaction_hash")},
        ])
    if not rows:
        rows=[{"block":head-32, "side":"BASELINE_NO_EVENT", "event_block":None,
               "trigger_transaction":None}]
    return rows

def run(events: dict, output: Path, assets: str, amounts: str, max_seconds: float=225,
        max_events: int=2, max_routes: int=10, refine_top: int=2):
    output.mkdir(parents=True, exist_ok=True)
    providers=[]; provider_errors=[]
    for name,url in ENDPOINTS:
        try:
            rpc=RPC(url,timeout=8)
            if rpc.chain_id()==8453:
                providers.append((name,rpc))
            else:
                provider_errors.append(name+": wrong chain")
        except (RpcError,ValueError,OSError) as ex:
            provider_errors.append(name+": "+str(ex)[:150])
    if not providers:
        result={"status":"NO_RPC_AVAILABLE","provider_errors":provider_errors,"snapshots":[]}
        (output/"event-backtest.json").write_text(json.dumps(result,indent=2))
        return result
    head=providers[0][1].block_number()
    blocks=event_blocks(events,head,max_events)
    tokens=parse_assets(assets)
    sizes=sorted(set(int(Decimal(s)*USDC_UNIT) for s in amounts.split(",")))
    sampled=[]; started=time.monotonic()
    for index,entry in enumerate(blocks):
        remaining=max_seconds-(time.monotonic()-started)
        if remaining <= 4:
            sampled.append({**entry,"status":"TIME_BUDGET_EXCEEDED"})
            continue
        item={**entry,"status":"UNAVAILABLE","providers_tried":[]}
        for name,rpc in providers:
            # Bound each attempt independently; an entire block is still atomic at one provider.
            try:
                snap=research_snapshot(HistoricalRPC(rpc,entry["block"]),
                    entry["block"],tokens,sizes,
                    max_seconds=max(4, remaining-min(2, remaining/5)),
                    max_routes=max_routes,refine_top=refine_top)
                item.update({"status":"QUOTED","provider":name,
                    "wall_seconds":snap["wall_seconds"],
                    "total_full_two_leg_quotes":snap["total_full_two_leg_quotes"],
                    "asset_summaries":snap["asset_summaries"],
                    "positive_research_quotes":snap["positive_quote_rows"],
                    "legacy_fork_candidates":snap.get("legacy_fork_candidates",[]),
                    "errors":snap["warnings"]})
                break
            except (RpcError,ValueError,OSError) as e:
                item["providers_tried"].append({"name":name,"error":str(e)[:160]})
        sampled.append(item)
        (output/"event-progress.json").write_text(json.dumps(sampled,indent=2))
    candidates = []
    for item in sampled:
        if item.get("status") != "QUOTED":
            continue
        endpoint = item.get("provider")
        for row in item.get("legacy_fork_candidates",[]):
            row["historic_endpoint"] = endpoint
            row["event_block"] = item.get("event_block")
            row["event_side"] = item["side"]
            candidates.append(row)
    # Fork candidates must match exactly one provider and pinned block.
    (output/"fork-candidates.jsonl").write_text(
        "".join(json.dumps(row)+"\n" for row in
                sorted(candidates,key=lambda r:r["estimated_net_usdc"],reverse=True)[:10]))
    comparisons=[]
    for event_block in sorted(set(i["event_block"] for i in blocks if i["event_block"])):
        pair=[x for x in sampled if x["event_block"]==event_block]
        if len(pair)!=2 or any(x["status"]!="QUOTED" for x in pair):
            comparisons.append({"event_block":event_block,"status":"INCOMPLETE_PAIR"})
            continue
        pre,post=sorted(pair,key=lambda x:x["block"])
        per_asset=[]
        preassets={x["asset"]:x for x in pre["asset_summaries"]}
        postassets={x["asset"]:x for x in post["asset_summaries"]}
        for symbol in preassets.keys() | postassets.keys():
            a=preassets.get(symbol,{}).get("best_modeled_net_usdc_units")
            b=postassets.get(symbol,{}).get("best_modeled_net_usdc_units")
            per_asset.append({"asset":symbol,"pre_best_net":a,"post_best_net":b,
                              "delta_best_net":b-a if a is not None and b is not None else None})
        comparisons.append({"event_block":event_block,"status":"END_OF_BLOCK_BEFORE_AFTER",
            "scope":"Cannot isolate event from other same-block transactions",
            "asset_comparisons":per_asset})
    summary={"status":"EVENT_CONDITIONED_QUOTES_ONLY",
        "at_utc":datetime.now(timezone.utc).isoformat(),
        "head_block":head,"event_selector_status":events.get("status","UNAVAILABLE"),
        "observations":sampled,"comparisons":comparisons,
        "snapshots_quoted":sum(x["status"]=="QUOTED" for x in sampled),
        "full_two_leg_quotes":sum(x.get("total_full_two_leg_quotes",0) for x in sampled),
        "positive_research_quotes":sum(len(x.get("positive_research_quotes",[])) for x in sampled),
        "verified_profit_executions":0, "legacy_fork_candidate_count":len(candidates),
        "wall_seconds":round(time.monotonic()-started,2),
        "provider_errors":provider_errors,
        "interpretation":"Historical B-1/B boundary, NOT intra-transaction state, not executable PnL"}
    (output/"event-backtest.json").write_text(json.dumps(summary,indent=2))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events",type=Path,default=Path("validation/history/event-selection.json"))
    p.add_argument("--out",type=Path,default=Path("validation/extended"))
    p.add_argument("--assets",default="WETH,cbBTC,DAI,EURC")
    p.add_argument("--amounts",default="200,1000,5000,20000")
    p.add_argument("--max-seconds",type=float,default=205)
    p.add_argument("--max-events",type=int,default=2)
    p.add_argument("--max-routes",type=int,default=10)
    p.add_argument("--refine-top",type=int,default=2)
    a=p.parse_args()
    events=json.loads(a.events.read_text()) if a.events.is_file() else {}
    result=run(events,a.out,a.assets,a.amounts,a.max_seconds,
        a.max_events,a.max_routes,a.refine_top)
    print(json.dumps({k:v for k,v in result.items()
        if k not in ("observations","comparisons","provider_errors")},indent=2))
    for observation in result["observations"]:
        print(json.dumps({"block":observation["block"],"side":observation["side"],
            "status":observation["status"],
            "quotes":observation.get("total_full_two_leg_quotes",0),
            "positives":len(observation.get("positive_research_quotes",[]))}))
    return 0 if result["snapshots_quoted"] else 2

if __name__=="__main__":
    raise SystemExit(main())

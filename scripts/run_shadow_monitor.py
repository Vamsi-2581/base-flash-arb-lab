"""Safe, self-hostable Base Aave SHADOW monitor: no keys, no signing, no broadcasting.

By default runs ONE iteration only. For a long-running process operator must
explicitly pass --iterations 0 and --interval >= 60. NOT a competitive liquidator.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time

from bot.rpc import RPC,RpcError
from research.aave_borrower_health import inspect
from research.live_liquidation_gate import get_snapshot

def cycle(rpc:RPC,output:Path,*,borrow_span:int=512,max_users:int=25)->dict:
    output.mkdir(parents=True,exist_ok=True)
    try:
        history=inspect(rpc,span=borrow_span,max_users=max_users)
        addresses=[item["borrower"] for item in history.get("sample",[])
                   if isinstance(item,dict) and item.get("borrower")]
        snapshot=get_snapshot(rpc,addresses)
        result={
            "status":"SHADOW_READ_ONLY",
            "at_utc":datetime.now(timezone.utc).isoformat(),
            "borrow_event_blocks_covered":history.get("borrow_log_blocks_covered",0),
            "addresses_resampled":len(addresses),
            "account_reads_complete":snapshot.get("account_reads_complete",0),
            "health_factor_below_one":snapshot.get("account_level_underwater",0),
            "USDC_WETH_pair_verified":snapshot.get("asset_specific_pair_verified",0),
            "underwater_USDC_WETH_verified":snapshot.get("underwater_and_USDC_WETH_pair_verified",0),
            "snapshot_block":snapshot.get("block"),
            "snapshot_status":snapshot.get("status"),
            "broadcast":False,
            "live_ready":False,
            "warning":"Sampling cannot guarantee transaction ordering or inclusion. No execution/profit claim."
        }
        (output/"latest_snapshot.json").write_text(json.dumps(snapshot,indent=2))
    except (RpcError,OSError,ValueError,TypeError) as exc:
        result={
            "status":"RPC_OR_CHAIN_DATA_UNAVAILABLE",
            "at_utc":datetime.now(timezone.utc).isoformat(),
            "reason_type":type(exc).__name__,
            "broadcast":False,"live_ready":False}
    (output/"latest_summary.json").write_text(json.dumps(result,indent=2))
    with (output/"samples.jsonl").open("a") as f:
        f.write(json.dumps(result)+"\n")
    return result

def run(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rpc",default="https://mainnet.base.org")
    p.add_argument("--out",type=Path,default=Path("validation/shadow-monitor"))
    p.add_argument("--interval",type=int,default=300,help=">=60 seconds to protect public RPC")
    p.add_argument("--iterations",type=int,default=1,
                   help="One pass by default; use 0 ONLY if explicitly operating a server")
    p.add_argument("--borrow-span",type=int,default=512)
    args=p.parse_args(argv)
    if args.interval<60 or args.iterations<0 or args.iterations>1_000_000:
        p.error("Invalid monitoring cadence")
    if not 1<=args.borrow_span<=1024:
        p.error("Borrow span out of safe range")
    rpc=RPC(args.rpc,timeout=8)
    count=0
    while args.iterations==0 or count<args.iterations:
        print(json.dumps(cycle(rpc,args.out,borrow_span=args.borrow_span)),flush=True)
        count+=1
        if args.iterations==0 or count<args.iterations:
            time.sleep(args.interval)
    return 0

if __name__=="__main__":
    raise SystemExit(run())

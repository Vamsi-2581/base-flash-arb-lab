"""Find historical high-volume Uniswap/Pancake Base swap blocks for quote backtesting.

Read-only swaps emitted by real V3 pools. Pinned EVM state at END of the block
may already include competitor arbitrage; event presence is NOT proof of profit.
"""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from bot.abi import keccak256, result_address, encode
from bot.networks import EXCHANGES, USDC, WETH
from bot.searcher import pool_at
from bot.rpc import RPC, RpcError

TOPIC="0x"+keccak256(b"Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
ENDPOINTS=(
    ("base_official","https://mainnet.base.org"),
    ("publicnode","https://base-rpc.publicnode.com"),
    ("drpc","https://base.drpc.org"),
)

def decode_usdc_swap(raw: str, usdc_slot: int) -> int:
    h=raw.removeprefix("0x")
    if len(h)<128:
        raise ValueError("Truncated V3 Swap event")
    n=int(h[usdc_slot*64:(usdc_slot+1)*64],16)
    if n>=1<<255: n-=1<<256
    return abs(n)

def collect_events(rpc: RPC, head: int, span: int, segments: int, width: int,
                   min_usdc: int, *, top_n: int=5):
    block_hex=hex(head)
    pools=[]
    for dex in EXCHANGES:
        try:
            v=pool_at(rpc,dex,500,block_hex)
            if v is None: continue
            token0=result_address(rpc.eth_call(v.pool, encode("token0()"),block_hex))
            token1=result_address(rpc.eth_call(v.pool, encode("token1()"),block_hex))
            if {token0.lower(),token1.lower()} != {WETH.lower(),USDC.lower()}:
                continue
            slot=0 if token0.lower()==USDC.lower() else 1
            pools.append((v.pool, slot, dex.label))
        except (RpcError,ValueError):
            pass
    if not pools:
        raise RpcError("No verifiable fee-500 USDC/WETH V3 pools")
    # Query short intervals across a larger span to avoid provider log response caps.
    totals, logs_seen, errors = {},0,[]
    for i in range(segments):
        start=max(1,head-span+(i*span//segments))
        stop=min(head-8,start+width-1)
        if stop<start: continue
        for address,slot,label in pools:
            try:
                rows=rpc.call("eth_getLogs",[{
                    "address":address, "topics":[TOPIC],
                    "fromBlock":hex(start), "toBlock":hex(stop)}])
                if not isinstance(rows,list): raise RpcError("Malformed logs array")
                logs_seen += len(rows)
                for row in rows:
                    amt=decode_usdc_swap(row["data"],slot)
                    if amt<min_usdc: continue
                    block=int(row["blockNumber"],16)
                    old=totals.get(block)
                    if old is None or amt > old["largest_swap_usdc_units"]:
                        totals[block]={"largest_swap_usdc_units":amt,
                            "transaction_hash":row.get("transactionHash"),
                            "log_index":int(row.get("logIndex","0x0"),16)}
            except (RpcError,ValueError,KeyError,TypeError) as exc:
                errors.append({"venue":label,"from":start,"to":stop,
                               "error":f"{type(exc).__name__}: {str(exc)[:140]}"})
    chosen=[]
    for block,event in sorted(totals.items(),key=lambda item:item[1]["largest_swap_usdc_units"],reverse=True):
        if len(chosen)>=top_n: break
        if any(abs(block-prev["block"])<=4 for prev in chosen): continue
        chosen.append({"block":block,"lookback":head-block,
                       "largest_swap_usdc":round(event["largest_swap_usdc_units"]/1e6,2),
                       "transaction_hash":event["transaction_hash"],
                       "log_index":event["log_index"]})
    return {"head":head,"pools":[{"address":v[0],"venue":v[2]} for v in pools],
            "segments":segments,"segment_width":width, "span":span,
            "min_swap_usdc":min_usdc/1e6, "logs_seen":logs_seen,
            "errors":errors,"selected":chosen}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,default=Path("validation/event"))
    p.add_argument("--span",type=int,default=16000)
    p.add_argument("--segments",type=int,default=8)
    p.add_argument("--width",type=int,default=60)
    p.add_argument("--top-n",type=int,default=5)
    p.add_argument("--min-swap",type=int,default=50000)
    args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    report={"status":"EVENT_LOG_UNAVAILABLE","attempts":[],
            "timestamp_utc":datetime.now(timezone.utc).isoformat()}
    for label,url in ENDPOINTS:
        try:
            rpc=RPC(url,timeout=9)
            if rpc.chain_id()!=8453: raise RpcError("Wrong chain")
            data=collect_events(rpc,rpc.block_number(),args.span,args.segments,
                args.width,args.min_swap*10**6,top_n=args.top_n)
            if not data["selected"] and data["errors"]:
                raise RpcError("No selected blocks and swap log calls failed")
            report.update({"status":"EVENT_BLOCKS_SELECTED","endpoint":label,**data})
            break
        except (RpcError,OSError,ValueError) as exc:
            report["attempts"].append({"endpoint":label,"error":str(exc)[:180]})
    (args.out/"event-selection.json").write_text(json.dumps(report,indent=2))
    (args.out/"event-lookbacks.txt").write_text(
        ",".join(str(x["lookback"]) for x in report.get("selected",[])))
    print(json.dumps(report,indent=2))
    return 0 if report.get("selected") else 2

if __name__=="__main__":
    raise SystemExit(main())
